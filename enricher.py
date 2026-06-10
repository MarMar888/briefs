"""
Enriches matched domains by scraping contact/about pages and extracting
structured info (owner name, full address, phone, email, established year).

Usage:
    python enricher.py [--limit N]
"""

import argparse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_FIRECRAWL_TIMEOUT = int(os.environ.get("FIRECRAWL_TIMEOUT_SECONDS", "40"))
_ENRICH_WORKERS = int(os.environ.get("ENRICH_WORKERS", "4"))
_LLM_RETRIES = 4

ENRICH_PROMPT = """You are extracting contact and business details from a website's content.

Return ONLY these fields, one per line. Use UNKNOWN for any field you cannot find.

OWNER_NAME: <first and last name of owner, founder, or primary contact — UNKNOWN if not found>
FULL_ADDRESS: <complete street address with city, state, zip — UNKNOWN if not found>
PHONE: <primary phone number — UNKNOWN if not found>
EMAIL: <primary contact email — UNKNOWN if not found>
ESTABLISHED: <year or date established — UNKNOWN if not found>

Website content:
{content}"""


def _firecrawl(url: str, max_chars: int = 2000) -> str:
    api_key = os.environ.get("FIRECRAWL_API_KEY") or os.environ.get("FIRECRAWL")
    if not api_key:
        return ""
    try:
        resp = requests.post(
            os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2/scrape"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "waitFor": 1000,
                "timeout": 30000,
                "location": {"country": "US", "languages": ["en-US"]},
                "removeBase64Images": True,
                "blockAds": True,
            },
            timeout=_FIRECRAWL_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        text = data.get("markdown") or data.get("summary") or ""
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception:
        return ""


def _fetch_pages(base_url: str) -> str:
    """Scrape homepage + /contact + /about, return combined text."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    urls = [base_url, f"{root}/contact", f"{root}/about"]
    parts = []
    for url in urls:
        text = _firecrawl(url)
        if text:
            parts.append(text)
    return "\n\n".join(parts)[:5000]


def _extract_info(content: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or not content.strip():
        return {}

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    model = os.environ.get("OPENROUTER_ENRICH_MODEL") or os.environ.get(
        "OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct"
    )

    for attempt in range(1, _LLM_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": ENRICH_PROMPT.format(content=content)}],
                temperature=0,
                max_tokens=200,
            )
            raw = resp.choices[0].message.content or ""
            return _parse_response(raw)
        except Exception as e:
            if attempt == _LLM_RETRIES:
                print(f"[enricher] LLM failed after {_LLM_RETRIES} attempts: {e}", flush=True)
                return {}
            time.sleep(2 * attempt)
    return {}


def _parse_response(raw: str) -> dict:
    result = {}
    for line in raw.splitlines():
        for key, field in [
            ("OWNER_NAME:", "owner_name"),
            ("FULL_ADDRESS:", "full_address"),
            ("PHONE:", "phone"),
            ("EMAIL:", "email"),
            ("ESTABLISHED:", "established"),
        ]:
            if line.startswith(key):
                val = line[len(key):].strip()
                if val and val.upper() != "UNKNOWN":
                    result[field] = val
    return result


def _enrich_row(row: dict) -> tuple[str, dict]:
    domain = row["domain"]
    url = row.get("website_url") or f"https://{domain}"
    content = _fetch_pages(url)
    info = _extract_info(content) if content else {}
    info["enriched_at"] = datetime.utcnow().isoformat()
    return domain, info


def run_enrichment(limit: int = 0) -> int:
    import domain_store
    domain_store.init_db()

    rows = domain_store.get_unenriched_matches(limit=limit)
    if not rows:
        print("[enricher] No unenriched matches found", flush=True)
        return 0

    print(f"[enricher] Enriching {len(rows)} matched domains ({_ENRICH_WORKERS} workers)", flush=True)
    enriched = 0

    with ThreadPoolExecutor(max_workers=_ENRICH_WORKERS) as executor:
        futures = {executor.submit(_enrich_row, row): row for row in rows}
        for future in as_completed(futures):
            domain, info = future.result()
            domain_store.update_domain(domain, **info)
            enriched += 1
            owner = info.get("owner_name", "")
            addr = info.get("full_address", "")
            tag = f" | owner: {owner}" if owner else ""
            tag += f" | addr: {addr}" if addr else ""
            print(f"[enricher] {domain}{tag}", flush=True)

    print(f"[enricher] Done — {enriched} domains enriched", flush=True)
    return enriched


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich matched domains with contact details")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max domains to enrich per run (0 = no limit)")
    args = parser.parse_args()
    run_enrichment(limit=args.limit)

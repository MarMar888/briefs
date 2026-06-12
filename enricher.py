"""
Deep-search audit of matched domains.

After a domain is matched, this performs a deeper crawl of the company's site
(homepage + discovered about/contact/team/history/locations pages) and produces
an enriched profile used to vet the lead:

  - Contact details:   owner name, full address, phone, email
  - Longevity:         how long the business has been around (established year,
                       copyright spans) — flags older businesses that just
                       registered a new domain.
  - Size / seriousness: business size, employee estimate, location count, legal
                       entity type, and whether it looks like a hobby/side project
                       rather than a real commercial operation.
  - Company details:   one-line summary, social presence.
  - audit_notes:       a short human-readable rollup of the above for the dashboard.

Usage:
    python enricher.py [--limit N]
"""

import argparse
import asyncio
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_ENRICH_WORKERS = int(os.environ.get("ENRICH_WORKERS", "4"))
_LLM_RETRIES = 4
_MAX_AUDIT_PAGES = int(os.environ.get("ENRICH_MAX_PAGES", "5"))
_MAX_CONTENT_CHARS = int(os.environ.get("ENRICH_MAX_CHARS", "9000"))
# Off-site search: looks the business up on the open web (directories, social,
# reviews, news) for age/size signals the site itself may not state. Works
# keyless via DuckDuckGo by default; set BRAVE_SEARCH_API_KEY or SERPAPI_API_KEY
# for a more reliable/higher-volume provider. Set ENRICH_EXTERNAL_SEARCH=0 to disable.
_EXTERNAL_SEARCH = os.environ.get("ENRICH_EXTERNAL_SEARCH", "1") != "0"
_SEARCH_RESULTS = int(os.environ.get("ENRICH_SEARCH_RESULTS", "6"))
_DDG_RETRIES = int(os.environ.get("ENRICH_DDG_RETRIES", "2"))

# Run-level throttle telemetry so we can monitor how often off-site search gets
# blocked (DuckDuckGo throttles datacenter IPs). Reset at the start of each run.
_throttle_lock = threading.Lock()
_throttle_count = 0
_search_attempts = 0


def _note_search(throttled: bool) -> None:
    global _throttle_count, _search_attempts
    with _throttle_lock:
        _search_attempts += 1
        if throttled:
            _throttle_count += 1

ENRICH_PROMPT = """You are auditing a company's website to vet it as a commercial insurance lead.
The broker wants NEW or EARLY-STAGE outdoor businesses and wants to AVOID two things:
  (a) long-established companies that merely registered a fresh domain, and
  (b) tiny hobby / passion / part-time side projects that are not real commercial operations.

Read the combined website content below and extract these fields, one per line.
The content may include an "EXTERNAL SEARCH RESULTS" section with off-site listings
(directories, social, reviews, news) — use it for age, size, location, and longevity
signals the site itself omits, but weigh the business's own site most heavily.
Use UNKNOWN for anything you genuinely cannot determine from the content. Do not guess.

OWNER_NAME: <first and last name of the owner, founder, or principal contact>
FULL_ADDRESS: <complete street address with city, state, zip>
PHONE: <primary phone number>
EMAIL: <primary contact email>
ESTABLISHED: <year or period the business was founded, e.g. "2024", "since 1998", "over 30 years", "3rd generation">
ENTITY_TYPE: <legal entity if stated in the content: LLC, Inc, Corporation, LLP, sole proprietor>
EMPLOYEE_ESTIMATE: <rough team size implied by the content: "1", "2-5", "6-20", "20+">
LOCATION_COUNT: <how many physical locations / storefronts are mentioned: "1", "2", "3+">
BUSINESS_SIZE: <exactly one of: solo, small, midsize, large — your best judgment of operating scale>
SIDE_PROJECT: <YES if this reads like a hobby, passion project, or part-time side venture rather than a
  full commercial operation. Signs: a single person, no legal entity, no street address, a free email
  (gmail/yahoo/outlook), very thin or personal "about me" content, no staff, no hours. NO if it presents
  as a real staffed commercial business.>
SUMMARY: <one concise sentence describing what the business does and what it sells or offers>

Website content:
{content}"""

_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "protonmail.com", "ymail.com",
    "comcast.net", "att.net", "verizon.net", "me.com", "mail.com",
}

_SOCIAL_HOSTS = {
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "linkedin.com": "LinkedIn",
    "yelp.com": "Yelp",
    "pinterest.com": "Pinterest",
}

# Link text / href keywords that point at pages worth reading for an audit.
_PAGE_HINTS = (
    "about", "our-story", "our story", "story", "history", "heritage",
    "team", "staff", "our-team", "meet", "contact", "location", "locations",
    "store", "stores", "visit", "hours", "services", "company", "who-we-are",
)

# Fallback paths to try when on-page link discovery finds nothing useful.
_FALLBACK_PATHS = ("/about", "/about-us", "/contact", "/our-story", "/locations")

_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
_SITEMAP_DIRECTIVE_RE = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)
_MAX_SITEMAP_FETCHES = 4


def _sitemap_audit_urls(root: str, host: str) -> list[str]:
    """Discover relevant pages from robots.txt / sitemap.xml.

    Returns same-host page URLs whose path matches an audit hint, ordered with
    hint pages first. Best-effort: any network/parse failure yields [].
    """
    bare_host = host.replace("www.", "")

    # Find sitemap locations: robots.txt directives first, then common defaults.
    sitemap_urls: list[str] = []
    try:
        resp = requests.get(f"{root}/robots.txt", timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok:
            sitemap_urls = _SITEMAP_DIRECTIVE_RE.findall(resp.text)
    except Exception:
        pass
    for default in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        url = root + default
        if url not in sitemap_urls:
            sitemap_urls.append(url)

    locs: list[str] = []
    fetches = 0
    queue = list(sitemap_urls)
    seen_sitemaps: set[str] = set()
    while queue and fetches < _MAX_SITEMAP_FETCHES:
        sm = queue.pop(0)
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        try:
            resp = requests.get(sm, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            fetches += 1
            if not resp.ok:
                continue
            found = _LOC_RE.findall(resp.text)
        except Exception:
            continue
        for loc in found:
            loc = loc.strip()
            if loc.lower().endswith(".xml") or "sitemap" in loc.lower().rsplit("/", 1)[-1]:
                queue.append(loc)  # nested sitemap index
            else:
                locs.append(loc)

    matches: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        p = urlparse(loc)
        if p.netloc.lower().replace("www.", "") != bare_host:
            continue
        if not any(hint in p.path.lower() for hint in _PAGE_HINTS):
            continue
        norm = loc.split("#")[0].rstrip("/")
        if norm and norm not in seen:
            seen.add(norm)
            matches.append(norm)
    return matches


async def _crawl4ai_links_async(url: str) -> list[dict]:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    config = CrawlerRunConfig(excluded_tags=[], remove_overlay_elements=False)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=config)
    if not result.success:
        return []
    return result.links.get("internal", [])


def _crawl4ai_discover_links(url: str) -> list[dict]:
    try:
        return _get_crawl4ai_loop().run_until_complete(_crawl4ai_links_async(url))
    except Exception:
        return []


async def _crawl4ai_fetch_async(url: str, max_chars: int) -> str:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    config = CrawlerRunConfig(
        excluded_tags=["nav", "footer", "aside"],
        remove_overlay_elements=True,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed"),
            options={"ignore_links": True},
        ),
    )
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=config)
    if not result.success:
        return ""
    md = result.markdown
    text = (md.fit_markdown or md.raw_markdown) if hasattr(md, "fit_markdown") else str(md)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


_crawl4ai_local = threading.local()


def _get_crawl4ai_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_crawl4ai_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _crawl4ai_local.loop = loop
    return loop


def _scrape(url: str, max_chars: int = 3000) -> str:
    """Scrape a URL via Crawl4AI. Switch body to _firecrawl() to restore Firecrawl."""
    try:
        return _get_crawl4ai_loop().run_until_complete(_crawl4ai_fetch_async(url, max_chars))
    except Exception as e:
        print(f"[enricher] Crawl4AI scrape failed for {url}: {e}", flush=True)
        return ""


def _firecrawl(url: str, max_chars: int = 3000) -> str:
    """Kept for easy restore when Firecrawl credits are available."""
    api_key = os.environ.get("FIRECRAWL_API_KEY") or os.environ.get("FIRECRAWL")
    if not api_key:
        return ""
    timeout = int(os.environ.get("FIRECRAWL_TIMEOUT_SECONDS", "40"))
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
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        text = data.get("markdown") or data.get("summary") or ""
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception:
        return ""


def _discover_audit_urls(base_url: str) -> tuple[list[str], str]:
    """Fetch the homepage HTML and pick internal pages worth reading for the audit.

    Returns (ordered list of audit URLs starting with the homepage, raw homepage HTML).
    Falls back to common paths when link discovery turns up nothing.
    """
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lower()

    html = ""
    try:
        resp = requests.get(base_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text
    except Exception:
        html = ""

    discovered: list[str] = []
    seen: set[str] = set()

    def _add_link(href: str, link_text: str = "") -> None:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return
        full = urljoin(root + "/", href)
        p = urlparse(full)
        if p.netloc.lower().replace("www.", "") != host.replace("www.", ""):
            return
        haystack = f"{p.path.lower()} {link_text.lower()}"
        if not any(hint in haystack for hint in _PAGE_HINTS):
            return
        norm = full.split("#")[0].rstrip("/")
        if norm and norm not in seen and norm != base_url.rstrip("/"):
            seen.add(norm)
            discovered.append(norm)

    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            _add_link(a["href"].strip(), a.get_text(" ", strip=True))

    # Augment with sitemap.xml-discovered pages (catches sites that bury pages).
    for url in _sitemap_audit_urls(root, host):
        if url not in seen and url != base_url.rstrip("/"):
            seen.add(url)
            discovered.append(url)

    # JS-rendered sites return a script shell to requests — no nav links visible.
    # Fall back to Crawl4AI's rendered links before guessing fallback paths.
    if not discovered:
        for link in _crawl4ai_discover_links(base_url):
            _add_link(link.get("href", ""), link.get("text", ""))

    if not discovered:
        discovered = [root + path for path in _FALLBACK_PATHS]

    urls = [base_url] + discovered[: _MAX_AUDIT_PAGES - 1]
    return urls, html


def _fetch_pages(base_url: str) -> tuple[str, str]:
    """Crawl the homepage plus discovered audit pages. Returns (combined_text, homepage_html)."""
    urls, html = _discover_audit_urls(base_url)
    parts: list[str] = []
    for url in urls:
        text = _scrape(url)
        if text:
            parts.append(text)
        if sum(len(p) for p in parts) >= _MAX_CONTENT_CHARS:
            break
    return "\n\n".join(parts)[:_MAX_CONTENT_CHARS], html


# --- off-site search -------------------------------------------------------

_TITLE_SEP_RE = re.compile(r"\s*[|\-–—:·]\s*")
_TITLE_NOISE = {"home", "homepage", "welcome", "official site", "official website"}


def _business_name(html: str, domain: str) -> str:
    """Best-effort business name from the homepage <title>, falling back to the domain label."""
    title = ""
    if html:
        try:
            tag = BeautifulSoup(html, "html.parser").find("title")
            title = tag.get_text(" ", strip=True) if tag else ""
        except Exception:
            title = ""
    if title:
        # Titles usually lead with the brand ("Brand | tagline"); take the first
        # meaningful segment after dropping generic words like "Home".
        segments = [s.strip() for s in _TITLE_SEP_RE.split(title) if s.strip()]
        segments = [s for s in segments if s.lower() not in _TITLE_NOISE]
        if segments:
            return segments[0]
    return domain.rsplit(".", 1)[0].replace("-", " ")


def _search_brave(query: str) -> list[dict]:
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            params={"q": query, "count": _SEARCH_RESULTS},
            timeout=12,
        )
        resp.raise_for_status()
        results = (resp.json().get("web") or {}).get("results") or []
        return [
            {"title": r.get("title", ""), "snippet": r.get("description", ""), "url": r.get("url", "")}
            for r in results[:_SEARCH_RESULTS]
        ]
    except Exception as e:
        print(f"[enricher] Brave search failed for '{query}': {e}", flush=True)
        return []


def _search_serpapi(query: str) -> list[dict]:
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        return []
    try:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"q": query, "engine": "google", "num": _SEARCH_RESULTS, "api_key": key},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("organic_results") or []
        return [
            {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("link", "")}
            for r in results[:_SEARCH_RESULTS]
        ]
    except Exception as e:
        print(f"[enricher] SerpAPI search failed for '{query}': {e}", flush=True)
        return []


_DDG_BLOCK_MARKERS = ("anomaly", "unfortunately, bots use", "if this error persists")


def _parse_ddg(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for res in soup.select(".result, .web-result")[: _SEARCH_RESULTS * 2]:
        a = res.select_one(".result__a")
        if not a:
            continue
        snippet_el = res.select_one(".result__snippet")
        url_el = res.select_one(".result__url")
        url = url_el.get_text(" ", strip=True) if url_el else ""
        if url and not url.startswith("http"):
            url = "https://" + url
        out.append({
            "title": a.get_text(" ", strip=True),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "url": url,
        })
        if len(out) >= _SEARCH_RESULTS:
            break
    return out


def _search_duckduckgo(query: str) -> list[dict]:
    """Keyless web search via DuckDuckGo's HTML endpoint (no API key required).

    DDG throttles/challenges datacenter IPs. This detects throttle responses
    (HTTP 202/429 or a block/anomaly page), retries a couple of times with
    backoff + jitter, and on persistent throttling logs a clear, greppable line
    and returns [] — the lead is then audited on its own site content alone.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    # Small jitter spreads concurrent worker calls so we don't self-trigger throttling.
    time.sleep(random.uniform(0, 0.6))

    throttled = False
    for attempt in range(1, _DDG_RETRIES + 2):
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query}, headers=headers, timeout=12,
            )
        except Exception as e:
            print(f"[enricher] DuckDuckGo request error for '{query}': {e}", flush=True)
            break

        body_head = resp.text[:3000].lower()
        is_throttle = resp.status_code in (202, 429) or any(m in body_head for m in _DDG_BLOCK_MARKERS)
        if is_throttle:
            throttled = True
            if attempt <= _DDG_RETRIES:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            print(f"[enricher] DuckDuckGo THROTTLED for '{query}' (status {resp.status_code}) — skipping off-site signals", flush=True)
            break

        if resp.status_code != 200:
            print(f"[enricher] DuckDuckGo HTTP {resp.status_code} for '{query}'", flush=True)
            break

        results = _parse_ddg(resp.text)
        _note_search(throttled=False)
        return results

    _note_search(throttled=throttled)
    return []


def _external_signals(domain: str, html: str, location: str) -> str:
    """Search the open web for off-site signals about the business.

    Uses an API provider when a key is configured (Brave or SerpAPI — more
    reliable / higher volume), otherwise falls back to keyless DuckDuckGo.
    Returns a labeled text block of result snippets (or "" when disabled / no
    hits). The block is appended to the audit content so both the LLM and the
    deterministic longevity/size checks can use it.
    """
    if not _EXTERNAL_SEARCH:
        return ""

    name = _business_name(html, domain)
    query = f"{name} {location}".strip() if location else name
    results = _search_brave(query) or _search_serpapi(query) or _search_duckduckgo(query)
    if not results:
        return ""

    lines = [f"(off-site search for: {query})"]
    for r in results:
        snippet = re.sub(r"\s+", " ", r["snippet"]).strip()
        line = " — ".join(filter(None, [r["title"].strip(), snippet]))
        if r["url"]:
            line += f" [{urlparse(r['url']).netloc}]"
        if line:
            lines.append(line)
    return "\n".join(lines)[:2500]


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
                max_tokens=320,
            )
            raw = resp.choices[0].message.content or ""
            return _parse_response(raw)
        except Exception as e:
            if attempt == _LLM_RETRIES:
                print(f"[enricher] LLM failed after {_LLM_RETRIES} attempts: {e}", flush=True)
                return {}
            time.sleep(2 * attempt)
    return {}


_FIELD_KEYS = [
    ("OWNER_NAME:", "owner_name"),
    ("FULL_ADDRESS:", "full_address"),
    ("PHONE:", "phone"),
    ("EMAIL:", "email"),
    ("ESTABLISHED:", "established"),
    ("ENTITY_TYPE:", "entity_type"),
    ("EMPLOYEE_ESTIMATE:", "employee_estimate"),
    ("LOCATION_COUNT:", "location_count"),
    ("BUSINESS_SIZE:", "business_size"),
    ("SIDE_PROJECT:", "_side_project_raw"),
    ("SUMMARY:", "business_summary"),
]


def _parse_response(raw: str) -> dict:
    result: dict = {}
    for line in raw.splitlines():
        for key, field in _FIELD_KEYS:
            if line.upper().startswith(key):
                val = line[len(key):].strip().strip("*").strip()
                if val and val.upper() not in {"UNKNOWN", "N/A", "NONE", ""}:
                    result[field] = val
    return result


# --- deterministic signals -------------------------------------------------

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_COPYRIGHT_RE = re.compile(r"(?:©|\(c\)|copyright)\s*[\d\s,–-]*?((?:19|20)\d{2})", re.I)
_SINCE_RE = re.compile(r"(?:since|established|est\.?|founded|serving\s+\w+\s+since)\s*(?:in\s*)?((?:19|20)\d{2})", re.I)
_GENERATION_RE = re.compile(
    r"\b(?:\d{1,2}(?:st|nd|rd|th)?|one|two|three|four|five|second|third|fourth|fifth)[\s-]*generation",
    re.I,
)
# Qualified duration phrases only, so "10 year warranty" / "5 year guarantee" don't
# get mistaken for business longevity. Requires a longevity-context cue.
_DURATION_RE = re.compile(
    r"\b(?:over|more than|nearly|almost|for)\s+(\d{1,3})\s*\+?\s*years\b"
    r"|\b(\d{1,3})\s*\+?\s*years?\s+(?:in\s+business|in\s+operation|of\s+(?:business|experience)|serving)",
    re.I,
)
_ENTITY_RE = re.compile(r"\b(LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|LLP|Ltd\.?|Co\.)\b")


def _max_duration_years(text: str) -> int:
    best = 0
    for m in _DURATION_RE.finditer(text):
        val = m.group(1) or m.group(2)
        if val:
            best = max(best, int(val))
    return best


def _founding_years(content: str, established: str) -> list[int]:
    text = f"{established} {content}"
    years: set[int] = set()
    for m in _COPYRIGHT_RE.finditer(text):
        years.add(int(m.group(1)))
    for m in _SINCE_RE.finditer(text):
        years.add(int(m.group(1)))
    # bare years inside the explicit established field are trustworthy
    for m in _YEAR_RE.finditer(established):
        years.add(int(m.group(1)))
    return sorted(years)


def _assess_longevity(established: str, content: str) -> tuple[str, bool]:
    """Return (human-readable longevity label, is_old flag).

    is_old=True means the business shows signs of being long-running (and thus a
    weaker "new business" lead even though the domain is freshly registered).
    """
    current_year = datetime.now().year
    years = [y for y in _founding_years(content, established) if 1900 <= y <= current_year]
    est_low = (established or "").lower()
    scan = f"{est_low} {content[:5000].lower()}"

    # Multi-generation / decades language is a strong "old business" tell.
    if _GENERATION_RE.search(scan):
        return ("Established — multi-generation business", True)
    if any(word in scan for word in ("decade", "long-running", "longstanding", "long-established")):
        return ("Established — decades in business", True)

    if years:
        founded = min(years)
        age = current_year - founded
        if age >= 3:
            return (f"Established ~{age}y (since {founded})", True)
        if age >= 1:
            return (f"Recent (since {founded})", False)
        return (f"New (founded {founded})", False)

    duration = _max_duration_years(scan)
    if duration >= 3:
        return (f"Established — {duration}+ years in business", True)

    return ("No age signal found", False)


def _detect_social(content: str, html: str) -> str:
    found: list[str] = []
    blob = f"{content} {html}".lower()
    for host, label in _SOCIAL_HOSTS.items():
        if host in blob and label not in found:
            found.append(label)
    return ", ".join(found)


def _detect_entity(content: str) -> str:
    m = _ENTITY_RE.search(content)
    if not m:
        return ""
    raw = m.group(1).rstrip(".").upper()
    mapping = {
        "LLC": "LLC", "L.L.C": "LLC", "INC": "Inc", "INCORPORATED": "Inc",
        "CORP": "Corp", "CORPORATION": "Corp", "LLP": "LLP", "LTD": "Ltd", "CO": "Co",
    }
    return mapping.get(raw, m.group(1))


def _is_free_email(email: str) -> bool:
    if "@" not in (email or ""):
        return False
    return email.split("@")[-1].strip().lower() in _FREE_EMAIL_DOMAINS


def _build_audit(info: dict, content: str, html: str) -> dict:
    """Layer deterministic signals on top of the LLM extraction and roll up audit_notes."""
    established = info.get("established", "")
    longevity, is_old = _assess_longevity(established, content)
    info["longevity"] = longevity

    if not info.get("entity_type"):
        entity = _detect_entity(content)
        if entity:
            info["entity_type"] = entity

    social = _detect_social(content, html)
    if social:
        info["social_links"] = social

    size = (info.get("business_size") or "").lower()
    has_address = bool(info.get("full_address"))
    free_email = _is_free_email(info.get("email", ""))

    # Resolve the side-project flag: LLM judgment, reinforced by hard signals.
    side_raw = (info.pop("_side_project_raw", "") or "").upper()
    side_project = side_raw.startswith("Y")
    solo_signals = sum([
        size == "solo",
        not has_address,
        free_email,
        not info.get("entity_type"),
    ])
    if solo_signals >= 3:
        side_project = True
    info["side_project"] = 1 if side_project else 0

    # Second-stage filter verdict: this audit exists to weed out the bad leads
    # that still slip past the classifier — established businesses (that merely
    # registered a fresh domain) and tiny hobby/side projects.
    disqualifiers: list[str] = []
    if is_old:
        disqualifiers.append(f"established business ({longevity})")
    if side_project:
        disqualifiers.append("looks like a hobby/side project")
    info["audit_verdict"] = "disqualified" if disqualifiers else "qualified"

    # Human-readable rollup for the dashboard.
    notes: list[str] = []
    for d in disqualifiers:
        notes.append(f"⚠ {d}")
    if size:
        notes.append(f"size: {size}")
    if info.get("employee_estimate"):
        notes.append(f"team: {info['employee_estimate']}")
    if info.get("location_count"):
        notes.append(f"{info['location_count']} location(s)")
    if info.get("entity_type"):
        notes.append(info["entity_type"])
    if free_email:
        notes.append("free email contact")
    info["audit_notes"] = "; ".join(notes)

    return info


def _enrich_row(row: dict) -> tuple[str, dict]:
    domain = row["domain"]
    url = row.get("website_url") or f"https://{domain}"
    content, html = _fetch_pages(url)

    # Off-site search adds external age/size signals (directories, social, reviews).
    # The LLM sees it; the deterministic checks below do NOT (a stray year in a
    # directory snippet must never auto-disqualify a new lead — see README).
    external = _external_signals(domain, html, row.get("location") or "")
    audit_content = content
    if external:
        audit_content = f"{content}\n\n=== EXTERNAL SEARCH RESULTS ===\n{external}"

    info = _extract_info(audit_content) if audit_content.strip() else {}
    info = _build_audit(info, content, html)  # deterministic checks on site content only
    info["enriched_at"] = datetime.utcnow().isoformat()

    # Label, don't kill: disqualified leads keep status=matched with audit_verdict
    # set. They're suppressed from the default dashboard view and from alerts, never
    # deleted or moved to a terminal state (see README "Design Principles").
    return domain, info


def run_enrichment(limit: int = 0) -> int:
    import domain_store
    domain_store.init_db()

    rows = domain_store.get_unenriched_matches(limit=limit)
    if not rows:
        print("[enricher] No unenriched matches found", flush=True)
        return 0

    global _throttle_count, _search_attempts
    with _throttle_lock:
        _throttle_count = 0
        _search_attempts = 0

    print(f"[enricher] Auditing {len(rows)} matched domains ({_ENRICH_WORKERS} workers)", flush=True)
    enriched = 0
    rejected = 0

    with ThreadPoolExecutor(max_workers=_ENRICH_WORKERS) as executor:
        futures = {executor.submit(_enrich_row, row): row for row in rows}
        for future in as_completed(futures):
            domain, info = future.result()
            domain_store.update_domain(domain, **info)
            enriched += 1
            disqualified = info.get("audit_verdict") == "disqualified"
            if disqualified:
                rejected += 1
            mark = "✗ suppressed" if disqualified else "✓ kept"
            bits = []
            if info.get("owner_name"):
                bits.append(f"owner: {info['owner_name']}")
            if info.get("longevity"):
                bits.append(info["longevity"])
            if info.get("audit_notes"):
                bits.append(info["audit_notes"])
            tag = (" | " + " | ".join(bits)) if bits else ""
            print(f"[enricher] {mark} {domain}{tag}", flush=True)

    kept = enriched - rejected
    print(f"[enricher] Done — {enriched} audited: {kept} qualified, {rejected} suppressed (kept in DB, hidden from default view + alerts)", flush=True)

    if _search_attempts:
        pct = 100 * _throttle_count / _search_attempts
        level = "WARNING: heavy throttling" if pct >= 50 else "ok"
        print(
            f"[enricher] Off-site search: {_search_attempts - _throttle_count}/{_search_attempts} "
            f"succeeded, {_throttle_count} throttled ({pct:.0f}%) — {level}",
            flush=True,
        )
    return enriched


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deep-search audit + enrichment of matched domains")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max domains to enrich per run (0 = no limit)")
    args = parser.parse_args()
    run_enrichment(limit=args.limit)

"""
Scans newly registered domains for outdoor retailers.

Source: domains-monitor.com daily NRD lists.
USA filter: local DNS resolution + ip-api.com batch IP geolocation.
State: SQLite queue in domain_leads.sqlite3.

Run flow (each invocation):
  1. Download NRD list(s), TLD-filter, upsert up to `limit` new domains (status=new).
  2. Resolve + geolocate due `new` / `geo_pending` domains.
     - DNS fail → geo_pending
     - Non-US IP → non_us (terminal)
     - US IP → site_pending
  3. Fetch + validate due `site_pending` domains.
     - No response / parked / sparse → stay site_pending (retried next run)
     - Classifiable content → LLM classify → matched or not_outdoor (terminal)
  4. Return Filing objects for newly matched domains only.
"""

import base64
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
import csv
import gzip
import io
import os
from pathlib import Path
import random
import re
import socket
import time
import zipfile
from datetime import datetime, timedelta

import wordninja
import requests
import domain_store
from classifier import validate_site, classify_domain
from fetcher import Filing

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lead-monitor/1.0)"}
USA_TLDS = {".com", ".net", ".us"}
IP_API_BATCH_URL = "http://ip-api.com/batch"
DNS_WORKERS = 64
DNS_TIMEOUT_SECONDS = 3
DNS_PROGRESS_INTERVAL = 1000
GEO_BATCH_SIZE = int(os.environ.get("GEO_BATCH_SIZE", "100"))
GEO_BATCH_SLEEP_SECONDS = float(os.environ.get("GEO_BATCH_SLEEP_SECONDS", "1.5"))
GEO_BATCH_RETRIES = int(os.environ.get("GEO_BATCH_RETRIES", "3"))
GEO_RETRY_SLEEP_SECONDS = float(os.environ.get("GEO_RETRY_SLEEP_SECONDS", "5"))
GEO_PROGRESS_INTERVAL = 25
SITE_WORKERS = 8
KEYWORD_BATCH_SIZE = 25000
DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+)", re.I)

# Domain name keywords for pre-filtering NRDs before geo/scrape.
# Used as a set for O(1) token lookup in _matches_keywords.
OUTDOOR_KEYWORDS = {
    # Snow sports
    "ski", "skiing", "skier", "snowboard", "snowboarding", "snowboarder",
    "snowshoe", "snowshoeing", "nordic", "telemark", "backcountry", "chalet",
    # Camping / overlanding
    "camp", "camping", "camper", "campground", "glamping",
    "overland", "overlanding", "basecamp", "bivouac",
    # Hunting
    "hunt", "hunting", "hunter",
    "bowhunt", "bowhunting", "bowhunter",
    "waterfowl", "upland", "muzzleloader",
    "taxidermy", "treestand", "camo", "camouflage",
    "gunclub", "shootingrange", "gunrange", "trapshoot", "trapshooting", "skeet",
    # Fishing
    "fish", "fishing", "fisherman", "angler", "angling",
    "flyfishing", "flyfish", "flyshop", "flyrod", "fly",
    "icefishing",
    "trout", "walleye", "muskie", "musky",
    "tackle", "lure", "bait", "wader", "waders",
    # Paddle sports
    "kayak", "kayaking", "paddle", "paddling", "canoe", "canoeing",
    "raft", "rafting", "whitewater", "rowboat", "marina",
    # Hiking / trail
    "hike", "hiking", "hiker", "trail", "trails", "trailhead",
    "trekking", "trek",
    # Climbing
    "climb", "climbing", "climber", "bouldering", "rappel", "rappelling",
    "canyoneer", "canyoneering",
    # Biking
    "mountainbike", "mtb", "bikepacking",
    # Shooting sports
    "shoot", "shooting",
    "firearm", "firearms", "gunsmith",
    "archery", "archer", "archeryrange",
    "crossbow", "bowshop",
    "ammo", "ammunition",
    "decoy", "decoys",
    # Gear / retail signals
    "outfitter", "outfitters",
    "gunshop", "gunstore",
    # Venues
    "lodge",
    "resort",
    "sportsman", "sportsmen", "sportswoman", "sportingclub",
    "wilderness",
    "preserve",
    "retreat",
    "duckclub", "huntingclub", "fishingclub",
    # Broad outdoor
    "outdoor", "outdoors",
    "sporting",
    "backpack", "backpacking",
    "mountaineer",
}

# Proper-noun / brand-like seeds that word tokenization will not infer.
# Keep this list curated; broad keyword matching would swamp the queue again.
OUTDOOR_PROPER_NOUNS = {
    "piragis",
    "backcountry",
    "cabelas",
    "basspro",
    "orvis",
    "sitka",
    "kuiu",
    "simms",
    "patagonia",
    "arcteryx",
    "blackdiamond",
    "bigagnes",
    "osprey",
    "marmot",
    "salomon",
    "burton",
    "rossignol",
    "rapala",
    "shimano",
    "fenwick",
    "sage",
    "redington",
    "gloomis",
    "stcroix",
    "vortex",
    "leupold",
    "browning",
    "benelli",
    "mathews",
    "hoyt",
    "pse",
    "yeti",
    "pelican",
    "hobie",
    "nrs",
    "yakima",
    "thule",
    "kuat",
    "deuter",
    "kelty",
    "thermarest",
    "msr",
    "nemo",
    "hydroflask",
    "smartwool",
    "icebreaker",
    "prana",
    "chaco",
    "teva",
    "keen",
    "merrell",
    "danner",
    "oboz",
    "hoka",
    "saucony",
    "lasportiva",
    "scarpa",
    "petzl",
    "edelrid",
    "metolius",
    "mammut",
    "volkl",
    "k2",
    "armada",
    "libtech",
    "nitro",
    "arbor",
}


def _matches_keywords(domain: str) -> bool:
    """Return True if wordninja tokens or curated proper-noun seeds match."""
    label = domain.rsplit(".", 1)[0].lower()
    parts = [part for part in re.split(r"[^a-z0-9]+", label) if part]
    compact = "".join(parts)

    if compact in OUTDOOR_KEYWORDS or any(part in OUTDOOR_KEYWORDS for part in parts):
        return True

    for proper_noun in OUTDOOR_PROPER_NOUNS:
        if compact == proper_noun or compact.startswith(f"the{proper_noun}"):
            return True
        if len(proper_noun) >= 5 and (compact.startswith(proper_noun) or compact.endswith(proper_noun)):
            return True

    for part in parts:
        for token in wordninja.split(part):
            if token in OUTDOOR_KEYWORDS:
                return True
    return False


def _filter_keyword_batch(domains: list[str]) -> tuple[int, list[str]]:
    return len(domains), [domain for domain in domains if _matches_keywords(domain)]


def _iter_chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _keyword_filter_domains(domains: list[str], workers: int = 1) -> list[str]:
    if not domains:
        return []
    if workers <= 1 or len(domains) <= KEYWORD_BATCH_SIZE:
        return [domain for domain in domains if _matches_keywords(domain)]

    total_chunks = (len(domains) + KEYWORD_BATCH_SIZE - 1) // KEYWORD_BATCH_SIZE
    progress_every = max(1, total_chunks // 20)
    max_pending = max(1, workers * 2)
    chunk_iter = iter(_iter_chunks(domains, KEYWORD_BATCH_SIZE))
    filtered: list[str] = []
    pending = set()
    processed = 0
    completed_chunks = 0

    print(
        f"[domain_scanner] Keyword filter: {len(domains)} domains "
        f"across {workers} processes",
        flush=True,
    )

    with ProcessPoolExecutor(max_workers=workers) as executor:
        while len(pending) < max_pending:
            try:
                pending.add(executor.submit(_filter_keyword_batch, next(chunk_iter)))
            except StopIteration:
                break

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                checked, matches = future.result()
                processed += checked
                completed_chunks += 1
                filtered.extend(matches)

                if completed_chunks % progress_every == 0 or completed_chunks == total_chunks:
                    print(
                        f"[domain_scanner]   keyword progress: "
                        f"{processed}/{len(domains)} checked, {len(filtered)} matched",
                        flush=True,
                    )

                try:
                    pending.add(executor.submit(_filter_keyword_batch, next(chunk_iter)))
                except StopIteration:
                    pass

    return filtered


def _tld(domain: str) -> str:
    parts = domain.rsplit(".", 1)
    return f".{parts[-1]}" if len(parts) > 1 else ""


def _normalize_domain(value: str) -> str | None:
    value = value.strip().lower().lstrip("\ufeff").strip('"').strip("'")
    match = DOMAIN_RE.match(value)
    if not match:
        return None
    domain = match.group(1).rstrip(".")
    if "." not in domain or ".." in domain:
        return None
    return domain


def _domains_from_text(text: str) -> list[str]:
    domains: list[str] = []
    domain_idx = 0
    header_checked = False

    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        cells = [cell.strip() for cell in row]

        if not header_checked:
            header_checked = True
            lowered = [cell.lower() for cell in cells]
            for header in ("domain", "domain name", "domain_name"):
                if header in lowered:
                    domain_idx = lowered.index(header)
                    break
            if domain_idx < len(cells) and cells[domain_idx].lower() in {"domain", "domain name", "domain_name"}:
                continue

        candidate = cells[domain_idx] if domain_idx < len(cells) else cells[0]
        domain = _normalize_domain(candidate)
        if domain:
            domains.append(domain)

    return domains


def _domains_from_bytes(content: bytes, filename: str = "") -> list[str]:
    name = filename.lower()
    if zipfile.is_zipfile(io.BytesIO(content)):
        domains: list[str] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                domains.extend(_domains_from_bytes(zf.read(member), member))
        return domains

    if name.endswith(".gz") or content[:2] == b"\x1f\x8b":
        return _domains_from_bytes(gzip.decompress(content), name.removesuffix(".gz"))

    text = content.decode("utf-8", errors="ignore")
    return _domains_from_text(text)


def _fetch_whoisds(date: datetime) -> list[str]:
    date_str = date.strftime("%Y-%m-%d") + ".zip"
    encoded = base64.b64encode(date_str.encode()).decode()
    url = f"https://www.whoisds.com/whois-database/newly-registered-domains/{encoded}/nrd"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                return [line.decode().strip().lower() for line in f if line.strip()]
    except Exception as e:
        print(f"[domain_scanner] WhoisDS fetch failed for {date.strftime('%Y-%m-%d')}: {e}")
        return []


def _fetch_domainkits(date: datetime) -> list[str]:
    """
    Fetch DomainKits data from a user-supplied URL template.

    DomainKits currently protects downloads behind login/Cloudflare, so this
    intentionally does not guess private endpoints. If you copy a real download
    URL, set DOMAINKITS_URL_TEMPLATE with {date} or {ymd} placeholders.
    """
    template = os.environ.get("DOMAINKITS_URL_TEMPLATE")
    if not template:
        print(
            "[domain_scanner] DomainKits direct download needs DOMAINKITS_URL_TEMPLATE; "
            "use --domain-source domainkits-file with downloaded files for now",
            flush=True,
        )
        return []

    url = template.format(date=date.strftime("%Y-%m-%d"), ymd=date.strftime("%Y%m%d"))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        domains = _domains_from_bytes(resp.content, url)
        if not domains and b"cf-mitigated" in resp.content[:5000].lower():
            print(f"[domain_scanner] DomainKits Cloudflare challenge for {date.strftime('%Y-%m-%d')}", flush=True)
        return domains
    except Exception as e:
        print(f"[domain_scanner] DomainKits fetch failed for {date.strftime('%Y-%m-%d')}: {e}")
        return []


def _infer_source_date(path: Path, fallback: datetime) -> str:
    name = path.name
    dashed = re.search(r"(20\d{2}-\d{2}-\d{2})", name)
    if dashed:
        return dashed.group(1)
    compact = re.search(r"(20\d{6})", name)
    if compact:
        raw = compact.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return fallback.strftime("%Y-%m-%d")


def _fetch_domainkits_file_batches(path: str, dates: list[datetime]) -> list[tuple[str, list[str]]]:
    root = Path(path).expanduser()
    if not root.exists():
        print(f"[domain_scanner] DomainKits path does not exist: {root}", flush=True)
        return []

    if root.is_file():
        domains = _domains_from_bytes(root.read_bytes(), root.name)
        source_date = _infer_source_date(root, dates[0])
        return [(source_date, domains)]

    files = sorted(p for p in root.iterdir() if p.is_file())
    batches: list[tuple[str, list[str]]] = []
    for date in dates:
        dashed = date.strftime("%Y-%m-%d")
        compact = date.strftime("%Y%m%d")
        matched = [p for p in files if dashed in p.name or compact in p.name]
        if not matched:
            print(f"[domain_scanner] No DomainKits files found for {dashed} in {root}", flush=True)
            continue

        daily: list[str] = []
        for file in matched:
            daily.extend(_domains_from_bytes(file.read_bytes(), file.name))
        batches.append((dashed, daily))

    return batches


def _fetch_domainsmonitor_live() -> list[str]:
    """Fetch today's newly registered domains from domains-monitor.com."""
    token = os.environ.get("DOMAINS_MONITOR_TOKEN")
    if not token:
        print("[domain_scanner] DOMAINS_MONITOR_TOKEN is required for --domain-source domainsmonitor", flush=True)
        return []

    url = os.environ.get(
        "DOMAINS_MONITOR_DAILY_URL",
        f"https://domains-monitor.com/api/v1/{token}/get/dailyupdate/list/text/",
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        return _domains_from_bytes(resp.content, url)
    except Exception as e:
        print(f"[domain_scanner] domains-monitor daily fetch failed: {e}", flush=True)
        return []


def _fetch_domainsmonitor_backfill(local_file: str) -> list[str]:
    """Read a downloaded domains-monitor text/zip/gz/csv file."""
    path = Path(local_file).expanduser()
    if not path.exists():
        print(f"[domain_scanner] domains-monitor file does not exist: {path}", flush=True)
        return []
    if path.is_dir():
        domains: list[str] = []
        for file in sorted(p for p in path.iterdir() if p.is_file()):
            domains.extend(_domains_from_bytes(file.read_bytes(), file.name))
        return domains
    return _domains_from_bytes(path.read_bytes(), path.name)


def _resolve_domain(domain: str) -> str | None:
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for info in infos:
        ip = info[4][0]
        if "." in ip:
            return ip
    return infos[0][4][0] if infos else None


def _resolve_domains(domains: list[str]) -> dict[str, str]:
    socket.setdefaulttimeout(DNS_TIMEOUT_SECONDS)
    resolved: dict[str, str] = {}
    total = len(domains)
    completed = 0
    with ThreadPoolExecutor(max_workers=DNS_WORKERS) as executor:
        futures = {executor.submit(_resolve_domain, d): d for d in domains}
        for future in as_completed(futures):
            completed += 1
            ip = future.result()
            if ip:
                resolved[futures[future]] = ip
            if completed % DNS_PROGRESS_INTERVAL == 0 or completed == total:
                print(
                    f"[domain_scanner]   DNS progress: "
                    f"{completed}/{total} checked, {len(resolved)} resolved",
                    flush=True,
                )
    return resolved


def _geolocate_ips(ips: list[str]) -> dict[str, str]:
    country_by_ip: dict[str, str] = {}
    total_batches = (len(ips) + GEO_BATCH_SIZE - 1) // GEO_BATCH_SIZE
    for i in range(0, len(ips), GEO_BATCH_SIZE):
        batch = ips[i:i + GEO_BATCH_SIZE]
        batch_num = (i // GEO_BATCH_SIZE) + 1
        for attempt in range(1, GEO_BATCH_RETRIES + 1):
            try:
                resp = requests.post(
                    IP_API_BATCH_URL,
                    json=[{"query": ip, "fields": "status,countryCode,query"} for ip in batch],
                    headers=HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
                for result in resp.json():
                    if result.get("status") == "success" and result.get("query"):
                        country_by_ip[result["query"]] = result.get("countryCode", "")
                break
            except Exception as e:
                if attempt == GEO_BATCH_RETRIES:
                    print(f"[domain_scanner] Geo batch failed after {attempt} attempts: {e} — leaving batch for retry", flush=True)
                    break
                sleep_for = GEO_RETRY_SLEEP_SECONDS * attempt
                print(f"[domain_scanner] Geo batch failed: {e} — retrying in {sleep_for}s", flush=True)
                time.sleep(sleep_for)
        if batch_num % GEO_PROGRESS_INTERVAL == 0 or batch_num == total_batches:
            print(
                f"[domain_scanner]   Geo progress: "
                f"{min(i + GEO_BATCH_SIZE, len(ips))}/{len(ips)} IPs checked "
                f"({batch_num}/{total_batches} batches), {len(country_by_ip)} located",
                flush=True,
            )
        if i + GEO_BATCH_SIZE < len(ips):
            time.sleep(GEO_BATCH_SLEEP_SECONDS)
    return country_by_ip


def _run_geo_phase(defer_site_days: int = 0, geo_limit: int = 0) -> None:
    due = domain_store.get_due(["new", "geo_pending"])
    if not due:
        return
    if geo_limit > 0:
        due = due[:geo_limit]
    print(f"[domain_scanner] Geo phase: {len(due)} domains", flush=True)

    ip_by_domain = _resolve_domains([r["domain"] for r in due])
    print(f"[domain_scanner]   {len(ip_by_domain)} resolved", flush=True)

    country_by_ip = _geolocate_ips(sorted(set(ip_by_domain.values())))

    for row in due:
        domain = row["domain"]
        now = datetime.utcnow().isoformat()
        count = row["attempt_count"] + 1
        ip = ip_by_domain.get(domain)

        if not ip:
            domain_store.update_domain(domain, status="geo_pending", last_checked_at=now, attempt_count=count)
            continue

        country = country_by_ip.get(ip)
        if not country:
            domain_store.update_domain(domain, status="geo_pending", resolved_ip=ip,
                                       last_checked_at=now, attempt_count=count)
            continue

        if country != "US":
            domain_store.update_domain(domain, status="non_us", resolved_ip=ip, country_code=country,
                                       last_checked_at=now, attempt_count=count)
        else:
            next_check = (datetime.utcnow() + timedelta(days=defer_site_days)).isoformat() if defer_site_days else None
            domain_store.update_domain(domain, status="site_pending", resolved_ip=ip, country_code="US",
                                       website_url=f"https://{domain}", next_check_at=next_check,
                                       last_checked_at=now, attempt_count=count)


def _check_domain(row: dict) -> dict:
    """Fetch and classify one domain. Runs in a thread pool worker."""
    domain = row["domain"]
    url = row["website_url"] or f"https://{domain}"
    site = validate_site(url)
    if site["pending_reason"]:
        return {
            "row": row,
            "url": url,
            "pending_reason": site["pending_reason"],
            "redirected_to": site.get("redirected_to", ""),
            "redirect_domain": site.get("redirect_domain", ""),
            "phone": site.get("phone", ""),
            "email": site.get("email", ""),
        }
    verdict = classify_domain(domain, site["content"])
    verdict["redirected_to"] = site.get("redirected_to", "")
    verdict["redirect_domain"] = site.get("redirect_domain", "")
    verdict["phone"] = site.get("phone", "")
    verdict["email"] = site.get("email", "")
    return {"row": row, "url": url, "pending_reason": None, "verdict": verdict}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _run_site_phase(filing_date: str, site_limit: int = 0, rescrape_days: int = 30) -> list[Filing]:
    due = domain_store.get_due(["site_pending"])
    if not due:
        return []
    if site_limit > 0:
        due = due[:site_limit]
    total = len(due)
    print(f"[domain_scanner] Site phase: {total} domains ({SITE_WORKERS} workers)", flush=True)

    matched: list[Filing] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=SITE_WORKERS) as executor:
        futures = {executor.submit(_check_domain, row): row for row in due}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            row = result["row"]
            domain = row["domain"]
            url = result["url"]
            now = datetime.utcnow().isoformat()
            count = row["attempt_count"] + 1
            prefix = f"[site {completed}/{total}] {domain}"

            if result["pending_reason"]:
                interval_days = 7 * count
                next_check_dt = datetime.utcnow() + timedelta(days=interval_days)
                expires_at = _parse_iso(row.get("expires_at"))

                if expires_at and next_check_dt > expires_at:
                    print(f"{prefix} ⏳ expired — {result['pending_reason']}", flush=True)
                    domain_store.update_domain(domain, status="expired",
                                               last_error=result["pending_reason"],
                                               redirected_to=result.get("redirected_to", ""),
                                               redirect_domain=result.get("redirect_domain", ""),
                                               phone=result.get("phone", ""),
                                               email=result.get("email", ""),
                                               last_checked_at=now, attempt_count=count)
                else:
                    print(
                        f"{prefix} ⏳ pending — {result['pending_reason']} "
                        f"(retry in {interval_days}d)",
                        flush=True,
                    )
                    domain_store.update_domain(domain, status="site_pending",
                                               last_error=result["pending_reason"],
                                               redirected_to=result.get("redirected_to", ""),
                                               redirect_domain=result.get("redirect_domain", ""),
                                               phone=result.get("phone", ""),
                                               email=result.get("email", ""),
                                               next_check_at=next_check_dt.isoformat(),
                                               last_checked_at=now, attempt_count=count)
            elif result["verdict"]["match"]:
                v = result["verdict"]
                location    = v.get("location", "")
                established = v.get("established", "")
                is_template = 1 if v.get("is_template") else 0
                ecom_only   = 1 if v.get("ecom_only") else 0
                score          = v.get("score", 0)
                score_cat      = v.get("score_category", "")
                redirected_to  = v.get("redirected_to", "")
                redirect_domain = v.get("redirect_domain", "")
                phone          = v.get("phone", "")
                email          = v.get("email", "")
                flags = " ".join(filter(None, [
                    f"[{score_cat}:{score}]",
                    f"[{location}]" if location else "",
                    f"[est. {established}]" if established else "",
                    f"[redirect: {redirect_domain}]" if redirect_domain else "",
                    f"[phone: {phone}]" if phone else "",
                    f"[email: {email}]" if email else "",
                    "[TEMPLATE]" if is_template else "",
                    "[ECOM]" if ecom_only else "",
                ]))
                print(f"{prefix} ✓ YES — {v['reason']}  {flags}", flush=True)
                next_rescrape = (datetime.utcnow() + timedelta(days=rescrape_days)).isoformat()
                domain_store.update_domain(domain, status="matched",
                                           classification_reason=v["reason"],
                                           location=location, established=established,
                                           is_template=is_template, ecom_only=ecom_only,
                                           score=score, score_category=score_cat,
                                           redirected_to=redirected_to,
                                           redirect_domain=redirect_domain,
                                           phone=phone, email=email,
                                           next_check_at=next_rescrape,
                                           classified_at=now, last_checked_at=now, attempt_count=count)
                matched.append(Filing(
                    name=domain, city=location, filing_date=filing_date,
                    website=url, verdict=result["verdict"],
                    redirected_to=redirected_to, redirect_domain=redirect_domain,
                    phone=phone, email=email,
                ))
            else:
                v = result["verdict"]
                if v.get("reason") == "classification error":
                    print(f"{prefix} ⏳ pending — LLM error, will retry", flush=True)
                    domain_store.update_domain(domain, status="site_pending",
                                               last_error="classification error",
                                               last_checked_at=now, attempt_count=count)
                    continue
                score     = v.get("score", 0)
                score_cat = v.get("score_category", "")
                score_tag = f" [{score_cat}:{score}]" if score >= 40 else ""
                print(f"{prefix} ✗ NO — {v['reason']}{score_tag}", flush=True)
                next_rescrape = (datetime.utcnow() + timedelta(days=rescrape_days)).isoformat()
                domain_store.update_domain(domain, status="not_outdoor",
                                           classification_reason=v["reason"],
                                           score=score, score_category=score_cat,
                                           redirected_to=v.get("redirected_to", ""),
                                           redirect_domain=v.get("redirect_domain", ""),
                                           phone=v.get("phone", ""),
                                           email=v.get("email", ""),
                                           next_check_at=next_rescrape,
                                           classified_at=now, last_checked_at=now, attempt_count=count)

    return matched


def scan_new_domains(
    days: int = 1,
    limit: int = 1000,
    keyword_filter: bool = False,
    keyword_workers: int = 1,
    start_date: str | None = None,
    defer_site_days: int = 0,
    source: str = "whoisds",
    domainkits_path: str | None = None,
    domainsmonitor_path: str | None = None,
    skip_import: bool = False,
    skip_geo: bool = False,
    site_limit: int = 0,
    geo_limit: int = 0,
    rescrape_days: int = 30,
) -> tuple[list[Filing], dict]:
    """
    Run the full domain pipeline and return Filing objects for newly matched domains.

    Args:
        days:           Number of days to pull NRD lists for.
        limit:          Max newly discovered domains to upsert from the NRD feed (0 = no limit).
        keyword_filter: If True, only process domains whose name contains an outdoor keyword.
        keyword_workers: Processes to use for keyword filtering.
        start_date:     YYYY-MM-DD to start from, pulling forward `days` days. Defaults to yesterday.
        source:         NRD source: domainsmonitor, domainsmonitor-file, whoisds,
                        domainkits, or domainkits-file.
        domainkits_path: File or directory of DomainKits downloads for domainkits-file.
        domainsmonitor_path: File or directory of domains-monitor downloads for domainsmonitor-file.
        skip_import:    If True, skip source import/filtering and process queued SQLite rows.
    """
    domain_store.init_db()
    expired = domain_store.expire_stale()
    if expired:
        print(f"[domain_scanner] Expired {expired} stale tracked domains", flush=True)
    requeued = domain_store.requeue_rescrapes()
    if requeued:
        print(f"[domain_scanner] Requeued {requeued} domains for rescrape", flush=True)

    today = datetime.now()

    if start_date:
        base = datetime.strptime(start_date, "%Y-%m-%d")
        dates = [base + timedelta(days=i) for i in range(days)]
    else:
        dates = [today - timedelta(days=i) for i in range(1, days + 1)]

    downloaded_total = 0
    inserted_total = 0

    if skip_import:
        print("[domain_scanner] Skipping domain import; resuming queued domains from SQLite", flush=True)
    else:
        # 1. Download/import + TLD/keyword filter
        if source == "domainsmonitor-file":
            path = domainsmonitor_path or domainkits_path
            if not path:
                print("[domain_scanner] --domainsmonitor-path is required with --domain-source domainsmonitor-file", flush=True)
                batches = []
            else:
                batches = [(today.strftime("%Y-%m-%d"), _fetch_domainsmonitor_backfill(path))]
        elif source == "domainkits-file":
            if not domainkits_path:
                print("[domain_scanner] --domainkits-path is required with --domain-source domainkits-file", flush=True)
                batches = []
            else:
                batches = _fetch_domainkits_file_batches(domainkits_path, dates)
        elif source == "domainsmonitor":
            print("[domain_scanner] Downloading daily NRD list from domains-monitor...", flush=True)
            batches = [(today.strftime("%Y-%m-%d"), _fetch_domainsmonitor_live())]
        else:
            fetcher = _fetch_domainkits if source == "domainkits" else _fetch_whoisds
            batches = []
            for date in dates:
                print(f"[domain_scanner] Downloading {date.strftime('%Y-%m-%d')} NRD list from {source}...", flush=True)
                daily = fetcher(date)
                batches.append((date.strftime("%Y-%m-%d"), daily))

        filtered_domains: list[tuple[str, str]] = []
        tld_total = 0
        keyword_total = 0
        for source_date, raw in batches:
            downloaded_total += len(raw)
            print(f"[domain_scanner]   {source_date}: {len(raw)} domains loaded", flush=True)

            tld_ok = [d for d in raw if _tld(d) in USA_TLDS]
            tld_total += len(tld_ok)

            if keyword_filter:
                tld_ok = _keyword_filter_domains(tld_ok, workers=keyword_workers)
            keyword_total += len(tld_ok)
            filtered_domains.extend((source_date, domain) for domain in tld_ok)

        random.shuffle(filtered_domains)
        if limit > 0:
            filtered_domains = filtered_domains[:limit]

        domains_by_date: dict[str, list[str]] = {}
        for source_date, domain in filtered_domains:
            domains_by_date.setdefault(source_date, []).append(domain)
        for source_date, domains in domains_by_date.items():
            inserted_total += domain_store.upsert_new(domains, source_date)

        print(f"[domain_scanner] {downloaded_total} domains loaded total", flush=True)
        print(f"[domain_scanner] {tld_total} after TLD filter", flush=True)
        if keyword_filter:
            print(f"[domain_scanner] {keyword_total} after keyword filter", flush=True)
        print(f"[domain_scanner] {inserted_total} new domains added to queue", flush=True)

    # 3. Geo phase: new + geo_pending → site_pending or non_us
    if skip_geo:
        print("[domain_scanner] Skipping geo phase", flush=True)
    else:
        _run_geo_phase(defer_site_days=defer_site_days, geo_limit=geo_limit)

    # 4. Site phase: site_pending → matched or not_outdoor
    filing_date = today.strftime("%m/%d/%Y")
    matched = _run_site_phase(filing_date, site_limit=site_limit, rescrape_days=rescrape_days)
    print(f"[domain_scanner] {len(matched)} newly matched domains", flush=True)

    stats = {
        "downloaded": downloaded_total,
        "inserted": inserted_total,
        "matched": len(matched),
        "expired": expired,
    }
    return matched, stats

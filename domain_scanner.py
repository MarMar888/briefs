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
# NOTE: This set is the source of truth for the scanner. The website mirrors it for
# display in frontend/lib/keywords.ts — keep the two in sync when editing keywords.
OUTDOOR_KEYWORDS = {
    # Snow sports
    "ski", "skiing", "skier", "skis",
    "snowboard", "snowboarding", "snowboarder",
    "snowshoe", "snowshoeing", "snowmobile", "snowmobiling", "snowcat",
    "nordic", "telemark", "backcountry", "chalet", "alpine", "mogul",
    "sled", "sledding", "tubing",
    "biathlon", "iceclimb", "iceclimbing", "snowkite", "snowkiting", "snowpark",
    # Camping / overlanding / RV
    "camp", "camping", "camper", "campground", "campsite", "glamping",
    "overland", "overlanding", "basecamp", "bivouac",
    "rv", "cabin", "cabins", "yurt", "yurts", "tipi", "tipis",
    "backwoods", "hammock",
    # Hunting
    "hunt", "hunting", "hunter", "hunters",
    "bowhunt", "bowhunting", "bowhunter",
    "waterfowl", "upland", "muzzleloader",
    "taxidermy", "treestand", "treestands", "camo", "camouflage",
    "gunclub", "shootingrange", "gunrange", "trapshoot", "trapshooting", "skeet",
    "deer", "elk", "turkey", "pheasant", "dove", "duck", "goose", "antler", "antlers",
    "biggame", "trophy", "game", "gamebird", "wildfowl",
    "varmint", "predatorcalling", "trapper", "trapping",
    "falconry", "falconer",
    "rangefinder", "rangefinders",
    # Firearms / shooting
    "gun", "guns", "rifle", "rifles", "shotgun", "shotguns",
    "pistol", "pistols", "handgun", "handguns", "revolver",
    "shoot", "shooting", "shooter",
    "firearm", "firearms", "gunsmith", "gunsmithing",
    "archery", "archer", "archeryrange", "bowhunter",
    "crossbow", "bowshop", "bow", "bows",
    "ammo", "ammunition", "reloading",
    "decoy", "decoys",
    "gunshop", "gunstore", "armory",
    "suppressor", "suppressors", "silencer",
    "holster", "holsters",
    # Fishing
    "fish", "fishing", "fisherman", "fishermen", "angler", "angling",
    "flyfishing", "flyfish", "flyshop", "flyrod", "flytying",
    "icefishing",
    "trout", "walleye", "muskie", "musky", "bass", "salmon", "steelhead",
    "crappie", "bluegill", "catfish", "perch", "pike", "panfish",
    "tackle", "lure", "lures", "bait", "baits", "wader", "waders",
    "charter", "charters", "fishingcharter",
    "reel", "reels",
    "spey", "tenkara", "nymphing",
    "bowfishing", "bowfish",
    "floattrip", "floatfishing",
    # Diving / underwater
    "dive", "diving", "diver", "divers",
    "scuba",
    "snorkel", "snorkeling",
    "spearfish", "spearfishing",
    "freedive", "freediving",
    # Paddle sports
    "kayak", "kayaking", "kayaker", "paddle", "paddling", "paddleboard", "paddleboards",
    "canoe", "canoeing", "canoeist", "sup",
    "raft", "rafting", "rafter", "whitewater", "rowboat", "marina", "watercraft",
    "packraft", "packrafting",
    "float",
    # Hiking / trail / running
    "hike", "hiking", "hiker", "hikers", "trail", "trails", "trailhead",
    "trekking", "trek", "treks", "thru", "backpacker",
    "trailrun", "trailrunning",
    # Climbing
    "climb", "climbing", "climber", "climbers", "bouldering",
    "rappel", "rappelling", "canyoneer", "canyoneering", "crag",
    # Caving
    "caving", "spelunk", "spelunking",
    # Biking
    "bike", "bikes", "biking", "biker", "bikers", "cyclist", "cycling",
    "mountainbike", "mountainbiking", "mtb", "bikepacking", "bikeshop",
    "cyclocross",
    # ATV / offroad
    "atv", "utv", "offroad", "fourwheeler", "dirtbike", "dirtbiking",
    # Equestrian
    "horse", "horses", "horseback", "equestrian", "stable", "stables",
    "ranch", "ranches",
    "rodeo", "saddle", "saddles",
    "trailride", "trailriding",
    # Air / aerial sports
    "paraglide", "paragliding", "paraglider",
    "hangglide", "hanggliding",
    "skydive", "skydiving", "skydiver",
    "parasail", "parasailing",
    "gliding", "glider", "soaring",  # sub-tokens when wordninja splits hang/paragliding
    "kiting",  # sub-token when wordninja splits snowkiting/kitesurfing
    # Zip / adventure
    "zipline", "ziplines", "ziplining", "aerial", "ropescourse",
    "adventure", "adventures", "adventurer", "expedition", "expeditions",
    # Guiding / outfitting
    "guide", "guides", "guiding",
    "outfitter", "outfitters", "outfitting",
    # Gear / retail signals
    "gear",
    "sporting", "sportinggoods",
    "sport", "sports",
    "supply", "supplies",
    "rental", "rentals",
    "proshop",
    "tradingpost",
    "consignment",
    "closeout", "liquidation",
    "demo",
    # Venues / lodging
    "lodge", "lodges", "lodging",
    "resort", "resorts",
    "campground", "campgrounds",
    "sportsman", "sportsmen", "sportswoman", "sportingclub",
    "wilderness",
    "preserve",
    "retreat", "retreats",
    "duckclub", "huntingclub", "fishingclub",
    "marina", "marinas",
    "outpost",
    # Boating / watersports
    "boat", "boats", "boating", "boater",
    "sailboat", "pontoon", "johnboat", "bassboat",
    "dock", "docks", "pier", "launch",
    "waterski", "waterskiing", "wakeboard", "wakeboarding",
    "jetski", "waverunner",
    "surf", "surfing", "surfer", "surfboard",
    "windsurfing", "kitesurfing", "kitesurf",
    # Hunting accessories / blinds
    "groundblind", "huntingblind",
    "broadhead", "broadheads",
    "venison", "gameprocessing",
    "retriever", "spaniel",
    # Survival / bushcraft
    "survival", "survivalist", "bushcraft",
    "prepper", "preppers",
    "knife", "knives", "blade", "blades",
    "hatchet", "axe", "axes", "tomahawk",
    # Water treatment / hydration (survival & camping gear)
    "hydration", "hydrate", "canteen", "canteens",
    "filtration", "purifier", "purifiers", "purification", "potable",
    # Mountain biking extras
    "singletrack", "enduro", "gravel",
    # Bird watching
    "birding", "birdwatching", "birder",
    # Exploration / ecotourism
    "explore", "explorer", "exploration",
    "excursion", "excursions",
    "safari",
    "ecotour", "ecotourism",
    # Target sports
    "paintball", "airsoft",
    # Broad outdoor
    "outdoor", "outdoors",
    "backpack", "backpacking",
    "mountaineer", "mountaineering",
    "nature", "naturalist",
    "wildlife", "wildland", "wildlands",
    "portage",
    "mountain", "mountains",
    "river", "rivers",
    "lake", "lakes",
    "forest", "forests",
    "woods", "woodland",
}

# Broad set of proper nouns (animals, landscape, plants, minerals, regional terms).
# Any domain whose wordninja tokens contain one of these — and no generic business
# descriptor — is passed to the geo/scrape phase. Cast wide; LLM filters non-outdoor.
PROPER_NOUNS = {
    # Animals
    "moose", "elk", "bear", "deer", "wolf", "fox", "coyote", "bison", "buffalo",
    "ram", "buck", "doe", "stag", "boar", "hog",
    "eagle", "hawk", "falcon", "kestrel", "osprey", "heron", "loon", "crane",
    "owl", "raven", "crow", "jay", "wren", "swift", "harrier",
    "duck", "goose", "pheasant", "grouse", "turkey", "quail", "woodcock", "snipe",
    "trout", "bass", "pike", "salmon", "walleye", "perch", "muskie", "crappie",
    "catfish", "carp", "steelhead", "char", "tench",
    "cougar", "lynx", "bobcat", "panther", "puma",
    "otter", "beaver", "mink", "marten", "wolverine", "badger", "muskrat", "weasel",
    "bison", "musk", "caribou", "pronghorn", "bighorn",
    # Landscape / geography
    "ridge", "valley", "creek", "lake", "river", "pond", "bay", "cove", "water",
    "inlet", "harbor", "peak", "summit", "bluff", "cliff", "canyon", "gorge",
    "gulch", "ravine", "hollow", "meadow", "prairie", "tundra", "rapids",
    "falls", "shore", "marsh", "delta", "dune", "glen", "fen", "moor",
    "heath", "tor", "knoll", "butte", "mesa", "bench", "flats", "crossing",
    "fork", "bend", "run", "slough", "swamp", "bog",
    # Plants / trees
    "pine", "birch", "cedar", "spruce", "maple", "oak", "ash", "hemlock",
    "fir", "aspen", "willow", "alder", "cottonwood", "sage", "juniper",
    "hickory", "walnut", "chestnut", "beech", "elm", "larch", "tamarack",
    "locust", "sycamore", "poplar", "basswood", "ironwood",
    # Minerals / materials
    "granite", "flint", "iron", "copper", "silver", "slate", "obsidian",
    "quartz", "basalt", "shale", "amber", "jasper", "feldspar", "limestone",
    # Regional / geographic descriptors
    "northwoods", "northwood", "boundary", "portage", "voyageur", "quetico",
    "boreal", "highland", "lowland", "tidewater", "piedmont", "chaparral",
    "savanna", "steppe",
}

# Generic business descriptor tokens — if any are present, the proper-noun rule
# does not apply (the domain is likely a non-outdoor service business).
_GENERIC_BUSINESS_TOKENS = {
    "services", "service", "consulting", "consultant", "solutions", "solution",
    "systems", "system", "tech", "technology", "technologies", "digital",
    "media", "marketing", "design", "designs", "financial", "finance",
    "realty", "realtor", "dental", "medical", "health", "healthcare",
    "legal", "attorney", "law", "auto", "automotive", "construction",
    "electric", "electrical", "plumbing", "cleaning", "management",
    "agency", "software", "hosting", "staffing", "recruiting", "logistics",
    "accounting", "mortgage", "roofing", "flooring", "painting", "landscaping",
    "pest", "hvac", "insurance",
}

# Domain label suffixes that indicate directories/aggregators, not actual businesses.
_DIRECTORY_SUFFIXES = {"finder", "directory", "listings", "locator", "search"}


def _is_junk_domain(domain: str) -> bool:
    """Return True for domain patterns that are structurally junk before scraping."""
    label = domain.rsplit(".", 1)[0].lower()

    # Double hyphens are a strong counterfeit signal (e.g. brand--name.com)
    if "--" in label:
        return True

    parts = [part for part in re.split(r"[^a-z0-9]+", label) if part]

    # Directory/aggregator suffix (e.g. archeryfinder, huntingdirectory)
    if parts and parts[-1] in _DIRECTORY_SUFFIXES:
        return True

    return False


def _matches_keywords(domain: str) -> bool:
    """Return True if the domain should be scraped.

    Default path: any proper noun token (animal, landscape, plant, mineral,
    regional term) with no generic business descriptor present.
    Keyword path: explicit outdoor activity word (kept as additional signal).
    Either path triggers inclusion.
    """
    if _is_junk_domain(domain):
        return False

    label = domain.rsplit(".", 1)[0].lower()
    parts = [part for part in re.split(r"[^a-z0-9]+", label) if part]

    # Tokenize all hyphen/number-separated parts together
    all_tokens: list[str] = []
    for part in parts:
        all_tokens.extend(wordninja.split(part) or [part])

    # Default path: proper noun present, no generic business descriptor
    if (any(t in PROPER_NOUNS for t in all_tokens)
            and not any(t in _GENERIC_BUSINESS_TOKENS for t in all_tokens)):
        return True

    # Keyword path: explicit outdoor activity word
    if any(t in OUTDOOR_KEYWORDS for t in all_tokens):
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


def _run_geo_phase(defer_site_days: int = 0, geo_limit: int = 0, keyword_filter: bool = False) -> dict:
    """Run geo phase. Returns counts: geo_us, geo_non_us, geo_failed."""
    due = domain_store.get_due(["new", "geo_pending"])
    if not due:
        return {"geo_us": 0, "geo_non_us": 0, "geo_failed": 0}

    if keyword_filter:
        now = datetime.utcnow().isoformat()
        keyword_results = {r["domain"]: _matches_keywords(r["domain"]) for r in due}
        rejected = [r for r in due if not keyword_results[r["domain"]]]
        if rejected:
            print(f"[domain_scanner]   Geo keyword filter: skipping {len(rejected)} non-keyword domains", flush=True)
            domain_store.batch_update_domains([
                {"domain": r["domain"], "status": "not_outdoor",
                 "classification_reason": "no keyword match in domain name",
                 "classified_at": now, "last_checked_at": now}
                for r in rejected
            ])
        due = [r for r in due if keyword_results[r["domain"]]]

    if geo_limit > 0:
        due = due[:geo_limit]
    if not due:
        return {"geo_us": 0, "geo_non_us": 0, "geo_failed": 0}
    print(f"[domain_scanner] Geo phase: {len(due)} domains", flush=True)

    ip_by_domain = _resolve_domains([r["domain"] for r in due])
    print(f"[domain_scanner]   {len(ip_by_domain)} resolved", flush=True)

    country_by_ip = _geolocate_ips(sorted(set(ip_by_domain.values())))

    geo_us = geo_non_us = geo_failed = 0
    now = datetime.utcnow().isoformat()
    updates = []
    for row in due:
        domain = row["domain"]
        count = row["attempt_count"] + 1
        ip = ip_by_domain.get(domain)

        if not ip:
            geo_failed += 1
            updates.append({"domain": domain, "status": "geo_pending", "last_checked_at": now, "attempt_count": count})
            continue

        country = country_by_ip.get(ip)
        if not country:
            geo_failed += 1
            updates.append({"domain": domain, "status": "geo_pending", "resolved_ip": ip,
                            "last_checked_at": now, "attempt_count": count})
            continue

        if country != "US":
            geo_non_us += 1
            updates.append({"domain": domain, "status": "non_us", "resolved_ip": ip,
                            "country_code": country, "last_checked_at": now, "attempt_count": count})
        else:
            geo_us += 1
            next_check = (datetime.utcnow() + timedelta(days=defer_site_days)).isoformat() if defer_site_days else None
            updates.append({"domain": domain, "status": "site_pending", "resolved_ip": ip,
                            "country_code": "US", "website_url": f"https://{domain}",
                            "next_check_at": next_check, "last_checked_at": now, "attempt_count": count})

    domain_store.batch_update_domains(updates)
    return {"geo_us": geo_us, "geo_non_us": geo_non_us, "geo_failed": geo_failed}


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


def _run_site_phase(filing_date: str, site_limit: int = 0, rescrape_days: int = 30) -> tuple[list[Filing], dict]:
    due = domain_store.get_due(["site_pending"])
    if not due:
        return [], {"random_processed": 0, "random_matched": 0, "keyword_processed": 0, "keyword_matched": 0}
    if site_limit > 0:
        due = due[:site_limit]
    total = len(due)
    print(f"[domain_scanner] Site phase: {total} domains ({SITE_WORKERS} workers)", flush=True)

    matched: list[Filing] = []
    completed = 0
    random_processed = sum(1 for r in due if r.get("random_sample"))
    random_matched = 0
    site_not_outdoor = 0
    site_pending_retry = 0
    pending_updates: list[dict] = []
    SITE_BATCH_SIZE = 50

    def _flush_site_updates():
        if pending_updates:
            domain_store.batch_update_domains(pending_updates)
            pending_updates.clear()

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
                    pending_updates.append({"domain": domain, "status": "expired",
                                            "last_error": result["pending_reason"],
                                            "redirected_to": result.get("redirected_to", ""),
                                            "redirect_domain": result.get("redirect_domain", ""),
                                            "phone": result.get("phone", ""),
                                            "email": result.get("email", ""),
                                            "last_checked_at": now, "attempt_count": count})
                else:
                    print(
                        f"{prefix} ⏳ pending — {result['pending_reason']} "
                        f"(retry in {interval_days}d)",
                        flush=True,
                    )
                    site_pending_retry += 1
                    pending_updates.append({"domain": domain, "status": "site_pending",
                                            "last_error": result["pending_reason"],
                                            "redirected_to": result.get("redirected_to", ""),
                                            "redirect_domain": result.get("redirect_domain", ""),
                                            "phone": result.get("phone", ""),
                                            "email": result.get("email", ""),
                                            "next_check_at": next_check_dt.isoformat(),
                                            "last_checked_at": now, "attempt_count": count})
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
                pending_updates.append({"domain": domain, "status": "matched",
                                        "classification_reason": v["reason"],
                                        "location": location, "established": established,
                                        "is_template": is_template, "ecom_only": ecom_only,
                                        "score": score, "score_category": score_cat,
                                        "redirected_to": redirected_to,
                                        "redirect_domain": redirect_domain,
                                        "phone": phone, "email": email,
                                        "next_check_at": next_rescrape,
                                        "classified_at": now, "last_checked_at": now,
                                        "attempt_count": count})
                if row.get("random_sample"):
                    random_matched += 1
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
                    pending_updates.append({"domain": domain, "status": "site_pending",
                                            "last_error": "classification error",
                                            "last_checked_at": now, "attempt_count": count})
                else:
                    site_not_outdoor += 1
                    score     = v.get("score", 0)
                    score_cat = v.get("score_category", "")
                    score_tag = f" [{score_cat}:{score}]" if score >= 40 else ""
                    print(f"{prefix} ✗ NO — {v['reason']}{score_tag}", flush=True)
                    next_rescrape = (datetime.utcnow() + timedelta(days=rescrape_days)).isoformat()
                    pending_updates.append({"domain": domain, "status": "not_outdoor",
                                            "classification_reason": v["reason"],
                                            "score": score, "score_category": score_cat,
                                            "redirected_to": v.get("redirected_to", ""),
                                            "redirect_domain": v.get("redirect_domain", ""),
                                            "phone": v.get("phone", ""),
                                            "email": v.get("email", ""),
                                            "next_check_at": next_rescrape,
                                            "classified_at": now, "last_checked_at": now,
                                            "attempt_count": count})

            if len(pending_updates) >= SITE_BATCH_SIZE:
                _flush_site_updates()

    _flush_site_updates()

    keyword_processed = total - random_processed
    keyword_matched = len(matched) - random_matched
    site_stats = {
        "site_processed": total,
        "site_not_outdoor": site_not_outdoor,
        "site_pending_retry": site_pending_retry,
        "random_processed": random_processed,
        "random_matched": random_matched,
        "keyword_processed": keyword_processed,
        "keyword_matched": keyword_matched,
    }
    return matched, site_stats


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
    tld_total = 0
    keyword_total = 0
    random_inserted = 0

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

        all_tld_filtered: list[tuple[str, str]] = []  # all .com/.net/.us domains
        keyword_matched_domains: list[tuple[str, str]] = []
        for source_date, raw in batches:
            downloaded_total += len(raw)
            print(f"[domain_scanner]   {source_date}: {len(raw)} domains loaded", flush=True)

            tld_ok = [d for d in raw if _tld(d) in USA_TLDS]
            tld_total += len(tld_ok)

            if keyword_filter:
                kw = _keyword_filter_domains(tld_ok, workers=keyword_workers)
                keyword_matched_domains.extend((source_date, d) for d in kw)
                all_tld_filtered.extend((source_date, d) for d in tld_ok)
            else:
                keyword_matched_domains.extend((source_date, d) for d in tld_ok)

        keyword_total = len(keyword_matched_domains)

        # Insert keyword-matched domains (subject to limit)
        random.shuffle(keyword_matched_domains)
        if limit > 0:
            keyword_matched_domains = keyword_matched_domains[:limit]

        domains_by_date: dict[str, list[str]] = {}
        for source_date, domain in keyword_matched_domains:
            domains_by_date.setdefault(source_date, []).append(domain)
        for source_date, domains in domains_by_date.items():
            inserted_total += domain_store.upsert_new(domains, source_date, random_sample=False)

        # Random sample from non-keyword domains — gives opaque brand names a shot at classification.
        # Set RANDOM_SAMPLE_SIZE=0 to disable. Tracked separately in hit-rate logs so you can see
        # whether the random sample is finding leads that keyword filter would have missed.
        if keyword_filter:
            random_sample_size = int(os.environ.get("RANDOM_SAMPLE_SIZE", "750"))
            if random_sample_size > 0:
                keyword_set = {d for _, d in keyword_matched_domains}
                non_keyword = [(sd, d) for sd, d in all_tld_filtered if d not in keyword_set]
                sample = random.sample(non_keyword, min(random_sample_size, len(non_keyword)))
                print(f"[domain_scanner] Random sample: {len(sample)} non-keyword domains added", flush=True)
                sample_by_date: dict[str, list[str]] = {}
                for source_date, domain in sample:
                    sample_by_date.setdefault(source_date, []).append(domain)
                for source_date, domains in sample_by_date.items():
                    n = domain_store.upsert_new(domains, source_date, random_sample=True)
                    random_inserted += n
                    inserted_total += n

        print(f"[domain_scanner] {downloaded_total} domains loaded total", flush=True)
        print(f"[domain_scanner] {tld_total} after TLD filter", flush=True)
        if keyword_filter:
            print(f"[domain_scanner] {keyword_total} after keyword filter", flush=True)
            if random_inserted:
                print(f"[domain_scanner] {random_inserted} random-sample domains added", flush=True)
        print(f"[domain_scanner] {inserted_total} new domains added to queue", flush=True)

    # 3. Geo phase: new + geo_pending → site_pending or non_us
    geo_stats: dict = {"geo_us": 0, "geo_non_us": 0, "geo_failed": 0}
    if skip_geo:
        print("[domain_scanner] Skipping geo phase", flush=True)
    else:
        geo_stats = _run_geo_phase(defer_site_days=defer_site_days, geo_limit=geo_limit, keyword_filter=keyword_filter)

    # 4. Site phase: site_pending → matched or not_outdoor
    filing_date = today.strftime("%m/%d/%Y")
    matched, site_stats = _run_site_phase(filing_date, site_limit=site_limit, rescrape_days=rescrape_days)
    print(f"[domain_scanner] {len(matched)} newly matched domains", flush=True)

    # Log random sample vs keyword effectiveness so we can evaluate whether the sample is pulling weight
    if site_stats["random_processed"] > 0:
        rs_pct = 100 * site_stats["random_matched"] / site_stats["random_processed"]
        print(
            f"[domain_scanner] Random sample hit rate: "
            f"{site_stats['random_matched']}/{site_stats['random_processed']} ({rs_pct:.1f}%)",
            flush=True,
        )
    if site_stats["keyword_processed"] > 0:
        kw_pct = 100 * site_stats["keyword_matched"] / site_stats["keyword_processed"]
        print(
            f"[domain_scanner] Keyword match hit rate: "
            f"{site_stats['keyword_matched']}/{site_stats['keyword_processed']} ({kw_pct:.1f}%)",
            flush=True,
        )

    stats = {
        "downloaded": downloaded_total,
        "tld_filtered": tld_total,
        "keyword_filtered": keyword_total,
        "random_inserted": random_inserted,
        "inserted": inserted_total,
        **geo_stats,
        **site_stats,
        "matched": len(matched),
        "expired": expired,
    }
    return matched, stats

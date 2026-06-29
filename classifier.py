"""
Classifies a business as outdoor sports or not using a free LLM via OpenRouter.
Reads business name + website content (if available).
"""

import asyncio
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse
import requests
from openai import OpenAI
from bs4 import BeautifulSoup

from vertical_profiles import get_profile


OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL   = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct")
_LLM_RETRIES       = 6
_MIN_CONTENT_CHARS = 300

DOMAIN_CLASSIFY_PROMPT = """You are identifying whether a newly registered website belongs to a NEW OR EARLY-STAGE commercial business in the outdoor sports industry that would need commercial insurance.

Target business types:

TYPE A — Physical retail, rental, demo, or repair shop for outdoor sports gear:
  fly fishing shop, ski/snowboard shop, hunting/archery shop, camping gear store, outdoor apparel store,
  mountain bike shop, paddle/kayak shop, trail running store, tackle shop, sporting goods store,
  ski rental shop, kayak rental, bike repair shop, gun shop, shooting range, archery range.

TYPE B — Outdoor resort, lodge, campground, glamping, RV park (new/early-stage only), marina, or fishing/hunting lodge with a physical location. TYPE B requires a commercial-scale hospitality operation: multiple rooms, cabins, or campsites; on-site staff or check-in; or clearly managed property amenities. A single privately-owned vacation rental (one cabin, one house, one unit listed on Airbnb/VRBO/HomeAway) does NOT qualify — even with real pricing and location details. The site must represent a business, not a personal property rental. For TYPE B, natural-setting accommodation IS enough — the site does not need to list specific outdoor activities or gear — but it must have real business-specific details such as location, booking/rates, lodging types, amenities, contact details, policies, or photos/copy specific to that property.

TYPE C — Outdoor gear manufacturer or distributor (makes or wholesales outdoor sports equipment, apparel, or accessories).

TYPE D — Commercial outdoor sports tour or activity operator with a physical or guided operation: hiking tours, biking tours, kayak/canoe tours, climbing guide services, fishing charters, hunting guides, whitewater rafting, zip-line/adventure parks, snowmobile tours, horseback riding outfitters, etc. The activity must involve physical outdoor sports or recreation requiring liability coverage — NOT games, entertainment, scavenger hunts, treasure hunts, escape rooms, or similar leisure/entertainment concepts.

Lead quality gate:
- The domain being newly registered is NOT enough. We want likely new or early-stage businesses, not old businesses that only recently registered a domain.
- This broker is focused on US leads. US-hosted IP is not enough. For resorts, lodges, camps, retreats, marinas, and tour/activity operators, score below 50 unless the content clearly shows a US business location, US state, US address, or US phone/contact context.
- Answer NO for long-running or established organizations, including phrases like "since 1995", "established in 1987", "founded in 1972", "serving for 20 years", "over 30 years", "decades", "generations", "resident-owned community", "55+ community", etc.
- Answer NO for generic template/starter websites, even if the domain name has an outdoor keyword. Boilerplate product/category text without real business-specific details is not enough.
- Answer NO for private/member clubs, youth camps, scout camps, nonprofits, associations, and community organizations unless the content clearly shows a new commercial retailer, rental shop, repair shop, resort, campground, marina, manufacturer, or distributor.
- Score online-only retail (ecommerce store with no confirmed physical storefront, no rental venue, no in-person bookings) at most 55. These are lower-priority leads since the broker focuses on physical/activity operations needing liability coverage.
- A business mentioning a city, state, or region ("serving Colorado", "based in Denver", "Minnesota-based") is NOT confirmed physical presence — that describes where the owner lives, not a store customers can visit. Physical presence requires explicit evidence: a street address, "visit us", store hours, "come in", directions to a location, or language clearly about in-person transactions. When there is no such evidence, assume online-only.
- Score solo/one-person operations (no business entity, no employees, no commercial venue) at most 75.

Note: Commercial outdoor tour and activity operators (TYPE D) qualify on their own — they do not need gear sales or lodging. A hiking tour company, bike tour operator, fishing charter, or any business running paid outdoor trips qualifies.

To answer YES for TYPE A or TYPE C, the content must explicitly mention specific outdoor sports products, gear, rentals, repairs, or manufacturing/distribution AND include real business-specific details. A physical location or business hours alone is NOT enough. Generic words like "outdoor", "nature", "preserve", "club", or "camp" alone are NOT enough.
For TYPE B (lodges, resorts, campgrounds), natural-setting accommodation with real business details (rates, booking, location, property-specific lodging details, amenities, or policies) is sufficient — no gear or activity listings required. A lodge/resort/campground name with sparse generic copy is NOT enough.

Answer NO if:
- TYPE A/C only: No explicit outdoor sports products, gear, rentals, or activities are mentioned
- Pure guide service or charter with no gear sales, rentals, lodging, or commercial outdoor trips (a solo guide with no business entity or commercial operation)
- Login page, SaaS tool, software application, or members-only platform
- Online-only with no physical store, venue, or warehouse
- Template/starter site with generic content and no real business-specific evidence
- Long-running/established business or organization, even if the website/domain appears newly registered
- Private/member club, youth camp, scout camp, nonprofits, associations, and community organizations
- Non-outdoor sports business (food, construction, medical, tech, landscaping, real estate, games, entertainment, escape rooms, scavenger hunts, treasure hunts, puzzle hunts, trivia, etc.)
- Parked domain, placeholder, or "coming soon" page with no real content
- Content is too sparse or generic to determine
- Directory, aggregator, or finder site whose purpose is to list or link to other businesses (e.g. "find an archery shop near you", "compare fishing gear prices", business listing directories) — the domain must itself be a business, not a tool for finding businesses
- Single-property vacation rental — a sole cabin, house, condo, or vacation home listed for short-term rental (Airbnb, VRBO, HomeAway style) is NOT a TYPE B resort or lodge; look for plural accommodation units or commercial-scale operations
- Counterfeit or brand-impersonation site — domain name contains double hyphens (e.g. brand--name.com), misspellings of a major brand combined with generic words ("official", "store", "shop"), or content that is clearly copied from a well-known brand
- The content explicitly names a non-US country as the business location (e.g. "London, UK",
  "Sydney, Australia", "Ontario, Canada", "Lusaka, Zambia"), or the location is an ambiguous
  non-US-looking resort/tour/camp location without a US state/address/phone signal. Do NOT guess
  that unfamiliar place names are US cities.

When in doubt, answer NO.

Domain: {domain}
Website content: {content}

Score the lead from 0–100 using this guide:
  90–100: Clear match — TYPE A/B/C/D business, new/early-stage, strong business-specific detail
  70–89:  Likely match — fits a type but missing some details (e.g. sparse content, no location)
  50–69:  Borderline — outdoor-related but a key qualifier is uncertain (could be established, might be online-only, activity type unclear)
  25–49:  Weak — outdoor-themed name/branding but doesn't clearly meet any type
  0–24:   No match — wrong industry, established org, template, parked, non-US, or too sparse

Answer with exactly this format:
SCORE: 0-100
LOCATION: city, state (if found in content and SCORE >= 50, otherwise leave blank)
ESTABLISHED: founding year or period if explicitly mentioned in content (e.g. "1987", "since 1995", "over 30 years"), otherwise leave blank
TEMPLATE: YES if the site uses generic placeholder/template content with no real business-specific details (stock photos, filler text, "coming soon", Wix/Squarespace/WordPress starter pages), NO otherwise
ECOM_ONLY: YES if the business sells products or services online without confirmed physical presence. Physical presence requires explicit evidence in the content: a street address, "visit us" / "stop by", store hours, directions to a location, or language about in-person transactions. A city/state mention, "serving the [region] area", "based in X", or "located in X" alone is NOT physical presence — that describes where the owner is from, not a storefront customers can visit. When in doubt, answer YES (assume online-only).
REASON: one sentence explaining why"""

OUTDOOR_SPORTS_PROMPT = """You are helping an insurance broker find new outdoor sports businesses in Minnesota
that need commercial insurance. They want two types of businesses:

TYPE A — Physical retail stores selling outdoor sports gear, equipment, or apparel:
ski shop, snowboard shop, fly fishing shop, hunting/archery shop, outdoor gear store, camping gear store,
mountain bike shop, paddle/kayak shop, trail running store, outdoor apparel store.

TYPE B — Outdoor resorts, lodges, campgrounds, or fishing clubs with a physical location:
fishing resorts, hunting lodges, wilderness campgrounds, outdoor recreation clubs with a physical site.

Answer YES for Type A or Type B. Use these rules:

For Type A (retail store): the name must contain an explicit retail signal — words like Shop, Store, Gear,
Tackle, Accessories, Apparel, Supply, or a specific product category (Ski, Paddle, Archery, Fly Fishing).
"Outdoors LLC" or "Outdoor Solutions" alone is NOT enough. The word "Outdoors" is too generic.

For Type B (resort/lodge/campground): the name must contain Resort, Lodge, Campground, Camp, Retreat,
or Fishing Club. Names like "Pine Lake Fishing Club & Campground" or "Fountain Lake Resort" qualify.

"Outfitters" qualifies as Type B (lodge/resort style) when paired with a specific place name
(e.g. "Fountain Lake Outfitters", "Boundary Waters Outfitters") but NOT when paired with a person name
or generic description.

Answer NO if any of these apply:
- Guide service, charter, or tour operator — sells experiences only, no gear or lodging
  e.g. "Johnson Fishing Guide Service", "Roc's Guide Services"
- Person's name + fishing/hunting: e.g. "Eli Rohloff Fishing LLC" — likely a personal guide
- Generic "Outdoors", "Outdoor", or "Adventures" with no retail or resort qualifier and no confirming website
  e.g. "Driftless Edge Outdoors LLC", "Andy's Adventures LLC", "Outdoor Solutions LLC"
- Landscaping, lawn care, or property maintenance — e.g. "X Outdoor Services LLC"
- Online-only (no physical location confirmed)
- Consulting, construction, real estate, medical, legal, or any non-outdoor business
- Crafting, arts, or unrelated hobbies — e.g. "Crafting Therapy LLC"

When in doubt, answer NO.

Business name: {name}
City: {city}
Website content: {content}

Answer with exactly this format:
MATCH: YES or NO
REASON: one sentence explaining why"""



def _site_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in host.split(".") if part]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _cross_domain_redirect(source_url: str, final_url: str) -> bool:
    source_key = _site_key(source_url)
    final_key = _site_key(final_url)
    return bool(source_key and final_key and source_key != final_key)


def _detect_cross_domain_redirect(url: str) -> str:
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        resp.close()
    except Exception:
        if url.startswith("https://"):
            return _detect_cross_domain_redirect("http://" + url.removeprefix("https://"))
        return ""
    return resp.url if _cross_domain_redirect(url, resp.url) else ""



import threading as _threading


async def _crawl4ai_fetch_async(url: str, max_chars: int, page_timeout_ms: int) -> str:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    config = CrawlerRunConfig(
        excluded_tags=["nav", "aside"],  # keep footer — it carries address/ZIP/phone for the geo gate
        remove_overlay_elements=True,
        page_timeout=page_timeout_ms,
        verbose=False,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed"),
            options={"ignore_links": True},
        ),
    )
    async with AsyncWebCrawler() as crawler:
        result = await asyncio.wait_for(
            crawler.arun(url=url, config=config),
            timeout=page_timeout_ms / 1000 + 10,
        )
    if not result.success:
        return ""
    md = result.markdown
    text = (md.fit_markdown or md.raw_markdown) if hasattr(md, "fit_markdown") else str(md)
    text = re.sub(r"\s+", " ", text).strip()[:max_chars]

    # The pruned markdown drops <script type=ld+json> and often the footer — exactly
    # where a JS/Cloudflare site's schema.org PostalAddress (ZIP/phone) lives. Mirror the
    # plain-HTTP path: pull JSON-LD + footer from the rendered DOM (already downloaded —
    # no extra fetch) and append them so the geo gate can see the address. Markdown
    # rendering is non-deterministic under Cloudflare; the structured JSON-LD is not.
    extra = ""
    try:
        soup = BeautifulSoup(result.html or "", "html.parser")
        jsonld = " ".join(
            s.get_text(" ", strip=True) for s in soup.find_all("script", type="application/ld+json")
        )[:1500]
        footer = " ".join(f.get_text(" ", strip=True) for f in soup.find_all("footer"))[:1500]
        extra = re.sub(r"\s+", " ", " ".join(filter(None, [footer, jsonld]))).strip()
    except Exception:
        pass
    return " ".join(filter(None, [text, extra])) if extra else text


def _fetch_via_crawl4ai(url: str, max_chars: int = 3000) -> str:
    """Fetch a JavaScript-rendered page via Crawl4AI (local Playwright browser).

    Runs the async crawl in a daemon thread so a hung browser startup never
    blocks the caller indefinitely.
    """
    page_timeout_ms = int(os.environ.get("ENRICH_PAGE_TIMEOUT_MS", "20000"))
    # Allow page_timeout + 30s for browser startup and teardown overhead.
    outer_timeout = page_timeout_ms / 1000 + 30

    result: list[str] = []
    error: list[Exception] = []

    def _worker():
        try:
            result.append(asyncio.run(_crawl4ai_fetch_async(url, max_chars, page_timeout_ms)))
        except Exception as e:
            error.append(e)

    t = _threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=outer_timeout)

    if t.is_alive():
        print(f"[classifier] Crawl4AI timed out after {outer_timeout:.0f}s for {url}", flush=True)
        return ""
    if error:
        print(f"[classifier] Crawl4AI scrape failed for {url}: {error[0]}", flush=True)
        return ""
    return result[0] if result else ""


def _fetch_via_firecrawl(url: str, max_chars: int = 3000) -> str:
    """Fetch via Firecrawl API. Kept for easy restore when credits are available."""
    api_key = os.environ.get("FIRECRAWL_API_KEY") or os.environ.get("FIRECRAWL")
    if not api_key:
        return ""
    timeout = int(os.environ.get("FIRECRAWL_TIMEOUT_SECONDS", "40"))
    endpoint = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2/scrape")
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "waitFor": 1000,
        "timeout": 30000,
        "location": {"country": "US", "languages": ["en-US"]},
        "removeBase64Images": True,
        "blockAds": True,
    }
    try:
        resp = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        text = data.get("markdown") or data.get("summary") or ""
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:
        print(f"[classifier] Firecrawl scrape failed for {url}: {e}", flush=True)
        return ""


BROWSER_HEADERS = {
    # A realistic desktop-Chrome header set. Validated to turn 403s (bot walls that
    # block a bare "Mozilla/5.0") into 200s — a near-free coverage win for all verticals.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def _fetch_page_text(url: str, max_chars: int = 1000) -> tuple[str, str]:
    """Fetch a URL; return ``(text, title)``.

    Plain HTTP first (realistic browser headers), escalating to Crawl4AI (local
    Playwright) for JS-heavy sites when plain content is below threshold. The page
    ``<title>`` is returned for the dataset. Footer text and schema.org JSON-LD are
    preserved and appended *after* the (truncated) body, because that is where a
    business's address / ZIP / phone usually lives — the signal the geo gate needs —
    and a long body must not be able to push it out of the window.
    """
    if not url:
        return "", ""

    plain_text, title = "", ""
    worth_rendering = False    # only escalate to Playwright when a render could plausibly help
    try:
        resp = requests.get(url, timeout=8, headers=BROWSER_HEADERS)
        raw = resp.text or ""
        # Decide BEFORE raise_for_status (so a 403 still counts). Render is worth it for a
        # bot wall (4xx/5xx — a real browser may pass, e.g. Cloudflare) or a real JS shell
        # (has <script> / a substantial body). Skip it for a tiny no-script stub (empty
        # holding page) and for network-dead domains (no response at all) — a browser
        # recovers nothing there and the render burns 5-20s. This is where most wasted
        # renders on the NRD firehose come from.
        worth_rendering = resp.status_code >= 400 or len(raw) >= 256 or "<script" in raw.lower()
        resp.raise_for_status()
        soup = BeautifulSoup(raw, "html.parser")
        if soup.title:
            title = soup.title.get_text(strip=True)
        meta_text = " ".join(
            tag.get("content", "")
            for tag in soup.find_all("meta")
            if tag.get("name", "").lower() in {"description", "og:description", "twitter:description"}
            or tag.get("property", "").lower() in {"og:description", "twitter:description"}
        )
        jsonld = " ".join(
            s.get_text(" ", strip=True) for s in soup.find_all("script", type="application/ld+json")
        )[:1500]
        footer_text = " ".join(f.get_text(" ", strip=True) for f in soup.find_all("footer"))[:1500]
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        body = soup.get_text(separator=" ", strip=True)
        plain_text = " ".join(filter(None, [meta_text, body[:max_chars], footer_text, jsonld]))
    except Exception:
        pass

    if len(plain_text.strip()) >= _MIN_CONTENT_CHARS:
        return plain_text, title

    if worth_rendering:
        fc_text = _fetch_via_crawl4ai(url, max_chars=max_chars)
        if fc_text:
            return fc_text, title

    return plain_text, title


_CONTACT_PATHS = ("/contact", "/contact-us", "/locations", "/about", "/service-area")


def _fetch_contact_text(url: str, max_chars: int = 2000) -> str:
    """Plain-HTTP fetch of likely contact/location pages — the geo gate's Tier-1
    "second look" when a homepage shows an MN signal but no service-area ZIP yet.
    No Crawl4AI, no enricher import. Short-circuits on the first page that shows an MN
    signal; otherwise returns the concatenated text of the pages it reached."""
    from urllib.parse import urlparse
    from geo_gate import mn_signal, metro_phone
    try:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
    found: list[str] = []
    for path in _CONTACT_PATHS:
        try:
            r = requests.get(root + path, timeout=6, headers=BROWSER_HEADERS, allow_redirects=True)
        except Exception:
            continue
        if not getattr(r, "ok", False) or not r.text:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        jsonld = " ".join(
            s.get_text(" ", strip=True) for s in soup.find_all("script", type="application/ld+json")
        )[:1000]
        footer_text = " ".join(f.get_text(" ", strip=True) for f in soup.find_all("footer"))[:1000]
        for tag in soup(["script", "style", "nav"]):
            tag.decompose()
        combined = " ".join(
            filter(None, [soup.get_text(" ", strip=True)[:max_chars], footer_text, jsonld])
        )
        if mn_signal(combined) or metro_phone(combined):
            return combined
        found.append(combined)
    return " ".join(found)[: max_chars * 2]


_NO_SIGNALS = ["guide service", "guide services", "charter service", "fishing charter"]
_YES_SIGNALS = ["campground", "fishing club", "hunting club", "fish camp"]

_PARKED_PATTERNS = [
    "domain for sale",
    "buy this domain",
    "this domain is for sale",
    "parked by",
    "parked free",
    "coming soon",
    "under construction",
    "website coming soon",
    "site is under construction",
    "hugedomains",
    "afternic",
    "sedo.com",
    "dan.com",
    "godaddy.com/domains",
]


def _extract_contact_info(content: str) -> dict:
    emails = sorted(set(re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", content, re.I)))
    phone_pattern = re.compile(
        r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)"
    )
    phones = sorted({re.sub(r"\s+", " ", match.group(0)).strip() for match in phone_pattern.finditer(content)})
    return {
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
    }


def validate_site(url: str) -> dict:
    """
    Fetch a URL and determine if it has classifiable content.
    Returns {"reachable": bool, "content": str, "pending_reason": str | None}.
    pending_reason is set when the site should be retried later.
    """
    final_url = _detect_cross_domain_redirect(url)
    redirected_to = final_url or ""
    redirect_domain = _site_key(final_url) if final_url else ""

    content, title = _fetch_page_text(url, max_chars=3000)
    if not content and url.startswith("https://"):
        content, title = _fetch_page_text("http://" + url.removeprefix("https://"), max_chars=3000)

    if not content:
        return {
            "reachable": False,
            "content": "",
            "title": title,
            "pending_reason": "no response or fetch failed",
            "redirected_to": redirected_to,
            "redirect_domain": redirect_domain,
            "email": "",
            "phone": "",
        }

    contact = _extract_contact_info(content)
    lower = content.lower()
    for pattern in _PARKED_PATTERNS:
        if pattern in lower:
            return {
                "reachable": True,
                "content": content,
                "title": title,
                "pending_reason": f"parked/placeholder ({pattern})",
                "redirected_to": redirected_to,
                "redirect_domain": redirect_domain,
                **contact,
            }

    if len(content.strip()) < _MIN_CONTENT_CHARS:
        return {
            "reachable": True,
            "content": content,
            "title": title,
            "pending_reason": "content too sparse",
            "redirected_to": redirected_to,
            "redirect_domain": redirect_domain,
            **contact,
        }

    return {
        "reachable": True,
        "content": content,
        "title": title,
        "pending_reason": None,
        "redirected_to": redirected_to,
        "redirect_domain": redirect_domain,
        **contact,
    }


def _pre_classify(name: str) -> dict | None:
    """Fast name-only rules for unambiguous cases. Returns None to fall through to LLM."""
    lower = name.lower()
    for sig in _NO_SIGNALS:
        if sig in lower:
            return {"match": False, "reason": f"Name contains '{sig}' — guide/charter service"}
    for sig in _YES_SIGNALS:
        if sig in lower:
            return {"match": True, "reason": f"Name contains '{sig}' — qualifies as outdoor resort/club"}
    return None


def _call_llm(prompt: str, label: str) -> str:
    """Call OpenRouter with simple retry on transient errors."""
    if not OPENROUTER_API_KEY:
        print(f"[classifier] No OPENROUTER_API_KEY set — skipping '{label}'", flush=True)
        return ""

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
    for attempt in range(_LLM_RETRIES):
        try:
            msg = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            is_429 = "429" in str(e)
            wait = (20.0 * (attempt + 1)) if is_429 else (3.0 * (attempt + 1))
            print(f"[classifier] OpenRouter error for '{label}' (attempt {attempt+1}): {err} — retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)

    print(f"[classifier] OpenRouter failed after {_LLM_RETRIES} attempts for '{label}'", flush=True)
    return ""


def classify(business_name: str, city: str, website_url: str | None) -> dict:
    """
    Returns {"match": bool, "reason": str}.
    """
    pre = _pre_classify(business_name)
    if pre is not None:
        return pre

    client = OpenAI(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )

    page_text = _fetch_page_text(website_url)[0] if website_url else "No website found"

    prompt = OUTDOOR_SPORTS_PROMPT.format(
        name=business_name,
        city=city,
        content=page_text or "No content available",
    )

    response = _call_llm(prompt, business_name)
    if not response:
        return {"match": False, "reason": "classification error"}

    match = False
    reason = response

    for line in response.splitlines():
        if line.startswith("MATCH:"):
            match = "YES" in line.upper()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    return {"match": match, "reason": reason}


def _established_is_too_old(established: str) -> bool:
    """Return True when an explicit age signal points to an older business."""
    value = established.strip().lower()
    if not value:
        return False

    current_year = datetime.now().year
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", value)]
    if years and min(years) <= current_year - 2:
        return True

    durations = [int(n) for n in re.findall(r"\b(?:over|more than|for|serving for)?\s*(\d{1,3})\s*(?:\+)?\s*years?\b", value)]
    if durations and max(durations) >= 3:
        return True

    return any(signal in value for signal in ("decade", "generation", "long-running", "longstanding"))


def score_category(score: int) -> str:
    if score >= 90: return "Strong Match"
    if score >= 70: return "Likely Match"
    if score >= 50: return "Borderline"
    if score >= 25: return "Weak"
    return "No Match"


_BLANK_VALUES = {"", "blank", "none", "n/a", "na", "unknown", "not found", "not mentioned"}

_NON_US_LOCATION_TERMS = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia", "aruba",
    "australia", "austria", "bahamas", "bahrain", "bangladesh", "barbados", "belgium", "belize",
    "bolivia", "botswana", "brazil", "bulgaria", "cambodia", "cameroon", "canada", "chile",
    "china", "colombia", "costa rica", "croatia", "cyprus", "czech", "denmark", "dominican",
    "ecuador", "egypt", "el salvador", "estonia", "ethiopia", "fiji", "finland", "france",
    "germany", "ghana", "greece", "greenland", "guatemala", "honduras", "hong kong", "hungary",
    "iceland", "india", "indonesia", "ireland", "israel", "italy", "jamaica", "japan",
    "kenya", "madagascar", "malaysia", "maldives", "mexico", "morocco", "namibia", "nepal",
    "netherlands", "new zealand", "nicaragua", "norway", "panama", "peru", "philippines",
    "poland", "portugal", "romania", "russia", "scotland", "singapore", "south africa",
    "spain", "sri lanka", "sweden", "switzerland", "taiwan", "tanzania", "thailand",
    "turkey", "uganda", "uk", "united kingdom", "vietnam", "wales", "zambia", "zimbabwe",
}


def _clean_optional_field(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" .:-")
    return "" if value.lower() in _BLANK_VALUES else value


def _mentions_non_us_location(*values: str) -> bool:
    text = " ".join(values).lower()
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in _NON_US_LOCATION_TERMS)


def _reason_contradicts_match(reason: str) -> bool:
    lower = reason.lower()
    return any(
        signal in lower
        for signal in (
            "not a commercial business",
            "non-commercial",
            "does not offer",
            "does not provide",
            "falls outside",
            "outside of the defined",
            "outside the defined",
            "too sparse to determine",
            "content is too sparse",
            # LLM recognized it's not a real business but hedged on score
            # (e.g. industry-themed tools/calculators/directories/blogs).
            "lack of clarity on whether",
            "collection of calculator",
            "rather than a real",
            "not a real business",
            "not a real company",
            "no information on the business",
            "no information about the business",
        )
    )


_US_STATE_RE = re.compile(
    r"\b(?:A[LKSZR]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|"
    r"O[HKR]|P[A]|RI|S[CD]|T[NX]|UT|V[AIT]|W[AIVY]|"
    r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|"
    r"Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|"
    r"Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|"
    r"Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|"
    r"Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|"
    r"Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b",
    re.I,
)


def _needs_clear_us_location(reason: str) -> bool:
    lower = reason.lower()
    return any(
        term in lower
        for term in (
            "type b", "resort", "lodge", "camp", "retreat", "marina",
            "tour", "charter", "guide", "activity operator", "accommodation",
        )
    )


def _has_us_signal(location: str, content: str) -> bool:
    if _US_STATE_RE.search(location) or _US_STATE_RE.search(content):
        return True
    if re.search(r"\b(?:\+?1[\s.-]?)?(?:\(?[2-9]\d{2}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b", content):
        return True
    return bool(re.search(r"\b\d{3,6}\s+[A-Za-z0-9 .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", content))


def classify_domain(domain: str, content: str, profile=None) -> dict:
    """Classify a domain from pre-fetched page content, per the active vertical."""
    if profile is None:
        profile = get_profile()
    prompt = profile.classify_prompt.format(domain=domain, content=content or "No content available")

    response = _call_llm(prompt, domain)
    if not response:
        return {"match": False, "reason": "classification error"}

    score = 0
    reason = response
    location = ""
    established = ""
    is_template = False
    ecom_only = False
    for line in response.splitlines():
        if line.startswith("SCORE:"):
            raw = re.search(r"\d+", line)
            score = min(100, max(0, int(raw.group()))) if raw else 0
        elif line.startswith("LOCATION:"):
            location = line.replace("LOCATION:", "").strip()
        elif line.startswith("ESTABLISHED:"):
            established = line.replace("ESTABLISHED:", "").strip()
        elif line.startswith("TEMPLATE:"):
            is_template = "YES" in line.upper()
        elif line.startswith("ECOM_ONLY:"):
            ecom_only = "YES" in line.upper()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    location = _clean_optional_field(location)
    established = _clean_optional_field(established)

    if is_template and score >= 60:
        score = min(score, 45)
        reason = "Template/starter website with no real business-specific evidence yet"
    elif profile.cap_established_in_classifier and _established_is_too_old(established) and score >= 60:
        # Outdoor only: an established business is a weak "new/early-stage" lead.
        # Construction (cap_established_in_classifier=False) skips this — an
        # established contractor still has ongoing spend, so it stays a strong lead.
        score = min(score, 45)
        reason = f"Established business or organization ({established}), not a new/early-stage lead"
    elif _mentions_non_us_location(location, reason) and score >= 60:
        score = min(score, 24)
        reason = "Explicit non-US business location detected"
        location = ""
    elif _reason_contradicts_match(reason) and score >= 60:
        score = min(score, 45)
        reason = f"Classifier reason contradicts a match: {reason}"
    elif _needs_clear_us_location(reason) and not _has_us_signal(location, content) and score >= 70:
        score = min(score, 49)
        reason = "Outdoor lodging/tour lead lacks a clear US location signal"
    elif ecom_only and score >= 56:
        score = min(score, 55)

    match = score >= 70
    return {"match": match, "score": score, "score_category": score_category(score),
            "reason": reason, "location": location, "established": established,
            "is_template": is_template, "ecom_only": ecom_only}

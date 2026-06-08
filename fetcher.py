"""
Pulls new business filings from MN Secretary of State.
Uses the public MBLS portal — no API key required.

Strategy: search for outdoor sports keywords, deduplicate by GUID,
then fetch detail pages to get filing dates. A local cache file
(seen_guids.txt) tracks previously checked GUIDs so subsequent
runs only check new businesses.
"""

import json
import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dataclasses import dataclass


SEARCH_KEYWORDS = [
    "outdoor", "fishing", "hunting", "kayak", "canoe", "ski", "snowboard",
    "outfitter", "climbing", "hiking", "archery", "trail",
    "tackle", "camp", "paddle", "rafting", "fly shop",
    "resort", "lodge", "campground",
]

BASE_URL = "https://mblsportal.sos.mn.gov"
SEARCH_URL = BASE_URL + "/Business/BusinessSearch"
DETAIL_URL = BASE_URL + "/Business/SearchDetails"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "seen_guids.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lead-monitor/1.0)"}


@dataclass
class Filing:
    name: str
    city: str
    state: str = "MN"
    filing_date: str = ""
    entity_type: str = ""


def _load_cache() -> dict[str, str]:
    """Returns {guid: filing_date} for previously seen businesses."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def _search_keyword(keyword: str, session: requests.Session) -> dict[str, dict]:
    """Returns {guid: {name, entity_type}} for all active businesses matching keyword."""
    params = {
        "BusinessName": keyword,
        "IncludePriorNames": "False",
        "Status": "Active",
        "Type": "Contains",
    }
    try:
        resp = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[fetcher] Search failed for '{keyword}': {e}", flush=True)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"class": "table"})
    if not table:
        return {}

    results = {}
    for row in table.find_all("tr")[1:]:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        link = tds[1].find("a", href=True) or tds[0].find("a", href=True) or row.find("a", href=True)
        if not link or "filingGuid=" not in link.get("href", ""):
            continue

        guid = link["href"].split("filingGuid=")[-1]
        spans = tds[0].find_all("span")
        name = tds[0].get_text(separator="|", strip=True).split("|")[0]
        entity_type = spans[0].get_text(strip=True) if spans else ""
        results[guid] = {"name": name, "entity_type": entity_type}

    return results


def _fetch_detail(guid: str, session: requests.Session) -> dict | None:
    """Returns {filing_date, city} for the given business GUID, or None on error."""
    try:
        resp = session.get(
            DETAIL_URL, params={"filingGuid": guid}, headers=HEADERS, timeout=15
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    filing_date = ""
    city = ""

    for dl in soup.find_all("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        label = dt.get_text(strip=True)
        if label == "Filing Date" and not filing_date:
            filing_date = dd.get_text(strip=True)
        elif label == "Registered Office Address" and not city:
            address_el = dd.find("address") or dd
            for s in address_el.strings:
                s = s.strip()
                if ", MN " in s:
                    city = s.split(",")[0].strip().title()
                    break

    return {"filing_date": filing_date, "city": city}


def fetch_new_filings(days: int = 7) -> list[Filing]:
    """Returns businesses registered in MN within the last `days` days."""
    cutoff = datetime.now() - timedelta(days=days)
    session = requests.Session()
    cache = _load_cache()

    # Collect unique GUIDs across all keywords
    all_businesses: dict[str, dict] = {}
    for keyword in SEARCH_KEYWORDS:
        found = _search_keyword(keyword, session)
        new = {g: v for g, v in found.items() if g not in all_businesses}
        all_businesses.update(new)
        print(f"[fetcher] '{keyword}' → {len(found)} results, {len(new)} new unique", flush=True)
        time.sleep(0.3)

    # Split into cached (known date) and unchecked
    unchecked = {g: v for g, v in all_businesses.items() if g not in cache}
    cached = {g: v for g, v in all_businesses.items() if g in cache}
    print(f"[fetcher] {len(all_businesses)} unique businesses: {len(cached)} cached, {len(unchecked)} to fetch", flush=True)

    # Check unchecked detail pages; cache stores {guid: [filing_date, city]}
    for i, (guid, meta) in enumerate(unchecked.items(), 1):
        detail = _fetch_detail(guid, session)
        cache[guid] = [
            detail["filing_date"] if detail else "",
            detail["city"] if detail else "",
        ]
        if i % 100 == 0:
            print(f"[fetcher] Fetched {i}/{len(unchecked)} detail pages...", flush=True)
        time.sleep(0.2)

    _save_cache(cache)

    # Collect matches
    filings = []
    for guid, meta in all_businesses.items():
        entry = cache.get(guid, ["", ""])
        # Support old cache format (plain string) and new ([date, city])
        if isinstance(entry, str):
            filing_date, city = entry, ""
        else:
            filing_date, city = entry[0], entry[1]
        if not filing_date:
            continue
        try:
            fd = datetime.strptime(filing_date, "%m/%d/%Y")
        except ValueError:
            continue
        if fd >= cutoff:
            filings.append(Filing(
                name=meta["name"],
                city=city,
                filing_date=filing_date,
                entity_type=meta["entity_type"],
            ))
            print(f"[fetcher]   + {meta['name']} ({city}) filed {filing_date}", flush=True)

    return filings

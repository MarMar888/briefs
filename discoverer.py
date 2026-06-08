"""
Finds a website or social profile for a newly registered business.
Uses SerpAPI (Google search). Falls back to None if nothing found.
"""

import os
import requests


SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# Domains that don't tell us anything useful about whether a business has a storefront.
SKIP_DOMAINS = {
    # State/legal/directory portals
    "mblsportal.sos.state.mn.us",
    "opencorporates.com",
    "bizapedia.com",
    "corporationwiki.com",
    "dnb.com",
    "yellowpages.com",
    "whitepages.com",
    "bbb.org",
    "linkedin.com",
    # Social media — a Facebook/Instagram profile is not evidence of a storefront
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    # Aggregators and junk results
    "yelp.com",
    "yumpu.com",
    "mapquest.com",
    "manta.com",
    "chamberofcommerce.com",
}


def find_website(business_name: str, city: str) -> str | None:
    """
    Returns the first plausible website URL for a business, or None.
    Searches Google for `"[business name]" [city] MN`.
    """
    if not SERPAPI_KEY:
        print("[discoverer] SERPAPI_KEY not set — skipping website discovery")
        return None

    query = f'"{business_name}" {city} MN'

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "q": query,
                "api_key": SERPAPI_KEY,
                "num": 5,
                "gl": "us",
                "hl": "en",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[discoverer] SerpAPI error for '{business_name}': {e}")
        return None

    for result in data.get("organic_results", []):
        url = result.get("link", "")
        domain = url.split("/")[2] if url.startswith("http") else ""
        base_domain = ".".join(domain.split(".")[-2:])

        if any(skip in domain for skip in SKIP_DOMAINS):
            continue

        # Prefer social profiles if no proper site found
        return url

    return None

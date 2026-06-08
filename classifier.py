"""
Classifies a business as outdoor sports or not using a free LLM via OpenRouter.
Reads business name + website content (if available).
"""

import os
import requests
from openai import OpenAI
from bs4 import BeautifulSoup


MODEL = "llama3.1:8b"

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


def _fetch_page_text(url: str) -> str:
    """Fetches a URL and returns cleaned text content (max 1000 chars)."""
    if not url:
        return ""
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts and styles
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:1000]
    except Exception:
        return ""


_NO_SIGNALS = ["guide service", "guide services", "charter service", "fishing charter"]
_YES_SIGNALS = ["campground", "fishing club", "hunting club", "fish camp"]


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

    page_text = _fetch_page_text(website_url) if website_url else "No website found"

    prompt = OUTDOOR_SPORTS_PROMPT.format(
        name=business_name,
        city=city,
        content=page_text or "No content available",
    )

    try:
        message = client.chat.completions.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.choices[0].message.content.strip()
    except Exception as e:
        print(f"[classifier] Ollama error for '{business_name}': {e}")
        return {"match": False, "reason": "classification error"}

    match = False
    reason = response

    for line in response.splitlines():
        if line.startswith("MATCH:"):
            match = "YES" in line.upper()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    return {"match": match, "reason": reason}

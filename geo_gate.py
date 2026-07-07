"""
Geographic gating for the ``minnesota`` vertical (and any future place-based market).

The outdoor/construction verticals pre-filter the newly-registered-domain firehose by
matching an *industry* keyword in the domain NAME. The ``minnesota`` vertical has no
industry keyword — it targets *any* new brick-and-mortar business in a cleaning
company's service area — so it runs the firehose with the name filter OFF and instead
decides membership from the *fetched page content*: does the page carry a service-area
ZIP code (the precise "core" signal) or a Twin Cities metro phone (the broader
"adjacent" signal)?

This module is a LEAF: it imports only ``re``/``dataclasses`` so both
``vertical_profiles`` and ``domain_scanner`` can import it with no cycle. It does no
network I/O and never calls an LLM — the gate and the per-crawl "basics" are pure
regex over already-fetched text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceArea:
    """A named set of ZIP codes a business operator actively services."""

    name: str
    zips: frozenset[str]


# The cleaning company at 2909 S Wayzata Blvd, Minneapolis MN 55405 services these
# Minneapolis / St. Paul metro ZIPs. Editable + growable: append another ServiceArea
# (the gate unions them all) to expand coverage or onboard another operator.
MN_CLEANING_AREAS: tuple[ServiceArea, ...] = (
    ServiceArea("Minneapolis / St. Paul metro", frozenset({
        "55412", "55411", "55405", "55403", "55402", "55401", "55415", "55404", "55454", "55455",
        "55414", "55413", "55418", "55113", "55108", "55117", "55130", "55101", "55155", "55102",
        "55103", "55104", "55105", "55406", "55407", "55408", "55409", "55410", "55419", "55417",
    })),
)

# A standalone 5-digit token. Leading (?<![\d.]) rejects longer numbers and decimal
# fractions ("0.55405"); trailing (?!\d)(?!\.\d) rejects a longer integer ("554050")
# and a decimal price ("55405.00") but STILL accepts a ZIP that ends a sentence
# ("Minneapolis, MN 55405.") — common in footers. ZIP+4 ("55405-1234") yields 55405.
_ZIP_TOKEN_RE = re.compile(r"(?<![\d.])(\d{5})(?!\d)(?!\.\d)")
# ", MN" address form only. The bare word "Minnesota" is intentionally NOT a signal —
# it shows up in shipping disclaimers and "we serve all 50 states" copy and would
# trip the Tier-1 contact-page fetch on a huge slice of the non-MN firehose.
_MN_STATE_RE = re.compile(r",\s*MN\b", re.I)
# Statewide MN ZIP prefix (550xx–569xx) — a looser MN tag for the dataset / Tier-1.
_MN_ZIP_RE = re.compile(r"(?<![\d.])(5[5-6]\d{3})(?!\d)(?!\.\d)")

# Minnesota area codes. METRO = the four Twin Cities codes (a metro phone passes the
# gate as "adjacent", D10=B); the full set also covers greater-MN for the mn_signal tag.
_MN_AREA = ("218", "320", "507", "612", "651", "763", "952")
METRO_AREA = ("612", "651", "763", "952")
# A US phone number; capture the area code. Requires phone-style punctuation so it does
# not match bare 3-digit runs (years, counts, prices).
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]\d{3}[\s.\-]?\d{4}(?!\d)")


def _area_codes(text: str) -> set[str]:
    return {m.group(1) for m in _PHONE_RE.finditer(text or "")}


def find_service_area_zips(text: str, areas: tuple[ServiceArea, ...]) -> list[str]:
    """Return the sorted service-area ZIPs that appear as standalone tokens in ``text``."""
    if not text:
        return []
    wanted = frozenset().union(*(a.zips for a in areas)) if areas else frozenset()
    return sorted({m.group(1) for m in _ZIP_TOKEN_RE.finditer(text) if m.group(1) in wanted})


def matches_service_area(text: str, areas: tuple[ServiceArea, ...]) -> bool:
    """True if a precise service-area ZIP is present (the 'core' signal)."""
    return bool(find_service_area_zips(text, areas))


def metro_phone(text: str) -> bool:
    """True if a Twin Cities metro area-code phone (612/651/763/952) is present."""
    return bool(_area_codes(text) & set(METRO_AREA))


def mn_signal(text: str) -> bool:
    """A *precise* Minnesota signal — used to trigger the Tier-1 contact-page fetch and
    to tag the dataset. Deliberately excludes the bare word "Minnesota" (too noisy)."""
    return bool(text) and (
        bool(_MN_STATE_RE.search(text) or _MN_ZIP_RE.search(text))
        or bool(_area_codes(text) & set(_MN_AREA))
    )


def gate_pass(text: str, areas: tuple[ServiceArea, ...]) -> bool:
    """D10=B gate: a precise service-area ZIP (core) OR a Twin Cities metro phone (adjacent)."""
    return bool(find_service_area_zips(text, areas)) or metro_phone(text)


# Distinctive Minnesota place tokens that hint a Twin Cities–metro (or greater-MN)
# business when they appear in a concatenated domain label. Curated to be reasonably
# unambiguous: generic English words that happen to be MN city names (Savage, Crystal,
# Buffalo, Austin, Hampton, Jackson…) are deliberately left out to avoid false hints.
# This is the "MN keyword" set — grow it to surface more in-area leads. Reorder-only:
# a hit just front-loads the domain in the queue (get_due orders by priority DESC), it
# never excludes anything, so an occasional false positive is cheap.
_MN_PLACE_TOKENS = (
    # Twin Cities core + statewide tags
    "minneapolis", "stpaul", "saintpaul", "twincities", "twincity", "minnesota",
    "mpls", "msp",
    # Distinctive metro suburbs
    "minnetonka", "edina", "wayzata", "eaganmn", "eagan", "bloomington", "edenprairie",
    "maplegrove", "maplewood", "brooklynpark", "brooklyncenter", "coonrapids",
    "applevalley", "shakopee", "chanhassen", "chaska", "roseville", "woodbury",
    "stillwater", "burnsville", "lakeville", "hopkins", "goldenvalley", "robbinsdale",
    "columbiaheights", "fridley", "newbrighton", "shoreview", "whitebearlake",
    "forestlake", "cottagegrove", "invergrove", "priorlake", "rosemount", "farmington",
    "elkriver", "champlin", "oakdale", "mendotaheights", "southstpaul", "vadnaisheights",
    "mahtomedi", "anoka", "waconia", "hutchinson", "northfield", "hastings",
    # Greater MN metros
    "duluth", "rochestermn", "stcloud", "mankato", "moorhead", "winona", "bemidji",
    "brainerd", "alexandriamn", "faribault", "owatonna", "willmar",
)


def name_priority(domain: str) -> int:
    """1 if the domain NAME hints Minnesota (front-load it in the queue), else 0.
    Reorder-only — it never excludes a domain, just changes processing order so in-area
    leads surface first. Concatenated labels make this approximate, which is fine."""
    label = (domain or "").rsplit(".", 1)[0].lower()
    if any(tok in label for tok in _MN_PLACE_TOKENS):
        return 1
    if (label.startswith("mn") or label.endswith("mn")
            or label.startswith("mn-") or label.endswith("-mn") or "-mn-" in label):
        return 1
    return 0


def extract_basics(content: str, site: dict) -> dict:
    """Cheap, regex-only "basics" persisted on EVERY crawled row (matched or rejected),
    so the firehose builds a dataset. No LLM, no extra fetch."""
    text = content or ""
    zips = sorted({m.group(1) for m in _ZIP_TOKEN_RE.finditer(text)})
    return {
        "crawl_title": (site.get("title") or "")[:200],
        "content_snippet": text[:500],
        "detected_zips": ",".join(zips[:20]),
        "detected_state": "MN" if mn_signal(text) else "",
        "is_reachable": 1 if site.get("reachable") else 0,
        "mn_signal": 1 if mn_signal(text) else 0,
    }

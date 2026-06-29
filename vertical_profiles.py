"""
Vertical profiles: per-market configuration for the lead pipeline.

The pipeline runs the SAME code paths for every market ("vertical"); only the
parameters change — which domain keywords to pre-filter NRDs on, the LLM classify
and enrich prompts, the audit thresholds, and the alert/branding label. Every lead
row is stamped with the active vertical's ``name`` in the DB ``industry`` column, so
multiple verticals coexist in one database and the frontend can scope to one.

Selection: the ``VERTICAL`` env var (or an explicit name) picks the active profile;
the default is ``outdoor``, which preserves the original OSI (Outdoor Sports
Insurance) behavior byte-for-byte.

This module is a LEAF: it does not import the pipeline modules at top level. The
outdoor profile reuses the literals already defined in domain_scanner/classifier/
enricher via deferred (function-local) imports, so there is no import cycle and the
large outdoor keyword sets are never duplicated (and so can never drift).

Usage:
    from vertical_profiles import get_profile
    profile = get_profile()                 # active vertical (env VERTICAL)
    profile = get_profile("construction")   # explicit
"""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class VerticalProfile:
    """Everything that differs between markets. Frozen + frozenset fields so a
    profile is an immutable, hashable value object built once and shared."""

    name: str                      # 'outdoor' | 'construction' — written to the industry column
    label: str                     # human label, e.g. "Outdoor Sports" / "Construction"
    keywords: frozenset[str]       # explicit activity/trade tokens (the keyword path)
    proper_nouns: frozenset[str]   # evocative-name tokens (the proper-noun path); empty disables it
    reject_tokens: frozenset[str]  # generic-business tokens that veto a proper-noun match
    classify_prompt: str           # LLM classify prompt; formats {domain} and {content}
    enrich_prompt: str             # LLM enrich/audit prompt; formats {content}
    substring_keywords: tuple[str, ...] = ()   # matched against the raw label (for abbreviations
                                               # wordninja mangles, e.g. "hvac")
    use_proper_noun_path: bool = True          # outdoor=True; construction=False (rely on trade words)
    reject_on_keyword_path: bool = False       # construction=True: a reject token vetoes a keyword
                                               # match (precision); outdoor=False (keyword always wins)
    disqualify_on_longevity: bool = True       # outdoor=True; construction=False (established is fine)
    cap_established_in_classifier: bool = True  # outdoor=True; construction=False
    side_project_solo_threshold: int = 3       # solo signals needed to auto-flag a side project
    alert_label: str = "OSI"                   # email subject/branding
    bypass_keyword_filter: bool = False        # minnesota: run the firehose with the domain-NAME filter OFF
    require_content_geo_gate: bool = False     # minnesota: gate on a service-area ZIP / metro phone in fetched content
    service_areas: tuple = ()                  # geo_gate.ServiceArea tuple for the content geo gate


# --------------------------------------------------------------------------
# Construction vertical (Ramp SDR leads)
# --------------------------------------------------------------------------
# WIDE net: GCs, home builders, design-build + all specialty building trades +
# landscaping/hardscaping/site work. Inflections included because wordninja
# tokenizes the domain label. Left OUT of the seed (so we don't hunt for them):
# restoration/remediation, real-estate developers/realty, specialty fabrication.
CONSTRUCTION_KEYWORDS = frozenset({
    # General / GC / builders
    "construction", "constructions", "contractor", "contractors", "contracting",
    "builder", "builders", "building", "build", "homebuilder", "homebuilders",
    "homebuilding", "designbuild", "generalcontractor", "gc",
    # Roofing / exterior
    "roofing", "roofer", "roofers", "roof", "siding", "gutters", "gutter",
    "waterproofing",
    # Concrete / masonry / structural / site
    "concrete", "masonry", "mason", "masons", "brick", "bricklayer",
    "framing", "framer", "framers", "foundation", "foundations",
    "excavation", "excavating", "excavator", "excavators", "earthwork",
    "grading", "demolition", "demo", "paving", "paver", "pavers", "asphalt",
    "septic",
    # Finishing trades
    "drywall", "sheetrock", "plaster", "plastering", "flooring", "floors",
    "tile", "tiling", "painting", "painter", "painters", "insulation",
    "windows", "doors", "cabinet", "cabinets", "cabinetry", "decking", "decks",
    "fencing", "fence", "fences",
    # Mechanical / MEP
    "plumbing", "plumber", "plumbers", "electrical", "electric", "electrician",
    "electricians", "hvac", "heating", "cooling", "airconditioning",
    "mechanical",
    # Remodeling / renovation
    "remodeling", "remodel", "remodeler", "remodelers", "renovation",
    "renovations", "renovating",
    # Landscaping / hardscaping / site work
    "landscaping", "landscaper", "landscapers", "hardscaping", "hardscape",
    "hardscapes", "sitework",
})

# Generic-business tokens that, for construction, veto a domain that ALSO matched
# a trade keyword (precision). Deliberately conservative: clearly-non-construction
# signals only, so real contractors are never dropped. Trades are NOT here (unlike
# the outdoor reject set) — they are the target signal.
_CONSTRUCTION_REJECT_TOKENS = frozenset({
    "software", "saas", "app", "apps", "hosting", "marketing", "seo", "agency",
    "media", "recruiting", "staffing", "dental", "medical", "healthcare",
    "clinic", "pharmacy", "salon", "spa", "realty", "realtor", "mortgage",
    "insurance", "attorney", "lawyer", "accounting", "bookkeeping", "ecommerce",
    "crypto", "nft",
})

CONSTRUCTION_CLASSIFY_PROMPT = """You are identifying whether a newly registered website belongs to a US-based CONSTRUCTION business that would be a strong sales lead for a corporate card and spend-management platform — i.e. a company that buys materials, fuel, tools, and equipment and pays crews or subcontractors.

Target business types (any ONE qualifies):
- General contractors, home builders, custom home builders, design-build firms, construction management, remodeling / renovation companies.
- Specialty trade contractors: roofing, siding, gutters, concrete, masonry, framing, foundations, excavation / earthwork, grading, demolition, paving / asphalt, drywall, insulation, flooring, tile, painting, windows / doors, cabinetry, decking, fencing, waterproofing.
- Mechanical trades: plumbing, electrical, HVAC / heating / cooling, mechanical contractors.
- Landscaping, hardscaping, and site-work contractors that run crews and equipment (not solo lawn-mowing).

What makes a STRONG lead (score high):
- Real operating spend: a crew or employees, trucks / fleet, owned equipment, a yard or shop, materials purchasing, subcontractors, active hiring.
- Commercial and/or sizable residential project work, multiple completed projects, a service area, or multiple locations.
- Licensed, bonded, and insured. Established firms are GOOD — they have ongoing spend. BOTH new and long-established construction businesses qualify.

Lead quality gate:
- US-only. If the content names an explicit non-US location (e.g. "Toronto, Canada", "London, UK", "Sydney, Australia"), score below 25. Do NOT guess that unfamiliar place names are US cities.
- A one-person handyman or hobby / side-gig with no crew, no real spend, a free email, and no business entity is a WEAK lead — score at most 45.
- Generic template / starter / parked / "coming soon" sites with no real business detail — score at most 45.
- The site must be an actual company that PERFORMS construction work — not a tool or content ABOUT construction. Score at most 24 for: online calculators, estimators, cost/material tools, apps, plugins, or SaaS (even construction-themed, e.g. a "build cost calculator"); directories, marketplaces, or "find a contractor" listing sites; blogs, news, magazines, guides, or info/education sites; and lead-generation or marketing sites. If the page is a tool or information with no evidence of a real operating contractor (no services performed, no crew or projects, no company identity or contact), score at most 24 even when the subject is construction.
- NOT construction (score at most 24): restoration / remediation (water, fire, mold), real-estate brokerage / realty / property management, pure building-materials ECOMMERCE with no field operations, industrial / product manufacturing, and software / marketing / recruiting companies that merely serve the construction industry.

Important: established age is NOT a disqualifier here. A contractor "serving since 1985" or "family owned for 3 generations" is a perfectly good lead — score on operating scale and spend, not on novelty.

A lead only scores 70 or above if the content shows a company whose business is to build or construct things — a contractor, GC, or trade company whose output is completed structures or improvements. Companies that sell products or services TO the construction industry (software, tools, SaaS, consulting, directories) never reach 70, no matter how construction-focused their branding.

Domain: {domain}
Website content: {content}

The critical distinction: does this company directly contribute to something getting BUILT — even as a GC managing subcontractors — or does it sell products/services TO the construction industry? A real construction company's output is a completed structure, renovation, or physical improvement. A software platform, SaaS tool, directory, or consulting firm whose customers are contractors is NOT a construction company, no matter how construction-focused its branding is.

Score the lead from 0-100 using this scale:
  90-100: Strong match — a company whose output is built or constructed things, with solid supporting evidence: named services or project types, a service area or location, and at minimum a phone or email. A real contractor website with services, location, and contact easily reaches 90.
  70-89:  Likely match — the company clearly exists to build or construct things (a GC, trade contractor, or remodeler), but the site is sparse: basic service list and contact info present, but little project detail or scale evidence.
  50-69:  Borderline — may be a real construction company but key evidence is missing or ambiguous: trade unclear, could be solo handyman, supplier, or hard to confirm field operations.
  25-49:  Weak — construction-themed but no real operating company shown: a one-person side-gig, no company identity, no crew, or content too thin to confirm.
  0-24:   No match — not a company that builds things: software, SaaS, apps, tools, calculators, directories, blogs, marketing/lead-gen firms, restoration/remediation, real estate, pure ecommerce materials, non-US, parked, or template sites.

Answer with exactly this format:
SCORE: 0-100
LOCATION: city, state (if found in content and SCORE >= 50, otherwise leave blank)
ESTABLISHED: founding year or period if explicitly mentioned (e.g. "1998", "since 2010", "over 20 years"), otherwise leave blank
TEMPLATE: YES if the site uses generic placeholder/template content with no real business-specific details (stock photos, filler text, "coming soon", Wix/Squarespace/WordPress starter pages), NO otherwise
ECOM_ONLY: YES only if the business sells construction products online with NO field / job-site operations and no physical yard, shop, or service area (a pure online store of building materials or tools). A contractor who works on job sites is NOT ecom_only even without a walk-in storefront — answer NO. When it clearly performs on-site construction work, answer NO.
REASON: one sentence explaining why"""

CONSTRUCTION_ENRICH_PROMPT = """You are auditing a US construction company's website to vet it as a sales lead for a corporate card and spend-management platform. The best leads are real, operating construction businesses with ongoing spend (crews or employees, equipment, materials, subcontractors). BOTH new and long-established companies are good — do NOT penalize age. The only businesses to flag are tiny hobby / side-gigs with no real spend (e.g. a one-person handyman with no crew, no entity, and a free email).

Read the combined website content below and extract these fields, one per line.
The content may include an "EXTERNAL SEARCH RESULTS" section with off-site listings
(directories, social, reviews, news) — use it for size, location, and longevity
signals the site itself omits, but weigh the business's own site most heavily.
Use UNKNOWN for anything you genuinely cannot determine from the content. Do not guess.

OWNER_NAME: <first and last name of the owner, founder, or principal contact>
FULL_ADDRESS: <complete street address with city, state, zip>
PHONE: <primary phone number>
EMAIL: <primary contact email>
ESTABLISHED: <year or period the business was founded, e.g. "2024", "since 1998", "over 20 years", "family owned for 3 generations">
ENTITY_TYPE: <legal entity if stated in the content: LLC, Inc, Corporation, LLP, sole proprietor>
EMPLOYEE_ESTIMATE: <rough crew / team size implied by the content: "1", "2-5", "6-20", "20+">
LOCATION_COUNT: <how many offices, yards, or service locations are mentioned: "1", "2", "3+">
BUSINESS_SIZE: <exactly one of: solo, small, midsize, large — your best judgment of operating scale and spend>
SIDE_PROJECT: <YES only if this reads like a one-person handyman or a hobby / part-time side-gig with no real spend:
  a single person, no crew or staff, no legal entity, no street address, a free email (gmail/yahoo/outlook),
  very thin or personal content. NO if it presents as a real staffed contractor with crews, equipment,
  projects, or a service area.>
SUMMARY: <one concise sentence describing what the business builds or what trade it performs, plus any sign of
  scale (crew size, fleet, commercial work, multiple locations)>

Website content:
{content}"""


# --------------------------------------------------------------------------
# Minnesota vertical (new physical-space businesses for a Twin Cities cleaning company)
# --------------------------------------------------------------------------
# No industry keyword: this vertical runs the firehose with the domain-NAME filter OFF
# and decides membership from FETCHED CONTENT (a service-area ZIP = "core", or a Twin
# Cities metro phone = "adjacent"). The gate runs in domain_scanner before the LLM, so by
# the time this prompt sees a page a metro signal is already confirmed.
MINNESOTA_CLASSIFY_PROMPT = """You are identifying whether a newly registered website belongs to a NEW brick-and-mortar business with a PHYSICAL SPACE in the Minneapolis–St. Paul, Minnesota metro. These are leads for a commercial & residential CLEANING company: the ideal lead is a business opening a physical space (office, storefront, clinic, studio, shop) that will need recurring cleaning / janitorial service.

A geographic pre-filter has already confirmed this page carries a Twin Cities signal (a metro ZIP or phone). Your job is to confirm it is a real, single, locally-operating business with a physical space — and to judge how well it fits.

QUALIFIES (any physical premises someone would clean):
- Professional-service offices — law, accounting/CPA, medical/dental, insurance, real estate, financial advisory, marketing/creative agencies, consultancies. THESE ARE THE STRONGEST leads; weight them high.
- Customer-facing storefronts — retail, restaurants/cafes, salons/spas, fitness/yoga studios, childcare/preschools, clinics.
- Other physical operations with a shop, studio, office, or yard staffed locally.

STRONG lead signals (score high): a real street address or "visit us"/hours, a local phone, named staff or services, a single identifiable business (not a chain), and signs it is new or recently opened ("now open", "grand opening", "opening soon", "newly opened").

LEAD QUALITY GATE:
- Must be a SINGLE local business with a physical space. A national or multi-state CHAIN/franchise that merely lists a metro location among many is NOT a lead — score at most 20.
- A pure online store / ecommerce / dropship / SaaS / app with NO physical premises is NOT a lead — score at most 20 and set ECOM_ONLY: YES.
- Directories, listing sites, marketplaces, "find a business" aggregators, blogs, news, lead-gen / marketing sites — score at most 20.
- If the content clearly places the business OUTSIDE Minnesota (an explicit non-MN address/state), score at most 20 even if a metro phone appears.

ESTABLISHED IS FINE (do NOT penalize age): a long-running Minneapolis office is still a good cleaning prospect. Prefer new openings, but never cap a business for being established. ALWAYS report any founding/age signal in ESTABLISHED.

SERVICE-AREA FIT — state it at the end of REASON:
- "core" = the content shows one of the cleaning company's core service ZIPs (downtown / near-Minneapolis 554xx and inner St. Paul 551xx).
- "adjacent" = a Twin Cities metro signal (e.g. a 612/651/763/952 phone or a nearby suburb) but not a core service ZIP. Prefer core; you may still pass a strong adjacent business but lean to a slightly lower score.

Domain: {domain}
Website content: {content}

Score the lead from 0-100:
  90-100: Clearly a real, single Minneapolis-area business with a physical space and real detail (named services, address or local phone, contact) — ideally a professional-service office or a just-opened storefront in the core area.
  70-89:  Likely a real metro brick-and-mortar business with a physical space, but detail is thin or it is adjacent rather than core.
  50-69:  Borderline — physical-ish and metro-ish but a key signal (real single business, physical premises, or in-metro location) is uncertain.
  25-49:  Weak — metro-themed but no real single operating business with a physical space shown.
  0-24:   No match — chain, directory, online-only, out-of-state, parked, template, or non-commercial.

Answer with exactly this format:
SCORE: 0-100
LOCATION: city, state (if found in content and SCORE >= 50, otherwise leave blank)
ESTABLISHED: founding year or period if explicitly mentioned (e.g. "1998", "since 2010", "now open"), otherwise leave blank
TEMPLATE: YES if the site uses generic placeholder/template content with no real business-specific details (stock photos, filler text, "coming soon", Wix/Squarespace starter pages), NO otherwise
ECOM_ONLY: YES only if the business sells online with NO physical premises (no storefront, office, studio, clinic, restaurant, shop, or yard); a business with any physical location is NO
REASON: one sentence explaining why, ending with the word "core" or "adjacent" for service-area fit"""

MINNESOTA_ENRICH_PROMPT = """You are auditing a Minneapolis–St. Paul business's website to vet it as a lead for a commercial & residential CLEANING company. The best leads are real businesses with a PHYSICAL SPACE in the metro that would need recurring cleaning. Prefer new / recently-opened businesses, but do NOT penalize age — an established local office is still a good prospect. The only things to flag are online-only / hobby ventures with no physical premises.

Read the combined website content below and extract these fields, one per line. The content may include an "EXTERNAL SEARCH RESULTS" section with off-site listings — use it for size, location, and longevity signals the site omits, but weigh the business's own site most heavily. Use UNKNOWN for anything you genuinely cannot determine. Do not guess.

OWNER_NAME: <first and last name of the owner, founder, or principal contact>
FULL_ADDRESS: <complete physical street address with city, state, zip. Ignore PO boxes. Prefer a Minnesota address.>
PHONE: <primary business phone number>
EMAIL: <primary contact email>
ESTABLISHED: <when the business was founded — "founded in 1998", "since 2003", "est. 1985", anniversary/duration phrases, or copyright spans. Treat "now open", "grand opening", "opening soon" as a brand-new founding signal. A single current-year copyright is NOT a founding signal. If nothing found, UNKNOWN.>
ENTITY_TYPE: <legal entity if stated: LLC, Inc, Corporation, LLP, sole proprietor>
EMPLOYEE_ESTIMATE: <rough team size implied by the content: "1", "2-5", "6-20", "20+">
LOCATION_COUNT: <distinct physical locations mentioned: "1", "2", "3+">
BUSINESS_SIZE: <exactly one of: solo, small, midsize, large>
SIDE_PROJECT: <YES if this reads like an online-only / hobby / part-time venture with NO physical premises: no street address, a free email (gmail/yahoo/outlook), very thin or personal content. NO if it presents as a real business with a physical Minnesota location.>
SUMMARY: <one concise sentence: what the business is, and where in the Twin Cities metro it operates>

Website content:
{content}"""


# --------------------------------------------------------------------------
# Profile registry
# --------------------------------------------------------------------------
def _build_outdoor() -> VerticalProfile:
    # Deferred imports keep this module a leaf (no import cycle). The outdoor
    # literals stay where they have always lived and are reused verbatim, so OSI
    # behavior is byte-for-byte unchanged and the big keyword sets are never copied.
    from domain_scanner import OUTDOOR_KEYWORDS, PROPER_NOUNS, _GENERIC_BUSINESS_TOKENS
    from classifier import DOMAIN_CLASSIFY_PROMPT
    from enricher import ENRICH_PROMPT

    return VerticalProfile(
        name="outdoor",
        label="Outdoor Sports",
        keywords=frozenset(OUTDOOR_KEYWORDS),
        proper_nouns=frozenset(PROPER_NOUNS),
        reject_tokens=frozenset(_GENERIC_BUSINESS_TOKENS),
        classify_prompt=DOMAIN_CLASSIFY_PROMPT,
        enrich_prompt=ENRICH_PROMPT,
        substring_keywords=(),
        use_proper_noun_path=True,
        reject_on_keyword_path=False,
        disqualify_on_longevity=True,
        cap_established_in_classifier=True,
        side_project_solo_threshold=3,
        alert_label="OSI",
    )


def _build_construction() -> VerticalProfile:
    return VerticalProfile(
        name="construction",
        label="Construction",
        keywords=CONSTRUCTION_KEYWORDS,
        proper_nouns=frozenset(),
        reject_tokens=_CONSTRUCTION_REJECT_TOKENS,
        classify_prompt=CONSTRUCTION_CLASSIFY_PROMPT,
        enrich_prompt=CONSTRUCTION_ENRICH_PROMPT,
        substring_keywords=("hvac",),
        use_proper_noun_path=False,
        reject_on_keyword_path=True,
        disqualify_on_longevity=False,
        cap_established_in_classifier=False,
        side_project_solo_threshold=4,
        alert_label="🏗️ Construction",
    )


def _build_minnesota() -> VerticalProfile:
    # Deferred import keeps this module a leaf (geo_gate imports only re/dataclasses).
    from geo_gate import MN_CLEANING_AREAS

    return VerticalProfile(
        name="minnesota",
        label="🧹 Minnesota",
        keywords=frozenset(),          # no industry keyword — firehose runs name-filter OFF
        proper_nouns=frozenset(),
        reject_tokens=frozenset(),
        classify_prompt=MINNESOTA_CLASSIFY_PROMPT,
        enrich_prompt=MINNESOTA_ENRICH_PROMPT,
        substring_keywords=(),
        use_proper_noun_path=False,
        reject_on_keyword_path=False,
        disqualify_on_longevity=False,        # D6=B: keep established (just label newness)
        cap_established_in_classifier=False,  # D6=B
        side_project_solo_threshold=3,
        alert_label="🧹 Minnesota",
        bypass_keyword_filter=True,
        require_content_geo_gate=True,
        service_areas=MN_CLEANING_AREAS,
    )


_BUILDERS = {
    "outdoor": _build_outdoor,
    "construction": _build_construction,
    "minnesota": _build_minnesota,
}
_CACHE: dict[str, VerticalProfile] = {}

VALID_VERTICALS = tuple(_BUILDERS)


def get_active_vertical() -> str:
    """Active vertical name from the VERTICAL env var (default 'outdoor')."""
    return (os.environ.get("VERTICAL") or "outdoor").strip().lower()


def get_profile(name: str | None = None) -> VerticalProfile:
    """Return the VerticalProfile for ``name`` (or the active vertical).

    Profiles are built once and cached. Raises SystemExit on an unknown name so a
    typo in VERTICAL / --vertical fails fast and loudly instead of silently
    running the wrong market.
    """
    key = (name or get_active_vertical()).strip().lower()
    if key not in _BUILDERS:
        raise SystemExit(
            f"Unknown VERTICAL={key!r}; valid verticals: {', '.join(VALID_VERTICALS)}"
        )
    if key not in _CACHE:
        _CACHE[key] = _BUILDERS[key]()
    return _CACHE[key]

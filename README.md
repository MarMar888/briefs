[![Backfill Queue](https://github.com/MarMar888/briefs/actions/workflows/backfill.yml/badge.svg)](https://github.com/MarMar888/briefs/actions/workflows/backfill.yml)
[![Lead Audit](https://github.com/MarMar888/briefs/actions/workflows/enrich.yml/badge.svg)](https://github.com/MarMar888/briefs/actions/workflows/enrich.yml)
[![Live Ingest Loop](https://github.com/MarMar888/briefs/actions/workflows/daily-pipeline.yml/badge.svg)](https://github.com/MarMar888/briefs/actions/workflows/daily-pipeline.yml)
# Outdoor Sports Lead Monitor

Finds newly emerging outdoor recreation businesses for an outdoor insurance broker.

The highest-value signal is newly registered domains: a business that just bought a domain and put up a real site may not have bought commercial insurance yet. The scanner imports newly registered domain lists, filters for outdoor keywords, checks whether the site is live, classifies likely leads, and writes a reviewable CSV with score, location, redirect, phone, and email fields.

## Verticals (one pipeline, many markets)

The same pipeline runs multiple **markets** side by side — currently `outdoor` (the
original OSI insurance-broker use case), `construction` (newly-forming contractors as
sales leads for a Ramp SDR), and `minnesota` (any new brick-and-mortar business in a Twin
Cities cleaning company's service area). One shared database holds them all; every lead row
carries an `industry` column so the markets never mix.

- **Selecting a vertical:** the `VERTICAL` env var (default `outdoor`), or `--vertical` on
  `run.py` / `enricher.py`. The default keeps the original OSI behavior byte-for-byte.
- **What a profile changes:** the domain keywords, the classify + enrich LLM prompts, the
  audit rules, and the email/branding label. Everything else (state machine, geo, crawl,
  scoring scale, dashboard) is shared. Profiles live in **`vertical_profiles.py`** — the
  per-vertical source of truth (mirrored into `frontend/lib/keywords.ts` for display).
- **Construction thesis is inverted from OSI.** OSI wants *new* businesses and disqualifies
  established ones. A Ramp lead just needs ongoing *spend*, so **new *and* established
  construction businesses both qualify** — the only hard disqualifiers are tiny
  hobby/side-projects with no real spend and out-of-country/invalid sites. (The longevity
  signal is still computed and shown on the dashboard; for construction it just never
  suppresses.)
- **`not_outdoor`** is a vertical-agnostic "classifier-rejected" bucket — for construction
  it simply means "not a qualifying construction lead." (Kept as-is to avoid a status migration.)
- **Minnesota is geographic, not industry-based.** The `minnesota` vertical has no industry
  keyword, so it runs the **full NRD firehose with the domain-name filter OFF**
  (`bypass_keyword_filter`) and gates on **fetched page content** instead
  (`require_content_geo_gate`): a domain is kept only if its content shows a **core
  service-area ZIP** (→ `service_tier = core`) or a **Twin Cities metro phone** (→ `adjacent`).
  The gate, the service-area ZIP set, and the signal tokens live in **`geo_gate.py`**. Because
  the name filter is off, the site phase scrapes harder (realistic browser headers, preserved
  footer + JSON-LD, and a bounded contact-page "second look"), only gate-passers reach the LLM,
  and full coverage **drains over time** via the durable queue (it won't clear a day's firehose
  in one run — by design). Basic crawl info (title, snippet, detected ZIPs/state, phone, email)
  is stored on **every reachable row** — matched or not — to build a dataset. Like construction,
  it keeps established businesses (it just labels newness). Domain *names* that hint Twin Cities
  (`minneapolis`, `stpaul`, `mn`…) are processed first via a reorder-only `priority`.
- **Overlap (discoverer-wins).** All verticals share one firehose + DB with an immutable
  `industry` stamp, so a Minnesota business whose domain *name* trips an outdoor/construction
  keyword is claimed by that vertical and stays there. Accepted, bounded.

## What Counts As A Lead

The classifier targets commercial US businesses in or adjacent to outdoor recreation:

- Gear retail, rentals, demos, repairs, and ranges: ski, fishing, hunting, archery, kayak, bike, camping, outdoor apparel, gun shops, shooting ranges.
- Lodging and venues: outdoor resorts, lodges, cabin rentals, campgrounds, glamping, RV parks, marinas, fishing/hunting lodges.
- Manufacturers or distributors of outdoor gear, apparel, and accessories.
- Paid outdoor activity operators: hiking/biking/kayak tours, fishing charters, hunting guides, rafting, zip lines, horseback trail riding, snowmobile tours.

The classifier tries to reject old/established businesses, generic template sites, parked domains, non-US businesses, nonprofits/clubs, and sites without enough concrete business details.

## Pipeline

1. **Import domains** from domains-monitor.com daily updates or downloaded backfill files.
2. **Keyword filter** domain names when `--keywords` is used.
3. **Queue in SQLite or Turso/LibSQL** at `domain_leads.sqlite3` unless `DOMAIN_DB_PATH` or `TURSO_DB_URL` is set.
4. **Resolve and geolocate hosting IPs** with DNS + `ip-api.com`; non-US-hosted domains are marked terminal `non_us`.
5. **Fetch and validate websites**; parked, coming-soon, sparse, and unreachable sites stay `site_pending` for later retry.
6. **Classify with OpenRouter** using a 0-100 lead score and structured fields. Score ≥ 70 → `matched`.
7. **Deep-search audit** (second-stage filter) on matched leads: multi-page crawl + sitemap + off-site search, then longevity/size/side-project signals. Established businesses and side projects are flagged `audit_verdict = disqualified` (suppressed from the default view and alerts — never deleted; see Design Principles).
8. **Alert via Resend** for newly matched, audit-qualified domains that have not already been emailed.
9. **Review in the Vercel frontend** backed by the same Turso database.

Important: “US-hosted” means the resolved IP geolocated to the US. It does not prove the business is in the US. The classifier also checks page content for US location signals and demotes obvious/ambiguous non-US lodging or tour leads.

## Design Principles

These are load-bearing. Re-read them before changing any filtering or audit logic.

**Label, don't kill — automated filters are never one-way doors.** A heuristic's job
is to *triage*, not to decide. When the audit (or any automated stage) rejects a lead,
it must demote it so it drops out of the default dashboard view and out of alerts —
**not** delete it, and **not** move it to a state it can never return from. Every
automated rejection must be:

- **Reversible** — a human (or a later rescrape) can bring it back.
- **Visible** — it stays queryable/filterable in the dashboard, with its verdict shown.
- **Attributed** — you can see *why* it was demoted (the audit reason).

Concretely:

- Heuristic disqualifications keep the lead in `status = matched` with
  `audit_verdict = 'disqualified'` plus a reason. They are filtered out of the default
  view and alerts — they are **not** deleted and **not** moved to a terminal status.
- Only a human verdict, or a hard unambiguous fact (e.g. non-US hosting IP), may set a
  truly terminal state.
- **External/off-site data may inform a flag but must never, on its own, drive a
  rejection.** A stray year in a directory snippet ("…since 2004") must not auto-kill a
  brand-new lead. Deterministic longevity/age checks run on the business's own site
  content; off-site search is context for the LLM and the dashboard, not a kill switch.

The reason: the cost of a false "kill" (a real new lead silently gone) is far higher than
the cost of a false "keep" (one extra row a human skips past). Keep the funnel ruthless in
what it *surfaces*, never in what it *destroys*.

## Architecture

The pipeline is a **state machine over a durable queue**, not a script: every domain is a
row with a `status`, and each stage advances rows that are due in a given state. That makes
every stage resumable, retryable, idempotent, and observable (counts at each hop = the
sourcing funnel).

Three GitHub Actions workflows drive it, sharing one concurrency group so they never overlap
(they touch the same matched backlog — overlap would double LLM spend and worsen search
throttling):

```
 LIVE INGEST LOOP (daily)     BACKFILL QUEUE (every 4h)      LEAD AUDIT (daily + inline)
 ───────────────────────     ─────────────────────────      ──────────────────────────
 fetch NRDs                  drain geo_pending               consume: matched & not-yet-audited
 TLD + keyword filter        drain site_pending              crawl + sitemap + off-site search
 seed queue (status=new)     classify → matched              LLM extract + deterministic signals
        │                          │                         write: audit fields + verdict
        │                          │                         qualified   → eligible to alert
        │                          │                         disqualified → suppressed, NOT killed
        ▼                          ▼                                   │
        └───────────► domains queue (explicit states) ◄───────────────┘
                              │                                        │
                              │                                        ▼
                              └────────► DELIVER (Resend): alert where
                                         status=matched AND audit_verdict='qualified'
                                         AND email_sent_at IS NULL      (idempotent)
        ▲
        └──── ONE concurrency group `lead-pipeline`: the three queue, never run at once

 Funnel ordering = cheapest filter first: keyword (free) → geo (cheap) → classify (LLM)
                   → deep audit (most expensive). Spend the LLM budget on what survives.

 States: new → geo_pending → site_pending → matched → (audited)
         terminal: not_outdoor · non_us · expired        (only hard facts/human get terminal)
         matched + audit_verdict='disqualified' = suppressed, reversible, still in the DB
```

MVP notes: the audit currently runs **inline** in `run.py` after classification (bounded by
`INLINE_AUDIT_LIMIT`, default 200) so alerts only fire on audit-qualified leads, with the
standalone **Lead Audit** job draining any remainder. The longer-term shape is the same audit
as a fully decoupled stage that delivery just reads from — already 80% there.

## Versioning

The pipeline version is a semver in the **`VERSION`** file at the repo root (the Python
equivalent of an npm package version). `version.py` reads it and appends the short git sha as
build metadata, e.g. `0.1.0+a1b2c3d` — locally from `git rev-parse`, in CI from `GITHUB_SHA`
(provided automatically by GitHub Actions). `PIPELINE_VERSION` env var fully overrides it.

Every lead records the exact pipeline version at each lifecycle stage, so any row traces back
to the code that produced it:

```text
found_version       version that discovered/ingested the domain   (upsert)
classified_version  version that classified it (matched/not_outdoor) (site phase)
enriched_version    version that ran the deep-search audit          (enricher)
```

Each row in `pipeline_runs` also stores `pipeline_version`. **Bump `VERSION`** whenever the
pipeline's behavior changes (new filters, scoring tweaks, audit logic) so you can tell which
leads were produced before vs after the change.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set these in `.env`:

```bash
OPENROUTER_API_KEY=your_key_here
SERPAPI_KEY=your_key_here
DOMAINS_MONITOR_TOKEN=your_domains_monitor_token
FIRECRAWL_API_KEY=fc_your_key
TURSO_DB_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
RESEND_API_KEY=re_your_key
ALERT_EMAIL=marleyhansenbarrett@gmail.com
ALERT_FROM=OSI Lead Monitor <alerts@your-domain.com>
```

`OPENROUTER_API_KEY` is required for classification. `DOMAINS_MONITOR_TOKEN` is required for the live domain feed. `FIRECRAWL_API_KEY` is optional but improves JavaScript-heavy site extraction. `SERPAPI_KEY` is only needed for the older Minnesota SOS filing workflow.

## Common Commands

Run a small one-day live domain scan from domains-monitor:

```bash
.venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainsmonitor \
  --days 1 \
  --keywords \
  --domain-limit 500 \
  --output digest.csv
```

Backfill a downloaded domains-monitor file:

```bash
.venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainsmonitor-file \
  --domainsmonitor-path ./domains-monitor-backfill.txt \
  --keywords \
  --domain-limit 0 \
  --output backfill.csv
```

Run against Turso from local Python:

```bash
DOMAIN_DB_PATH="$TURSO_DB_URL" .venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainsmonitor \
  --keywords \
  --domain-limit 1000
```

Use an isolated test database so experiments do not mix with the main queue:

```bash
DOMAIN_DB_PATH=.context/rest_may_test.sqlite3 .venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainsmonitor-file \
  --domainsmonitor-path ./domains-monitor-backfill.txt \
  --keywords \
  --domain-limit 200 \
  --output .context/rest-may-test.csv
```

Combine MN SOS filings with domain scanning:

```bash
.venv/bin/python3 run.py --domains --days 7 --keywords --output digest.csv
```

## Frontend

The Vercel app lives in `frontend/`.

```bash
cd frontend
npm install
TURSO_DB_URL="$TURSO_DB_URL" TURSO_AUTH_TOKEN="$TURSO_AUTH_TOKEN" npm run dev
```

Deploy `frontend/` to Vercel and set `TURSO_DB_URL` plus `TURSO_AUTH_TOKEN` in Vercel environment variables.

## Output Columns

CSV output includes:

```text
Business Name
City
Filing Date
Website
Match
Score
Score Category
Redirected To
Redirect Domain
Phone
Email
Reason
```

Score categories:

```text
90-100  Strong Match
70-89   Likely Match
50-69   Borderline
25-49   Weak
0-24    No Match
```

By default, the CSV contains matched leads only. Use `--all` to include every processed filing/domain result from that run.

## Queue States

The domain queue is stored in SQLite.

```text
new           Imported but not resolved/geolocated yet
geo_pending   DNS or IP geolocation failed; retry later
site_pending  US-hosted but site is unreachable, parked, sparse, or deferred
matched       Classified as a lead
not_outdoor   Classified as not a lead
non_us        Hosting IP geolocated outside the US
expired       No longer active after the tracking window
```

Matched leads also carry an `audit_verdict` once the deep-search audit has run:
`qualified` (eligible for alerts + default view) or `disqualified` (established business
or side project — suppressed from the default view and alerts, but kept in the DB and
visible via the dashboard's "Suppressed" audit filter). The audit never deletes a lead or
moves it to a terminal state — see Design Principles.

Useful inspection commands:

```bash
sqlite3 domain_leads.sqlite3 "SELECT status, COUNT(*) FROM domains GROUP BY status;"
sqlite3 domain_leads.sqlite3 "SELECT * FROM matched_domains LIMIT 20;"
```

## Retry Behavior

The pipeline is resumable. Re-running the same command does not start from scratch:

- Existing domains are ignored on insert.
- `matched`, `not_outdoor`, `non_us`, and `expired` are terminal.
- `geo_pending` and `site_pending` remain eligible for future runs.
- `site_pending` retries escalate by attempt count: 7 days, 14 days, 21 days, and so on until the 180-day tracking window expires.
- `next_check_at` can also defer the first site check when using `--defer-site-days`.

This matters because brand-new domains often have no site yet. A later run can pick up domains that were parked, sparse, or unreachable during the first pass.

## Scraping And Classification Notes

This is a needle-in-a-haystack scraping problem, so optimize the pipeline for staged filtering rather than expecting one classifier pass to be perfect:

- Keep early filters broad and recall-oriented. Many good new businesses start with thin, generic, or half-finished landing pages.
- Spend expensive extraction and LLM classification only after cheap filters have reduced the candidate set.
- Treat ambiguous labels as normal. The definition of a strong outdoor lead will drift as the market, broker appetite, and available signals change.
- Maintain a small, clean review set of manually checked examples for threshold tuning. Noisy production labels are useful for volume, but calibration needs high-confidence examples.
- Review rejected and borderline domains periodically. False negatives are easy to miss when the positive class is rare.
- Look beyond page text when useful: redirects, DNS age, sitemap shape, contact details, external links, and site structure can all help separate real businesses from parked or template sites.

## Files

```text
run.py            Main CLI entry point (--vertical selects the market)
vertical_profiles.py  Per-vertical config: keywords, classify/enrich prompts, audit rules, label
domain_scanner.py Domain import, keyword filtering, DNS, geo, site phase, queue updates
domain_store.py   SQLite/Turso schema, migrations, industry column, matched_domains view
classifier.py     Website fetching, Firecrawl fallback, contact extraction, redirects, OpenRouter classification
email_alerts.py   Resend digest emails for unalerted matched domains
frontend/         Next.js review dashboard for Vercel
fetcher.py        Minnesota SOS filing fetcher and shared Filing model
discoverer.py     SerpAPI website discovery for MN filings
digest.py         CSV/plain-text output formatting
domain_leads.sqlite3 Default domain queue database
.env.example      Environment variable template
```

## Current Caveats

- IP geolocation is only a hosting filter, not proof of business location.
- Domain keyword filtering is intentionally broad; it catches good leads but also many false positives like outdoor lighting, landscaping, restaurants, templates, and parked pages.
- Redirects are tracked, not rejected. A redirected new domain may still be useful, but it is weaker evidence that the business itself is brand new.
- Phone and email extraction only sees text available in fetched page content.
- JavaScript-heavy sites use Firecrawl only after the basic fetch returns sparse JS-heavy content, so Firecrawl cost stays bounded.

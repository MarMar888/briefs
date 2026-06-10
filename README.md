# Outdoor Sports Lead Monitor

Finds newly emerging outdoor recreation businesses for an outdoor insurance broker.

The highest-value signal is newly registered domains: a business that just bought a domain and put up a real site may not have bought commercial insurance yet. The scanner imports newly registered domain lists, filters for outdoor keywords, checks whether the site is live, classifies likely leads, and writes a reviewable CSV with score, location, redirect, phone, and email fields.

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
6. **Classify with OpenRouter** using a 0-100 lead score and structured fields.
7. **Alert via Resend** for newly matched domains that have not already been emailed.
8. **Review in the Vercel frontend** backed by the same Turso database.

Important: “US-hosted” means the resolved IP geolocated to the US. It does not prove the business is in the US. The classifier also checks page content for US location signals and demotes obvious/ambiguous non-US lodging or tour leads.

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
run.py            Main CLI entry point
domain_scanner.py Domain import, keyword filtering, DNS, geo, site phase, queue updates
domain_store.py   SQLite/Turso schema, migrations, matched_domains view
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

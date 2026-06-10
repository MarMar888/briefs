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

1. **Import domains** from WhoisDS, DomainKits, or local DomainKits files.
2. **Keyword filter** domain names when `--keywords` is used.
3. **Queue in SQLite** at `domain_leads.sqlite3` unless `DOMAIN_DB_PATH` is set.
4. **Resolve and geolocate hosting IPs** with DNS + `ip-api.com`; non-US-hosted domains are marked terminal `non_us`.
5. **Fetch and validate websites**; parked, coming-soon, sparse, and unreachable sites stay `site_pending` for later retry.
6. **Classify with OpenRouter** using a 0-100 lead score and structured fields.
7. **Write CSV output** for newly matched leads only, unless `--all` is used.

Important: “US-hosted” means the resolved IP geolocated to the US. It does not prove the business is in the US. The classifier also checks page content for US location signals and demotes obvious/ambiguous non-US lodging or tour leads.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m playwright install chromium
cp .env.example .env
```

Set these in `.env`:

```bash
OPENROUTER_API_KEY=your_key_here
SERPAPI_KEY=your_key_here
```

`OPENROUTER_API_KEY` is required for classification. `SERPAPI_KEY` is only needed for the older Minnesota SOS filing workflow.

## Common Commands

Run a small one-day domain scan from WhoisDS:

```bash
.venv/bin/python3 run.py \
  --domains-only \
  --days 1 \
  --keywords \
  --domain-limit 500 \
  --output digest.csv
```

Backtest local DomainKits files in a folder:

```bash
.venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainkits-file \
  --domainkits-path ./domainkits-nrds \
  --start-date 2026-05-16 \
  --days 16 \
  --keywords \
  --domain-limit 1000 \
  --output rest-may-1000.csv
```

Run the full keyword-filtered set for a date range:

```bash
.venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainkits-file \
  --domainkits-path ./domainkits-nrds \
  --start-date 2026-05-16 \
  --days 16 \
  --keywords \
  --domain-limit 0 \
  --output rest-may-full.csv
```

Use an isolated test database so experiments do not mix with the main queue:

```bash
DOMAIN_DB_PATH=.context/rest_may_test.sqlite3 .venv/bin/python3 run.py \
  --domains-only \
  --domain-source domainkits-file \
  --domainkits-path ./domainkits-nrds \
  --start-date 2026-05-16 \
  --days 16 \
  --keywords \
  --domain-limit 200 \
  --output .context/rest-may-test.csv
```

Combine MN SOS filings with domain scanning:

```bash
.venv/bin/python3 run.py --domains --days 7 --keywords --output digest.csv
```

## DomainKits File Naming

When `--domainkits-path` points at a directory, files are matched by date in the filename. Preferred names:

```text
com-domains-2026-05-16.txt.gz
com-domains-2026-05-17.txt.gz
```

If DomainKits downloads are named like `com-domains (7).txt.gz`, create date-named symlinks or rename them before running a date range.

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
```

Useful inspection commands:

```bash
sqlite3 domain_leads.sqlite3 "SELECT status, COUNT(*) FROM domains GROUP BY status;"
sqlite3 domain_leads.sqlite3 "SELECT * FROM matched_domains LIMIT 20;"
```

## Retry Behavior

The pipeline is resumable. Re-running the same command does not start from scratch:

- Existing domains are ignored on insert.
- `matched`, `not_outdoor`, and `non_us` are terminal.
- `geo_pending` and `site_pending` remain eligible for future runs.
- `next_check_at` can defer site checks when using `--defer-site-days`.

This matters because brand-new domains often have no site yet. A later run can pick up domains that were parked, sparse, or unreachable during the first pass.

## Files

```text
run.py            Main CLI entry point
domain_scanner.py Domain import, keyword filtering, DNS, geo, site phase, queue updates
domain_store.py   SQLite schema, migrations, matched_domains view
classifier.py     Website fetching, contact extraction, redirects, OpenRouter classification
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
- Phone and email extraction only sees text available in fetched/rendered page content.
- JavaScript-heavy sites are best effort via Playwright; install Chromium with `python -m playwright install chromium`.

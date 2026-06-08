# MN Outdoor Sports Lead Monitor

Watches new Minnesota business registrations, finds their website or social profile, and surfaces the ones that look like outdoor sports businesses (ski shops, outfitters, guides, gear rentals, adventure tours) to an insurance broker before they've already bought coverage.

## How it works

1. **Fetch** — pulls new business filings from the MN Secretary of State
2. **Discover** — searches for each business's website or social profile
3. **Classify** — asks Claude "is this an outdoor sports business?" with a yes/no + reason
4. **Digest** — outputs a list of matches you can review or email

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# add your ANTHROPIC_API_KEY and SERPAPI_KEY to .env
```

## Run

```bash
# fetch new filings from the last 7 days and classify them
python run.py --days 7

# output to a file instead of stdout
python run.py --days 7 --output digest.csv
```

## Output

```
Business Name        | City       | Website                  | Match | Reason
---------------------|------------|--------------------------|-------|-------
Snake River Outfitters| Duluth    | snakeriveroutfitters.com | YES   | Guided fly fishing and kayak tours
Summit Gear LLC      | Minneapolis| summitgear.com           | YES   | Outdoor equipment retail and rental
Blue Sky Consulting  | St. Paul   | —                        | NO    | Business consulting firm
```

## Files

```
run.py          — main entry point
fetcher.py      — pulls MN SOS filings
discoverer.py   — finds website/social for each business
classifier.py   — Claude-based outdoor sports classifier
digest.py       — formats output as CSV or plain text
.env.example    — environment variable template
```

## Notes

- MN SOS data: scraped from https://mblsportal.sos.state.mn.us/
- Website discovery uses SerpAPI (Google search for business name + city + MN)
- Classification prompt targets: ski shops, snowboard shops, outfitters, hunting/fishing guides, gear rentals, kayak/canoe rentals, climbing gyms, adventure tour operators, hiking/backpacking guides
- Businesses with no website found are still included in output — flag them for manual review

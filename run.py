"""
MN Outdoor Sports Lead Monitor
Usage: python run.py --days 7 [--output digest.csv] [--all]
"""

import argparse
import os
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_new_filings
from discoverer import find_website
from classifier import classify
from digest import Result, to_csv, to_text


def main():
    parser = argparse.ArgumentParser(description="MN outdoor sports lead monitor")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--output", type=str, help="Write CSV to this file instead of stdout")
    parser.add_argument("--all", action="store_true", help="Show all businesses, not just matches")
    args = parser.parse_args()

    print(f"[run] Fetching MN SOS filings from the last {args.days} days...")
    filings = fetch_new_filings(days=args.days)
    print(f"[run] Found {len(filings)} new filings")

    results = []
    for i, filing in enumerate(filings, 1):
        print(f"[run] {i}/{len(filings)} {filing.name} ({filing.city})")

        website = find_website(filing.name, filing.city)
        verdict = classify(filing.name, filing.city, website)

        results.append(Result(
            name=filing.name,
            city=filing.city,
            filing_date=filing.filing_date,
            website=website or "",
            match=verdict["match"],
            reason=verdict["reason"],
        ))

    if args.output:
        with open(args.output, "w") as f:
            f.write(to_csv(results if args.all else [r for r in results if r.match]))
        print(f"[run] Saved to {args.output}")
    else:
        print("\n" + to_text(results))


if __name__ == "__main__":
    main()

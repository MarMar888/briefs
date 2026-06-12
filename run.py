"""
MN Outdoor Sports Lead Monitor
Usage: python run.py --days 7 [--output digest.csv] [--all]
       python run.py --domains --days 7 [--domain-limit 3000] [--output digest.csv]
"""

import argparse
import os
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_new_filings
from discoverer import find_website
from classifier import classify, classify_domain
from digest import Result, to_csv, to_text


def main():
    parser = argparse.ArgumentParser(description="Outdoor sports lead monitor")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--output", type=str, help="Write CSV to this file instead of stdout")
    parser.add_argument("--all", action="store_true", help="Show all businesses, not just matches")
    parser.add_argument("--domains", action="store_true", help="Scan newly registered domains (in addition to MN SOS)")
    parser.add_argument("--domains-only", action="store_true", help="Skip MN SOS, only scan new domains")
    parser.add_argument("--domain-limit", type=int, default=3000,
                        help="Max new domains to queue after TLD/keyword filtering (default: 3000; 0 = no limit)")
    parser.add_argument("--keywords", action="store_true",
                        help="Pre-filter domains by outdoor keywords before geo/scrape (much higher signal-to-noise)")
    parser.add_argument("--keyword-workers", type=int, default=0,
                        help="Processes for keyword filtering (default: auto for --domain-file, 1 otherwise; 0 = auto)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start date for NRD pulls in YYYY-MM-DD format; pulls --days days forward from this date (default: yesterday)")
    parser.add_argument("--defer-site-days", type=int, default=0,
                        help="Delay site classification N days after geo resolves (e.g. 15 gives new sites time to go live)")
    parser.add_argument(
        "--domain-source",
        choices=["domainsmonitor", "domainsmonitor-file", "whoisds", "domainkits", "domainkits-file"],
        default="domainsmonitor",
        help="Newly registered domain source (default: domainsmonitor)",
    )
    parser.add_argument("--domainkits-path", type=str, default=None,
                        help="File or directory of DomainKits .txt/.csv/.gz/.zip downloads when --domain-source domainkits-file")
    parser.add_argument("--domainsmonitor-path", type=str, default=None,
                        help="File or directory of domains-monitor downloads when --domain-source domainsmonitor-file")
    parser.add_argument("--domain-file", type=str, default=None,
                        help="Plain-text file of domains (one per line); shorthand for --domains-only --domain-source domainkits-file --domainkits-path <file> --keywords --domain-limit 0")
    parser.add_argument("--skip-domain-import", action="store_true",
                        help="For --domains/--domains-only, skip source import/filtering and resume queued domains already in SQLite")
    parser.add_argument("--skip-geo", action="store_true",
                        help="Skip geo phase and go straight to site classification")
    parser.add_argument("--site-limit", type=int, default=0,
                        help="Max site_pending domains to classify per run (0 = no limit); use for batched backfill")
    parser.add_argument("--geo-limit", type=int, default=0,
                        help="Max new/geo_pending domains to geo-check per run (0 = no limit)")
    parser.add_argument("--rescrape-days", type=int, default=30,
                        help="Days between rescrapes of already-classified domains (default: 30)")
    args = parser.parse_args()

    if args.domain_file:
        args.domains_only = True
        args.domain_source = "domainkits-file"
        args.domainkits_path = args.domain_file
        args.keywords = True
        if args.domain_limit == 3000:
            args.domain_limit = 0

    if args.keyword_workers == 0:
        args.keyword_workers = max(1, min((os.cpu_count() or 2) - 1, 8)) if args.domain_file else 1

    filings = []

    if not args.domains_only:
        print(f"[run] Fetching MN SOS filings from the last {args.days} days...")
        sos_filings = fetch_new_filings(days=args.days)
        print(f"[run] Found {len(sos_filings)} new SOS filings")
        filings.extend(sos_filings)

    if args.domains or args.domains_only:
        import domain_store
        from domain_scanner import scan_new_domains
        from email_alerts import send_match_alerts

        domain_store.init_db()
        run_id = domain_store.start_run(source=args.domain_source)
        print(f"[run] Scanning newly registered domains from {args.domain_source} (last {args.days} days, limit {args.domain_limit})...")
        try:
            domain_filings, scan_stats = scan_new_domains(
                days=args.days,
                limit=args.domain_limit,
                keyword_filter=args.keywords,
                start_date=args.start_date,
                defer_site_days=args.defer_site_days,
                source=args.domain_source,
                domainkits_path=args.domainkits_path,
                domainsmonitor_path=args.domainsmonitor_path,
                keyword_workers=args.keyword_workers,
                skip_import=args.skip_domain_import,
                skip_geo=args.skip_geo,
                site_limit=args.site_limit,
                geo_limit=args.geo_limit,
                rescrape_days=args.rescrape_days,
            )
            domain_store.finish_run(run_id, **scan_stats)
        except Exception as exc:
            domain_store.finish_run(run_id, matched=0, downloaded=0, inserted=0, expired=0, error=str(exc))
            raise


        print(f"[run] {len(domain_filings)} new USA-hosted domains queued for classification")
        filings.extend(domain_filings)

        # Second-stage filter: deep-search audit runs BEFORE alerting so only
        # audit-qualified leads are emailed. Bounded so a large match batch can't
        # blow this run's time budget — the standalone Lead Audit job drains the
        # rest. Disqualified leads stay in the DB, just suppressed (see enricher).
        from enricher import run_enrichment
        inline_audit_limit = int(os.environ.get("INLINE_AUDIT_LIMIT", "200"))
        print(f"[run] Running deep-search audit on newly matched leads (limit {inline_audit_limit})...")
        run_enrichment(limit=inline_audit_limit)

        unalerted = domain_store.get_unalerted_matches()
        if unalerted and send_match_alerts(unalerted):
            domain_store.mark_alert_sent([match["domain"] for match in unalerted])

    results = []
    for i, filing in enumerate(filings, 1):
        print(f"[run] {i}/{len(filings)} {filing.name} ({filing.city or 'domain'})", flush=True)

        if filing.verdict is not None:
            # Pre-classified by domain scanner (already went through validate_site + LLM)
            verdict = filing.verdict
            website = filing.website
        elif filing.website:
            # Domain-sourced lead with a URL but not yet classified
            from classifier import validate_site
            site = validate_site(filing.website)
            if site["pending_reason"]:
                verdict = {"match": False, "reason": f"site pending: {site['pending_reason']}"}
            else:
                verdict = classify_domain(filing.name, site["content"])
            verdict["redirected_to"] = site.get("redirected_to", "")
            verdict["redirect_domain"] = site.get("redirect_domain", "")
            verdict["phone"] = site.get("phone", "")
            verdict["email"] = site.get("email", "")
            website = filing.website
        else:
            website = find_website(filing.name, filing.city)
            verdict = classify(filing.name, filing.city, website)

        results.append(Result(
            name=filing.name,
            city=filing.city,
            filing_date=filing.filing_date,
            website=website or "",
            match=verdict["match"],
            reason=verdict["reason"],
            score=verdict.get("score"),
            score_category=verdict.get("score_category", ""),
            redirected_to=verdict.get("redirected_to", filing.redirected_to),
            redirect_domain=verdict.get("redirect_domain", filing.redirect_domain),
            phone=verdict.get("phone", filing.phone),
            email=verdict.get("email", filing.email),
            ecom_only=verdict.get("ecom_only", False),
        ))

    if args.output:
        with open(args.output, "w") as f:
            f.write(to_csv(results if args.all else [r for r in results if r.match]))
        print(f"[run] Saved to {args.output}")
    else:
        print("\n" + to_text(results))


if __name__ == "__main__":
    main()

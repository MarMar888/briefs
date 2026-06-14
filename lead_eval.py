"""
Lead evaluation harness — audit found leads and print a quality report.

Runs the real deep-search audit (the enricher) over the matched leads for a vertical,
then prints each lead grouped by audit outcome (QUALIFIED vs SUPPRESSED) with its score,
classify reason, longevity, side-project flag, and business summary — so you can eyeball
lead quality and catch regressions after tuning prompts or keywords.

Use the SAME DOMAIN_DB_PATH as your find run so it reads the leads you actually found.
Point it at an isolated test DB (not the live Turso) while iterating.

Usage:
  # 1) audit + report on leads already in the DB (e.g. found by a prior run.py):
  DOMAIN_DB_PATH=/tmp/construction_test.sqlite3 python lead_eval.py --vertical construction

  # 2) one-shot — find from a domain file, then auto-audit, then report:
  DOMAIN_DB_PATH=/tmp/construction_test.sqlite3 python lead_eval.py --vertical construction --find domains.txt

  # re-audit everything (after a prompt change), not just not-yet-audited leads:
  DOMAIN_DB_PATH=/tmp/construction_test.sqlite3 python lead_eval.py --vertical construction --reaudit
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

import domain_store
from enricher import run_enrichment
from vertical_profiles import get_profile


def _print_lead(row: dict) -> None:
    bits = [f"score {row.get('score')}"]
    if row.get("longevity") and row["longevity"] != "No age signal found":
        bits.append(row["longevity"])
    if row.get("side_project"):
        bits.append("side-project")
    if row.get("audit_notes"):
        bits.append(row["audit_notes"])
    print(f"  {row['domain']:<36} {' | '.join(str(b) for b in bits if b)}")
    if row.get("business_summary"):
        print(f"      {row['business_summary']}")
    if row.get("reason"):
        print(f"      why: {row['reason']}")


def report(profile) -> None:
    rows = [r for r in domain_store.get_matched() if r.get("industry") == profile.name]
    rows.sort(key=lambda r: (r.get("score") or 0), reverse=True)

    qualified = [r for r in rows if r.get("audit_verdict") == "qualified"]
    suppressed = [r for r in rows if r.get("audit_verdict") == "disqualified"]
    unaudited = [r for r in rows if not r.get("enriched_at")]

    print(f"\n=== {profile.label}: {len(rows)} matched leads ===")
    print(f"\n✓ QUALIFIED ({len(qualified)}) — would surface / alert:")
    for r in qualified:
        _print_lead(r)
    print(f"\n✗ SUPPRESSED BY AUDIT ({len(suppressed)}) — hidden from default view:")
    for r in suppressed:
        _print_lead(r)
    if unaudited:
        print(f"\n… NOT YET AUDITED ({len(unaudited)}): {', '.join(r['domain'] for r in unaudited[:20])}")
    print(
        f"\nSummary [{profile.name}]: {len(rows)} matched → "
        f"{len(qualified)} qualified, {len(suppressed)} suppressed"
    )
    print(
        "Note: leads rejected at classify (e.g. tools/calculators/directories) never become "
        "matched, so they correctly do not appear above."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit found leads and print a quality report")
    ap.add_argument("--vertical", default=None,
                    help="outdoor | construction (default: VERTICAL env, or outdoor)")
    ap.add_argument("--find", metavar="FILE",
                    help="First find leads from this domain file (one per line), then audit them")
    ap.add_argument("--site-limit", type=int, default=0,
                    help="Max sites to classify during --find (0 = all)")
    ap.add_argument("--reaudit", action="store_true",
                    help="Re-audit every matched lead (use after changing prompts), not just new ones")
    args = ap.parse_args()

    profile = get_profile(args.vertical)
    domain_store.init_db()

    if args.find:
        from domain_scanner import scan_new_domains
        print(f"[eval] Finding {profile.name} leads from {args.find} ...", flush=True)
        scan_new_domains(
            source="domainkits-file",
            domainkits_path=args.find,
            keyword_filter=True,
            limit=0,
            site_limit=args.site_limit,
            profile=profile,
        )

    print(f"[eval] Auditing {'all' if args.reaudit else 'new'} {profile.name} matched leads ...", flush=True)
    run_enrichment(limit=0, reaudit="all" if args.reaudit else None, profile=profile)

    report(profile)


if __name__ == "__main__":
    main()

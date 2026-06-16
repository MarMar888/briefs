#!/usr/bin/env python3
"""
pipeline_health.py — one-shot pipeline status digest for humans and LLMs.

Reads entirely from GitHub Actions via the `gh` CLI.
Requires: gh auth login (already needed for everything else)

Usage:
    python pipeline_health.py           # last 10 runs
    python pipeline_health.py --runs 20

Exit code 1 if any warning is detected (timeouts, failures).
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


REPO = "MarMar888/briefs"

# Workflow timeout limits in minutes (mirrors .github/workflows/*.yml)
TIMEOUT_MINUTES = {
    "Backfill Queue": 330,
    "Lead Audit": 120,
    "Live Ingest Loop": 60,
}


def _run_gh(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh error: {r.stderr.strip()}")
    return r.stdout


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M UTC")
    except Exception:
        return iso[:16]


def _duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, rem = divmod(s, 60)
    h, m2 = divmod(m, 60)
    if h:
        return f"{h}h{m2}m" if m2 else f"{h}h"
    return f"{m}m{rem}s" if rem else f"{m}m"


def fetch_runs(limit: int) -> list[dict]:
    raw = _run_gh([
        "gh", "run", "list",
        "--repo", REPO,
        "--limit", str(limit),
        "--json", "databaseId,name,status,conclusion,createdAt",
    ])
    runs = json.loads(raw)

    enriched = []
    for run in runs:
        run_id = run["databaseId"]
        try:
            job_raw = _run_gh([
                "gh", "run", "view", str(run_id),
                "--repo", REPO,
                "--json", "jobs",
            ])
            jobs = json.loads(job_raw).get("jobs", [])
        except Exception:
            jobs = []

        timeout_min = TIMEOUT_MINUTES.get(run["name"])
        job_summaries = []
        for job in jobs:
            started, completed = job.get("startedAt"), job.get("completedAt")
            dur_s = None
            if started and completed:
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                e = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                dur_s = (e - s).total_seconds()

            timed_out = (
                job.get("conclusion") == "cancelled"
                and dur_s is not None
                and timeout_min is not None
                and dur_s >= (timeout_min * 60 - 30)
            )

            failed_steps = [
                step["name"] for step in job.get("steps", [])
                if step.get("conclusion") not in ("success", "skipped", None)
            ]

            job_summaries.append({
                "name": job["name"],
                "conclusion": job.get("conclusion"),
                "duration_s": dur_s,
                "timed_out": timed_out,
                "failed_steps": failed_steps,
            })

        enriched.append({**run, "jobs": job_summaries, "timeout_min": timeout_min})

    return enriched


def render(runs: list[dict]) -> list[str]:
    warnings: list[str] = []

    print(f"\n{'═' * 80}")
    print(f"  PIPELINE HEALTH  —  {_utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═' * 80}")
    print(f"\n{'Run ID':<14} {'Workflow':<22} {'Started':<18}  Jobs")
    print("─" * 90)

    for run in runs:
        icon = {"success": "✓", "failure": "✗", "cancelled": "⊘", "skipped": "–"}.get(
            run.get("conclusion") or "", "…"
        )

        job_parts = []
        for j in run["jobs"]:
            dur = _duration(j["duration_s"]) if j["duration_s"] is not None else "?"
            # Strip common prefixes to keep output tight
            label = (
                j["name"]
                .replace("backfill ", "")
                .replace("lead audit ", "")
                .replace("pipeline ", "")
            )

            if j["timed_out"]:
                tag = f" ⚠ TIMEOUT"
                warnings.append(
                    f"Run #{run['databaseId']} — '{j['name']}' timed out after {dur} "
                    f"(limit {run['timeout_min']}m)"
                )
            elif j["conclusion"] == "failure":
                tag = " ✗ FAILED"
                if j["failed_steps"]:
                    tag += f" [{', '.join(j['failed_steps'][:2])}]"
                warnings.append(f"Run #{run['databaseId']} — '{j['name']}' failed")
            elif j["conclusion"] == "cancelled" and not j["timed_out"]:
                tag = " ⊘"
            else:
                tag = ""

            job_parts.append(f"{label} {dur}{tag}")

        job_str = " | ".join(job_parts) if job_parts else run.get("status", "")
        print(f"{icon} {run['databaseId']:<12}  {run['name']:<22} {_fmt_dt(run['createdAt']):<18}  {job_str}")

    print()

    # Summary
    total = len(runs)
    ok = sum(1 for r in runs if r.get("conclusion") == "success")
    timeouts = sum(
        1 for r in runs
        for j in r["jobs"] if j["timed_out"]
    )
    failures = sum(
        1 for r in runs
        for j in r["jobs"] if j["conclusion"] == "failure"
    )

    print(f"  Runs shown: {total}   ✓ success: {ok}   ⚠ timeouts: {timeouts}   ✗ failures: {failures}")
    print(f"{'═' * 80}")

    if warnings:
        print(f"\n  ⚠  {len(warnings)} WARNING(S):")
        for w in warnings:
            print(f"     • {w}")
        print(f"{'═' * 80}")

    print()
    return warnings


def main():
    parser = argparse.ArgumentParser(description="Pipeline health digest")
    parser.add_argument("--runs", type=int, default=10, help="Number of runs to fetch (default 10)")
    args = parser.parse_args()

    try:
        runs = fetch_runs(args.runs)
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        print("Make sure `gh` is installed and authenticated (`gh auth status`)")
        sys.exit(2)

    warnings = render(runs)
    sys.exit(1 if warnings else 0)


if __name__ == "__main__":
    main()

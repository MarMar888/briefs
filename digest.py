"""
Formats classified results as a CSV or plain-text digest.
"""

import csv
import io
from dataclasses import dataclass


@dataclass
class Result:
    name: str
    city: str
    filing_date: str
    website: str
    match: bool
    reason: str


def to_csv(results: list[Result]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Business Name", "City", "Filing Date", "Website", "Match", "Reason"])
    for r in results:
        writer.writerow([
            r.name,
            r.city,
            r.filing_date,
            r.website or "—",
            "YES" if r.match else "NO",
            r.reason,
        ])
    return output.getvalue()


def to_text(results: list[Result]) -> str:
    matches = [r for r in results if r.match]
    lines = [
        f"Outdoor sports leads — {len(matches)} of {len(results)} new businesses matched\n",
        "-" * 60,
    ]
    for r in matches:
        lines.append(f"\n{r.name}")
        lines.append(f"  City:    {r.city}")
        lines.append(f"  Filed:   {r.filing_date}")
        lines.append(f"  Website: {r.website or 'not found'}")
        lines.append(f"  Why:     {r.reason}")
    if not matches:
        lines.append("\nNo matches found in this period.")
    return "\n".join(lines)

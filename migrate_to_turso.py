"""
One-shot script: copies all rows from a local SQLite file into the Turso DB.

Usage:
    TURSO_DB_URL=libsql://... TURSO_AUTH_TOKEN=... python3 migrate_to_turso.py [path/to/source.sqlite3]

Defaults to /Users/marleybarrett/conductor/workspaces/Briefs/houston/domain_leads.sqlite3
"""

import os
import sqlite3
import sys
from contextlib import closing

SOURCE = sys.argv[1] if len(sys.argv) > 1 else (
    "/Users/marleybarrett/conductor/workspaces/Briefs/houston/domain_leads.sqlite3"
)
TURSO_URL = os.environ.get("TURSO_DB_URL", "libsql://briefs-marmar888.aws-us-east-2.turso.io")
TURSO_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

# Columns present in the houston SQLite (older schema, missing expires_at etc.)
COLS = [
    "domain", "source_date", "first_seen_at", "last_seen_at", "status",
    "resolved_ip", "country_code", "website_url", "location", "attempt_count",
    "last_checked_at", "next_check_at", "last_http_status", "last_error",
    "classification_reason", "classified_at", "established", "is_template",
    "score", "score_category", "redirected_to", "redirect_domain", "phone",
    "email", "ecom_only", "human_reviewed", "human_review_notes",
]

import libsql_experimental as libsql

print(f"[migrate] Source: {SOURCE}")
print(f"[migrate] Target: {TURSO_URL}")

# Init remote schema (creates all tables + runs column migrations)
import domain_store
os.environ["DOMAIN_DB_PATH"] = TURSO_URL
domain_store.DB_PATH = TURSO_URL
domain_store.init_db()
print("[migrate] Remote schema initialised")

# Read all rows from local SQLite
local = sqlite3.connect(SOURCE)
local.row_factory = sqlite3.Row
rows = local.execute("SELECT * FROM domains").fetchall()
print(f"[migrate] {len(rows)} rows to push")

remote = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)

ph = ",".join("?" * len(COLS))
cols_str = ",".join(COLS)
sql = f"INSERT OR IGNORE INTO domains ({cols_str}) VALUES ({ph})"

BATCH = 100
pushed = 0
for i in range(0, len(rows), BATCH):
    batch = rows[i : i + BATCH]
    for row in batch:
        vals = [row[c] if c in row.keys() else None for c in COLS]
        remote.execute(sql, tuple(vals))
    remote.commit()
    pushed += len(batch)
    print(f"[migrate] {pushed}/{len(rows)} rows pushed", flush=True)

print(f"[migrate] Done — {pushed} domains in Turso.")

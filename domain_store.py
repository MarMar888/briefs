"""
SQLite-backed domain queue. Tracks state from discovery through classification.
"""

import sqlite3
import os
from contextlib import closing
from datetime import datetime

DB_PATH = os.environ.get("DOMAIN_DB_PATH") or os.path.join(os.path.dirname(__file__), "domain_leads.sqlite3")

TERMINAL_STATUSES = {"matched", "not_outdoor", "non_us"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain                TEXT PRIMARY KEY,
    source_date           TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'new',
    resolved_ip           TEXT,
    country_code          TEXT,
    website_url           TEXT,
    location              TEXT,
    attempt_count         INTEGER NOT NULL DEFAULT 0,
    last_checked_at       TEXT,
    next_check_at         TEXT,
    last_http_status      INTEGER,
    last_error            TEXT,
    classification_reason TEXT,
    classified_at         TEXT,
    established           TEXT,
    is_template           INTEGER NOT NULL DEFAULT 0,
    score                 INTEGER,
    score_category        TEXT,
    redirected_to         TEXT,
    redirect_domain       TEXT,
    phone                 TEXT,
    email                 TEXT,
    ecom_only             INTEGER NOT NULL DEFAULT 0,
    human_reviewed        INTEGER NOT NULL DEFAULT 0,
    human_review_notes    TEXT
)
"""

_MATCHED_VIEW = """
CREATE VIEW IF NOT EXISTS matched_domains AS
SELECT
    domain,
    website_url,
    location,
    established,
    is_template,
    ecom_only,
    human_reviewed,
    human_review_notes,
    score,
    score_category,
    redirected_to,
    redirect_domain,
    phone,
    email,
    classification_reason  AS reason,
    classified_at,
    source_date
FROM domains
WHERE status = 'matched'
ORDER BY ecom_only ASC, score DESC, is_template ASC, classified_at DESC
"""

_MIGRATIONS = [
    "ALTER TABLE domains ADD COLUMN location TEXT",
    "ALTER TABLE domains ADD COLUMN established TEXT",
    "ALTER TABLE domains ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN score INTEGER",
    "ALTER TABLE domains ADD COLUMN score_category TEXT",
    "ALTER TABLE domains ADD COLUMN redirected_to TEXT",
    "ALTER TABLE domains ADD COLUMN redirect_domain TEXT",
    "ALTER TABLE domains ADD COLUMN phone TEXT",
    "ALTER TABLE domains ADD COLUMN email TEXT",
    "ALTER TABLE domains ADD COLUMN ecom_only INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN human_reviewed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN human_review_notes TEXT",
]


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(_SCHEMA)
        # Run any migrations that haven't been applied yet
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column/object already exists
        conn.execute("DROP VIEW IF EXISTS matched_domains")
        conn.execute(_MATCHED_VIEW)
        conn.commit()


def upsert_new(domains: list[str], source_date: str) -> int:
    """Insert domains that don't exist yet. Returns count inserted."""
    now = datetime.utcnow().isoformat()
    inserted = 0
    with closing(_db()) as conn:
        with conn:
            for domain in domains:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO domains "
                    "(domain, source_date, first_seen_at, last_seen_at, status) "
                    "VALUES (?, ?, ?, ?, 'new')",
                    (domain, source_date, now, now),
                )
                inserted += cur.rowcount
    return inserted


def get_due(statuses: list[str]) -> list[dict]:
    """Return domains with given statuses that are due for processing."""
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(statuses))
    with closing(_db()) as conn:
        rows = conn.execute(
            f"SELECT * FROM domains WHERE status IN ({ph}) "
            "AND (next_check_at IS NULL OR next_check_at <= ?)",
            (*statuses, now),
        ).fetchall()
    return [dict(r) for r in rows]


def get_matched() -> list[dict]:
    """Return all matched domains from the view."""
    with closing(_db()) as conn:
        rows = conn.execute("SELECT * FROM matched_domains").fetchall()
    return [dict(r) for r in rows]


def update_domain(domain: str, **fields) -> None:
    if not fields:
        return
    fields["last_seen_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with closing(_db()) as conn:
        with conn:
            conn.execute(
                f"UPDATE domains SET {set_clause} WHERE domain = ?",
                [*fields.values(), domain],
            )

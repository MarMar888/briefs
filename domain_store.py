import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta

DB_PATH = (
    os.environ.get("DOMAIN_DB_PATH")
    or os.environ.get("TURSO_DB_URL")
    or os.path.join(os.path.dirname(__file__), "domain_leads.sqlite3")
)

TERMINAL_STATUSES = {"matched", "not_outdoor", "non_us", "expired", "audit_rejected"}
TRACKING_DAYS = int(os.environ.get("DOMAIN_TRACKING_DAYS", "180"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain                TEXT PRIMARY KEY,
    source_date           TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    expires_at            TEXT,
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
    email_sent_at         TEXT,
    human_reviewed        INTEGER NOT NULL DEFAULT 0,
    human_verdict         TEXT,
    human_review_notes    TEXT,
    random_sample         INTEGER NOT NULL DEFAULT 0,
    -- deep-search audit enrichments (written by enricher.py)
    owner_name            TEXT,
    full_address          TEXT,
    enriched_at           TEXT,
    starred               INTEGER NOT NULL DEFAULT 0,
    business_summary      TEXT,
    business_size         TEXT,
    employee_estimate     TEXT,
    location_count        TEXT,
    entity_type           TEXT,
    social_links          TEXT,
    side_project          INTEGER NOT NULL DEFAULT 0,
    longevity             TEXT,
    audit_notes           TEXT,
    audit_verdict         TEXT
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
    email_sent_at,
    human_reviewed,
    human_verdict,
    human_review_notes,
    score,
    score_category,
    redirected_to,
    redirect_domain,
    phone,
    email,
    owner_name,
    full_address,
    enriched_at,
    business_summary,
    business_size,
    employee_estimate,
    location_count,
    entity_type,
    social_links,
    side_project,
    longevity,
    audit_notes,
    audit_verdict,
    classification_reason  AS reason,
    classified_at,
    source_date,
    random_sample
FROM domains
WHERE status = 'matched'
ORDER BY ecom_only ASC, score DESC, is_template ASC, classified_at DESC
"""

_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    source              TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    -- ingestion funnel
    downloaded          INTEGER,
    tld_filtered        INTEGER,
    keyword_filtered    INTEGER,
    random_inserted     INTEGER,
    inserted            INTEGER,
    -- geo phase
    geo_us              INTEGER,
    geo_non_us          INTEGER,
    geo_failed          INTEGER,
    -- site phase totals
    site_processed      INTEGER,
    matched             INTEGER,
    site_not_outdoor    INTEGER,
    site_pending_retry  INTEGER,
    -- random sample effectiveness (subset of site phase)
    random_processed    INTEGER,
    random_matched      INTEGER,
    -- keyword match effectiveness (subset of site phase)
    keyword_processed   INTEGER,
    keyword_matched     INTEGER,
    -- housekeeping
    expired             INTEGER,
    error               TEXT
)
"""

_RUNS_MIGRATIONS = [
    "ALTER TABLE pipeline_runs ADD COLUMN tld_filtered INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN keyword_filtered INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN random_inserted INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN geo_us INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN geo_non_us INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN geo_failed INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN site_processed INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN site_not_outdoor INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN site_pending_retry INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN random_processed INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN random_matched INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN keyword_processed INTEGER",
    "ALTER TABLE pipeline_runs ADD COLUMN keyword_matched INTEGER",
]

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
    "ALTER TABLE domains ADD COLUMN expires_at TEXT",
    "ALTER TABLE domains ADD COLUMN email_sent_at TEXT",
    "ALTER TABLE domains ADD COLUMN human_reviewed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN human_verdict TEXT",
    "ALTER TABLE domains ADD COLUMN human_review_notes TEXT",
    "ALTER TABLE domains ADD COLUMN owner_name TEXT",
    "ALTER TABLE domains ADD COLUMN full_address TEXT",
    "ALTER TABLE domains ADD COLUMN enriched_at TEXT",
    "ALTER TABLE domains ADD COLUMN random_sample INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN starred INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN business_summary TEXT",
    "ALTER TABLE domains ADD COLUMN business_size TEXT",
    "ALTER TABLE domains ADD COLUMN employee_estimate TEXT",
    "ALTER TABLE domains ADD COLUMN location_count TEXT",
    "ALTER TABLE domains ADD COLUMN entity_type TEXT",
    "ALTER TABLE domains ADD COLUMN social_links TEXT",
    "ALTER TABLE domains ADD COLUMN side_project INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN longevity TEXT",
    "ALTER TABLE domains ADD COLUMN audit_notes TEXT",
    "ALTER TABLE domains ADD COLUMN audit_verdict TEXT",
]


def _db():
    if DB_PATH.startswith("libsql://") or DB_PATH.startswith("https://"):
        try:
            import libsql_experimental as libsql
        except ImportError as exc:
            raise RuntimeError(
                "libsql-experimental is required when DOMAIN_DB_PATH/TURSO_DB_URL points to Turso"
            ) from exc

        conn = libsql.connect(DB_PATH, auth_token=os.environ.get("TURSO_AUTH_TOKEN"))
    else:
        conn = sqlite3.connect(DB_PATH)

    if hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(_SCHEMA)
        conn.execute(_RUNS_SCHEMA)
        for sql in _MIGRATIONS + _RUNS_MIGRATIONS:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column/object already exists
        conn.execute("DROP VIEW IF EXISTS matched_domains")
        conn.execute(_MATCHED_VIEW)
        conn.commit()


def upsert_new(domains: list[str], source_date: str, random_sample: bool = False) -> int:
    """Insert domains that don't exist yet. Returns count inserted."""
    now_dt = datetime.utcnow()
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=TRACKING_DAYS)).isoformat()
    rs_flag = 1 if random_sample else 0
    inserted = 0
    with closing(_db()) as conn:
        for domain in domains:
            cur = conn.execute(
                "INSERT OR IGNORE INTO domains "
                "(domain, source_date, first_seen_at, last_seen_at, expires_at, status, random_sample) "
                "VALUES (?, ?, ?, ?, ?, 'new', ?)",
                (domain, source_date, now, now, expires_at, rs_flag),
            )
            inserted += cur.rowcount
        conn.commit()
    return inserted


def _rows_to_dicts(cursor) -> list[dict]:
    rows = cursor.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [dict(r) for r in rows]
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def get_due(statuses: list[str]) -> list[dict]:
    """Return domains with given statuses that are due for processing."""
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(statuses))
    with closing(_db()) as conn:
        cursor = conn.execute(
            f"SELECT * FROM domains WHERE status IN ({ph}) "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "AND (next_check_at IS NULL OR next_check_at <= ?)",
            (*statuses, now, now),
        )
        return _rows_to_dicts(cursor)


def expire_stale() -> int:
    """Expire non-terminal domains whose 180-day tracking window has elapsed."""
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(TERMINAL_STATUSES))
    with closing(_db()) as conn:
        cur = conn.execute(
            f"UPDATE domains SET status = 'expired', last_seen_at = ? "
            f"WHERE expires_at IS NOT NULL AND expires_at <= ? AND status NOT IN ({ph})",
            (now, now, *TERMINAL_STATUSES),
        )
        conn.commit()
        return cur.rowcount


def requeue_rescrapes() -> int:
    """Move matched/not_outdoor domains back to site_pending when their rescrape date is due.
    Skips human-reviewed domains to preserve manual verdicts."""
    now = datetime.utcnow().isoformat()
    new_expires = (datetime.utcnow() + timedelta(days=TRACKING_DAYS)).isoformat()
    with closing(_db()) as conn:
        cur = conn.execute(
            "UPDATE domains SET status = 'site_pending', email_sent_at = NULL, "
            "enriched_at = NULL, expires_at = ? "
            "WHERE status IN ('matched', 'not_outdoor') "
            "AND next_check_at IS NOT NULL AND next_check_at <= ? "
            "AND human_reviewed = 0",
            (new_expires, now),
        )
        conn.commit()
        return cur.rowcount


def get_unenriched_matches(limit: int = 0) -> list[dict]:
    """Return matched domains that have not yet been enriched."""
    with closing(_db()) as conn:
        sql = "SELECT * FROM domains WHERE status = 'matched' AND enriched_at IS NULL ORDER BY score DESC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        cursor = conn.execute(sql)
        return _rows_to_dicts(cursor)


def get_unalerted_matches() -> list[dict]:
    """Return matched domains that are ready to alert.

    Only audited leads are eligible: the deep-search audit is the second-stage
    filter, and leads it disqualifies are demoted out of the matched view before
    this runs. Requiring enriched_at IS NOT NULL ensures we never alert a lead
    that has not yet cleared the audit.
    """
    with closing(_db()) as conn:
        cursor = conn.execute(
            "SELECT * FROM matched_domains "
            "WHERE email_sent_at IS NULL AND enriched_at IS NOT NULL "
            "ORDER BY classified_at DESC"
        )
        return _rows_to_dicts(cursor)


def mark_alert_sent(domains: list[str]) -> None:
    if not domains:
        return
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(domains))
    with closing(_db()) as conn:
        conn.execute(
            f"UPDATE domains SET email_sent_at = ?, last_seen_at = ? WHERE domain IN ({ph})",
            (now, now, *domains),
        )
        conn.commit()


def get_pending() -> list[dict]:
    """Return active domains still waiting for DNS/site readiness."""
    with closing(_db()) as conn:
        cursor = conn.execute(
            "SELECT * FROM domains "
            "WHERE status IN ('new', 'geo_pending', 'site_pending') "
            "ORDER BY expires_at ASC, next_check_at ASC"
        )
        return _rows_to_dicts(cursor)


def review_domain(domain: str, verdict: str, notes: str = "") -> None:
    if verdict not in {"approved", "rejected"}:
        raise ValueError("verdict must be 'approved' or 'rejected'")
    update_domain(domain, human_reviewed=1, human_verdict=verdict, human_review_notes=notes)


def get_matched() -> list[dict]:
    """Return all matched domains from the view."""
    with closing(_db()) as conn:
        cursor = conn.execute("SELECT * FROM matched_domains")
        return _rows_to_dicts(cursor)


def _execute_with_retry(fn, retries: int = 3, backoff: float = 1.0):
    """Run fn(), retrying on transient Hrana/libSQL HTTP errors."""
    for attempt in range(retries):
        try:
            return fn()
        except ValueError as exc:
            if "Hrana" not in str(exc) or attempt == retries - 1:
                raise
            time.sleep(backoff * (2 ** attempt))


def update_domain(domain: str, **fields) -> None:
    if not fields:
        return
    fields["last_seen_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)

    def _run():
        with closing(_db()) as conn:
            conn.execute(
                f"UPDATE domains SET {set_clause} WHERE domain = ?",
                (*fields.values(), domain),
            )
            conn.commit()

    _execute_with_retry(_run)


def batch_update_domains(updates: list, chunk_size: int = 100) -> None:
    """Write a list of domain updates in batches, one commit per chunk.

    Each item in `updates` must be a dict with a ``domain`` key plus the
    fields to set.  All updates in a chunk share one connection and one
    commit, cutting Turso round-trips from O(n) to O(n/chunk_size).
    """
    now = datetime.utcnow().isoformat()
    for i in range(0, len(updates), chunk_size):
        chunk = updates[i : i + chunk_size]

        def _run(chunk=chunk):
            with closing(_db()) as conn:
                for item in chunk:
                    fields = {k: v for k, v in item.items() if k != "domain"}
                    if not fields:
                        continue
                    fields["last_seen_at"] = now
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    conn.execute(
                        f"UPDATE domains SET {set_clause} WHERE domain = ?",
                        (*fields.values(), item["domain"]),
                    )
                conn.commit()

        _execute_with_retry(_run)


def start_run(source: str) -> int:
    """Record the start of a pipeline run. Returns the new run id."""
    now = datetime.utcnow().isoformat()
    with closing(_db()) as conn:
        cur = conn.execute(
            "INSERT INTO pipeline_runs (started_at, source, status) VALUES (?, ?, 'running')",
            (now, source),
        )
        conn.commit()
        return cur.lastrowid


def finish_run(
    run_id: int,
    *,
    matched: int,
    downloaded: int,
    inserted: int,
    expired: int,
    error: str | None = None,
    tld_filtered: int | None = None,
    keyword_filtered: int | None = None,
    random_inserted: int | None = None,
    geo_us: int | None = None,
    geo_non_us: int | None = None,
    geo_failed: int | None = None,
    site_processed: int | None = None,
    site_not_outdoor: int | None = None,
    site_pending_retry: int | None = None,
    random_processed: int | None = None,
    random_matched: int | None = None,
    keyword_processed: int | None = None,
    keyword_matched: int | None = None,
) -> None:
    now = datetime.utcnow().isoformat()
    status = "error" if error else "done"
    with closing(_db()) as conn:
        conn.execute(
            "UPDATE pipeline_runs "
            "SET finished_at=?, status=?, downloaded=?, tld_filtered=?, keyword_filtered=?, "
            "random_inserted=?, inserted=?, geo_us=?, geo_non_us=?, geo_failed=?, "
            "site_processed=?, matched=?, site_not_outdoor=?, site_pending_retry=?, "
            "random_processed=?, random_matched=?, keyword_processed=?, keyword_matched=?, "
            "expired=?, error=? "
            "WHERE id=?",
            (
                now, status,
                downloaded, tld_filtered, keyword_filtered,
                random_inserted, inserted,
                geo_us, geo_non_us, geo_failed,
                site_processed, matched, site_not_outdoor, site_pending_retry,
                random_processed, random_matched, keyword_processed, keyword_matched,
                expired, error,
                run_id,
            ),
        )
        conn.commit()


def get_runs(limit: int = 100) -> list[dict]:
    with closing(_db()) as conn:
        cursor = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return _rows_to_dicts(cursor)

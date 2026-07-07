import functools
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta

from timeutil import utcnow
from version import get_version

DB_PATH = (
    os.environ.get("DOMAIN_DB_PATH")
    or os.environ.get("TURSO_DB_URL")
    or os.path.join(os.path.dirname(__file__), "domain_leads.sqlite3")
)

TERMINAL_STATUSES = {"matched", "not_outdoor", "non_us", "expired"}
TRACKING_DAYS = int(os.environ.get("DOMAIN_TRACKING_DAYS", "180"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain                TEXT PRIMARY KEY,
    source_date           TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    expires_at            TEXT,
    status                TEXT NOT NULL DEFAULT 'new',
    industry              TEXT NOT NULL DEFAULT 'outdoor',
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
    audit_verdict         TEXT,
    -- pipeline provenance: which pipeline version touched the domain at each stage
    found_version         TEXT,
    classified_version    TEXT,
    enriched_version      TEXT
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
    found_version,
    classified_version,
    enriched_version,
    classification_reason  AS reason,
    classified_at,
    source_date,
    random_sample,
    industry
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
    pipeline_version    TEXT,
    industry            TEXT DEFAULT 'outdoor',
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
    "ALTER TABLE pipeline_runs ADD COLUMN pipeline_version TEXT",
    "ALTER TABLE pipeline_runs ADD COLUMN industry TEXT DEFAULT 'outdoor'",
    # Liveness telemetry: the phase the run is in and the last time it made forward
    # progress. Lets you answer "is run N alive or hung, and where?" with one query
    # (SELECT id, industry, current_phase, last_progress_at FROM pipeline_runs WHERE
    # status='running') instead of reverse-engineering it from domains.last_checked_at.
    "ALTER TABLE pipeline_runs ADD COLUMN current_phase TEXT",
    "ALTER TABLE pipeline_runs ADD COLUMN last_progress_at TEXT",
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
    "ALTER TABLE domains ADD COLUMN found_version TEXT",
    "ALTER TABLE domains ADD COLUMN classified_version TEXT",
    "ALTER TABLE domains ADD COLUMN enriched_version TEXT",
    "ALTER TABLE domains ADD COLUMN industry TEXT NOT NULL DEFAULT 'outdoor'",
    # minnesota vertical: per-crawl "basics" stored on EVERY reachable row (matched or
    # rejected) to build a dataset, plus the gate's service-area tier and queue priority.
    "ALTER TABLE domains ADD COLUMN crawl_title TEXT",
    "ALTER TABLE domains ADD COLUMN content_snippet TEXT",
    "ALTER TABLE domains ADD COLUMN detected_zips TEXT",
    "ALTER TABLE domains ADD COLUMN detected_state TEXT",
    "ALTER TABLE domains ADD COLUMN is_reachable INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN mn_signal INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE domains ADD COLUMN service_tier TEXT",
    "ALTER TABLE domains ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
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


# Turso is a remote HTTP database; transient "connection reset" / 5xx / timeout blips
# happen and must NOT abort a whole run — that throws away the CI minutes already spent
# and (before this) even crashed the error handler. Retry with capped exponential
# backoff. Each retry reopens a fresh connection (every write helper opens its own
# `with closing(_db())`), so a reset connection is discarded, not reused.
_DB_RETRY_ATTEMPTS = int(os.environ.get("DB_RETRY_ATTEMPTS", "6"))
_DB_RETRY_MAX_BACKOFF = float(os.environ.get("DB_RETRY_MAX_BACKOFF_SECONDS", "8"))
_TRANSIENT_DB_MARKERS = (
    "hrana", "connection reset", "connection error", "http error", "stream closed",
    "timed out", "timeout", "os error 104", "502", "503", "504",
)


def _is_transient_db_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_DB_MARKERS)


# A stalled Turso connection blocks in a socket read with NO exception and NO timeout of
# its own — so #24's transient-retry (which only fires on errors that RAISE) never sees it.
# That silent hang is what pinned run #84 for 3h+ on a single geo-phase write. We cap every
# DB op with a wall-clock timeout in a daemon thread: on stall we raise TimeoutError, whose
# message ("timed out") is already a transient marker, so it flows straight back into the
# retry loop below and reopens a fresh connection. The abandoned daemon thread dies with the
# process. 0 disables the cap (local debugging).
_DB_OP_TIMEOUT = float(os.environ.get("DB_OP_TIMEOUT_SECONDS", "45"))


def _call_with_timeout(fn, timeout: float):
    if timeout <= 0:
        return fn()
    box: dict = {}

    def _runner():
        try:
            box["value"] = fn()
        except Exception as exc:  # surface in the calling thread
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(
            f"DB op timed out after {timeout:.0f}s — stalled connection abandoned"
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _execute_with_retry(fn, retries: int | None = None, backoff: float = 1.0):
    """Run fn(), retrying transient Hrana/libSQL connection blips with capped backoff.
    Each attempt is wall-clock bounded (see _call_with_timeout) so a silent stall becomes
    a retryable error instead of hanging the run forever."""
    attempts = retries or _DB_RETRY_ATTEMPTS
    for attempt in range(attempts):
        try:
            result = _call_with_timeout(fn, _DB_OP_TIMEOUT)
            _touch()  # a completed DB op is forward progress — feed the watchdog
            return result
        except Exception as exc:
            if not _is_transient_db_error(exc) or attempt == attempts - 1:
                raise
            wait = min(backoff * (2 ** attempt), _DB_RETRY_MAX_BACKOFF)
            print(f"[domain_store] transient DB error (attempt {attempt+1}/{attempts}): "
                  f"{exc} — retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)


def _resilient(fn):
    """Decorator: run a DB helper through the transient-retry loop. Applied to every
    function that opens a connection so a single Turso connection blip can never kill
    the pipeline — reads and writes alike. (batch_update_domains is the deliberate
    exception: it retries per-chunk so a mid-batch blip doesn't re-commit prior chunks.)"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return _execute_with_retry(lambda: fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# Liveness: heartbeat + watchdog
#
# The pipeline used to go silent for hours with no way to tell "hung vs slow" short of
# querying domains.last_checked_at by hand. Now every phase and every DB op marks progress
# on an in-process monotonic clock; a watchdog thread aborts the process if that clock goes
# stale, and heartbeat() mirrors the current phase into pipeline_runs so liveness is also
# queryable from the DB.
# ---------------------------------------------------------------------------
_hb_lock = threading.Lock()
_last_activity = time.monotonic()
_current_phase = "init"
_current_run_id: int | None = None

_watchdog_stop: threading.Event | None = None
_WATCHDOG_STALL_LIMIT = float(os.environ.get("WATCHDOG_STALL_MINUTES", "20")) * 60
_WATCHDOG_POLL_SECONDS = 30.0

# 2026-07-06: the construction backfill leg went silent for 88min inside a
# batch_update_domains write and NEITHER the per-op _call_with_timeout thread NOR this
# in-process watchdog fired. Root cause: libsql_experimental is a PyO3/Rust extension; a
# stalled connection blocks inside it without releasing the GIL, which starves every other
# Python thread in the process — including the ones this file spins up to detect exactly
# that. A same-process thread can't watch a process that won't schedule threads. So we also
# drop a plain timestamp file on every touch, cheap enough to call constantly, that an
# external OS-level watcher (outside this interpreter, immune to the GIL) can poll and
# kill -9 the process on. Set by the CI step; a no-op locally when unset.
_HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE")


def _touch() -> None:
    """Mark forward progress on the in-process liveness clock (what the watchdog reads)."""
    global _last_activity
    with _hb_lock:
        _last_activity = time.monotonic()
    if _HEARTBEAT_FILE:
        try:
            with open(_HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass


def seconds_since_activity() -> float:
    with _hb_lock:
        return time.monotonic() - _last_activity


def heartbeat(phase: str | None = None, run_id: int | None = None) -> None:
    """Record forward progress. Bumps the in-process clock and, when a run is known,
    best-effort writes current_phase + last_progress_at to pipeline_runs so run liveness
    is queryable without log access. Never raises — telemetry must not crash a run."""
    global _current_phase, _current_run_id
    _touch()
    if phase is not None:
        _current_phase = phase
    if run_id is not None:
        _current_run_id = run_id
    rid = _current_run_id
    if rid is None:
        return
    now = utcnow().isoformat()
    ph = _current_phase

    def _run():
        with closing(_db()) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET current_phase=?, last_progress_at=? WHERE id=?",
                (ph, now, rid),
            )
            conn.commit()

    try:
        _execute_with_retry(_run)
    except Exception as exc:
        print(f"[domain_store] heartbeat write failed (non-fatal): {exc}", flush=True)


def _mark_run_stalled(run_id: int, phase: str) -> None:
    now = utcnow().isoformat()
    with closing(_db()) as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status='error', finished_at=?, error=?, "
            "current_phase=?, last_progress_at=? WHERE id=?",
            (now, f"watchdog: stalled in phase '{phase}'", phase, now, run_id),
        )
        conn.commit()


def start_watchdog(run_id: int | None = None) -> None:
    """Start a daemon that aborts the process if no progress is made for
    WATCHDOG_STALL_MINUTES. This is the backstop that turns a silent hang into a fast,
    clean CI failure instead of burning to the job's hard timeout. Set the env var to 0
    to disable (local runs)."""
    global _watchdog_stop, _current_run_id
    if _WATCHDOG_STALL_LIMIT <= 0:
        return
    if run_id is not None:
        _current_run_id = run_id
    _touch()
    _watchdog_stop = threading.Event()
    stop = _watchdog_stop

    def _loop():
        while not stop.wait(_WATCHDOG_POLL_SECONDS):
            idle = seconds_since_activity()
            if idle >= _WATCHDOG_STALL_LIMIT:
                phase = _current_phase
                print(
                    f"[watchdog] no progress for {idle/60:.1f} min in phase '{phase}' — "
                    f"aborting (stall limit {_WATCHDOG_STALL_LIMIT/60:.0f} min). A DB op or "
                    f"network call hung; failing fast instead of burning the CI budget.",
                    flush=True,
                )
                rid = _current_run_id
                if rid is not None:
                    try:
                        _call_with_timeout(lambda: _mark_run_stalled(rid, phase), 20)
                    except Exception:
                        pass
                os._exit(1)

    threading.Thread(target=_loop, daemon=True, name="pipeline-watchdog").start()


def stop_watchdog() -> None:
    if _watchdog_stop is not None:
        _watchdog_stop.set()


@_resilient
def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(_SCHEMA)
        conn.execute(_RUNS_SCHEMA)
        for sql in _MIGRATIONS + _RUNS_MIGRATIONS:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column/object already exists
        # Index for get_due: serves WHERE (industry, status) + ORDER BY (priority DESC,
        # first_seen_at) so a single-status query walks ~LIMIT rows instead of scanning
        # and temp-b-tree-sorting the whole backlog (the MN firehose grew `new` to ~480k).
        # The priority DESC direction must be baked into the index or the ORDER BY still
        # forces a sort. On a huge pre-existing table the build can exceed the DB-op
        # timeout — prod was indexed out-of-band once; on fresh/empty DBs it's instant.
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_domains_due2 "
                "ON domains(industry, status, priority DESC, first_seen_at)"
            )
            conn.execute("DROP INDEX IF EXISTS idx_domains_due")  # superseded ASC-priority index
        except Exception:
            pass
        conn.execute("DROP VIEW IF EXISTS matched_domains")
        conn.execute(_MATCHED_VIEW)
        # Label, don't kill: recover any leads stranded in the retired terminal
        # 'audit_rejected' state back into matched with a disqualified verdict, so
        # the audit filter suppresses them instead of deleting them. Idempotent.
        try:
            conn.execute(
                "UPDATE domains SET status = 'matched', audit_verdict = 'disqualified' "
                "WHERE status = 'audit_rejected'"
            )
        except Exception:
            pass
        conn.commit()


def upsert_new(domains: list[str], source_date: str, random_sample: bool = False,
               industry: str = "outdoor") -> int:
    """Insert domains that don't exist yet. Returns count inserted.

    `industry` stamps the vertical that discovered the domain; it is immutable for
    the life of the row (classify/enrich never change it), so a row is only ever
    advanced by its own vertical's pipeline.
    """
    if not domains:
        return 0
    now_dt = utcnow()
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=TRACKING_DAYS)).isoformat()
    rs_flag = 1 if random_sample else 0
    version = get_version()
    # Queue prioritization (minnesota only): tag rows whose NAME hints Twin Cities so
    # get_due surfaces them first. Reorder-only; non-minnesota verticals stay priority 0.
    prioritize = industry == "minnesota"
    name_priority = None
    if prioritize:
        from geo_gate import name_priority

    # Batch the insert into multi-row statements, one bounded round-trip per chunk. The
    # firehose (minnesota, name-filter off, no domain-limit) hands this ~100k domains; the
    # old row-at-a-time loop was ~100k serial Turso round-trips in a single op, which blew
    # past the per-op timeout and failed the whole import. 9 bound params/row → 100 rows is
    # 900 params, safely under SQLite's conservative 999-variable ceiling.
    UPSERT_CHUNK = 100
    row_sql = "(?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)"
    inserted = 0
    for start in range(0, len(domains), UPSERT_CHUNK):
        chunk = domains[start : start + UPSERT_CHUNK]
        params: list = []
        for domain in chunk:
            pr = name_priority(domain) if prioritize else 0
            params.extend((domain, source_date, now, now, expires_at, rs_flag, version, industry, pr))
        values_clause = ", ".join(row_sql for _ in chunk)
        sql = (
            "INSERT OR IGNORE INTO domains "
            "(domain, source_date, first_seen_at, last_seen_at, expires_at, status, "
            "random_sample, found_version, industry, priority) VALUES " + values_clause
        )
        # libsql (Turso) requires a tuple, not a list, for bound params — sqlite3 accepts
        # either, which is why this only shows up against the real DB.
        param_tuple = tuple(params)

        def _run(sql=sql, params=param_tuple):
            with closing(_db()) as conn:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount

        inserted += _execute_with_retry(_run) or 0
        heartbeat()  # each committed chunk is progress — keep the watchdog satisfied
    return inserted


def _rows_to_dicts(cursor) -> list[dict]:
    rows = cursor.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [dict(r) for r in rows]
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


@_resilient
def get_due(statuses: list[str], industry: str | None = None, limit: int = 0) -> list[dict]:
    """Return domains with given statuses that are due for processing.

    When `industry` is given, only that vertical's rows are returned so a run never
    advances another vertical's leads through its own (wrong) prompt/keywords.

    `limit` (>0) caps how many rows come back — and, crucially, how many cross the wire
    from Turso. The caller already discards everything past its geo/site cap, so pulling
    the whole backlog was pure waste: the minnesota firehose grew `new` to ~480k rows and
    an unbounded get_due tried to stream them all, blowing past the 45s DB-op timeout.
    With a limit we query each status separately so the
    (industry, status, priority DESC, first_seen_at) index serves ORDER BY + LIMIT by
    walking ~`limit` rows — a two-value `status IN (...)` would instead force a temp-b-tree
    sort of the entire backlog. Results are merged and re-sorted so the caller still sees a
    single priority-then-oldest ordering.
    """
    now = utcnow().isoformat()

    def _query(status_subset: list[str], lim: int) -> list[dict]:
        ph = ",".join("?" * len(status_subset))
        sql = (
            f"SELECT * FROM domains WHERE status IN ({ph}) "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "AND (next_check_at IS NULL OR next_check_at <= ?)"
        )
        params: list = [*status_subset, now, now]
        if industry is not None:
            sql += " AND industry = ?"
            params.append(industry)
        # Priority first (minnesota queue prioritization; 0 for every other vertical, so
        # this is a no-op there), then oldest-first for a stable, fair drain order.
        sql += " ORDER BY priority DESC, first_seen_at"
        if lim and lim > 0:
            sql += " LIMIT ?"
            params.append(lim)
        with closing(_db()) as conn:
            return _rows_to_dicts(conn.execute(sql, tuple(params)))

    if limit and limit > 0 and len(statuses) > 1:
        merged: list[dict] = []
        for status in statuses:
            merged.extend(_query([status], limit))
        merged.sort(key=lambda r: (-(r.get("priority") or 0), r.get("first_seen_at") or ""))
        return merged[:limit]
    return _query(statuses, limit)


@_resilient
def expire_stale() -> int:
    """Expire non-terminal domains whose 180-day tracking window has elapsed."""
    now = utcnow().isoformat()
    ph = ",".join("?" * len(TERMINAL_STATUSES))
    with closing(_db()) as conn:
        cur = conn.execute(
            f"UPDATE domains SET status = 'expired', last_seen_at = ? "
            f"WHERE expires_at IS NOT NULL AND expires_at <= ? AND status NOT IN ({ph})",
            (now, now, *TERMINAL_STATUSES),
        )
        conn.commit()
        return cur.rowcount


@_resilient
def requeue_rescrapes() -> int:
    """Move matched/not_outdoor domains back to site_pending when their rescrape date is due.
    Skips human-reviewed domains to preserve manual verdicts."""
    now = utcnow().isoformat()
    new_expires = (utcnow() + timedelta(days=TRACKING_DAYS)).isoformat()
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


@_resilient
def get_unenriched_matches(limit: int = 0, industry: str | None = None) -> list[dict]:
    """Return matched domains that have not yet been enriched."""
    with closing(_db()) as conn:
        sql = "SELECT * FROM domains WHERE status = 'matched' AND enriched_at IS NULL"
        params: list = []
        if industry is not None:
            sql += " AND industry = ?"
            params.append(industry)
        sql += " ORDER BY score DESC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        cursor = conn.execute(sql, tuple(params))
        return _rows_to_dicts(cursor)


@_resilient
def get_matches_to_reaudit(limit: int = 0, stale_only: bool = True,
                           industry: str | None = None) -> list[dict]:
    """Return matched domains to re-run through the deep-search audit.

    stale_only=True (default) returns only leads whose enriched_version is not the
    current semver — i.e. those not yet caught up to the running pipeline version
    (including never-audited NULLs). This makes a re-audit idempotent: re-running
    skips leads already on the current version.
    stale_only=False returns every matched lead.
    """
    with closing(_db()) as conn:
        sql = "SELECT * FROM domains WHERE status = 'matched'"
        params: list = []
        if industry is not None:
            sql += " AND industry = ?"
            params.append(industry)
        if stale_only:
            semver = get_version().split("+")[0]
            sql += " AND (enriched_version IS NULL OR enriched_version NOT LIKE ?)"
            params.append(f"{semver}%")
        sql += " ORDER BY score DESC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        cursor = conn.execute(sql, tuple(params))
        return _rows_to_dicts(cursor)


@_resilient
def get_unalerted_matches(industry: str | None = None) -> list[dict]:
    """Return matched domains that are ready to alert.

    Only audited leads are eligible: the deep-search audit is the second-stage
    filter, and leads it disqualifies are demoted out of the matched view before
    this runs. Requiring enriched_at IS NOT NULL ensures we never alert a lead
    that has not yet cleared the audit. When `industry` is given, only that
    vertical's leads are returned so each vertical's digest stays separate.
    """
    sql = (
        "SELECT * FROM matched_domains "
        "WHERE email_sent_at IS NULL AND enriched_at IS NOT NULL "
        "AND audit_verdict = 'qualified'"
    )
    params: list = []
    if industry is not None:
        sql += " AND industry = ?"
        params.append(industry)
    sql += " ORDER BY classified_at DESC"
    with closing(_db()) as conn:
        cursor = conn.execute(sql, tuple(params))
        return _rows_to_dicts(cursor)


@_resilient
def mark_alert_sent(domains: list[str]) -> None:
    if not domains:
        return
    now = utcnow().isoformat()
    ph = ",".join("?" * len(domains))
    with closing(_db()) as conn:
        conn.execute(
            f"UPDATE domains SET email_sent_at = ?, last_seen_at = ? WHERE domain IN ({ph})",
            (now, now, *domains),
        )
        conn.commit()


@_resilient
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


@_resilient
def get_matched() -> list[dict]:
    """Return all matched domains from the view."""
    with closing(_db()) as conn:
        cursor = conn.execute("SELECT * FROM matched_domains")
        return _rows_to_dicts(cursor)


def update_domain(domain: str, **fields) -> None:
    if not fields:
        return
    fields["last_seen_at"] = utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)

    def _run():
        with closing(_db()) as conn:
            conn.execute(
                f"UPDATE domains SET {set_clause} WHERE domain = ?",
                (*fields.values(), domain),
            )
            conn.commit()

    _execute_with_retry(_run)


def batch_update_domains(updates: list, chunk_size: int = 100, label: str = "batch-write") -> None:
    """Write a list of domain updates in batches, one commit per chunk.

    Each item in `updates` must be a dict with a ``domain`` key plus the
    fields to set.  All updates in a chunk share one connection and one
    commit, cutting Turso round-trips from O(n) to O(n/chunk_size).

    `label` tags the timing logs so a stall here is attributable to a phase (this is the
    call that silently hung run #84 for 3h+ — it now logs each chunk and heartbeats, and
    every chunk is timeout-bounded via _execute_with_retry).
    """
    now = utcnow().isoformat()
    total = len(updates)
    if not total:
        return
    chunks = (total + chunk_size - 1) // chunk_size
    started = time.monotonic()
    for idx, i in enumerate(range(0, total, chunk_size), 1):
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
        heartbeat()  # each committed chunk is progress — keep the watchdog satisfied
    print(f"[domain_store] {label}: wrote {total} rows in {chunks} chunk(s), "
          f"{time.monotonic()-started:.1f}s", flush=True)


def start_run(source: str, industry: str = "outdoor") -> int:
    """Record the start of a pipeline run. Returns the new run id."""
    now = utcnow().isoformat()

    def _run():
        with closing(_db()) as conn:
            cur = conn.execute(
                "INSERT INTO pipeline_runs (started_at, source, status, pipeline_version, "
                "industry, current_phase, last_progress_at) "
                "VALUES (?, ?, 'running', ?, ?, 'starting', ?)",
                (now, source, get_version(), industry, now),
            )
            conn.commit()
            return cur.lastrowid

    return _execute_with_retry(_run)


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
    now = utcnow().isoformat()
    status = "error" if error else "done"
    phase = "error" if error else "done"

    def _run():
        with closing(_db()) as conn:
            conn.execute(
                "UPDATE pipeline_runs "
                "SET current_phase=?, last_progress_at=?, "
                "finished_at=?, status=?, downloaded=?, tld_filtered=?, keyword_filtered=?, "
                "random_inserted=?, inserted=?, geo_us=?, geo_non_us=?, geo_failed=?, "
                "site_processed=?, matched=?, site_not_outdoor=?, site_pending_retry=?, "
                "random_processed=?, random_matched=?, keyword_processed=?, keyword_matched=?, "
                "expired=?, error=? "
                "WHERE id=?",
                (
                    phase, now,
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

    _execute_with_retry(_run)


@_resilient
def get_runs(limit: int = 100) -> list[dict]:
    with closing(_db()) as conn:
        cursor = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return _rows_to_dicts(cursor)

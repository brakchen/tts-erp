"""PG-backed repository for analytics_sync.

Production implementation of AnalyticsRepository from domain.py.

The batch upload path runs inside a single transaction so that records,
daily page tracking, completeness flags, and cursor advances either all
commit or all roll back.

v2 semantics:
- Each record carries an expected_page_count (v1 records are treated as 1).
- A day is "complete" only when analytics_daily_pages contains every page
  in 1..expected_page_count for that (scope, storageKey, campaignId, day).
- analytics_cursors.latest_completed_day is the LAST day of a contiguous
  complete sequence starting at bootstrap_day; gaps prevent skipping.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from .domain import (
    DEFAULT_TIMEZONE,
    AcceptedRecord,
    AnalyticsRepository,
    BatchResult,
    CursorEntry,
    Record,
    RejectedRecord,
    Scope,
    StorageKey,
    compute_idempotency_key,
)


def connect() -> psycopg.Connection:
    """Open a new PG connection. Caller manages transaction."""
    url = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("ANALYTICS_SYNC_DB_URL")
    if not url:
        raise RuntimeError(
            "TTS_ERP_DB_URL (or ANALYTICS_SYNC_DB_URL) not configured; set it in .env"
        )
    return psycopg.connect(url)


class PgAnalyticsRepository(AnalyticsRepository):
    def upsert_records(
        self,
        scope: Scope,
        records: list[Record],
        request_id: str | None,
        *,
        today_in_shop_tz: date,
        bootstrap_day: date,
    ) -> BatchResult:
        accepted: list[AcceptedRecord] = []
        rejected: list[RejectedRecord] = []

        with connect() as conn:
            with conn.cursor() as cur:
                # Units/days whose completeness or cursor may need a refresh.
                modified_unit_days: set[tuple[str, str, str, str, date]] = set()

                for rec in records:
                    # Safety net: recompute canonical idempotency key.
                    expected = compute_idempotency_key(
                        seller_id=scope.seller_id,
                        advertiser_id=scope.advertiser_id,
                        storage_key=rec.storage_key,
                        campaign_id=rec.campaign_id,
                        day=rec.day,
                        page=rec.page,
                    )
                    if expected != rec.idempotency_key:
                        rejected.append(
                            RejectedRecord(
                                idempotency_key=rec.idempotency_key,
                                code="SCHEMA_INVALID",
                                message=(
                                    f"idempotencyKey mismatch: "
                                    f"client={rec.idempotency_key[:16]}… "
                                    f"server={expected[:16]}…"
                                ),
                                retryable=False,
                            )
                        )
                        continue

                    if rec.expected_page_count is None:
                        rejected.append(
                            RejectedRecord(
                                idempotency_key=rec.idempotency_key,
                                code="SCHEMA_INVALID",
                                message="expectedPageCount is required for protocolVersion 2",
                                retryable=False,
                            )
                        )
                        continue

                    unit_day = (
                        scope.seller_id,
                        scope.advertiser_id,
                        rec.storage_key.value,
                        rec.campaign_id,
                        rec.day,
                    )

                    # Cross-batch page-count conflict: once a daily unit has
                    # an expected_page_count, every record for that day must
                    # agree. Lock the row so concurrent batches serialize.
                    cur.execute(
                        """
                        SELECT expected_page_count
                        FROM analytics_daily_completeness
                        WHERE seller_id = %s AND advertiser_id = %s
                          AND storage_key = %s AND campaign_id = %s AND day = %s
                        FOR UPDATE
                        """,
                        unit_day,
                    )
                    row = cur.fetchone()
                    stored_expected = row[0] if row else None  # type: ignore[index]
                    if (
                        stored_expected is not None
                        and stored_expected != rec.expected_page_count
                    ):
                        rejected.append(
                            RejectedRecord(
                                idempotency_key=rec.idempotency_key,
                                code="PAGE_COUNT_CONFLICT",
                                message=(
                                    f"expectedPageCount conflict for "
                                    f"{rec.storage_key.value}/{rec.campaign_id}/{rec.day.isoformat()}: "
                                    f"stored={stored_expected}, received={rec.expected_page_count}"
                                ),
                                retryable=False,
                            )
                        )
                        continue

                    # Upsert the raw record.
                    cur.execute(
                        """
                        INSERT INTO analytics_records (
                            idempotency_key, source_record_id,
                            seller_id, advertiser_id, storage_key, campaign_id,
                            day, page, shop_name, expected_page_count,
                            endpoint, method, request_body, response_data,
                            source, captured_at,
                            schema_version, protocol_version,
                            request_id
                        ) VALUES (
                            %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s
                        )
                        ON CONFLICT (idempotency_key) DO NOTHING
                        RETURNING id
                        """,
                        (
                            rec.idempotency_key,
                            rec.source_record_id,
                            scope.seller_id,
                            scope.advertiser_id,
                            rec.storage_key.value,
                            rec.campaign_id,
                            rec.day,
                            rec.page,
                            scope.shop_name,
                            rec.expected_page_count,
                            rec.endpoint,
                            rec.method,
                            Jsonb(rec.request_body)
                            if rec.request_body is not None
                            else None,
                            Jsonb(rec.response),
                            rec.source,
                            rec.captured_at,
                            rec.schema_version,
                            rec.protocol_version,
                            request_id,
                        ),
                    )
                    inserted = cur.fetchone() is not None

                    # Track the page for completeness regardless of whether
                    # the raw record was inserted or a duplicate. A duplicate
                    # still means this page is durably stored.
                    if inserted:
                        accepted.append(
                            AcceptedRecord(
                                idempotency_key=rec.idempotency_key,
                                status="inserted",
                            )
                        )
                    else:
                        accepted.append(
                            AcceptedRecord(
                                idempotency_key=rec.idempotency_key,
                                status="duplicate",
                            )
                        )

                    # Upsert expected_page_count for the daily unit. The first
                    # record for a day wins; conflicts were rejected above.
                    cur.execute(
                        """
                        INSERT INTO analytics_daily_completeness (
                            seller_id, advertiser_id, storage_key, campaign_id, day,
                            expected_page_count, is_complete, completed_at, last_recomputed_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, FALSE, NULL, now())
                        ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day)
                        DO UPDATE SET
                            last_recomputed_at = now()
                        WHERE analytics_daily_completeness.expected_page_count
                              IS DISTINCT FROM EXCLUDED.expected_page_count
                        """,
                        (*unit_day, rec.expected_page_count),
                    )

                    # Mark this page as present. ON CONFLICT DO NOTHING makes
                    # duplicates harmless and lets concurrent batches race
                    # safely.
                    cur.execute(
                        """
                        INSERT INTO analytics_daily_pages (
                            seller_id, advertiser_id, storage_key, campaign_id, day, page
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (
                            seller_id, advertiser_id, storage_key, campaign_id, day, page
                        ) DO NOTHING
                        """,
                        (*unit_day, rec.page),
                    )

                    modified_unit_days.add(unit_day)

                # Recompute completeness for every touched unit/day.
                _recompute_completeness(cur, modified_unit_days)

                # Recompute cursors for every touched unit. latest_completed_day
                # is the last day of the contiguous complete prefix starting
                # at bootstrap_day; gaps cannot be skipped.
                _recompute_cursors(
                    cur,
                    modified_unit_days,
                    bootstrap_day=bootstrap_day,
                    today_in_shop_tz=today_in_shop_tz,
                    request_id=request_id,
                )

                # Upsert the shop timezone (last-write-wins) so cursor queries
                # always have a row to read.
                cur.execute(
                    """
                    INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (seller_id) DO UPDATE
                        SET advertiser_id = EXCLUDED.advertiser_id,
                            updated_at = now()
                    """,
                    (scope.seller_id, scope.advertiser_id, DEFAULT_TIMEZONE),
                )

            conn.commit()
        return BatchResult(accepted=accepted, rejected=rejected)


class IdempotencyKeyMismatch(Exception):
    """The client-sent idempotency_key disagrees with the canonical key
    the server computes. Signals a client bug or tampering."""

    def __init__(self, client_key: str, canonical_key: str) -> None:
        super().__init__(
            f"idempotency_key mismatch: client sent {client_key[:16]}…, "
            f"server computed {canonical_key[:16]}…"
        )
        self.client_key = client_key
        self.canonical_key = canonical_key


# ─── Completeness / cursor helpers ────────────────────────────────────


def _recompute_completeness(
    cur: psycopg.Cursor,
    unit_days: set[tuple[str, str, str, str, date]],
) -> None:
    """For each (scope, storageKey, campaignId, day), mark is_complete
    TRUE iff analytics_daily_pages contains exactly the pages
    1..expected_page_count."""
    for unit_day in unit_days:
        # pi-lens-ignore: python-sql-injection
        cur.execute(
            """
            SELECT expected_page_count
            FROM analytics_daily_completeness
            WHERE seller_id = %s AND advertiser_id = %s
              AND storage_key = %s AND campaign_id = %s AND day = %s
            """,
            unit_day,
        )
        row = cur.fetchone()
        if not row:
            continue
        expected = row[0]

        # A day is complete iff every page in [1, expected] is present.
        # Extra pages beyond expected (possible for v1 rows where the
        # implicit expectation is 1 but the client uploaded more pages)
        # do not affect completeness.
        cur.execute(
            """
            SELECT COUNT(DISTINCT page)
            FROM analytics_daily_pages
            WHERE seller_id = %s AND advertiser_id = %s
              AND storage_key = %s AND campaign_id = %s AND day = %s
              AND page BETWEEN 1 AND %s
            """,
            (*unit_day, expected),
        )
        row = cur.fetchone()
        if row is None:
            continue
        (pages_in_range,) = row  # type: ignore[misc]
        is_complete = pages_in_range is not None and pages_in_range == expected

        cur.execute(
            """
            UPDATE analytics_daily_completeness
            SET is_complete = %s,
                completed_at = CASE WHEN %s THEN now() ELSE NULL END,
                last_recomputed_at = now()
            WHERE seller_id = %s AND advertiser_id = %s
              AND storage_key = %s AND campaign_id = %s AND day = %s
            """,
            (is_complete, is_complete, *unit_day),
        )


def _recompute_cursors(
    cur: psycopg.Cursor,
    unit_days: set[tuple[str, str, str, str, date]],
    *,
    bootstrap_day: date,
    today_in_shop_tz: date,
    request_id: str | None,
) -> None:
    """Advance analytics_cursors to the last day of the contiguous complete
    prefix, separately per (seller, advertiser, storage_key, campaign_id).

    The contiguity chain is anchored at first_seen_day (the earliest day
    with any record for the unit), not at bootstrap_day: a client is told
    to start at bootstrap, and its first upload defines the anchor. Interior
    gaps (missing or incomplete days after the anchor) block advancement.
    The cursor never regresses: a legacy GREATEST-style value from the v1
    era is kept until contiguous completeness catches up with it.
    """
    units: set[tuple[str, str, str, str]] = {
        (sid, aid, skey, cid) for sid, aid, skey, cid, _ in unit_days
    }

    for sid, aid, skey, cid in units:
        # Anchor: earliest day with any completeness row for this unit.
        cur.execute(
            """
            SELECT MIN(day)
            FROM analytics_daily_completeness
            WHERE seller_id = %s AND advertiser_id = %s
              AND storage_key = %s AND campaign_id = %s
            """,
            (sid, aid, skey, cid),
        )
        row = cur.fetchone()
        first_seen: date | None = row[0] if row else None
        if first_seen is None:
            continue

        # Read all complete days for this unit from the anchor forward.
        cur.execute(
            """
            SELECT day
            FROM analytics_daily_completeness
            WHERE seller_id = %s AND advertiser_id = %s
              AND storage_key = %s AND campaign_id = %s
              AND day >= %s AND day <= %s
              AND is_complete = TRUE
            ORDER BY day
            """,
            (sid, aid, skey, cid, first_seen, today_in_shop_tz),
        )
        complete_days = {r[0] for r in cur.fetchall()}

        latest_completed: date | None = None
        current = first_seen
        while current <= today_in_shop_tz and current in complete_days:
            latest_completed = current
            current += timedelta(days=1)

        # Advance-only upsert. first_seen_day keeps the earliest anchor ever
        # seen (a later backfill of an earlier day moves it backwards so the
        # chain re-anchors; latest_completed_day itself never regresses).
        cur.execute(
            """
            INSERT INTO analytics_cursors (
                seller_id, advertiser_id, storage_key, campaign_id,
                latest_completed_day, first_seen_day, last_updated_at, request_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, now(), %s)
            ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
            DO UPDATE SET
                latest_completed_day = CASE
                    WHEN EXCLUDED.latest_completed_day IS NULL
                        THEN analytics_cursors.latest_completed_day
                    WHEN analytics_cursors.latest_completed_day IS NULL
                        THEN EXCLUDED.latest_completed_day
                    ELSE GREATEST(
                        analytics_cursors.latest_completed_day,
                        EXCLUDED.latest_completed_day
                    )
                END,
                first_seen_day = LEAST(
                    analytics_cursors.first_seen_day,
                    EXCLUDED.first_seen_day
                ),
                last_updated_at = now(),
                request_id = EXCLUDED.request_id
            """,
            (sid, aid, skey, cid, latest_completed, first_seen, request_id),
        )


# ─── Cursor lookup helpers (used by app.py) ───────────────────────────


def fetch_cursor_page(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | None,
    campaign_id: str | None,
    timezone_name: str,
    today_in_shop_tz: date,
    bootstrap_lookback_days: int,
) -> list[CursorEntry]:
    """Read cursor rows and compute nextRequiredDay per row.

    Reads analytics_cursors (maintained transactionally by upsert_records).
    nextRequiredDay is authoritative: the server, not the client, decides
    the next day to sync.
    """
    storage_key_value = storage_key.value if storage_key is not None else None
    params = [
        seller_id,
        advertiser_id,
        storage_key_value,
        storage_key_value,
        campaign_id,
        campaign_id,
    ]

    bootstrap_day = _subtract_days(today_in_shop_tz, bootstrap_lookback_days)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT storage_key, campaign_id, latest_completed_day, first_seen_day
            FROM analytics_cursors
            WHERE seller_id = %s
              AND advertiser_id = %s
              AND (%s::text IS NULL OR storage_key = %s)
              AND (%s::text IS NULL OR campaign_id = %s)
            """,
            params,
        )
        rows = cur.fetchall()

    entries: list[CursorEntry] = []
    for storage_key_str, campaign_id_str, latest_completed_day, first_seen_day in rows:
        if latest_completed_day is not None:
            next_required = max(
                _add_days(latest_completed_day, 1),
                bootstrap_day,
            )
        elif first_seen_day is not None:
            # Records exist but the anchor day itself is not yet complete:
            # the first day still missing pages is the next required day.
            next_required = max(first_seen_day, bootstrap_day)
        else:
            next_required = bootstrap_day
        entries.append(
            CursorEntry(
                storage_key=StorageKey(storage_key_str),
                campaign_id=campaign_id_str,
                latest_completed_day=latest_completed_day,
                next_required_day=next_required,
            )
        )
    return entries


def fetch_timezone(seller_id: str) -> str:
    """Return the canonical IANA timezone for this seller.

    Seeds the row with Asia/Shanghai if missing. If the stored value is
    not a valid IANA identifier (e.g. corrupted row, manual edit, or a
    future server deployment where the TZ column default changed),
    falls back to Asia/Shanghai rather than passing the garbage string
    to the ZoneInfo constructor (which would raise).
    """
    from zoneinfo import ZoneInfo as _ZI

    default_tz = DEFAULT_TIMEZONE
    with connect() as conn, conn.cursor() as cur:
        # Read-first: the INSERT below is a no-op for 99.99% of calls, so
        # don't pay a write (WAL + lock) on every GET /cursor (W1.8).
        cur.execute(
            "SELECT timezone FROM analytics_shop_timezones WHERE seller_id = %s",
            (seller_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
                VALUES (%s, '', %s)
                ON CONFLICT (seller_id) DO NOTHING
                """,
                (seller_id, default_tz),
            )
            conn.commit()
            row = (default_tz,)
    tz_str = row[0] if row else default_tz
    # Validate: a garbage TZ in the DB would crash _today_in_tz (ZoneInfo
    # raises). Repair the row + fall back rather than 500-ing every
    # request that touches that seller.
    try:
        _ZI(tz_str)
    except Exception:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE analytics_shop_timezones SET timezone = %s WHERE seller_id = %s",
                (default_tz, seller_id),
            )
            conn.commit()
        return default_tz
    return tz_str


# ─── Audit log helper ─────────────────────────────────────────────────


def write_audit(
    *,
    request_id: str | None,
    endpoint: str,
    method: str,
    path: str,
    status: int,
    key_prefix: str | None,
    records_in: int | None = None,
    records_ok: int | None = None,
    records_rej: int | None = None,
    error_code: str | None = None,
) -> None:
    """Append one audit row. Best-effort — failures are logged to stderr
    but do not propagate, so audit problems never break the request."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics_audit_log (
                    request_id, endpoint, method, path, status,
                    key_prefix, records_in, records_ok, records_rej, error_code
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request_id,
                    endpoint,
                    method,
                    path,
                    status,
                    key_prefix,
                    records_in,
                    records_ok,
                    records_rej,
                    error_code,
                ),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover — best-effort
        import sys

        sys.stderr.write(f"[analytics-sync] audit write failed: {exc!r}\n")


# ─── Date helpers (no zoneinfo needed for additive day math) ─────────
# We don't ship zoneinfo because the protocol contract is "today in
# shop timezone". The caller computes today_in_shop_tz once and passes
# it in. These helpers only need to add/subtract days correctly across
# month/year boundaries.


def _add_days(d: date, n: int) -> date:
    """Add n days to d (positive or negative). Handles month/year
    rollover correctly via datetime arithmetic."""
    return date.fromordinal(d.toordinal() + n)


def _subtract_days(d: date, n: int) -> date:
    return _add_days(d, -n)

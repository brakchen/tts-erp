"""PG-backed repository for analytics_sync.

Production implementation of AnalyticsRepository from domain.py.

The batch upload path runs inside a single transaction so that records
and cursor advances either all commit or all roll back. Each record is
inserted with ON CONFLICT (idempotency_key) DO NOTHING so a duplicate
upload returns a "duplicate" outcome without aborting the batch.

Cursor advance is conditional: latest_completed_day = GREATEST(existing,
new_day). This is idempotent and never regresses.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from .domain import (
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
            "TTS_ERP_DB_URL (or ANALYTICS_SYNC_DB_URL) not configured; "
            "set it in .env"
        )
    return psycopg.connect(url)


class PgAnalyticsRepository(AnalyticsRepository):
    def upsert_records(
        self,
        scope: Scope,
        records: list[Record],
        request_id: str | None,
    ) -> BatchResult:
        with connect() as conn:
            with conn.cursor() as cur:
                accepted: list[AcceptedRecord] = []
                # day per scope/storage/campaign that was *inserted* (not
                # duplicate) → candidates for cursor advance.
                advance_keys: set[tuple[str, str, StorageKey, str, date]] = set()

                for rec in records:
                    expected = compute_idempotency_key(
                        seller_id=scope.seller_id,
                        advertiser_id=scope.advertiser_id,
                        storage_key=rec.storage_key,
                        campaign_id=rec.campaign_id,
                        day=rec.day,
                        page=rec.page,
                    )
                    if expected != rec.idempotency_key:
                        raise IdempotencyKeyMismatch(rec.idempotency_key, expected)

                    cur.execute(
                        """
                        INSERT INTO analytics_records (
                            idempotency_key, source_record_id,
                            seller_id, advertiser_id, storage_key, campaign_id,
                            day, page, shop_name,
                            endpoint, method, request_body, response_data,
                            source, captured_at,
                            schema_version, protocol_version,
                            request_id
                        ) VALUES (
                            %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s,
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
                            rec.endpoint,
                            rec.method,
                            psycopg.types.json.Jsonb(rec.request_body) if rec.request_body is not None else None,
                            psycopg.types.json.Jsonb(rec.response),
                            rec.source,
                            rec.captured_at,
                            rec.schema_version,
                            1,
                            request_id,
                        ),
                    )
                    inserted = cur.fetchone() is not None
                    if inserted:
                        accepted.append(
                            AcceptedRecord(idempotency_key=rec.idempotency_key, status="inserted")
                        )
                        advance_keys.add(
                            (scope.seller_id, scope.advertiser_id, rec.storage_key, rec.campaign_id, rec.day)
                        )
                    else:
                        accepted.append(
                            AcceptedRecord(idempotency_key=rec.idempotency_key, status="duplicate")
                        )

                # Atomic cursor advance. Only days corresponding to
                # *inserted* records move the cursor; duplicates are
                # already represented in the latest_completed_day either
                # directly or via GREATEST, so this is a no-op for them.
                for sid, aid, skey, cid, day in advance_keys:
                    cur.execute(
                        """
                        INSERT INTO analytics_cursors (
                            seller_id, advertiser_id, storage_key, campaign_id,
                            latest_completed_day, last_updated_at, request_id
                        )
                        VALUES (%s, %s, %s, %s, %s, now(), %s)
                        ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
                        DO UPDATE SET
                            latest_completed_day = GREATEST(
                                analytics_cursors.latest_completed_day,
                                EXCLUDED.latest_completed_day
                            ),
                            last_updated_at = now(),
                            request_id = EXCLUDED.request_id
                        """,
                        (sid, aid, skey.value, cid, day, request_id),
                    )

                # Upsert the shop timezone (last-write-wins) so we
                # always have a row to read.
                cur.execute(
                    """
                    INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (seller_id) DO UPDATE
                        SET advertiser_id = EXCLUDED.advertiser_id,
                            updated_at = now()
                    """,
                    (scope.seller_id, scope.advertiser_id, "Asia/Shanghai"),
                )

            conn.commit()
        return BatchResult(accepted=accepted, rejected=[])


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

    Pure SQL for the read + pure Python for the date arithmetic.
    The timezone computation (today_in_shop_tz) is done by the caller
    so the SQL stays portable.
    """
    sql = [
        "SELECT storage_key, campaign_id, latest_completed_day",
        "FROM analytics_cursors",
        "WHERE seller_id = %s AND advertiser_id = %s",
    ]
    params: list[Any] = [seller_id, advertiser_id]
    if storage_key is not None:
        sql.append("AND storage_key = %s")
        params.append(storage_key.value)
    if campaign_id is not None:
        sql.append("AND campaign_id = %s")
        params.append(campaign_id)

    bootstrap_day = _subtract_days(today_in_shop_tz, bootstrap_lookback_days)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("\n".join(sql), params)
        rows = cur.fetchall()

    entries: list[CursorEntry] = []
    for storage_key_str, campaign_id_str, latest_completed_day in rows:
        if latest_completed_day is None:
            next_required = bootstrap_day
        else:
            next_required = max(
                _add_days(latest_completed_day, 1),
                bootstrap_day,
            )
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
    default_tz = "Asia/Shanghai"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
            VALUES (%s, '', %s)
            ON CONFLICT (seller_id) DO NOTHING
            """,
            (seller_id, default_tz),
        )
        cur.execute(
            "SELECT timezone FROM analytics_shop_timezones WHERE seller_id = %s",
            (seller_id,),
        )
        row = cur.fetchone()
        conn.commit()
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

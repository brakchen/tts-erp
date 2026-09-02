"""Analytics 存储层（v2，SQLAlchemy）。

2026-09-02 从 ``analytics_sync/pg_repositories.py``（裸 psycopg、自带
connect()）重写：统一走 ``tts_erp_v2.db.base`` 的 engine/session 工厂，
表全部 schema 限定为 ``analytics.ad_*``。

设计说明：
- SQL 以模块级 ``text()`` 常量书写（v2 惯例，见 api/v2/reporting.py 的
  SQL_COST_SNAPSHOTS 模式）。相对旧实现的改动**仅限表名**——事务语义
  （FOR UPDATE / ON CONFLICT / 事务内重算）逐条保留，便于 review 对照。
- ``upsert_records`` 在传入 session 上跑完整个 batch 事务并 commit；
  异常由调用方（handler）兜底，请求结束时 deps.get_session rollback。
- ``write_audit`` 用**独立** engine 连接提交（best-effort）——审计失败
  不影响主请求，也不随请求 session 的 rollback 丢失。
- ``fetch_timezone`` 懒写（首次某 seller 时补默认时区行）会就地 commit；
  在两个 handler 里它都先于其它写入调用，commit 不会夹带别的状态。

v2 语义（与旧实现一致）：
- 每条 record 带 expected_page_count（v1 记录按 1 处理）。
- 一天 "complete" 当且仅当 ad_daily_pages 覆盖 1..expected_page_count。
- ad_cursors.latest_completed_day = 从 anchor 起连续 complete 链的最后一天，
  缺口不可跳过；只进不退。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from zoneinfo import ZoneInfo as _ZI

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tts_erp_v2.db.base import get_engine

from .domain import (
    DEFAULT_TIMEZONE,
    AcceptedRecord,
    BatchResult,
    CursorEntry,
    Record,
    RejectedRecord,
    Scope,
    StorageKey,
    compute_idempotency_key,
)

# ─── SQL 常量（模块级，无插值）────────────────────────────────────────

SQL_LOCK_COMPLETENESS = """
SELECT expected_page_count
FROM analytics.ad_daily_completeness
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id AND day = :day
FOR UPDATE
"""

SQL_INSERT_RECORD = """
INSERT INTO analytics.ad_records (
    idempotency_key, source_record_id,
    seller_id, advertiser_id, storage_key, campaign_id,
    day, page, shop_name, expected_page_count,
    endpoint, method, request_body, response_data,
    source, captured_at,
    schema_version, protocol_version,
    request_id
) VALUES (
    :idempotency_key, :source_record_id,
    :seller_id, :advertiser_id, :storage_key, :campaign_id,
    :day, :page, :shop_name, :expected_page_count,
    :endpoint, :method, CAST(:request_body AS JSONB), CAST(:response_data AS JSONB),
    :source, :captured_at,
    :schema_version, :protocol_version,
    :request_id
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id
"""

# 首条记录定下当天的 expected_page_count；冲突已在写入前拒绝。
# DO UPDATE 的 WHERE 使同值冲突完全不触发写入（防御性 no-op）。
SQL_UPSERT_COMPLETENESS = """
INSERT INTO analytics.ad_daily_completeness (
    seller_id, advertiser_id, storage_key, campaign_id, day,
    expected_page_count, is_complete, completed_at, last_recomputed_at
)
VALUES (:seller_id, :advertiser_id, :storage_key, :campaign_id, :day,
        :expected_page_count, FALSE, NULL, now())
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day)
DO UPDATE SET last_recomputed_at = now()
WHERE ad_daily_completeness.expected_page_count
      IS DISTINCT FROM EXCLUDED.expected_page_count
"""

SQL_INSERT_DAILY_PAGE = """
INSERT INTO analytics.ad_daily_pages (
    seller_id, advertiser_id, storage_key, campaign_id, day, page
)
VALUES (:seller_id, :advertiser_id, :storage_key, :campaign_id, :day, :page)
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day, page)
DO NOTHING
"""

SQL_GET_EXPECTED = """
SELECT expected_page_count
FROM analytics.ad_daily_completeness
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id AND day = :day
"""

SQL_COUNT_PAGES_IN_RANGE = """
SELECT COUNT(DISTINCT page)
FROM analytics.ad_daily_pages
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id AND day = :day
  AND page BETWEEN 1 AND :expected
"""

SQL_UPDATE_COMPLETENESS = """
UPDATE analytics.ad_daily_completeness
SET is_complete = :is_complete,
    completed_at = CASE WHEN :is_complete THEN now() ELSE NULL END,
    last_recomputed_at = now()
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id AND day = :day
"""

SQL_FIRST_SEEN_DAY = """
SELECT MIN(day)
FROM analytics.ad_daily_completeness
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id
"""

SQL_COMPLETE_DAYS = """
SELECT day
FROM analytics.ad_daily_completeness
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND storage_key = :storage_key AND campaign_id = :campaign_id
  AND day >= :first_seen AND day <= :today
  AND is_complete = TRUE
ORDER BY day
"""

# 只进不退 upsert：latest_completed_day 取 GREATEST（NULL 安全），
# first_seen_day 取 LEAST（更早的回填会把 anchor 往前挪）。
SQL_UPSERT_CURSOR = """
INSERT INTO analytics.ad_cursors (
    seller_id, advertiser_id, storage_key, campaign_id,
    latest_completed_day, first_seen_day, last_updated_at, request_id
)
VALUES (:seller_id, :advertiser_id, :storage_key, :campaign_id,
        :latest_completed_day, :first_seen_day, now(), :request_id)
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
DO UPDATE SET
    latest_completed_day = CASE
        WHEN EXCLUDED.latest_completed_day IS NULL
            THEN ad_cursors.latest_completed_day
        WHEN ad_cursors.latest_completed_day IS NULL
            THEN EXCLUDED.latest_completed_day
        ELSE GREATEST(
            ad_cursors.latest_completed_day,
            EXCLUDED.latest_completed_day
        )
    END,
    first_seen_day = LEAST(
        ad_cursors.first_seen_day,
        EXCLUDED.first_seen_day
    ),
    last_updated_at = now(),
    request_id = EXCLUDED.request_id
"""

# 店铺时区 last-write-wins（batch 事务内尾部）
SQL_UPSERT_TIMEZONE = """
INSERT INTO analytics.ad_shop_timezones (seller_id, advertiser_id, timezone)
VALUES (:seller_id, :advertiser_id, :timezone)
ON CONFLICT (seller_id) DO UPDATE
    SET advertiser_id = EXCLUDED.advertiser_id,
        updated_at = now()
"""

SQL_FETCH_CURSOR_ROWS = """
SELECT storage_key, campaign_id, latest_completed_day, first_seen_day
FROM analytics.ad_cursors
WHERE seller_id = :seller_id
  AND advertiser_id = :advertiser_id
  AND (CAST(:storage_key AS text) IS NULL OR storage_key = :storage_key)
  AND (CAST(:campaign_id AS text) IS NULL OR campaign_id = :campaign_id)
"""

SQL_GET_TIMEZONE = (
    "SELECT timezone FROM analytics.ad_shop_timezones WHERE seller_id = :seller_id"
)

SQL_SEED_TIMEZONE = """
INSERT INTO analytics.ad_shop_timezones (seller_id, advertiser_id, timezone)
VALUES (:seller_id, '', :timezone)
ON CONFLICT (seller_id) DO NOTHING
"""

SQL_REPAIR_TIMEZONE = (
    "UPDATE analytics.ad_shop_timezones "
    "SET timezone = :timezone WHERE seller_id = :seller_id"
)

SQL_INSERT_AUDIT = """
INSERT INTO analytics.ad_audit_log (
    request_id, endpoint, method, path, status,
    key_prefix, records_in, records_ok, records_rej,
    error_code, error_message
)
VALUES (
    :request_id, :endpoint, :method, :path, :status,
    :key_prefix, :records_in, :records_ok, :records_rej,
    :error_code, :error_message
)
"""

# retention（analytics.retention job；原 retention.sql 语义）：
# records 90 天（received_at）、audit 30 天（created_at）；
# cursors / shop_timezones 永久保留（见 tech-doc/compatibility.md §2）。
SQL_PURGE_RECORDS = (
    "DELETE FROM analytics.ad_records "
    "WHERE received_at < now() - make_interval(days => :days)"
)
SQL_PURGE_AUDIT = (
    "DELETE FROM analytics.ad_audit_log "
    "WHERE created_at < now() - make_interval(days => :days)"
)


# ─── batch upsert ─────────────────────────────────────────────────────

# unit_day 参数组的键序（SQL 占位符命名一致）
_UNIT_DAY_KEYS = ("seller_id", "advertiser_id", "storage_key", "campaign_id", "day")


def _unit_day_params(unit_day: tuple[str, str, str, str, date]) -> dict:
    return dict(zip(_UNIT_DAY_KEYS, unit_day, strict=True))


def upsert_records(
    sess: Session,
    scope: Scope,
    records: list[Record],
    request_id: str | None,
    *,
    today_in_shop_tz: date,
    bootstrap_day: date,
) -> BatchResult:
    """写入一个 batch 的全部有效记录，单事务原子提交。

    records → ad_records / ad_daily_pages → 重算 completeness → 推进
    cursors → 店铺时区 last-write-wins，全部在 ``sess`` 上执行后
    ``sess.commit()``。任一步失败抛给调用方，session 由 deps 回滚。
    """
    accepted: list[AcceptedRecord] = []
    rejected: list[RejectedRecord] = []

    # 本 batch 触动的 (scope, storageKey, campaignId, day) —— 用于重算。
    modified_unit_days: set[tuple[str, str, str, str, date]] = set()

    for rec in records:
        # 安全网：重算 canonical idempotency key。
        expected_key = compute_idempotency_key(
            seller_id=scope.seller_id,
            advertiser_id=scope.advertiser_id,
            storage_key=rec.storage_key,
            campaign_id=rec.campaign_id,
            day=rec.day,
            page=rec.page,
        )
        if expected_key != rec.idempotency_key:
            rejected.append(
                RejectedRecord(
                    idempotency_key=rec.idempotency_key,
                    code="SCHEMA_INVALID",
                    message=(
                        f"idempotencyKey mismatch: "
                        f"client={rec.idempotency_key[:16]}… "
                        f"server={expected_key[:16]}…"
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
        unit_params = _unit_day_params(unit_day)

        # 跨 batch 页数冲突：当天的 expected_page_count 一旦定下，后续
        # 所有记录必须一致。FOR UPDATE 行锁让并发 batch 串行化。
        # pi-lens-ignore: python-sql-injection
        stored_expected = sess.execute(
            text(SQL_LOCK_COMPLETENESS), unit_params
        ).scalar()

        if stored_expected is not None and stored_expected != rec.expected_page_count:
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

        # 原始记录 upsert（重复幂等键 → DO NOTHING → duplicate）。
        # pi-lens-ignore: python-sql-injection
        inserted_id = sess.execute(
            text(SQL_INSERT_RECORD),
            {
                "idempotency_key": rec.idempotency_key,
                "source_record_id": rec.source_record_id,
                **unit_params,
                "page": rec.page,
                "shop_name": scope.shop_name,
                "expected_page_count": rec.expected_page_count,
                "endpoint": rec.endpoint,
                "method": rec.method,
                "request_body": (
                    json.dumps(rec.request_body, ensure_ascii=False)
                    if rec.request_body is not None
                    else None
                ),
                "response_data": json.dumps(rec.response, ensure_ascii=False),
                "source": rec.source,
                "captured_at": rec.captured_at,
                "schema_version": rec.schema_version,
                "protocol_version": rec.protocol_version,
                "request_id": request_id,
            },
        ).scalar()

        # 无论新插还是重复，该页都已持久化 —— 都要记进页位图。
        accepted.append(
            AcceptedRecord(
                idempotency_key=rec.idempotency_key,
                status="inserted" if inserted_id is not None else "duplicate",
            )
        )

        # 当天 expected_page_count 的 upsert（首条获胜；冲突已在上面拒绝）。
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_UPSERT_COMPLETENESS),
            {**unit_params, "expected_page_count": rec.expected_page_count},
        )

        # 页位图标记。ON CONFLICT DO NOTHING 让并发 batch 安全 race。
        # pi-lens-ignore: python-sql-injection
        sess.execute(text(SQL_INSERT_DAILY_PAGE), {**unit_params, "page": rec.page})

        modified_unit_days.add(unit_day)

    # 重算每个触动的 (unit, day) 的完整性。
    _recompute_completeness(sess, modified_unit_days)

    # 重算每个触动的 unit 的 cursor：latest_completed_day 是从 anchor
    # 起连续 complete 前缀的最后一天；缺口不可跳过，cursor 只进不退。
    _recompute_cursors(
        sess,
        modified_unit_days,
        bootstrap_day=bootstrap_day,
        today_in_shop_tz=today_in_shop_tz,
        request_id=request_id,
    )

    # 店铺时区 upsert（last-write-wins），保证 cursor 查询永远有行可读。
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_UPSERT_TIMEZONE),
        {
            "seller_id": scope.seller_id,
            "advertiser_id": scope.advertiser_id,
            "timezone": DEFAULT_TIMEZONE,
        },
    )

    sess.commit()
    return BatchResult(accepted=accepted, rejected=rejected)


# ─── 完整性 / cursor 重算（同事务内）───────────────────────────────────


def _recompute_completeness(
    sess: Session,
    unit_days: set[tuple[str, str, str, str, date]],
) -> None:
    """每个 (scope, storageKey, campaignId, day)：当且仅当 ad_daily_pages
    恰好覆盖 1..expected_page_count 时置 is_complete = TRUE。"""
    for unit_day in unit_days:
        params = _unit_day_params(unit_day)
        # pi-lens-ignore: python-sql-injection
        expected = sess.execute(text(SQL_GET_EXPECTED), params).scalar()
        if expected is None:
            continue

        # 页 1..expected 全部在场才算 complete；v1 遗留的多余页
        # （隐式 expected=1 但传了更多页）不影响判定。
        # pi-lens-ignore: python-sql-injection
        pages_in_range = sess.execute(
            text(SQL_COUNT_PAGES_IN_RANGE), {**params, "expected": expected}
        ).scalar()
        if pages_in_range is None:
            continue
        is_complete = pages_in_range == expected

        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_UPDATE_COMPLETENESS), {**params, "is_complete": is_complete}
        )


def _recompute_cursors(
    sess: Session,
    unit_days: set[tuple[str, str, str, str, date]],
    *,
    bootstrap_day: date,
    today_in_shop_tz: date,
    request_id: str | None,
) -> None:
    """按 (seller, advertiser, storage_key, campaign_id) 分别推进
    ad_cursors 到连续 complete 前缀的最后一天。

    连续链 anchor 在 first_seen_day（该 unit 有记录的最早一天），而不是
    bootstrap_day：client 被告知从 bootstrap 开始，它的首次上传定义 anchor。
    anchor 之后的内部缺口（缺天或不完整的天）阻止推进。cursor 只进不退：
    v1 时代的 GREATEST 值会保留到连续完整性追上它。
    """
    units: set[tuple[str, str, str, str]] = {
        (sid, aid, skey, cid) for sid, aid, skey, cid, _ in unit_days
    }

    for sid, aid, skey, cid in units:
        unit_params = {
            "seller_id": sid,
            "advertiser_id": aid,
            "storage_key": skey,
            "campaign_id": cid,
        }
        # anchor：该 unit 有 completeness 行的最早一天。
        # pi-lens-ignore: python-sql-injection
        first_seen = sess.execute(text(SQL_FIRST_SEEN_DAY), unit_params).scalar()
        if first_seen is None:
            continue

        # 从 anchor 起到今天为止的所有 complete 天。
        # pi-lens-ignore: python-sql-injection
        rows = sess.execute(
            text(SQL_COMPLETE_DAYS),
            {**unit_params, "first_seen": first_seen, "today": today_in_shop_tz},
        ).fetchall()
        complete_days = {r[0] for r in rows}

        latest_completed: date | None = None
        current = first_seen
        while current <= today_in_shop_tz and current in complete_days:
            latest_completed = current
            current += timedelta(days=1)

        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_UPSERT_CURSOR),
            {
                **unit_params,
                "latest_completed_day": latest_completed,
                "first_seen_day": first_seen,
                "request_id": request_id,
            },
        )


# ─── cursor 查询（GET /cursor）────────────────────────────────────────


def fetch_cursor_page(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | None,
    campaign_id: str | None,
    today_in_shop_tz: date,
    bootstrap_lookback_days: int,
) -> list[CursorEntry]:
    """读 cursor 行并计算每行的 nextRequiredDay。

    读 ad_cursors（由 upsert_records 事务性维护）。nextRequiredDay 是
    权威值：由服务端而非客户端决定下一个要同步的日期。
    """
    bootstrap_day = _subtract_days(today_in_shop_tz, bootstrap_lookback_days)

    # pi-lens-ignore: python-sql-injection
    rows = sess.execute(
        text(SQL_FETCH_CURSOR_ROWS),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "storage_key": storage_key.value if storage_key is not None else None,
            "campaign_id": campaign_id,
        },
    ).fetchall()

    entries: list[CursorEntry] = []
    for storage_key_str, campaign_id_str, latest_completed_day, first_seen_day in rows:
        if latest_completed_day is not None:
            next_required = max(
                _add_days(latest_completed_day, 1),
                bootstrap_day,
            )
        elif first_seen_day is not None:
            # 有记录但 anchor 当天尚不完整：第一个还缺页的天就是下一个
            # 需要同步的天。
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


def fetch_timezone(sess: Session, seller_id: str) -> str:
    """返回该 seller 的权威 IANA 时区。

    缺行时以 Asia/Shanghai 补种（懒写会就地 commit——两个 handler 都在
    其它写入之前调用它）。若库里的值不是合法 IANA 标识（脏行、手改、
    或未来部署改了列默认值），修复该行并回落 Asia/Shanghai，而不是把
    垃圾串传给 ZoneInfo 构造器（会抛）。
    """
    default_tz = DEFAULT_TIMEZONE
    # pi-lens-ignore: python-sql-injection
    tz_str = sess.execute(text(SQL_GET_TIMEZONE), {"seller_id": seller_id}).scalar()
    if tz_str is None:
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_SEED_TIMEZONE),
            {"seller_id": seller_id, "timezone": default_tz},
        )
        sess.commit()
        tz_str = default_tz
    # 校验：脏 TZ 会让 _today_in_tz 崩（ZoneInfo raises）。修复 + 回落，
    # 而不是让该 seller 的每个请求都 500。
    try:
        _ZI(tz_str)
    except Exception:
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_REPAIR_TIMEZONE),
            {"seller_id": seller_id, "timezone": default_tz},
        )
        sess.commit()
        return default_tz
    return tz_str


# ─── 审计（独立连接，best-effort）─────────────────────────────────────


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
    error_message: str | None = None,
    engine: Engine | None = None,
) -> None:
    """追加一行审计。best-effort —— 失败只打 stderr，不外抛，
    审计问题永远不打断请求。

    用独立 engine 连接（自带事务提交），不经过请求 session：
    请求 session 结束时会被 deps rollback，审计行走这里才能留存。

    ``error_message`` 是消毒后的 Pydantic/JSON 解析细节（≤500 字符，
    无 token/body）。入库让 ops 在 stderr 日志轮转后仍可
    ``SELECT ... WHERE error_message LIKE '%capturedAt%'``。
    """
    try:
        eng = engine or get_engine()
        with eng.begin() as conn:
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(SQL_INSERT_AUDIT),
                {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "method": method,
                    "path": path,
                    "status": status,
                    "key_prefix": key_prefix,
                    "records_in": records_in,
                    "records_ok": records_ok,
                    "records_rej": records_rej,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
    except Exception as exc:  # best-effort：审计失败不外抛
        sys.stderr.write(f"[analytics-sync] audit write failed: {exc!r}\n")


# ─── retention（analytics.retention job 用）───────────────────────────


def purge_expired(
    sess: Session,
    *,
    records_days: int = 90,
    audit_days: int = 30,
) -> dict[str, int]:
    """删除过期数据并 commit。返回各表删除行数。

    语义与原 ``analytics_sync/retention.sql`` 一致：records 按
    received_at 保留 90 天，audit 按 created_at 保留 30 天；
    cursors / shop_timezones 永久保留。
    """
    # pi-lens-ignore: python-sql-injection
    records_deleted = sess.execute(
        text(SQL_PURGE_RECORDS), {"days": records_days}
    ).rowcount
    # pi-lens-ignore: python-sql-injection
    audit_deleted = sess.execute(text(SQL_PURGE_AUDIT), {"days": audit_days}).rowcount
    return {"records_deleted": records_deleted, "audit_deleted": audit_deleted}


# ─── 日期工具（加性日期运算不需要 zoneinfo）────────────────────────────
# 协议契约是「店铺时区的今天」——调用方算好 today_in_shop_tz 传进来。
# 这些工具只需要跨月/跨年正确地加减天数。


def _add_days(d: date, n: int) -> date:
    """d 加 n 天（可正可负）。用 datetime 序数运算正确处理月/年 rollover。"""
    return date.fromordinal(d.toordinal() + n)


def _subtract_days(d: date, n: int) -> date:
    return _add_days(d, -n)

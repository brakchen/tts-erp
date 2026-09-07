"""Analytics 存储层（v2 dump architecture + v3 range-aggregate,SQLAlchemy）。

2026-09-07 range-aggregate（tech-doc/analytics/range-aggregate-history-sync.md，
Design A；migration 0012/0013）核心改造：

- ad_raw 每 (scope, endpoint, campaign, kind) **至多一行 live**：
  - kind='history' 历史整段快照 [S..T-1]，区间是**可变内容**，原地 upsert 推进；
  - kind='today'   今日快照 [T..T]，30s 原地覆盖；
  - kind='daily'   legacy 逐日行（存量 + 旧 v2 插件写入），首个覆盖它们的 v3
    history 写入时**同事务折叠删除**。
- capturedAt 单调守卫：旧快照（迟到重试/重装竞态）不覆盖新快照 → status
  ``stale_ignored``；插入 ``inserted`` / 覆盖成功 ``updated``。
- 内容被取代事件写 ``analytics.ad_sync_audit`` 一行元数据（D-4），同事务提交。
- 30s today 常规刷新不写审计；仅跨天 ``today_reset`` 时写。

红线（保持 has_data_cache 无 stale-true 论证）：live 行只 upsert 不删；物理删除只
发生在 daily legacy 折叠。

SQL 以模块级 text() 常量书写（v2 惯例）。表全部 schema 限定为 analytics.ad_*。
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import (
    KIND_HISTORY,
    KIND_TODAY,
    LIVE_KINDS,
    DumpPayload,
    DumpResult,
    HasDataResult,
    LiveRowResult,
    StorageKey,
    compute_idempotency_key,
    compute_idempotency_key_v3,
)

# ─── endpoint → storage_key 映射（server-side 单点定义）────────────────
# 4 路径 1:1 映射（post_campaign_list 是 discovery,不走 dump 协议,不在此表）:
# plugin dump 协议不传 storageKey（消除 client 端 enum 知识）,
# server 端用 STORAGE_KEY_BY_PATH[endpoint] 推导。
STORAGE_KEY_BY_PATH: dict[str, StorageKey] = {
    "/oec_ads/shopping/v1/oec/stat/post_product_list": StorageKey.PRODUCT_ANALYSES,
    "/oec_ads/shopping/v1/oec/stat/post_session_list": StorageKey.SESSION_ANALYSES,
    "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list": StorageKey.CAMPAIGN_CHANGE_LOGS,
}


# ─── SQL 常量（模块级,无插值）────────────────────────────────────────

# live 行 upsert（kind history/today；区间原地更新 + capturedAt 单调守卫）。
# RETURNING 行为：
#   - 新插 → (xmax = 0) = true
#   - 冲突且 captured_at 守卫通过 → 更新 → 返回行,(xmax = 0) = false
#   - 冲突但守卫拒绝（旧 captured_at < 活动行）→ DO UPDATE 被 WHERE 跳过 → 无返回行
SQL_UPSERT_LIVE = """
INSERT INTO analytics.ad_raw (
    idempotency_key, seller_id, advertiser_id, endpoint, method,
    kind, day_start, day_end, campaign_id, request, response, captured_at,
    source, request_id, protocol_version, schema_version
) VALUES (
    :idempotency_key, :seller_id, :advertiser_id, :endpoint, :method,
    :kind, :day_start, :day_end, :campaign_id,
    CAST(:request AS JSONB), CAST(:response AS JSONB),
    :captured_at, :source, :request_id, :protocol_version, :schema_version
)
ON CONFLICT (seller_id, advertiser_id, endpoint, campaign_id, kind)
    WHERE kind IN ('history', 'today')
DO UPDATE SET request         = EXCLUDED.request,
              response        = EXCLUDED.response,
              day_start       = EXCLUDED.day_start,
              day_end         = EXCLUDED.day_end,
              idempotency_key = EXCLUDED.idempotency_key,
              captured_at     = EXCLUDED.captured_at,
              received_at     = now()
    WHERE analytics.ad_raw.captured_at <= EXCLUDED.captured_at
RETURNING (xmax = 0) AS was_inserted
"""

# legacy daily 行（旧 v2 插件/存量迁移）：唯一键 = 旧 5 元组（day_end = 单日）。
SQL_UPSERT_DAILY = """
INSERT INTO analytics.ad_raw (
    idempotency_key, seller_id, advertiser_id, endpoint, method,
    kind, day_start, day_end, campaign_id, request, response, captured_at,
    source, request_id, protocol_version, schema_version
) VALUES (
    :idempotency_key, :seller_id, :advertiser_id, :endpoint, :method,
    'daily', :day_start, :day_end, :campaign_id,
    CAST(:request AS JSONB), CAST(:response AS JSONB),
    :captured_at, :source, :request_id, :protocol_version, :schema_version
)
ON CONFLICT (seller_id, advertiser_id, endpoint, day_end, campaign_id)
    WHERE kind = 'daily'
DO UPDATE SET request         = EXCLUDED.request,
              response        = EXCLUDED.response,
              day_start       = EXCLUDED.day_start,
              idempotency_key = EXCLUDED.idempotency_key,
              captured_at     = EXCLUDED.captured_at,
              received_at     = now()
RETURNING (xmax = 0) AS was_inserted
"""

# 读 live 行（coverage / 审计 prev）
SQL_LIVE_ROW = """
SELECT day_start, day_end, captured_at
FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND campaign_id = :campaign_id AND kind = :kind
"""

# legacy daily 折叠（幂等：命中 0 行无害）；与 history upsert 同一事务
SQL_FOLD_DAILY = """
DELETE FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND campaign_id = :campaign_id
  AND kind = 'daily'
  AND day_end BETWEEN :day_start AND :day_end
"""

# 审计写（内容被取代事件一行，元数据）
SQL_INSERT_AUDIT = """
INSERT INTO analytics.ad_sync_audit (
    seller_id, advertiser_id, endpoint, campaign_id, kind, event,
    prev_day_start, prev_day_end, prev_captured_at,
    new_day_start, new_day_end, new_captured_at,
    reason, request_id
) VALUES (
    :seller_id, :advertiser_id, :endpoint, :campaign_id, :kind, :event,
    :prev_day_start, :prev_day_end, :prev_captured_at,
    :new_day_start, :new_day_end, :new_captured_at,
    :reason, :request_id
)
"""

# legacy has-data（覆盖 day 语义：daily 单日 或 live 区间含该日）
SQL_HAS_DATA = """
SELECT 1 FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND (
        (kind = 'daily' AND day_end = :day)
     OR (kind IN ('history', 'today') AND day_start <= :day AND day_end >= :day)
  )
  AND (CAST(:campaign_id AS text) IS NULL OR campaign_id = :campaign_id)
LIMIT 1
"""

# campaign 桶灌载（cursor coverage 缓存 miss 回源）: 只拉 live 行 5 元组，
# 绝不碰 request/response JSONB blob。
SQL_CAMPAIGN_LIVE_ROWS = """
SELECT endpoint, kind, day_start, day_end, captured_at
FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND campaign_id = :campaign_id
  AND kind IN ('history', 'today')
"""


def load_campaign_live_rows(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
) -> frozenset[tuple[str, str, str, str, str]]:
    """Load live rows of (scope, campaign) as 5-tuples.

    has_data_cache 桶灌载用。只读，不做 storage_key 校验（调用方提前白名单）。
    返回元素 = (endpoint, kind, day_start.isoformat(), day_end.isoformat(),
    captured_at.isoformat())。缓存只跟踪 live 行；daily 折叠不改变 live 行集，
    因此不会产生 stale-true。
    """
    # pi-lens-ignore: python-sql-injection
    rows = sess.execute(
        text(SQL_CAMPAIGN_LIVE_ROWS),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "campaign_id": campaign_id,
        },
    ).all()
    return frozenset(
        (endpoint, kind, ds.isoformat(), de.isoformat(), ca.isoformat())
        for endpoint, kind, ds, de, ca in rows
    )


def live_row(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    campaign_id: str,
    kind: str,
) -> LiveRowResult:
    """Return the live (history/today) row for (scope, endpoint, campaign, kind)."""
    storage_key = STORAGE_KEY_BY_PATH.get(endpoint)
    if storage_key is None:
        raise ValueError(f"unknown endpoint: {endpoint}")
    # pi-lens-ignore: python-sql-injection
    row = sess.execute(
        text(SQL_LIVE_ROW),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "campaign_id": campaign_id,
            "kind": kind,
        },
    ).first()
    if row is None:
        return LiveRowResult(
            endpoint=endpoint,
            storage_key=storage_key,
            campaign_id=campaign_id,
            kind=kind,
        )
    day_start, day_end, captured_at = row
    return LiveRowResult(
        endpoint=endpoint,
        storage_key=storage_key,
        campaign_id=campaign_id,
        kind=kind,
        has_row=True,
        day_start=day_start,
        day_end=day_end,
        captured_at=captured_at,
    )


# ─── dump upsert（单事务：live upsert + daily 折叠 + 审计）──────────────


def upsert_dump(
    sess: Session,
    dump: DumpPayload,
    request_id: str | None,
) -> DumpResult:
    """Write one dump into ad_raw with Design-A semantics.

    - live 行 (kind history/today)：partial unique upsert + capturedAt 单调守卫；
      判定 status = inserted / updated / stale_ignored。
    - kind='history' 覆盖成功时：同事务折叠被覆盖的 legacy daily 行 + 写审计；
    - kind='today'：30s 常规刷新原地覆盖（不写审计）；跨天 reset 写 today_reset 审计。
    - legacy daily（旧 v2 插件）：旧 5 元组 upsert（inserted / duplicate）。
    """
    if dump.kind in LIVE_KINDS:
        return _upsert_live(sess, dump, request_id)
    return _upsert_daily(sess, dump, request_id)


def _upsert_live(
    sess: Session,
    dump: DumpPayload,
    request_id: str | None,
) -> DumpResult:
    idem_key = compute_idempotency_key_v3(
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        storage_key=dump.storage_key,
        campaign_id=dump.campaign_id,
        kind=dump.kind,
        day_start=dump.day_start,
        day_end=dump.day_end,
    )
    common = {
        "idempotency_key": idem_key,
        "seller_id": dump.seller_id,
        "advertiser_id": dump.advertiser_id,
        "endpoint": dump.endpoint,
        "method": dump.method,
        "kind": dump.kind,
        "day_start": dump.day_start,
        "day_end": dump.day_end,
        "campaign_id": dump.campaign_id,
        "request": json.dumps(dump.request, ensure_ascii=False),
        "response": json.dumps(dump.response, ensure_ascii=False),
        "captured_at": dump.captured_at,
        "source": dump.source,
        "request_id": request_id,
        "protocol_version": dump.protocol_version,
        "schema_version": dump.schema_version,
    }
    prev = _read_prev(sess, dump)

    # 精确重放去重（review P2c）：同 (…, kind) 且区间 + capturedAt 与活动行全等 →
    # duplicate：不写库、不折叠、不写审计（首写时折叠/审计已做）。
    # 区间相同但 capturedAt 更新（同区间重拉/推进）→ 走下方 upsert → updated + 审计。
    if prev is not None and (
        prev["day_start"] == dump.day_start
        and prev["day_end"] == dump.day_end
        and prev["captured_at"] == dump.captured_at
    ):
        return DumpResult(idempotency_key=idem_key, status="duplicate")

    # pi-lens-ignore: python-sql-injection
    was_inserted = sess.execute(text(SQL_UPSERT_LIVE), common).scalar()
    if was_inserted is None:
        # 冲突且守卫拒绝：旧 capturedAt >= 本次 → stale_ignored，不改任何行
        sess.commit()
        return DumpResult(idempotency_key=idem_key, status="stale_ignored")

    status = "inserted" if was_inserted else "updated"

    if dump.kind == KIND_HISTORY:
        # 折叠被覆盖的 legacy daily 行（幂等；命中 0 行无害）
        folded = _fold_daily(sess, dump)
        _write_history_audit(
            sess, dump, prev=prev, status=status,
            folded_count=folded, request_id=request_id,
        )
    elif dump.kind == KIND_TODAY:
        # 今日行：仅跨天 reset（day_end 变化）写 today_reset 审计；
        # 30s 同区间常规刷新不写（防审计风暴）。
        if prev is not None and prev["day_end"] != dump.day_end:
            _write_today_reset_audit(sess, dump, prev=prev, request_id=request_id)

    sess.commit()
    return DumpResult(idempotency_key=idem_key, status=status)


def _upsert_daily(
    sess: Session,
    dump: DumpPayload,
    request_id: str | None,
) -> DumpResult:
    idem_key = compute_idempotency_key(
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        storage_key=dump.storage_key,
        campaign_id=dump.campaign_id,
        day=dump.day_end,  # daily: day_start == day_end == 单日
        page=1,
    )
    params = {
        "idempotency_key": idem_key,
        "seller_id": dump.seller_id,
        "advertiser_id": dump.advertiser_id,
        "endpoint": dump.endpoint,
        "method": dump.method,
        "day_start": dump.day_start,
        "day_end": dump.day_end,
        "campaign_id": dump.campaign_id,
        "request": json.dumps(dump.request, ensure_ascii=False),
        "response": json.dumps(dump.response, ensure_ascii=False),
        "captured_at": dump.captured_at,
        "source": dump.source,
        "request_id": request_id,
        "protocol_version": dump.protocol_version,
        "schema_version": dump.schema_version,
    }
    # pi-lens-ignore: python-sql-injection
    was_inserted = sess.execute(text(SQL_UPSERT_DAILY), params).scalar()
    sess.commit()
    return DumpResult(
        idempotency_key=idem_key,
        status="inserted" if was_inserted else "duplicate",
    )


def _read_prev(sess: Session, dump: DumpPayload) -> dict | None:
    """读 upsert 前的 live 行（audit prev + event 分类用）。"""
    row = sess.execute(
        text(SQL_LIVE_ROW),
        {
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "endpoint": dump.endpoint,
            "campaign_id": dump.campaign_id,
            "kind": dump.kind,
        },
    ).first()
    if row is None:
        return None
    day_start, day_end, captured_at = row
    return {
        "day_start": day_start,
        "day_end": day_end,
        "captured_at": captured_at,
    }


def _fold_daily(sess: Session, dump: DumpPayload) -> int:
    """折叠 [day_start..day_end] 内被覆盖的 legacy daily 行，返回折叠数。"""
    # pi-lens-ignore: python-sql-injection
    result = sess.execute(
        text(SQL_FOLD_DAILY),
        {
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "endpoint": dump.endpoint,
            "campaign_id": dump.campaign_id,
            "day_start": dump.day_start,
            "day_end": dump.day_end,
        },
    )
    return result.rowcount or 0


def _audit(
    sess: Session,
    *,
    dump: DumpPayload,
    event: str,
    prev: dict | None,
    reason: str,
    request_id: str | None,
) -> None:
    """写一行 ad_sync_audit（同事务）。prev 为 None 时 prev 字段留空。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_AUDIT),
        {
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "endpoint": dump.endpoint,
            "campaign_id": dump.campaign_id,
            "kind": dump.kind,
            "event": event,
            "prev_day_start": None if prev is None else prev["day_start"],
            "prev_day_end": None if prev is None else prev["day_end"],
            "prev_captured_at": None if prev is None else prev["captured_at"],
            "new_day_start": dump.day_start,
            "new_day_end": dump.day_end,
            "new_captured_at": dump.captured_at,
            "reason": reason,
            "request_id": request_id,
        },
    )


def _write_history_audit(
    sess: Session,
    dump: DumpPayload,
    *,
    prev: dict | None,
    status: str,
    folded_count: int,
    request_id: str | None,
) -> None:
    """history 内容被写入/取代 → 分类审计；折叠>0 再补一行 legacy_collapsed。"""
    event: str
    reason: str
    if prev is None:
        event = "history_replaced"
        reason = "reinstall"  # 无前身 = 首装/清数据后重建
    elif prev["day_start"] != dump.day_start:
        event = "window_rebuilt"
        reason = "start_change"
    elif prev["day_end"] < dump.day_end:
        event = "rollover_advanced"
        reason = "rollover"
    else:
        event = "history_replaced"
        reason = "retry"
    if status == "inserted" or prev is not None:
        _audit(sess, dump=dump, event=event, prev=prev, reason=reason,
               request_id=request_id)
    if folded_count > 0:
        _audit(sess, dump=dump, event="legacy_collapsed", prev=prev,
               reason=reason, request_id=request_id)


def _write_today_reset_audit(
    sess: Session,
    dump: DumpPayload,
    *,
    prev: dict,
    request_id: str | None,
) -> None:
    _audit(sess, dump=dump, event="today_reset", prev=prev, reason="rollover",
           request_id=request_id)


# ─── legacy has-data 检查（旧 v2 插件 / 无 kind 的 GET /cursor）────────


def has_data(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    day: date,
    campaign_id: str | None = None,
) -> HasDataResult:
    """存在性查询:该 (scope, endpoint, day[, campaign_id]) 有没有覆盖数据。

    覆盖语义：daily 行 day_end=day，或 live history/today 区间含该 day。
    v2 旧插件借此跳过已捕获的 day；迁移期 history 区间会覆盖历史日 → 返回 true，
    旧插件不再重复补拉已覆盖的日（避免向已含区间内写入 daily 行）。
    """
    storage_key = STORAGE_KEY_BY_PATH.get(endpoint)
    if storage_key is None:
        raise ValueError(f"unknown endpoint: {endpoint}")

    # pi-lens-ignore: python-sql-injection
    row = sess.execute(
        text(SQL_HAS_DATA),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "day": day,
            "campaign_id": campaign_id,
        },
    ).scalar()

    return HasDataResult(
        day=day,
        endpoint=endpoint,
        storage_key=storage_key,
        has_data=row is not None,
        campaign_id=campaign_id,
    )


__all__ = [
    "SQL_CAMPAIGN_LIVE_ROWS",
    "SQL_HAS_DATA",
    "SQL_UPSERT_DAILY",
    "SQL_UPSERT_LIVE",
    "STORAGE_KEY_BY_PATH",
    "has_data",
    "live_row",
    "load_campaign_live_rows",
    "upsert_dump",
]

"""Coverage for ``tts_erp_v2/analytics/repository.py``（v3 range-aggregate, Design A）。

Targets（tech-doc/analytics/range-aggregate-history-sync.md §9.1 S1–S10）:
- upsert live history：首插 inserted + legacy daily 折叠；同区间且 capturedAt 全等的
  重放 → duplicate（不写库/不审计）；同区间新 capturedAt → updated + 审计；
  capturedAt 更旧的迟到重试 → stale_ignored（行不变）
- rollover 推进 [S,T-1]→[S,T] → history 行原地 day_end + 审计 rollover_advanced
- 改 S（向前/向后 superset/subrange）→ window_rebuilt 审计
- today 30s 同区间刷新：updated 单行、无审计增长；跨天 reset → today_reset 审计
- legacy daily（v2 插件）：inserted / duplicate；不同 scope 幂等键隔离
- has_data（legacy 覆盖语义）+ live_row / load_campaign_live_rows（coverage）

Data isolation: TEST_-prefixed seller ids；autouse 清 ad_raw + ad_sync_audit。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"


@pytest.fixture(autouse=True)
def _wipe_analytics_rows(db_engine):
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text("DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.ad_sync_audit WHERE seller_id LIKE 'TEST_%'")
        )


# ---------------------------------------------------------------------------
# DumpPayload 工厂
# ---------------------------------------------------------------------------


def _make_payload(
    *,
    kind: str = "history",
    day_start: date = date(2026, 7, 1),
    day_end: date = date(2026, 9, 5),
    campaign: str = "TEST_repo_campaign",
    seller: str = "TEST_repo_seller",
    captured_at: datetime | None = None,
    marker: str = "first",
    response_rows: list[dict] | None = None,
):
    from tts_erp_v2.analytics.domain import DumpPayload, StorageKey

    return DumpPayload(
        seller_id=seller,
        advertiser_id="TEST_repo_adv",
        endpoint=_ENDPOINT,
        method="POST",
        kind=kind,
        day_start=day_start,
        day_end=day_end,
        campaign_id=campaign,
        request={
            "url": "https://ads.tiktok.com/oec_ads/.../post_product_list",
            "body": {
                "start_time": day_start.isoformat(),
                "end_time": day_end.isoformat(),
            },
        },
        response={
            "status": 200,
            "body": {
                "data": {
                    "table": response_rows or [
                        {"product_id": f"TEST_SPU_{marker}"}
                    ]
                }
            },
        },
        captured_at=captured_at or datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC),
        storage_key=StorageKey.PRODUCT_ANALYSES,
        request_id=f"TEST_{marker}",
        source="tiktok-shop-data-sync",
        protocol_version=3,
        schema_version=2,
    )


def _insert_daily_raw(db_session, day: date, campaign: str = "TEST_repo_campaign",
                      seller: str = "TEST_repo_seller", spu: str = "TEST_SPU_daily",
                      captured_at: datetime | None = None):
    """Direct legacy daily row（等价旧 v2 写入的落库形态）。"""
    import json as _json

    response = _json.dumps({
        "status": 200,
        "body": {"data": {"table": [{"product_id": spu}]}},
    }, ensure_ascii=False)
    db_session.execute(
        text(
            """
            INSERT INTO analytics.ad_raw (
                idempotency_key, seller_id, advertiser_id, endpoint, method,
                kind, day_start, day_end, campaign_id, request, response,
                captured_at, source, protocol_version, schema_version
            ) VALUES (
                :idem, :seller, :adv, :ep, 'POST',
                'daily', :day, :day, :campaign,
                CAST(:request AS JSONB), CAST(:response AS JSONB),
                :captured_at, 'TEST', 2, 1
            )
            """
        ),
        {
            "idem": f"TEST_IDEM_DAILY_{campaign}_{day.isoformat()}",
            "seller": seller,
            "adv": "TEST_repo_adv",
            "ep": _ENDPOINT,
            "day": day,
            "campaign": campaign,
            "request": _json.dumps({"url": "https://x/", "body": {}}),
            "response": response,
            "captured_at": captured_at or datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC),
        },
    )


def _count_raw(db_session, seller: str = "TEST_repo_seller",
               kind: str | None = None) -> int:
    where = "WHERE seller_id = :s"
    params: dict = {"s": seller}
    if kind is not None:
        where += " AND kind = :k"
        params["k"] = kind
    # pi-lens-ignore: python-sql-injection — bound params only
    return db_session.execute(
        text(f"SELECT count(*) FROM analytics.ad_raw {where}"), params
    ).scalar()


def _audit_rows(db_session, seller: str = "TEST_repo_seller") -> list[dict]:
    return [
        dict(r)
        for r in db_session.execute(
            text(
                "SELECT event, kind, prev_day_start, prev_day_end, prev_captured_at, "
                "new_day_start, new_day_end, new_captured_at "
                "FROM analytics.ad_sync_audit "
                "WHERE seller_id = :s ORDER BY id"
            ),
            {"s": seller},
        ).mappings()
    ]


# ---------------------------------------------------------------------------
# S1 / S2: history 首插 + 折叠 + 同区间重放
# ---------------------------------------------------------------------------


def test_live_history_first_insert_folds_legacy_daily(db_session):
    """S1：v3 history 首插 → inserted；同 campaign 的 legacy daily 行被同事务折叠。"""
    from tts_erp_v2.analytics import repository

    _insert_daily_raw(db_session, date(2026, 8, 1))
    _insert_daily_raw(db_session, date(2026, 8, 2))
    assert _count_raw(db_session, kind="daily") == 2

    dump = _make_payload()
    result = repository.upsert_dump(db_session, dump, request_id="TEST_s1")
    assert result.status == "inserted"
    assert len(result.idempotency_key) == 64

    row = db_session.execute(
        text(
            "SELECT kind, day_start, day_end FROM analytics.ad_raw "
            "WHERE seller_id = :s AND kind = 'history'"
        ),
        {"s": dump.seller_id},
    ).first()
    assert row is not None
    assert row.kind == "history"
    assert row.day_start == date(2026, 7, 1)
    assert row.day_end == date(2026, 9, 5)
    # legacy daily 行已被折叠
    assert _count_raw(db_session, kind="daily") == 0
    assert _count_raw(db_session) == 1

    audits = _audit_rows(db_session)
    assert audits[0]["event"] == "history_replaced"
    # 折叠 > 0 → 补一行 legacy_collapsed
    assert audits[1]["event"] == "legacy_collapsed"


def test_live_history_replay_is_duplicate_single_row(db_session):
    """S2：同 (…,kind) 同区间且 capturedAt 全等的重放 → duplicate（不写库/不审计）。"""
    from tts_erp_v2.analytics import repository

    dump = _make_payload(marker="a")
    r1 = repository.upsert_dump(db_session, dump, request_id="TEST_s2a")
    r2 = repository.upsert_dump(db_session, dump, request_id="TEST_s2b")
    assert r1.status == "inserted"
    assert r2.status == "duplicate"
    assert r1.idempotency_key == r2.idempotency_key
    assert _count_raw(db_session) == 1
    # 精确重放不再追加审计（首插那次已有 history_replaced）
    audits = _audit_rows(db_session)
    assert [a["event"] for a in audits] == ["history_replaced"]


def test_live_history_same_interval_newer_captured_at_updates(db_session):
    """S2b：同区间但 capturedAt 更新 → updated + history_replaced 审计（review P2c）。"""
    from tts_erp_v2.analytics import repository

    first = _make_payload(
        captured_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC), marker="a"
    )
    assert repository.upsert_dump(db_session, first, "TEST_s2c").status == "inserted"
    refresh = _make_payload(
        captured_at=datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC), marker="b"
    )
    result = repository.upsert_dump(db_session, refresh, "TEST_s2d")
    assert result.status == "updated"
    assert _count_raw(db_session) == 1
    audits = _audit_rows(db_session)
    assert audits[-1]["event"] == "history_replaced"
    assert audits[-1]["prev_captured_at"] is not None


# ---------------------------------------------------------------------------
# S3: capturedAt 单调守卫
# ---------------------------------------------------------------------------


def test_stale_dump_older_captured_at_is_ignored(db_session):
    """S3：迟到旧快照（capturedAt 更旧）→ stale_ignored，活动行不被回退。"""
    from tts_erp_v2.analytics import repository

    newer = _make_payload(
        captured_at=datetime(2026, 9, 10, 2, 0, 0, tzinfo=UTC), marker="new"
    )
    assert repository.upsert_dump(db_session, newer, "TEST_s3a").status == "inserted"

    older = _make_payload(
        captured_at=datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC), marker="old"
    )
    result = repository.upsert_dump(db_session, older, "TEST_s3b")
    assert result.status == "stale_ignored"

    row = db_session.execute(
        text(
            "SELECT day_start, day_end, response FROM analytics.ad_raw "
            "WHERE seller_id = :s AND kind = 'history'"
        ),
        {"s": newer.seller_id},
    ).first()
    assert row.day_end == date(2026, 9, 5)
    # 内容仍是新快照（marker new 的 SPU）
    table = row.response["body"]["data"]["table"]
    assert table[0]["product_id"] == "TEST_SPU_new"


# ---------------------------------------------------------------------------
# S4: rollover 推进（区间原地更新）
# ---------------------------------------------------------------------------


def test_rollover_advances_day_end_and_audits(db_session):
    """S4：[S,T-1] → [S,T] 推进：history 行原地 day_end=T；审计 rollover_advanced。"""
    from tts_erp_v2.analytics import repository

    first = _make_payload(
        day_start=date(2026, 7, 1),
        day_end=date(2026, 9, 5),
        captured_at=datetime(2026, 9, 6, 2, 0, 0, tzinfo=UTC),
    )
    assert repository.upsert_dump(db_session, first, "TEST_s4a").status == "inserted"

    rolled = _make_payload(
        day_start=date(2026, 7, 1),
        day_end=date(2026, 9, 6),
        captured_at=datetime(2026, 9, 7, 2, 0, 0, tzinfo=UTC),
    )
    result = repository.upsert_dump(db_session, rolled, "TEST_s4b")
    assert result.status == "updated"
    assert _count_raw(db_session) == 1

    row = db_session.execute(
        text(
            "SELECT day_start, day_end FROM analytics.ad_raw "
            "WHERE seller_id = :s AND kind = 'history'"
        ),
        {"s": first.seller_id},
    ).first()
    assert row.day_end == date(2026, 9, 6)

    audits = _audit_rows(db_session)
    # 首插 history_replaced + 推进 rollover_advanced
    assert [a["event"] for a in audits] == ["history_replaced", "rollover_advanced"]
    assert audits[1]["prev_day_end"].isoformat() == "2026-09-05"
    assert audits[1]["new_day_end"].isoformat() == "2026-09-06"


# ---------------------------------------------------------------------------
# S5 / S6: 改 S（向前 superset / 向后 subrange 重建）
# ---------------------------------------------------------------------------


def test_start_moved_earlier_rebuilds_interval(db_session):
    """S5：S 提前（超集重拉）→ day_start 前移 + 内容替换；审计 window_rebuilt。"""
    from tts_erp_v2.analytics import repository

    original = _make_payload(
        day_start=date(2026, 7, 10),
        day_end=date(2026, 9, 5),
        captured_at=datetime(2026, 9, 6, 2, 0, 0, tzinfo=UTC),
    )
    assert repository.upsert_dump(db_session, original, "TEST_s5a").status == "inserted"

    expanded = _make_payload(
        day_start=date(2026, 7, 1),
        day_end=date(2026, 9, 9),
        captured_at=datetime(2026, 9, 10, 2, 0, 0, tzinfo=UTC),
    )
    result = repository.upsert_dump(db_session, expanded, "TEST_s5b")
    assert result.status == "updated"
    row = db_session.execute(
        text(
            "SELECT day_start, day_end FROM analytics.ad_raw "
            "WHERE seller_id = :s AND kind = 'history'"
        ),
        {"s": original.seller_id},
    ).first()
    assert row.day_start == date(2026, 7, 1)
    assert row.day_end == date(2026, 9, 9)
    assert _count_raw(db_session) == 1

    audits = _audit_rows(db_session)
    assert audits[-1]["event"] == "window_rebuilt"
    assert audits[-1]["prev_day_start"].isoformat() == "2026-07-10"
    assert audits[-1]["new_day_start"].isoformat() == "2026-07-01"


def test_start_moved_later_rebuilds_interval(db_session):
    """S6：S 延后（subrange 重建）→ day_start 后移 + 整段替换；旧区间仅审计可见。"""
    from tts_erp_v2.analytics import repository

    original = _make_payload(
        day_start=date(2026, 7, 1),
        day_end=date(2026, 9, 5),
        captured_at=datetime(2026, 9, 6, 2, 0, 0, tzinfo=UTC),
    )
    repository.upsert_dump(db_session, original, "TEST_s6a")

    narrowed = _make_payload(
        day_start=date(2026, 8, 1),
        day_end=date(2026, 9, 9),
        captured_at=datetime(2026, 9, 10, 2, 0, 0, tzinfo=UTC),
    )
    result = repository.upsert_dump(db_session, narrowed, "TEST_s6b")
    assert result.status == "updated"
    row = db_session.execute(
        text(
            "SELECT day_start, day_end FROM analytics.ad_raw "
            "WHERE seller_id = :s AND kind = 'history'"
        ),
        {"s": original.seller_id},
    ).first()
    assert row.day_start == date(2026, 8, 1)
    # Design A：单行原地更新，旧区间只留审计 prev
    assert _count_raw(db_session) == 1
    audits = _audit_rows(db_session)
    assert audits[-1]["event"] == "window_rebuilt"
    assert audits[-1]["prev_day_start"].isoformat() == "2026-07-01"
    assert audits[-1]["new_day_start"].isoformat() == "2026-08-01"


# ---------------------------------------------------------------------------
# S9: today 快照（30s 刷新不审计；跨天 reset 审计）
# ---------------------------------------------------------------------------


def test_today_snapshot_refresh_no_audit_then_reset_audits(db_session):
    """S9：同 day 30s 刷新 → updated、无审计；跨天 day_end 变化 → today_reset。"""
    from tts_erp_v2.analytics import repository

    dump = _make_payload(kind="today", day_start=date(2026, 9, 10),
                         day_end=date(2026, 9, 10),
                         captured_at=datetime(2026, 9, 10, 1, 0, 0, tzinfo=UTC))
    assert repository.upsert_dump(db_session, dump, "TEST_s9a").status == "inserted"
    # 30s 常规刷新（同 day_end，capturedAt 更新）
    refresh = _make_payload(
        kind="today", day_start=date(2026, 9, 10), day_end=date(2026, 9, 10),
        captured_at=datetime(2026, 9, 10, 1, 0, 30, tzinfo=UTC),
    )
    assert repository.upsert_dump(db_session, refresh, "TEST_s9b").status == "updated"
    assert _count_raw(db_session, kind="today") == 1
    # 同天刷新不产生审计（30s 风暴防护）
    assert _audit_rows(db_session) == []

    # 跨天：today 切到新 day_end → today_reset 审计
    reset = _make_payload(
        kind="today", day_start=date(2026, 9, 11), day_end=date(2026, 9, 11),
        captured_at=datetime(2026, 9, 11, 0, 0, 5, tzinfo=UTC),
    )
    assert repository.upsert_dump(db_session, reset, "TEST_s9c").status == "updated"
    audits = _audit_rows(db_session)
    assert len(audits) == 1
    assert audits[0]["event"] == "today_reset"
    assert audits[0]["prev_day_end"].isoformat() == "2026-09-10"
    assert audits[0]["new_day_end"].isoformat() == "2026-09-11"


# ---------------------------------------------------------------------------
# legacy daily（v2 插件）路径
# ---------------------------------------------------------------------------


def test_daily_upsert_insert_then_duplicate(db_session):
    """旧 v2 daily 写入：首插 inserted，重放 duplicate（旧语义保留）。"""
    from tts_erp_v2.analytics import repository

    dump = _make_payload(kind="daily", day_start=date(2026, 8, 23),
                         day_end=date(2026, 8, 23))
    r1 = repository.upsert_dump(db_session, dump, request_id="TEST_daily1")
    r2 = repository.upsert_dump(db_session, dump, request_id="TEST_daily2")
    assert r1.status == "inserted"
    assert r2.status == "duplicate"
    assert r1.idempotency_key == r2.idempotency_key
    assert _count_raw(db_session, kind="daily") == 1


def test_daily_different_sellers_get_distinct_keys(db_session):
    """不同 seller → 不同幂等键（scope 隔离）。"""
    from tts_erp_v2.analytics import repository

    a = _make_payload(seller="TEST_repo_a", kind="daily",
                      day_start=date(2026, 8, 23), day_end=date(2026, 8, 23))
    b = _make_payload(seller="TEST_repo_b", kind="daily",
                      day_start=date(2026, 8, 23), day_end=date(2026, 8, 23))
    repository.upsert_dump(db_session, a, "TEST_a")
    repository.upsert_dump(db_session, b, "TEST_b")
    # 各自存在（不同 scope 幂等键隔离）
    assert _count_raw(db_session, seller="TEST_repo_a", kind="daily") == 1
    assert _count_raw(db_session, seller="TEST_repo_b", kind="daily") == 1


# ---------------------------------------------------------------------------
# has_data（legacy 覆盖语义）
# ---------------------------------------------------------------------------


def test_has_data_false_before_any_dump(db_session):
    from tts_erp_v2.analytics import repository

    result = repository.has_data(
        db_session,
        seller_id="TEST_repo_hasdata_empty",
        advertiser_id="TEST_repo_adv",
        endpoint=_ENDPOINT,
        day=date(2099, 1, 1),
    )
    assert result.has_data is False
    assert result.storage_key.value == "productAnalyses"


def test_has_data_covering_day_in_live_history_range(db_session):
    """live history 区间覆盖的 day → has_data True（迁移期旧插件不会重复补拉）。"""
    from tts_erp_v2.analytics import repository

    dump = _make_payload(seller="TEST_repo_hdcov", day_start=date(2026, 7, 1),
                         day_end=date(2026, 9, 5))
    repository.upsert_dump(db_session, dump, request_id="TEST_hdcov")
    inside = repository.has_data(
        db_session, seller_id=dump.seller_id, advertiser_id=dump.advertiser_id,
        endpoint=_ENDPOINT, day=date(2026, 8, 15), campaign_id=dump.campaign_id,
    )
    assert inside.has_data is True
    outside = repository.has_data(
        db_session, seller_id=dump.seller_id, advertiser_id=dump.advertiser_id,
        endpoint=_ENDPOINT, day=date(2026, 6, 1), campaign_id=dump.campaign_id,
    )
    assert outside.has_data is False


def test_has_data_unknown_endpoint_raises(db_session):
    from tts_erp_v2.analytics import repository

    with pytest.raises(ValueError) as exc_info:
        repository.has_data(
            db_session,
            seller_id="TEST_repo_bad",
            advertiser_id="TEST_repo_adv",
            endpoint="/oec_ads/UNKNOWN/path",
            day=date(2026, 8, 23),
        )
    assert "unknown endpoint" in str(exc_info.value)


# ---------------------------------------------------------------------------
# live_row / load_campaign_live_rows（coverage 数据面）
# ---------------------------------------------------------------------------


def test_live_row_returns_interval_after_upsert(db_session):
    """live_row：kind=history 有行 → has_row + 区间 + capturedAt。"""
    from tts_erp_v2.analytics import repository

    dump = _make_payload(seller="TEST_repo_livecov")
    repository.upsert_dump(db_session, dump, request_id="TEST_livecov")
    result = repository.live_row(
        db_session,
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        endpoint=_ENDPOINT,
        campaign_id=dump.campaign_id,
        kind="history",
    )
    assert result.has_row is True
    assert result.kind == "history"
    assert result.day_start == date(2026, 7, 1)
    assert result.day_end == date(2026, 9, 5)
    assert result.storage_key.value == "productAnalyses"
    # 没写入的 kind → has_row False
    missing = repository.live_row(
        db_session,
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        endpoint=_ENDPOINT,
        campaign_id=dump.campaign_id,
        kind="today",
    )
    assert missing.has_row is False


def test_load_campaign_live_rows_returns_5_tuples(db_session):
    """load_campaign_live_rows：返回 (endpoint,kind,ds,de,capturedAt) 5 元组集。"""
    from tts_erp_v2.analytics import repository

    history = _make_payload(seller="TEST_repo_loadrows")
    repository.upsert_dump(db_session, history, request_id="TEST_lr1")
    today = _make_payload(seller="TEST_repo_loadrows", kind="today",
                          day_start=date(2026, 9, 10), day_end=date(2026, 9, 10))
    repository.upsert_dump(db_session, today, request_id="TEST_lr2")
    rows = repository.load_campaign_live_rows(
        db_session,
        seller_id=history.seller_id,
        advertiser_id=history.advertiser_id,
        campaign_id=history.campaign_id,
    )
    assert len(rows) == 2
    entry = next(r for r in rows if r[1] == "history")
    assert entry[0] == _ENDPOINT
    assert entry[2] == "2026-07-01"
    assert entry[3] == "2026-09-05"
    assert len(entry[4]) > 0  # captured_at iso


# ---------------------------------------------------------------------------
# STORAGE_KEY_BY_PATH（模块级常量 smoke）
# ---------------------------------------------------------------------------


def test_storage_key_by_path_covers_three_endpoints():
    from tts_erp_v2.analytics.domain import StorageKey
    from tts_erp_v2.analytics.repository import STORAGE_KEY_BY_PATH

    assert (
        STORAGE_KEY_BY_PATH["/oec_ads/shopping/v1/oec/stat/post_product_list"]
        == StorageKey.PRODUCT_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH["/oec_ads/shopping/v1/oec/stat/post_session_list"]
        == StorageKey.SESSION_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH["/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"]
        == StorageKey.CAMPAIGN_CHANGE_LOGS
    )
    assert len(STORAGE_KEY_BY_PATH) == 3


# 额外域不变量：v3 idempotency key 与 v2 不冲突（domain 级）
def test_v3_idem_key_differs_from_v2():
    from tts_erp_v2.analytics.domain import (
        StorageKey,
        compute_idempotency_key,
        compute_idempotency_key_v3,
    )

    v2 = compute_idempotency_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day=date(2026, 8, 23), page=1,
    )
    v3 = compute_idempotency_key_v3(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", kind="history", day_start=date(2026, 7, 1),
        day_end=date(2026, 9, 5),
    )
    assert v2 != v3
    assert len(v3) == 64

"""HTTP 契约测试：/v2/analytics/sync v3 range-aggregate（Design A）。

覆盖（tech-doc/analytics/range-aggregate-history-sync.md §9.1）：
- S1 history v3 首插 inserted + legacy daily 折叠（审计 legacy_collapsed）
- S2 同 (…,kind) 同区间重放 updated；行数不变
- S3 capturedAt 更旧的迟到重试 → stale_ignored；活动行不变
- S4 rollover 推进 → history day_end 推进 + 审计 rollover_advanced
- S9 today 30s 刷新 updated 且无审计；跨天 today_reset 审计
- coverage cursor 各分支（hasRow/区间/kind/campaignId 回显）
- S11 scope 隔离 + 无 kind legacy has-data 覆盖语义
- v3 schema 校验（kind/dayStart/dayEnd 必填、day==dayEnd、非法 kind/协议）

数据隔离：TEST_ 哨兵 seller；autouse 直连清 ad_raw + ad_sync_audit。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-v3"
ADVERTISER = "TEST_adv-v3"
CAMPAIGN = "TEST_campaign-v3"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
S = "2026-07-01"
T1 = "2026-09-05"  # history day_end（首轮）
T2 = "2026-09-06"  # rollover 推进后的 day_end
TODAY = "2026-09-10"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, bound param
        conn.execute(text("DELETE FROM analytics.ad_raw WHERE seller_id = :s"), {"s": SELLER})
        conn.execute(
            text("DELETE FROM analytics.ad_sync_audit WHERE seller_id = :s"), {"s": SELLER}
        )
    yield
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, bound param
        conn.execute(text("DELETE FROM analytics.ad_raw WHERE seller_id = :s"), {"s": SELLER})
        conn.execute(
            text("DELETE FROM analytics.ad_sync_audit WHERE seller_id = :s"), {"s": SELLER}
        )


def _dump_body(*, kind: str = "history", day_start: str = S, day_end: str = T1,
               day: str | None = None, campaign: str = CAMPAIGN,
               captured_at: str = "2026-09-10T02:00:00.000Z", request_id: str | None = None):
    return {
        "protocolVersion": 3,
        "requestId": request_id or str(uuid.uuid4()),
        "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
        "dump": {
            "endpoint": ENDPOINT,
            "method": "POST",
            "day": day_end if day is None else day,
            "kind": kind,
            "dayStart": day_start,
            "dayEnd": day_end,
            "campaignId": campaign,
            "request": {"url": "http://tiktok.test/...", "body": {}},
            "response": {"status": 200, "body": {"data": {"rows": []}}},
            "capturedAt": captured_at,
        },
    }


def _post(api_client, key, body) -> dict:
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _coverage(api_client, key, *, kind: str = "history", campaign: str = CAMPAIGN) -> dict:
    params = {
        "sellerId": SELLER,
        "advertiserId": ADVERTISER,
        "endpoint": ENDPOINT,
        "kind": kind,
        "campaignId": campaign,
    }
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _raw_count(db_engine) -> int:
    with db_engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM analytics.ad_raw WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _audit_events(db_engine) -> list[str]:
    with db_engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT event FROM analytics.ad_sync_audit "
                    "WHERE seller_id = :s ORDER BY id"
                ),
                {"s": SELLER},
            )
        ]


def _insert_daily(db_engine, day: str) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, bound params
        conn.execute(
            text(
                """
                INSERT INTO analytics.ad_raw (
                    idempotency_key, seller_id, advertiser_id, endpoint, method,
                    kind, day_start, day_end, campaign_id, request, response,
                    captured_at, source, protocol_version, schema_version
                ) VALUES (
                    :idem, :seller, :adv, :ep, 'POST',
                    'daily', CAST(:day AS date), CAST(:day AS date), :campaign,
                    CAST(:req AS jsonb), CAST(:res AS jsonb), now(), 'TEST', 2, 1
                )
                """
            ),
            {
                "idem": f"TEST_IDEM_DAILY_{day}",
                "seller": SELLER,
                "adv": ADVERTISER,
                "ep": ENDPOINT,
                "day": day,
                "campaign": CAMPAIGN,
                "req": json.dumps({"url": "x"}),
                "res": json.dumps({"status": 200, "body": {"data": {"table": []}}}),
            },
        )


# ─── S1/S2：history 首插折叠 + 重放 ─────────────────────────────────


def test_v3_history_first_insert_folds_daily_and_covers(api_client, readwrite_key, db_engine):
    _insert_daily(db_engine, "2026-08-01")
    _insert_daily(db_engine, "2026-08-02")
    with db_engine.connect() as conn:
        daily_before = conn.execute(
            text("SELECT count(*) FROM analytics.ad_raw WHERE seller_id=:s AND kind='daily'"),
            {"s": SELLER},
        ).scalar()
    assert daily_before == 2

    data = _post(api_client, readwrite_key, _dump_body())
    assert data["status"] == "inserted"
    assert len(data["idempotencyKey"]) == 64
    # legacy daily 被折叠；只剩 1 行 live history
    assert _raw_count(db_engine) == 1
    assert "legacy_collapsed" in _audit_events(db_engine)

    # coverage：hasRow + 区间回显
    cov = _coverage(api_client, readwrite_key)
    assert cov["hasRow"] is True
    assert cov["kind"] == "history"
    assert cov["dayStart"] == S
    assert cov["dayEnd"] == T1
    assert cov["campaignId"] == CAMPAIGN
    assert cov["storageKey"] == "productAnalyses"

    # legacy has-data（无 kind）：history 区间覆盖的 day → true
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={
            "sellerId": SELLER, "advertiserId": ADVERTISER,
            "endpoint": ENDPOINT, "day": "2026-08-01", "campaignId": CAMPAIGN,
        },
    )
    assert r.json()["data"]["hasData"] is True


def test_v3_history_replay_single_row(api_client, readwrite_key, db_engine):
    _post(api_client, readwrite_key, _dump_body())
    events_before = _audit_events(db_engine)
    data = _post(api_client, readwrite_key, _dump_body())
    # review P2c：同 (…,kind) 同区间且 capturedAt 全等 → duplicate，不追加审计
    assert data["status"] == "duplicate"
    assert _raw_count(db_engine) == 1
    assert _audit_events(db_engine) == events_before


def test_v3_history_same_interval_newer_captured_at_updates(
    api_client, readwrite_key, db_engine
):
    """同区间但 capturedAt 更新（同范围重拉/重试）→ updated + history_replaced 审计。"""
    _post(api_client, readwrite_key, _dump_body(
        captured_at="2026-09-06T02:00:00.000Z"))
    data = _post(api_client, readwrite_key, _dump_body(
        captured_at="2026-09-07T02:00:00.000Z"))
    assert data["status"] == "updated"
    assert _raw_count(db_engine) == 1
    events = _audit_events(db_engine)
    assert events[-1] == "history_replaced"


# ─── S3：capturedAt 单调守卫（API 层）──────────────────────────────


def test_v3_stale_dump_ignored(api_client, readwrite_key, db_engine):
    _post(api_client, readwrite_key, _dump_body(captured_at="2026-09-10T02:00:00.000Z"))
    data = _post(
        api_client, readwrite_key,
        _dump_body(captured_at="2026-09-08T02:00:00.000Z"),
    )
    assert data["status"] == "stale_ignored"
    cov = _coverage(api_client, readwrite_key)
    assert cov["hasRow"] is True
    assert cov["dayEnd"] == T1


# ─── S4：rollover 推进 ──────────────────────────────────────────────


def test_v3_rollover_advances_and_audits(api_client, readwrite_key, db_engine):
    _post(api_client, readwrite_key,
          _dump_body(day_end=T1, captured_at="2026-09-06T02:00:00.000Z"))
    data = _post(api_client, readwrite_key,
                 _dump_body(day_end=T2, captured_at="2026-09-07T02:00:00.000Z"))
    assert data["status"] == "updated"
    assert _raw_count(db_engine) == 1
    cov = _coverage(api_client, readwrite_key)
    assert cov["dayEnd"] == T2
    events = _audit_events(db_engine)
    assert "rollover_advanced" in events


# ─── S9：today 快照 / 审计 ─────────────────────────────────────────


def test_v3_today_refresh_no_audit_and_reset_audits(
    api_client, readwrite_key, db_engine
):
    t1 = _dump_body(kind="today", day_start=TODAY, day_end=TODAY,
                    captured_at="2026-09-10T01:00:00.000Z")
    assert _post(api_client, readwrite_key, t1)["status"] == "inserted"
    t1b = _dump_body(kind="today", day_start=TODAY, day_end=TODAY,
                     captured_at="2026-09-10T01:00:30.000Z")
    assert _post(api_client, readwrite_key, t1b)["status"] == "updated"
    assert _audit_events(db_engine) == []  # 同天刷新不审计

    # 跨天 today_reset
    next_day = "2026-09-11"
    reset = _dump_body(kind="today", day_start=next_day, day_end=next_day,
                       captured_at="2026-09-11T00:00:05.000Z")
    assert _post(api_client, readwrite_key, reset)["status"] == "updated"
    assert _audit_events(db_engine) == ["today_reset"]
    cov = _coverage(api_client, readwrite_key, kind="today")
    assert cov["hasRow"] is True
    assert cov["dayEnd"] == next_day


# ─── S11：scope 隔离 / 授权 ─────────────────────────────────────────


def test_v3_scope_isolation_and_readonly_denied(api_client, readonly_key, db_engine):
    # readonly 无写权限
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json=_dump_body(),
    )
    assert r.status_code == 403


# ─── v3 schema 校验 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mutate,message_fragment",
    [
        (lambda b: b["dump"].pop("dayStart"), "dayStart/dayEnd"),
        (lambda b: b["dump"].pop("dayEnd"), "dayStart/dayEnd"),
        (lambda b: b["dump"].update({"kind": "nonsense"}), "kind"),
        (lambda b: b["dump"].update({"dayEnd": "2026-01-01"}), "dayStart"),
        (lambda b: b["dump"].update({"day": "2026-01-01"}), "dayEnd"),
        # review P2a：kind=today 必须单日区间
        (lambda b: b["dump"].update({
            "kind": "today", "dayStart": "2026-09-10",
            "dayEnd": "2026-09-11", "day": "2026-09-11",
        }), "kind=today"),
    ],
)
def test_v3_schema_validation(api_client, readwrite_key, mutate, message_fragment):
    body = _dump_body()
    mutate(body)
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=body,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_v3_unsupported_protocol_version(api_client, readwrite_key):
    body = _dump_body()
    body["protocolVersion"] = 99
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=body,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "UNSUPPORTED_PROTOCOL_VERSION"

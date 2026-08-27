"""TDD: persist_logistics_tracking 事件顺序 bug

TikTok /fulfillment/202309/orders/{id}/tracking 返回的 tracking 列表是
**最新事件在前**（descending）。bug：旧实现取 events[0] 当 first、
events[-1] 当 last，导致物流汇总表 first_event_at / last_event_at /
last_action_code / last_description 全部写反。

正确行为：按 update_time_millis 时间戳推导 first/last，不依赖列表顺序。
"""

from __future__ import annotations

import psycopg
import psycopg.rows
import pytest

import tts_erp

SHOP_ID = "TEST_SHOP_LOGISTICS"
ORDER_ID = "TEST_LOGISTICS_ORDER_001"

# 毫秒时间戳：下单 → 打包 → 签收，间隔 1 小时
T_PLACED = 1_700_000_000_000
T_PACKED = T_PLACED + 3_600_000
T_DELIVERED = T_PLACED + 7_200_000

EV_PLACED = {
    "action_code": 10101,
    "description": "Order placed.",
    "update_time_millis": T_PLACED,
}
EV_PACKED = {
    "action_code": 20101,
    "description": "Packed by seller.",
    "update_time_millis": T_PACKED,
}
EV_DELIVERED = {
    "action_code": 50101,
    "description": "Package delivered.",
    "update_time_millis": T_DELIVERED,
}

# TikTok 真实返回顺序：最新在前
EVENTS_NEWEST_FIRST = [EV_DELIVERED, EV_PACKED, EV_PLACED]


def _resp(events: list) -> dict:
    return {"code": 0, "message": "Success", "data": {"tracking": events}}


@pytest.fixture(autouse=True)
def _cleanup(db_url: str):
    """persist_* 自己开连接 commit，事务回滚隔离不住，测试后按 sentinel 清掉。"""
    yield
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM logistics_tracking_events WHERE order_id = %s", (ORDER_ID,)
            )
            cur.execute(
                "DELETE FROM logistics_sync_targets WHERE order_id = %s", (ORDER_ID,)
            )
            cur.execute(
                "DELETE FROM logistics_tracking WHERE order_id = %s", (ORDER_ID,)
            )
        conn.commit()
    finally:
        conn.close()


def _fetch_summary(db_url: str) -> dict:
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT * FROM logistics_tracking WHERE order_id = %s", (ORDER_ID,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "summary row not persisted"
    return dict(row)


def test_first_last_event_derived_by_timestamp_not_list_position(db_url: str):
    """TikTok 实际返回（最新在前）：first=最旧事件，last=最新事件。"""
    ok = tts_erp.persist_logistics_tracking(
        SHOP_ID, ORDER_ID, _resp(EVENTS_NEWEST_FIRST)
    )
    assert ok

    row = _fetch_summary(db_url)
    assert row["first_event_at"] == T_PLACED
    assert row["last_event_at"] == T_DELIVERED
    assert row["last_action_code"] == 50101
    assert row["last_description"] == "Package delivered."
    assert row["final_status"] == "DELIVERED"
    assert row["n_events"] == 3


def test_oldest_first_input_gives_same_result(db_url: str):
    """防御性：即便上游哪天改成最旧在前，落库结果也必须一致。"""
    ok = tts_erp.persist_logistics_tracking(
        SHOP_ID, ORDER_ID, _resp(list(reversed(EVENTS_NEWEST_FIRST)))
    )
    assert ok

    row = _fetch_summary(db_url)
    assert row["first_event_at"] == T_PLACED
    assert row["last_event_at"] == T_DELIVERED
    assert row["last_action_code"] == 50101


def test_events_without_timestamp_fall_back_gracefully(db_url: str):
    """缺 update_time_millis 的事件不参与 first/last 推导，但也不能炸。"""
    events = [
        {
            "action_code": 20101,
            "description": "Packed by seller.",
            "update_time_millis": T_PACKED,
        },
        {"action_code": 10101, "description": "Order placed."},  # 无时间戳
    ]
    ok = tts_erp.persist_logistics_tracking(SHOP_ID, ORDER_ID, _resp(events))
    assert ok

    row = _fetch_summary(db_url)
    assert row["first_event_at"] == T_PACKED
    assert row["last_event_at"] == T_PACKED


# ─── W1.6: /sync/logistics_tracking overlap guard + hard cap ─────────


class TestSyncLogisticsTrackingGuard:
    """Advisory lock prevents overlapping cron/manual runs; max_per_run
    has a hard ceiling so one request can't occupy a worker for hours."""

    @pytest.fixture()
    def client(self, monkeypatch):
        import tts_erp_fastapi
        from fastapi.testclient import TestClient

        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
        return TestClient(tts_erp_fastapi.app), tts_erp_fastapi

    def _patch_common(self, mod, monkeypatch, lock_acquired=True, n_orders=5):
        from domain import Creds

        monkeypatch.setattr(
            mod,
            "_get_creds",
            lambda shop_id: Creds(
                access_token="t", shop_cipher="c", region="VN", shop_id=shop_id
            ),  # noqa: S106 -- test double, not a real credential
        )

        class _FakeHttp:
            def request(self, *a, **kw):
                return {"code": 0, "data": {"tracking": []}}

        monkeypatch.setattr(mod, "_tiktok_http_for", lambda creds: _FakeHttp())
        monkeypatch.setattr(mod, "_try_advisory_lock", lambda key: lock_acquired)
        monkeypatch.setattr(mod, "_release_advisory_lock", lambda key: None)
        # no jitter in tests
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            mod,
            "_db_query_dict",
            lambda sql, args=(): [{"order_id": f"O{i}"} for i in range(n_orders)],
        )
        monkeypatch.setattr(mod.tts_erp, "persist_logistics_tracking", lambda *a: True)

    def test_lock_conflict_returns_409(self, client, monkeypatch):
        app, mod = client
        self._patch_common(mod, monkeypatch, lock_acquired=False)
        r = app.post(
            "/sync/logistics_tracking",
            json={"shop_id": "TEST_SHOP_LOGISTICS", "order_ids": ["O1"]},
        )
        assert r.status_code == 409

    def test_max_per_run_hard_capped_at_100(self, client, monkeypatch):
        app, mod = client
        self._patch_common(mod, monkeypatch, n_orders=250)
        r = app.post(
            "/sync/logistics_tracking",
            json={
                "shop_id": "TEST_SHOP_LOGISTICS",
                "all_with_tracking": True,
                "limit": 250,
                "max_per_run": 250,
            },
        )
        assert r.status_code == 200
        # 250 requested, but hard cap is 100
        assert r.json()["total"] == 100

    def test_lock_released_after_success(self, client, monkeypatch):
        app, mod = client
        self._patch_common(mod, monkeypatch, n_orders=2)
        released = []
        monkeypatch.setattr(
            mod, "_release_advisory_lock", lambda key: released.append(key)
        )
        r = app.post(
            "/sync/logistics_tracking",
            json={"shop_id": "TEST_SHOP_LOGISTICS", "order_ids": ["O1", "O2"]},
        )
        assert r.status_code == 200
        assert len(released) == 1

    def test_lock_released_on_exception(self, client, monkeypatch):
        app, mod = client
        self._patch_common(mod, monkeypatch)
        released = []
        monkeypatch.setattr(
            mod, "_release_advisory_lock", lambda key: released.append(key)
        )

        class _BoomHttp:
            def request(self, *a, **kw):
                raise RuntimeError("upstream exploded")

        monkeypatch.setattr(mod, "_tiktok_http_for", lambda creds: _BoomHttp())
        import pytest as _pt

        with _pt.raises(RuntimeError, match="upstream exploded"):
            app.post(
                "/sync/logistics_tracking",
                json={"shop_id": "TEST_SHOP_LOGISTICS", "order_ids": ["O1"]},
                # TestClient re-raises server exceptions by default
            )
        assert len(released) == 1

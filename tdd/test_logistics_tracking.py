"""TDD: persist_logistics_tracking 事件顺序 bug

TikTok /fulfillment/202309/orders/{id}/tracking 返回的 tracking 列表是
**最新事件在前**（descending）。bug：旧实现取 events[0] 当 first、
events[-1] 当 last，导致物流汇总表 first_event_at / last_event_at /
last_action_code / last_description 全部写反。

正确行为：按 update_time_millis 时间戳推导 first/last，不依赖列表顺序。
"""
from __future__ import annotations

import psycopg
import pytest

import tts_erp

SHOP_ID = "TEST_SHOP_LOGISTICS"
ORDER_ID = "TEST_LOGISTICS_ORDER_001"

# 毫秒时间戳：下单 → 打包 → 签收，间隔 1 小时
T_PLACED = 1_700_000_000_000
T_PACKED = T_PLACED + 3_600_000
T_DELIVERED = T_PLACED + 7_200_000

EV_PLACED = {"action_code": 10101, "description": "Order placed.", "update_time_millis": T_PLACED}
EV_PACKED = {"action_code": 20101, "description": "Packed by seller.", "update_time_millis": T_PACKED}
EV_DELIVERED = {"action_code": 50101, "description": "Package delivered.", "update_time_millis": T_DELIVERED}

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
            cur.execute("DELETE FROM logistics_tracking_events WHERE order_id = %s", (ORDER_ID,))
            cur.execute("DELETE FROM logistics_sync_targets WHERE order_id = %s", (ORDER_ID,))
            cur.execute("DELETE FROM logistics_tracking WHERE order_id = %s", (ORDER_ID,))
        conn.commit()
    finally:
        conn.close()


def _fetch_summary(db_url: str) -> dict:
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM logistics_tracking WHERE order_id = %s", (ORDER_ID,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "summary row not persisted"
    return dict(row)


def test_first_last_event_derived_by_timestamp_not_list_position(db_url: str):
    """TikTok 实际返回（最新在前）：first=最旧事件，last=最新事件。"""
    ok = tts_erp.persist_logistics_tracking(SHOP_ID, ORDER_ID, _resp(EVENTS_NEWEST_FIRST))
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
    ok = tts_erp.persist_logistics_tracking(SHOP_ID, ORDER_ID, _resp(list(reversed(EVENTS_NEWEST_FIRST))))
    assert ok

    row = _fetch_summary(db_url)
    assert row["first_event_at"] == T_PLACED
    assert row["last_event_at"] == T_DELIVERED
    assert row["last_action_code"] == 50101


def test_events_without_timestamp_fall_back_gracefully(db_url: str):
    """缺 update_time_millis 的事件不参与 first/last 推导，但也不能炸。"""
    events = [
        {"action_code": 20101, "description": "Packed by seller.", "update_time_millis": T_PACKED},
        {"action_code": 10101, "description": "Order placed."},  # 无时间戳
    ]
    ok = tts_erp.persist_logistics_tracking(SHOP_ID, ORDER_ID, _resp(events))
    assert ok

    row = _fetch_summary(db_url)
    assert row["first_event_at"] == T_PACKED
    assert row["last_event_at"] == T_PACKED

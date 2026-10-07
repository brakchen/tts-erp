"""主 Agent 独立安全探针（Wave 3A）：验证撤回 P1-1 门禁后，下游闸门确实拦得住脏设备。

与 test_publish_action_gates.py 里已有的断言**独立**：这里只问三个安全属性，
其中 B 是本次改动的安全底线——若拦不住，等于让手机还脏着就发下一个视频，比原 bug 更糟。

A) 设备干净的 retry  -> intent 保持 'none'  -> claim_one 立刻领取（健康路径不回归）
B) 设备脏的 retry    -> intent 'requeue_publish' -> claim_one 返回 None（闸门真的拦住）
C) 只脏 spool 的 retry -> 清理工作确实 due（不会永久滞留，无死锁）
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = [pytest.mark.domain_publishing]

from sqlalchemy.orm import Session

from tts_erp_v2.publishing import repository as R
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    claim_cleanup_work,
    claim_one,
    has_pending_device_cleanup,
)

from tests.publishing.test_publish_action_gates import _seed


def _queue_and_probe(db_session: Session, label: str, **seed_kw) -> dict:
    """把失败任务交给 retry 的入队路径，然后观察闸门与可领取性。"""
    task = _seed(db_session, **seed_kw)
    R.queue_task(db_session, task)          # <-- 被测函数
    db_session.commit()
    db_session.refresh(task)

    result = {
        "label": label,
        "intent": task.cleanup_intent,
        "device_cleanup_status": task.device_cleanup_status,
        "spool_cleanup_status": task.spool_cleanup_status,
        "gate_says_dirty": has_pending_device_cleanup(db_session),
        "claimed": claim_one(db_session, "probe-worker", lease_seconds=30, max_attempts=3)
        is not None,
    }
    print(
        f"\n[probe:{label}] intent={result['intent']!r} "
        f"device={result['device_cleanup_status']!r} spool={result['spool_cleanup_status']!r} "
        f"gate_says_dirty={result['gate_says_dirty']} claim_one="
        f"{'CLAIMED' if result['claimed'] else 'None'}"
    )
    return result


def test_safety_A_clean_retry_stays_claimable(db_session: Session) -> None:
    """设备/暂存/对象全干净：retry 必须立刻可被领取，否则整个队列会停摆。"""
    r = _queue_and_probe(db_session, "A-clean")
    assert r["intent"] == "none", f"干净的 retry 不该登记清理所有权: {r['intent']!r}"
    assert r["claimed"] is True, (
        "设备干净的 retry 必须能被 claim_one 领取；"
        "若此处为 None，整个发布队列会永久停摆（这是 3A 的致命风险）"
    )


def test_safety_B_dirty_device_retry_is_blocked(db_session: Session) -> None:
    """核心安全属性：设备清理未完成的 retry 绝不能被领取。"""
    r = _queue_and_probe(
        db_session,
        "B-dirty-device",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
    )
    assert r["intent"] == "requeue_publish", (
        "设备清理未完成时必须登记 requeue_publish，"
        "否则 has_pending_device_cleanup 看不见它（它要求 cleanup_intent != 'none'）"
    )
    assert r["gate_says_dirty"] is True, "闸门必须认定设备脏"
    assert r["claimed"] is False, (
        "【安全底线】设备清理未完成的 retry 不能被领取——"
        "否则手机还脏着就会写入下一个视频"
    )


def test_safety_B2_dirty_spool_retry_is_not_claimable_either(db_session: Session) -> None:
    """spool 未清理同样登记所有权（本地残留文件不该被下一次 staging 覆盖）。"""
    r = _queue_and_probe(
        db_session,
        "B2-dirty-spool",
        cleanup_intent="preserve_state",
        spool_cleanup_status="failed",
    )
    assert r["intent"] == "requeue_publish", r["intent"]
    assert r["claimed"] is False, "spool 未清理时不应被领取"


def test_safety_C_no_permanent_stall(db_session: Session) -> None:
    """无死锁：登记了清理所有权的任务，其清理工作必须真的 due，能被清理循环领取。

    若清理工作永不 due，intent 就永远回不到 'none'，任务永久滞留队列。
    """
    task = _seed(
        db_session,
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
    )
    R.queue_task(db_session, task)
    db_session.commit()
    db_session.refresh(task)
    assert task.cleanup_intent == "requeue_publish"

    work = claim_cleanup_work(
        lambda: db_session,
        CleanupClaimRequest(owner="probe-cleaner", lease_seconds=30, scope="device"),
    )
    print(f"\n[probe:C-no-stall] claim_cleanup_work(device) -> {work!r}")
    assert work, (
        "登记了清理所有权却领不到清理工作 = 死锁：intent 永远回不到 'none'，"
        "任务永久滞留队列"
    )

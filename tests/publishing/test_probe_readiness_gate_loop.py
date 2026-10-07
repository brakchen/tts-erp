"""readiness gate 完整闭环验证：设备脏 → retry → 刹车踩住 → 清理完成 → 活自动被领走。

回答的问题是：设计文档第 1079 行「设备清理失败则在下一次 staging 前形成 readiness
gate，清除残留后才能继续」这条，是否真的端到端成立。

四步：
  1) 设备清理 failed 的任务点 retry -> intent=requeue_publish，claim_one 踩刹车（None）
  2) 清理 worker 领到活（claim_cleanup_work）
  3) 清理成功 -> finish_cleanup_work -> intent 应复位成 none
  4) 刹车松开 -> claim_one 应能领到该任务
"""

from __future__ import annotations

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


def _factory(db_session: Session):
    """A real session factory: a fresh Session on the same bind using a savepoint.

    Passing ``lambda: db_session`` does NOT work — claim_cleanup_work /
    finish_cleanup_work open their own ``with session_factory() as session`` and
    would close the caller's session on exit. Same pattern as
    tests/publishing/test_a3w3_verify.py:91.
    """

    def factory():
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _dirty_retry_task(db_session: Session):
    """构造 P1-1 触发态并走 retry 的入队路径（queue_task）。"""
    task = _seed(
        db_session,
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
    )
    R.queue_task(db_session, task)
    db_session.commit()
    db_session.refresh(task)
    return task


def test_readiness_gate_full_loop_releases_after_cleanup(db_session: Session) -> None:
    """完整闭环：踩住 -> 清理 -> 松开 -> 活被领走。任一步断掉就是设计缺口。"""
    task = _dirty_retry_task(db_session)
    print(f"\n[gate:1-retry] intent={task.cleanup_intent!r} "
          f"device={task.device_cleanup_status!r}")

    # ---- 1) 刹车应踩住 ----
    assert task.cleanup_intent == "requeue_publish", task.cleanup_intent
    assert has_pending_device_cleanup(db_session) is True, "闸门必须认定设备脏"
    blocked = claim_one(db_session, "w1", lease_seconds=30, max_attempts=3)
    print(f"[gate:2-blocked] claim_one -> {blocked!r}")
    assert blocked is None, "设备清理未完成时不得领取（安全底线）"

    # ---- 2) 清理 worker 必须领得到活，否则会死锁 ----
    work = claim_cleanup_work(
        _factory(db_session),
        CleanupClaimRequest(owner="w2", lease_seconds=30, scope="device"),
    )
    print(f"[gate:3-claim-cleanup] -> {work is not None and work.resources}")
    assert work is not None, (
        "登记了清理所有权却领不到清理工作 = 死锁：intent 永远回不到 none，"
        "任务永久滞留队列"
    )
    assert "device" in work.resources, work.resources

    # ---- 3) 清理成功后 intent 应复位 ----
    # resource_results: resource -> error message (None = 成功)
    R.finish_cleanup_work(
        _factory(db_session),
        work.token,
        {"device": None},
    )
    db_session.expire_all()
    after = db_session.get(type(task), task.id)
    print(f"[gate:4-finished] intent={after.cleanup_intent!r} "
          f"device={after.device_cleanup_status!r} status={after.status!r} "
          f"stage={after.stage!r}")
    assert after.cleanup_intent == "none", (
        f"清理成功后 intent 应复位成 'none'，实际 {after.cleanup_intent!r}"
    )
    assert after.device_cleanup_status == "succeeded", after.device_cleanup_status
    # 关键：清理成功后 apply_cleanup_result(REQUEUE_PUBLISH) 必须把 stage 置回
    # QUEUED，否则任务会停在 terminal 的 'done' 上——status=pending + stage=done
    # 违反 video_publish_task_status_stage_check（pending 只能配
    # awaiting_upload/queued/waiting_device）。这条之前漏断言了。
    assert after.stage == "queued", (
        f"清理成功后 stage 应为 'queued'（等 Worker 领走），实际 {after.stage!r}"
    )

    # ---- 4) 刹车松开，活应能被领走 ----
    assert has_pending_device_cleanup(db_session) is False, "闸门应已松开"
    released = claim_one(db_session, "w3", lease_seconds=30, max_attempts=3)
    # 每个字段独立成行、带字段名：避免 `bool and (uuid, stage)` 挤在一行导致
    # 打印串里 ID 与阶段粘连（截图/OCR 时会被读成一个不存在的编号）。
    print("[gate:5-released] claim_one 结果：")
    print(f"    是否领到任务 = {released is not None}")
    if released is not None:
        print(f"    taskId  = {released.public_id}")
        print(f"    stage   = {released.stage}")
        print(f"    status  = {released.status}")
    assert released is not None, (
        "清理完成后任务仍领不到 = 闭环断裂：该发布被永久卡住"
    )
    assert str(released.public_id) == str(task.public_id), "应放行同一个任务"
    print("[gate:5-released] 放行的是同一个任务 ✅")

    print("\n[GATE LOOP] 闭环成立：踩住 -> 清理 -> 松开 -> 领走 ✅")


def test_readiness_gate_failure_path_backs_off_instead_of_spinning(
    db_session: Session,
) -> None:
    """设备清理【再次失败】时：必须退避重试，不能放行、也不能无限立即重试。

    上一版只测了成功路径就宣称"闭环成立"，这是真缺口：失败路径才是 1079 行
    真正关心的场景（设备可能长期离线/锁屏/ADB 不可用）。
    """
    task = _dirty_retry_task(db_session)

    # 领取清理活，故意报失败
    work = claim_cleanup_work(
        _factory(db_session),
        CleanupClaimRequest(owner="w1", lease_seconds=30, scope="device"),
    )
    assert work is not None, "清理工应领得到活"
    R.finish_cleanup_work(
        _factory(db_session), work.token, {"device": "probe ADB offline"}
    )
    db_session.expire_all()
    after = db_session.get(type(task), task.id)

    print("[gate:F1-failed] "
          f"device={after.device_cleanup_status!r} intent={after.cleanup_intent!r} "
          f"status={after.status!r} stage={after.stage!r} "
          f"attempts={after.device_cleanup_attempts} "
          f"next_in={after.device_cleanup_next_attempt_at is not None}")

    # (a) 闸门必须继续踩住：清理还没成功，不得放行新任务
    assert has_pending_device_cleanup(db_session) is True, (
        "清理失败后闸门必须继续踩住，否则会往脏设备发新视频"
    )
    blocked = claim_one(db_session, "w2", lease_seconds=30, max_attempts=3)
    print(f"[gate:F2-blocked] claim_one -> {blocked!r}")
    assert blocked is None, "清理失败期间不得放行"

    # (b) 必须退避：不能立刻又能领到，否则清理失败会打满 CPU
    assert after.device_cleanup_next_attempt_at is not None, (
        "清理失败必须设置下次重试时间（退避），否则会紧循环重试"
    )
    again = claim_cleanup_work(
        _factory(db_session),
        CleanupClaimRequest(owner="w3", lease_seconds=30, scope="device"),
    )
    print(f"[gate:F3-backoff] 立即再领 -> "
          f"{'领到了(无退避!)' if again is not None else '领不到(已退避) OK'}")
    assert again is None, (
        "清理刚失败就立刻能再领到 = 没有退避，会形成紧循环打满 CPU"
    )

    # (c) 失败原因必须落库，便于运维排查（不能吞）
    assert after.device_cleanup_error, "清理失败原因必须持久化"
    assert "ADB offline" in after.device_cleanup_error, after.device_cleanup_error
    print(f"[gate:F4-error] device_cleanup_error = {after.device_cleanup_error!r}")

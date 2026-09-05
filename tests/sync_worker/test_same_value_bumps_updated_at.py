"""回归测试:set_cursor 用相同值再调用,updated_at 必须 bump。

之前 bug:on_conflict_do_update 的 SET 子句没包含 updated_at,
导致 cursor 值不变时(updated_at 留在旧值),staleness 监控误报。
"""
import time

from sqlalchemy import select

from tts_erp_v2.db.models import SyncCursor
from tts_erp_v2.sync_worker import watermarks


def test_set_cursor_same_value_bumps_updated_at(db_session):
    """相同 cursor_epoch_ms 重复 set_cursor,updated_at 必须 bump。"""
    # First write
    watermarks.set_cursor(
        db_session, job_name="tiktok.products", scope="*",
        cursor_epoch_ms=1788498603427,
    )
    db_session.commit()
    first = db_session.execute(
        select(SyncCursor).where(
            SyncCursor.job_name == "tiktok.products",
            SyncCursor.scope == "*",
        )
    ).scalar_one()
    first_updated = first.updated_at
    assert first_updated is not None

    # 模拟 sync worker 跑了几小时后再 upsert(same value)
    time.sleep(0.1)
    watermarks.set_cursor(
        db_session, job_name="tiktok.products", scope="*",
        cursor_epoch_ms=1788498603427,  # ← 完全相同的值
    )
    db_session.commit()
    second = db_session.execute(
        select(SyncCursor).where(
            SyncCursor.job_name == "tiktok.products",
            SyncCursor.scope == "*",
        )
    ).scalar_one()

    # updated_at 必须 bump(表示"job 跑了")
    assert second.updated_at > first_updated, (
        f"updated_at 没 bump: first={first_updated}, second={second.updated_at}"
    )

    # cursor 值不应该被改
    assert second.cursor_epoch_ms == 1788498603427

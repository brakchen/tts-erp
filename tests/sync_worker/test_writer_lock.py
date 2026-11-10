"""唯一写者锁的自证测试。

三件事必须被证明（否则锁只是"看起来在那儿"）：

1. **锁跨业务提交仍被持有** —— 这正是"会话级"的意义。事务级 advisory lock
   （``pg_advisory_xact_lock``）在每次提交时释放 ⇒ 两台机可以交替拿到锁、每个都
   报 succeeded ⇒ 唯一写者失效且没有任何失败信号。
2. **作业被 kill 后锁自动释放**（不残留）—— 会话级锁绑在连接上，进程死掉 ⇒
   连接关闭 ⇒ PG 放锁。
3. **第二实例在窗口内启动 ⇒ 拒绝启动 + 留痕**（不是排队）。

断言分类：位置 / 数量枚举 / 分支覆盖，各自独立 method。
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from tts_erp_v2.sync_worker.job_runner import record_lock_refusal
from tts_erp_v2.sync_worker.writer_lock import (
    KIND_OAUTH_STATE,
    KIND_PUBLISH,
    KIND_SYNC,
    KIND_TOKEN_REFRESH,
    WRITER_KINDS,
    WriterLock,
    describe_key,
    job_writer_kind,
    writer_lock_key,
)

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def session_factory(db_engine) -> sessionmaker:
    """A real sessionmaker bound to the per-test engine.

    锁必须待在**真实连接**上（会话级 advisory lock 绑连接），所以这里不能用
    mock —— 断言要真的去 ``pg_locks`` 看锁在不在。
    """
    return sessionmaker(bind=db_engine, expire_on_commit=False, future=True)


def _advisory_lock_count(session_factory: sessionmaker, key: int) -> int:
    """当前库里有多少条 advisory 锁命中这个键（只看本库）。"""
    parts = describe_key(key)
    with session_factory() as session:
        return int(
            session.execute(
                text(
                    "select count(*) from pg_locks "
                    "where locktype = 'advisory' "
                    "and classid = CAST(:classid AS oid) "
                    "and objid = CAST(:objid AS oid) "
                    "and database = (select oid from pg_database "
                    "where datname = current_database())"
                ),
                parts,
            ).scalar_one()
        )


# ─── 位置断言 ─────────────────────────────────────────────────────────────


def test_writer_lock_module_lives_in_sync_worker() -> None:
    """位置断言：锁的唯一实现落在 tts_erp_v2/sync_worker/writer_lock.py。"""
    import tts_erp_v2.sync_worker.writer_lock as wl

    path = Path(wl.__file__).resolve()
    assert path.name == "writer_lock.py"
    assert path.parent.name == "sync_worker"


def test_one_shot_entry_point_takes_the_lock() -> None:
    """位置断言：一次性入口 main.py 的 run <job> 真的取了写者锁。"""
    source = (_REPO_ROOT / "tts_erp_v2/sync_worker/main.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "writer_lock"
        ):
            imported |= {alias.name for alias in node.names}
    assert {"WriterLock", "job_writer_kind"} <= imported
    assert "lock.acquire()" in source
    assert "EXIT_WRITER_LOCK_REFUSED" in source


def test_job_writer_kind_maps_token_refresh_separately() -> None:
    """位置断言：token.refresh 走 W3（按店铺），其余作业走 W1（按作业）。"""
    assert job_writer_kind("token.refresh") == KIND_TOKEN_REFRESH
    assert job_writer_kind("tiktok.orders") == KIND_SYNC
    assert job_writer_kind("miaoshou.purchase_price_clean") == KIND_SYNC
    assert job_writer_kind("reporting.profit_daily") == KIND_SYNC


# ─── 数量枚举 ─────────────────────────────────────────────────────────────


def test_exactly_four_writer_kinds_exist() -> None:
    """数量枚举：写者类型恰好四个（W1 sync / W2 publish / W3 token / W4 oauth）。"""
    assert len(WRITER_KINDS) == 4
    assert set(WRITER_KINDS) == {
        KIND_SYNC,
        KIND_PUBLISH,
        KIND_TOKEN_REFRESH,
        KIND_OAUTH_STATE,
    }


def test_lock_key_is_stable_and_scope_sensitive() -> None:
    """数量枚举：键稳定（同 kind+scope 永远同值）且不同 scope 不同值。"""
    first = writer_lock_key(KIND_SYNC, "tiktok.orders")
    assert writer_lock_key(KIND_SYNC, "tiktok.orders") == first
    assert writer_lock_key(KIND_SYNC, "tiktok.products") != first
    assert writer_lock_key(KIND_TOKEN_REFRESH, "tiktok.orders") != first
    assert -2**63 <= first < 2**63


# ─── 分支覆盖 ─────────────────────────────────────────────────────────────


def test_lock_is_still_held_after_a_business_commit(
    session_factory: sessionmaker,
) -> None:
    """分支覆盖（改变结果）：业务事务提交后，锁**仍然**被持有。

    这就是"会话级"的证明：事务级锁在提交时就没了。
    """
    lock = WriterLock(session_factory, kind=KIND_SYNC, scope="TEST_lock.commit")
    assert lock.acquire() is True
    try:
        assert _advisory_lock_count(session_factory, lock.info.key) == 1
        # 业务单元提交（另一个 session）
        with session_factory() as session, session.begin():
            session.execute(text("select 1"))
        # 提交之后锁还在 ⇒ 会话级，不是事务级
        assert _advisory_lock_count(session_factory, lock.info.key) == 1
    finally:
        lock.release()
    assert _advisory_lock_count(session_factory, lock.info.key) == 0


def test_lock_is_released_when_the_holder_is_killed(
    session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    """分支覆盖（改变结果）：持锁进程被 SIGKILL ⇒ 锁自动释放，不残留。

    会话级 advisory lock 绑在连接上；进程死掉 ⇒ 连接关闭 ⇒ PG 放锁。
    """
    scope = "TEST_lock.kill"
    key = writer_lock_key(KIND_SYNC, scope)
    script = tmp_path / "holder.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {_REPO_ROOT.as_posix()!r})\n"
        "from tts_erp_v2.db.base import get_session_factory\n"
        "from tts_erp_v2.sync_worker.writer_lock import WriterLock, KIND_SYNC\n"
        "lock = WriterLock(get_session_factory(), kind=KIND_SYNC, scope='TEST_lock.kill')\n"
        "assert lock.acquire()\n"
        "print('ACQUIRED', flush=True)\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert "ACQUIRED" in line, f"子进程没拿到锁：{line!r}"
        assert _advisory_lock_count(session_factory, key) == 1

        # 模拟作业被 kill（SIGKILL：不会跑清理代码）
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)

        deadline = time.time() + 30
        while time.time() < deadline:
            if _advisory_lock_count(session_factory, key) == 0:
                break
            time.sleep(0.5)
        assert _advisory_lock_count(session_factory, key) == 0, "被 kill 后锁残留了"

        # 残留会让下一个实例永远拿不到锁 ⇒ 必须能再拿到
        again = WriterLock(session_factory, kind=KIND_SYNC, scope=scope)
        assert again.acquire() is True
        again.release()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_second_instance_is_refused_and_leaves_a_trace(
    session_factory: sessionmaker,
) -> None:
    """分支覆盖（改变结果）：窗口内第二实例 ⇒ 拒绝 + 留痕（不排队）。"""
    first = WriterLock(session_factory, kind=KIND_SYNC, scope="TEST_lock.refuse")
    assert first.acquire() is True
    try:
        second = WriterLock(session_factory, kind=KIND_SYNC, scope="TEST_lock.refuse")
        assert second.acquire() is False
        assert second.held is False

        with session_factory() as session, session.begin():
            row = record_lock_refusal(
                session,
                job_name="TEST_tiktok.orders",
                marker=second.info.marker,
            )
            assert row.status == "skipped"
            assert row.finished_at is not None
            assert "writer lock held" in (row.error_message or "")
            assert "kind=sync" in (row.error_message or "")
            assert "scope=TEST_lock.refuse" in (row.error_message or "")
    finally:
        first.release()


def test_release_is_idempotent_and_reacquire_works(
    session_factory: sessionmaker,
) -> None:
    """分支覆盖：release 幂等；同一对象重复 acquire 不会自锁失败。"""
    lock = WriterLock(session_factory, kind=KIND_SYNC, scope="TEST_lock.idempotent")
    assert lock.acquire() is True
    assert lock.acquire() is True
    assert lock.held is True
    lock.release()
    lock.release()
    assert lock.held is False
    assert _advisory_lock_count(session_factory, lock.info.key) == 0


def test_one_shot_entry_refuses_to_start_when_the_lock_is_held(
    session_factory: sessionmaker,
) -> None:
    """反向夹具：人为让第二实例在窗口内启动 ⇒ **拒绝启动 + 留痕**。

    这是派工里的原话：「人为让第二实例在窗口内启动 ⇒ 必须拒绝启动并留痕」。
    走的是**真实入口** ``main._run_one_job``（调度器每个 tick 都起
    ``python -m tts_erp_v2.sync_worker.main run <job>`` ⇒ 全部调度写者都走它）。
    """
    from sqlalchemy import delete

    from tts_erp_v2.db.models import SyncJob
    from tts_erp_v2.sync_worker import main as sync_main

    job_name = "tiktok.orders"
    holder = WriterLock(session_factory, kind=KIND_SYNC, scope=job_name)
    assert holder.acquire() is True
    try:
        code = sync_main._run_one_job(job_name)
    finally:
        holder.release()

    assert code == sync_main.EXIT_WRITER_LOCK_REFUSED
    assert code != 0, "被锁挡住必须是非零退出（否则调用方以为它跑过了）"

    # 留痕：sync_jobs 一行 skipped + 稳定标记。
    with session_factory() as session, session.begin():
        rows = list(
            session.execute(
                select(SyncJob).where(
                    SyncJob.job_name == job_name,
                    SyncJob.status == "skipped",
                )
            )
            .scalars()
            .all()
        )
        assert rows, "被拒绝必须留痕（skipped 行）"
        assert "writer lock held" in (rows[-1].error_message or "")
        assert f"scope={job_name}" in (rows[-1].error_message or "")
        # 清理（作业名是真实注册名，不带 TEST_ 前缀 ⇒ 必须自己删）
        session.execute(
            delete(SyncJob).where(
                SyncJob.job_name == job_name,
                SyncJob.status == "skipped",
                SyncJob.error_message.like("writer lock held:%"),
            )
        )

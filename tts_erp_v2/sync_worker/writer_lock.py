"""唯一写者锁：会话级 PostgreSQL advisory lock。

为什么必须是**会话级**（不是事务级）
--------------------------------------
作业的业务单元是**短事务**（``with session.begin():`` 写一块就提交）。
事务级 advisory lock（``pg_advisory_xact_lock``）**在每次提交时释放** ⇒
两台机可以**交替**拿到锁：机 A 提交 → 锁释放 → 机 B 拿到 → 提交 …
每个 tick 都报 succeeded ⇒ 唯一写者失效，而且**没有任何失败信号**
（这正是 2026-11-10 那次「公网机接入生产库」评估里最危险的形态）。

会话级 ``pg_advisory_lock`` 绑在**连接**上，只有显式 ``pg_advisory_unlock``
或**连接关闭**（进程退出 / 被 kill）才释放 ⇒ 锁的存活期 = 作业的存活期。

语义：拿不到锁 ⇒ **拒绝启动 + 留痕**，**绝不排队**
---------------------------------------------------------
排队 = 僵尸的温床（一个被 kill 的排队进程会留一串等待者）。
实现用 ``pg_try_advisory_lock``——它属于会话级锁家族，但**非阻塞**：
拿不到就立刻返回 false。阻塞版 ``pg_advisory_lock`` 就是在排队，不用。

键的选取
--------
``sha256("tts-erp/writer-lock/v1|<kind>|<scope>")`` 的前 8 字节，按有符号
int64 解释：

* **稳定**：跨进程、跨主机、跨重启都一样（不是 Python 的 ``hash()``，那个带
  随机种子，跨进程会变）；
* **不撞车**：带命名空间前缀，不会和别的程序用 advisory lock 时撞上；
* **可反查**：键 → (kind, scope) 可还原，排查 ``pg_locks`` 里的数字时能看懂。

四个键（详见 ``docs/ops/writer-lock-design.md``）
--------------------------------------------------
===========  ====================  ==========================================
键            scope                冲突后果
===========  ====================  ==========================================
W1 ``sync``   job_name             重复写 / watermark 互覆
W2 ``publish``  发布目标             重复发布到 TikTok（**不可逆**）
W3 ``token_refresh`` provider:shop  整店凭证链断裂（refresh_token 轮换式）
W4 ``oauth_state`` provider:shop   双消费 / 状态机错乱 + token 轮换竞争
===========  ====================  ==========================================

W3 与 W1/W2 的机制不同：sync/publish 是「一个实例跑、另一个不跑」；
**token 轮换是「同一时刻只能有一个进程在刷某个 shop 的 token」**——因为
refresh_token 是轮换式的，两台机都刷 ⇒ 一台先成功把旧值轮换掉 ⇒ 另一台拿
失效的 refresh_token ⇒ 整店失联（恢复要人工重走 OAuth）。所以 W3 的粒度是
**按 shop**（不是全局）：不同店铺的凭据互不相干，全局锁会把无关店铺串行化；
而**同一店铺**的双刷才是真正的危险边界。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Self

log = logging.getLogger("tts_erp_v2.sync_worker.writer_lock")

#: 键的命名空间前缀。改它 = 所有已部署的锁键都变（= 旧锁不再互斥），慎改。
WRITER_LOCK_NAMESPACE = "tts-erp/writer-lock/v1"

#: 四类写者。
KIND_SYNC = "sync"
KIND_PUBLISH = "publish"
KIND_TOKEN_REFRESH = "token_refresh"
KIND_OAUTH_STATE = "oauth_state"

WRITER_KINDS: tuple[str, ...] = (
    KIND_SYNC,
    KIND_PUBLISH,
    KIND_TOKEN_REFRESH,
    KIND_OAUTH_STATE,
)

#: 作业名 → 写者类型。W1 覆盖除 token.refresh 以外的全部注册作业；
#: publish 与 oauth_state 不是调度作业（发布是 API/HTTP 侧的人工触发，
#: OAuth 回调是 HTTP 路由），它们在各自入口处单独取锁。
_JOB_KIND_OVERRIDES: dict[str, str] = {
    "token.refresh": KIND_TOKEN_REFRESH,
}


def job_writer_kind(job_name: str) -> str:
    """作业名 → 写者类型（默认 sync）。"""
    return _JOB_KIND_OVERRIDES.get(job_name, KIND_SYNC)


def writer_lock_key(kind: str, scope: str) -> int:
    """``(kind, scope)`` → 64 位 advisory lock 键（有符号）。

    Args:
        kind: 写者类型（:data:`WRITER_KINDS` 之一）。
        scope: 冲突粒度（作业名 / 发布目标 / ``provider:shop``）。

    Returns:
        稳定的有符号 int64 键。
    """
    material = f"{WRITER_LOCK_NAMESPACE}|{kind}|{scope}".encode()
    digest = hashlib.sha256(material).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def describe_key(key: int) -> dict[str, Any]:
    """把 ``pg_locks`` 里的 advisory 键拆回 (classid, objid)，便于查询。

    PostgreSQL 把 64 位 advisory key 拆成两个 32 位无符号整数存进
    ``pg_locks.classid`` / ``pg_locks.objid``。
    """
    unsigned = key & 0xFFFFFFFFFFFFFFFF
    return {
        "classid": (unsigned >> 32) & 0xFFFFFFFF,
        "objid": unsigned & 0xFFFFFFFF,
    }


class WriterLockRefused(RuntimeError):
    """拿不到写者锁 ⇒ 拒绝启动（不是排队等待）。"""

    def __init__(self, *, kind: str, scope: str, key: int) -> None:
        super().__init__(
            f"writer lock held by another instance: kind={kind} scope={scope} key={key}"
        )
        self.kind = kind
        self.scope = scope
        self.key = key


@dataclass(frozen=True)
class WriterLockInfo:
    """锁的静态信息（写进留痕与日志）。"""

    kind: str
    scope: str
    key: int

    @property
    def marker(self) -> str:
        """留痕用的稳定前缀（watchdog 靠它认出「被锁挡住」的作业）。"""
        return f"writer lock held: kind={self.kind} scope={self.scope} key={self.key}"


class WriterLock:
    """会话级 advisory lock 的持有者。

    用法（作业级，独立连接）::

        lock = WriterLock(session_factory, kind=KIND_SYNC, scope="tiktok.orders")
        if not lock.acquire():
            ...  # 拒绝启动 + 留痕
        try:
            ...  # 干活（内部可以多次提交事务）
        finally:
            lock.release()

    用法（作业内部逐店铺取锁，复用调用方的 session）::

        lock = WriterLock.from_session(session, kind=KIND_TOKEN_REFRESH, scope=shop)
        ...

    两种用法的差别只在**sessionmaker 从哪来**：

    * 作业级 ⇒ 直接给 sessionmaker（`main.py` 传的是全局那个）⇒ 独占一条连接，
      业务 session 的生命周期与锁无关；
    * 作业内部 ⇒ :meth:`from_session` 从调用方 session 所在的**引擎**另开一个
      sessionmaker ⇒ 同样独占一条连接。

    两种都**独占连接**，有两个必须踩过的坑（本实现都规避了）：

    * 复用调用方自己的 session ⇒ ``session.connection()`` 会**隐式开启事务**，
      作业里随后的 ``with session.begin():`` 直接报 "A transaction is already
      begun on this Session"；
    * 用 ``session.get_bind()`` 派生 sessionmaker ⇒ 测试里 session 常直接绑在一条
      Connection 上（``Session(bind=conn)``），那样新 session 会和调用方**共用同一条
      连接** ⇒ 锁的释放会动到调用方的事务。必须取 ``bind.engine``。
    """

    def __init__(self, source: Any, *, kind: str, scope: str) -> None:
        if kind not in WRITER_KINDS:
            raise ValueError(f"unknown writer kind: {kind!r}")
        self._source = source
        self._kind = kind
        self._scope = scope
        self._key = writer_lock_key(kind, scope)
        self._session = None
        self._owns_session = False
        self._held = False

    @property
    def info(self) -> WriterLockInfo:
        return WriterLockInfo(kind=self._kind, scope=self._scope, key=self._key)

    @property
    def held(self) -> bool:
        """本进程当前是否持有这把锁。"""
        return self._held

    @classmethod
    def from_session(cls, session: Any, *, kind: str, scope: str) -> WriterLock:
        """从调用方的 session 派生一把锁（作业内部逐店铺取锁时用）。

        ⚠️ 必须取**引擎**，不能直接用 ``session.get_bind()``：测试里 session 常常
        直接绑在一条 Connection 上（``Session(bind=conn)``），那样新 session 会和
        调用方**共用同一条连接** ⇒ 锁的释放会动到调用方的事务。``bind.engine``
        才是真正的引擎 ⇒ 锁独占一条池连接。

        锁独占连接还有第二个好处：``session.connection()`` 会**隐式开启事务**，
        若复用自己的 session，作业里随后的 ``with session.begin():`` 会直接
        报 "A transaction is already begun on this Session"。
        """
        from sqlalchemy.orm import sessionmaker

        bind = session.get_bind()
        engine = getattr(bind, "engine", bind)
        return cls(sessionmaker(bind=engine), kind=kind, scope=scope)

    def acquire(self) -> bool:
        """尝试**非阻塞**地拿到锁。

        Returns:
            True = 拿到；False = 已被别人持有（调用方必须拒绝启动并留痕）。
        """
        if self._held:
            return True
        from sqlalchemy import text

        # 独占一条连接（不与业务 session 共用）：锁必须活过业务事务的提交。
        session = self._source()
        self._owns_session = True
        # session.connection() 把连接从池里取出来并**一直留着**——锁必须活过
        # 业务事务的提交，所以它必须待在一条不被归还的连接上。
        connection = session.connection()
        got = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
        ).scalar()
        if not got:
            session.close()
            log.warning(
                "writer lock refused: kind=%s scope=%s key=%s",
                self._kind,
                self._scope,
                self._key,
            )
            return False
        self._session = session
        self._held = True
        return True

    def release(self) -> None:
        """释放锁（幂等）。

        先 ``pg_advisory_unlock`` 再把连接还回池子——**必须**显式解锁：
        连接归还时只做 rollback，而 rollback **不会**释放会话级 advisory lock，
        锁会跟着这条连接泄漏给下一个拿到它的人。
        """
        if not self._held:
            return
        from sqlalchemy import text

        session, self._session = self._session, None
        self._held = False
        owns, self._owns_session = self._owns_session, False
        try:
            session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": self._key}
            )
        except Exception:
            log.warning(
                "advisory unlock failed for key=%s (connection close will release it)",
                self._key,
                exc_info=True,
            )
        finally:
            if owns:
                # 只关我们自己的 session；调用方的 session 一律不碰（不提交）。
                session.close()

    def __enter__(self) -> Self:
        if not self.acquire():
            raise WriterLockRefused(
                kind=self._kind, scope=self._scope, key=self._key
            )
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


__all__ = [
    "KIND_OAUTH_STATE",
    "KIND_PUBLISH",
    "KIND_SYNC",
    "KIND_TOKEN_REFRESH",
    "WRITER_KINDS",
    "WRITER_LOCK_NAMESPACE",
    "WriterLock",
    "WriterLockInfo",
    "WriterLockRefused",
    "describe_key",
    "job_writer_kind",
    "writer_lock_key",
]

"""Declarative base + engine/session factory for tts_erp_v2.

Reads TTS_ERP_DB_URL from os.environ (populated from .env at app startup).
Does NOT mutate the existing public.* schema or the legacy tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Schemas we manage. Alembic uses these to create schemas before table create.
SCHEMAS: tuple[str, ...] = (
    "integration",
    "commerce",
    "procurement",
    "fulfillment",
    "after_sales",
    "finance",
    "linkage",
    "reporting",
    "security",
    "analytics",
    "plugin",
)


class Base(DeclarativeBase):
    """Declarative base for all tts_erp_v2 models.

    Forces every ``Mapped[datetime]`` annotation to render as
    ``TIMESTAMP WITH TIME ZONE`` on PostgreSQL. V3 §14 requires this
    (time一律 timestamptz). SQLAlchemy 2.0 supports overriding the
    inferred column type per-declarative-base via ``type_annotation_map``.
    """

    # ClassVar：这是 DeclarativeBase 的类级配置（非映射列），SQLAlchemy 按
    # 名从类 dict 读取，注释类型不影响功能；ruff RUF012 要求显式标注。
    type_annotation_map: ClassVar[dict[Any, type]] = {
        datetime: TIMESTAMP(timezone=True),
    }


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

# ─── QueuePool 配置（2026-09-06，见 commit 信息 + 压测基线）────────────
# 背景：默认 QueuePool 5+10=15 + pool_pre_ping 每 checkout 一次 SELECT 往返 +
# checkout 超时 30s，是 09-05 cursor 池耗尽 503（排队 100s+）与单进程吞吐
# 上限 ~200 qps 的共因（实测同查询 pre_ping on 198 qps → off 371 qps，
# SQLAlchemy 8 线程 139 qps < 单线程 371 qps = GIL 后池锁排队）。
#
# 决策：
# - pool_pre_ping=False：去掉每请求一次往返。残余风险（review Finding-1
#   修正表述）：PG 容器重启瞬间**所有**池连接全死（recycle=300 只按创建时
#   长静默换新，不预检 <300s 的连接）→ 每条死连接在 checkout 后第一次
#   execute 才暴露，恰好一次失败/连接（API+sync 两池合计至多 ~pool size 个
#   请求），随后该连接被池判死丢弃并重建，数分钟内自愈；有客户端重试兜底。
#   若未来要彻底消除：只对 sync-worker 进程开 pool_pre_ping=True（它对
#   故障代价最高），API 保持 off。
# - size 10 / overflow 20（单进程 30）：API + sync-worker 两进程各 30 = 60，
#   PG max_connections=100 留 40 头；09-05 那种 15 连接被占满的耗尽点后移。
# - pool_timeout 10s（原 30s）：真到耗尽时快速失败（503/错误），不再让请求
#   挂 100s 才报错。
POOL_SIZE = 10
POOL_MAX_OVERFLOW = 20
POOL_TIMEOUT_S = 10.0
POOL_RECYCLE_S = 300


def _resolve_db_url() -> str:
    url = os.environ.get("TTS_ERP_DB_URL")
    if not url:
        raise RuntimeError(
            "TTS_ERP_DB_URL not configured. Set it in .env or os.environ "
            "before importing tts_erp_v2.db.base."
        )
    return url


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Return a process-wide Engine. Idempotent; recreate only if url changes."""
    global _engine
    target = url or _resolve_db_url()
    if _engine is None or _engine.url.render_as_string(hide_password=False) != target:
        _engine = create_engine(
            target,
            echo=echo,
            future=True,
            # 池参数见模块顶部注释（pre_ping off + recycle 300 + size 10/
            # overflow 20 + checkout 超时 10s）。
            pool_pre_ping=False,
            pool_recycle=POOL_RECYCLE_S,
            pool_size=POOL_SIZE,
            max_overflow=POOL_MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT_S,
        )
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a process-wide SessionLocal. Idempotent."""
    global _SessionLocal
    if _SessionLocal is None:
        eng = engine or get_engine()
        _SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    return _SessionLocal


def reset_for_testing() -> None:
    """Drop the cached engine/sessionmaker. Used by conftest fixtures."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_scope() -> Iterator[Session]:
    """Context manager-style session helper for one-off scripts."""
    SessionLocal = get_session_factory()
    sess = SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()

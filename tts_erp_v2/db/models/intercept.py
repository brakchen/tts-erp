"""plugin.intercept_* — 请求拦截配置与记录（4 张表）。

拦截配置（1 张）：
  intercept_configs — 域名 + endpoint 白名单规则

拦截记录（3 张）：
  intercepted_requests — 拦截的 HTTP 请求记录
  intercept_sessions — 浏览器会话
  intercept_sync_cursors — 同步游标

详见 tech-doc/intercept-design.md。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


# ── intercept_configs ────────────────────────────────────────────────
# 拦截配置白名单：domain + endpoint 唯一组合
class InterceptConfig(Base):
    __tablename__ = "intercept_configs"
    __table_args__ = (
        UniqueConstraint(
            "domain", "endpoint", name="intercept_configs_domain_endpoint_unique"
        ),
        Index(
            "idx_intercept_configs_domain",
            "domain",
            postgresql_where=text("enabled = true"),
        ),
        Index(
            "idx_intercept_configs_domain_endpoint",
            "domain",
            "endpoint",
            unique=True,
        ),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    capture_headers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    capture_body: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[dict | None] = mapped_column(JSONB, server_default=text("'[]'"))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


# ── intercepted_requests ─────────────────────────────────────────────
# 拦截的 HTTP 请求记录
class InterceptedRequest(Base):
    __tablename__ = "intercepted_requests"
    __table_args__ = (
        Index("idx_intercepted_requests_captured_at", "captured_at"),
        Index("idx_intercepted_requests_url_hash", "url_hash"),
        Index("idx_intercepted_requests_endpoint", "endpoint_host", "endpoint_path"),
        Index("idx_intercepted_requests_session", "session_id", "captured_at"),
        Index(
            "idx_intercepted_requests_seller",
            "seller_id",
            postgresql_where=text("seller_id IS NOT NULL"),
        ),
        Index("idx_intercepted_requests_whitelisted", "is_whitelisted", "captured_at"),
        Index(
            "idx_intercepted_requests_config",
            "matched_config_id",
            postgresql_where=text("matched_config_id IS NOT NULL"),
        ),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    trace_id: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_path: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_host: Mapped[str] = mapped_column(Text, nullable=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    matched_config_id: Mapped[int | None] = mapped_column(BigInteger)
    request_headers: Mapped[dict | None] = mapped_column(JSONB)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_status_text: Mapped[str | None] = mapped_column(Text)
    response_headers: Mapped[dict | None] = mapped_column(JSONB)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    seller_id: Mapped[str | None] = mapped_column(Text)
    advertiser_id: Mapped[str | None] = mapped_column(Text)
    business_context: Mapped[dict | None] = mapped_column(JSONB)
    pagination: Mapped[dict | None] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── intercept_sessions ───────────────────────────────────────────────
# 浏览器会话
class InterceptSession(Base):
    __tablename__ = "intercept_sessions"
    __table_args__ = ({"schema": "plugin"},)

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    session_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tab_id: Mapped[int | None] = mapped_column(Integer)
    tab_url: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    total_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    whitelisted_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    metadata_only_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── intercept_sync_cursors ───────────────────────────────────────────
# 同步游标
class InterceptSyncCursor(Base):
    __tablename__ = "intercept_sync_cursors"
    __table_args__ = ({"schema": "plugin"},)

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    cursor_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    last_synced_id: Mapped[int | None] = mapped_column(BigInteger)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_synced: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = [
    "InterceptConfig",
    "InterceptedRequest",
    "InterceptSession",
    "InterceptSyncCursor",
]

"""security.* — 用户账号体系 ORM（设计：tech-doc/user-account-authz-design.md §4）。

6 张表，与现有 ``security.api_keys`` 并列，全部独立于 API key 数据：
- users            账号（用户名 + argon2id 密码哈希 + 状态）
- roles            角色（页面权限点集合 + api_tier 档位）
- permissions      页面权限点（page:<page_id>）
- role_permissions  角色 ↔ 权限点
- user_roles       用户 ↔ 角色
- user_sessions    服务端会话（只存 token 哈希）
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="users_username_key"),
        Index("ix_users_username", "username"),
        {"schema": "security"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    username: Mapped[str] = mapped_column(Text, nullable=False)  # 小写归一化
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2id PHC
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )  # active | disabled
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"), onupdate=text("now()")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = ({"schema": "security"},)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    api_tier: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'readwrite'")
    )  # readonly | readwrite | admin
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"), onupdate=text("now()")
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = ({"schema": "security"},)

    code: Mapped[str] = mapped_column(Text, primary_key=True)  # page:<page_id>
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'page'")
    )  # v1 只有 page
    name: Mapped[str] = mapped_column(Text, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        PrimaryKeyConstraint("role_code", "permission_code"),
        {"schema": "security"},
    )

    role_code: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # FK → security.roles(code) ON DELETE CASCADE
    permission_code: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # FK → security.permissions(code) ON DELETE CASCADE


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role_code"),
        {"schema": "security"},
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_code: Mapped[str] = mapped_column(Text, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="user_sessions_token_key"),
        Index("ix_user_sessions_user", "user_id"),
        Index("ix_user_sessions_active", "user_id", "expires_at"),
        {"schema": "security"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # sha256
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)

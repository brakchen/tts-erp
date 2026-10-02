"""用户账号与页面权限体系（6 表 + 种子）。

设计：tech-doc/user-account-authz-design.md §4/§4.1
变更范围：只新增 security.users / roles / permissions / role_permissions /
user_roles / user_sessions；零 ALTER 现有表，可回滚（drop 新表即可）。

Revision ID: 0052_user_accounts
Revises: 0051_spu_roi_query_indexes
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0052_user_accounts"
down_revision: str | None = "0051_spu_roi_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 页面权限点 + 内置角色定义与 tts_erp_v2/accounts/pages.py 同源（代码常量为准，
# 本表为落库快照；新增页面时同步两处并跑 sync-permissions）。
_PAGE_PERMISSIONS: tuple[str, ...] = (
    "page:dashboard",
    "page:focused-spus",
    "page:spu-roi",
    "page:ad-daily",
    "page:manual-costs",
    "page:shops",
    "page:enum-map",
    "page:runtime-configs",
    "page:sync-jobs",
    "page:users",
    "page:intercept-configs",
    "page:intercept-requests",
    "page:intercept-stats",
)

_BUILTIN_ROLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "admin",
        "系统管理员",
        "admin",
        _PAGE_PERMISSIONS,
    ),
    (
        "operator",
        "运营",
        "readwrite",
        tuple(p for p in _PAGE_PERMISSIONS if p != "page:users"),
    ),
    (
        "viewer",
        "只读分析",
        "readonly",
        (
            "page:dashboard",
            "page:focused-spus",
            "page:spu-roi",
            "page:ad-daily",
            "page:intercept-stats",
        ),
    ),
)

_CREATE_SQL: tuple[str, ...] = (
    """
    CREATE TABLE security.users (
        id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        username            TEXT NOT NULL,
        display_name        TEXT NOT NULL,
        password_hash       TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'active',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_login_at       TIMESTAMPTZ,
        password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT users_username_key UNIQUE (username),
        CONSTRAINT users_status_check CHECK (status IN ('active', 'disabled'))
    )
    """,
    "CREATE INDEX ix_users_username ON security.users (username)",
    """
    CREATE TABLE security.roles (
        code        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        api_tier    TEXT NOT NULL DEFAULT 'readwrite',
        is_builtin  BOOLEAN NOT NULL DEFAULT false,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT roles_api_tier_check
            CHECK (api_tier IN ('readonly', 'readwrite', 'admin'))
    )
    """,
    """
    CREATE TABLE security.permissions (
        code TEXT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'page',
        name TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT permissions_kind_check CHECK (kind = 'page')
    )
    """,
    """
    CREATE TABLE security.role_permissions (
        role_code       TEXT NOT NULL
            REFERENCES security.roles (code) ON DELETE CASCADE,
        permission_code TEXT NOT NULL
            REFERENCES security.permissions (code) ON DELETE CASCADE,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (role_code, permission_code)
    )
    """,
    """
    CREATE TABLE security.user_roles (
        user_id   BIGINT NOT NULL REFERENCES security.users (id) ON DELETE CASCADE,
        role_code TEXT NOT NULL REFERENCES security.roles (code) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, role_code)
    )
    """,
    """
    CREATE TABLE security.user_sessions (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        token_hash   BYTEA NOT NULL,
        user_id      BIGINT NOT NULL
            REFERENCES security.users (id) ON DELETE CASCADE,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ,
        revoked_at   TIMESTAMPTZ,
        ip           TEXT,
        user_agent   TEXT,
        CONSTRAINT user_sessions_token_key UNIQUE (token_hash)
    )
    """,
    "CREATE INDEX ix_user_sessions_user ON security.user_sessions (user_id)",
    """
    CREATE INDEX ix_user_sessions_active ON security.user_sessions (user_id, expires_at)
    """,
    # updated_at 触发器 —— 命名与既有触发器同一约定
    # (trg_<schema>_<table>_touch → public.fn_touch_updated_at())。
    """
    CREATE OR REPLACE TRIGGER trg_users_touch BEFORE UPDATE ON security.users
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
    """
    CREATE OR REPLACE TRIGGER trg_roles_touch BEFORE UPDATE ON security.roles
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
    """
    CREATE OR REPLACE TRIGGER trg_permissions_touch BEFORE UPDATE ON security.permissions
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
    """
    CREATE OR REPLACE TRIGGER trg_role_permissions_touch BEFORE UPDATE ON security.role_permissions
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
    """
    CREATE OR REPLACE TRIGGER trg_user_roles_touch BEFORE UPDATE ON security.user_roles
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
    """
    CREATE OR REPLACE TRIGGER trg_user_sessions_touch BEFORE UPDATE ON security.user_sessions
    FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
    """,
)

_DROP_SQL: tuple[str, ...] = (
    "DROP TABLE IF EXISTS security.user_sessions",
    "DROP TABLE IF EXISTS security.user_roles",
    "DROP TABLE IF EXISTS security.role_permissions",
    "DROP TABLE IF EXISTS security.permissions",
    "DROP TABLE IF EXISTS security.roles",
    "DROP TABLE IF EXISTS security.users",
)


def upgrade() -> None:
    for statement in _CREATE_SQL:
        op.execute(text(statement))
    # 种子：权限点（幂等 upsert）
    for code in _PAGE_PERMISSIONS:
        op.execute(
            text(
                "INSERT INTO security.permissions (code, kind, name) "
                "VALUES (:code, 'page', :name) "
                "ON CONFLICT (code) DO NOTHING"
            ).params(code=code, name=code)
        )
    # 种子：内置角色 + role_permissions（幂等）
    for role_code, name, api_tier, perms in _BUILTIN_ROLES:
        op.execute(
            text(
                "INSERT INTO security.roles (code, name, api_tier, is_builtin) "
                "VALUES (:code, :name, :tier, true) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name"
            ).params(code=role_code, name=name, tier=api_tier)
        )
        for perm in perms:
            op.execute(
                text(
                    "INSERT INTO security.role_permissions (role_code, permission_code) "
                    "VALUES (:role, :perm) "
                    "ON CONFLICT (role_code, permission_code) DO NOTHING"
                ).params(role=role_code, perm=perm)
            )
    # 首个 admin 账号不入迁移：部署者用 CLI 创建（避免默认口令入库）。


def downgrade() -> None:
    for statement in _DROP_SQL:
        op.execute(text(statement))

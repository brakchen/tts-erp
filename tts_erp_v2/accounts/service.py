"""账号服务层：登录、用户/角色管理、授权装载（设计 §5/§7/§9）。

API（users API、CLI）与认证链路共用本模块；权限装载带短 TTL 缓存
（沿用 access 模块的 auth-cache 模式：权限变更最多延迟一个缓存 TTL 生效，
禁用/登出走 revoked_at 即时）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.accounts import passwords, sessions
from tts_erp_v2.accounts.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserSession,
)
from tts_erp_v2.accounts.pages import BUILTIN_ROLE_NAMES, BUILTIN_ROLES

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")

CACHE_TTL = 60.0
NEG_CACHE_TTL = 20.0

PERM_TIERS = {"readonly": 1, "readwrite": 2, "admin": 3}


class AccountError(ValueError):
    """业务规则拒绝（用户名/密码策略、护栏等）；message 可直接回给用户。"""


class NotFoundError(AccountError):
    pass


@dataclass(frozen=True, slots=True)
class UserContext:
    """一次会话/认证的授权快照（写入 request.scope 供下游使用）。"""

    user_id: int
    username: str
    display_name: str
    roles: tuple[str, ...]
    pages: frozenset[str]  # 有效权限点（多角色并集），如 {"page:dashboard", …}
    api_tier: str  # readonly | readwrite | admin（多角色取最高）


# ── 授权装载（每请求路径；带缓存）────────────────────────────────────
_context_cache: dict[str, tuple[UserContext | None, float]] = {}


def clear_context_cache() -> None:
    _context_cache.clear()


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.match(normalized):
        raise AccountError("用户名需为 2-32 位小写字母/数字/._-，且以字母或数字开头")
    return normalized


def load_user_context(session: Session, user_id: int) -> UserContext | None:
    """Load the effective authorization snapshot for one active user (cached)."""
    cache_key = str(user_id)
    now = time.monotonic()
    hit = _context_cache.get(cache_key)
    if hit is not None and hit[1] > now:
        return hit[0]

    result = _load_user_context_uncached(session, user_id)
    ttl = CACHE_TTL if result is not None else NEG_CACHE_TTL
    _context_cache[cache_key] = (result, now + ttl)
    return result


def _load_user_context_uncached(session: Session, user_id: int) -> UserContext | None:
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        return None
    role_codes = tuple(
        session.execute(
            select(UserRole.role_code).where(UserRole.user_id == user_id)
        ).scalars()
    )
    return _build_context(session, user, role_codes)


def _build_context(
    session: Session, user: User, role_codes: tuple[str, ...]
) -> UserContext:
    pages: set[str] = set()
    api_tier = "readonly"
    for role_code in role_codes:
        row = session.get(Role, role_code)
        if row is None:
            continue
        if PERM_TIERS.get(row.api_tier, 1) > PERM_TIERS.get(api_tier, 1):
            api_tier = row.api_tier
        codes = session.execute(
            select(RolePermission.permission_code).where(
                RolePermission.role_code == role_code
            )
        ).scalars()
        pages.update(codes)
    return UserContext(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=role_codes,
        pages=frozenset(pages),
        api_tier=api_tier,
    )


# ── 认证 ────────────────────────────────────────────────────────────
def authenticate_session_cookie(raw_cookie: str | None):
    """Open a short-lived DB session and authenticate one browser cookie."""
    from tts_erp_v2.db.base import get_session_factory

    with get_session_factory()() as session:
        return authenticate_session_token(session, raw_cookie)


def authenticate_session_token(
    session: Session, raw_cookie: str | None
):
    """cookie 值 → UserCredential | None（每请求；会话/用户状态即时复查）.

    旧格式/无效格式/已吊销/已过期/用户禁用 → None（fail closed）。
    """
    from tts_erp_v2.access._types import Role as ApiRole
    from tts_erp_v2.access._types import UserCredential

    token = sessions.parse_token(raw_cookie)
    if token is None:
        return None
    row = sessions.get_session_row(session, token)
    if row is None:
        return None
    context = load_user_context(session, row.user_id)
    if context is None:  # 用户不存在/被禁用
        return None
    sessions.touch_session(session, row)
    return UserCredential(
        user_id=context.user_id,
        username=context.username,
        display_name=context.display_name,
        role=ApiRole(context.api_tier),
        pages=context.pages,
        session_id=row.id,
    )


def authenticate(
    session: Session, username: str, password: str
) -> User | None:
    """用户名+密码认证；用户不存在也跑一次 dummy 校验（防枚举）。

    返回 None = 凭据不对/用户禁用；抛异常 = 认证存储不可用（fail closed）。
    """
    normalized = normalize_username(username)
    user = session.execute(
        select(User).where(User.username == normalized)
    ).scalar_one_or_none()
    if user is None:
        passwords.verify_dummy(password)
        return None
    if not passwords.verify_password(password, user.password_hash):
        return None
    if user.status != "active":
        return None
    return user


def touch_login(session: Session, user: User) -> None:
    user.last_login_at = datetime.now(UTC)
    session.commit()


# ── 用户管理 ────────────────────────────────────────────────────────
def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    roles: list[str],
    actor: str,
) -> User:
    normalized = validate_username(username)
    if not display_name.strip():
        raise AccountError("显示名不能为空")
    exists = session.execute(
        select(User.id).where(User.username == normalized)
    ).scalar_one_or_none()
    if exists is not None:
        raise AccountError(f"用户名已存在: {normalized}")
    _assert_roles_exist(session, roles)
    user = User(
        username=normalized,
        display_name=display_name.strip(),
        password_hash=passwords.hash_password(password),  # 先过策略再入库
    )
    session.add(user)
    session.flush()
    for role_code in roles:
        session.add(UserRole(user_id=user.id, role_code=role_code))
    session.commit()
    return user


def reset_password(
    session: Session, *, user_id: int, password: str, actor: str
) -> None:
    user = _get_user(session, user_id)
    user.password_hash = passwords.hash_password(password)
    user.password_changed_at = datetime.now(UTC)
    session.commit()
    sessions.revoke_user_sessions(session, user_id=user_id)  # 全部失效


def change_password(
    session: Session,
    *,
    user_id: int,
    old_password: str,
    new_password: str,
    keep_session_id: int | None = None,
) -> None:
    user = _get_user(session, user_id)
    if not passwords.verify_password(old_password, user.password_hash):
        raise AccountError("当前密码不正确")
    user.password_hash = passwords.hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    session.commit()
    sessions.revoke_user_sessions(
        session, user_id=user_id, keep_session_id=keep_session_id
    )


def set_user_roles(session: Session, *, user_id: int, roles: list[str]) -> None:
    _get_user(session, user_id)
    _assert_roles_exist(session, roles)
    for row in session.execute(
        select(UserRole).where(UserRole.user_id == user_id)
    ).scalars():
        session.delete(row)
    for role_code in roles:
        session.add(UserRole(user_id=user_id, role_code=role_code))
    session.commit()
    clear_context_cache()


def set_user_status(
    session: Session, *, user_id: int, status: str, actor_id: int | None
) -> None:
    if status not in {"active", "disabled"}:
        raise AccountError("status 只能是 active 或 disabled")
    if actor_id is not None and actor_id == user_id and status == "disabled":
        raise AccountError("不能禁用自己")
    if status == "disabled":
        _assert_not_last_admin(session, user_id)
    user = _get_user(session, user_id)
    user.status = status
    session.commit()
    clear_context_cache()
    if status == "disabled":
        sessions.revoke_user_sessions(session, user_id=user_id)


def update_user(
    session: Session, *, user_id: int, display_name: str | None
) -> None:
    user = _get_user(session, user_id)
    if display_name is not None:
        if not display_name.strip():
            raise AccountError("显示名不能为空")
        user.display_name = display_name.strip()
    session.commit()


def list_users(session: Session) -> list[dict]:
    rows = session.execute(select(User).order_by(User.username)).scalars().all()
    result = []
    for user in rows:
        roles = list(
            session.execute(
                select(UserRole.role_code).where(UserRole.user_id == user.id)
            ).scalars()
        )
        active_sessions = session.execute(
            select(func.count())
            .select_from(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
        ).scalar_one()
        result.append(
            {
                "id": user.id,
                "username": user.username,
                "displayName": user.display_name,
                "status": user.status,
                "roles": roles,
                "lastLoginAt": user.last_login_at,
                "activeSessions": int(active_sessions),
            }
        )
    return result


def get_user_detail(session: Session, user_id: int) -> dict:
    user = _get_user(session, user_id)
    roles = list(
        session.execute(
            select(UserRole.role_code).where(UserRole.user_id == user_id)
        ).scalars()
    )
    context = _build_context(session, user, tuple(roles))
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "status": user.status,
        "roles": roles,
        "pages": sorted(context.pages),
        "apiTier": context.api_tier,
        "lastLoginAt": user.last_login_at,
    }


def list_user_sessions(session: Session, user_id: int) -> list[dict]:
    _get_user(session, user_id)
    rows = session.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.created_at.desc())
    ).scalars()
    now = datetime.now(UTC)
    result = []
    for row in rows:
        active = row.revoked_at is None and row.expires_at > now
        result.append(
            {
                "id": row.id,
                "createdAt": row.created_at,
                "expiresAt": row.expires_at,
                "lastSeenAt": row.last_seen_at,
                "revokedAt": row.revoked_at,
                "ip": row.ip,
                "userAgent": row.user_agent,
                "active": active,
            }
        )
    return result


def revoke_session_for_user(
    session: Session, *, user_id: int, session_id: int | None
) -> int:
    _get_user(session, user_id)
    if session_id is None:
        return sessions.revoke_user_sessions(session, user_id=user_id)
    if not sessions.revoke_session_by_id(
        session, user_id=user_id, session_id=session_id
    ):
        raise NotFoundError("会话不存在或已失效")
    return 1


# ── 角色管理 ────────────────────────────────────────────────────────
def list_roles(session: Session) -> list[dict]:
    rows = session.execute(select(Role).order_by(Role.code)).scalars().all()
    result = []
    for role in rows:
        perms = sorted(
            session.execute(
                select(RolePermission.permission_code).where(
                    RolePermission.role_code == role.code
                )
            ).scalars()
        )
        user_count = session.execute(
            select(func.count())
            .select_from(UserRole)
            .where(UserRole.role_code == role.code)
        ).scalar_one()
        result.append(
            {
                "code": role.code,
                "name": role.name,
                "description": role.description,
                "apiTier": role.api_tier,
                "isBuiltin": role.is_builtin,
                "permissions": perms,
                "userCount": int(user_count),
            }
        )
    return result


def create_role(
    session: Session,
    *,
    code: str,
    name: str,
    api_tier: str,
    permissions: list[str],
) -> Role:
    normalized = code.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,31}$", normalized):
        raise AccountError("角色 code 需为 2-32 位小写字母/数字/_-")
    if session.get(Role, normalized) is not None:
        raise AccountError(f"角色已存在: {normalized}")
    _validate_tier_and_pages(api_tier, permissions)
    role = Role(
        code=normalized,
        name=name.strip() or normalized,
        api_tier=api_tier,
        is_builtin=False,
    )
    session.add(role)
    session.flush()
    for code_ in permissions:
        session.add(RolePermission(role_code=normalized, permission_code=code_))
    session.commit()
    clear_context_cache()
    return role


def update_role(
    session: Session,
    *,
    code: str,
    name: str | None,
    api_tier: str | None,
    permissions: list[str] | None,
) -> None:
    role = session.get(Role, code)
    if role is None:
        raise NotFoundError(f"角色不存在: {code}")
    if role.is_builtin and code == "admin":
        raise AccountError("内置 admin 角色不可编辑")
    if role.is_builtin and api_tier is not None and api_tier != role.api_tier:
        raise AccountError("内置角色不可改 api_tier")
    if permissions is not None:
        _validate_tier_and_pages(api_tier or role.api_tier, permissions)
    if name is not None:
        role.name = name.strip() or role.code
    if api_tier is not None:
        role.api_tier = api_tier
    session.flush()
    if permissions is not None:
        for row in session.execute(
            select(RolePermission).where(RolePermission.role_code == code)
        ).scalars():
            session.delete(row)
        for perm_code in permissions:
            session.add(RolePermission(role_code=code, permission_code=perm_code))
    session.commit()
    clear_context_cache()


def delete_role(session: Session, *, code: str) -> None:
    role = session.get(Role, code)
    if role is None:
        raise NotFoundError(f"角色不存在: {code}")
    if role.is_builtin:
        raise AccountError("内置角色不可删除")
    in_use = session.execute(
        select(func.count()).select_from(UserRole).where(UserRole.role_code == code)
    ).scalar_one()
    if in_use:
        raise AccountError(f"角色仍被 {in_use} 个用户使用，先移除引用")
    for row in session.execute(
        select(RolePermission).where(RolePermission.role_code == code)
    ).scalars():
        session.delete(row)
    session.delete(role)
    session.commit()
    clear_context_cache()


def sync_permissions(session: Session) -> int:
    """把代码权限点清单 upsert 进 permissions 表；返回写入条数。"""
    from tts_erp_v2.accounts.pages import ALL_PERMISSION_CODES

    count = 0
    for code in ALL_PERMISSION_CODES:
        if session.get(Permission, code) is None:
            session.add(Permission(code=code, kind="page", name=code))
            count += 1
    session.commit()
    return count


def seed_builtin_roles(session: Session) -> None:
    """幂等种子：3 内置角色 + 权限点 + role_permissions（alembic 也调用同源逻辑）."""
    from tts_erp_v2.accounts.pages import ALL_PERMISSION_CODES

    for code in ALL_PERMISSION_CODES:
        if session.get(Permission, code) is None:
            session.add(Permission(code=code, kind="page", name=code))
    session.flush()
    for role_code, (api_tier, page_ids) in BUILTIN_ROLES.items():
        role = session.get(Role, role_code)
        if role is None:
            session.add(
                Role(
                    code=role_code,
                    name=BUILTIN_ROLE_NAMES.get(role_code, role_code),
                    api_tier=api_tier,
                    is_builtin=True,
                )
            )
            session.flush()
        for page_id in page_ids:
            perm_code = f"page:{page_id}"
            exists = session.execute(
                select(RolePermission).where(
                    RolePermission.role_code == role_code,
                    RolePermission.permission_code == perm_code,
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    RolePermission(role_code=role_code, permission_code=perm_code)
                )
    session.commit()


# ── 内部工具 ────────────────────────────────────────────────────────
def _get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"用户不存在: {user_id}")
    return user


def _assert_roles_exist(session: Session, roles: list[str]) -> None:
    for role_code in roles:
        if session.get(Role, role_code) is None:
            raise AccountError(f"角色不存在: {role_code}")


def _assert_not_last_admin(session: Session, user_id: int) -> None:
    """护栏：不允许禁用最后一个挂 admin 角色的 active 用户。"""
    admin_users = session.execute(
        select(func.count())
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_code == "admin", User.status == "active")
    ).scalar_one()
    target_is_admin = (
        session.execute(
            select(func.count())
            .select_from(UserRole)
            .where(UserRole.user_id == user_id, UserRole.role_code == "admin")
        ).scalar_one()
        > 0
    )
    if target_is_admin and admin_users <= 1:
        raise AccountError("不能禁用最后一个 admin 用户")


def _validate_tier_and_pages(api_tier: str, permissions: list[str]) -> None:
    if api_tier not in PERM_TIERS:
        raise AccountError("api_tier 只能是 readonly/readwrite/admin")
    from tts_erp_v2.accounts.pages import PAGE_BY_ID, PAGE_MIN_WRITE_TIER

    for perm_code in permissions:
        if not perm_code.startswith("page:"):
            raise AccountError(f"未知权限点: {perm_code}")
        page_id = perm_code[len("page:") :]
        if page_id not in PAGE_BY_ID:
            raise AccountError(f"未知页面权限点: {perm_code}")
        min_tier = PAGE_MIN_WRITE_TIER.get(page_id)
        if min_tier and PERM_TIERS[api_tier] < PERM_TIERS[min_tier]:
            raise AccountError(
                f"页面 {page_id} 含写操作，api_tier 至少要 {min_tier}"
            )

"""账号运维 CLI（设计 §9.2）：``python -m tts_erp_v2.accounts.cli <command>``。

场景：首次部署建第一个 admin、脚本批量建号、管理页不可用时应急。
密码一律交互式（getpass）或 --password-stdin，绝不走命令行参数（防 ps 泄露）。
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from tts_erp_v2.accounts import service
from tts_erp_v2.accounts.models import User, UserRole
from tts_erp_v2.accounts.passwords import PasswordPolicyError


def _session():
    from tts_erp_v2.db.base import get_session_factory

    return get_session_factory()()


def _read_password(*, confirm: bool = True, stdin: bool = False) -> str:
    if stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("密码: ")
        if confirm:
            again = getpass.getpass("确认密码: ")
            if password != again:
                raise SystemExit("两次输入不一致")
    return password


def _print_error(exc: Exception) -> None:
    print(f"错误: {exc}", file=sys.stderr)


# ── 用户命令 ────────────────────────────────────────────────────────
def cmd_create_user(args: argparse.Namespace) -> int:
    password = _read_password(confirm=not args.password_stdin, stdin=args.password_stdin)
    with _session() as session:
        try:
            user = service.create_user(
                session,
                username=args.username,
                display_name=args.name or args.username,
                password=password,
                roles=args.role or [],
                actor="cli",
            )
        except (service.AccountError, PasswordPolicyError) as exc:
            _print_error(exc)
            return 1
    print(f"已创建用户 {user.username} (id={user.id})，角色: {', '.join(args.role or []) or '（无）'}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    password = _read_password(confirm=not args.password_stdin, stdin=args.password_stdin)
    with _session() as session:
        try:
            user = _find_user(session, args.username)
            service.reset_password(
                session, user_id=user.id, password=password, actor="cli"
            )
        except (service.AccountError, PasswordPolicyError) as exc:
            _print_error(exc)
            return 1
    print(f"已重置 {args.username} 的密码，并吊销其全部会话")
    return 0


def cmd_set_roles(args: argparse.Namespace) -> int:
    with _session() as session:
        try:
            user = _find_user(session, args.username)
            service.set_user_roles(session, user_id=user.id, roles=args.role or [])
        except service.AccountError as exc:
            _print_error(exc)
            return 1
    print(f"已设置 {args.username} 的角色: {', '.join(args.role or []) or '（无）'}")
    return 0


def cmd_grant_role(args: argparse.Namespace) -> int:
    return _mutate_roles(args.username, args.role, add=True)


def cmd_revoke_role(args: argparse.Namespace) -> int:
    return _mutate_roles(args.username, args.role, add=False)


def _mutate_roles(username: str, role: str, *, add: bool) -> int:
    with _session() as session:
        try:
            user = _find_user(session, username)
            current = set(
                session.execute(
                    select(UserRole.role_code).where(UserRole.user_id == user.id)
                ).scalars()
            )
            current = current | {role} if add else current - {role}
            service.set_user_roles(session, user_id=user.id, roles=sorted(current))
        except service.AccountError as exc:
            _print_error(exc)
            return 1
    verb = "授予" if add else "撤销"
    print(f"已{verb} {username} 的角色 {role}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    return _set_status(args.username, "disabled")


def cmd_enable(args: argparse.Namespace) -> int:
    return _set_status(args.username, "active")


def _set_status(username: str, status: str) -> int:
    with _session() as session:
        try:
            user = _find_user(session, username)
            service.set_user_status(session, user_id=user.id, status=status, actor_id=None)
        except service.AccountError as exc:
            _print_error(exc)
            return 1
    print(f"已{'禁用' if status == 'disabled' else '启用'} {username}")
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    with _session() as session:
        rows = service.list_users(session)
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    print(f"{'username':<20} {'display':<16} {'status':<9} {'roles':<24} last_login")
    for r in rows:
        last = r["lastLoginAt"].strftime("%Y-%m-%d %H:%M") if r["lastLoginAt"] else "-"
        print(
            f"{r['username']:<20} {r['displayName']:<16} {r['status']:<9} "
            f"{','.join(r['roles']):<24} {last}"
        )
    return 0


def cmd_show_user(args: argparse.Namespace) -> int:
    with _session() as session:
        user = _find_user(session, args.username)
        detail = service.get_user_detail(session, user.id)
        sessions_ = service.list_user_sessions(session, user.id)
    active = sum(1 for s in sessions_ if s["active"])
    print(f"用户: {detail['username']} ({detail['displayName']})")
    print(f"状态: {detail['status']}  角色: {', '.join(detail['roles']) or '（无）'}")
    print(f"API 档位: {detail['apiTier']}  活跃会话: {active}")
    print("页面权限:")
    for page in detail["pages"]:
        print(f"  - {page}")
    return 0


def cmd_revoke_sessions(args: argparse.Namespace) -> int:
    with _session() as session:
        user = _find_user(session, args.username)
        count = service.revoke_session_for_user(session, user_id=user.id, session_id=None)
    print(f"已吊销 {args.username} 的 {count} 个活跃会话")
    return 0


# ── 角色命令 ────────────────────────────────────────────────────────
def cmd_create_role(args: argparse.Namespace) -> int:
    pages = [p if p.startswith("page:") else f"page:{p}" for p in (args.pages or [])]
    with _session() as session:
        try:
            service.create_role(
                session,
                code=args.code,
                name=args.name or args.code,
                api_tier=args.api_tier,
                permissions=pages,
            )
        except service.AccountError as exc:
            _print_error(exc)
            return 1
    print(f"已创建角色 {args.code} (api_tier={args.api_tier}, 权限点 {len(pages)} 个)")
    return 0


def cmd_edit_role(args: argparse.Namespace) -> int:
    pages = None
    if args.pages is not None:
        pages = [p if p.startswith("page:") else f"page:{p}" for p in args.pages]
    with _session() as session:
        try:
            service.update_role(
                session,
                code=args.code,
                name=args.name,
                api_tier=args.api_tier,
                permissions=pages,
            )
        except service.AccountError as exc:
            _print_error(exc)
            return 1
    print(f"已更新角色 {args.code}")
    return 0


def cmd_list_roles(args: argparse.Namespace) -> int:
    with _session() as session:
        rows = service.list_roles(session)
    for r in rows:
        builtin = " [内置]" if r["isBuiltin"] else ""
        print(f"{r['code']:<12} api_tier={r['apiTier']:<9} 用户 {r['userCount']}{builtin}")
    return 0


def cmd_show_role(args: argparse.Namespace) -> int:
    with _session() as session:
        rows = {r["code"]: r for r in service.list_roles(session)}
    role = rows.get(args.code)
    if role is None:
        print(f"错误: 角色不存在: {args.code}", file=sys.stderr)
        return 1
    print(f"角色: {role['code']} ({role['name']})  api_tier={role['apiTier']}")
    for perm in role["permissions"]:
        print(f"  - {perm}")
    return 0


def cmd_sync_permissions(args: argparse.Namespace) -> int:
    with _session() as session:
        service.seed_builtin_roles(session)
    print("已同步权限点清单与内置角色（幂等）")
    return 0


def _find_user(session, username: str) -> User:
    normalized = service.normalize_username(username)
    user = session.execute(
        select(User).where(User.username == normalized)
    ).scalar_one_or_none()
    if user is None:
        raise service.NotFoundError(f"用户不存在: {username}")
    return user


# ── argparse ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tts_erp_v2.accounts.cli", description="tts-erp 账号运维 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="创建账号（密码交互式输入）")
    p.add_argument("username")
    p.add_argument("--name", help="显示名（默认同用户名）")
    p.add_argument("--role", action="append", help="角色 code，可多次")
    p.add_argument("--password-stdin", action="store_true", help="从 stdin 读密码")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("reset-password", help="重置密码并吊销全部会话")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("set-roles", help="整体替换角色集合")
    p.add_argument("username")
    p.add_argument("--role", action="append")
    p.set_defaults(func=cmd_set_roles)

    p = sub.add_parser("grant-role", help="追加一个角色")
    p.add_argument("username")
    p.add_argument("role")
    p.set_defaults(func=cmd_grant_role)

    p = sub.add_parser("revoke-role", help="撤销一个角色")
    p.add_argument("username")
    p.add_argument("role")
    p.set_defaults(func=cmd_revoke_role)

    for name, func, help_text in (
        ("disable", cmd_disable, "禁用账号并吊销全部会话"),
        ("enable", cmd_enable, "启用账号"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("username")
        p.set_defaults(func=func)

    p = sub.add_parser("list-users", help="用户列表")
    p.add_argument("--status", choices=["active", "disabled"])
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("show-user", help="用户详情（角色/权限点/会话）")
    p.add_argument("username")
    p.set_defaults(func=cmd_show_user)

    p = sub.add_parser("revoke-sessions", help="吊销该用户全部会话")
    p.add_argument("username")
    p.set_defaults(func=cmd_revoke_sessions)

    p = sub.add_parser("create-role", help="创建自定义角色")
    p.add_argument("code")
    p.add_argument("--name")
    p.add_argument("--api-tier", default="readwrite", choices=["readonly", "readwrite", "admin"])
    p.add_argument("--pages", help="逗号分隔页面权限点（page:dashboard,...）")
    p.set_defaults(func=cmd_create_role)

    p = sub.add_parser("edit-role", help="编辑角色")
    p.add_argument("code")
    p.add_argument("--name")
    p.add_argument("--api-tier", choices=["readonly", "readwrite", "admin"])
    p.add_argument("--pages")
    p.set_defaults(func=cmd_edit_role)

    for name, func, help_text in (
        ("list-roles", cmd_list_roles, "角色列表"),
        ("show-role", cmd_show_role, "角色详情（权限点）"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name == "show-role":
            p.add_argument("code")
        p.set_defaults(func=func)

    p = sub.add_parser("sync-permissions", help="同步权限点清单与内置角色（幂等）")
    p.set_defaults(func=cmd_sync_permissions)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # create-role/edit-role 的 --pages 逗号分隔 → list
    if hasattr(args, "pages") and isinstance(args.pages, str):
        args.pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    try:
        return args.func(args)
    except service.NotFoundError as exc:
        _print_error(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

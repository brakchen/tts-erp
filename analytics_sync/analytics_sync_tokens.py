#!/usr/bin/env python3
"""Manage analytics_sync Bearer tokens.

The full token is printed ONCE at creation/rotation; the DB stores only
its SHA-256 hash plus a 16-char prefix for identification. Mirrors
../api_keys.py.

Usage:
    python3 analytics_sync/analytics_sync_tokens.py create --name cron-sync [--expires-days 90]
    python3 analytics_sync/analytics_sync_tokens.py list
    python3 analytics_sync/analytics_sync_tokens.py revoke --prefix anlsync_Kx9vQ2mP
    python3 analytics_sync/analytics_sync_tokens.py rotate --prefix anlsync_Kx9vQ2mP
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PREFIX = "anlsync_"


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _connect() -> psycopg.Connection:
    url = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("ANALYTICS_SYNC_DB_URL")
    if not url:
        sys.exit("TTS_ERP_DB_URL (or ANALYTICS_SYNC_DB_URL) not configured (check .env)")
    return psycopg.connect(url)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _insert_token(conn, name: str | None, scopes: list[str], expires_days: int | None) -> str:
    """Generate a new token, insert its hash + prefix, return the plaintext ONCE."""
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"
    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_sync_tokens
                (key_prefix, key_hash, name, scopes, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (token[:16], _sha256(token), name, scopes, expires_at),
        )
    conn.commit()
    return token


def cmd_create(args) -> None:
    with _connect() as conn:
        token = _insert_token(conn, args.name, args.scopes or [], args.expires_days)
    print(f"name    : {args.name or '-'}")
    print(f"prefix  : {token[:16]}")
    print(f"scopes  : {args.scopes or '(none — token grants full access)'}")
    if args.expires_days:
        print(f"expires : in {args.expires_days} days")
    print()
    print(f"SYNC TOKEN (shown ONCE, store it now):  {token}")


def cmd_list(_args) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT key_prefix, name, scopes, enabled, created_at, last_used_at, expires_at
            FROM analytics_sync_tokens
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    if not rows:
        print("(no sync tokens)")
        return
    fmt = "%Y-%m-%d %H:%M:%S"
    print(f"{'PREFIX':<18} {'NAME':<24} {'SCOPES':<20} {'ON':<3} {'CREATED':<19} {'LAST_USED':<19} {'EXPIRES':<19}")
    for prefix, name, scopes, enabled, created, last_used, expires in rows:
        print(
            f"{prefix:<18} {(name or '-'):<24} {','.join(scopes or []):<20} "
            f"{'Y' if enabled else 'N':<3} {created.strftime(fmt):<19} "
            f"{(last_used.strftime(fmt) if last_used else '-'):<19} "
            f"{(expires.strftime(fmt) if expires else '-'):<19}"
        )


def _lookup_by_prefix(conn, prefix: str) -> tuple[int, bool]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, enabled FROM analytics_sync_tokens WHERE key_prefix = %s",
            (prefix,),
        )
        row = cur.fetchone()
        if not row:
            sys.exit(f"no sync token with prefix {prefix}")
        return row


def cmd_revoke(args) -> None:
    with _connect() as conn:
        token_id, _ = _lookup_by_prefix(conn, args.prefix)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analytics_sync_tokens SET enabled = false WHERE id = %s",
                (token_id,),
            )
        conn.commit()
    print(f"revoked {args.prefix}")


def cmd_rotate(args) -> None:
    """Mint a fresh token and disable the old one in a single transaction."""
    with _connect() as conn:
        old_id, _ = _lookup_by_prefix(conn, args.prefix)
        new_token = _insert_token(conn, args.name, args.scopes or [], args.expires_days)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analytics_sync_tokens SET enabled = false WHERE id = %s",
                (old_id,),
            )
        conn.commit()
    print(f"rotated {args.prefix} → new prefix {new_token[:16]}")
    print()
    print(f"NEW SYNC TOKEN (shown ONCE, store it now):  {new_token}")


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(description="Manage analytics_sync Bearer tokens")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Issue a new sync token")
    p_create.add_argument("--name", help="Operator-friendly label")
    p_create.add_argument(
        "--scopes",
        nargs="*",
        default=[],
        help="Optional scope strings (e.g. seller:1 advertiser:2). MVP: advisory only.",
    )
    p_create.add_argument("--expires-days", type=int, help="TTL in days (default: never)")

    sub.add_parser("list", help="List all sync tokens")

    p_revoke = sub.add_parser("revoke", help="Disable a sync token by prefix")
    p_revoke.add_argument("--prefix", required=True)

    p_rotate = sub.add_parser("rotate", help="Replace a sync token (issues a new plaintext)")
    p_rotate.add_argument("--prefix", required=True)
    p_rotate.add_argument("--name", help="Name on the new token (default: same as old)")
    p_rotate.add_argument("--scopes", nargs="*", help="Scopes on the new token")
    p_rotate.add_argument("--expires-days", type=int)

    args = parser.parse_args()
    if args.cmd == "create":
        cmd_create(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "revoke":
        cmd_revoke(args)
    elif args.cmd == "rotate":
        cmd_rotate(args)


if __name__ == "__main__":
    main()

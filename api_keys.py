#!/usr/bin/env python3
"""Manage tts-erp API keys (design: tech-doc/api-key-auth-design.md).

The full key is printed ONCE at creation/rotation; the DB stores only its
SHA-256 hash plus a 16-char prefix for identification.

Usage:
    python3 api_keys.py create --name cron-sync --role readwrite [--expires-days 90]
    python3 api_keys.py list
    python3 api_keys.py revoke --prefix ttserp_rw_Kx9vQ2mP
    python3 api_keys.py rotate --prefix ttserp_rw_Kx9vQ2mP
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

from scripts._db_url import normalize_db_url

ROOT = Path(__file__).resolve().parent
ROLE_PREFIX = {"readonly": "ro", "readwrite": "rw", "admin": "admin"}


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
    url = os.environ.get("TTS_ERP_DB_URL")
    if not url:
        sys.exit("TTS_ERP_DB_URL not configured (check .env)")
    # .env stores the URL in SQLAlchemy form (postgresql+psycopg://...).
    # Raw psycopg.connect only accepts the plain postgresql:// scheme;
    # normalize first or psycopg3 raises "missing '=' after ...+psycopg".
    return psycopg.connect(normalize_db_url(url))


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _insert_key(
    conn,
    name: str | None,
    role: str,
    scopes: list[str],
    expires_days: int | None,
) -> str:
    key = f"ttserp_{ROLE_PREFIX[role]}_{secrets.token_urlsafe(24)}"
    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (key_hash, key_prefix, name, role, scopes, expires_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (_sha256(key), key[:16], name, role, scopes, expires_at),
        )
    conn.commit()
    return key


def cmd_create(args) -> None:
    with _connect() as conn:
        key = _insert_key(
            conn, args.name, args.role, args.scopes or [], args.expires_days
        )
    print(f"name    : {args.name or '-'}")
    print(f"role    : {args.role}")
    print(f"prefix  : {key[:16]}")
    if args.scopes:
        print(f"scopes  : {','.join(args.scopes)}")
    else:
        print("scopes  : (none — token grants full access)")
    if args.expires_days:
        print(f"expires : in {args.expires_days} days")
    print()
    print(f"API KEY (shown ONCE, store it now):  {key}")


def cmd_list(_args) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key_prefix, name, role, scopes, enabled, created_at, last_used_at, expires_at"
            " FROM api_keys ORDER BY id"
        )
        rows = cur.fetchall()
    if not rows:
        print("(no api keys)")
        return
    fmt = "%Y-%m-%d %H:%M:%S"
    print(
        f"{'PREFIX':<18} {'NAME':<20} {'ROLE':<10} {'SCOPES':<22} {'ON':<3} {'CREATED':<19} {'LAST_USED':<19} {'EXPIRES':<19}"
    )
    for prefix, name, role, scopes, enabled, created, last_used, expires in rows:
        print(
            f"{prefix:<18} {(name or '-'):<20} {role:<10} {','.join(scopes or []):<22} "
            f"{'Y' if enabled else 'N':<3} {created.strftime(fmt):<19} "
            f"{(last_used.strftime(fmt) if last_used else '-'):<19} "
            f"{(expires.strftime(fmt) if expires else '-'):<19}"
        )


def _revoke(conn, prefix: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET enabled = false WHERE key_prefix = %s", (prefix,)
        )
        n = cur.rowcount
    conn.commit()
    return n > 0


def cmd_revoke(args) -> None:
    with _connect() as conn:
        ok = _revoke(conn, args.prefix)
    print(f"revoked: {args.prefix}" if ok else f"NOT FOUND: {args.prefix}")
    if not ok:
        sys.exit(1)
    print("note: in-process caches make revocation effective within ~60s")


def cmd_rotate(args) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, role FROM api_keys WHERE key_prefix = %s AND enabled = true",
                (args.prefix,),
            )
            row = cur.fetchone()
        if not row:
            sys.exit(f"NOT FOUND or already revoked: {args.prefix}")
        name, role = row
        # Default: copy scopes from old key. Explicit --scopes overrides.
        if args.scopes is not None:
            scopes = args.scopes
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT scopes FROM api_keys WHERE key_prefix = %s",
                    (args.prefix,),
                )
                scopes_row = cur.fetchone()
                # cur.fetchone() may return None (prefix just rotated
                # out from under us, or the row's scopes column is
                # NULL); either way, treat as "no scopes carried over".
                scopes = (scopes_row[0] if scopes_row is not None else None) or []
        key = _insert_key(conn, name, role, list(scopes), args.expires_days)
        _revoke(conn, args.prefix)
    print(f"rotated: {args.prefix} -> new key for name={name} role={role}")
    print(f"scopes  : {','.join(scopes) or '(none)'}")
    print()
    print(f"API KEY (shown ONCE, store it now):  {key}")


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(description="tts-erp API key management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="create a new key (printed once)")
    p.add_argument("--name", required=True, help="purpose label, e.g. cron-sync")
    p.add_argument("--role", required=True, choices=sorted(ROLE_PREFIX))
    p.add_argument(
        "--scopes",
        nargs="*",
        default=[],
        help=(
            "Optional scope strings for analytics_sync per-seller restriction. "
            "Format: 'seller:<id>' or 'advertiser:<id>'. "
            "Empty (default) = unrestricted. Examples: "
            "--scopes seller:shop-1 --scopes advertiser:adv-1"
        ),
    )
    p.add_argument("--expires-days", type=int, default=None)
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("list", help="list keys (prefix/role/usage, no secrets)")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("revoke", help="disable a key by prefix")
    p.add_argument("--prefix", required=True)
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser(
        "rotate", help="create a fresh key with same name/role, revoke the old one"
    )
    p.add_argument("--prefix", required=True)
    p.add_argument(
        "--scopes",
        nargs="*",
        default=None,
        help="Override scopes on the new key (default: copy from old)",
    )
    p.add_argument("--expires-days", type=int, default=None)
    p.set_defaults(fn=cmd_rotate)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

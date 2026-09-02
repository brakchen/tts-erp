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
    scopes: list[str],  # ignored: V3 dropped the scopes column
    expires_days: int | None,  # ignored: V3 dropped the expires_at column
) -> str:
    key = f"ttserp_{ROLE_PREFIX[role]}_{secrets.token_urlsafe(24)}"
    # .env's TTS_ERP_DB_URL is shared by both SQLAlchemy and raw psycopg.
    # The V2 → V3 schema split moved api_keys to ``security.api_keys``
    # and dropped the ``scopes`` / ``expires_at`` / ``enabled`` columns
    # in favour of ``status`` (text) + ``rotated_to_key_hash``. The
    # previous version of this function INSERTed into the unqualified
    # ``api_keys`` table — which the V1 schema puts at ``public.api_keys``
    # via Postgres' default ``search_path = "$user", public`` — and
    # therefore wrote rows that the v2 app could never authenticate.
    # The ``security.`` prefix below is mandatory.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security.api_keys "
            "(key_hash, key_prefix, name, role, status) "
            "VALUES (%s, %s, %s, %s, 'active')",
            (_sha256(key), key[:16], name, role),
        )
    conn.commit()
    return key


def cmd_create(args) -> None:
    # ``scopes`` and ``expires_days`` flags are accepted (so any
    # existing scripts in cron / setup docs don't break with
    # "unrecognized argument" errors) but the values are no longer
    # persisted — V3's security.api_keys has no such columns. Print
    # a deprecation note if the caller passed them, so a future
    # operator can spot the legacy flag in their stack.
    if args.scopes:
        print(
            f"note: --scopes={args.scopes!r} is accepted for back-compat "
            f"but no longer persisted (V3 security.api_keys has no scopes column).",
            file=sys.stderr,
        )
    if args.expires_days:
        print(
            f"note: --expires-days={args.expires_days} is accepted for back-compat "
            f"but no longer persisted (V3 has no expires_at column).",
            file=sys.stderr,
        )
    with _connect() as conn:
        key = _insert_key(
            conn, args.name, args.role, args.scopes or [], args.expires_days
        )
    print(f"name    : {args.name or '-'}")
    print(f"role    : {args.role}")
    print(f"prefix  : {key[:16]}")
    print("status  : active")
    print()
    print(f"API KEY (shown ONCE, store it now):  {key}")


def cmd_list(_args) -> None:
    # V3 schema: no scopes / expires_at / enabled columns. status
    # is a text column ('active' / 'disabled'); the legacy "ON" column
    # is replaced with STATUS. Same as the INSERT in _insert_key: the
    # security. prefix is mandatory — without it the unqualified name
    # resolves to public.api_keys (V1 dead table) via the default
    # search_path.
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key_prefix, name, role, status, created_at, last_used_at "
            " FROM security.api_keys ORDER BY created_at, id"
        )
        rows = cur.fetchall()
    if not rows:
        print("(no api keys)")
        return
    fmt = "%Y-%m-%d %H:%M:%S"
    print(
        f"{'PREFIX':<18} {'NAME':<20} {'ROLE':<10} {'STATUS':<8} "
        f"{'CREATED':<19} {'LAST_USED':<19}"
    )
    for prefix, name, role, status, created, last_used in rows:
        print(
            f"{prefix:<18} {(name or '-'):<20} {role:<10} {status:<8} "
            f"{created.strftime(fmt):<19} "
            f"{(last_used.strftime(fmt) if last_used else '-'):<19}"
        )


def _revoke(conn, prefix: str) -> bool:
    # V3 uses status='disabled' (text), not the V1 enabled=false (bool).
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE security.api_keys SET status = 'disabled' "
            "WHERE key_prefix = %s AND status <> 'disabled'",
            (prefix,),
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
                "SELECT name, role FROM security.api_keys "
                "WHERE key_prefix = %s AND status = 'active'",
                (args.prefix,),
            )
            row = cur.fetchone()
        if not row:
            sys.exit(f"NOT FOUND or already revoked: {args.prefix}")
        name, role = row
        # V3 dropped the ``scopes`` column. ``--scopes`` is still
        # accepted (no error) but not persisted; same for
        # ``--expires-days``. The new key inherits name + role only.
        key = _insert_key(conn, name, role, [], None)
        _revoke(conn, args.prefix)
    print(f"rotated: {args.prefix} -> new key for name={name} role={role}")
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
            "Optional scope strings for analytics ingest per-seller restriction. "
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

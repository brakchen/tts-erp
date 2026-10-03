"""One-shot migration: re-encrypt ``integration.credentials.ciphertext`` into the v2 JSON-envelope format.

Background (2026-08-30 incident)
--------------------------------
The v1→v2 migration copied the legacy ``oauth_receiver.oauth_tokens``
``access_token_encrypted`` column *verbatim* into v2's
``integration.credentials.ciphertext`` (confirmed byte-identical at the
time of writing). The legacy column stores ``Fernet(raw_access_token)``
— one plaintext string per column. But v2's
:class:`tts_erp_v2.proxy.token_service.CredentialsView.from_row`
expects ``Fernet(json.dumps({"access_token":..., "refresh_token":...,
"shop_cipher":...}))`` — a single JSON envelope. Result: every v2 job
that loads credentials crashes with
``json.decoder.JSONDecodeError: Expecting value``.

This script fixes the *data* (not the code): for each v2
``integration.credentials`` row whose ``provider='tiktok'``, it:

1. Reads the matching legacy row from ``oauth_receiver.oauth_tokens``.
2. Fernet-decrypts ``access_token_encrypted`` / ``refresh_token_encrypted``
   / ``shop_cipher_encrypted`` (same key: ``TTS_ERP_FERNET_KEY`` mirrors
   ``OAUTH_DB_ENCRYPTION_KEY``).
3. Re-encrypts the three plaintexts as a single JSON envelope using the
   v2 ``encrypt()`` (so ``from_row`` round-trips cleanly).
4. UPDATEs ``integration.credentials.ciphertext`` in place, preserving
   ``expires_at`` / ``granted_scopes`` / ``extra`` / ``account_label``.

Idempotent: running twice reads the legacy row both times, so the second
run just re-writes the same envelope. Safe to re-run after a token
refresh as long as ``oauth_receiver.oauth_tokens`` is the freshest copy
(which it is — oauth-receiver owns the refresh cron per AGENTS.md §8).

Usage::

    set -a && . ./.env && set +a \\
        && .venv/bin/python scripts/migrate_v1_to_v2/re_encrypt_credentials.py

Exit code: 0 = all rows rewritten, 1 = some failed (rows_failed > 0).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from sqlalchemy import select

# Make the repo importable from a script run at repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.migrate_v1_to_v2.common import require_prod_guard
from tts_erp_v2.db.base import get_engine, get_session_factory  # noqa: E402
from tts_erp_v2.db.models.integration import Credentials  # noqa: E402
from tts_erp_v2.proxy.token_service import (  # noqa: E402
    encrypt as v2_encrypt,
)
from tts_erp_v2.proxy.token_service import (  # noqa: E402
    load_credentials,
)


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _legacy_row(conn: psycopg.Connection, shop_id: str) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT access_token_encrypted, refresh_token_encrypted,
                   shop_cipher_encrypted
            FROM oauth_tokens
            WHERE shop_id = %s AND provider = 'tiktok'
            """,
            (shop_id,),
        )
        return cur.fetchone()


def _legacy_decrypt(blob: bytes | memoryview | None) -> str:
    """Decrypt a legacy column. NULL/empty → empty string (envelope omits)."""
    if blob is None:
        return ""
    return _decrypt(bytes(blob))


def _decrypt(blob: bytes) -> str:
    from tts_erp_v2.proxy.token_service import decrypt

    return decrypt(blob)


def _rebuild(shop_id: str) -> tuple[str, int]:
    """Rebuild one shop's ciphertext. Returns (status, code)."""
    legacy_conn = psycopg.connect(os.environ["OAUTH_DB_URL"], connect_timeout=10)
    try:
        row = _legacy_row(legacy_conn, shop_id)
        if row is None:
            return f"no legacy oauth_tokens row for {shop_id}", 1
        at, rt, sc = row
        plaintext_at = _legacy_decrypt(at)
        plaintext_rt = _legacy_decrypt(rt)
        plaintext_sc = _legacy_decrypt(sc)
    finally:
        legacy_conn.close()

    if not plaintext_at:
        return f"legacy access_token empty for {shop_id}", 1

    # Build + write the v2 envelope through the v2 token_service path so
    # the stored format is byte-identical to what a fresh OAuth callback
    # would produce (single source of truth).
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        existing = session.execute(
            select(Credentials).where(
                Credentials.provider == "tiktok",
                Credentials.external_account_id == shop_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            return f"no v2 credentials row for {shop_id}", 1

        envelope = {
            "access_token": plaintext_at,
        }
        if plaintext_rt:
            envelope["refresh_token"] = plaintext_rt
        if plaintext_sc:
            envelope["shop_cipher"] = plaintext_sc
        new_ciphertext = v2_encrypt(json.dumps(envelope, ensure_ascii=False))

        existing.ciphertext = new_ciphertext
        session.commit()

        # Verify round-trip through the v2 loader (catches any format drift).
        check = load_credentials(session, "tiktok", shop_id)
        if check is None:
            return f"round-trip load returned None for {shop_id}", 1
        if check.access_token != plaintext_at:
            return f"round-trip access_token mismatch for {shop_id}", 1
        if check.shop_cipher != (plaintext_sc or None):
            return f"round-trip shop_cipher mismatch for {shop_id}", 1
        return (
            f"{shop_id}: OK (at_len={len(plaintext_at)}, rt={'yes' if plaintext_rt else 'no'}, sc={'yes' if plaintext_sc else 'no'})",
            0,
        )
    except Exception as e:  # noqa: BLE001 — report + continue
        session.rollback()
        return f"{shop_id}: FAILED {type(e).__name__}: {e}", 1
    finally:
        session.close()


def main() -> int:
    # 2026-08-30 incident guard: this script always rewrites ciphertext
    # in place (no dry_run mode), so it requires explicit opt-in via the
    # kill-switch. We pass dry_run=False so the guard refuses on missing
    # env var — there's no safe path that writes to the prod DB.
    require_prod_guard(dry_run=False, action="re_encrypt_credentials.main()")
    _load_env()
    if not os.environ.get("OAUTH_DB_URL"):
        print("OAUTH_DB_URL not set in .env — abort", file=sys.stderr)
        return 2
    if not os.environ.get("TTS_ERP_FERNET_KEY"):
        print("TTS_ERP_FERNET_KEY not set in .env — abort", file=sys.stderr)
        return 2

    # Discover v2 tiktok credential rows.
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(Credentials.external_account_id)
            .where(Credentials.provider == "tiktok")
            .order_by(Credentials.external_account_id)
        ).all()
    shop_ids = [r[0] for r in rows]

    if not shop_ids:
        print("no provider='tiktok' credentials rows to migrate")
        return 0

    failed = 0
    for shop_id in shop_ids:
        msg, code = _rebuild(shop_id)
        print(msg)
        failed += code

    print(
        f"\n{'ALL OK' if failed == 0 else f'{failed} FAILED'} ({len(shop_ids)} shops)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

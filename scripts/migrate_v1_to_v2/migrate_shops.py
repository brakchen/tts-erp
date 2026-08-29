"""Migrate shops + oauth_tokens → channel_accounts + credentials.

Source tables (read-only):
  * public.shops                  (2 rows)
  * oauth_receiver.oauth_tokens   (2 rows; on a separate DB on the same host)

Target tables (v2, writeable):
  * commerce.channel_accounts     (UNIQUE platform, external_account_id)
  * integration.credentials       (UNIQUE provider, external_account_id)

Idempotency: upserts on the unique constraint above. Re-running with no
data delta is a no-op. Re-running after source additions upserts the new
rows; source removals do NOT propagate (no delete).

MOCK_SHOP_12345 is excluded from BOTH source tables — it's the synthetic
test row that the legacy startup-lifespan ``backfill`` propagates.

Implementation notes:
  * SQL is plain string + psycopg ``%(name)s`` pyformat placeholders, passed
    via ``conn.exec_driver_sql()``. See ``migrate_logistics.py`` for rationale.

Run order:
    migrate_shops.py    --dry-run
    migrate_shops.py    --batch-size 500

The script is safe to re-run between steps (idempotent).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from scripts.migrate_v1_to_v2.common import (
    MOCK_SHOP_ID,
    DryRunSink,
    epoch_seconds_to_utc,
    get_oauth_engine,
    get_source_engine,
    get_target_engine,
    is_real_shop_id,
    iter_batches,
)


@dataclass
class MigrationStats:
    """Counters surfaced to stdout at the end of a run."""

    shops_seen: int = 0
    shops_skipped_mock: int = 0
    shops_skipped_blank: int = 0
    oauth_seen: int = 0
    oauth_skipped_mock: int = 0
    oauth_skipped_blank: int = 0
    accounts_upserted: int = 0
    credentials_upserted: int = 0

    def report(self, dry_run: bool) -> str:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        return (
            f"{mode} shops migration:\n"
            f"  source public.shops         seen={self.shops_seen} "
            f"skipped(mock)={self.shops_skipped_mock} skipped(blank)={self.shops_skipped_blank}\n"
            f"  source oauth_receiver.oauth_tokens  seen={self.oauth_seen} "
            f"skipped(mock)={self.oauth_skipped_mock} skipped(blank)={self.oauth_skipped_blank}\n"
            f"  commerce.channel_accounts   upserted={self.accounts_upserted}\n"
            f"  integration.credentials     upserted={self.credentials_upserted}\n"
        )


# ─── source readers ──────────────────────────────────────────────────


_SHOP_SQL = (
    "SELECT shop_id, shop_name, shop_region, seller_type, "
    "       last_seen_at, created_at, updated_at "
    "FROM public.shops"
)


_OAUTH_SQL = (
    "SELECT shop_id, provider, "
    "       access_token_encrypted, refresh_token_encrypted, "
    "       shop_cipher_encrypted, shop_name, shop_region, seller_type, "
    "       access_token_expires_at, refresh_token_expires_at, "
    "       granted_scopes, created_at, updated_at "
    "FROM oauth_tokens"
)


def _iter_shop_rows(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_SHOP_SQL).mappings():
            yield dict(row)


def _iter_oauth_rows(oauth: Engine) -> Iterator[dict]:
    with oauth.connect() as conn:
        for row in conn.exec_driver_sql(_OAUTH_SQL).mappings():
            yield dict(row)


# ─── upsert writers ──────────────────────────────────────────────────


_UPSERT_ACCOUNT = (
    "INSERT INTO commerce.channel_accounts "
    "    (platform, external_account_id, account_name, region, seller_type, "
    "     status, credential_id, source_updated_at) "
    "VALUES "
    "    (%(platform)s, %(external_account_id)s, %(account_name)s, "
    "     %(region)s, %(seller_type)s, %(status)s, %(credential_id)s, "
    "     %(source_updated_at)s) "
    "ON CONFLICT (platform, external_account_id) DO UPDATE SET "
    "    account_name      = EXCLUDED.account_name, "
    "    region            = EXCLUDED.region, "
    "    seller_type       = EXCLUDED.seller_type, "
    "    status            = COALESCE(EXCLUDED.status, "
    "                               commerce.channel_accounts.status), "
    "    credential_id     = COALESCE(EXCLUDED.credential_id, "
    "                               commerce.channel_accounts.credential_id), "
    "    source_updated_at = EXCLUDED.source_updated_at, "
    "    synced_at         = now() "
    "RETURNING id"
)


_UPSERT_CREDENTIAL = (
    "INSERT INTO integration.credentials "
    "    (provider, external_account_id, account_label, "
    "     ciphertext, company_secret_ciphertext, "
    "     expires_at, granted_scopes, "
    "     extra, created_at, updated_at) "
    "VALUES "
    "    (%(provider)s, %(external_account_id)s, %(account_label)s, "
    "     %(ciphertext)s, %(company_secret_ciphertext)s, "
    "     %(expires_at)s, CAST(%(granted_scopes)s AS jsonb), "
    "     CAST(%(extra)s AS jsonb), "
    "     COALESCE(%(created_at)s, now()), "
    "     COALESCE(%(updated_at)s, now())) "
    "ON CONFLICT (provider, external_account_id) DO UPDATE SET "
    "    account_label  = EXCLUDED.account_label, "
    "    ciphertext     = EXCLUDED.ciphertext, "
    "    expires_at     = COALESCE(EXCLUDED.expires_at, "
    "                             integration.credentials.expires_at), "
    "    granted_scopes = EXCLUDED.granted_scopes, "
    "    extra          = EXCLUDED.extra, "
    "    updated_at     = now() "
    "RETURNING id"
)


def _upsert_account(
    target: Engine, *, platform: str, external_account_id: str,
    account_name: str | None, region: str | None, seller_type: str | None,
    status: str | None, credential_id: int | None,
    source_updated_at, dry_run: bool,
) -> int | None:
    """Upsert a channel_account row; returns its DB id (None if dry-run)."""
    if dry_run:
        return None
    with target.connect() as conn, conn.begin():
        row = conn.exec_driver_sql(
            _UPSERT_ACCOUNT,
            {
                "platform": platform,
                "external_account_id": external_account_id,
                "account_name": account_name,
                "region": region,
                "seller_type": seller_type,
                "status": status,
                "credential_id": credential_id,
                "source_updated_at": source_updated_at,
            },
        ).first()
    return int(row[0]) if row else None


def _upsert_credential(
    target: Engine, *, provider: str, external_account_id: str,
    account_label: str | None, ciphertext: bytes,
    company_secret_ciphertext: bytes | None,
    expires_at, granted_scopes: list | None,
    extra: dict | None, created_at, updated_at, dry_run: bool,
) -> int | None:
    """Upsert a credentials row; returns its DB id (None if dry-run)."""
    if dry_run:
        return None
    with target.connect() as conn, conn.begin():
        row = conn.exec_driver_sql(
            _UPSERT_CREDENTIAL,
            {
                "provider": provider,
                "external_account_id": external_account_id,
                "account_label": account_label,
                "ciphertext": bytes(ciphertext),
                "company_secret_ciphertext": company_secret_ciphertext,
                "expires_at": expires_at,
                "granted_scopes": json.dumps(granted_scopes)
                    if granted_scopes is not None else None,
                "extra": json.dumps(extra) if extra is not None else None,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        ).first()
    return int(row[0]) if row else None


# ─── main pipeline ──────────────────────────────────────────────────


def run(dry_run: bool = False, batch_size: int = 500,
        verbose: bool = True) -> MigrationStats:
    stats = MigrationStats()
    sink = DryRunSink()
    source = get_source_engine()
    oauth = get_oauth_engine()
    target = get_target_engine()

    # Pass 1 — collect real shops (skip mock + blank).
    real_shops: list[dict] = []
    for row in _iter_shop_rows(source):
        stats.shops_seen += 1
        if row["shop_id"] == MOCK_SHOP_ID:
            stats.shops_skipped_mock += 1
            continue
        if not is_real_shop_id(row["shop_id"]):
            stats.shops_skipped_blank += 1
            continue
        real_shops.append(row)

    # Pass 2 — collect real oauth tokens.
    real_oauth: list[dict] = []
    for row in _iter_oauth_rows(oauth):
        stats.oauth_seen += 1
        if row["shop_id"] == MOCK_SHOP_ID:
            stats.oauth_skipped_mock += 1
            continue
        if not is_real_shop_id(row["shop_id"]):
            stats.oauth_skipped_blank += 1
            continue
        real_oauth.append(row)

    # Index oauth by shop_id for credential lookup during account upsert.
    oauth_by_shop = {r["shop_id"]: r for r in real_oauth}

    # Upsert credentials first (channel_accounts has FK to it).
    credential_ids: dict[str, int] = {}
    for batch in iter_batches(real_oauth, batch_size):
        for o in batch:
            expires_at = epoch_seconds_to_utc(o.get("access_token_expires_at"))
            granted = o.get("granted_scopes")
            cred_id = _upsert_credential(
                target,
                provider=o["provider"],
                external_account_id=o["shop_id"],
                account_label=o.get("shop_name"),
                ciphertext=bytes(o["access_token_encrypted"]),
                company_secret_ciphertext=None,
                expires_at=expires_at,
                granted_scopes=list(granted) if granted else None,
                extra={
                    "refresh_token_expires_at":
                        o.get("refresh_token_expires_at"),
                    "shop_region": o.get("shop_region"),
                    "seller_type": o.get("seller_type"),
                    "shop_cipher_len":
                        len(bytes(o["shop_cipher_encrypted"]))
                        if o.get("shop_cipher_encrypted") else 0,
                },
                created_at=o.get("created_at"),
                updated_at=o.get("updated_at"),
                dry_run=dry_run,
            )
            stats.credentials_upserted += 1
            if cred_id is not None:
                credential_ids[o["shop_id"]] = cred_id
            sink.record("integration.credentials", 1)

    # Upsert channel_accounts, linking to credential row when present.
    for batch in iter_batches(real_shops, batch_size):
        for s in batch:
            oauth_row = oauth_by_shop.get(s["shop_id"])
            cred_id = credential_ids.get(s["shop_id"])
            source_updated_at = s.get("updated_at") or s.get("last_seen_at")
            # Promote oauth provider to channel_accounts.platform ('tiktok' default).
            platform = (oauth_row or {}).get("provider") or "tiktok"
            _upsert_account(
                target,
                platform=platform,
                external_account_id=s["shop_id"],
                account_name=s.get("shop_name")
                or (oauth_row or {}).get("shop_name"),
                region=s.get("shop_region")
                or (oauth_row or {}).get("shop_region"),
                seller_type=s.get("seller_type")
                or (oauth_row or {}).get("seller_type"),
                status="active",
                credential_id=cred_id,
                source_updated_at=source_updated_at,
                dry_run=dry_run,
            )
            stats.accounts_upserted += 1
            sink.record("commerce.channel_accounts", 1)

    if verbose:
        # Avoid the unused 'batch_size' ruff complaint.
        _ = batch_size
        print(stats.report(dry_run=dry_run))
        if dry_run:
            print(sink.report())
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate shops + oauth_tokens → channel_accounts + credentials."
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Report the migration plan without writing.")
    p.add_argument("--batch-size", type=int, default=500,
                   help="Rows per upsert batch (default 500).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the final summary print.")
    return p.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    args = _parse_args()
    run(dry_run=args.dry_run, batch_size=args.batch_size,
        verbose=not args.quiet)
    sys.exit(0)

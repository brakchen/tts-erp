#!/usr/bin/env python3
"""Store a Miaoshou browser ERP session in encrypted credentials.

Usage (interactive; secrets are hidden and never enter shell history):

    python3 scripts/configure_miaoshou_web_session.py \
      --account-id 12629145 --front-version 1790677442555 --confirm

The first prompt expects the complete Cookie header value. The second expects
``x-app-zebra``. Both are encrypted by ``token_service``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_secret(path: str | None, prompt: str) -> str:
    if path:
        file_path = Path(path)
        mode = file_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise SystemExit(f"secret file must be chmod 600: {file_path}")
        return file_path.read_text().strip()
    return getpass.getpass(prompt).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True, help="Miaoshou accountId")
    parser.add_argument("--cookie-file", help="chmod 600 file containing Cookie header")
    parser.add_argument("--zebra-file", help="chmod 600 file containing x-app-zebra")
    parser.add_argument("--front-version", default="")
    parser.add_argument("--base-url", default="https://erp.91miaoshou.com")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required")

    _load_env()
    cookie = _read_secret(args.cookie_file, "Miaoshou Cookie: ")
    zebra = _read_secret(args.zebra_file, "Miaoshou x-app-zebra: ")
    if not cookie or not zebra:
        raise SystemExit("cookie and x-app-zebra must not be empty")

    from tts_erp_v2.db.base import get_engine, get_session_factory
    from tts_erp_v2.proxy.token_service import mask_secret, upsert_credentials

    session_factory = get_session_factory(get_engine())
    with session_factory() as session:
        row = upsert_credentials(
            session,
            provider="miaoshou_web",
            external_account_id=args.account_id.strip(),
            account_label=f"Miaoshou browser ERP account {args.account_id.strip()}",
            plaintext_access_token=cookie,
            plaintext_refresh_token=zebra,
            extra={
                "base_url": args.base_url.rstrip("/"),
                "front_version": args.front_version.strip(),
                "referer": "/order/purchase_record",
            },
        )
        session.commit()
        print(
            "saved credential",
            f"id={row.id}",
            "provider=miaoshou_web",
            f"account={row.external_account_id}",
            f"cookie={mask_secret(cookie)}",
            f"zebra={mask_secret(zebra)}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

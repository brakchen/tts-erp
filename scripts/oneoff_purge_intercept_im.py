#!/usr/bin/env python3
"""Purge IM/ticket rows already covered by blacklist configs 317–328.

Only touches ``plugin.intercepted_requests``. Does not delete configs,
sessions, or any non-IM endpoint.

Targets
-------
- api16 IM prefixes: /api/ticket/*, /api/v1/shop_im/*,
  /api/v1/seller/message/*, /api/v2/seller/message/*, /helpdesk/im/*,
  /chat/*, /assist_chat/*, /api/v1/sellerassistant/*,
  /api/v1/oec/affiliate/seller/im/*
- seller /api/ticket/*
- oec-im-tt-sg.tiktokglobalshopv.com /v1/message/* and /v2/message/*

USAGE
-----
    python3 scripts/oneoff_purge_intercept_im.py
    ALLOW_PROD_DESTRUCTIVE=1 python3 scripts/oneoff_purge_intercept_im.py --confirm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tts_erp_v2.api.deps import require_destructive_script_guard  # noqa: E402
from tts_erp_v2.db import get_engine, get_session_factory  # noqa: E402

load_dotenv()

_SCRIPT_NAME = "oneoff_purge_intercept_im"

_API16 = "api16-normal-sg.tiktokshopglobalselling.com"
_SELLER = "seller.tiktokshopglobalselling.com"
_OEC_IM = "oec-im-tt-sg.tiktokglobalshopv.com"

_API16_IM_PREFIXES = (
    "/api/ticket/",
    "/api/v1/shop_im/",
    "/api/v1/seller/message/",
    "/api/v2/seller/message/",
    "/helpdesk/im/",
    "/chat/",
    "/assist_chat/",
    "/api/v1/sellerassistant/",
    "/api/v1/oec/affiliate/seller/im/",
)

_WHERE = """
(
    (
        endpoint_host = :api16
        AND (
            endpoint_path LIKE :p_ticket
            OR endpoint_path LIKE :p_shop_im
            OR endpoint_path LIKE :p_seller_msg_v1
            OR endpoint_path LIKE :p_seller_msg_v2
            OR endpoint_path LIKE :p_helpdesk
            OR endpoint_path LIKE :p_chat
            OR endpoint_path LIKE :p_assist_chat
            OR endpoint_path LIKE :p_seller_assistant
            OR endpoint_path LIKE :p_affiliate_im
        )
    )
    OR (
        endpoint_host = :seller
        AND endpoint_path LIKE :p_ticket
    )
    OR (
        endpoint_host = :oec_im
        AND (
            endpoint_path LIKE :p_oec_v1
            OR endpoint_path LIKE :p_oec_v2
        )
    )
)
"""


def _params(*, batch: int | None = None) -> dict[str, object]:
    params: dict[str, object] = {
        "api16": _API16,
        "seller": _SELLER,
        "oec_im": _OEC_IM,
        "p_ticket": _API16_IM_PREFIXES[0] + "%",
        "p_shop_im": _API16_IM_PREFIXES[1] + "%",
        "p_seller_msg_v1": _API16_IM_PREFIXES[2] + "%",
        "p_seller_msg_v2": _API16_IM_PREFIXES[3] + "%",
        "p_helpdesk": _API16_IM_PREFIXES[4] + "%",
        "p_chat": _API16_IM_PREFIXES[5] + "%",
        "p_assist_chat": _API16_IM_PREFIXES[6] + "%",
        "p_seller_assistant": _API16_IM_PREFIXES[7] + "%",
        "p_affiliate_im": _API16_IM_PREFIXES[8] + "%",
        "p_oec_v1": "/v1/message/%",
        "p_oec_v2": "/v2/message/%",
    }
    if batch is not None:
        params["batch"] = batch
    return params


_COUNT_SQL = text(
    f"""
    SELECT endpoint_host, endpoint_path, count(*) AS n
    FROM plugin.intercepted_requests
    WHERE {_WHERE}
    GROUP BY 1, 2
    ORDER BY n DESC
    """
)
_TOTAL_SQL = text(f"SELECT count(*) FROM plugin.intercepted_requests WHERE {_WHERE}")
_DELETE_SQL = text(
    f"""
    DELETE FROM plugin.intercepted_requests
    WHERE id IN (
        SELECT id FROM plugin.intercepted_requests
        WHERE {_WHERE}
        LIMIT :batch
    )
    """
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Purge intercept IM/ticket rows"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete (without this flag: dry-run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="Rows per DELETE batch (default: 10000)",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        sys.exit("--batch-size must be >= 1")

    require_destructive_script_guard(
        script_name=_SCRIPT_NAME,
        confirmation=args.confirm,
        dangerous=args.confirm,
    )

    engine = get_engine()
    Session = get_session_factory(engine)
    sess = Session()
    try:
        rows = list(sess.execute(_COUNT_SQL, _params()))
        total = int(sess.execute(_TOTAL_SQL, _params()).scalar() or 0)
        print("table=plugin.intercepted_requests")
        print("breakdown:")
        for host, path, n in rows:
            print(f"  {host} {path}  {n:,}")
        print(f"target rows: {total:,}")

        if total == 0:
            print("Nothing to purge.")
            return
        if not args.confirm:
            print("DRY-RUN — pass --confirm to execute.")
            return

        deleted = 0
        while True:
            result = sess.execute(_DELETE_SQL, _params(batch=args.batch_size))
            batch_deleted = int(result.rowcount or 0)
            sess.commit()
            deleted += batch_deleted
            print(f"  deleted {deleted:,} / {total:,} ...")
            if batch_deleted < args.batch_size:
                break

        leftover = int(sess.execute(_TOTAL_SQL, _params()).scalar() or 0)
        print(f"\nDone. Purged {deleted:,} rows; leftover={leftover:,}.")
        if leftover:
            sys.exit(1)
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    main()

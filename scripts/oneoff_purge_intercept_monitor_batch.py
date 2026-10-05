#!/usr/bin/env python3
"""Purge Chrome intercept telemetry for monitor_browser/collect/batch.

Only touches ``plugin.intercepted_requests``. Does not delete configs,
sessions, or any other endpoint.

USAGE
-----
    # 1. dry-run (default): print counts, no writes
    python3 scripts/oneoff_purge_intercept_monitor_batch.py

    # 2. execute on prod-shape db
    ALLOW_PROD_DESTRUCTIVE=1 python3 scripts/oneoff_purge_intercept_monitor_batch.py --confirm
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

# Prefix match: /monitor_browser/collect/batch/ and /.../batch/security/
_PATH_PREFIX = "/monitor_browser/collect/batch%"
_SCRIPT_NAME = "oneoff_purge_intercept_monitor_batch"

_COUNT_SQL = text(
    """
    SELECT endpoint_host, endpoint_path, count(*) AS n
    FROM plugin.intercepted_requests
    WHERE endpoint_path LIKE :path_prefix
    GROUP BY 1, 2
    ORDER BY n DESC
    """
)
_TOTAL_SQL = text(
    """
    SELECT count(*)
    FROM plugin.intercepted_requests
    WHERE endpoint_path LIKE :path_prefix
    """
)
_DELETE_SQL = text(
    """
    DELETE FROM plugin.intercepted_requests
    WHERE id IN (
        SELECT id
        FROM plugin.intercepted_requests
        WHERE endpoint_path LIKE :path_prefix
        LIMIT :batch
    )
    """
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Purge intercept monitor_browser/collect/batch rows"
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
    params = {"path_prefix": _PATH_PREFIX}
    try:
        rows = list(sess.execute(_COUNT_SQL, params))
        total = int(sess.execute(_TOTAL_SQL, params).scalar() or 0)
        print(f"table=plugin.intercepted_requests")
        print(f"where=endpoint_path LIKE '{_PATH_PREFIX}'")
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
            result = sess.execute(
                _DELETE_SQL, {**params, "batch": args.batch_size}
            )
            batch_deleted = int(result.rowcount or 0)
            sess.commit()
            deleted += batch_deleted
            print(f"  deleted {deleted:,} / {total:,} ...")
            if batch_deleted < args.batch_size:
                break

        leftover = int(sess.execute(_TOTAL_SQL, params).scalar() or 0)
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

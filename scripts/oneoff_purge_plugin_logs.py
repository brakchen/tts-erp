#!/usr/bin/env python3
"""Purge plugin.plugin_logs rows older than N days.

Usage:
    python scripts/oneoff_purge_plugin_logs.py --days 3 --confirm
    python scripts/oneoff_purge_plugin_logs.py --days 3             # dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── project root on sys.path (must precede tts_erp_v2 imports) ───────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tts_erp_v2.api.deps import require_destructive_script_guard  # noqa: E402
from tts_erp_v2.db import get_engine, get_session_factory  # noqa: E402

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge old plugin_logs rows")
    parser.add_argument(
        "--days", type=int, default=3, help="Keep rows newer than N days (default: 3)"
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

    dangerous = args.confirm
    require_destructive_script_guard(
        script_name="oneoff_purge_plugin_logs",
        confirmation=args.confirm,
        dangerous=dangerous,
    )

    engine = get_engine()
    Session = get_session_factory(engine)
    sess = Session()
    try:
        # Count target rows
        r = sess.execute(
            text(
                "SELECT count(*) FROM plugin.plugin_logs WHERE received_at < now() - interval '1 day' * :days"
            ),
            {"days": args.days},
        )
        total = r.scalar()
        print(f"Target rows (> {args.days} days old): {total:,}")

        if total == 0:
            print("Nothing to purge.")
            return

        if not args.confirm:
            print("DRY-RUN — pass --confirm to execute.")
            return

        # Batched DELETE to avoid long-held locks
        deleted = 0
        while True:
            r = sess.execute(
                text("""
                    DELETE FROM plugin.plugin_logs
                    WHERE id IN (
                        SELECT id FROM plugin.plugin_logs
                        WHERE received_at < now() - interval '1 day' * :days
                        LIMIT :batch
                    )
                """),
                {"days": args.days, "batch": args.batch_size},
            )
            batch_deleted = r.rowcount
            sess.commit()
            deleted += batch_deleted
            print(f"  deleted {deleted:,} / {total:,} ...")
            if batch_deleted < args.batch_size:
                break

        print(f"\nDone. Purged {deleted:,} rows from plugin.plugin_logs.")
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    main()

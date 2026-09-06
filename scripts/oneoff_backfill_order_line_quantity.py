"""oneoff_backfill_order_line_quantity.py

Backfill ``commerce.sales_order_lines.quantity`` NULL rows to 1.

Root cause (2026-09-06): TikTok /order/202309/orders/search ``line_items[]``
is one row PER PIECE and does NOT carry a ``quantity`` field (verified on
live payloads: 0/3686 lines have it; every one of the 472 historical rows
is exactly 1; 420/452 orders reconcile payment == Σ line price with qty=1).
Multi-piece orders arrive as repeated identical lines with distinct
line_ids. Since ~8/18 the parser stored NULL when the field was missing,
zeroing all line-aggregated revenue reports from 8/31 onward.

jobs/tiktok/orders.py::_parse_line_payload now defaults to 1 for future
syncs; this script repairs the historical rows (idempotent: only touches
rows where quantity IS NULL).

Usage:
    .venv/bin/python scripts/oneoff_backfill_order_line_quantity.py        # dry-run
    .venv/bin/python scripts/oneoff_backfill_order_line_quantity.py --apply
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
)

from sqlalchemy import func, select, update  # noqa: E402

from tts_erp_v2.db.base import get_session_factory  # noqa: E402
from tts_erp_v2.db.models import SalesOrderLine  # noqa: E402

APPLY = "--apply" in sys.argv


def main() -> int:
    sf = get_session_factory()
    with sf() as sess:  # type: Session
        total = sess.execute(
            select(func.count())
            .select_from(SalesOrderLine)
            .where(SalesOrderLine.quantity.is_(None))
        ).scalar_one()
        with_price = sess.execute(
            select(func.count())
            .select_from(SalesOrderLine)
            .where(
                SalesOrderLine.quantity.is_(None),
                SalesOrderLine.unit_price.is_not(None),
            )
        ).scalar_one()
        print(f"quantity IS NULL 总行数: {total} (其中 unit_price 非空: {with_price})")

        if not APPLY:
            print("dry-run: 加 --apply 才会执行 UPDATE。")
            return 0

        affected = sess.execute(
            update(SalesOrderLine)
            .where(SalesOrderLine.quantity.is_(None))
            .values(
                quantity=1,
                updated_at=datetime.now(timezone.utc),
            )
        ).rowcount
        sess.commit()
        left = sess.execute(
            select(func.count())
            .select_from(SalesOrderLine)
            .where(SalesOrderLine.quantity.is_(None))
        ).scalar_one()
        print(f"已回填 {affected} 行 quantity=1；剩余 quantity IS NULL: {left}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

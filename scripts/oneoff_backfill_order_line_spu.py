"""oneoff_backfill_order_line_spu.py

Backfill ``commerce.sales_order_lines.spu_pk`` NULL rows from the product
catalog.

Root cause (2026-09-06): ``sales_order_lines.spu_pk`` was only ever
backfilled once on 2026-08-31. The orders / order_detail jobs wrote lines
with ``spu_pk`` left NULL ("later join" comment in jobs/tiktok/orders.py),
and no job performed that join afterwards — every line synced after 08-31
stayed unattributed and dropped out of SPU-level reports (ROI board order
count / GMV / effective sales).

Fix summary:
* jobs/tiktok/orders.py + order_detail.py now resolve the catalog at write
  time (spu_link.spu_map_for_shop) so new lines link immediately.
* jobs/tiktok/products.py runs ``spu_link.backfill_null_line_spu_pk`` after
  each sync so lines that landed before their product was synced get linked.
* This script repairs the historical population (idempotent: only touches
  rows where ``spu_pk IS NULL`` and the product snapshot exists in the same
  shop's ``products_spu``).

Usage:
    .venv/bin/python scripts/oneoff_backfill_order_line_spu.py        # dry-run
    .venv/bin/python scripts/oneoff_backfill_order_line_spu.py --apply
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
)

from sqlalchemy import func, select, text

from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.db.models import SalesOrderLine

APPLY = "--apply" in sys.argv

#: Same SQL as tts_erp_v2/jobs/tiktok/spu_link.backfill_null_line_spu_pk
#: (shop_pk=None = 全库). Kept here verbatim so the oneoff stays
#: self-contained and readable.
_BACKFILL_SQL = text(
    """
    UPDATE commerce.sales_order_lines sl
    SET spu_pk = cp.id, updated_at = :now
    FROM commerce.products_spu cp
    WHERE sl.spu_pk IS NULL
      AND cp.spu_id = sl.external_product_id_snapshot
      AND EXISTS (SELECT 1 FROM commerce.sales_orders so
                  WHERE so.id = sl.order_pk AND so.shop_pk = cp.shop_pk)
    """
)


def main() -> int:
    sf = get_session_factory()
    with sf() as sess:
        total_null = sess.execute(
            select(func.count())
            .select_from(SalesOrderLine)
            .where(SalesOrderLine.spu_pk.is_(None))
        ).scalar_one()
        linkable = sess.execute(
            text(
                """
                SELECT count(*)
                FROM commerce.sales_order_lines sl
                JOIN commerce.products_spu cp
                  ON cp.spu_id = sl.external_product_id_snapshot
                WHERE sl.spu_pk IS NULL
                  AND EXISTS (SELECT 1 FROM commerce.sales_orders so
                              WHERE so.id = sl.order_pk
                                AND so.shop_pk = cp.shop_pk)
                """
            )
        ).scalar_one()
        print(f"spu_pk IS NULL 总行数: {total_null} (其中可关联: {linkable})")
        if not APPLY:
            print("dry-run: 加 --apply 才会执行 UPDATE。")
            return 0
        affected = sess.execute(
            _BACKFILL_SQL, {"now": datetime.now(UTC)}
        ).rowcount
        sess.commit()
        left = sess.execute(
            select(func.count())
            .select_from(SalesOrderLine)
            .where(SalesOrderLine.spu_pk.is_(None))
        ).scalar_one()
        print(f"已回填 {affected} 行 spu_pk；剩余 spu_pk IS NULL: {left}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

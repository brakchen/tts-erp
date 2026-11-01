"""Preview historical plugin shop IDs; --apply inserts missing shop metadata.

Run after migrations 0071/0072 with TTS_ERP_DB_URL set. No existing shop,
credentials or plugin facts are changed. Names/regions are left unknown.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from tts_erp_v2.commerce.shop_metadata import upsert_shop_metadata
from tts_erp_v2.db.base import get_session_factory


_CANDIDATES = text("""
    WITH observed AS (
        SELECT seller_id AS shop_id FROM plugin.ad_today
        UNION SELECT seller_id FROM plugin.ad_daily
        UNION SELECT seller_id FROM plugin.plugin_logs
        UNION SELECT shop_id FROM plugin.monitor_health
    )
    SELECT shop_id FROM observed
    WHERE shop_id ~ '^[0-9]+$' AND NOT EXISTS (
        SELECT 1 FROM commerce.shops s
        WHERE s.platform = 'tiktok' AND s.shop_id = observed.shop_id
    ) ORDER BY shop_id
""")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="insert the previewed missing shops")
    args = parser.parse_args()
    with get_session_factory()() as session:
        shop_ids = list(session.scalars(_CANDIDATES))
        for shop_id in shop_ids:
            print(shop_id)
            if args.apply:
                upsert_shop_metadata(session, shop_id=shop_id)
        if args.apply:
            session.commit()
        print(f"{'Inserted' if args.apply else 'Preview'}: {len(shop_ids)} shops")


if __name__ == "__main__":
    main()

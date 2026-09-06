"""probe_search_full_scan — one-off probe: 无 watermark(update_time_ge)时
/product/202309/products/search 全量返回的页数与耗时。

用途：手动触发 products 全量补图前，确认 TikTok search 无时间过滤时
返回全部 SPU(147) 需要几页、单页耗时，判断 run 卡住是上游慢还是别的。

Run:  PYTHONPATH=. .venv/bin/python scripts/probe_search_full_scan.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tts_erp_v2.sync_worker.proxy_call import build_proxy_call  # noqa: E402

ENDPOINT = "/product/202309/products/search"


def main() -> int:
    engine = create_engine(os.environ["TTS_ERP_DB_URL"])
    sess = Session(engine)
    try:
        from tts_erp_v2.db.models import ChannelAccount

        row = sess.execute(
            select(ChannelAccount.shop_id)
            .where(
                ChannelAccount.platform == "tiktok",
                ChannelAccount.shop_id.not_like("MOCK_%"),
            )
            .limit(1)
        ).first()
        shop_id = row[0] if row else None
        if not shop_id:
            print("no tiktok shop found", file=sys.stderr)
            return 2
        print(f"shop_id={shop_id}")

        proxy_call = build_proxy_call(sess, shop_id=shop_id)
        total = 0
        pages = 0
        next_token = None
        while True:
            t0 = time.time()
            body = {"page_size": 50}
            if next_token:
                body["next_page_token"] = next_token
            resp = proxy_call("POST", ENDPOINT, body=body)
            dt = time.time() - t0
            pages += 1
            data = resp.get("data") or {}
            products = data.get("products") or []
            total += len(products)
            print(f"page {pages}: {len(products)} products in {dt:.1f}s (cum {total})")
            next_token = data.get("next_page_token") or None
            if not next_token:
                break
        print(f"TOTAL {total} products over {pages} pages")
        return 0
    finally:
        sess.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

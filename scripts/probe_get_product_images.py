"""probe_get_product_images — one-off probe: confirm TikTok Get Product
endpoint returns image fields (and what shape).

Run from repo root:
    .venv/bin/python scripts/probe_get_product_images.py <shop_pk> <product_id>

Reads .env via dotenv (same pattern as scripts/oneoff_ad_raw_report.py)
so we get the real signed call against the production TikTok account.
Prints the image-related top-level + sku[0] fields + the actual URL strings,
then exits.

Does NOT write to DB (no raw_records, no products_spu update). Pure read.

Per AGENTS.md §2: scripts/probe_* one-off, no commit to business dirs.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tts_erp_v2.proxy.tts_shop.products_api import get_product

# Load .env AFTER imports so env reads (only at function call time inside
# tiktok_auth._resolve_app_credentials) see the populated os.environ.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    try:
        shop_pk = int(sys.argv[1])
        product_id = sys.argv[2]
    except (ValueError, IndexError) as e:
        print(f"bad args: {e}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2

    db_url = os.environ.get("TTS_ERP_DB_URL")
    if not db_url:
        print("TTS_ERP_DB_URL not set in .env", file=sys.stderr)
        return 2

    engine = create_engine(db_url)
    sess = Session(engine)

    try:
        data = get_product(session=sess, shop_pk=shop_pk, product_id=product_id)
    except Exception as e:
        print(f"ERROR calling get_product: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        sess.close()
        engine.dispose()

    print("== top-level keys ==")
    print(sorted(data.keys()))
    print()
    print("== image-related top-level fields ==")
    for k in sorted(data.keys()):
        if "image" in k.lower() or "main_" in k.lower() or "photo" in k.lower():
            v = data[k]
            if isinstance(v, list):
                print(
                    f"  {k}: list[{len(v)}] "
                    f"first={json.dumps(v[0], ensure_ascii=False) if v else None}"
                )
            else:
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
    print()
    skus = data.get("skus") or []
    print(f"== skus ({len(skus)}) ==")
    if skus:
        print("  sku[0] keys:", sorted(skus[0].keys()))
        print("  image-related sku[0] fields:")
        for k in sorted(skus[0].keys()):
            if "image" in k.lower() or "main_" in k.lower() or "photo" in k.lower():
                v = skus[0][k]
                if isinstance(v, list):
                    print(
                        f"    {k}: list[{len(v)}] "
                        f"first={json.dumps(v[0], ensure_ascii=False) if v else None}"
                    )
                else:
                    print(f"    {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

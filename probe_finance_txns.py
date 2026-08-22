#!/usr/bin/env python3
"""Probe: TikTok Finance transactions 端点能否替代 Excel 财务明细（fee_lines/financial_lines）。

只读 GET 探测：
  1. GET /finance/202309/statements/{statement_id}/transactions — 按账单逐交易
  2. GET /finance/202309/orders/{order_id}/transactions        — 按订单逐交易
  3. GET /finance/202309/transactions/unsettled                — 未结算池
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tts_signing import tiktok_request  # noqa: E402

API = "https://open-api.tiktokglobalshop.com"
SHOP_ID = "7494763368967603447"
SHOP_CIPHER = "ROW_xST8NgAAAACArTk0UMazcuYA7bWVL5En"

STATEMENT_ID = "7674871280482535186"
ORDER_ID = "585413071133639786"  # COMPLETED，且在退货表里出现过

with urllib.request.urlopen(f"http://127.0.0.1:9876/token/{SHOP_ID}?reveal=1", timeout=5) as r:
    tok = json.load(r)
ACCESS_TOKEN = tok["access_token"]

APP_KEY = APP_SECRET = ""
for ln in open("/home/schan/tts-erp/.env"):
    ln = ln.strip()
    if ln.startswith("TIKTOK_APP_KEY="):
        APP_KEY = ln.split("=", 1)[1]
    if ln.startswith("TIKTOK_APP_SECRET="):
        APP_SECRET = ln.split("=", 1)[1]


def call(path, extra=None):
    extra = dict(extra or {})
    extra["shop_cipher"] = SHOP_CIPHER
    return tiktok_request(
        method="GET", api_host=API, path=path,
        access_token=ACCESS_TOKEN, app_key=APP_KEY, app_secret=APP_SECRET,
        body=None, extra_params=extra, timeout=20,
    )


def show(name, r, full=False):
    print("=" * 80)
    print(name)
    print("=" * 80)
    print(f"code={r.get('code')}  msg={(r.get('message') or '')[:120]}")
    data = r.get("data")
    if isinstance(data, dict):
        print(f"data keys: {list(data.keys())}")
        for k, v in data.items():
            if isinstance(v, list) and v:
                print(f"  {k}[0] keys: {list(v[0].keys()) if isinstance(v[0], dict) else v[0]}")
                print(f"  {k} len={len(v)}")
        if full:
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:3500])


# 1) 按 statement 拉交易明细
r = call(f"/finance/202309/statements/{STATEMENT_ID}/transactions",
         extra={"page_size": "20"})
show("GET /finance/202309/statements/{id}/transactions", r, full=True)

# 2) 按 order 拉交易明细
r = call(f"/finance/202309/orders/{ORDER_ID}/transactions")
show("GET /finance/202309/orders/{id}/transactions", r, full=True)

# 3) 未结算池
r = call("/finance/202309/transactions/unsettled", extra={"page_size": "20"})
show("GET /finance/202309/transactions/unsettled", r, full=False)

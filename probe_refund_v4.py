#!/usr/bin/env python3
"""Phase 2: list actual returns/cancellations and inspect the response schema."""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tts_signing import tiktok_request  # noqa: E402

API = "https://open-api.tiktokglobalshop.com"
SHOP_ID = "7494763368967603447"
SHOP_CIPHER = "ROW_xST8NgAAAACArTk0UMazcuYA7bWVL5En"

# Load token
with urllib.request.urlopen(f"http://127.0.0.1:9876/token/{SHOP_ID}?reveal=1", timeout=5) as r:
    tok = json.load(r)
ACCESS_TOKEN = tok["access_token"]

# Load app creds
env_path = "/home/schan/tts-erp/.env"
APP_KEY = APP_SECRET = ""
for ln in open(env_path):
    ln = ln.strip()
    if ln.startswith("TIKTOK_APP_KEY="):
        APP_KEY = ln.split("=", 1)[1]
    if ln.startswith("TIKTOK_APP_SECRET="):
        APP_SECRET = ln.split("=", 1)[1]


def call(method, path, body=None, extra=None):
    extra = dict(extra or {})
    extra["shop_cipher"] = SHOP_CIPHER
    return tiktok_request(
        method=method, api_host=API, path=path,
        access_token=ACCESS_TOKEN, app_key=APP_KEY, app_secret=APP_SECRET,
        body=body if method != "GET" else None,
        extra_params=extra, timeout=20,
    )


# 1) /return_refund/202309/returns/search — try various body shapes
print("=" * 80)
print("/return_refund/202309/returns/search")
print("=" * 80)
for body in [{}, {"status": "AWAITING_SELLER_RESPONSE"}, {"return_status": "AWAITING_SELLER_RESPONSE"}]:
    extra = {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}
    r = call("POST", "/return_refund/202309/returns/search", body=body, extra=extra)
    print(f"\n--- body={body} ---")
    print(f"code={r.get('code')} msg={(r.get('message') or '')[:100]}")
    data = r.get("data") or {}
    print(f"data keys: {list(data.keys())[:20]}")
    print(f"data.total = {data.get('total')}")
    items = data.get("returns") or data.get("return_requests") or data.get("list") or []
    print(f"data.returns len = {len(items)}")
    if items:
        print(f"first item keys: {list(items[0].keys())[:30]}")
        print(f"first item sample (truncated):\n{json.dumps(items[0], indent=2, default=str)[:2000]}")
    time.sleep(0.5)

# 2) /return_refund/202309/cancellations/search
print("\n" + "=" * 80)
print("/return_refund/202309/cancellations/search")
print("=" * 80)
for body in [{}, {"status": "AWAITING_SELLER_RESPONSE"}, {"cancel_status": "AWAITING_SELLER_RESPONSE"}]:
    extra = {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}
    r = call("POST", "/return_refund/202309/cancellations/search", body=body, extra=extra)
    print(f"\n--- body={body} ---")
    print(f"code={r.get('code')} msg={(r.get('message') or '')[:100]}")
    data = r.get("data") or {}
    print(f"data keys: {list(data.keys())[:20]}")
    print(f"data.total = {data.get('total')}")
    items = data.get("cancellations") or data.get("cancel_requests") or data.get("list") or []
    print(f"data.cancellations len = {len(items)}")
    if items:
        print(f"first item keys: {list(items[0].keys())[:30]}")
        print(f"first item sample (truncated):\n{json.dumps(items[0], indent=2, default=str)[:2000]}")
    time.sleep(0.5)

# 3) /reverse/202309/orders — try various query shapes
print("\n" + "=" * 80)
print("/reverse/202309/orders")
print("=" * 80)
for extra in [
    {"page_size": "5"},
    {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"},
    {"page_size": "5", "sort_field": "update_time", "sort_order": "DESC"},
]:
    r = call("GET", "/reverse/202309/orders", extra=extra)
    print(f"\n--- extra={extra} ---")
    print(f"code={r.get('code')} msg={(r.get('message') or '')[:100]}")
    if r.get("code") == 0:
        data = r.get("data") or {}
        print(f"data keys: {list(data.keys())[:20]}")
        items = data.get("orders") or data.get("reverse_orders") or data.get("list") or []
        print(f"data.orders len = {len(items)}")
        if items:
            print(f"first item keys: {list(items[0].keys())[:30]}")
            print(f"first item sample:\n{json.dumps(items[0], indent=2, default=str)[:2000]}")
    time.sleep(0.5)

# 4) /reverse/202309/orders/search
print("\n" + "=" * 80)
print("/reverse/202309/orders/search")
print("=" * 80)
for body in [{}, {"status": "AWAITING_SHIPMENT"}]:
    extra = {"page_size": "5"}
    r = call("GET", "/reverse/202309/orders/search", body=body, extra=extra)
    print(f"\n--- body={body} ---")
    print(f"code={r.get('code')} msg={(r.get('message') or '')[:100]}")
    if r.get("code") == 0:
        data = r.get("data") or {}
        print(f"data keys: {list(data.keys())[:20]}")
        items = data.get("orders") or data.get("reverse_orders") or data.get("list") or []
        print(f"data.orders len = {len(items)}")
        if items:
            print(f"first item keys: {list(items[0].keys())[:30]}")
            print(f"first item sample:\n{json.dumps(items[0], indent=2, default=str)[:2000]}")
    time.sleep(0.5)

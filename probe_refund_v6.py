#!/usr/bin/env python3
"""Phase 4: try /reverse/ path variants to be thorough."""
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

with urllib.request.urlopen(f"http://127.0.0.1:9876/token/{SHOP_ID}?reveal=1", timeout=5) as r:
    tok = json.load(r)
ACCESS_TOKEN = tok["access_token"]

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
        extra_params=extra, timeout=15,
    )


# Try various /reverse/ path variants
print("Trying /reverse/202309 path variants:\n")
for path in [
    "/reverse/202309/orders",
    "/reverse/202309/orders/search",
    "/reverse/202309/orders/list",
    "/reverse/202309/return_orders",
    "/reverse/202309/return_orders/search",
    "/reverse/202309/shipment",
    "/reverse/202309/shipments",
    "/reverse/202309/shipments/search",
    "/reverse/202309/return/orders",
    "/reverse/202309/return/orders/search",
    "/reverse/202309/return_order",
    "/reverse/202309/return_order/search",
    "/reverse/202309",
    "/reverse",
]:
    r = call("GET", path, extra={"page_size": "5"})
    code = r.get("code", "?")
    msg = (r.get("message") or "")[:80]
    print(f"  GET  {path:55s} code={code:>5}  {msg}")

# Also try POST variants
print("\nPOST variants:")
for path in [
    "/reverse/202309/orders",
    "/reverse/202309/orders/search",
    "/reverse/202309/return_orders",
    "/reverse/202309/shipments",
    "/reverse/202309/return_order",
]:
    r = call("POST", path, body={}, extra={"page_size": "5"})
    code = r.get("code", "?")
    msg = (r.get("message") or "")[:80]
    print(f"  POST {path:55s} code={code:>5}  {msg}")

# Try fulfillment module too
print("\n/fulfillment/202309/ variants (just to be complete):")
for path in [
    "/fulfillment/202309/orders",
    "/fulfillment/202309/orders/search",
    "/fulfillment/202309/shipment",
    "/fulfillment/202309/packages",
]:
    r = call("GET", path, extra={"page_size": "5"})
    code = r.get("code", "?")
    msg = (r.get("message") or "")[:80]
    print(f"  GET  {path:55s} code={code:>5}  {msg}")

#!/usr/bin/env python3
"""Phase 3: deep-dive on /reverse/ and /return_refund/202309/returns variants.

- Test all plausible methods (GET/POST) for /reverse/202309/orders and /search
- Test /return_refund/202309/returns (POST without /search) with proper body
- See if there's a /return_refund/202309/returns/<id> detail endpoint
"""
from __future__ import annotations
import json
import os
import sys
import time
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
        extra_params=extra, timeout=20,
    )


def show(label, r, max_body=800):
    code = r.get("code", "?")
    msg = (r.get("message") or "")[:120]
    request_id = r.get("request_id", "")[:14]
    print(f"  code={code}  msg={msg!r}  req_id={request_id}")
    if code == 0:
        data = r.get("data") or {}
        print(f"  data keys: {list(data.keys())[:15]}")
    elif "body" in str(r):
        print(f"  full body: {json.dumps(r, default=str)[:max_body]}")


# 1) /return_refund/202309/returns — try GET (read detail) and POST (read list vs create)
print("=" * 80)
print("A. /return_refund/202309/returns  (no /search)")
print("=" * 80)
print("A.1 GET (read list?)")
show("A.1", call("GET", "/return_refund/202309/returns", extra={"page_size": "5"}))
time.sleep(0.3)
print("\nA.2 POST (read list?)")
show("A.2", call("POST", "/return_refund/202309/returns", body={}, extra={"page_size": "5"}))
time.sleep(0.3)
print("\nA.3 POST with proper body shape")
for body in [
    {"order_id": "585570672755049704", "order_line_item_ids": ["585570672755115240"]},
    {"order_id": "585570672755049704"},
]:
    show(f"A.3 body={body}", call("POST", "/return_refund/202309/returns", body=body, extra={"page_size": "5"}))
    time.sleep(0.3)

# 2) /return_refund/202309/returns/<id> — try various id shapes
print("\n" + "=" * 80)
print("B. /return_refund/202309/returns/<id>  (detail by id)")
print("=" * 80)
for rid in ["585570672755049704", "4041967324378727656"]:  # order_id and cancel_id
    print(f"\nB.{rid} GET")
    show(f"B.{rid}", call("GET", f"/return_refund/202309/returns/{rid}"))
    time.sleep(0.3)

# 3) /return_refund/202309/returns/list — alternative list?
print("\n" + "=" * 80)
print("C. /return_refund/202309/returns/list  (alt list)")
print("=" * 80)
print("C.1 GET")
show("C.1", call("GET", "/return_refund/202309/returns/list"))
time.sleep(0.3)
print("\nC.2 POST")
show("C.2", call("POST", "/return_refund/202309/returns/list", body={}, extra={"page_size": "5"}))
time.sleep(0.3)

# 4) /return_refund/202309/cancellations — list variant
print("\n" + "=" * 80)
print("D. /return_refund/202309/cancellations  (no /search)")
print("=" * 80)
print("D.1 GET")
show("D.1", call("GET", "/return_refund/202309/cancellations", extra={"page_size": "5"}))
time.sleep(0.3)
print("\nD.2 POST")
show("D.2", call("POST", "/return_refund/202309/cancellations", body={}, extra={"page_size": "5"}))
time.sleep(0.3)

# 5) /return_refund/202309/cancellations/<id> — detail
print("\n" + "=" * 80)
print("E. /return_refund/202309/cancellations/<id>  (detail)")
print("=" * 80)
for cid in ["4041967324378727656"]:  # a real cancel_id
    print(f"\nE.{cid} GET")
    show(f"E.{cid}", call("GET", f"/return_refund/202309/cancellations/{cid}"))
    time.sleep(0.3)

# 6) /reverse/ — try POST
print("\n" + "=" * 80)
print("F. /reverse/202309/orders  (POST list)")
print("=" * 80)
for body in [{}, {"status": "AWAITING_SHIPMENT"}]:
    show(f"F body={body}", call("POST", "/reverse/202309/orders", body=body, extra={"page_size": "5"}))
    time.sleep(0.3)

print("\n" + "=" * 80)
print("G. /reverse/202309/orders/search  (POST search)")
print("=" * 80)
for body in [{}, {"status": "AWAITING_SHIPMENT"}]:
    show(f"G body={body}", call("POST", "/reverse/202309/orders/search", body=body, extra={"page_size": "5"}))
    time.sleep(0.3)

# 7) /reverse/ root & module check
print("\n" + "=" * 80)
print("H. /reverse/ module root")
print("=" * 80)
for path in ["/reverse", "/reverse/202309", "/reverse/202309/orders/0", "/reverse/202309/orders/0/tracking"]:
    show(f"H {path}", call("GET", path))
    time.sleep(0.3)

# 8) Try /return_refund/202309/returns/search with status filter that produces data
print("\n" + "=" * 80)
print("I. /return_refund/202309/returns/search — try time range")
print("=" * 80)
# cancel was 1786854718 (Aug 16 2026). Try create_time_ge earlier.
# But returns list is empty. Try with the cancellation as a "return"?
for body in [{"create_time_ge": 1700000000, "create_time_lt": 1900000000}]:
    show(f"I body={body}", call("POST", "/return_refund/202309/returns/search", body=body, extra={"page_size": "20"}))
    time.sleep(0.3)

#!/usr/bin/env python3
"""Probe all plausible refund/return/cancel/reverse endpoints on TikTok 202309.

Read-only + safe initiate (no reject/approve/accept-cancel/approve-cancel/approve-return).
This is a discovery script: hit each path with the right method, log the result code.
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Use the same signing code as tts-erp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tts_signing import tiktok_request, sign_request, build_signed_url, resolve_path  # noqa: E402

API = "https://open-api.tiktokglobalshop.com"
SHOP_ID = "7494763368967603447"
SHOP_CIPHER = "ROW_xST8NgAAAACArTk0UMazcuYA7bWVL5En"  # hard-coded for probing only

# 1) load real token from oauth-receiver
import urllib.request as ur
try:
    with ur.urlopen(f"http://127.0.0.1:9876/token/{SHOP_ID}?reveal=1", timeout=5) as r:
        tok = json.load(r)
    ACCESS_TOKEN = tok.get("access_token", "")
    if not ACCESS_TOKEN:
        raise SystemExit(f"no access_token in response: {tok}")
except Exception as e:
    raise SystemExit(f"failed to fetch token: {e}")

APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")

if not APP_KEY or not APP_SECRET:
    # source from .env if not exported
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        for ln in open(env_path):
            ln = ln.strip()
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
        APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")

if not APP_KEY or not APP_SECRET:
    raise SystemExit("TIKTOK_APP_KEY / TIKTOK_APP_SECRET not set")

results = []


def probe(method: str, path: str, body: dict | None = None,
          extra: dict | None = None, note: str = ""):
    extra = dict(extra or {})
    extra["shop_cipher"] = SHOP_CIPHER
    try:
        r = tiktok_request(
            method=method,
            api_host=API,
            path=path,
            access_token=ACCESS_TOKEN,
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            body=body if method != "GET" else None,
            extra_params=extra,
            timeout=15,
        )
        code = r.get("code", "?")
        msg = (r.get("message") or "")[:80]
        rid = r.get("request_id", "")[:12]
        exists = "EXISTS" if (code == 0 or code not in (36009009, 36009010, 36009004)) else "MISSING"
        if code in (36009009, 36009010, 36009004):
            exists = "MISSING"
        results.append((exists, method, path, code, msg, note, rid))
        return r
    except Exception as e:
        results.append(("ERROR", method, path, -1, str(e)[:80], note, ""))
        return None


# Endpoints to probe
# Read endpoints (safe)
read_eps = [
    # return_refund/202309/returns — POST for list, GET for single
    ("POST", "/return_refund/202309/returns", {}, {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}, "list returns"),
    ("POST", "/return_refund/202309/returns/search", {}, {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}, "search returns"),
    ("POST", "/return_refund/202309/returns/list", {}, {"page_size": "5"}, "list returns (alt)"),

    # cancellations (list)
    ("POST", "/return_refund/202309/cancellations", {}, {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}, "list cancellations"),
    ("POST", "/return_refund/202309/cancellations/search", {}, {"page_size": "5"}, "search cancellations"),

    # reverse logistics — list
    ("GET", "/reverse/202309/orders", None, {"page_size": "5", "sort_field": "create_time", "sort_order": "DESC"}, "list reverse orders"),
    ("GET", "/reverse/202309/orders/search", None, {"page_size": "5"}, "search reverse"),

    # root level
    ("GET", "/return_refund/202309", None, {}, "root"),
]

# write (initiate) endpoints — safe to test, NOT to actually fire (use placeholder ids)
write_eps = []  # user explicitly said NO write endpoint testing (no reject/approve/accept/initiate)

print("=" * 80)
print(f"Probing TikTok 202309 return/refund/cancel/reverse READ endpoints only")
print(f"shop_id = {SHOP_ID}")
print("=" * 80)

print("\n--- READ endpoints ---")
for method, path, body, extra, note in read_eps:
    probe(method, path, body, extra, note)
    time.sleep(0.4)

print("\n--- Detail endpoint (using a dummy return id, will fail but path check) ---")
# these need an id — test if path exists with non-existent id (should give 36009004 or auth issue, not 36009009)
for path, note in [
    ("/return_refund/202309/returns/0", "get return detail by id"),
    ("/return_refund/202309/returns/0/evidence", "get return evidence"),
    ("/return_refund/202309/cancellations/0", "get cancellation detail"),
    ("/reverse/202309/orders/0", "get reverse order detail"),
    ("/reverse/202309/orders/0/tracking", "get reverse tracking"),
    ("/reverse/202309/orders/0/history", "get reverse history"),
    ("/reverse/202309/orders/0/items", "get reverse items"),
    ("/reverse/202309/orders/0/records", "get reverse records"),
    ("/reverse/202309/orders/0/dispute", "get reverse dispute"),
    ("/reverse/202309/orders/0/buyer", "get reverse buyer"),
    ("/reverse/202309/orders/0/recipient", "get reverse recipient"),
    ("/reverse/202309/orders/0/shipping", "get reverse shipping"),
    ("/reverse/202309/orders/0/return_items", "get reverse return items"),
]:
    method = "GET"
    probe(method, path, None, {}, note)
    time.sleep(0.4)

# write detail endpoints (with dummy id)
print("\n--- Detail WRITE endpoints SKIPPED per user instruction (no reject/approve/accept testing) ---")

# Print summary
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"{'STATUS':<8} {'METHOD':<7} {'PATH':<55} {'CODE':<8} {'NOTE'}")
print("-" * 100)
for status, method, path, code, msg, note, rid in results:
    print(f"{status:<8} {method:<7} {path:<55} {code!s:<8} {note}")

# Filter to existing only
existing = [r for r in results if r[0] == "EXISTS"]
print(f"\n>>> {len(existing)} endpoints EXIST out of {len(results)} probed <<<")
print("\nEXISTING ENDPOINTS:")
for status, method, path, code, msg, note, rid in existing:
    print(f"  {method:5s} {path}   [code={code}]  {note}")

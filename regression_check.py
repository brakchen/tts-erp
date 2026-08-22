#!/usr/bin/env python3
"""Quick regression check: orders, statements, payments, returns, cancellations all present."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:9877"
SHOP = "7494763368967603447"


def _api_key():
    p = Path("/home/schan/tts-erp/.env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("TTS_ERP_SERVICE_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_HEADERS = {"Authorization": f"Bearer {_api_key()}"} if _api_key() else {}


def get(path):
    req = urllib.request.Request(f"{BASE}{path}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


print("=== Local DB row counts (regression) ===")
for name, path in [
    ("orders",       f"/db/orders?shop_id={SHOP}&limit=1"),
    ("statements",   f"/db/statements?shop_id={SHOP}&limit=1"),
    ("payments",     f"/db/payments?shop_id={SHOP}&limit=1"),
    ("returns",      f"/db/returns?shop_id={SHOP}&limit=1"),
    ("cancellations", f"/db/cancellations?shop_id={SHOP}&limit=1"),
]:
    d = get(path)
    print(f"  {name:15s}  count={d['count']}")

print("\n=== /db/sync_log full (chronological order) ===")
sl = get("/db/sync_log")
for x in sl["items"]:
    print(f"  {x['sync_type']:20s}  {x['status']:6s}  rows={x['rows']:>4}  ts={x['ts']:.0f}")

print("\n=== Service healthz ===")
print(json.dumps(get("/healthz"), indent=2))

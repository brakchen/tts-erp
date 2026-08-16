#!/usr/bin/env python3
"""Quick regression check: orders, statements, payments, returns, cancellations all present."""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9877"
SHOP = "7494763368967603447"


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
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

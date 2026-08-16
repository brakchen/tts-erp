#!/usr/bin/env python3
"""Direct /sync/returns debug — capture 502 body."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9877"
SHOP = "7494763368967603447"

req = urllib.request.Request(
    f"{BASE}/sync/returns",
    data=json.dumps({"shop_id": SHOP, "page_size": 10}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"status={r.status}")
        print(r.read().decode("utf-8", errors="replace")[:500])
except urllib.error.HTTPError as e:
    print(f"HTTPError status={e.code}")
    print(f"body={e.read().decode('utf-8', errors='replace')[:1000]}")

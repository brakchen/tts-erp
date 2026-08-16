#!/usr/bin/env python3
"""Final e2e smoke test: sync all 5 modules + verify DB + 501 protection."""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9877"
SHOP = "7494763368967603447"


def post(path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return {"_http_error": e.code, "_body": body_text}


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
        return json.load(r)


print("=== /sync all 5 modules ===")
for mod in ("orders", "statements", "payments", "returns", "cancellations"):
    r = post(f"/sync/{mod}", {"shop_id": SHOP, "page_size": 100})
    if r.get("_http_error"):
        print(f"  /sync/{mod:14s}  HTTP {r['_http_error']}: {r['_body'][:200]}")
    else:
        print(f"  /sync/{mod:14s}  saved={r.get('saved', '?'):>4}  total={r.get('total', '?'):>4}  pages={r.get('pages', '?')}")

print("\n=== /db counts ===")
for mod in ("orders", "statements", "payments", "returns", "cancellations"):
    r = get(f"/db/{mod}?shop_id={SHOP}&limit=1")
    print(f"  /db/{mod:14s}  count={r['count']}")

print("\n=== /endpoints check ===")
ep = get("/endpoints")
print(f"  return_refund_api_proxy routes: {len(ep['return_refund_api_proxy'])}")
print(f"  sync routes: {len(ep['sync'])}")
print(f"  local_db routes: {len(ep['local_db'])}")

print("\n=== 501 protection (write endpoints blocked) ===")
for path, body in [("/returns", {"shop_id": SHOP}), ("/cancellations", {"shop_id": SHOP})]:
    try:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # We expect 501, urllib will NOT raise on 4xx/5xx by default
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"  POST {path}  status={r.status}  body={r.read()[:200]}")
    except urllib.error.HTTPError as e:
        print(f"  POST {path}  status={e.code}  body={e.read()[:200].decode()}")

print("\n=== /healthz ===")
print(json.dumps(get("/healthz"), indent=2))

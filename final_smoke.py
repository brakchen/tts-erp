#!/usr/bin/env python3
"""Final e2e smoke test: sync all 6 modules + verify DB + finance reads + healthz.

CREATE write endpoints (POST /returns, /cancellations) are intentionally NOT
exposed by the service (removed 2026-08-17); smoke no longer probes them.

Auth: reads TTS_ERP_SERVICE_KEY from .env and sends it as Bearer; when
TTS_ERP_AUTH_MODE=enforce it also asserts that keyless requests get 401.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:9877"
SHOP = "7494763368967603447"


def _load_env() -> dict:
    env = {}
    p = Path("/home/schan/tts-erp/.env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()
API_KEY = _ENV.get("TTS_ERP_SERVICE_KEY")
AUTH_MODE = _ENV.get("TTS_ERP_AUTH_MODE", "off")


def _headers(extra=None):
    h = dict(extra or {})
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def post(path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body or {}).encode("utf-8"),
        headers=_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return {"_http_error": e.code, "_body": body_text}


def get(path):
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", headers=_headers()), timeout=10) as r:
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

print("\n=== /db/statement_transactions (接口财务明细) ===")
r = get("/db/statement_transactions?limit=1")
print(f"  /db/statement_transactions?limit=1  count={r['count']}")
if r["count"]:
    item = r["items"][0]
    print(f"  sample txn: order={item.get('order_id')} settlement={item.get('settlement_amount')} {item.get('currency')}")

print("\n=== /healthz ===")
print(json.dumps(get("/healthz"), indent=2))

if AUTH_MODE == "enforce":
    print("\n=== auth enforce check (no key → expect 401) ===")
    # probe WITHOUT the key regardless of API_KEY being set:
    req = urllib.request.Request(
        f"{BASE}/db/orders?limit=1",
        headers={"Content-Type": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  GET /db/orders (no key)  status={resp.status}  ← UNEXPECTED, should be 401")
    except urllib.error.HTTPError as e:
        print(f"  GET /db/orders (no key)  status={e.code}  {'OK' if e.code == 401 else '← UNEXPECTED'}")

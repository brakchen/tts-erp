"""End-to-end smoke test for tts-erp finance endpoints."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9877"
SHOP_ID = "7494763368967603447"


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(f"{BASE}{path}", method=method, data=data,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "body": e.read().decode()[:300]}


print("=" * 70)
print("TTS-ERP FINANCE E2E SMOKE")
print("=" * 70)

# 1) Health
r = req("GET", "/healthz")
print(f"\n[1] /healthz: status={r.get('status')}")

# 2) Proxy: list statements (raw)
r = req("GET", f"/finance/statements?shop_id={SHOP_ID}&page_size=2")
print(f"\n[2] /finance/statements: code={r.get('code')} msg={r.get('message','')[:80]}")
if r.get("data", {}).get("statements"):
    s = r["data"]["statements"][0]
    print(f"    first: id={s['id']} rev={s['revenue_amount']} fee={s['fee_amount']} settle={s['settlement_amount']} {s['currency']}")

# 3) Proxy: list payments (raw)
r = req("GET", f"/finance/payments?shop_id={SHOP_ID}&page_size=2")
print(f"\n[3] /finance/payments: code={r.get('code')} msg={r.get('message','')[:80]}")
if r.get("data", {}).get("payments"):
    p = r["data"]["payments"][0]
    amt = p.get("amount", {})
    print(f"    first: id={p['id']} amount={amt.get('value')} {amt.get('currency')} status={p['status']}")

# 4) DB read: statements
r = req("GET", f"/db/statements?shop_id={SHOP_ID}&limit=3")
print(f"\n[4] /db/statements: count={r.get('count')}")
for s in (r.get("items") or [])[:2]:
    print(f"    {s['statement_id']} rev={s['revenue_amount']} settle={s['settlement_amount']}")

# 5) DB read: payments
r = req("GET", f"/db/payments?shop_id={SHOP_ID}&limit=3")
print(f"\n[5] /db/payments: count={r.get('count')}")
for p in (r.get("items") or [])[:2]:
    print(f"    {p['payment_id']} amount={p['amount_value']} {p['currency']} status={p['status']}")

# 6) Sync log
r = req("GET", "/db/sync_log")
print(f"\n[6] /db/sync_log: items={len(r.get('items', []))}")
for s in r.get("items", []):
    print(f"    {s['sync_type']:12s} status={s['status']:6s} rows={s['rows']}")

# 7) /endpoints inventory
r = req("GET", "/endpoints")
print(f"\n[7] /endpoints: {len(r.get('finance_api_proxy', []))} finance + {len(r.get('local_db', []))} DB + {len(r.get('sync', []))} sync endpoints")
print(f"    finance_api_proxy: {r.get('finance_api_proxy')}")

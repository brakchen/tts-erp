"""End-to-end test for tts-erp service."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:9877"
SHOP_ID = "7494763368967603447"  # Bridge nook VN


def req(method, path, body=None):
    print(f"--- {method} {path} ---")
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            text = r.read().decode("utf-8")
            try:
                obj = json.loads(text)
                print(json.dumps(obj, default=str)[:1500])
                return obj
            except json.JSONDecodeError:
                print(text[:600])
                return None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code}: {body_text[:600]}")
        return None
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return None


print("=" * 70)
print("TTS-ERP E2E TEST")
print("=" * 70)

# 1) Health
print("\n[1] /healthz")
req("GET", "/healthz")

# 2) OAuth passthrough — list shops
print("\n[2] /shops  (proxies to oauth-receiver /tokens/shops)")
req("GET", "/shops")

# 3) Get token (reveal=1) for our shop
print(f"\n[3] /token/{SHOP_ID}?reveal=1  (proxies to oauth-receiver)")
tok = req("GET", f"/token/{SHOP_ID}?reveal=1")
if tok:
    at = tok.get("access_token", "")
    print(f"  access_token: {at[:18]}...{at[-8:]} (len={len(at)})")
    print(f"  shop_cipher:  {tok.get('shop_cipher')}")
    print(f"  shop_region:  {tok.get('shop_region')}")

# 4) Sync orders (this is the main thing)
print(f"\n[4] POST /sync/orders  {{shop_id, page_size=10}}")
req("POST", "/sync/orders", {"shop_id": SHOP_ID, "page_size": 10})

# 5) List orders from local DB
print("\n[5] GET /db/orders?shop_id=...")
req("GET", f"/db/orders?shop_id={SHOP_ID}")

# 6) Get sync log
print("\n[6] GET /db/sync_log")
req("GET", "/db/sync_log")

# 7) Endpoints inventory
print("\n[7] GET /endpoints  (full list)")
req("GET", "/endpoints")

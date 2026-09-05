# `GET /v2/tiktok-shop/products/{product_id}` — Spec

**Single source of truth** for the TikTok Shop Get Product read-through.
If anything in the codebase (`tts_erp_v2/api/v2/tiktok_shop.py`,
`tts_erp_v2/proxy/tts_shop/products_api.py`) disagrees with this file,
this file wins — open an issue / fix the code, don't paper over the drift.

Upstream contract reference: `tts-partner-api-docs/Get Product.md`
(`GET /product/202309/products/{product_id}`).

---

## 1. URL

```
GET /v2/tiktok-shop/products/{product_id}
    ?shop_pk={ca_id}
    &return_under_review_version={bool}        # optional, default false
    &return_draft_version={bool}              # optional, default false
    &locale={bcp47}                           # optional, default null
```

| Component | In | Required | Type | Notes |
| --- | --- | --- | --- | --- |
| `product_id` | path | yes | string | TikTok product ID. Upstream returns string IDs (`"1729592969712207008"`). |
| `shop_pk` | query | yes | int ≥ 1 | Internal `commerce.shops.id`. Resolves upstream `shop_id` + `access_token` + `shop_cipher`. Must be `platform='tiktok'`. |
| `return_under_review_version` | query | no | bool | Upstream flag (see upstream docs). Mutually exclusive with `return_draft_version`. |
| `return_draft_version` | query | no | bool | Upstream flag. Mutually exclusive with `return_under_review_version`. |
| `locale` | query | no | BCP-47 string, ≤ 16 chars | Display locale. `None` → upstream uses shop default. |

---

## 2. Auth

| Layer | Requirement |
| --- | --- |
| Header | `Authorization: Bearer <key>` **or** `X-API-Key: <key>` |
| Cookie | browser session cookie (`tts_session`) — see `tech-doc/browser-login-design.md` |
| Role | **`readonly`** (the whole `/v2/tiktok-shop/*` prefix is classified readonly in `tts_erp_v2/middleware/auth.py::_READONLY_PREFIXES`) |
| Upstream scope (TikTok app side) | **`seller.product.basic`** must be enabled on the Partner App. Missing → upstream returns `code=105005 "Access denied"` → our 502 with `upstream_code=105005`. |

---

## 3. Required server-side env (operator)

| Var | Source | Purpose |
| --- | --- | --- |
| `TIKTOK_APP_KEY` | `.env` | TikTok Partner App app_key, placed in the `app_key` query param + HMAC canonical |
| `TIKTOK_APP_SECRET` | `.env` | TikTok Partner App app_secret, used for HMAC-SHA256 signing per AGENTS.md §2.2 |
| `TTS_ERP_FERNET_KEY` | `.env` | Decrypts `integration.credentials.ciphertext` (Fernet envelope) |

Any of these missing → 500 with `detail: "signing/config error: ..."`.

---

## 4. 200 response shape

The upstream `data` payload returned **verbatim**. The upstream envelope
(`{code, message, request_id}`) is stripped after the success check.

We do **not** validate / model the response with Pydantic — the upstream
schema has hundreds of fields and changes without notice; the client
controls its own consumption of the shape.

Abridged example (real payload is far larger — see `tts-partner-api-docs/Get Product.md`):

```json
{
  "id": "1729592969712207008",
  "title": "Premium Yoga Leggings - Black",
  "status": "ACTIVATE",
  "audit": { "status": "APPROVED", "pre_approved_reasons": [] },
  "brand": { "id": "7082427311584347905", "name": "Bridge nook" },
  "category_chains": [
    { "id": "853000", "local_name": "Botol & Stoples Penyimpanan", "is_leaf": true, "parent_id": "851848" }
  ],
  "main_images": [ { "uri": "...", "urls": ["..."], "width": 600, "height": 600 } ],
  "skus": [ { "id": "10001", "seller_sku": "sku_name", "price": { "currency": "USD", "sale_price": "117.5" } } ],
  "create_time": 1234567890,
  "...": "...(see upstream docs for the full schema)..."
}
```

---

## 5. Status code matrix (complete)

| Status | When | `detail` body |
| --- | --- | --- |
| **200** | upstream `code == 0` | the upstream `data` dict (verbatim) |
| **401** | missing / invalid / disabled API key | (auth middleware JSON `{"detail": "..."}`) |
| **403** | key role < readonly | (auth middleware JSON `{"detail": "requires readonly"}`) |
| **404** | `shop_pk` not found, OR not `platform='tiktok'`, OR `integration.credentials` row missing, OR `shop_cipher` empty | `{"detail": "<message>"}` |
| **422** | missing `shop_pk` query param, OR `shop_pk < 1`, OR mutually exclusive flags set together | standard FastAPI validation array OR `{"detail": "return_under_review_version and return_draft_version are mutually exclusive ..."}` |
| **429** | upstream rate-limit (internal retry budget exhausted: 3 attempts) | `{"detail": "upstream rate limit: ..."}` |
| **502** | upstream `code != 0` (business error) | `{"detail": {"message": "upstream returned a non-zero business code", "upstream_code": <int>, "upstream_message": <str>, "upstream_request_id": <str \| null>}}` |
| **502** | upstream auth rejected (401/403 from TikTok) | `{"detail": "upstream auth rejected: ..."}` |
| **502** | upstream 4xx/5xx (non-retryable) | `{"detail": "upstream http error: ..."}` |
| **502** | network blip, retry budget exhausted | `{"detail": "transient upstream error: ..."}` |
| **500** | `TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET` / `TTS_ERP_FERNET_KEY` not configured | `{"detail": "signing/config error: ..."}` |

---

## 6. Examples

### 6.1 Happy path

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/1729592969712207008?shop_pk=314"
```

→ 200, body = upstream `data` dict.

### 6.2 Under-review version with explicit locale

```bash
curl -sS -H "Authorization: Bearer $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/1729592969712207008?shop_pk=314&return_under_review_version=true&locale=en-US"
```

### 6.3 Mutually exclusive flags

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/1729592969712207008?shop_pk=314&return_under_review_version=true&return_draft_version=true"
```

→ 422, `{"detail": "return_under_review_version and return_draft_version are mutually exclusive ..."}`.

### 6.4 Upstream product not found

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/DOES_NOT_EXIST?shop_pk=314"
```

→ 502:
```json
{
  "detail": {
    "message": "upstream returned a non-zero business code",
    "upstream_code": 12000002,
    "upstream_message": "Product does not exist",
    "upstream_request_id": "202203070749000101890810281E8C70B7"
  }
}
```

### 6.5 Missing credentials

```bash
# commerce.shops has id=314, but integration.credentials
# has no row for external_account_id='7494763368967603447'
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/1729592969712207008?shop_pk=314"
```

→ 404, `{"detail": "integration.credentials missing for tiktok shop_id='7494763368967603447'"}`.

---

## 7. Properties

| Property | Value |
| --- | --- |
| HTTP method | `GET` (idempotent) |
| Side effects | **none** — pure read-through |
| Caching | **none** server-side. Every call hits upstream. Cache client-side if needed (the `data` shape is stable for the product's `status`; `audit` may change). |
| Pagination | n/a (single record) |
| Rate limiting | Internal: exponential backoff with jitter, 3 attempts total. Caller sees 429 only after budget exhausted. |

---

## 8. Discovery

| Need | Where |
| --- | --- |
| Live route list | `GET /endpoints` (operator index, public) |
| OpenAPI schema (machine-readable) | `GET /openapi.json` (public) |
| Swagger UI | `GET /docs` (public) |
| ReDoc UI | `GET /redoc` (public) |

---

## 9. Implementation pointers

| Concern | File |
| --- | --- |
| Router | `tts_erp_v2/api/v2/tiktok_shop.py::router` |
| Proxy wrapper (credential resolve + envelope check) | `tts_erp_v2/proxy/tts_shop/products_api.py::get_product` |
| HTTP transport (HMAC signing, retry, classification) | `tts_erp_v2/proxy/tts_shop/client.py::TiktokShopClient` |
| Credential resolve | `tts_erp_v2/proxy/token_service.py::load_credentials` |
| App-key / app-secret resolve | `tts_erp_v2/proxy/tiktok_auth.py::_resolve_app_credentials` |
| Role classification (readonly) | `tts_erp_v2/middleware/auth.py::_READONLY_PREFIXES` (`/v2/tiktok-shop/`) |
| Tests (proxy layer) | `tests/proxy/test_tts_shop_products_api.py` (14 cases) |
| Tests (API layer) | `tests/api/test_tiktok_shop_get_product.py` (14 + OpenAPI regression cases) |

---

## 10. Deferred endpoints

The 7 other Partner API product-domain GETs in `tts-partner-api-docs/` are
**not** yet wired:

- `GET /product/202312/prerequisites` — Check Listing Prerequisites
- `GET /product/202309/categories` — Get Categories
- `GET /product/202309/categories/{id}/attributes` — Get Attributes
- `GET /product/202309/brands` — Get Brands
- `GET /product/202309/categories/{id}/rules` — Get Category Rules
- `GET /product/202506/images/translation_tasks` — Get Image Translation Tasks (scope: `seller.product.optimize` — note typo in upstream docs)
- `GET /product/202604/opportunities/submissions` — Get Submission Records

When added they will live under `/v2/tiktok-shop/<resource>` and reuse
the proxy pattern + `_map_proxy_error` defined for Get Product.
# `GET /v2/commerce/channel-accounts/by-external/{shop_id}` — Spec

**Single source of truth** for the channel-account reverse-lookup
endpoint. If anything in `tts_erp_v2/api/v2/commerce.py` disagrees
with this file, this file wins.

---

## 1. URL

```
GET /v2/commerce/channel-accounts/by-external/{shop_id}
    ?platform={platform_string}     # optional, default "tiktok"
```

| Component | In | Required | Type | Notes |
| --- | --- | --- | --- | --- |
| `external_account_id` | path | yes | string | Upstream shop_id (e.g. TikTok shop_id). Example: `7494763368967603447`. |
| `platform` | query | no | string, ≤ 32 chars | Default `"tiktok"`. **Required for uniqueness** — `external_account_id` is only unique within a platform, not globally. |

---

## 2. Auth

| Layer | Requirement |
| --- | --- |
| Header | `Authorization: Bearer <key>` **or** `X-API-Key: <key>` |
| Role | **`readonly`** (whole `/v2/commerce/*` prefix is classified readonly in `tts_erp_v2/middleware/auth.py::_READONLY_PREFIXES`) |

---

## 3. 200 response shape

A single `ChannelAccountOut` (see `tts_erp_v2/api/schemas.py`):

```json
{
  "id": 314,
  "platform": "tiktok",
  "external_account_id": "7494763368967603447",
  "account_name": "Bridge nook",
  "region": "VN",
  "seller_type": "CROSS_BORDER",
  "status": "active",
  "synced_at": "2026-09-04T13:22:11Z",
  "created_at": "2026-08-01T08:00:00Z",
  "updated_at": "2026-09-04T13:22:11Z"
}
```

---

## 4. Status code matrix

| Status | When | `detail` body |
| --- | --- | --- |
| **200** | row matches `(platform, external_account_id)` | the `ChannelAccountOut` object |
| **401** | missing / invalid / disabled API key | (auth middleware JSON `{"detail": "..."}`) |
| **403** | key role < readonly | (auth middleware JSON `{"detail": "requires readonly"}`) |
| **404** | no row matches | `{"detail": "channel account not found for platform='tiktok' external_account_id='XYZ'"}` |

---

## 5. Examples

### 5.1 Happy path (default platform)

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts/by-external/7494763368967603447"
```

→ 200, body = `ChannelAccountOut`.

### 5.2 Explicit platform (foreseeable when miaoshou is onboarded)

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts/by-external/7494763368967603447?platform=miaoshou"
```

→ 200 if a miaoshou row with that external id exists, 404 otherwise.

### 5.3 Unknown external_account_id

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts/by-external/DOES_NOT_EXIST"
```

→ 404:
```json
{
  "detail": "channel account not found for platform='tiktok' external_account_id='DOES_NOT_EXIST'"
}
```

### 5.4 Known row, wrong platform

```bash
# TikTok row exists for "7494763368967603447"; ask for platform=miaoshou → 404
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts/by-external/7494763368967603447?platform=miaoshou"
```

→ 404 (prevents accidental cross-platform collisions).

---

## 6. Properties

| Property | Value |
| --- | --- |
| HTTP method | `GET` (idempotent) |
| Side effects | **none** |
| Caching | safe — channel_account rows are mutated rarely. Client-side caching is fine. |
| Pagination | n/a (single record) |
| Rate limiting | inherits the app-wide per-key bucket |

---

## 7. Discovery

| Need | Where |
| --- | --- |
| Live route list | `GET /endpoints` |
| OpenAPI schema | `GET /openapi.json` |
| Swagger UI | `GET /docs` |
| ReDoc UI | `GET /redoc` |

---

## 8. Implementation pointers

| Concern | File |
| --- | --- |
| Router | `tts_erp_v2/api/v2/commerce.py::get_channel_account_by_external` |
| SQL constant | `tts_erp_v2/api/v2/commerce.py::SQL_GET_CHANNEL_ACCOUNT_BY_EXTERNAL` |
| Row mapper | `tts_erp_v2/api/v2/commerce.py::_row_to_channel_account` |
| Response schema | `tts_erp_v2/api/schemas.py::ChannelAccountOut` |
| Role classification | `tts_erp_v2/middleware/auth.py::_READONLY_PREFIXES` (`/v2/commerce/`) |
| Tests | `tests/api/test_commerce_by_external.py` (6 functional + 4 OpenAPI regression) |

---

## 9. Related routes

| Route | Purpose |
| --- | --- |
| `GET /v2/commerce/channel-accounts` | List all accounts (filter `?platform=`) |
| `GET /v2/commerce/channel-accounts/{shop_pk}` | Look up by internal bigint PK |
| `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | **This endpoint** — look up by upstream shop_id |

The three together cover both forward and reverse lookup patterns
without forcing callers to dump the table.
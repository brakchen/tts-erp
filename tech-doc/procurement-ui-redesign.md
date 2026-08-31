# Procurement UI redesign — manual cost entry + SPU image upload

> **⚠ SUPERSEDED 2026-08-31 (same day, user decision)**: the custom
> design-system CSS (`console.css`, §2.3 tokens, IBM Plex typography, FILED
> stamp) was **dropped in favor of Bootstrap 5.3.8** (self-hosted at
> `/static/vendor/`, MIT). Rationale: hand-rolled CSS shipped broken twice
> in one day (missing font files; absolute asset paths that 404'd behind
> the NGINX `/tts` prefix). §2's aesthetic direction no longer applies;
> §3 backend contracts remain accurate. The JS keeps plain-DOM logic but
> renders Bootstrap classes only, and derives the public path prefix from
> `location.pathname`.

> Feature branch: `feature/procurement-ui` · worktree: `~/tts-erp.procurement`
> Owner: schan · Date: 2026-08-31
> Status: implementation in progress

## 1. What & why

`/v2/pages/manual-costs` was a single-file `<details>`-token-paste table that
predated the session-cookie login flow and predated any image storage. We now
need:

1. **Shop switcher** — a single console used across multiple TikTok shops,
   with the active shop scoping every list/upload.
2. **Per-SPU image upload** — operators attach supplier reference photos
   (packing slips, WeChat shots, weight-scale photos) to each SPU so future
   manual-cost disputes can be resolved against the actual product.

Constraints (from `AGENTS.md`):

- tts-erp is a FastAPI app — no SPA framework, no Jinja (server-rendered HTML
  + plain inline JS, per `tts_erp_v2/api/v2/pages.py` precedent).
- v2 endpoint conventions: `/v2/<domain>/<resource>` (see
  `tech-doc/external-api.md`), bearer/cookie auth, role gates in
  `tts_erp_v2/middleware/auth.py::required_role`.
- New dependency (`minio`) goes into the shared `.venv` only.
- TDD: tests first, then implementation (see `tdd/conftest.py` for the
  transactional-rollback fixture convention).

## 2. Design direction

### 2.1 Subject

An **internal procurement workbench** — a 1–2 person ops team triages a small
batch of TikTok SPUs each day to fill in missing unit costs and attach
reference photos. The page is not a dashboard; it's where someone sits down
for 20 minutes and does a focused filing task. The world it lives in is
paper receipts, packing slips, weight-scale printouts, and currency
conversions — the back office of a small cross-border trading house.

### 2.2 Aesthetic

**Commercial-invoice / warehouse-receipt.** Deliberately not:

- The warm cream + terracotta café default.
- The near-black + acid green crypto-terminal default.
- The broadsheet newspaper default.

References: the back of an airline cargo waybill, a paid-in-full stamp on
a customs declaration, the rubber stamp on a Hong Kong trading-house
receipt.

### 2.3 Token system

| Token        | Hex       | Use                                                     |
|--------------|-----------|---------------------------------------------------------|
| `ink`        | `#1B1F23` | primary text, table headers, form labels                 |
| `graphite`   | `#5C6470` | secondary text, captions, helper text                   |
| `paper`      | `#E8E2D5` | page background (warm ledger tint, never the loud color)|
| `slip`       | `#FFFFFF` | card / table-row background                              |
| `stamp`      | `#A8341E` | primary action accent, active-tab underline, the "FILED" stamp animation |
| `settled`    | `#2F6B4F` | success / saved state                                    |
| `rule`       | `#D9D2C2` | hairline rules between table rows                        |

| Role     | Family           | Weight / use                                            |
|----------|------------------|---------------------------------------------------------|
| Display  | IBM Plex Serif   | 600 — h1 / h2 / page chrome                             |
| Body     | IBM Plex Sans    | 400 body, 500 table headers, 600 inline labels          |
| Mono     | JetBrains Mono   | SKU codes, currency amounts, sizes, byte counts — all numeric & code data |

Type scale: 11px mono · 13px body · 15px subhead · 22px h2 · 32px h1.
Table leading 1.4; prose leading 1.55. IBM Plex and JetBrains Mono are
self-hosted from `/static/fonts/` (no Google Fonts dependency, no third-party
CDN leak of operator IP).

### 2.4 Layout

Two-row split:

```
+-------------------------------------------------------------------------+
|  tts-erp · procurement    shop: [ VN-MAINSHOP ▾ ]   ops: schan · logout |
+-------------------------------------------------------------------------+
|                                                                         |
|   ┌─ Needs cost ───┐  ┌─ Needs photo ───┐  ┌─ Recently filed ───┐       |
|   │    12 to fill  │  │    3 missing    │  │    last 7d: 47     │       |
|   └────────────────┘  └─────────────────┘  └────────────────────┘       |
|                                                                         |
|   filter: [ all categories ▾ ]   search: [_________]   50 / page ▾     |
|                                                                         |
|   +--------+------------------+---------------+----------------------+  |
|   | SKU    | Title            | State         | Action               |  |
|   +--------+------------------+---------------+----------------------+  |
|   | 9VN-K1 | Cotton tee navy M| ⊘ no cost     | [12.40] VND [Submit] |  |
|   | 9VN-K2 | Cotton tee navy L| ⊘ no cost     | [14.20] VND [Submit] |  |
|   | 9VN-P1 | Polar fleece red | ✚ photo only  | [📷 drop or browse]  |  |
|   +--------+------------------+---------------+----------------------+  |
|                                                                         |
|   ← prev                                              next →            |
+-------------------------------------------------------------------------+
```

Tab labels are **operational states of the data**, not numeric indices.
Switching tabs swaps the row set; it does not paginate within one.

### 2.5 Signature element

**The "FILED" stamp.** When a row is successfully submitted (cost or photo),
a red diagonal `FILED` mark slides in over the row from the top-left,
sits for 600 ms, then the row collapses out of view. It echoes a real
rubber stamp on a paper invoice — one orchestrated moment instead of
scattered micro-feedback.

### 2.6 Empty / loading / error

- Empty: "All SPUs in this shop have a cost and a photo. Nice." (conversational,
  not an apology).
- Loading: hairline shimmer through the affected rows (table-stripe
  animation, not a full-screen spinner).
- Network error: inline message in the row's status cell + a single retry
  affordance. No modal, no toast.

### 2.7 Accessibility floor

- All interactive elements keyboard-reachable, `:focus-visible` shows a
  2 px `stamp`-colored outline.
- `prefers-reduced-motion` disables the stamp animation (it just collapses).
- Color contrast ≥ 4.5:1 for body text, 3:1 for large text.
- Drag-and-drop has a click-equivalent (`<input type=file>`).

## 3. Backend contracts

All routes registered on the v2 app via `app.include_router(spu_images_router,
prefix="/v2/spu-images", tags=["spu-images"])`.

### 3.1 `POST /v2/spu-images/upload-url`

Issues a presigned MinIO PUT URL the browser uses to upload the file
directly to MinIO (server never proxies the bytes — keeps memory bounded
and lets the browser do multipart/chunked if needed).

- Auth: readwrite or admin (CSRF `X-Requested-With: tts-erp` enforced for
  cookie-authed callers, same as `POST /v2/reporting/manual-costs`).
- Request body:

  ```json
  {
    "channel_account_id": 7,            // scopes the upload to one shop
    "channel_product_id": 1234,         // SPU the image belongs to
    "filename": "packing-slip-front.jpg", // sanitised server-side
    "content_type": "image/jpeg",
    "size_bytes": 184320                 // sanity-bound ≤ 8 MiB
  }
  ```

- Response `201`:

  ```json
  {
    "image_id": 555,
    "object_key": "shops/7/spus/1234/2026-08-31/555-packing-slip-front.jpg",
    "upload_url": "http://127.0.0.1:9000/tts-erp-spu-images/...?X-Amz-...",
    "upload_expires_at": "2026-08-31T03:14:07Z",
    "required_headers": { "Content-Type": "image/jpeg" }
  }
  ```

- Errors: `400` (bad filename / content_type / size), `403` (auth/role),
  `404` (channel_account_id or channel_product_id not found).

Object-key layout (set on the server, never user-controlled):

```
shops/<channel_account_id>/spus/<channel_product_id>/<YYYY-MM-DD>/<image_id>-<slug>.<ext>
```

### 3.2 `POST /v2/spu-images/{image_id}/confirm`

Called after the browser PUT to MinIO succeeds. Server HEADs the object
to verify size + content-type match, marks the row `status='ready'`, and
returns the read URL (presigned or public-base, depending on
`MINIO_PUBLIC_BASE_URL`).

- Auth: same as upload-url.
- Response `200`:

  ```json
  {
    "image_id": 555,
    "status": "ready",
    "object_key": "shops/7/spus/1234/2026-08-31/555-packing-slip-front.jpg",
    "size_bytes": 184320,
    "content_type": "image/jpeg",
    "url": "http://127.0.0.1:9000/...?X-Amz-...",
    "url_expires_at": "2026-08-31T03:29:07Z"
  }
  ```

- If the HEAD fails: `409` `UPLOAD_NOT_FOUND` with a hint to retry the
  upload-url flow.

### 3.3 `GET /v2/spu-images?channel_product_id=X`

Lists ready images for one SPU. Used by the "Needs photo" tab counter
and by the gallery on the per-SPU detail view.

- Auth: readonly.
- Response `200`:

  ```json
  [
    {
      "image_id": 555,
      "channel_product_id": 1234,
      "object_key": "shops/7/spus/1234/2026-08-31/555-...",
      "filename": "packing-slip-front.jpg",
      "content_type": "image/jpeg",
      "size_bytes": 184320,
      "uploaded_at": "2026-08-31T03:13:42Z",
      "uploaded_by_key_prefix": "ttserp_rw_",
      "url": "http://127.0.0.1:9000/...?X-Amzz-...",
      "url_expires_at": "2026-08-31T03:29:07Z"
    }
  ]
  ```

### 3.4 `DELETE /v2/spu-images/{image_id}`

Soft-delete: marks `deleted_at`, removes the MinIO object on a best-effort
basis (logged but not fatal if MinIO fails). Idempotent.

- Auth: readwrite or admin.
- Response `204`.

### 3.5 Tab counters

`GET /v2/reporting/missing-cost-products` already exists (returns active
SPUs with no manual cost and no link). We extend it to accept
`?channel_account_id=X` and to include a new `missing_photo` flag (counts
of images vs. presence/absence). The frontend tab counters are read off
these endpoints; we don't add a dedicated `/counts` endpoint.

## 4. Database schema

New table in schema `procurement`:

```sql
-- schema_storage.sql (tts_erp_v2/storage/schema_storage.sql)
CREATE SCHEMA IF NOT EXISTS procurement;

CREATE TABLE IF NOT EXISTS procurement.spu_images (
    id                  BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    channel_account_id  BIGINT NOT NULL
        REFERENCES commerce.channel_accounts(id) ON DELETE RESTRICT,
    channel_product_id  BIGINT NOT NULL
        REFERENCES commerce.channel_products(id) ON DELETE RESTRICT,
    object_key          TEXT NOT NULL UNIQUE,
    filename            TEXT NOT NULL,
    content_type        TEXT NOT NULL,
    size_bytes          BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 8388608),
    status              TEXT NOT NULL DEFAULT 'awaiting_upload'
        CHECK (status IN ('awaiting_upload','ready','failed')),
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by_key_id  BIGINT REFERENCES security.api_keys(id) ON DELETE SET NULL,
    uploaded_by_prefix  TEXT,
    deleted_at          TIMESTAMPTZ,
    failure_reason      TEXT,
    raw_metadata        JSONB
);

CREATE INDEX IF NOT EXISTS ix_spu_images_product_status
    ON procurement.spu_images (channel_product_id, status)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_spu_images_account_uploaded
    ON procurement.spu_images (channel_account_id, uploaded_at DESC)
    WHERE deleted_at IS NULL;
```

Three states matter:

- `awaiting_upload` — upload-url was issued, browser hasn't confirmed yet.
  Garbage-collected by a daily job after 24 h.
- `ready` — head-OK, served by GET endpoint.
- `failed` — head failed, kept for operator inspection for 7 days.

`uploaded_by_key_id` resolves to `security.api_keys.id`; `uploaded_by_prefix`
is denormalised so revocations don't lose the audit trail. The plain key
never leaves the security schema.

## 5. MinIO client (env-driven)

`tts_erp_v2/storage/minio_client.py` — one `MinioClient` class:

- Reads `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
  `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_REGION`,
  `MINIO_PRESIGN_EXPIRY_SECONDS`, `MINIO_PUBLIC_BASE_URL` from env at
  construction time (fail fast on missing required).
- `ensure_bucket()` — idempotent; called once at startup lifespan.
- `presign_put(object_key, content_type, expiry=None) -> str`
- `presign_get(object_key, expiry=None) -> tuple[str, datetime]`
- `stat(object_key) -> dict` — wraps `stat_object`, raises `ObjectNotFound`.
- `remove(object_key)` — best-effort, swallows `ObjectNotFound`.
- `public_url_or_none(object_key) -> str | None` — returns plain URL if
  `MINIO_PUBLIC_BASE_URL` is set, else `None`.

All SDK calls go through this client — no other module imports `minio`
directly. Easier to mock in tests.

## 6. Frontend contracts

The page HTML template lives in `tts_erp_v2/api/v2/pages.py` but the CSS
and JS move to `tts_erp_v2/static/css/console.css` and
`tts_erp_v2/static/js/console.js` — referenced via `/static/...` and
mounted by `app.mount("/static", StaticFiles(directory=...))`.

The page JS layer has three top-level functions (no framework, no build
step):

- `loadShops()` → `GET /v2/commerce/channel-accounts?platform=tiktok`
  populates the dropdown. Persists `active_account_id` to
  `localStorage["mc_active_account"]`.
- `loadTab(name)` → switches the workbench to one of `needs_cost`,
  `needs_photo`, `recent`. Calls the appropriate list endpoint and
  re-renders the table.
- `submitCost(row)` / `submitPhoto(row)` — inline filing per row.
  Photo path: `POST /upload-url` → `PUT upload_url` → `POST /confirm`.
  All three steps show a per-row status; the row is collapsed only
  after the final step returns 2xx.

The token paste UI (`<details>API token…</details>`) is **deleted**.
Login is now via `/v2/auth/login` (cookie). The page checks
`/v2/auth/me` on load; if it returns 401, the JS redirects to the login
page with `?next=/v2/pages/manual-costs`.

## 7. Auth & roles

New endpoints in `tts_erp_v2/middleware/auth.py::required_role`:

| Endpoint                                 | Role        |
|------------------------------------------|-------------|
| `POST /v2/spu-images/upload-url`         | readwrite   |
| `POST /v2/spu-images/{id}/confirm`       | readwrite   |
| `GET  /v2/spu-images`                    | readonly    |
| `DELETE /v2/spu-images/{id}`             | readwrite   |

CSRF guard on POST: cookie-authed callers must send
`X-Requested-With: tts-erp`, mirroring
`POST /v2/reporting/manual-costs`.

## 8. Out of scope (this PR)

- Multi-image gallery on a separate per-SPU page (we just list in the
  table; a future PR can add `/v2/pages/spu/{id}`).
- Variant-level (SKU) images — only SPU-level.
- Image moderation / EXIF stripping.
- Server-side thumbnail generation.
- Direct browser → MinIO upload without server-side presign (no signed
  cookies, no public-write buckets).

## 9. Test plan

| Layer        | File                                                  | What                                                                  |
|--------------|-------------------------------------------------------|-----------------------------------------------------------------------|
| MinIO client | `tests_v2/storage/test_minio_client.py`               | presign URL shape, ensure_bucket idempotency, stat errors              |
| Backend API  | `tests_v2/api/test_spu_images.py`                     | upload-url happy path + bad size / bad content-type; confirm head-fail|
|              |                                                       | list filter; delete idempotency; role gates                           |
| Frontend     | `tests_v2/api/test_manual_costs_page_v2.py`           | page renders shop switcher + 3 tabs; static assets served             |
| E2E smoke    | `tests_v2/api/test_spu_images_e2e.py`                 | upload-url → PUT to fake MinIO (mitmproxy or in-proc mock) → confirm   |

Tests use the existing `conftest.py` transaction-rollback pattern. MinIO
is mocked at the client boundary (the SDK is never imported in tests).
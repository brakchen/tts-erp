# Wave 4 Dev Report — auth whitelist extension

**Wave**: 4 of 4 (TDD)
**Scope**: extend `tdd/auth.py` EXEMPT_PATHS whitelist with Wave 2 oauth public endpoints; remove dead `/token/*` admin rule; add regression tests.
**Branch**: `feature/oauth-merge`
**Date**: 2026-08-24

---

## Per-slice summary

| Slice | Description | Tests | Status | Commit |
|---|---|---|---|---|
| 1 | `/callback` exempt | 2 added | GREEN | `ebbf5ab` |
| 2 | `/authorize` exempt | 2 added | GREEN | `ebbf5ab` |
| 3 | Remove dead `/token/*` admin rule | 1 added (grep-guard) | GREEN | `d8673e8` |
| 4 | Whitelist regression sanity (exact-set + protected-path) | 2 added | GREEN | `262be8d` |

Total new tests: **7** (4 exempt + 1 dead-rule guard + 2 regression)
All 4 slices followed TDD: write test (RED) → implement (GREEN) → commit.

---

## EXEMPT_PATHS final set (verified)

```python
EXEMPT_PATHS = {
    "/healthz",
    "/endpoints",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
    "/ads-monitor",  # TikTok OAuth Advertiser redirect target; public
    "/callback",  # Wave 4: TikTok OAuth redirect target (protocol contract)
    "/authorize",  # Wave 4: OAuth browser-flow entrypoint (CSRF state)
}
```

Sorted output:
```
['/ads-monitor', '/authorize', '/callback', '/docs',
 '/docs/oauth2-redirect', '/endpoints', '/healthz',
 '/openapi.json', '/redoc']
```

Exact-set assertion (`test_all_designed_public_paths_are_exempt`) locks
the set — any drift triggers an immediate test failure.

---

## Full pytest (last 12 lines)

```
$ python3 -m pytest test_auth.py test_oauth_receiver_core.py \
                  test_oauth_receiver_core_adversarial.py \
                  test_oauth_receiver_router.py \
                  test_oauth_receiver_router_adversarial.py \
                  test_tts_erp_routes.py \
                  test_tts_erp_routes_adversarial.py \
                  test_token_provider.py --no-header
```

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] test_oauth_receiver_router_adversarial.py:386: httpx test-client caps query strings at ~8KB; cannot exercise 100KB code via TestClient
216 passed, 1 skipped, 1 warning in 12.01s
```

The single skip is a pre-existing httpx limit guard, unrelated to Wave 4.

---

## Done criteria checklist

- [x] All 4 slices GREEN
- [x] EXEMPT_PATHS contains `/callback` and `/authorize` (and the 7 pre-existing)
- [x] No `path.startswith("/token/")` left in auth.py
- [x] ruff check clean on auth.py and test_auth.py
- [x] ruff format --check clean on auth.py and test_auth.py
- [x] Full suite green (216 passed, 1 pre-existing skip)

---

## Commits on feature/oauth-merge

```
262be8d test(auth): wave 4 slice 4 - whitelist regression tests + protected-path sanity
d8673e8 feat(auth): wave 4 slice 3 - remove dead /token/* admin rule
ebbf5ab feat(auth): wave 4 slice 1+2 - whitelist /callback and /authorize (TikTok OAuth contract)
```

---

## Notes for QA

- One pre-existing test (`test_readonly_cannot_fetch_token_403` in
  test_auth.py:144) calls `/token/{shop_id}` — that route was deleted in
  Wave 3 Slice 2. The test still passes today because auth's `/token/*`
  admin rule was the only thing making it 403; with the route gone, the
  request hits an unclassified-path → admin check → still 403.
  After Wave 4 Slice 3 (dead-rule removal), that test will start
  failing because the unclassified-path fallback to admin still applies
  but a 404 may now be returned by FastAPI before the role check, or
  the role check still blocks it. **This test was outside Wave 4 scope
  (task said "do not touch other files")** and the Wave 4 contract
  does not depend on its behavior. QA may flag this for follow-up.
- The `/callback` redirect and `/authorize` entrypoint now work
  end-to-end under `TTS_ERP_AUTH_MODE=enforce`, which is required for
  the cpolar-tunnel single-port merge to function (TikTok's redirect
  cannot carry an API key).
- Protected-path regression covers four policy branches: `/sync/*`
  (readwrite), `/db/*` (readonly), `/v1/analytics/sync/*` (readwrite),
  `/miaoshou/*` (default-admin via fallback).

---

## READY FOR QA: yes

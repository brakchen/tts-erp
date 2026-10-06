# Test split by domain

The suite under `tests/` uses **domain** and **layer** markers so a business slice can run through the repository's isolated test entry point.

## Safety boundary

For agents, every test command must start with:

```bash
bash scripts/test_isolated.sh
```

This wrapper clones `tts_erp_test_template` into a per-run ephemeral `tts_erp_test_*` database, delegates marker selection internally, and drops the clone afterward. Do not bypass it with a low-level runner, a shared long-lived database, or a bare Python test command. Production-shaped databases are always forbidden.

Historical migration suites under `docs/archive/migrate-v1-to-v2-2026-08-29/` are archival evidence only and must never be executed.

## Taxonomy

Every test file carries one or more module-level markers.

### Business domains

| Marker | What it covers |
| --- | --- |
| `domain_miaoshou` | 妙手 SDK / callbacks / jobs |
| `domain_commerce` | TikTok 订单/商品同步 |
| `domain_finance` | 财务/对账与利润/成本报表 |
| `domain_logistics` | 物流追踪 |
| `domain_after_sales` | 退货/取消 |
| `domain_reporting` | 报表 |
| `domain_api` | FastAPI routes, auth, middleware |
| `domain_proxy` | 出站代理、签名和 token service |
| `domain_middleware` | 中间件 |
| `domain_sync` | 同步 Worker |
| `domain_models` | ORM/model smoke tests |
| `domain_token_refresh` | Token renewal jobs |
| `domain_sdk` | SDK tests |
| `domain_e2e` | Live `:9877` read-only smoke tests |
| `domain_browser` | Local browser-render regressions; included by `fast` |

`domain_migration` refers only to historical migration coverage and is excluded from agent execution.

### Layers

| Marker | Meaning |
| --- | --- |
| `layer_unit` | Pure helpers without database fixtures |
| `layer_integration` | Database, fixtures, or `TestClient` |
| `slow` | Slow tests |
| `requires_db` | Requires the isolated PostgreSQL clone |
| `requires_service` | Requires a live local service |
| `requires_browser` | Requires Playwright and Chromium |

## Canonical invocations

```bash
# Fast suite
bash scripts/test_isolated.sh fast

# Pure unit layer
bash scripts/test_isolated.sh unit

# One business domain
bash scripts/test_isolated.sh commerce
bash scripts/test_isolated.sh miaoshou
bash scripts/test_isolated.sh finance

# One file or test while retaining isolation
bash scripts/test_isolated.sh fast tests/api/test_auth_login.py
bash scripts/test_isolated.sh fast tests/api/test_auth_login.py::test_login_sets_cookie

# Browser and live read-only e2e domains
bash scripts/test_isolated.sh browser
bash scripts/test_isolated.sh e2e

# Refresh a stale template before the selected safe suite
bash scripts/test_isolated.sh --refresh-template fast
```

There is no agent `all` or `coverage` path. Those selections include historical or service-dependent behavior outside the safe agent contract.

## Browser gates

| Layer | Location | Isolated command | Protects |
| --- | --- | --- | --- |
| Static lint | `tests/api/test_style_tokens.py` | `bash scripts/test_isolated.sh fast` | Unapproved font-family literals |
| Browser | `tests/browser/` | `bash scripts/test_isolated.sh browser` | Multi-viewport overflow and computed font stacks |
| Live e2e | `tests/e2e/` | `bash scripts/test_isolated.sh e2e` | Read-only deployed contracts |

Browser tests render the current worktree's Jinja and static assets with local mocks. Missing Playwright/Chromium may produce documented skips; publishing's canonical gate requires zero publishing skips.

## Mapping files to domains

| Edited area | Isolated command |
| --- | --- |
| `tts_erp_v2/jobs/tiktok/orders.py` | `bash scripts/test_isolated.sh commerce` |
| `tts_erp_v2/jobs/tiktok/finance.py` | `bash scripts/test_isolated.sh finance` |
| `tts_erp_v2/jobs/tiktok/logistics.py` | `bash scripts/test_isolated.sh logistics` |
| `tts_erp_v2/jobs/tiktok/after_sales.py` | `bash scripts/test_isolated.sh after_sales` |
| `tts_erp_v2/jobs/miaoshou/*.py` | `bash scripts/test_isolated.sh miaoshou` |
| `tts_erp_v2/api/**/*.py` | `bash scripts/test_isolated.sh api` |
| `tts_erp_v2/middleware/*.py` | `bash scripts/test_isolated.sh middleware` |
| `tts_erp_v2/reporting/*.py` | `bash scripts/test_isolated.sh reporting finance` |
| `tts_erp_v2/proxy/*.py` | `bash scripts/test_isolated.sh proxy` |
| `miaoshou/miaoshou_signing.py` | `bash scripts/test_isolated.sh miaoshou unit` |
| `tests/browser/**` | `bash scripts/test_isolated.sh browser` |
| CSS/templates | `bash scripts/test_isolated.sh fast` then optional isolated `e2e` |

## Adding tests

Use the narrowest public seam and a module-level domain marker:

```python
import pytest

pytestmark = pytest.mark.domain_<your_domain>
```

Add `requires_db`, `requires_service`, or `requires_browser` only when the behavior actually needs it. Prefix test-owned data with `TEST_`, keep fixtures owner-scoped, and run the narrow slice before the canonical fast gate.

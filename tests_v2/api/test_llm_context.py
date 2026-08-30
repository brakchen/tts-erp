"""TDD tests for GET /v2/llm-context.

The LLM context endpoint is a self-describing system + data dictionary
that LLM agents can fetch to understand the v2 architecture, schema, and
business rules. It must be:

- Authenticated (no anonymous access).
- Readonly (any role can see it; no leakage via role escalation).
- Stable across deployments (same output for the same schema).
- Secrets-free (no Fernet key, no app_secret, no api_key plaintext).
- Helpful: must mention the 9 schemas, the cost priority chain, the
  known pitfalls, and the v2 API surface.
"""
from __future__ import annotations

# All of these are real phrases the LLM must see in the output to
# understand the system correctly. Each one encodes a non-obvious
# business rule the user MUST be told.
REQUIRED_PHRASES = [
    # Identity
    "tts-erp",
    "v2",  # matches both "tts-erp v2" and "tts-erp (v2)"
    # Schemas — all 9
    "integration", "commerce", "procurement", "fulfillment",
    "after_sales", "finance", "linkage", "reporting", "security",
    # The view
    "effective_product_links",
    # Cost rules — the core business invariant
    "MANUAL_ENTRY",
    "1688",
    "missing-cost-products",
    "manual-costs",
    # Architecture
    "sync-worker",
    "APScheduler",
    "FastAPI",
    # Auth
    "readonly", "readwrite", "admin",
    "api_key" if False else "Authorization",  # noqa: just for readability
    # Endpoints — at least the v2 canonical ones
    "/v2/commerce/sales-orders",
    "/v2/commerce/channel-products",
    "/v2/linkage/product-links",
    "/v2/reporting/profit-daily",
    "/v2/reporting/coverage",
    "/v2/reporting/missing-cost-products",
    "/v2/pages/manual-costs",
    "/v2/llm-context",
    # Pitfalls
    "public.*",
    # Versioning
    "1d8ed0d",
]


SECRETS_NEVER = [
    "TTS_ERP_FERNET_KEY=",
    "TIKTOK_APP_SECRET=",
    "MIAOSHOU_COMPANY_SECRET=",
    "ttserp_ro_",
    "ttserp_rw_",
    "ttserp_admin_",
]


# ── /v2/llm-context endpoint tests ───────────────────────────────────


def test_llm_context_no_auth_returns_401(api_client):
    """No key → 401 (matches every other v2 endpoint convention)."""
    r = api_client.get("/v2/llm-context")
    assert r.status_code == 401, r.text


def test_llm_context_bad_key_returns_401(api_client):
    """Invalid key → 401."""
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 401, r.text


def test_llm_context_readonly_key_succeeds(api_client, readonly_key):
    """The endpoint is readonly; a readonly key must get 200."""
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert "text/markdown" in r.headers["content-type"]


def test_llm_context_readwrite_key_succeeds(api_client, readwrite_key):
    """readwrite also works (readwrite >= readonly)."""
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text


def test_llm_context_admin_key_succeeds(api_client, admin_key):
    """admin also works (admin >= readonly)."""
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text


def test_llm_context_markdown_contains_required_business_rules(
    api_client, readonly_key
):
    """The LLM must see every business-critical phrase in the markdown.

    If a maintainer accidentally deletes a section (e.g. the 1688 cost
    prohibition), the LLM agent would silently start using the wrong
    cost source. This test guards against that.
    """
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    missing = [p for p in REQUIRED_PHRASES if p not in body]
    assert not missing, f"missing required phrases in LLM context: {missing}"


def test_llm_context_markdown_contains_no_secrets(api_client, readonly_key):
    """The LLM context must NEVER contain real secret values.

    It should mention the NAMES of the secrets in the secrets-hygiene
    section, but not the values. This test asserts none of the env-var
    patterns show up in plaintext.
    """
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    leaked = [s for s in SECRETS_NEVER if s in body]
    assert not leaked, f"LEAKED SECRETS in LLM context: {leaked}"


def test_llm_context_json_format_returns_structured_envelope(
    api_client, readonly_key
):
    """?format=json returns the same content wrapped in a JSON envelope."""
    r = api_client.get(
        "/v2/llm-context?format=json",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    payload = r.json()
    assert payload["schema_version"] == "v2-1"
    assert "sections" in payload
    assert isinstance(payload["sections"], list)
    assert len(payload["sections"]) >= 5
    section_ids = [s["id"] for s in payload["sections"]]
    assert "cost_profit" in section_ids
    assert "pitfalls" in section_ids
    assert "tables_live" in section_ids


def test_llm_context_includes_live_table_introspection(
    api_client, readonly_key
):
    """The dynamic §9 section must show real table names from PG.

    This proves the endpoint actually queries information_schema
    rather than hard-coding a stale list.
    """
    r = api_client.get(
        "/v2/llm-context?format=json",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    payload = r.json()
    tables_section = next(
        s for s in payload["sections"] if s["id"] == "tables_live"
    )
    body = tables_section["body"]
    # These tables MUST be present in the live introspection
    # (they're created by the V3 alembic init migration).
    for required in [
        "commerce.sales_orders",
        "procurement.manual_product_costs",
        "linkage.product_links",
        "finance.settlement_components",
    ]:
        assert required in body, f"missing live table: {required}"


def test_llm_context_cache_header_present(api_client, readonly_key):
    """No-cache header prevents stale LLM contexts after a schema change."""
    r = api_client.get(
        "/v2/llm-context",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert "no-cache" in r.headers.get("cache-control", "").lower()
    # Schema-version header helps LLM clients detect drift.
    assert r.headers.get("x-llm-context-schema-version") == "v2-1"


def test_llm_context_invalid_format_rejected(api_client, readonly_key):
    """?format=anything-other-than-md|json → 422 (validation error)."""
    r = api_client.get(
        "/v2/llm-context?format=xml",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 422, r.text


def test_llm_context_json_envelope_no_secrets_either(
    api_client, readonly_key
):
    """The JSON envelope (not just markdown) must also be secrets-free."""
    r = api_client.get(
        "/v2/llm-context?format=json",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    leaked = [s for s in SECRETS_NEVER if s in body]
    assert not leaked, f"LEAKED SECRETS in JSON envelope: {leaked}"


def test_llm_context_section_count_at_least_eight(
    api_client, readonly_key
):
    """We have 10 hand-curated sections; require >= 8 to catch silent
    section-deletion bugs."""
    r = api_client.get(
        "/v2/llm-context?format=json",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    payload = r.json()
    assert len(payload["sections"]) >= 8

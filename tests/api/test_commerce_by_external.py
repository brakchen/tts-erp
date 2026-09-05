"""TDD tests for ``GET /v2/commerce/channel-accounts/by-external/{shop_id}``.

Reverse-lookup endpoint: given the upstream shop_id
(``shop_id``, e.g. the TikTok shop_id) plus a ``platform``
filter, return the internal ``commerce.shops`` row.

Why this exists
---------------
The list endpoint ``GET /v2/commerce/channel-accounts`` already accepts
``?platform=`` but returns *all* matching rows. Dashboards / scripts
that only know the upstream shop_id have to fetch the list, filter
client-side, and pray there's exactly one match. This endpoint
collapses that round-trip into one query and returns 404 cleanly when
the row doesn't exist.

Contract (mirrors :mod:`tech-doc/api/channel-accounts-by-external.md`):

* Path: ``{shop_id}`` — upstream shop_id (string).
* Query: ``platform`` (string, default ``"tiktok"``).
* Response: a single :class:`ChannelAccountOut` on hit, 404 on miss.
* Auth: ``readonly`` (the whole ``/v2/commerce/`` prefix is classified
  readonly in ``middleware/auth.py::_READONLY_PREFIXES``).

Test surface
------------
* happy path — explicit ``?platform=tiktok`` returns the seeded row
* default — omitting ``platform`` defaults to ``tiktok``
* 404 — unknown ``shop_id``
* 404 — known ``shop_id`` but wrong ``platform``
* 404 — known row but a different platform's shop_id
* 401 — no API key
* The route is declared BEFORE ``/channel-accounts/{account_id}`` so
  FastAPI matches ``by-external`` as a literal segment, not as an
  ``int`` that fails to coerce.

The ``seed_commerce_rows`` fixture in :mod:`tests.api.test_commerce`
already provisions a tiktok row with shop_id
``"TEST_commerce_acct"``; we reuse it. The :mod:`tests.api.conftest`
autouse wipes TEST_-prefixed rows between tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, text

from tts_erp_v2.db.base import Base

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# Minimal local seeding — we only need the shops row, not
# the full commerce graph that test_commerce.py's seed_commerce_rows
# provisions. Keeping this self-contained avoids cross-file fixture
# coupling and the autouse _isolate_state in tests/api/conftest.py
# wipes TEST_-prefixed rows at teardown.
EXT_ACCT = "TEST_byext_acct"


@pytest.fixture()
def seed_channel_account(db_engine):
    """Insert a single tiktok channel_account row; yield its id."""
    accounts_tbl = Base.metadata.tables["commerce.shops"]
    with db_engine.begin() as conn:
        conn.execute(
            insert(accounts_tbl).values(
                platform="tiktok",
                shop_id=EXT_ACCT,
                account_name="TEST acct by-external",
                region="VN",
                seller_type="CROSS_BORDER",
                status="active",
            )
        )
        return conn.execute(
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": EXT_ACCT},
        ).scalar_one()


def _url(ext: str) -> str:
    return f"/v2/commerce/channel-accounts/by-external/{ext}"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_by_external_happy_path_with_explicit_platform(
    api_client, readonly_key, seed_channel_account
):
    """Explicit ``?platform=tiktok`` returns the seeded tiktok row."""
    r = api_client.get(
        _url(EXT_ACCT),
        params={"platform": "tiktok"},
        headers=_auth(readonly_key),
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seed_channel_account
    assert body["platform"] == "tiktok"
    assert body["shop_id"] == EXT_ACCT


def test_by_external_defaults_platform_to_tiktok(
    api_client, readonly_key, seed_channel_account
):
    """Omitting ``?platform=`` defaults to ``tiktok`` (per user spec)."""
    r = api_client.get(_url(EXT_ACCT), headers=_auth(readonly_key))

    assert r.status_code == 200, r.text
    assert r.json()["shop_id"] == EXT_ACCT


def test_by_external_404_when_shop_id_unknown(
    api_client, readonly_key
):
    """No row matches → 404 (NOT 200 with empty list)."""
    r = api_client.get(
        _url("TEST_unknown_shop_zzz"),
        params={"platform": "tiktok"},
        headers=_auth(readonly_key),
    )

    assert r.status_code == 404, r.text
    assert "not found" in r.json()["detail"].lower()


def test_by_external_404_when_platform_mismatched(
    api_client, readonly_key, seed_channel_account
):
    """Row exists under tiktok but caller asks for miaoshou → 404.

    Prevents accidental cross-platform collisions once we onboard
    miaoshou accounts: ``shop_id`` is only unique within
    a platform, not globally.
    """
    r = api_client.get(
        _url(EXT_ACCT),
        params={"platform": "miaoshou"},
        headers=_auth(readonly_key),
    )

    assert r.status_code == 404, r.text


def test_by_external_401_without_key(api_client):
    r = api_client.get(
        _url(EXT_ACCT),
        params={"platform": "tiktok"},
    )

    assert r.status_code == 401, r.text


def test_by_external_path_segment_does_not_collide_with_account_id_route(
    api_client, readonly_key, seed_channel_account
):
    """`/channel-accounts/by-external/...` must NOT be parsed as
    ``/channel-accounts/{int}``.

    Regression guard: if the by-external route is declared AFTER
    ``/channel-accounts/{account_id}``, FastAPI tries ``account_id=int``
    first, fails to coerce ``"by-external"``, and returns 422 instead
    of 404. We assert 200 (the by-external route matches).
    """
    r = api_client.get(_url(EXT_ACCT), headers=_auth(readonly_key))

    # If the route order regressed, this would be 422 (int parse error)
    # or 404 (account_id="by-external" found nothing). Either way, NOT
    # the 200 we expect when the row exists.
    assert r.status_code == 200, (
        f"expected 200 (by-external route matched), got {r.status_code}: "
        f"{r.text} — likely the by-external route was declared after "
        f"/channel-accounts/{{account_id}}"
    )


# ---------------------------------------------------------------------------
# OpenAPI metadata — regression guard so a future refactor doesn't drop the
# contract docs from /docs Swagger UI.
# ---------------------------------------------------------------------------


PATH_KEY = "/v2/commerce/channel-accounts/by-external/{shop_id}"


def _get_openapi_path(api_client) -> dict:
    r = api_client.get("/openapi.json")
    assert r.status_code == 200, r.text
    return r.json()["paths"][PATH_KEY]["get"]


def test_openapi_summary_is_set(api_client):
    op = _get_openapi_path(api_client)
    summary = op.get("summary", "")
    assert summary, "summary is empty — Swagger UI list view will be blank"
    assert "channel" in summary.lower()
    assert "external" in summary.lower()


def test_openapi_description_references_spec_doc(api_client):
    op = _get_openapi_path(api_client)
    desc = op.get("description", "")
    assert desc, "description is empty"
    assert "tech-doc/api/channel-accounts-by-external.md" in desc, (
        "description must name the canonical spec doc so the two "
        "don't drift"
    )


def test_openapi_responses_documents_full_status_matrix(api_client):
    """401 / 404 / 422 must all appear (the endpoints that can fire)."""
    op = _get_openapi_path(api_client)
    responses = op.get("responses", {})
    assert responses, "responses map is empty"
    for code in ("200", "401", "404", "422"):
        assert code in responses, f"missing OpenAPI response: {code}"


def test_openapi_platform_query_param_is_documented(api_client):
    """``platform`` query param must have a description AND document its default."""
    op = _get_openapi_path(api_client)
    params = op.get("parameters", [])
    by_name = {p["name"]: p for p in params}

    assert "platform" in by_name, "missing OpenAPI parameter: platform"
    p = by_name["platform"]
    assert p.get("description"), "platform param has no description"
    # The default value lives on the schema; assert it's "tiktok".
    schema = p.get("schema", {})
    assert schema.get("default") == "tiktok", (
        f"platform default must be 'tiktok' per spec; got {schema.get('default')!r}"
    )
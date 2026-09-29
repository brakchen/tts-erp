"""GET /v2/fx/* API contract tests (readonly; cached fx.* tables only).

Seeded snapshots use a TEST_-prefixed base code so the shared dev DB is
never polluted with lookalike real-currency rows — the api-conftest
wipe removes every ``base_code LIKE 'TEST_%'`` snapshot after each test.
The 400/404 branches are written to stay deterministic even once the
production fx.sync job has populated real USD rows (unknown-code 400 and
unknown-base 404 never depend on the DB being empty). The one test that
must observe the default-USD path seeds a real-USD snapshot carrying a
TEST_-marked rate (newest-by-id ⇒ deterministic) and the wipe's marker
rule removes it again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ExchangeRate, ExchangeRateSnapshot

TEST_BASE = "TEST_USD"
TEST_RATES = {"TEST_USD": "1", "TEST_CNY": "7.1", "TEST_EUR": "0.9"}


def _seed_snapshot(
    db_engine, *, base: str = TEST_BASE, rates: dict[str, str] | None = None
) -> int:
    """Committed seed visible to the app under test (real commit + wipe)."""
    rates = rates or TEST_RATES
    now = datetime.now(UTC)
    with Session(db_engine) as sess:
        snap = ExchangeRateSnapshot(
            base_code=base,
            upstream_last_update=now - timedelta(hours=1),
            next_update_at=now + timedelta(hours=23),
            fetched_at=now,
            rates_count=len(rates),
        )
        sess.add(snap)
        sess.flush()
        for code, value in rates.items():
            sess.add(
                ExchangeRate(
                    snapshot_id=snap.id,
                    base_code=base,
                    target_code=code,
                    rate=Decimal(value),
                )
            )
        sess.commit()
        return snap.id


def _auth(headers: dict | None = None) -> dict:
    return headers or {}


def test_latest_requires_auth(api_client) -> None:
    r = api_client.get("/v2/fx/latest")
    assert r.status_code == 401, r.text


def test_latest_with_readonly_key_returns_snapshot(
    api_client, readonly_key, db_engine
) -> None:
    _seed_snapshot(db_engine)
    r = api_client.get(
        "/v2/fx/latest",
        params={"base_code": TEST_BASE},
        headers=_auth({"Authorization": f"Bearer {readonly_key}"}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_code"] == TEST_BASE
    assert body["rate_count"] == 3
    assert body["stale"] is False
    # Decimal serializes as a JSON string (repo money-string convention).
    assert body["rates"] == {
        "TEST_USD": "1.00000000",
        "TEST_CNY": "7.10000000",
        "TEST_EUR": "0.90000000",
    }
    assert body["upstream_last_update"] is not None
    assert body["next_update_at"] is not None
    assert body["fetched_at"] is not None


def test_latest_defaults_to_usd_base(api_client, readonly_key, db_engine) -> None:
    """Omitted base_code → the default (USD) cache path.

    The production fx.sync job already writes real USD rows to the
    shared dev DB, so "no USD snapshot ⇒ 404" is no longer assertable.
    Instead seed a real-USD snapshot whose id is newer than every live
    row (⇒ it is the latest) and assert the default endpoint serves it:
    the TEST_-marked rate proves we got our own seed, not a live one.
    """
    # Marker targets (every rate carries the TEST_ prefix): the wipe's
    # marker rule deletes all of this USD snapshot's rates, orphaning it
    # so the follow-up snapshot cleanup removes it even if a test run
    # dies between seed and wipe.
    _seed_snapshot(
        db_engine,
        base="USD",
        rates={"TEST_CNY": "7.25", "TEST_EUR": "8.5"},
    )
    r = api_client.get(
        "/v2/fx/latest", headers=_auth({"Authorization": f"Bearer {readonly_key}"})
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_code"] == "USD"
    assert body["rates"]["TEST_CNY"] == "7.25000000"
    assert body["rates"]["TEST_EUR"] == "8.50000000"
    assert body["rate_count"] == 2
    assert body["stale"] is False


def test_latest_unknown_base_404(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/fx/latest",
        params={"base_code": "TEST_EMPTY"},
        headers=_auth({"Authorization": f"Bearer {readonly_key}"}),
    )
    assert r.status_code == 404, r.text


def test_convert_uses_latest_cached_snapshot(
    api_client, readonly_key, db_engine
) -> None:
    _seed_snapshot(db_engine)
    r = api_client.get(
        "/v2/fx/convert",
        params={
            "amount": 100,
            "from_code": "TEST_CNY",
            "to_code": "TEST_USD",
            "base_code": TEST_BASE,
        },
        headers=_auth({"Authorization": f"Bearer {readonly_key}"}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_code"] == TEST_BASE
    assert body["from_code"] == "TEST_CNY"
    assert body["to_code"] == "TEST_USD"
    assert body["rate"] == "0.14084507"  # 1 / 7.1 quantized to 8 dp
    assert body["converted"] == "14.08450700"
    assert body["amount"] == "100"
    assert body["stale"] is False


def test_convert_cross_pair(api_client, readonly_key, db_engine) -> None:
    _seed_snapshot(db_engine)
    r = api_client.get(
        "/v2/fx/convert",
        params={
            "amount": "7.1",
            "from_code": "TEST_CNY",
            "to_code": "TEST_EUR",
            "base_code": TEST_BASE,
        },
        headers=_auth({"Authorization": f"Bearer {readonly_key}"}),
    )
    assert r.status_code == 200, r.text
    assert r.json()["converted"] == "0.89999998"


def test_convert_unknown_currency_400(api_client, readonly_key, db_engine) -> None:
    """Deterministic: with a snapshot present, an uncached code is a 400."""
    _seed_snapshot(db_engine)
    r = api_client.get(
        "/v2/fx/convert",
        params={
            "amount": 1,
            "from_code": "ZZZ",
            "to_code": "TEST_USD",
            "base_code": TEST_BASE,
        },
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 400, r.text
    assert "ZZZ" in r.json()["detail"]


def test_convert_no_snapshot_404(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/fx/convert",
        params={
            "amount": 1,
            "from_code": "TEST_CNY",
            "to_code": "TEST_USD",
            "base_code": "TEST_EMPTY",
        },
        headers=_auth({"Authorization": f"Bearer {readonly_key}"}),
    )
    assert r.status_code == 404, r.text


def test_fx_paths_listed_in_endpoints_index(api_client) -> None:
    """/endpoints (public) must include the new readonly routes."""
    r = api_client.get("/endpoints")
    assert r.status_code == 200
    paths = {entry["path"] for entry in r.json()["endpoints"]}
    assert "/v2/fx/latest" in paths
    assert "/v2/fx/convert" in paths

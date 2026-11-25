"""Core advertising metrics are normalized before reaching SQL bindings."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from tts_erp_v2.plugin.ads import repository

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]

_DECIMAL_FIELDS = (
    "mixed_real_cost",
    "onsite_roi2_shopping_value",
    "onsite_mixed_real_roi2_shopping",
)
_INTEGER_FIELD = "onsite_roi2_shopping_sku"
_METRIC_FIELDS = (*_DECIMAL_FIELDS, _INTEGER_FIELD)


class _RecordingSession:
    """Exercise the real repository call site without opening a database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    def execute(self, statement, parameters):
        self.calls.append((str(statement), deepcopy(parameters)))
        return SimpleNamespace(rowcount=1)

    def commit(self) -> None:
        self.commits += 1


def _write(kind: str, rows: list[dict[str, Any]], session: _RecordingSession):
    return getattr(repository, f"upsert_{kind}_rows")(
        session,
        seller_id="TEST_metric_seller",
        advertiser_id="TEST_metric_advertiser",
        endpoint="/oec_ads/shopping/v1/oec/stat/post_product_list",
        campaign_id="TEST_metric_campaign",
        day=date(2026, 11, 14),
        rows=rows,
        request_url="https://seller.example.test/analytics",
        request_body={"page": 1},
        response_status=200,
        response_body={"body": {"data": {"table": rows}}},
        created_at=datetime(2026, 11, 25, 9, 56, tzinfo=UTC),
        request_id="TEST_metric_request",
        source="tiktok-shop-data-sync",
    )


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize("placeholder", [None, "", " \t\r\n", "-", " - "])
def test_missing_metrics_bind_null_and_leave_raw_rows_unchanged(kind, placeholder):
    rows = [{"product_id": "TEST_metric_product", **dict.fromkeys(_METRIC_FIELDS, placeholder)}]
    original = deepcopy(rows)
    session = _RecordingSession()

    assert _write(kind, rows, session) == 1

    metrics = session.calls[0][1]
    assert {field: metrics[field] for field in _METRIC_FIELDS} == dict.fromkeys(
        _METRIC_FIELDS
    )
    assert rows == original
    assert session.commits == 1
    import json

    archived = json.loads(session.calls[-1][1]["response_body"])
    assert archived["body"]["data"]["table"] == original


@pytest.mark.parametrize("kind", ["daily", "today"])
def test_numeric_zero_remains_zero_with_typed_sql_bindings(kind):
    row = {
        "product_id": "TEST_metric_zero",
        "mixed_real_cost": 0,
        "onsite_roi2_shopping_value": "0.0000",
        "onsite_mixed_real_roi2_shopping": 0.0,
        "onsite_roi2_shopping_sku": "0",
    }
    session = _RecordingSession()
    _write(kind, [row], session)

    bindings = session.calls[0][1]
    for field in _DECIMAL_FIELDS:
        assert type(bindings[field]) is Decimal
        assert bindings[field] == Decimal(0)
    assert type(bindings[_INTEGER_FIELD]) is int
    assert bindings[_INTEGER_FIELD] == 0


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 1.2500 ", Decimal("1.2500")),
        ("1e-3", Decimal("0.001")),
        (-4.5, Decimal("-4.5")),
        ("9999999999999999.9999", Decimal("9999999999999999.9999")),
    ],
)
def test_decimal_metrics_are_parsed_without_binary_float_conversion(kind, raw, expected):
    row = {"product_id": "TEST_metric_decimal", **dict.fromkeys(_DECIMAL_FIELDS, raw)}
    session = _RecordingSession()
    _write(kind, [row], session)
    for field in _DECIMAL_FIELDS:
        assert type(session.calls[0][1][field]) is Decimal
        assert session.calls[0][1][field] == expected


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" 2 ", 2), ("2.0", 2), (2.0, 2), (str(2**63 - 1), 2**63 - 1), (-(2**63), -(2**63))],
)
def test_order_counts_are_exact_integers_within_bigint_range(kind, raw, expected):
    session = _RecordingSession()
    _write(kind, [{"product_id": "TEST_metric_count", _INTEGER_FIELD: raw}], session)
    assert type(session.calls[0][1][_INTEGER_FIELD]) is int
    assert session.calls[0][1][_INTEGER_FIELD] == expected


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize("field", _METRIC_FIELDS)
@pytest.mark.parametrize("invalid", [True, False, [], {}, "not-a-number", "NaN", "Infinity", float("nan"), float("inf")])
def test_invalid_metrics_fail_explicitly_without_committing(kind, field, invalid):
    session = _RecordingSession()
    row = {"product_id": "TEST_metric_invalid", field: invalid}
    with pytest.raises(ValueError, match=field):
        _write(kind, [row], session)
    assert session.commits == 0


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize("invalid", ["2.5", 2.5, str(2**63), -(2**63) - 1])
def test_fractional_or_overflowing_order_counts_do_not_truncate(kind, invalid):
    session = _RecordingSession()
    with pytest.raises(ValueError, match=_INTEGER_FIELD):
        _write(kind, [{"product_id": "TEST_metric_fraction", _INTEGER_FIELD: invalid}], session)
    assert session.commits == 0


@pytest.mark.parametrize("kind", ["daily", "today"])
@pytest.mark.parametrize("invalid", ["1e16", "-1e16", "9999999999999999.99999"])
def test_decimal_metrics_outside_column_range_fail_before_database_write(kind, invalid):
    session = _RecordingSession()
    with pytest.raises(ValueError, match="mixed_real_cost"):
        _write(kind, [{"product_id": "TEST_metric_overflow", "mixed_real_cost": invalid}], session)
    assert session.commits == 0

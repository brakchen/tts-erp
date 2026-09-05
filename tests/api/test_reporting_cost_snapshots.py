"""Regression tests for ``GET /v2/reporting/cost-snapshots``.

2026-08-31: the optional-filter SQL pattern
    ``WHERE (:channel_id IS NULL OR spu_pk = :channel_id)``
broke with ``psycopg.errors.AmbiguousParameter`` ("could not determine
data type of parameter $1") because PG cannot infer the parameter type
when bound to NULL. The fix adds explicit ``CAST(:channel_id AS bigint)``
and ``CAST(:method AS text)`` so the query plans cleanly with either
NULL or a real value.

These tests pin the contract:

- 200 + bare array (NOT the envelope the procurement UI added to
  ``/missing-cost-products``)
- the OR-short-circuit still works when filters are supplied (no rows is
  expected for nonsense values, but the query must NOT 500)

The companion ``/v2/reporting/profit-daily`` endpoint has the same
AmbiguousParameter bug AND a separate schema-drift bug (SQL selects
columns that don't exist on the actual table — see fleet review notes).
That's a bigger fix and is NOT covered here — see handoff.
"""

from __future__ import annotations


def test_cost_snapshots_no_filter_returns_200_bare_array(api_client, readonly_key):
    """GET with no filters → 200 + JSON array (the previously-500 case).

    Regression guard (2026-08-31): the :channel_id IS NULL branch used to
    fail at query-planning time with AmbiguousParameter because PG could
    not infer the parameter type. After adding ``CAST(:channel_id AS
    bigint)`` the planner is happy and the endpoint returns 200.
    """
    r = api_client.get(
        "/v2/reporting/cost-snapshots?limit=5",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, (
        f"cost-snapshots 500 regression — likely the AmbiguousParameter bug: {r.text}"
    )
    body = r.json()
    assert isinstance(body, list), (
        f"expected bare array (NOT the /missing-cost-products envelope), got {type(body).__name__}"
    )


def test_cost_snapshots_with_filters_returns_200_empty(api_client, readonly_key):
    """Filter values that match nothing → 200 + empty array.

    Exercises the ``OR spu_pk = :channel_id`` branch with
    non-NULL params, so we know the CAST didn't break the OR-short-
    circuit when filters are supplied.
    """
    r = api_client.get(
        "/v2/reporting/cost-snapshots"
        "?limit=5&spu_pk=999999999&cost_method=definitely_not_a_method",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_cost_snapshots_with_only_one_filter_returns_200(api_client, readonly_key):
    """Single-filter combos: each branch of the OR must be reachable.

    PG parameter-type inference is per-statement; this confirms the
    CASTs handle (NULL, real) and (real, NULL) as well as (NULL, NULL)
    from test #1.
    """
    # Only spu_pk
    r1 = api_client.get(
        "/v2/reporting/cost-snapshots?spu_pk=999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r1.status_code == 200, r1.text
    assert isinstance(r1.json(), list)
    # Only cost_method
    r2 = api_client.get(
        "/v2/reporting/cost-snapshots?cost_method=definitely_not_a_method",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r2.status_code == 200, r2.text
    assert isinstance(r2.json(), list)

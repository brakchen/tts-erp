"""Exact-shop catalogue reads and manual-cost history readback on the owned clone.

New acceptance coverage for the read side of the manual-cost workbench: the
shop-scoped catalogue (filters, status, sort keys, limit/offset, the server
count header), the identity pair for one external SPU id shared by two shops,
and the recent manual-cost history read back after one canonical write.
Everything binds to the original owners only (the decorated ``clone`` and
``manual_cost_facts`` fixtures, ``login``/``owned``/``cost_history``/
``checked_session`` and the child liveness checks), adds no engine, proof,
factory, transport, session or fixture owner and modifies nothing: only status
codes, identities, exact decimal strings and booleans are compared, and every
owned ``TEST_`` row is removed again after an ownership check.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.orm import Session

from tests.scripts.test_frontend_remaining_live_api import (
    CSRF,
    OwnedClone,
    cost_history,
    login,
    owned,
)
from tests.scripts.test_frontend_remaining_live_api import (
    clone as owned_clone,
)
from tests.scripts.test_frontend_remaining_live_api import (
    manual_cost_facts as owned_facts,
)
from tests.support.frontend_p2_binding import checked_session
from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct
from tts_erp_v2.db.models.procurement import ManualProductCost

# Expose the original decorated fixtures without wrapping or changing scope.
clone = owned_clone
manual_cost_facts = owned_facts

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]

CATALOGUE = "/v2/commerce/channel-products"
HISTORY = "/v2/reporting/manual-costs"

# The catalogue row is the shop-scoped product plus the cost the server joins:
# one open manual row per SPU and the newest procurement source price as the
# fallback. NULL costs sort to the tail in both directions.
_HAS_ORDERS = text(
    "EXISTS (SELECT 1 FROM commerce.sales_order_lines sol "
    "WHERE sol.spu_pk = commerce.products_spu.id)"
)
_SEED_SNAPSHOT = text(
    "SELECT cp.id, cp.shop_pk, cp.spu_id, cp.status, cp.source_created_at, "
    "cp.source_updated_at, "
    "(SELECT m.unit_cost FROM procurement.manual_product_costs m "
    "WHERE m.spu_pk = cp.id AND m.valid_to IS NULL LIMIT 1) AS unit_cost, "
    "(SELECT pp.source_unit_cost FROM procurement.procurement_products pp "
    "WHERE pp.external_product_id = cp.spu_id AND pp.source_unit_cost IS NOT NULL "
    "ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC LIMIT 1) AS source_unit_cost "
    "FROM commerce.products_spu cp WHERE cp.shop_pk = :shop_pk ORDER BY cp.id"
)
_STATUS_WEIGHT = {"ACTIVATE": 0, "DELETED": 1, "SELLER_DEACTIVATED": 2}
_SORT_KEYS = ("id", "created_at", "updated_at", "unit_cost", "status")


# --- HTTP helpers (real requests, canonical owner checks) -----------------
def catalogue(
    clone: OwnedClone, client: httpx.Client, *, expect: int = 200, **params: Any
) -> httpx.Response:
    """One real catalogue request through the owned child."""
    query = {key: value for key, value in params.items() if value is not None}
    return owned(clone, client.get(clone.prefix + CATALOGUE, params=query), expect)


def history(
    clone: OwnedClone, client: httpx.Client, *, expect: int = 200, **params: Any
) -> httpx.Response:
    """One real recent-submission read through the owned child."""
    query = {key: value for key, value in params.items() if value is not None}
    return owned(clone, client.get(clone.prefix + HISTORY, params=query), expect)


@contextmanager
def session_client(clone: OwnedClone, role: str) -> Iterator[httpx.Client]:
    """A client with one real login, logged out again when the test ends.

    A failing body keeps its own error; the logout is then best effort.
    """
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        login(clone, client, role)
        failed = False
        try:
            yield client
        except BaseException:
            failed = True
            raise
        finally:
            if failed:
                with suppress(Exception):
                    client.post(clone.prefix + "/v2/auth/logout", headers=CSRF)
            else:
                owned(
                    clone,
                    client.post(clone.prefix + "/v2/auth/logout", headers=CSRF),
                    204,
                )


def body(response: httpx.Response) -> Any:
    """Decoded payload; decimals stay exact instead of becoming floats."""
    return response.json(parse_float=Decimal)


def money(value: Any) -> Decimal:
    """Exact decimal from the wire, whatever shape the server used."""
    assert value is not None, "cost is missing on the wire"
    assert isinstance(value, (Decimal, str)), "cost is not a decimal on the wire"
    return value if isinstance(value, Decimal) else Decimal(value)


def row_ids(payload: Any) -> list[int]:
    """Internal identities of a catalogue payload, in server order."""
    assert isinstance(payload, list)
    return [int(row["id"]) for row in payload]


# --- independent SQL (fresh session, not the handler's rows) --------------
def owned_session(clone: OwnedClone) -> AbstractContextManager[Session]:
    """A fresh checked session on the owned clone (original owner)."""
    return checked_session(clone.child.binding, clone.engine, clone.factory)


def product_map(clone: OwnedClone, pks: tuple[int, ...]) -> dict[int, tuple[int, str]]:
    """Owned product identity: id -> (shop_pk, external SPU id)."""
    clone.child.require_live()
    with owned_session(clone) as session:
        rows = session.scalars(
            select(ChannelProduct).where(ChannelProduct.id.in_(pks))
        ).all()
    clone.child.require_live()
    return {row.id: (row.shop_pk, row.spu_id) for row in rows}


def seed_rows(clone: OwnedClone, shop_pk: int) -> list[dict[str, Any]]:
    """The known seed of one shop, read straight from the fresh clone."""
    clone.child.require_live()
    with owned_session(clone) as session:
        rows = session.execute(_SEED_SNAPSHOT, {"shop_pk": shop_pk}).mappings().all()
    clone.child.require_live()
    return [dict(row) for row in rows]


def expected_ids(clone: OwnedClone, *, shop_pk: int, **filters: Any) -> list[int]:
    """Filtered identity set recomputed independently of the handler."""
    conditions: list[Any] = [ChannelProduct.shop_pk == shop_pk]
    if filters.get("status") is not None:
        conditions.append(ChannelProduct.status == filters["status"])
    if filters.get("q"):
        pattern = f"%{filters['q']}%"
        conditions.append(
            or_(
                ChannelProduct.spu_id.ilike(pattern),
                ChannelProduct.title.ilike(pattern),
            )
        )
    if filters.get("has_orders"):
        conditions.append(_HAS_ORDERS)
    if filters.get("missing_manual_cost"):
        manual = select(ManualProductCost.id).where(
            ManualProductCost.spu_pk == ChannelProduct.id,
            ManualProductCost.valid_to.is_(None),
        )
        conditions.append(~exists(manual))
    clone.child.require_live()
    with owned_session(clone) as session:
        rows = session.execute(
            select(ChannelProduct.id).where(*conditions).order_by(ChannelProduct.id)
        ).all()
    clone.child.require_live()
    return [int(row[0]) for row in rows]


def other_shop_pk(identities: dict[int, tuple[int, str]], shop_pk: int) -> int:
    """The owned shop that is not the given one (identity, not index)."""
    shops = {identity[0] for identity in identities.values()}
    assert len(shops) == 2
    return next(shop for shop in shops if shop != shop_pk)


def sort_value(row: dict[str, Any], key: str) -> Any:
    """The value the server sorts on, for one seed row."""
    values = {
        "id": int(row["id"]),
        "status": _STATUS_WEIGHT.get(str(row["status"]), 3),
        "created_at": row["source_created_at"],
        "updated_at": row["source_updated_at"],
        "unit_cost": row["unit_cost"]
        if row["unit_cost"] is not None
        else row["source_unit_cost"],
    }
    if key not in values:
        raise ValueError(f"unsupported sort key {key!r}")
    return values[key]


def expected_order(rows: list[dict[str, Any]], key: str, order: str) -> list[int]:
    """Server order rebuilt from the known seed, not from the response."""
    assert order in {"asc", "desc"}
    items = [(sort_value(row, key), int(row["id"])) for row in rows]
    if key == "id":
        items.sort(key=lambda item: item[0], reverse=order == "desc")
        return [pk for _value, pk in items]
    # NULL costs and NULL timestamps stay at the tail in both directions; equal
    # values keep the ascending internal-key tiebreak.
    present = [item for item in items if item[0] is not None]
    missing = [item for item in items if item[0] is None]
    present.sort(key=lambda item: item[1])
    present.sort(key=lambda item: item[0], reverse=order == "desc")
    missing.sort(key=lambda item: item[1])
    return [pk for _value, pk in present + missing]


# --- owned facts ----------------------------------------------------------
@pytest.fixture
def alt_product(
    clone: OwnedClone, manual_cost_facts: tuple[str, tuple[int, ...], int]
) -> Iterator[tuple[int, str, str]]:
    """One extra owned product so the status filter and sort are decidable.

    The original fact fixture is used unchanged: it already owns two shops
    that share one external SPU id. This adds a single product with another
    status and another title, and removes exactly that row again.
    """
    marker, _spu_pks, shop_pk = manual_cost_facts
    spu_id = marker + "_alt"
    title = marker + "_alt_title"
    with owned_session(clone) as session:
        clone.child.require_live()
        product = ChannelProduct(
            shop_pk=shop_pk, spu_id=spu_id, title=title, status="DELETED"
        )
        session.add(product)
        session.flush()
        product_pk = int(product.id)
        session.commit()
    try:
        yield product_pk, spu_id, title
    finally:
        with owned_session(clone) as session:
            clone.child.require_live()
            owned_row = session.get(ChannelProduct, product_pk)
            assert owned_row is not None
            assert owned_row.shop_pk == shop_pk
            assert owned_row.spu_id == spu_id
            assert owned_row.title == title
            assert owned_row.status == "DELETED"
            session.execute(
                delete(ManualProductCost).where(ManualProductCost.spu_pk == product_pk)
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.id == product_pk)
            )
            clone.child.require_live()
            session.commit()
        with owned_session(clone) as session:
            clone.child.require_live()
            assert session.get(ChannelProduct, product_pk) is None
            remaining = session.scalar(
                select(ManualProductCost.id).where(
                    ManualProductCost.spu_pk == product_pk
                )
            )
            assert remaining is None


# --- tests ----------------------------------------------------------------
def test_catalogue_is_scoped_to_the_exact_shop_and_keeps_internal_identity(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
    alt_product: tuple[int, str, str],
) -> None:
    """One shop's catalogue, one external id shared by two shops, one 404."""
    marker, spu_pks, shop_pk = manual_cost_facts
    alt_pk, alt_spu_id, alt_title = alt_product
    identities = product_map(clone, spu_pks)
    assert len(identities) == 3
    shared_external = marker + "_shared"
    shared = {pk: v for pk, v in identities.items() if v[1] == shared_external}
    assert len(shared) == 2
    in_shop = [pk for pk, v in shared.items() if v[0] == shop_pk]
    out_shop = [pk for pk, v in shared.items() if v[0] != shop_pk]
    assert len(in_shop) == 1 and len(out_shop) == 1
    other_shop = other_shop_pk(identities, shop_pk)
    owned_ids = sorted(
        [pk for pk, v in identities.items() if v[0] == shop_pk] + [alt_pk]
    )

    with session_client(clone, "viewer") as client:
        scoped = catalogue(clone, client, shop_pk=shop_pk)
        payload = body(scoped)
        assert row_ids(payload) == owned_ids
        assert scoped.headers.get("X-Total-Count") == str(len(owned_ids))
        assert all(int(row["shop_pk"]) == shop_pk for row in payload)
        # The same external id in the other shop is never pulled in.
        assert out_shop[0] not in row_ids(payload)
        shared_row = next(row for row in payload if row["id"] == in_shop[0])
        assert shared_row["spu_id"] == shared_external
        assert int(shared_row["shop_pk"]) == shop_pk

        # The owned shop identity stays the exact internal key plus shop id.
        with owned_session(clone) as session:
            shop = session.get(ChannelAccount, shop_pk)
            other = session.get(ChannelAccount, other_shop)
            assert shop is not None and other is not None
            assert shop.platform == "tiktok" and shop.shop_id.startswith("TEST_")
            assert other.shop_id != shop.shop_id

        other_response = catalogue(clone, client, shop_pk=other_shop)
        other_payload = body(other_response)
        assert row_ids(other_payload) == sorted(out_shop)
        assert other_response.headers.get("X-Total-Count") == "1"
        assert other_payload[0]["spu_id"] == shared_external
        assert other_payload[0]["id"] != in_shop[0]

        # Unscoped search spans both shops and keeps two distinct identities.
        unscoped = catalogue(clone, client, q=shared_external)
        assert sorted(row_ids(body(unscoped))) == sorted([in_shop[0], out_shop[0]])
        assert unscoped.headers.get("X-Total-Count") == "2"

        # One product by its internal key, and an absent key.
        single = body(
            owned(clone, client.get(clone.prefix + f"{CATALOGUE}/{alt_pk}"), 200)
        )
        assert int(single["id"]) == alt_pk
        assert int(single["shop_pk"]) == shop_pk
        assert single["spu_id"] == alt_spu_id
        assert single["status"] == "DELETED"
        assert single["title"] == alt_title
        clone.child.require_live()
        with owned_session(clone) as session:
            highest = session.scalar(select(func.max(ChannelProduct.id)))
        clone.child.require_live()
        absent = int(highest or 0) + 1
        owned(clone, client.get(clone.prefix + f"{CATALOGUE}/{absent}"), 404)


def test_catalogue_filters_are_server_side_and_match_fresh_sql(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
    alt_product: tuple[int, str, str],
) -> None:
    """Status, text, order presence and cost presence filter the catalogue."""
    marker, spu_pks, shop_pk = manual_cost_facts
    alt_pk, alt_spu_id, alt_title = alt_product
    identities = product_map(clone, spu_pks)
    other_external = marker + "_other"
    in_shop_other = [pk for pk, v in identities.items() if v[1] == other_external]
    assert len(in_shop_other) == 1
    owned_ids = sorted(
        [pk for pk, v in identities.items() if v[0] == shop_pk] + [alt_pk]
    )

    with session_client(clone, "viewer") as client:
        active = catalogue(clone, client, shop_pk=shop_pk, status="ACTIVATE")
        assert row_ids(body(active)) == [pk for pk in owned_ids if pk != alt_pk]
        assert active.headers.get("X-Total-Count") == "2"

        removed = catalogue(clone, client, shop_pk=shop_pk, status="DELETED")
        assert row_ids(body(removed)) == [alt_pk]
        assert removed.headers.get("X-Total-Count") == "1"

        stopped = catalogue(clone, client, shop_pk=shop_pk, status="SELLER_DEACTIVATED")
        assert row_ids(body(stopped)) == []
        assert stopped.headers.get("X-Total-Count") == "0"

        # Text search reaches the external id and the title, case-insensitively.
        by_id = catalogue(clone, client, shop_pk=shop_pk, q=alt_spu_id)
        assert row_ids(body(by_id)) == [alt_pk]
        assert by_id.headers.get("X-Total-Count") == "1"
        by_other = catalogue(clone, client, shop_pk=shop_pk, q=other_external)
        assert row_ids(body(by_other)) == in_shop_other
        by_title = catalogue(clone, client, shop_pk=shop_pk, q=alt_title)
        assert row_ids(body(by_title)) == [alt_pk]
        upper = catalogue(clone, client, shop_pk=shop_pk, q=marker.upper())
        lower = catalogue(clone, client, shop_pk=shop_pk, q=marker)
        assert row_ids(body(upper)) == row_ids(body(lower))
        assert row_ids(body(lower)) == owned_ids
        absent_text = catalogue(clone, client, shop_pk=shop_pk, q=marker + "_absent")
        assert row_ids(body(absent_text)) == []
        assert absent_text.headers.get("X-Total-Count") == "0"

        # The order filter needs an owned order line; none is owned here, so
        # the server filter is proven by excluding every owned row.
        with_orders = catalogue(clone, client, shop_pk=shop_pk, has_orders=True)
        assert row_ids(body(with_orders)) == []
        assert with_orders.headers.get("X-Total-Count") == "0"
        without_orders = catalogue(clone, client, shop_pk=shop_pk, has_orders=False)
        assert row_ids(body(without_orders)) == owned_ids

        # Before any cost is filed every owned row still lacks one.
        without_cost = catalogue(
            clone, client, shop_pk=shop_pk, missing_manual_cost=True
        )
        assert row_ids(body(without_cost)) == owned_ids
        assert without_cost.headers.get("X-Total-Count") == str(len(owned_ids))

        # Fresh SQL recomputes the same filtered sets.
        assert expected_ids(clone, shop_pk=shop_pk, status="DELETED") == [alt_pk]
        assert expected_ids(clone, shop_pk=shop_pk, q=alt_spu_id) == [alt_pk]
        assert expected_ids(clone, shop_pk=shop_pk, has_orders=True) == []
        missing = expected_ids(clone, shop_pk=shop_pk, missing_manual_cost=True)
        assert missing == owned_ids
        outside = expected_ids(clone, shop_pk=other_shop_pk(identities, shop_pk))
        assert outside == sorted(pk for pk, v in identities.items() if v[0] != shop_pk)


def test_catalogue_sort_keys_and_paging_come_from_the_server(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
    alt_product: tuple[int, str, str],
) -> None:
    """Every allowlisted sort key and direction, plus stable paging."""
    _marker, spu_pks, shop_pk = manual_cost_facts
    alt_pk = alt_product[0]
    identities = product_map(clone, spu_pks)
    owned_ids = sorted(
        [pk for pk, v in identities.items() if v[0] == shop_pk] + [alt_pk]
    )
    seed = seed_rows(clone, shop_pk)
    assert sorted(int(row["id"]) for row in seed) == owned_ids

    with session_client(clone, "viewer") as client:
        for key in _SORT_KEYS:
            for order in ("asc", "desc"):
                try:
                    response = catalogue(
                        clone, client, shop_pk=shop_pk, sort=key, order=order
                    )
                except BaseException as exc:
                    # Name the exact combination; the original error is chained.
                    raise AssertionError(
                        f"catalogue sort request failed: sort={key} order={order}"
                    ) from exc
                assert row_ids(body(response)) == expected_order(seed, key, order)
                assert response.headers.get("X-Total-Count") == str(len(owned_ids))

        # Paging splits the filtered set without losing or duplicating a row.
        pages = []
        for offset in range(len(owned_ids) + 1):
            page = {"shop_pk": shop_pk, "sort": "id", "order": "asc"}
            page.update(limit=1, offset=offset)
            response = catalogue(clone, client, **page)
            assert response.headers.get("X-Total-Count") == str(len(owned_ids))
            pages.append(row_ids(body(response)))
        assert pages[-1] == []
        assert sorted(pk for page in pages for pk in page) == owned_ids
        assert len({pk for page in pages for pk in page}) == len(owned_ids)
        assert all(len(page) <= 1 for page in pages)


def test_one_canonical_write_is_read_back_with_exact_decimals(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
    alt_product: tuple[int, str, str],
) -> None:
    """A single filed cost shows up exactly, in the catalogue and the history."""
    marker, _spu_pks, shop_pk = manual_cost_facts
    alt_pk, alt_spu_id, _alt_title = alt_product
    amount = "87654321098765.4321"
    note = marker + "_cost"
    payload = {
        "spu_pk": alt_pk,
        "spu_id": alt_spu_id,
        "unit_cost": amount,
        "currency": "USD",
        "note": note,
    }
    with session_client(clone, "operator") as client:
        created = body(
            owned(
                clone,
                client.post(clone.prefix + HISTORY, json=payload, headers=CSRF),
                201,
            )
        )
        assert int(created["spu_pk"]) == alt_pk
        assert money(created["unit_cost"]) == Decimal(amount)
        assert created["currency"] == "USD"
        assert created["valid_to"] is None

        # The catalogue now carries the exact decimal for that SPU only.
        scoped = catalogue(clone, client, shop_pk=shop_pk)
        rows = {int(row["id"]): row for row in body(scoped)}
        assert money(rows[alt_pk]["unit_cost"]) == Decimal(amount)
        assert rows[alt_pk]["cost_method"] == "MANUAL_ENTRY"
        for pk, row in rows.items():
            if pk != alt_pk:
                assert row["unit_cost"] is None
                assert row["cost_method"] is None

        # A filed cost removes that SPU from the missing-cost filter.
        without_cost = catalogue(
            clone, client, shop_pk=shop_pk, missing_manual_cost=True
        )
        assert alt_pk not in row_ids(body(without_cost))
        assert without_cost.headers.get("X-Total-Count") == "2"

        # The cost sort now has one priced row and the unpriced tail.
        seed = seed_rows(clone, shop_pk)
        for order in ("asc", "desc"):
            response = catalogue(
                clone, client, shop_pk=shop_pk, sort="unit_cost", order=order
            )
            assert row_ids(body(response)) == expected_order(seed, "unit_cost", order)
            assert row_ids(body(response))[0] == alt_pk

        # The recent-submission list reads the same fact back.
        recent = history(clone, client, shop_pk=shop_pk, q=alt_spu_id)
        listed = body(recent)
        assert listed["total"] == 1
        item = listed["items"][0]
        assert int(item["id"]) == int(created["id"])
        assert int(item["spu_pk"]) == alt_pk
        assert item["spu_id"] == alt_spu_id
        assert int(item["shop_pk"]) == shop_pk
        assert money(item["unit_cost"]) == Decimal(amount)
        assert item["currency"] == "USD"
        assert item["prev_unit_cost"] is None
        assert item["prev_currency"] is None
        assert item["valid_to"] is None
        assert item["note"] == note
        assert item["valid_from"] is not None
        assert item["created_at"] is not None

        # Independent session: the stored row is the same exact decimal.
        stored = cost_history(clone, (alt_pk,))
        assert len(stored) == 1
        assert int(stored[0][1]) == alt_pk
        assert stored[0][2] == Decimal(amount)
        assert stored[0][3] == "USD"
        assert stored[0][5] is None
        assert stored[0][6] == note
        assert stored[0][4].utcoffset().total_seconds() == 0


def test_invalid_catalogue_filters_are_rejected_with_422(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
) -> None:
    """Only the documented invalid filters are rejected; neighbours still work."""
    _marker, _spu_pks, shop_pk = manual_cost_facts
    with session_client(clone, "viewer") as client:
        catalogue(clone, client, shop_pk=shop_pk, sort="not_a_column", expect=422)
        catalogue(
            clone,
            client,
            shop_pk=shop_pk,
            sort="unit_cost",
            order="sideways",
            expect=422,
        )
        catalogue(clone, client, shop_pk=shop_pk, limit=0, expect=422)
        catalogue(clone, client, shop_pk=shop_pk, limit=501, expect=422)
        catalogue(clone, client, shop_pk=shop_pk, offset=-1, expect=422)
        catalogue(clone, client, shop_pk=shop_pk, q="x" * 201, expect=422)

        # The valid neighbours of the rejected filters still answer.
        neighbour = catalogue(
            clone, client, shop_pk=shop_pk, sort="unit_cost", order="desc"
        )
        assert neighbour.status_code == 200
        assert catalogue(clone, client, shop_pk=shop_pk, limit=1).status_code == 200
        assert catalogue(clone, client, shop_pk=shop_pk, offset=0).status_code == 200

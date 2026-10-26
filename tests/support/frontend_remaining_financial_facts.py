"""The one owned fact set for the remaining financial reads.

The seed, the ownership intents, the intent-based recovery and the cleanup of
the advertising and profitability facts used by
``tests/scripts/test_frontend_remaining_financial_reads_live_api.py``. This
module owns the facts and nothing else: no database, session, proof, factory
or auth owner is created here, and the original ``clone``/``manual_cost_facts``
fixtures stay in the API test module, which imports this owner by name.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from tests.support.frontend_p2_binding import checked_session
from tts_erp_v2.db.models import (
    AdDaily,
    ChannelAccount,
    ChannelProduct,
    SalesOrder,
    SalesOrderLine,
    SyncJob,
)
from tts_erp_v2.db.models.fx import ExchangeRate, ExchangeRateSnapshot

if TYPE_CHECKING:
    from tests.scripts.test_frontend_remaining_live_api import OwnedClone

AD_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
SOURCE_JOBS = ("tiktok.orders", "tiktok.after_sales", "tiktok.finance")
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SPEND = Decimal("2.5000")
GMV = Decimal("10.0000")
ROW_ORDERS = 2
ROW_ROI = Decimal("4.0000")


@dataclass(frozen=True)
class Owned:
    """Ownership intents: enough to find and prove every row this file owns."""

    marker: str
    fx_upstream: datetime
    shop_ids: tuple[str, ...]
    spu_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    ad_scopes: tuple[tuple[str, str, str], ...]
    job_shop_ids: tuple[str, ...]
    zero_spu_id: str


@dataclass(frozen=True)
class Facts:
    """The committed facts, read back through the original session owner."""

    owned: Owned
    anchor: date
    original_marker: str
    original_shop_pk: int
    original_shop_id: str
    original_spu_ids: tuple[str, str]
    own_shop_pk: int
    noreg_shop_pk: int
    scope_pks: tuple[int, ...]
    fx_snapshot_id: int


def _intents(marker: str, original_marker: str) -> Owned:
    """Record the ownership intents before a single row is inserted."""
    own, noreg = marker + "_own_shop", marker + "_noreg_shop"
    zero = "TESTFINZERO" + uuid4().hex
    return Owned(
        marker=marker,
        fx_upstream=datetime.now(UTC) - timedelta(hours=2),
        shop_ids=(own, noreg),
        spu_ids=tuple(
            f"{marker}_{tag}_spu_{index}"
            for tag in ("own", "noreg")
            for index in range(5)
        ),
        order_ids=tuple(
            f"{marker}_{tag}_order_{index}"
            for tag in ("own", "noreg")
            for index in range(4)
        ),
        ad_scopes=(
            (original_marker + "_shop_a", marker + "_adv", marker + "_camp"),
            (original_marker + "_shop_a", zero + "_adv", zero + "_camp"),
            (own, marker + "_own_adv", marker + "_own_camp"),
            (noreg, marker + "_noreg_adv", marker + "_noreg_camp"),
        ),
        job_shop_ids=(own, noreg),
        zero_spu_id=zero,
    )


def _seed_shop(
    session: Session,
    *,
    shop_id: str,
    region: str | None,
    tag: str,
    marker: str,
    advertiser_id: str,
    campaign_id: str,
    anchor: date,
    started: datetime,
) -> None:
    """One owned shop with its products, paid orders, ad grid and source jobs."""
    shop = ChannelAccount(
        platform="tiktok",
        shop_id=shop_id,
        account_name=marker,
        region=region,
        status="active",
    )
    session.add(shop)
    session.flush()
    products = [
        ChannelProduct(
            shop_pk=shop.id,
            spu_id=f"{marker}_{tag}_spu_{index}",
            title=marker,
            status="ACTIVATE",
        )
        for index in range(5)
    ]
    session.add_all(products)
    session.flush()
    days = (
        (anchor - timedelta(days=20), 0, 15),
        (anchor - timedelta(days=8), 0, 15),
        (anchor, 0, 15),
        (anchor, 12, 0),
    )
    for number, (day, hour, minute) in enumerate(days):
        order = SalesOrder(
            shop_pk=shop.id,
            order_id=f"{marker}_{tag}_order_{number}",
            status="DELIVERED",
            currency="VND",
            order_time=datetime.combine(day, time(hour, minute), TZ),
            paid_at=datetime.combine(day, time(hour, minute), TZ),
        )
        session.add(order)
        session.flush()
        targets = products[:3] if number < 3 else products[4:5]
        for index, product in enumerate(targets):
            session.add(
                SalesOrderLine(
                    order_pk=order.id,
                    spu_pk=product.id,
                    external_line_id=f"{marker}_{tag}_order_{number}_line_{index}",
                    quantity=1,
                    unit_price=100000,
                    currency="VND",
                )
            )
    for ago in range(14):
        for product in products[:4]:
            session.add(
                AdDaily(
                    seller_id=shop.shop_id,
                    advertiser_id=advertiser_id,
                    campaign_id=campaign_id,
                    endpoint=AD_ENDPOINT,
                    day=anchor - timedelta(days=ago),
                    product_id=product.spu_id,
                    mixed_real_cost=SPEND,
                    onsite_roi2_shopping_sku=ROW_ORDERS,
                    onsite_roi2_shopping_value=GMV,
                    onsite_mixed_real_roi2_shopping=ROW_ROI,
                )
            )
    for name in SOURCE_JOBS:
        session.add(
            SyncJob(
                job_name=name,
                started_at=started,
                finished_at=started + timedelta(hours=2),
                status="succeeded",
                rows_failed=0,
                extra={"shop_id": shop.shop_id},
            )
        )


def _seed(
    clone: OwnedClone,
    owned: Owned,
    original_marker: str,
    anchor: date,
    *,
    fail_before_capture: bool = False,
) -> None:
    """Insert and commit every owned fact."""
    started = datetime.combine(anchor + timedelta(days=1), time(0, 5), TZ)
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        for index, (region, tag) in enumerate((("VN", "own"), (None, "noreg"))):
            _seed_shop(
                session,
                shop_id=owned.shop_ids[index],
                region=region,
                tag=tag,
                marker=owned.marker,
                advertiser_id=owned.marker + f"_{tag}_adv",
                campaign_id=owned.marker + f"_{tag}_camp",
                anchor=anchor,
                started=started,
            )
        # The advertising scope reuses the original fixture's shop and product
        # ids unchanged; only the daily rows themselves are owned here.
        for ago in range(14):
            for spu_id in (original_marker + "_shared", original_marker + "_other"):
                session.add(
                    AdDaily(
                        seller_id=original_marker + "_shop_a",
                        advertiser_id=owned.marker + "_adv",
                        campaign_id=owned.marker + "_camp",
                        endpoint=AD_ENDPOINT,
                        day=anchor - timedelta(days=ago),
                        product_id=spu_id,
                        mixed_real_cost=SPEND,
                        onsite_roi2_shopping_sku=ROW_ORDERS,
                        onsite_roi2_shopping_value=GMV,
                        onsite_mixed_real_roi2_shopping=ROW_ROI,
                    )
                )
        # One zero-spend row without a catalogue product, on its own campaign.
        seller_id, advertiser_id, campaign_id = owned.ad_scopes[1]
        session.add(
            AdDaily(
                seller_id=seller_id,
                advertiser_id=advertiser_id,
                campaign_id=campaign_id,
                endpoint=AD_ENDPOINT,
                day=anchor,
                product_id=owned.zero_spu_id,
                mixed_real_cost=Decimal(0),
                onsite_roi2_shopping_sku=None,
                onsite_roi2_shopping_value=None,
                onsite_mixed_real_roi2_shopping=None,
            )
        )
        snapshot_id = session.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, "
                "rates_count) VALUES ('USD', :upstream, :next, now(), 3) "
                "RETURNING id"
            ),
            {
                "upstream": owned.fx_upstream,
                "next": owned.fx_upstream + timedelta(days=1),
            },
        ).scalar_one()
        for code, rate in (("USD", 1), ("CNY", 7), ("VND", 25000)):
            session.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:snapshot, 'USD', :code, :rate)"
                ),
                {"snapshot": snapshot_id, "code": code, "rate": Decimal(rate)},
            )
        clone.child.require_live()
        session.commit()
        if fail_before_capture:
            raise AssertionError("EXPECTED_SETUP_FAILURE")


def _owned_candidates(clone: OwnedClone, owned: Owned) -> dict[str, list[tuple]]:
    """Every row that matches an ownership intent, read by the original owner."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()

        def rows(statement: Any) -> list[tuple]:
            return [tuple(row) for row in session.execute(statement)]

        shops = rows(
            select(
                ChannelAccount.id, ChannelAccount.shop_id, ChannelAccount.account_name
            )
            .where(
                ChannelAccount.platform == "tiktok",
                ChannelAccount.shop_id.in_(owned.shop_ids),
                ChannelAccount.account_name == owned.marker,
            )
            .order_by(ChannelAccount.id)
        )
        shop_pks = [row[0] for row in shops]
        products = rows(
            select(
                ChannelProduct.id,
                ChannelProduct.shop_pk,
                ChannelProduct.spu_id,
                ChannelProduct.title,
            )
            .where(
                ChannelProduct.shop_pk.in_(shop_pks),
                ChannelProduct.spu_id.in_(owned.spu_ids),
                ChannelProduct.title == owned.marker,
            )
            .order_by(ChannelProduct.id)
        )
        orders = rows(
            select(SalesOrder.id, SalesOrder.shop_pk, SalesOrder.order_id)
            .where(
                SalesOrder.shop_pk.in_(shop_pks),
                SalesOrder.order_id.in_(owned.order_ids),
            )
            .order_by(SalesOrder.id)
        )
        lines = rows(
            select(SalesOrderLine.id, SalesOrderLine.order_pk).where(
                SalesOrderLine.order_pk.in_([row[0] for row in orders])
            )
        )
        ads: list[tuple] = []
        for seller_id, advertiser_id, campaign_id in owned.ad_scopes:
            ads.extend(
                rows(
                    select(
                        AdDaily.id,
                        AdDaily.seller_id,
                        AdDaily.advertiser_id,
                        AdDaily.campaign_id,
                    ).where(
                        AdDaily.seller_id == seller_id,
                        AdDaily.advertiser_id == advertiser_id,
                        AdDaily.campaign_id == campaign_id,
                    )
                )
            )
        jobs = rows(
            select(SyncJob.id, SyncJob.job_name).where(
                SyncJob.extra["shop_id"].astext.in_(owned.job_shop_ids),
                SyncJob.job_name.in_(SOURCE_JOBS),
            )
        )
        snapshots = rows(
            select(ExchangeRateSnapshot.id, ExchangeRateSnapshot.base_code).where(
                ExchangeRateSnapshot.base_code == "USD",
                ExchangeRateSnapshot.upstream_last_update == owned.fx_upstream,
            )
        )
        rates = rows(
            select(ExchangeRate.id, ExchangeRate.snapshot_id).where(
                ExchangeRate.snapshot_id.in_([row[0] for row in snapshots])
            )
        )
        return {
            "shops": shops,
            "products": products,
            "orders": orders,
            "lines": lines,
            "ad_daily": ads,
            "sync_jobs": jobs,
            "fx_snapshots": snapshots,
            "fx_rates": rates,
        }


def _verify_ownership(owned: Owned, state: dict[str, list[tuple]]) -> None:
    """Every candidate row must carry this file's own marker."""
    for row in state["shops"]:
        assert row[2] == owned.marker and row[1] in owned.shop_ids
    for row in state["products"]:
        assert row[3] == owned.marker and row[2] in owned.spu_ids
    for row in state["orders"]:
        assert row[2] in owned.order_ids
    for row in state["ad_daily"]:
        assert (row[1], row[2], row[3]) in owned.ad_scopes
    for row in state["sync_jobs"]:
        assert row[1] in SOURCE_JOBS
    for row in state["fx_snapshots"]:
        assert row[1] == "USD"


def _delete_owned(session: Session, ids: dict[str, list[int]]) -> None:
    """Delete the given rows in foreign-key order."""
    for model, key in (
        (ExchangeRate, "fx_rates"),
        (ExchangeRateSnapshot, "fx_snapshots"),
        (SalesOrderLine, "lines"),
        (SalesOrder, "orders"),
        (AdDaily, "ad_daily"),
        (SyncJob, "sync_jobs"),
        (ChannelProduct, "products"),
        (ChannelAccount, "shops"),
    ):
        session.execute(delete(model).where(model.id.in_(ids.get(key, []))))


def _cleanup(clone: OwnedClone, owned: Owned) -> None:
    """Remove every owned row: recover the ids by intent, verify, delete."""
    state = _owned_candidates(clone, owned)
    _verify_ownership(owned, state)
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        _delete_owned(
            session, {name: [row[0] for row in rows] for name, rows in state.items()}
        )
        clone.child.require_live()
        session.commit()
    leftover = sorted(
        name for name, rows in _owned_candidates(clone, owned).items() if rows
    )
    assert not leftover, f"owned rows survived the cleanup: {leftover}"


def _facts(
    clone: OwnedClone,
    owned: Owned,
    original: tuple[str, tuple[int, ...], int],
    anchor: date,
) -> Facts:
    """Read the committed facts back by intent (the id capture step)."""
    state = _owned_candidates(clone, owned)
    _verify_ownership(owned, state)
    shops = {row[1]: row[0] for row in state["shops"]}
    products = [row for row in state["products"] if row[1] == shops[owned.shop_ids[0]]]
    return Facts(
        owned=owned,
        anchor=anchor,
        original_marker=original[0],
        original_shop_pk=original[2],
        original_shop_id=original[0] + "_shop_a",
        original_spu_ids=(original[0] + "_shared", original[0] + "_other"),
        own_shop_pk=shops[owned.shop_ids[0]],
        noreg_shop_pk=shops[owned.shop_ids[1]],
        scope_pks=tuple(row[0] for row in products[:4]),
        fx_snapshot_id=state["fx_snapshots"][0][0],
    )


@contextmanager
def _seeded_facts(
    clone: OwnedClone,
    owned_intents: Owned,
    original: tuple[str, tuple[int, ...], int],
    *,
    fail_before_capture: bool = False,
) -> Iterator[Facts]:
    """Seed the owned facts and remove them again when the block ends."""
    anchor = datetime.now(TZ).date() - timedelta(days=4)
    try:
        _seed(
            clone,
            owned_intents,
            original[0],
            anchor,
            fail_before_capture=fail_before_capture,
        )
        yield _facts(clone, owned_intents, original, anchor)
    finally:
        _cleanup(clone, owned_intents)


@pytest.fixture
def financial_facts(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
) -> Iterator[Facts]:
    """The owned facts, live for one test and removed again afterwards."""
    owned_intents = _intents("TEST_financial_" + uuid4().hex, manual_cost_facts[0])
    with _seeded_facts(clone, owned_intents, manual_cost_facts) as facts:
        yield facts


def _set_region(clone: OwnedClone, shop_pk: int, marker: str, region: str) -> None:
    """Give an owned shop its reporting region (this file's own row)."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        owned_row = session.scalar(
            select(ChannelAccount.id).where(
                ChannelAccount.id == shop_pk,
                ChannelAccount.account_name == marker,
            )
        )
        assert owned_row == shop_pk
        session.execute(
            update(ChannelAccount)
            .where(ChannelAccount.id == shop_pk)
            .values(region=region)
        )
        clone.child.require_live()
        session.commit()

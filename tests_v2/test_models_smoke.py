"""Smoke tests: every tts_erp_v2 table accepts insert + select.

Strategy: insert one minimal row per table via the ORM, then read it back
to confirm column names + types behave as the schema spec requires.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session


pytestmark = [pytest.mark.domain_models, pytest.mark.layer_integration]

from tts_erp_v2.db.models import (
    ApiKey,
    AccountLink,
    Case as AfterSalesCase, CaseLine as AfterSalesCaseLine,
    ChannelAccount, ChannelProduct, ChannelProductVariant,
    Credentials,
    LinkEvidence, LinkIssue, LinkOverride,
    ManualProductCost,
    Payout,
    ProductCostSnapshot, ProductProfitDaily,
    ProcurementAccount, ProcurementProduct, ProcurementProductVariant,
    ProductLink,
    PurchaseOrder, PurchaseOrderLine,
    RawRecord,
    SalesOrder, SalesOrderLine,
    SettlementComponent, SettlementStatement, SettlementTransaction,
    Shipment, ShipmentLine, TrackingEvent,
    SyncCursor, SyncIssue, SyncJob,
    VariantLink,
)


# ── fixtures shared across the smoke matrix ─────────────────────────

@pytest.fixture()
def credentials_row(db_session: Session) -> Credentials:
    c = Credentials(
        provider="tiktok",
        external_account_id="TEST_creds_1",
        ciphertext=b"x" * 16,
    )
    db_session.add(c)
    db_session.flush()
    return c


@pytest.fixture()
def raw_record_row(db_session: Session, credentials_row: Credentials) -> RawRecord:
    rr = RawRecord(
        credential_id=credentials_row.id,
        endpoint="tiktok.test",
        external_id="TEST_rr_1",
        payload={"hello": "world"},
    )
    db_session.add(rr)
    db_session.flush()
    return rr


@pytest.fixture()
def channel_account_row(db_session: Session, credentials_row: Credentials) -> ChannelAccount:
    a = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_acct_1",
        credential_id=credentials_row.id,
    )
    db_session.add(a)
    db_session.flush()
    return a


@pytest.fixture()
def procurement_account_row(db_session: Session, credentials_row: Credentials) -> ProcurementAccount:
    p = ProcurementAccount(
        provider="miaoshou",
        external_account_id="TEST_lic_1",
        credential_id=credentials_row.id,
    )
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture()
def channel_product_row(db_session: Session, channel_account_row: ChannelAccount) -> ChannelProduct:
    p = ChannelProduct(
        channel_account_id=channel_account_row.id,
        external_product_id="TEST_prod_1",
    )
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture()
def procurement_product_row(
    db_session: Session, procurement_account_row: ProcurementAccount
) -> ProcurementProduct:
    p = ProcurementProduct(
        procurement_account_id=procurement_account_row.id,
        external_product_id="TEST_pprod_1",
    )
    db_session.add(p)
    db_session.flush()
    return p


# ── per-table smoke inserts ────────────────────────────────────────

def test_credentials_insert_select(db_session: Session) -> None:
    c = Credentials(
        provider="tiktok",
        external_account_id="TEST_cred_2",
        ciphertext=b"\x00\x01\x02",
        granted_scopes=["orders.read", "products.read"],
    )
    db_session.add(c)
    db_session.flush()

    found = db_session.execute(
        select(Credentials).where(Credentials.external_account_id == "TEST_cred_2")
    ).scalar_one()
    assert found.provider == "tiktok"
    assert found.ciphertext == b"\x00\x01\x02"
    assert found.granted_scopes == ["orders.read", "products.read"]
    assert isinstance(found.created_at, datetime)
    # timestamptz check — should be tz-aware
    assert found.created_at.tzinfo is not None, "created_at must be timestamptz (V3 §14)"


def test_raw_records_insert_select(db_session: Session, credentials_row: Credentials) -> None:
    rr = RawRecord(
        credential_id=credentials_row.id,
        endpoint="tiktok.order.search",
        external_id="TEST_order_42",
        payload={"list": [{"id": 1}, {"id": 2}], "total": 2},
    )
    db_session.add(rr)
    db_session.flush()

    found = db_session.execute(
        select(RawRecord).where(RawRecord.external_id == "TEST_order_42")
    ).scalar_one()
    assert found.endpoint == "tiktok.order.search"
    assert found.payload == {"list": [{"id": 1}, {"id": 2}], "total": 2}


def test_sync_jobs_lifecycle(db_session: Session) -> None:
    j = SyncJob(
        job_name="tiktok.orders",
        status="running",
        rows_total=0,
    )
    db_session.add(j)
    db_session.flush()

    # simulate completion
    j.status = "succeeded"
    j.rows_inserted = 42
    j.finished_at = datetime.now(timezone.utc)
    db_session.flush()

    found = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "tiktok.orders")
    ).scalar_one()
    assert found.status == "succeeded"
    assert found.rows_inserted == 42


def test_sync_cursors_unique(db_session: Session) -> None:
    a = SyncCursor(job_name="tiktok.orders", scope="TEST_acct")
    db_session.add(a)
    db_session.flush()
    b = SyncCursor(job_name="tiktok.orders", scope="TEST_acct")
    db_session.add(b)
    import sqlalchemy.exc
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_sync_issues_basic(db_session: Session) -> None:
    i = SyncIssue(
        job_name="tiktok.orders",
        issue_type="PRODUCT_LINK_MISSING",
        external_id="TEST_pid",
    )
    db_session.add(i)
    db_session.flush()
    assert i.id is not None


def test_channel_account_credential_fk(
    db_session: Session, credentials_row: Credentials
) -> None:
    a = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_acct_3",
        credential_id=credentials_row.id,
    )
    db_session.add(a)
    db_session.flush()
    assert a.credential_id == credentials_row.id


def test_channel_products_unique_per_account(
    db_session: Session, channel_account_row: ChannelAccount
) -> None:
    a = ChannelProduct(
        channel_account_id=channel_account_row.id,
        external_product_id="TEST_prod_dup",
    )
    b = ChannelProduct(
        channel_account_id=channel_account_row.id,
        external_product_id="TEST_prod_dup",
    )
    db_session.add_all([a, b])
    import sqlalchemy.exc
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_channel_variants_attributes_jsonb(
    db_session: Session, channel_product_row: ChannelProduct
) -> None:
    v = ChannelProductVariant(
        channel_product_id=channel_product_row.id,
        external_variant_id="TEST_var_1",
        attributes={"color": "red", "size": "L"},
    )
    db_session.add(v)
    db_session.flush()
    assert v.attributes == {"color": "red", "size": "L"}


def test_sales_order_and_line_with_snapshots(
    db_session: Session, channel_account_row: ChannelAccount, channel_product_row: ChannelProduct
) -> None:
    so = SalesOrder(
        channel_account_id=channel_account_row.id,
        external_order_id="TEST_ord_1",
        currency="VND",
        payment_amount=100000.0,
    )
    db_session.add(so)
    db_session.flush()

    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="TEST_line_1",
        channel_product_id=channel_product_row.id,
        external_product_id_snapshot=channel_product_row.external_product_id,
        product_name_snapshot="TEST widget",
        quantity=1,
        unit_price=100000.0,
        currency="VND",
    )
    db_session.add(sol)
    db_session.flush()

    assert sol.channel_product_id == channel_product_row.id
    assert sol.product_name_snapshot == "TEST widget"


def test_procurement_accounts(db_session: Session, credentials_row: Credentials) -> None:
    p = ProcurementAccount(
        provider="miaoshou",
        external_account_id="TEST_lic_2",
        credential_id=credentials_row.id,
    )
    db_session.add(p)
    db_session.flush()
    assert p.id is not None


def test_procurement_products(db_session: Session, procurement_account_row: ProcurementAccount) -> None:
    p = ProcurementProduct(
        procurement_account_id=procurement_account_row.id,
        external_product_id="TEST_pp_1",
        product_type="SPU",
        title="TEST widget",
    )
    db_session.add(p)
    db_session.flush()
    assert p.product_type == "SPU"


def test_procurement_variants_empty_in_practice(
    db_session: Session, procurement_product_row: ProcurementProduct
) -> None:
    """Miaoshou SKU rows stay empty unless explicitly populated."""
    v = ProcurementProductVariant(
        procurement_product_id=procurement_product_row.id,
        external_variant_id="TEST_pvar_1",
    )
    db_session.add(v)
    db_session.flush()
    assert v.id is not None


def test_purchase_orders_and_lines(
    db_session: Session,
    procurement_account_row: ProcurementAccount,
    procurement_product_row: ProcurementProduct,
) -> None:
    po = PurchaseOrder(
        procurement_account_id=procurement_account_row.id,
        external_purchase_order_id="TEST_po_1",
        currency="CNY",
        total_amount=500.0,
    )
    db_session.add(po)
    db_session.flush()

    pol = PurchaseOrderLine(
        purchase_order_id=po.id,
        external_line_id="TEST_pol_1",
        procurement_product_id=procurement_product_row.id,
        quantity=10,
        unit_cost=50.0,
        currency="CNY",
    )
    db_session.add(pol)
    db_session.flush()
    assert pol.unit_cost == 50.0


def test_manual_product_costs(
    db_session: Session, channel_product_row: ChannelProduct
) -> None:
    m = ManualProductCost(
        channel_product_id=channel_product_row.id,
        unit_cost=42.5,
        currency="CNY",
        note="TEST entry",
        created_by="op1",
    )
    db_session.add(m)
    db_session.flush()
    assert m.unit_cost == 42.5
    assert m.created_by == "op1"
    # valid_from defaults to now()
    assert isinstance(m.valid_from, datetime)


def test_shipment_and_lines_and_tracking(
    db_session: Session,
    channel_account_row: ChannelAccount,
    channel_product_row: ChannelProduct,
) -> None:
    so = SalesOrder(
        channel_account_id=channel_account_row.id,
        external_order_id="TEST_ord_ship",
    )
    db_session.add(so)
    db_session.flush()

    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="TEST_sol_ship",
        channel_product_id=channel_product_row.id,
    )
    db_session.add(sol)
    db_session.flush()

    sh = Shipment(
        sales_order_id=so.id,
        external_package_id="TEST_pkg_1",
        tracking_number="TEST_tracking_1",
        status="shipped",
    )
    db_session.add(sh)
    db_session.flush()

    sl = ShipmentLine(
        shipment_id=sh.id,
        sales_order_line_id=sol.id,
        quantity=1,
    )
    db_session.add(sl)
    db_session.flush()

    te = TrackingEvent(
        shipment_id=sh.id,
        external_event_key="TEST_evt_1",
        action_code=10,
        event_at=datetime.now(timezone.utc),
        description="Picked up",
        location="HCM",
    )
    db_session.add(te)
    db_session.flush()
    assert te.id is not None


def test_after_sales_cases_and_lines(
    db_session: Session,
    channel_account_row: ChannelAccount,
    channel_product_row: ChannelProduct,
) -> None:
    so = SalesOrder(
        channel_account_id=channel_account_row.id,
        external_order_id="TEST_ord_case",
    )
    db_session.add(so)
    db_session.flush()

    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="TEST_sol_case",
        channel_product_id=channel_product_row.id,
    )
    db_session.add(sol)
    db_session.flush()

    case = AfterSalesCase(
        channel_account_id=channel_account_row.id,
        sales_order_id=so.id,
        external_case_id="TEST_case_1",
        case_type="REFUND_ONLY",
        status="pending",
    )
    db_session.add(case)
    db_session.flush()

    cl = AfterSalesCaseLine(
        case_id=case.id,
        sales_order_line_id=sol.id,
        external_case_line_id="TEST_cl_1",
        quantity=1,
        refund_amount=50.0,
        currency="VND",
    )
    db_session.add(cl)
    db_session.flush()
    assert cl.refund_amount == 50.0


def test_finance_chain(
    db_session: Session,
    channel_account_row: ChannelAccount,
    channel_product_row: ChannelProduct,
) -> None:
    so = SalesOrder(
        channel_account_id=channel_account_row.id,
        external_order_id="TEST_ord_fin",
    )
    db_session.add(so)
    db_session.flush()

    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="TEST_sol_fin",
        channel_product_id=channel_product_row.id,
    )
    db_session.add(sol)
    db_session.flush()

    pay = Payout(
        channel_account_id=channel_account_row.id,
        external_payout_id="TEST_pay_1",
        amount=1000.0,
        currency="VND",
    )
    db_session.add(pay)
    db_session.flush()

    ss = SettlementStatement(
        payout_id=pay.id,
        external_statement_id="TEST_stmt_1",
        currency="VND",
    )
    db_session.add(ss)
    db_session.flush()

    st = SettlementTransaction(
        settlement_statement_id=ss.id,
        external_transaction_id="TEST_st_1",
        sales_order_id=so.id,
        sales_order_line_id=sol.id,
    )
    db_session.add(st)
    db_session.flush()

    sc = SettlementComponent(
        transaction_id=st.id,
        component_code="GROSS_SALES",
        amount=100.0,
        currency="VND",
    )
    db_session.add(sc)
    db_session.flush()
    assert sc.amount == 100.0

    # unique (transaction, code)
    sc2 = SettlementComponent(
        transaction_id=st.id,
        component_code="GROSS_SALES",
        amount=200.0,
        currency="VND",
    )
    db_session.add(sc2)
    import sqlalchemy.exc
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_linkage_chain(
    db_session: Session,
    procurement_account_row: ProcurementAccount,
    channel_account_row: ChannelAccount,
    procurement_product_row: ProcurementProduct,
    channel_product_row: ChannelProduct,
) -> None:
    al = AccountLink(
        procurement_account_id=procurement_account_row.id,
        channel_account_id=channel_account_row.id,
    )
    db_session.add(al)
    db_session.flush()

    pl = ProductLink(
        procurement_product_id=procurement_product_row.id,
        channel_product_id=channel_product_row.id,
        relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
        is_primary=True,
    )
    db_session.add(pl)
    db_session.flush()
    assert pl.is_primary is True

    le = LinkEvidence(
        product_link_id=pl.id,
        evidence_type="MOVE_COLLECT_TASK",
        evidence_payload={"task_id": "TEST_t_1"},
    )
    db_session.add(le)
    db_session.flush()

    lo = LinkOverride(
        procurement_product_id=procurement_product_row.id,
        channel_product_id=channel_product_row.id,
        decision="PRIMARY",
        created_by="op1",
    )
    db_session.add(lo)
    db_session.flush()
    assert lo.decision == "PRIMARY"


def test_product_links_unique_with_valid_from(
    db_session: Session,
    procurement_product_row: ProcurementProduct,
    channel_product_row: ChannelProduct,
) -> None:
    """Per refactor-tech-plan-v2 §3.2: UNIQUE(procurement, channel, valid_from)
    so historical versions don't collide.
    """
    from datetime import timedelta

    pl1 = ProductLink(
        procurement_product_id=procurement_product_row.id,
        channel_product_id=channel_product_row.id,
        relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    pl2 = ProductLink(
        procurement_product_id=procurement_product_row.id,
        channel_product_id=channel_product_row.id,
        relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
        valid_from=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([pl1, pl2])
    db_session.flush()
    assert pl1.id != pl2.id
    assert pl1.valid_from != pl2.valid_from


def test_variant_links(db_session: Session) -> None:
    """variant_links table structure exists; rows are typically empty."""
    # Empty placeholder — variant_links row requires both Miaoshou + TikTok
    # variants; we don't insert here, just verify the table exists.
    insp = inspect(db_session.get_bind())
    cols = {c["name"] for c in insp.get_columns("variant_links", schema="linkage")}
    assert "procurement_product_variant_id" in cols
    assert "channel_product_variant_id" in cols


def test_link_issues_record(
    db_session: Session, channel_product_row: ChannelProduct
) -> None:
    li = LinkIssue(
        issue_type="AMBIGUOUS_SOURCE",
        channel_product_id=channel_product_row.id,
        candidate_count=3,
    )
    db_session.add(li)
    db_session.flush()
    assert li.candidate_count == 3


def test_reporting_cost_snapshot(
    db_session: Session, channel_product_row: ChannelProduct
) -> None:
    snap = ProductCostSnapshot(
        channel_product_id=channel_product_row.id,
        cost_method="MANUAL_ENTRY",
        unit_cost=42.5,
        currency="CNY",
        calculation_version=1,
    )
    db_session.add(snap)
    db_session.flush()
    assert snap.cost_method == "MANUAL_ENTRY"


def test_reporting_profit_daily(
    db_session: Session, channel_product_row: ChannelProduct
) -> None:
    from datetime import date
    p = ProductProfitDaily(
        channel_product_id=channel_product_row.id,
        profit_date=date(2024, 8, 29),
        units_sold=10,
        gross_revenue=1000.0,
        estimated_cogs=500.0,
        estimated_gross_profit=500.0,
        currency="VND",
        cost_method="MANUAL_ENTRY",
    )
    db_session.add(p)
    db_session.flush()
    assert p.profit_date == date(2024, 8, 29)


def test_api_keys_hashed(db_session: Session) -> None:
    """Key material must be SHA-256 hex only; plaintext shown ONCE at creation."""
    import hashlib
    plaintext = "ttserp_rw_smoke_test_xyz"
    k = ApiKey(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        key_prefix="ttserp_rw_smoke_",
        name="smoke test",
        role="readwrite",
        status="active",
    )
    db_session.add(k)
    db_session.flush()
    assert k.key_hash != plaintext
    assert len(k.key_hash) == 64  # sha256 hex


def test_effective_product_links_view_consultable(db_engine) -> None:
    """The hand-written VIEW must be present and queryable.

    Empty data is fine — we just confirm the view exists and parses.
    """
    from sqlalchemy import text
    with db_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='linkage' AND table_name='effective_product_links'"
        )).fetchall()
    cols = {r[0] for r in rows}
    assert "channel_product_id" in cols
    assert "procurement_product_id" in cols
    assert "effective_relation_type" in cols

"""TDD tests for jobs.tiktok.spu_link — order line → products_spu linkage.

Background (2026-09-06): ``commerce.sales_order_lines.spu_pk`` was only
ever backfilled once (2026-08-31); the orders / order_detail jobs write
lines with ``spu_pk`` left NULL ("later join" comment in orders.py), and
no job performed that join afterwards — every line synced after 08-31
stayed unattributed and dropped out of SPU-level reports (ROI board
order count / GMV / sales).

This module provides two convergent mechanisms:

* ``link_line_spu_pk`` — orders / order_detail resolve the catalog at
  write time (cheap per-shop preload); a line whose product snapshot is
  already in ``products_spu`` gets ``spu_pk`` immediately.
* ``backfill_null_line_spu_pk`` — the products job runs this after each
  sync so lines that landed *before* their product was synced get linked
  once the catalog catches up; the oneoff script uses the same routine
  for the historical 2026-08-31+ NULL population.

Both are idempotent and shop-scoped (a line links only to a product of
its own shop — product ids are NOT trusted to be globally unique).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.jobs.tiktok import spu_link

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]


def _make_shop(session: Session, external_id: str) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        shop_id=external_id,
        credential_id=cred.id,
        status="active",
        data_source="api",
    )
    session.add(acct)
    session.flush()
    return acct


def _seed_product(session: Session, shop_pk: int, spu_id: str) -> int:
    row = ChannelProduct(shop_pk=shop_pk, spu_id=spu_id, title=f"TEST {spu_id}")
    session.add(row)
    session.flush()
    return row.id


def _seed_order_line(
    session: Session,
    shop_pk: int,
    order_id: str,
    line_ext: str,
    snapshot_pid: str,
) -> None:
    order = SalesOrder(
        shop_pk=shop_pk,
        order_id=order_id,
        status="DELIVERED",
        currency="VND",
    )
    session.add(order)
    session.flush()
    session.add(
        SalesOrderLine(
            order_pk=order.id,
            external_line_id=line_ext,
            external_product_id_snapshot=snapshot_pid,
            quantity=1,
            unit_price=100,
            currency="VND",
        )
    )
    session.flush()


def test_spu_map_loads_only_that_shops_products(db_session) -> None:
    s1 = _make_shop(db_session, "TEST_SPULINK_SHOP_A")
    s2 = _make_shop(db_session, "TEST_SPULINK_SHOP_B")
    p1 = _seed_product(db_session, s1.id, "PID_SHARED")
    _seed_product(db_session, s2.id, "PID_SHARED")  # same external id, other shop
    m = spu_link.spu_map_for_shop(db_session, shop_pk=s1.id)
    assert m == {"PID_SHARED": p1}


def test_link_line_spu_pk_assigns_when_catalog_known(db_session) -> None:
    shop = _make_shop(db_session, "TEST_SPULINK_SHOP_C")
    pid = _seed_product(db_session, shop.id, "PID_C1")
    m = spu_link.spu_map_for_shop(db_session, shop_pk=shop.id)
    assert spu_link.link_line_spu_pk(m, product_snapshot="PID_C1") == pid


def test_link_line_spu_pk_unknown_product_is_none(db_session) -> None:
    shop = _make_shop(db_session, "TEST_SPULINK_SHOP_D")
    m = spu_link.spu_map_for_shop(db_session, shop_pk=shop.id)
    assert spu_link.link_line_spu_pk(m, product_snapshot="PID_MISSING") is None
    assert spu_link.link_line_spu_pk(m, product_snapshot=None) is None


def test_backfill_links_null_lines_only_and_shop_scoped(db_session) -> None:
    s1 = _make_shop(db_session, "TEST_SPULINK_SHOP_E")
    s2 = _make_shop(db_session, "TEST_SPULINK_SHOP_F")
    pid1 = _seed_product(db_session, s1.id, "PID_E1")
    # s1: 一条可关联的 NULL 行 + 一条已关联行(不动)
    _seed_order_line(db_session, s1.id, "ORD_E1", "L_E1", "PID_E1")
    _seed_order_line(db_session, s1.id, "ORD_E2", "L_E2", "PID_E1")
    db_session.execute(
        SalesOrderLine.__table__.update()
        .where(SalesOrderLine.external_line_id == "L_E2")
        .values(spu_pk=pid1)
    )
    db_session.flush()
    # s2: 同名 product id 的行必须保持 NULL(跨店不误连)
    _seed_order_line(db_session, s2.id, "ORD_F1", "L_F1", "PID_E1")

    n = spu_link.backfill_null_line_spu_pk(db_session, shop_pk=s1.id)
    db_session.flush()

    assert n == 1
    lines = (
        db_session.execute(
            select(SalesOrderLine).where(
                SalesOrderLine.external_line_id.in_(["L_E1", "L_E2", "L_F1"])
            )
        )
        .scalars()
        .all()
    )
    by_ext = {ln.external_line_id: ln for ln in lines}
    assert by_ext["L_E1"].spu_pk == pid1  # 回填
    assert by_ext["L_E2"].spu_pk == pid1  # 已关联行未被清空
    assert by_ext["L_F1"].spu_pk is None  # 跨店不误连
    # 幂等: 再跑一遍 0 行
    assert spu_link.backfill_null_line_spu_pk(db_session, shop_pk=s1.id) == 0


def test_backfill_all_shops_when_shop_none(db_session) -> None:
    """shop_pk=None → 全库(oneoff 路径)。

    共享 dev DB 里存在真实 NULL 行,所以这里不断言精确计数,
    只验证 (a) 不报错、(b) 本测试新增的 TEST 行确实被关联。
    (外层事务回滚,不会真改到库里的真实行。)
    """
    s1 = _make_shop(db_session, "TEST_SPULINK_SHOP_G")
    _seed_product(db_session, s1.id, "PID_G1")
    _seed_order_line(db_session, s1.id, "ORD_G1", "L_G1", "PID_G1")
    n = spu_link.backfill_null_line_spu_pk(db_session)
    assert n >= 1
    line = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.external_line_id == "L_G1")
    ).scalar_one()
    assert line.spu_pk is not None

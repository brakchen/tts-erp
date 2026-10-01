"""SPU ROI E2E 测试数据 seed / cleanup helper.

所有 seed 函数写入 TEST_* 前缀数据，适合隔离测试库。
用法：由 tests/e2e_browser/ 中的 pytest 编排层调用。

日期策略：所有 seed 数据使用 VN 时区 T-1（昨天）的日期，
与页面默认筛选范围保持一致，确保 seed 数据在页面加载后可见。
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Engine

# ── Dynamic date (VN T-1) ─────────────────────────────────────────
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_TODAY_VN = _dt.datetime.now(_VN_TZ).date()
_TARGET_DATE = _TODAY_VN - _dt.timedelta(days=1)  # T-1
TARGET_DATE_STR = _TARGET_DATE.isoformat()  # e.g. "2026-10-01"
FX_SEED_TS = f"{TARGET_DATE_STR}T00:00:00+00:00"
AD_DAY_STR = TARGET_DATE_STR  # plugin.ad_daily.day
ORDER_DAY_STR = TARGET_DATE_STR  # order_time / paid_at
SETTLEMENT_DAY_STR = str(_TARGET_DATE + _dt.timedelta(days=14))  # settlement ~2 weeks later

# ── Constants ──────────────────────────────────────────────────────
SHOP_ID = "TEST_E2E_VN_SHOP"
SHOP_NAME = "TEST E2E VN Shop"
SHOP_REGION = "VN"
SHOP2_ID = "TEST_E2E_VN_SHOP2"
SHOP2_NAME = "TEST E2E VN Shop 2"
REPORTING_TZ = "Asia/Ho_Chi_Minh"

FX_USD_CNY = "6.7686473"
FX_USD_VND = "26330"
FX_CNY_VND = format(Decimal(FX_USD_VND) / Decimal(FX_USD_CNY), "f")

K1_CNY = Decimal(40)

SPU_PREFIX = "TEST_E2E_SPU_"
ORDER_PREFIX = "TEST_E2E_ORD_"
CASE_PREFIX = "TEST_E2E_CASE_"
TXN_PREFIX = "TEST_E2E_TXN_"
ADV_PREFIX = "TEST_E2E_ADV_"
CAMPAIGN_PREFIX = "TEST_E2E_CAMP_"
SHOP2_SPU_PREFIX = "TEST_E2E_S2_SPU_"
SHOP2_ORDER_PREFIX = "TEST_E2E_S2_ORD_"


def cleanup(engine: Engine) -> None:
    """删除所有 TEST_E2E_* 前缀数据（按 FK 依赖顺序）。"""
    with engine.begin() as c:
        for sql in [
            "DELETE FROM plugin.ad_daily WHERE seller_id LIKE 'TEST_E2E_%'",
            "DELETE FROM plugin.ad_today WHERE seller_id LIKE 'TEST_E2E_%'",
            "DELETE FROM finance.settlement_components WHERE transaction_id IN ("
            "  SELECT id FROM finance.settlement_transactions WHERE external_transaction_id LIKE 'TEST_E2E_%')",
            "DELETE FROM finance.settlement_transactions WHERE external_transaction_id LIKE 'TEST_E2E_%'",
            "DELETE FROM finance.settlement_statements WHERE external_statement_id LIKE 'TEST_E2E_%'",
            "DELETE FROM after_sales.case_lines WHERE case_id IN ("
            "  SELECT id FROM after_sales.cases WHERE shop_pk IN ("
            "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%'))",
            "DELETE FROM after_sales.cases WHERE shop_pk IN ("
            "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%')",
            "DELETE FROM fulfillment.tracking_events WHERE shipment_id IN ("
            "  SELECT id FROM fulfillment.shipments WHERE order_pk IN ("
            "    SELECT id FROM commerce.sales_orders WHERE shop_pk IN ("
            "      SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%')))",
            "DELETE FROM fulfillment.shipments WHERE order_pk IN ("
            "  SELECT id FROM commerce.sales_orders WHERE shop_pk IN ("
            "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%'))",
            "DELETE FROM commerce.sales_order_lines WHERE order_pk IN ("
            "  SELECT id FROM commerce.sales_orders WHERE shop_pk IN ("
            "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%'))",
            "DELETE FROM commerce.sales_orders WHERE shop_pk IN ("
            "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%')",
            "DELETE FROM procurement.manual_product_costs WHERE spu_pk IN ("
            "  SELECT id FROM commerce.products_spu WHERE shop_pk IN ("
            "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%'))",
            "DELETE FROM commerce.products_spu WHERE shop_pk IN ("
            "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_E2E_%')",
            "DELETE FROM fx.exchange_rates WHERE snapshot_id IN ("
            "  SELECT id FROM fx.exchange_rate_snapshots WHERE upstream_last_update = :fx_ts)",
            "DELETE FROM fx.exchange_rate_snapshots WHERE upstream_last_update = :fx_ts",
            "DELETE FROM security.api_keys WHERE name LIKE 'TEST_E2E_%'",
        ]:
            # pi-lens-ignore: python-sql-injection
            c.execute(text(sql), {"fx_ts": FX_SEED_TS})


def seed_all(engine: Engine) -> dict:
    """Seed 完整 E2E 数据集，返回 seed 信息 dict。"""
    cleanup(engine)
    with engine.begin() as c:
        shop_pk = _seed_shop(c)
        shop2_pk = _seed_shop2(c)
        _seed_fx(c)
        key_plaintext = _seed_api_key(c)
        spu_pks = _seed_spus(c, shop_pk)
        _seed_ad_data(c, shop_pk)
        _seed_orders_cases_settlements(c, shop_pk, spu_pks)
        _seed_shop2_data(c, shop2_pk)
    return {
        "shop_pk": shop_pk,
        "shop_id": SHOP_ID,
        "shop2_pk": shop2_pk,
        "shop2_id": SHOP2_ID,
        "key_plaintext": key_plaintext,
        "spu_count": len(spu_pks),
        "spu_pks": spu_pks,
    }


def _seed_shop(c) -> int:
    """创建测试店铺（VN region）。"""
    # pi-lens-ignore: python-sql-injection
    return c.execute(
        text(
            "INSERT INTO commerce.shops (platform, shop_id, account_name, status, region) "
            "VALUES ('tiktok', :sid, :name, 'active', :region) "
            "ON CONFLICT (platform, shop_id) DO UPDATE SET account_name = :name, region = :region "
            "RETURNING id"
        ),
        {"sid": SHOP_ID, "name": SHOP_NAME, "region": SHOP_REGION},
    ).scalar_one()


def _seed_fx(c) -> None:
    """注入汇率快照（USD→CNY/VND）。"""
    # pi-lens-ignore: python-sql-injection
    sid = c.execute(
        text(
            "INSERT INTO fx.exchange_rate_snapshots "
            "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
            "VALUES ('USD', :ts, :ts2, now(), 3) RETURNING id"
        ),
        {"ts": FX_SEED_TS, "ts2": f"{SETTLEMENT_DAY_STR}T00:00:00+00:00"},
    ).scalar()
    for code, rate in [("USD", "1"), ("VND", FX_USD_VND), ("CNY", FX_USD_CNY)]:
        # pi-lens-ignore: python-sql-injection
        c.execute(
            text(
                "INSERT INTO fx.exchange_rates (snapshot_id, base_code, target_code, rate) "
                "VALUES (:sid, 'USD', :c, :r)"
            ),
            {"sid": sid, "c": code, "r": rate},
        )


def _seed_api_key(c) -> str:
    """创建 TEST_E2E readonly API key，返回明文 key。"""
    import hashlib

    plaintext = "ttserp_ro_TEST_E2E_KEY"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    key_prefix = plaintext[:12]
    # pi-lens-ignore: python-sql-injection
    c.execute(
        text(
            "INSERT INTO security.api_keys (key_hash, key_prefix, name, role, status) "
            "VALUES (:h, :p, :n, 'readonly', 'active') "
            "ON CONFLICT (key_hash) DO UPDATE SET status = 'active'"
        ),
        {"h": key_hash, "p": key_prefix, "n": "TEST_E2E_readonly"},
    )
    return plaintext


def _seed_spus(c, shop_pk: int) -> list[int]:
    """创建 55 个 SPU（51+ 用于分页），含多种业务状态。"""
    pks = []
    for i in range(1, 56):
        spu_id = f"{SPU_PREFIX}{i:03d}"
        title = f"测试商品 {i:03d}"
        status = "ACTIVATE"
        image_url = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" if i <= 3 else None
        # pi-lens-ignore: python-sql-injection
        pk = c.execute(
            text(
                "INSERT INTO commerce.products_spu (shop_pk, spu_id, title, status, main_image_url) "
                "VALUES (:shop, :sid, :title, :status, :img) "
                "RETURNING id"
            ),
            {"shop": shop_pk, "sid": spu_id, "title": title, "status": status, "img": image_url},
        ).scalar_one()
        pks.append(pk)

    # SPU 1: 人工成本（MANUAL，25 CNY/件）
    # pi-lens-ignore: python-sql-injection
    c.execute(
        text(
            "INSERT INTO procurement.manual_product_costs "
            "(spu_pk, unit_cost, currency, valid_from, valid_to, note, created_by) "
            "VALUES (:spu, 25.0000, 'CNY', now(), NULL, 'TEST E2E 手工成本', 'test')"
        ),
        {"spu": pks[0]},
    )
    return pks


def _seed_ad_data(c, shop_pk: int) -> None:
    """为前 5 个 SPU 注入广告数据。"""
    for i in range(1, 6):
        spu_id = f"{SPU_PREFIX}{i:03d}"
        spend = f"{10 + i * 5:.2f}"
        orders = str(i * 2)
        gmv = f"{50 + i * 20:.2f}"
        # pi-lens-ignore: python-sql-injection
        c.execute(
            text(
                "INSERT INTO plugin.ad_daily ("
                "  seller_id, advertiser_id, campaign_id, product_id, endpoint, day,"
                "  mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,"
                "  onsite_mixed_real_roi2_shopping, metrics_extra, created_at"
                ") VALUES ("
                "  :seller, :adv, :camp, :pid,"
                "  '/oec_ads/shopping/v1/oec/stat/post_product_list',"
                "  :ad_day,"
                "  CAST(:spend AS NUMERIC), CAST(:orders AS BIGINT), CAST(:gmv AS NUMERIC),"
                "  NULL, '{}'::JSONB, now()"
                ") ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET"
                "  mixed_real_cost = EXCLUDED.mixed_real_cost, updated_at = now()"
            ),
            {
                "seller": SHOP_ID,
                "adv": f"{ADV_PREFIX}{i:03d}",
                "camp": f"{CAMPAIGN_PREFIX}{i:03d}",
                "pid": spu_id,
                "ad_day": AD_DAY_STR,
                "spend": spend,
                "orders": orders,
                "gmv": gmv,
            },
        )


def _seed_orders_cases_settlements(c, shop_pk: int, spu_pks: list[int]) -> None:
    """为前 5 个 SPU 创建多样化的订单/售后/结算数据。"""
    base_day = ORDER_DAY_STR

    # SPU 1: 盈利场景 - 有效订单 5 件 × 526600 VND + 退货 1 件 + 结算
    _insert_order_with_line(c, shop_pk, spu_pks[0],
                           f"{ORDER_PREFIX}001", "DELIVERED", "5", "526600", base_day, paid=True)
    order_pk_1 = c.execute(
        text("SELECT id FROM commerce.sales_orders WHERE order_id = :oid"),
        {"oid": f"{ORDER_PREFIX}001"},
    ).scalar_one()
    line_pk_1 = c.execute(
        text("SELECT id FROM commerce.sales_order_lines WHERE order_pk = :o LIMIT 1"),
        {"o": order_pk_1},
    ).scalar_one()
    # 退货退款 1 件
    _insert_case(c, shop_pk, order_pk_1, f"{CASE_PREFIX}001", "RETURN_AND_REFUND",
                "RETURN_OR_REFUND_REQUEST_COMPLETE", line_pk_1, "1", "526600")
    # 结算
    _insert_settlement(c, order_pk_1, f"{TXN_PREFIX}001", "2106400")

    # SPU 2: 亏损场景 - spend 高，销售低
    _insert_order_with_line(c, shop_pk, spu_pks[1],
                           f"{ORDER_PREFIX}002", "DELIVERED", "2", "263300", base_day, paid=True)
    order_pk_2 = c.execute(
        text("SELECT id FROM commerce.sales_orders WHERE order_id = :oid"),
        {"oid": f"{ORDER_PREFIX}002"},
    ).scalar_one()
    _insert_settlement(c, order_pk_2, f"{TXN_PREFIX}002", "526600")

    # SPU 3: 全损场景 - 已付被取消 + 海外取消（38301）
    _insert_order_with_line(c, shop_pk, spu_pks[2],
                           f"{ORDER_PREFIX}003", "CANCELLED", "1", "526600", base_day, paid=True)
    order_pk_3 = c.execute(
        text("SELECT id FROM commerce.sales_orders WHERE order_id = :oid"),
        {"oid": f"{ORDER_PREFIX}003"},
    ).scalar_one()
    line_pk_3 = c.execute(
        text("SELECT id FROM commerce.sales_order_lines WHERE order_pk = :o LIMIT 1"),
        {"o": order_pk_3},
    ).scalar_one()
    _insert_case(c, shop_pk, order_pk_3, f"{CASE_PREFIX}003", "CANCELLATION",
                "CANCELLATION_REQUEST_COMPLETE", line_pk_3, "1", "526600")
    # 海外物流证据
    ship_id = c.execute(
        text("INSERT INTO fulfillment.shipments (order_pk, external_package_id, status) "
             "VALUES (:o, :pkg, 'IN_TRANSIT') RETURNING id"),
        {"o": order_pk_3, "pkg": f"TEST_E2E_PKG_{order_pk_3}"},
    ).scalar()
    c.execute(
        text("INSERT INTO fulfillment.tracking_events "
             "(shipment_id, external_event_key, action_code, event_at, description) "
             "VALUES (:sid, :ek, 38301, now(), 'Arrived in destination country')"),
        {"sid": ship_id, "ek": f"TEST_E2E_EVT_{ship_id}"},
    )

    # SPU 4: 未结算 + 有效订单（IN_TRANSIT，已付）
    _insert_order_with_line(c, shop_pk, spu_pks[3],
                           f"{ORDER_PREFIX}004", "IN_TRANSIT", "3", "526600", base_day, paid=True)

    # SPU 5: 国内取消（无 38301）
    _insert_order_with_line(c, shop_pk, spu_pks[4],
                           f"{ORDER_PREFIX}005", "CANCELLED", "1", "263300", base_day, paid=True)

    # 填充更多订单确保分页数据充足（SPU 6-55 各一个简单有效订单）
    for i in range(6, 56):
        _insert_order_with_line(
            c, shop_pk, spu_pks[i - 1],
            f"{ORDER_PREFIX}{i:03d}", "DELIVERED", "1", "263300", base_day, paid=True,
        )


def _insert_order_with_line(c, shop_pk: int, spu_pk: int, order_id: str,
                           status: str, qty: str, price: str, day: str, paid: bool = False):
    """插入订单 + 订单行。"""
    paid_at = f"{day}T08:00:00+00:00" if paid else None
    c.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, paid_at, order_time) "
            "VALUES (:shop, :oid, :status, 'VND', CAST(:paid AS timestamptz), "
            "CAST(:ot AS timestamptz))"
        ),
        {"shop": shop_pk, "oid": order_id, "status": status, "paid": paid_at, "ot": f"{day}T08:00:00+00:00"},
    )
    order_pk = c.execute(
        text("SELECT id FROM commerce.sales_orders WHERE order_id = :oid"),
        {"oid": order_id},
    ).scalar_one()
    c.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "VALUES (:o, :ext, :spu, CAST(:qty AS numeric), CAST(:price AS numeric), 'VND')"
        ),
        {"o": order_pk, "ext": f"LINE_{order_id}", "spu": spu_pk, "qty": qty, "price": price},
    )


def _insert_case(c, shop_pk: int, order_pk: int, ext_case: str,
                case_type: str, status: str, line_pk: int, qty: str, amt: str):
    """插入售后 case + case line。"""
    case_pk = c.execute(
        text(
            "INSERT INTO after_sales.cases "
            "(shop_pk, order_pk, external_case_id, case_type, status, "
            "created_at_source, updated_at_source, currency) "
            "VALUES (:shop, :o, :ec, :ct, :st, now(), now(), 'VND') RETURNING id"
        ),
        {"shop": shop_pk, "o": order_pk, "ec": ext_case, "ct": case_type, "st": status},
    ).scalar_one()
    c.execute(
        text(
            "INSERT INTO after_sales.case_lines "
            "(case_id, sales_order_line_id, external_case_line_id, quantity, refund_amount, currency) "
            "VALUES (:c, :sl, :ext, CAST(:qty AS numeric), CAST(:amt AS numeric), 'VND')"
        ),
        {"c": case_pk, "sl": line_pk, "ext": f"CLINE_{ext_case}", "qty": qty, "amt": amt},
    )


def _insert_settlement(c, order_pk: int, external_id: str, amount_vnd: str):
    """插入结算 statement + transaction + component。"""
    settlement_ts = f"{SETTLEMENT_DAY_STR}T00:00:00+00:00"
    stmt_pk = c.execute(
        text(
            "INSERT INTO finance.settlement_statements "
            "(external_statement_id, statement_time, currency) "
            "VALUES (:sid, :ts, 'VND') RETURNING id"
        ),
        {"sid": f"TEST_E2E_STMT_{external_id}", "ts": settlement_ts},
    ).scalar_one()
    txn_pk = c.execute(
        text(
            "INSERT INTO finance.settlement_transactions "
            "(settlement_statement_id, external_transaction_id, order_pk, transaction_time) "
            "VALUES (:stmt, :eid, :opk, :ts) RETURNING id"
        ),
        {"stmt": stmt_pk, "eid": external_id, "opk": order_pk, "ts": settlement_ts},
    ).scalar_one()
    c.execute(
        text(
            "INSERT INTO finance.settlement_components "
            "(transaction_id, component_code, amount, currency) "
            "VALUES (:txn, 'SETTLEMENT', :amt, 'VND')"
        ),
        {"txn": txn_pk, "amt": amount_vnd},
    )


def _seed_shop2(c) -> int:
    """创建第二家测试店铺（VN），用于店铺切换测试。"""
    # pi-lens-ignore: python-sql-injection
    return c.execute(
        text(
            "INSERT INTO commerce.shops (platform, shop_id, account_name, status, region) "
            "VALUES ('tiktok', :sid, :name, 'active', :region) "
            "ON CONFLICT (platform, shop_id) DO UPDATE SET account_name = :name, region = :region "
            "RETURNING id"
        ),
        {"sid": SHOP2_ID, "name": SHOP2_NAME, "region": SHOP_REGION},
    ).scalar_one()


def _seed_shop2_data(c, shop2_pk: int) -> list[int]:
    """为第二家店铺创建少量 SPU 和订单，确保店铺切换测试有意义。"""
    pks = []
    for i in range(1, 4):
        spu_id = f"{SHOP2_SPU_PREFIX}{i:03d}"
        # pi-lens-ignore: python-sql-injection
        pk = c.execute(
            text(
                "INSERT INTO commerce.products_spu (shop_pk, spu_id, title, status) "
                "VALUES (:shop, :sid, :title, 'ACTIVATE') RETURNING id"
            ),
            {"shop": shop2_pk, "sid": spu_id, "title": f"Shop2 测试商品 {i:03d}"},
        ).scalar_one()
        pks.append(pk)
    # One order per SPU
    for i, spu_pk in enumerate(pks, start=1):
        _insert_order_with_line(
            c, shop2_pk, spu_pk,
            f"{SHOP2_ORDER_PREFIX}{i:03d}", "DELIVERED", "1", "263300",
            ORDER_DAY_STR, paid=True,
        )
    return pks

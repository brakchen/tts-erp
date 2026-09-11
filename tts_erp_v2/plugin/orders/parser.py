"""plugin.orders 解析层。

三个解析函数，每个函数：
1. 从 TikTok 响应 JSON 提取结构化数据
2. 调用 repository 写入业务表
3. 返回写入行数

解析规则严格按 tech-doc/chrome-ext-order-sync-design.md §10。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.plugin.orders.repository import (
    _parse_bill_period,
    _payment_status_to_text,
    _to_decimal,
    _ts_to_datetime,
    upsert_order,
    upsert_order_line,
    upsert_settlement,
    upsert_settlement_detail,
    upsert_shipment,
    upsert_tracking_event,
)

log = logging.getLogger("tts_erp_v2.plugin.orders.parser")


# ── helpers ─────────────────────────────────────────────────────────


def flatten_fees(fee_list: list[dict] | None) -> list[dict]:
    """递归展开 fee_list 为扁平 [{code, amount, currency}]。"""
    if not fee_list:
        return []
    result: list[dict] = []
    for fee in fee_list:
        code = fee.get("type", "UNKNOWN")
        amount_obj = fee.get("amount") or {}
        amount = amount_obj.get("amount", "0")
        currency = amount_obj.get("currency", "")
        result.append({"code": code, "amount": amount, "currency": currency})
        result.extend(flatten_fees(fee.get("sub_fees")))
    return result


# ── 订单解析 ────────────────────────────────────────────────────────


def parse_order_response(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    response_body: dict,
    captured_at: datetime,
) -> int:
    """解析 order/list 响应 → 写 orders + order_lines。返回写入行数。"""
    rows_written = 0
    data = response_body.get("data") or {}
    main_orders = data.get("main_orders") or []

    for order in main_orders:
        order_id = str(order.get("main_order_id", ""))
        if not order_id:
            log.warning("order missing main_order_id, skipping: %s", order)
            continue

        # ✅ 实测确认（2026-09-09 域名观察）
        # order_status_module 是数组，每个 order_line 一个元素
        osm_list = order.get("order_status_module") or []
        osm_first = osm_list[0] if isinstance(osm_list, list) and osm_list else {}
        # price_module: grand_total=实付, sub_total=总额
        pm = order.get("price_module") or {}
        grand_total = pm.get("grand_total") or {}
        sub_total = pm.get("sub_total") or {}
        # 时间戳在 trade_order_module（不在 order_status_module）
        tom = order.get("trade_order_module") or {}
        # buyer 信息
        bim = order.get("buyer_info_module") or {}

        main_order_status = osm_first.get("main_order_status")  # 整数
        sku_display_status = osm_first.get("sku_display_status")  # 整数
        currency = grand_total.get("currency") or sub_total.get("currency")
        payment_amount = _to_decimal(grand_total.get("price_val"))
        total_amount = _to_decimal(sub_total.get("price_val"))
        fulfillment_type = tom.get("fulfillment_type")  # 整数
        pay_method = tom.get("pay_method")  # 文本
        sale_region = tom.get("sale_region")  # 如 "VN"
        shipping_fee = _to_decimal((tom.get("shipping_fee") or {}).get("price_val"))
        order_time = _ts_to_datetime(tom.get("create_time"))  # 秒级字符串
        update_time = _ts_to_datetime(tom.get("update_time"))  # 毫秒级字符串
        latest_rts_time = _ts_to_datetime(tom.get("latest_rts_time"))
        latest_tts_time = _ts_to_datetime(tom.get("latest_tts_time"))
        buyer_nickname = bim.get("buyer_nickname")

        upsert_order(
            sess,
            log_id=log_id,
            shop_id=shop_id,
            order_id=order_id,
            main_order_status=main_order_status,
            sku_display_status=sku_display_status,
            currency=currency,
            payment_amount=payment_amount,
            total_amount=total_amount,
            fulfillment_type=fulfillment_type,
            pay_method=pay_method,
            sale_region=sale_region,
            shipping_fee=shipping_fee,
            order_time=order_time,
            update_time=update_time,
            latest_rts_time=latest_rts_time,
            latest_tts_time=latest_tts_time,
            buyer_nickname=buyer_nickname,
        )
        rows_written += 1

        # sku_module 优先，fulfill_line_module 备选
        sku_items = order.get("sku_module") or order.get("fulfill_line_module") or []
        seen_skus: set[str] = set()
        for item in sku_items:
            sku_id = str(item.get("sku_id", ""))
            if not sku_id or sku_id in seen_skus:
                continue
            seen_skus.add(sku_id)

            # ✅ 实测确认（2026-09-09）
            product_id = item.get("product_id")
            product_name = item.get("product_name")
            variant_name = item.get("sku_name")
            # product_image.url_list[0]（不是 sku_image 字符串）
            image_obj = item.get("product_image") or {}
            image_url = (image_obj.get("url_list") or [None])[0]
            quantity = _to_decimal(item.get("quantity"))
            # sku_unit_price.price_val（不是 sale_price.amount）
            unit_price_obj = item.get("sku_unit_price") or {}
            total_price_obj = item.get("sku_total_price") or {}
            unit_price = _to_decimal(unit_price_obj.get("price_val"))
            total_price = _to_decimal(total_price_obj.get("price_val"))
            line_currency = unit_price_obj.get("currency")
            # order_status_module 按 order_line_id 关联
            line_ids = item.get("order_line_ids") or []
            line_main_status = None
            line_sku_status = None
            if line_ids:
                for osm_item in osm_list:
                    if (
                        isinstance(osm_item, dict)
                        and osm_item.get("order_line_id") == line_ids[0]
                    ):
                        line_main_status = osm_item.get("main_order_status")
                        line_sku_status = osm_item.get("sku_display_status")
                        break

            upsert_order_line(
                sess,
                log_id=log_id,
                shop_id=shop_id,
                order_id=order_id,
                sku_id=sku_id,
                product_id=str(product_id) if product_id is not None else None,
                product_name=product_name,
                variant_name=variant_name,
                image_url=image_url,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                currency=line_currency,
                main_order_status=line_main_status,
                sku_display_status=line_sku_status,
            )
            rows_written += 1

    return rows_written


# ── 物流解析 ────────────────────────────────────────────────────────


def _parse_track_time(value: Any) -> datetime | None:
    """track_list[].time → datetime。ISO 字符串或时间戳。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _ts_to_datetime(value)
    if isinstance(value, str):
        if value in ("", "0"):
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            return None
    return None


def parse_logistics_response(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    order_id: str,
    response_body: dict,
    captured_at: datetime,
) -> int:
    """解析 logistic_detail/list 响应 → 写 shipments + tracking_events。返回写入行数。"""
    rows_written = 0
    data = response_body.get("data") or {}
    package_list = data.get("package_list") or []

    for pkg in package_list:
        package_id = str(pkg.get("package_id", ""))
        if not package_id:
            log.warning("package missing package_id, skipping: %s", pkg)
            continue

        tracking_number = pkg.get("tracking_no")
        carrier_name = pkg.get("logistic_supplier")

        # track_list → status, shipped_at, delivered_at
        logistic_detail = pkg.get("logistic_detail") or {}
        track_list = logistic_detail.get("track_list") or []

        status = None
        shipped_at = None
        delivered_at = None
        if track_list:
            first_track = track_list[0]
            last_track = track_list[-1]
            status = last_track.get("track_status")
            shipped_at = _parse_track_time(first_track.get("time"))
            last_time = _parse_track_time(last_track.get("time"))
            # 仅当 status 含 "elivered" 时填 delivered_at
            if status and "elivered" in status.lower():
                delivered_at = last_time

        upsert_shipment(
            sess,
            log_id=log_id,
            shop_id=shop_id,
            order_id=order_id,
            package_id=package_id,
            tracking_number=tracking_number,
            carrier_name=carrier_name,
            status=status,
            shipped_at=shipped_at,
            delivered_at=delivered_at,
        )
        rows_written += 1

        # tracking_events
        for idx, track in enumerate(track_list):
            event_key = f"{package_id}_{idx}"
            event_at = _parse_track_time(track.get("time"))
            description = track.get("track_status")
            location = track.get("location")

            upsert_tracking_event(
                sess,
                log_id=log_id,
                shop_id=shop_id,
                package_id=package_id,
                event_key=event_key,
                event_at=event_at,
                description=description,
                location=location,
            )
            rows_written += 1

    return rows_written


# ── 结算解析 ────────────────────────────────────────────────────────


def _parse_amount(amount_obj: dict | None) -> Decimal | None:
    """TikTok {amount, currency} 对象 → Decimal。"""
    if not amount_obj:
        return None
    return _to_decimal(amount_obj.get("amount"))


def _parse_iso_dt(value: str | None) -> datetime | None:
    """ISO 字符串 → datetime(UTC)。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def parse_statement_list_response(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    response_body: dict,
    captured_at: datetime,
) -> int:
    """解析 statement/list/detail 响应 → 写 settlements。返回写入行数。"""
    rows_written = 0
    data = response_body.get("data") or {}
    statement_records = data.get("statement_records") or []

    for record in statement_records:
        statement_id = str(record.get("statement_id", ""))
        if not statement_id:
            log.warning("statement missing statement_id, skipping: %s", record)
            continue

        statement_version = record.get("statement_version", 0)
        bill_period = record.get("bill_period")
        period_start, period_end = _parse_bill_period(bill_period)
        settlement_time = _parse_iso_dt(record.get("settlement_time"))
        settlement_id = record.get("settlement_id")
        payment_id = record.get("payment_id")
        payment_status = _payment_status_to_text(record.get("payment_status"))
        statement_type = record.get("statement_type")
        payment_pending_reason = record.get("payment_pending_reason")

        settle_amount_obj = record.get("settle_amount") or {}
        earning_amount_obj = record.get("earning_amount") or {}
        fee_amount_obj = record.get("fee_amount") or {}
        adjust_amount_obj = record.get("adjust_amount") or {}
        payable_amount_obj = record.get("payable_amount") or {}
        shipping_amount_obj = record.get("shipping_amount") or {}
        total_reserve_amount_obj = record.get("total_reserve_amount") or {}

        upsert_settlement(
            sess,
            log_id=log_id,
            shop_id=shop_id,
            statement_id=statement_id,
            statement_version=statement_version,
            bill_period=bill_period,
            period_start=period_start,
            period_end=period_end,
            settlement_time=settlement_time,
            settlement_id=settlement_id,
            payment_id=payment_id,
            payment_status=payment_status,
            statement_type=statement_type,
            payment_pending_reason=payment_pending_reason,
            settle_amount=_parse_amount(settle_amount_obj),
            earning_amount=_parse_amount(earning_amount_obj),
            fee_amount=_parse_amount(fee_amount_obj),
            adjust_amount=_parse_amount(adjust_amount_obj),
            payable_amount=_parse_amount(payable_amount_obj),
            shipping_amount=_parse_amount(shipping_amount_obj),
            total_reserve_amount=_parse_amount(total_reserve_amount_obj),
            currency=settle_amount_obj.get("currency"),
        )
        rows_written += 1

    return rows_written


def parse_statement_transaction_response(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    response_body: dict,
    captured_at: datetime,
) -> int:
    """解析 statement/transaction/detail 响应 → 写 settlement_details。返回写入行数。"""
    rows_written = 0
    data = response_body.get("data") or {}
    sku_record = data.get("sku_record") or {}

    sku_detail_id = str(sku_record.get("statement_sku_detail_id", ""))
    if not sku_detail_id:
        log.warning("sku_record missing statement_sku_detail_id")
        return 0

    statement_id = str(sku_record.get("statement_id", ""))
    statement_version = sku_record.get("statement_version", 0)
    trade_order_id = sku_record.get("trade_order_id")
    sku_id = str(sku_record.get("sku_id", "")) or None
    product_name = sku_record.get("product_name")
    sku_name = sku_record.get("sku_name")
    quantity = _to_decimal(sku_record.get("quantity"))
    settlement_status = (
        str(sku_record.get("settlement_status", ""))
        if sku_record.get("settlement_status") is not None
        else None
    )
    placed_time = _parse_iso_dt(sku_record.get("placed_time"))

    settlement_amount_obj = sku_record.get("settlement_amount") or {}
    earning_amount_obj = sku_record.get("earning_amount") or {}
    fees_obj = sku_record.get("fees") or {}

    # 递归展开费用树
    in_come = sku_record.get("in_come") or {}
    out_come = sku_record.get("out_come") or {}
    fee_components = flatten_fees(in_come.get("fee_list")) + flatten_fees(
        out_come.get("fee_list")
    )

    seller_web_cut_flow = response_body.get("seller_web_cut_flow")
    seller_app_cut_flow = response_body.get("seller_app_cut_flow")

    upsert_settlement_detail(
        sess,
        log_id=log_id,
        shop_id=shop_id,
        statement_id=statement_id,
        statement_version=statement_version,
        sku_detail_id=sku_detail_id,
        trade_order_id=trade_order_id,
        sku_id=sku_id,
        product_name=product_name,
        sku_name=sku_name,
        quantity=quantity,
        settlement_status=settlement_status,
        placed_time=placed_time,
        settlement_amount=_parse_amount(settlement_amount_obj),
        earning_amount=_parse_amount(earning_amount_obj),
        fees_amount=_parse_amount(fees_obj),
        currency=settlement_amount_obj.get("currency"),
        fee_components=fee_components if fee_components else None,
        seller_web_cut_flow=seller_web_cut_flow,
        seller_app_cut_flow=seller_app_cut_flow,
    )
    rows_written += 1

    return rows_written

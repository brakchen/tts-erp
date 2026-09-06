"""spu_link — 订单行 → ``products_spu`` 关联（写时解析 + 同步后回填）。

背景（2026-09-06 根因）：``commerce.sales_order_lines.spu_pk`` 只在
2026-08-31 一次性 backfill 过；orders / order_detail job 写行时把
``spu_pk`` 留 NULL（orders.py 里 "later join" 注释），而**没有任何
job 执行那次 join** —— 8-31 之后同步的每一行都停留在 NULL，整单从
SPU 级报表（ROI 看板单量 / GMV / 有效销售）消失。

本模块提供两条收敛路径（都幂等、同店限定——产品外部 id 不做全局唯一
假设，行只关联到自己店铺目录里的产品）：

* ``spu_map_for_shop`` + ``link_line_spu_pk``：orders / order_detail
  写行时用每 shop 一次的预载 map 命中即填；目录未命中 → 保持 NULL。
* ``backfill_null_line_spu_pk``：products job 每次同步后调用，把
  "订单先落地、产品后入库" 的 NULL 行补上；oneoff 脚本全库跑同一
  routine 修存量。

关联键 = ``sales_order_lines.external_product_id_snapshot``
（= TikTok 下单时的 ``product_id``）↔ ``products_spu.spu_id``，且
``sales_orders.shop_pk = products_spu.shop_pk``。
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelProduct

__all__ = [
    "backfill_null_line_spu_pk",
    "link_line_spu_pk",
    "spu_map_for_shop",
]


def spu_map_for_shop(session: Session, *, shop_pk: int) -> dict[str, int]:
    """Return ``{products_spu.spu_id: pk}`` for one shop（写时解析用）。"""
    rows = session.execute(
        select(ChannelProduct.id, ChannelProduct.spu_id).where(
            ChannelProduct.shop_pk == shop_pk
        )
    ).all()
    return {spu_id: pk for pk, spu_id in rows}


def link_line_spu_pk(
    spu_map: dict[str, int], *, product_snapshot: str | None
) -> int | None:
    """Resolve a line's ``spu_pk`` from its product snapshot.

    ``spu_map`` comes from :func:`spu_map_for_shop`; callers should only
    set ``spu_pk`` on the line when this returns non-None（NULL 行留给
    products 回填 / 保持未关联，绝不按 title 之类猜）。
    """
    if not product_snapshot:
        return None
    return spu_map.get(product_snapshot)


def backfill_null_line_spu_pk(session: Session, *, shop_pk: int | None = None) -> int:
    """Link every NULL ``spu_pk`` line whose snapshot now exists in catalog.

    Idempotent（只碰 ``spu_pk IS NULL`` 行）。``shop_pk=None`` = 全库
    （oneoff 存量修复用）；给定 shop_pk = 只处理该店（products job 用）。
    Returns the number of rows linked.
    """
    # 关联键:external_product_id_snapshot ↔ products_spu.spu_id,且
    # 行的店 = 产品的店(经 sales_orders 桥接;不做全局唯一假设)。
    if shop_pk is not None:
        stmt_text = (
            "UPDATE commerce.sales_order_lines sl "
            "SET spu_pk = cp.id "
            "FROM commerce.products_spu cp "
            "WHERE sl.spu_pk IS NULL "
            "AND cp.shop_pk = :shop_pk "
            "AND cp.spu_id = sl.external_product_id_snapshot "
            "AND EXISTS (SELECT 1 FROM commerce.sales_orders so "
            "            WHERE so.id = sl.order_pk AND so.shop_pk = cp.shop_pk)"
        )
        result = session.execute(text(stmt_text), {"shop_pk": shop_pk})
    else:
        stmt_text = (
            "UPDATE commerce.sales_order_lines sl "
            "SET spu_pk = cp.id "
            "FROM commerce.products_spu cp "
            "WHERE sl.spu_pk IS NULL "
            "AND cp.spu_id = sl.external_product_id_snapshot "
            "AND EXISTS (SELECT 1 FROM commerce.sales_orders so "
            "            WHERE so.id = sl.order_pk AND so.shop_pk = cp.shop_pk)"
        )
        result = session.execute(text(stmt_text))
    return int(result.rowcount or 0)

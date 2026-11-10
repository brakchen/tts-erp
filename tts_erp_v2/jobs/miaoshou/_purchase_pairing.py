"""采购单 ⇄ 下游 SPU 的配对：序号配对 + 官方关联键交叉校验。

2026-11-10 生产库只读实测（1,954 条原始采购单载荷 / 857 张采购单）：

**① 两侧之间没有任何关联键 —— "用官方关联键替换序号配对"在事实层面不成立。**
cookie 载荷的顶层货源行只有 ``sourceItemId/sourceSkuId/sourceUnitPrice/
sourceQuantity/sourceTitle``；包裹行只有 ``platformItemId/opOrderItemId/
opOrderPackageId/...``。``platformOuterSkuId`` 非空率 1/5445，
``skuSubName``/``title`` 与货源侧 0 命中；官方包裹接口的 items[] 同样没有
``sourceItemId`` ⇒ **官方接口也提供不了 source↔platform 的键**。

**② 官方关联键 cookie 侧已经有了**（同名同义）：包裹行带 ``opOrderItemId`` /
``opOrderPackageId`` / ``opOrderPackageItemId``，与官方包裹接口 items[] 一致。
⇒ 官方接口给的是**同一份平台侧映射的独立副本**。

**③ 官方副本已经在本地库里**：``miaoshou.packages`` 作业已把官方包裹列表同步进
``miaoshou.package_items``（``external_order_item_id``=opOrderItemId、
``platform_product_id``=platformItemId，3,444 行，分钟级新鲜）⇒ 交叉校验
**不需要新增上游调用、不需要新的凭据路径**，只读本地表。

**④ 序号配对从未漂移**：253 张多版本采购单里，源侧/平台侧顺序变化 0 次、
zip 配对结果变化 0 次；按 opOrderItemId 的确定性排序与今天的"首次出现顺序"
在 98 张多 SPU 采购单上**完全一致**（差异 0）⇒ 新模式不改变任何现有结果，
只把"依赖抓取顺序"换成"有据可查"。

**⑤ 交叉校验实测**：cookie 5,445 条包裹行中 5,395 条能在官方表找到，
**platformItemId 冲突 0 处**；50 条（0.9%）官方表未覆盖（覆盖缺口，不是冲突）。

于是本模块提供两件东西：

* :func:`platform_group_order` —— 平台侧分组按官方关联键确定性排序（可关闭）。
* :func:`cross_check_order` —— cookie 包裹行 vs 官方映射的逐条比对：
  **冲突**（两边对同一 opOrderItemId 说了不同的 platformItemId）⇒ 显式失败
  （跳过该采购单并记 issue）；**覆盖缺口**（官方表没有这条行）⇒ 只记 issue，
  不阻断 —— 官方表本身可能没同步到那么老的包裹。

两种模式都由设置 ``MIAOSHOU_PURCHASE_PAIRING_MODE`` 选择：
``ordinal``（默认，与 2026-11-10 之前的行为逐字节一致）/
``official_keys``（本模块的新行为）。官方表缺失或查询失败时**自动降级**回
``ordinal`` —— 新模式永远不能成为 cookie 链的单点故障。
"""

from __future__ import annotations

from typing import Any

#: 平台侧分组按「首次出现顺序」排（旧行为，默认）。
PAIRING_MODE_ORDINAL = "ordinal"
#: 平台侧分组按官方关联键确定性排序 + 与官方映射交叉校验。
PAIRING_MODE_OFFICIAL_KEYS = "official_keys"
PAIRING_MODES: tuple[str, ...] = (PAIRING_MODE_ORDINAL, PAIRING_MODE_OFFICIAL_KEYS)

#: 设置名（runtime_settings.get_setting 读）。
PAIRING_MODE_SETTING = "MIAOSHOU_PURCHASE_PAIRING_MODE"

ISSUE_OFFICIAL_KEY_CONFLICT = "OFFICIAL_KEY_CONFLICT"
ISSUE_OFFICIAL_KEY_NOT_FOUND = "OFFICIAL_KEY_NOT_FOUND"

#: 逐条比对时，单个 issue 里最多列多少条明细（避免 issue 行无限长）。
_MAX_DETAIL_ROWS = 20


def platform_group_order(platform_groups: dict[str, list[Any]]) -> list[str]:
    """平台侧分组顺序：按官方关联键 ``opOrderItemId`` 升序（确定性）。

    没有 ``opOrderItemId`` 的分组排在最后（并按 spu_id 稳定排序），这样
    "键缺失"不会把整张采购单的顺序打乱。

    Args:
        platform_groups: ``platformItemId -> [(item, shop_id, shop_name), ...]``
    """
    def sort_key(spu_id: str) -> tuple[int, str, str]:
        keys = sorted(
            str(item.get("opOrderItemId") or "")
            for item, _shop_id, _shop_name in platform_groups.get(spu_id, [])
            if isinstance(item, dict) and item.get("opOrderItemId")
        )
        if not keys:
            return (1, "", spu_id)
        return (0, keys[0], spu_id)

    return sorted(platform_groups, key=sort_key)


def official_item_map(session: Any) -> dict[str, tuple[str, str]]:
    """``opOrderItemId -> (opOrderPackageId, platformItemId)``，读本地官方包裹表。

    官方包裹接口由 ``miaoshou.packages`` 作业同步进 ``miaoshou.package_items``，
    所以这里**只读本地表**：不新增上游调用、不新增凭据路径、不给 cookie 链
    增加任何外部依赖。

    Args:
        session: 已开启事务的 SQLAlchemy session。
    """
    from sqlalchemy import select

    from tts_erp_v2.db.models.miaoshou import MiaoshouPackage, MiaoshouPackageItem

    stmt = (
        select(
            MiaoshouPackageItem.external_order_item_id,
            MiaoshouPackage.external_package_id,
            MiaoshouPackageItem.platform_product_id,
        )
        .join(MiaoshouPackage, MiaoshouPackage.id == MiaoshouPackageItem.package_id)
        .where(MiaoshouPackageItem.active.is_(True))
        .where(MiaoshouPackageItem.external_order_item_id.isnot(None))
    )
    out: dict[str, tuple[str, str]] = {}
    for item_id, package_id, product_id in session.execute(stmt).all():
        if not item_id:
            continue
        out[str(item_id)] = (str(package_id or ""), str(product_id or ""))
    return out


def cross_check_order(
    order: dict[str, Any],
    official_map: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """cookie 包裹行 vs 官方映射的逐条比对。

    返回 issue 列表：

    * ``OFFICIAL_KEY_CONFLICT`` —— 同一 ``opOrderItemId`` 的 platformItemId /
      opOrderPackageId 两边不一致（**矛盾** ⇒ 该采购单的平台侧映射不可信）；
    * ``OFFICIAL_KEY_NOT_FOUND`` —— 官方表里没有这条行（**覆盖缺口**，不是矛盾；
      官方表可能只是没同步到那么老的包裹）。
    """
    order_sn = str(order.get("purchaseOrderSn") or "")
    conflicts: list[dict[str, Any]] = []
    missing: list[str] = []
    for package in order.get("opOrderPackageList") or []:
        if not isinstance(package, dict):
            continue
        cookie_package_id = str(package.get("opOrderPackageId") or "")
        for item in package.get("purchaseItems") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("opOrderItemId") or "")
            if not item_id:
                continue
            cookie_spu = str(item.get("platformItemId") or "")
            official = official_map.get(item_id)
            if official is None:
                missing.append(item_id)
                continue
            official_package_id, official_spu = official
            if official_spu != cookie_spu or (
                cookie_package_id
                and official_package_id
                and official_package_id != cookie_package_id
            ):
                conflicts.append(
                    {
                        "op_order_item_id": item_id,
                        "cookie_platform_item_id": cookie_spu,
                        "official_platform_item_id": official_spu,
                        "cookie_op_order_package_id": cookie_package_id,
                        "official_op_order_package_id": official_package_id,
                    }
                )
    issues: list[dict[str, Any]] = []
    if conflicts:
        issues.append(
            {
                "issue_type": ISSUE_OFFICIAL_KEY_CONFLICT,
                "purchase_order_sn": order_sn,
                "conflict_count": len(conflicts),
                "conflicts": conflicts[:_MAX_DETAIL_ROWS],
            }
        )
    if missing:
        issues.append(
            {
                "issue_type": ISSUE_OFFICIAL_KEY_NOT_FOUND,
                "purchase_order_sn": order_sn,
                "missing_count": len(missing),
                "missing_item_ids": missing[:_MAX_DETAIL_ROWS],
            }
        )
    return issues


def has_conflict(issues: list[dict[str, Any]]) -> bool:
    """这批 issue 里有没有「矛盾」（矛盾才让采购单显式失败）。"""
    return any(
        str(issue.get("issue_type")) == ISSUE_OFFICIAL_KEY_CONFLICT
        for issue in issues
    )


__all__ = [
    "ISSUE_OFFICIAL_KEY_CONFLICT",
    "ISSUE_OFFICIAL_KEY_NOT_FOUND",
    "PAIRING_MODES",
    "PAIRING_MODE_OFFICIAL_KEYS",
    "PAIRING_MODE_ORDINAL",
    "PAIRING_MODE_SETTING",
    "cross_check_order",
    "has_conflict",
    "official_item_map",
    "platform_group_order",
]

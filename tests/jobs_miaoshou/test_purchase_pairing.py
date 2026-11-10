"""④：采购单 ⇄ 下游 SPU 的配对 —— 官方关联键模式与交叉校验。

2026-11-10 生产库只读实测（1,954 条原始采购单载荷 / 857 张采购单）：

* **两侧之间没有任何关联键**：顶层货源行只有 ``sourceItemId``/``sourceUnitPrice``…，
  包裹行只有 ``platformItemId``/``opOrderItemId``…；``platformOuterSkuId`` 非空率
  1/5445，``skuSubName``/``title`` 与货源侧 0 命中 ⇒ 官方包裹接口同样给不了
  source↔platform 的键。**"用官方关联键替换序号配对"在事实层面不成立。**
* 官方关联键 cookie 侧**已经有了**（同名同义），且官方副本已被
  ``miaoshou.packages`` 作业同步进 ``miaoshou.package_items`` ⇒ 交叉校验只读本地
  表，**不新增上游调用、不新增凭据路径**。
* 序号配对在 253 张多版本采购单上**从未漂移**；按 opOrderItemId 的确定性排序与
  今天的"首次出现顺序"在 98 张多 SPU 单上完全一致 ⇒ 新模式不改变现有结果，
  只把"依赖抓取顺序"换成"有据可查"。

本文件断言三类，各自独立 method：
* 位置断言 —— 判据落在哪个模块、哪个函数/设置上；
* 数量枚举 —— 映射形状、issue 明细条数的精确内容；
* 分支覆盖 —— 每个分支一个用例，含「必须命中」与「必须不命中」孪生。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tts_erp_v2.jobs.miaoshou import _purchase_pairing as pairing
from tts_erp_v2.jobs.miaoshou import purchase_price_clean as job

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]


def _order() -> dict:
    """一张两货源组、两 SPU 组的采购单。

    **故意**让"包裹返回顺序"与"官方关联键顺序"不一致：
    包裹里先出现 SPU-B（opOrderItemId=ITEM-9），后出现 SPU-A（opOrderItemId=ITEM-1），
    而货源行是 SRC-A、SRC-B。⇒

    * 序号配对（旧）会配成 SRC-A↔SPU-B、SRC-B↔SPU-A；
    * 官方键模式配成 SRC-A↔SPU-A、SRC-B↔SPU-B（与官方映射一致）。

    生产数据里这两种顺序目前**从未不同**（实测 98 张多 SPU 单差异 0），这个夹具
    存在的意义是：证明新模式对"上游改了返回顺序"是稳的。
    """
    return {
        "purchaseOrderSn": "SN-TEST-1",
        "gmtPurchaseOrderStart": "2026-11-01 10:00:00",
        "purchaseOrderStatus": "finished",
        "purchaseItems": [
            {
                "sourceItemId": "SRC-A",
                "sourceSkuId": "SKU-A1",
                "sourceUnitPrice": "28.00",
                "sourceQuantity": "2",
                "sourceSkuSubName": "红色",
                "sourceTitle": "货源甲",
            },
            {
                "sourceItemId": "SRC-B",
                "sourceSkuId": "SKU-B1",
                "sourceUnitPrice": "35.00",
                "sourceQuantity": "1",
                "sourceSkuSubName": "蓝色",
                "sourceTitle": "货源乙",
            },
        ],
        "opOrderPackageList": [
            {
                "opOrderPackageId": "PKG-1",
                "shopId": "SHOP-1",
                "shopName": "越南店",
                "purchaseItems": [
                    {
                        "platformItemId": "SPU-B",
                        "opOrderItemId": "ITEM-9",
                        "opOrderPackageItemId": "PI-9",
                        "unitPrice": "999223",
                    }
                ],
            },
            {
                "opOrderPackageId": "PKG-2",
                "shopId": "SHOP-1",
                "shopName": "越南店",
                "purchaseItems": [
                    {
                        "platformItemId": "SPU-A",
                        "opOrderItemId": "ITEM-1",
                        "opOrderPackageItemId": "PI-1",
                        "unitPrice": "919147",
                    }
                ],
            },
        ],
    }


def _official_map_agreeing() -> dict[str, tuple[str, str]]:
    """官方映射：与 cookie 载荷一致（ITEM-1→SPU-A、ITEM-9→SPU-B）。"""
    return {
        "ITEM-1": ("PKG-2", "SPU-A"),
        "ITEM-9": ("PKG-1", "SPU-B"),
    }


def _pairing_of(observations) -> dict[str, str]:
    return {o.source_item_id: o.spu_id for o in observations}


# ─── 位置断言 ─────────────────────────────────────────────────────────────


def test_pairing_helpers_live_in_the_pairing_module() -> None:
    """位置断言：配对逻辑的唯一实现落在 jobs/miaoshou/_purchase_pairing.py。"""
    assert callable(pairing.platform_group_order)
    assert callable(pairing.cross_check_order)
    assert callable(pairing.official_item_map)
    assert callable(pairing.has_conflict)
    path = Path(pairing.__file__).resolve()
    assert path.name == "_purchase_pairing.py"
    assert path.parent.name == "miaoshou"


def test_the_job_uses_the_shared_pairing_module() -> None:
    """位置断言：作业从共享模块取实现，不自己写一套。"""
    source = Path(job.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "_purchase_pairing"
        ):
            imported |= {alias.name for alias in node.names}
    assert {
        "cross_check_order",
        "has_conflict",
        "official_item_map",
        "platform_group_order",
    } <= imported


def test_pairing_mode_is_read_from_a_runtime_setting() -> None:
    """位置断言：模式开关是运行期设置（可单独关闭），不是写死的。"""
    assert pairing.PAIRING_MODE_SETTING == "MIAOSHOU_PURCHASE_PAIRING_MODE"
    assert job._pairing_mode() == pairing.PAIRING_MODE_ORDINAL


# ─── 数量枚举 ─────────────────────────────────────────────────────────────


def test_official_map_values_are_package_and_product_pairs() -> None:
    """数量枚举：官方映射的值恰好是 (包裹 id, SPU id) 两元组。"""
    official = _official_map_agreeing()
    assert len(official) == 2
    for value in official.values():
        assert isinstance(value, tuple)
        assert len(value) == 2


def test_group_count_mismatch_issue_lists_every_group() -> None:
    """数量枚举：数量不等的 issue 里，两个清单与计数的条数一一对应。"""
    order = _order()
    order["purchaseItems"].append(
        {
            "sourceItemId": "SRC-C",
            "sourceSkuId": "SKU-C1",
            "sourceUnitPrice": "9.00",
            "sourceQuantity": "1",
        }
    )
    _observations, issues = job._observations_from_order(order)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["issue_type"] == "GROUP_COUNT_MISMATCH"
    assert issue["source_group_count"] == len(issue["source_item_ids"])
    assert issue["platform_group_count"] == len(issue["platform_item_ids"])
    assert set(issue["platform_group_keys"]) == set(issue["platform_item_ids"])


# ─── 分支覆盖 ─────────────────────────────────────────────────────────────


def test_ordinal_mode_keeps_first_appearance_order() -> None:
    """分支覆盖：默认模式（ordinal）保持"首次出现顺序"，行为不变。"""
    observations, issues = job._observations_from_order(_order())
    assert issues == []
    assert _pairing_of(observations) == {"SRC-A": "SPU-B", "SRC-B": "SPU-A"}


def test_official_keys_mode_pairs_by_the_association_key() -> None:
    """分支覆盖（改变结果）：官方键模式按 opOrderItemId 确定性排序后配对。"""
    observations, issues = job._observations_from_order(
        _order(), official_map=_official_map_agreeing()
    )
    assert issues == []
    assert _pairing_of(observations) == {"SRC-A": "SPU-A", "SRC-B": "SPU-B"}


def test_official_keys_mode_agrees_with_the_official_mapping() -> None:
    """分支覆盖（验收判据）：官方键模式的配对结果与官方映射逐条一致。"""
    order = _order()
    observations, issues = job._observations_from_order(
        order, official_map=_official_map_agreeing()
    )
    assert issues == []
    official_spu_by_item = {
        item_id: product_id for item_id, (_pkg, product_id) in
        _official_map_agreeing().items()
    }
    for observation in observations:
        item_ids = [
            str(item.get("opOrderItemId"))
            for pkg in order["opOrderPackageList"]
            for item in pkg["purchaseItems"]
            if item.get("platformItemId") == observation.spu_id
        ]
        assert item_ids
        for item_id in item_ids:
            assert official_spu_by_item[item_id] == observation.spu_id


def test_official_keys_mode_skips_the_order_on_a_conflict() -> None:
    """分支覆盖（必须命中）：官方映射与 cookie 载荷矛盾 ⇒ 显式失败（跳过并记 issue）。"""
    conflicting = {"ITEM-1": ("PKG-2", "SPU-A"), "ITEM-9": ("PKG-1", "SPU-OTHER")}
    observations, issues = job._observations_from_order(
        _order(), official_map=conflicting
    )
    assert observations == []
    assert len(issues) == 1
    assert issues[0]["issue_type"] == pairing.ISSUE_OFFICIAL_KEY_CONFLICT
    assert issues[0]["conflict_count"] == 1
    assert issues[0]["conflicts"][0]["op_order_item_id"] == "ITEM-9"


def test_official_keys_mode_records_a_coverage_gap_without_skipping() -> None:
    """分支覆盖（不命中孪生）：官方表没覆盖到这条行 ⇒ 只记 issue，不阻断。"""
    partial = {"ITEM-1": ("PKG-2", "SPU-A")}
    observations, issues = job._observations_from_order(
        _order(), official_map=partial
    )
    assert len(observations) == 2
    assert [i["issue_type"] for i in issues] == [
        pairing.ISSUE_OFFICIAL_KEY_NOT_FOUND
    ]
    assert issues[0]["missing_count"] == 1
    assert issues[0]["missing_item_ids"] == ["ITEM-9"]


def test_missing_official_rows_do_not_count_as_conflicts() -> None:
    """分支覆盖（不命中孪生）：覆盖缺口不得被当成矛盾（否则会误杀整单）。"""
    issues = pairing.cross_check_order(_order(), {})
    assert [i["issue_type"] for i in issues] == [
        pairing.ISSUE_OFFICIAL_KEY_NOT_FOUND
    ]
    assert pairing.has_conflict(issues) is False


def test_official_map_empty_behaves_like_ordinal_mode() -> None:
    """分支覆盖（降级）：官方映射为空 ⇒ 等价于旧行为，不改变任何结果。"""
    order = _order()
    ordinal_observations, ordinal_issues = job._observations_from_order(order)
    empty_observations, empty_issues = job._observations_from_order(
        order, official_map={}
    )
    assert _pairing_of(ordinal_observations) == _pairing_of(empty_observations)
    assert ordinal_issues == empty_issues


def test_platform_group_order_puts_keyless_groups_last() -> None:
    """分支覆盖：没有 opOrderItemId 的分组排在最后（不打乱其余顺序）。"""
    groups = {
        "SPU-NO-KEY": [({"platformItemId": "SPU-NO-KEY"}, "S", "n")],
        "SPU-B": [({"opOrderItemId": "ITEM-9"}, "S", "n")],
        "SPU-A": [({"opOrderItemId": "ITEM-1"}, "S", "n")],
    }
    assert pairing.platform_group_order(groups) == ["SPU-A", "SPU-B", "SPU-NO-KEY"]

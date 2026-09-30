"""妙手 ERP 开放平台 · 包裹查询。

Endpoints:
- ``get_info()`` 获取包裹详情（Apifox api-457111980）
- ``search()``   批量获取包裹列表（Apifox api-457209915）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..miaoshou_client import MiaoshouApiError  # type: ignore[reportMissingImports]
from ..miaoshou_erp_client import MiaoshouErpClient, _safe_validate

DETAIL_PATH = "/open/v1/order/package/fetch/get_package_info"
SEARCH_PATH = "/open/v1/order/package/fetch/search_package_list"


class PackageOrderInfo(BaseModel):
    """包裹关联的平台订单摘要。"""

    model_config = ConfigDict(extra="allow")

    opOrderId: int | str | None = None
    shopId: int | str | None = None
    platform: str | None = None
    platformOrderSn: str | None = None
    platformOrderStatus: str | None = None
    site: str | None = None
    currency: str | None = None
    gmtOrderStart: str | None = None
    gmtOrderModified: str | None = None
    gmtPay: str | None = None
    gmtLastDelivery: str | None = None
    gmtDelivery: str | None = None
    gmtRefund: str | None = None
    gmtSettlement: str | None = None
    gmtFinish: str | None = None
    appOrderStatus: str | None = None
    appOrderStatusText: str | None = None


class PackageItem(BaseModel):
    """包裹内一个平台订单商品行。"""

    model_config = ConfigDict(extra="allow")

    opOrderItemId: int | str | None = None
    opOrderPackageId: int | str | None = None
    opOrderPackageItemId: int | str | None = None
    platformOrderItemIndex: str | None = None
    platformItemId: int | str | None = None
    platformSkuId: str | None = None
    platformItemNum: str | None = None
    platformOuterSkuId: str | None = None
    title: str | None = None
    skuSubName: str | None = None
    quantity: int | str | None = None
    originalPrice: float | str | None = None
    discountedPrice: float | str | None = None
    picUrl: str | None = None
    originalPicUrl: str | None = None


class PackageGiftItem(BaseModel):
    """包裹赠品行。"""

    model_config = ConfigDict(extra="allow")

    opOrderPackageGiftId: int | str | None = None
    opOrderPackageId: int | str | None = None
    goodsId: int | str | None = None
    goodsSkuId: int | str | None = None
    goodsName: str | None = None
    itemNum: str | None = None
    goodsSkuSubName: str | None = None
    goodsSkuOuterId: str | None = None
    quantity: int | str | None = None
    originalPrice: float | str | None = None
    discountedPrice: float | str | None = None
    picUrl: str | None = None


class PackageLogisticsInfo(BaseModel):
    """包裹物流渠道或尾程物流信息。"""

    model_config = ConfigDict(extra="allow")

    logisticsAgentProductId: int | str | None = None
    logisticsAgentId: int | str | None = None
    productCode: str | None = None
    productName: str | None = None
    status: str | None = None
    platformPackageNo: str | None = None
    logisticsNo: str | None = None
    logisticsCompany: str | None = None
    shippingFee: float | str | None = None
    shippingFeeCurrency: str | None = None
    warehouseName: str | None = None
    combineProductName: str | None = None


class OrderPackageInfo(BaseModel):
    """包裹详情/列表共用的数据结构。"""

    model_config = ConfigDict(extra="allow")

    opOrderPackageId: int | str | None = None
    platform: str | None = None
    platformName: str | None = None
    site: str | None = None
    siteName: str | None = None
    shopId: int | str | None = None
    shopNick: str | None = None
    shopName: str | None = None
    orderInfo: PackageOrderInfo | None = None
    items: list[PackageItem] = Field(default_factory=list)
    giftItems: list[PackageGiftItem] = Field(default_factory=list)
    logisticsAgentProductInfo: PackageLogisticsInfo | None = None
    opOrderPackageToPlatformLastMile: PackageLogisticsInfo | None = None
    appPackageNo: str | None = None
    platformPackageStatus: str | None = None
    appPackageStatus: str | None = None
    appPackageStatusText: str | None = None
    applyTrackingNoFailReason: str | None = None
    applyTrackingNoFailCode: str | None = None
    fulfillmentType: str | None = None
    logisticsNo: str | None = None


class PackageDetailData(BaseModel):
    model_config = ConfigDict(extra="allow")

    orderPackageInfo: OrderPackageInfo


class PackageDetailResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: str
    code: str
    message: str | None = None
    data: PackageDetailData | None = None


class PackageSearchData(BaseModel):
    model_config = ConfigDict(extra="allow")

    orderPackageList: list[OrderPackageInfo] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 0


class PackageSearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: str
    code: str
    message: str | None = None
    data: PackageSearchData | None = None


def _check_erp(payload: dict[str, Any]) -> dict[str, Any]:
    """妙手业务失败仍可能使用 HTTP 200；以 ``result`` 判定成功。"""
    result = str(payload.get("result", ""))
    if result != "success":
        reason = payload.get("message") or payload.get("reason") or "unknown error"
        raise MiaoshouApiError(500, f"{result}: {reason}", payload)
    return payload


def _normalize_detail(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list):
        data = next((row for row in data if isinstance(row, dict)), None)
    if not isinstance(data, dict) and isinstance(payload.get("orderPackageInfo"), dict):
        data = {"orderPackageInfo": payload["orderPackageInfo"]}
    if isinstance(data, dict):
        info = data.get("orderPackageInfo")
        if isinstance(info, dict) and set(info) == {""} and isinstance(info[""], dict):
            data = {**data, "orderPackageInfo": info[""]}
    return {**payload, "data": data}


def _normalize_search(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list):
        rows: list[dict[str, Any]] = []
        merged: dict[str, Any] = {}
        for page in data:
            if not isinstance(page, dict):
                continue
            rows.extend(
                row
                for row in page.get("orderPackageList") or []
                if isinstance(row, dict)
            )
            merged.update({k: v for k, v in page.items() if k != "orderPackageList"})
        data = {**merged, "orderPackageList": rows}
    return {**payload, "data": data}


class PackageEndpoint:
    """妙手 ERP 包裹查询 endpoint。"""

    def __init__(self, client: MiaoshouErpClient) -> None:
        self._c = client

    def get_info(self, *, op_order_package_id: int | str) -> PackageDetailResult:
        """获取一个妙手包裹详情（api-457111980）。"""
        package_id = str(op_order_package_id).strip()
        if not package_id:
            raise ValueError("op_order_package_id must not be empty")
        payload = self._c._call_erp(
            path=DETAIL_PATH,
            body={"opOrderPackageId": op_order_package_id},
        )
        return _safe_validate(
            _normalize_detail(_check_erp(payload)), PackageDetailResult
        )

    def search(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        gmt_modified_to: str | None = None,
        gmt_modified_from: str | None = None,
        gmt_create_to: str | None = None,
        gmt_create_from: str | None = None,
        platform_order_sns: str | None = None,
        app_package_status: str | None = None,
        shop_ids: list[str] | None = None,
        platform: str | None = None,
        app_package_tab: str | None = None,
    ) -> PackageSearchResult:
        """批量获取包裹列表（api-457209915），单页调用。"""
        if page < 1:
            raise ValueError(f"page must be >= 1, got {page}")
        if page_size < 1 or page_size > 100:
            raise ValueError(f"page_size must be 1..100, got {page_size}")
        body: dict[str, Any] = {"page": page, "pageSize": page_size}
        optional = {
            "gmtModifiedTo": gmt_modified_to,
            "gmtModifiedFrom": gmt_modified_from,
            "gmtCreateTo": gmt_create_to,
            "gmtCreateFrom": gmt_create_from,
            "platformOrderSns": platform_order_sns,
            "appPackageStatus": app_package_status,
            "shopIds": shop_ids,
            "platform": platform,
            "appPackageTab": app_package_tab,
        }
        body.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        payload = self._c._call_erp(path=SEARCH_PATH, body=body)
        return _safe_validate(
            _normalize_search(_check_erp(payload)), PackageSearchResult
        )


__all__ = [
    "DETAIL_PATH",
    "SEARCH_PATH",
    "OrderPackageInfo",
    "PackageDetailResult",
    "PackageEndpoint",
    "PackageSearchResult",
]

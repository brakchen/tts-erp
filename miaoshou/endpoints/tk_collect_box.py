"""妙手 ERP 开放平台 · 开放平台/商品/TK采集箱.

Endpoints:
- get_category_tree()              获取类目列表             apifox api-446814588
- get_category_metadata()           获取类目属性信息         apifox api-446814589
- get_shop_warehouse_list()         获取店铺仓库列表         apifox api-446814590
- search_collect_box_list()         获取采集箱列表           apifox api-446814591
- get_shop_collect_item_info()      获取采集箱店铺模式详情   apifox api-446814592
- save_shop_collect_item_info()      保存采集箱店铺模式详情   apifox api-446814593
- get_responsible_person_list()     获取欧盟责任人列表       apifox api-449869293
- claim_to_shop()                   认领预发布店铺           apifox api-446814594
- get_site_collect_item_info()      获取采集箱站点模式详情   apifox api-449650816
- translate_collect_item_info()     产品翻译                 apifox api-482189160
- ai_match_cid_by_shop_ids()        AI匹配类目               apifox api-482189162
- delete_collect_box_detail()       批量删除采集箱产品接口   apifox api-496804330
- ai_match_product_attribute()      AI属性匹配               apifox api-482189161
- get_brand_list()                  获取品牌列表             apifox api-497001483
- search_move_collect_list()        发布记录列表             apifox api-482189163
- get_price_template_list()         获取定价模板列表         apifox api-482189164
- get_site_default_setting()         获取各个站点的默认配置   apifox api-482189165
- translate_image()                 图片翻译                 apifox api-484286514
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..miaoshou_erp_client import (
    MiaoshouErpClient,
    _safe_validate,  # type: ignore[reportMissingImports]
)
from .collection_box import _debug_headers, _timer_query

# ============================================================
# Models
# ============================================================

# ---- get_category_tree ----


class CategoryNode(BaseModel):
    """类目树节点（递归 children）."""

    model_config = ConfigDict(extra="allow")
    cid: int
    aid: int | None = None
    fid: int | None = None
    name: str | None = None
    nameChinese: str | None = None
    isLastLevel: str | None = None
    disabled: bool | None = None
    children: dict[str, CategoryNode] = Field(default_factory=dict)


class GetCategoryTreeData(BaseModel):
    model_config = ConfigDict(extra="allow")
    cateTree: dict[str, CategoryNode] = Field(default_factory=dict)


class GetCategoryTreeResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetCategoryTreeData | None = None
    reason: str | None = None


# ---- get_category_metadata ----


class CategoryMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    categoryConfig: dict[str, Any] = Field(default_factory=dict)
    categorySaleAttrList: list[dict[str, Any]] = Field(default_factory=list)
    categoryProductAttrList: list[dict[str, Any]] = Field(default_factory=list)


class GetCategoryMetadataData(BaseModel):
    categoryMetadata: CategoryMetadata | None = None


class GetCategoryMetadataResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetCategoryMetadataData | None = None
    reason: str | None = None


# ---- get_shop_warehouse_list ----


class Warehouse(BaseModel):
    model_config = ConfigDict(extra="allow")
    shopId: str | None = None
    warehouseId: str | None = None
    warehouseName: str | None = None
    warehouseSubType: str | None = None
    warehouseEffectStatus: str | None = None
    isDefault: str | None = None
    inventoryRule: dict[str, Any] = Field(default_factory=dict)


class ShopWarehouse(BaseModel):
    model_config = ConfigDict(extra="allow")
    shopId: int
    shopName: str | None = None
    platform: str | None = None
    site: str | None = None
    warehouseList: list[Warehouse] = Field(default_factory=list)


class GetShopWarehouseListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    shopWarehouseList: list[ShopWarehouse] = Field(default_factory=list)


class GetShopWarehouseListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetShopWarehouseListData | None = None
    reason: str | None = None


# ---- search_collect_box_list ----


class CollectBoxDetailShopInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    shopId: str | None = None


class SearchCollectBoxDetailData(BaseModel):
    """妙手 search_collect_box_list 的 data 字段.

    API 实际返回：data 是单 object（含当前 item 的扁平字段） + data.detailList 是整页 list。
    这里只保留 detailList（同步需要），其他扁平字段靠 extra="allow" 透传不报错。
    """
    model_config = ConfigDict(extra="allow")
    detailList: list["CollectBoxDetailDetail"] = Field(default_factory=list)

class CollectBoxDetailDetail(BaseModel):
    """公共采集箱详情项（list 元素，18 字段全在顶层）."""
    model_config = ConfigDict(extra="allow")
    collectBoxDetailId: str | None = None
    itemNum: str | None = None
    stock: str | None = None
    price: str | None = None
    thumbnail: str | None = None
    listThumbnail: str | None = None
    gmtCreate: str | None = None
    editModel: str | None = None
    commonCollectBoxDetailId: str | None = None
    appAccountId: str | None = None
    subAppAccountId: str | None = None
    platform: str | None = None
    title: str | None = None
    remark: str | None = None
    copyType: str | None = None
    collectBoxGroupId: str | None = None
    collectBoxGroupName: str | None = None
    collectBoxDetailShopList: list[dict] | None = None
    isSupportReplicateProduct: bool | None = None


class SearchCollectBoxDetailResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: SearchCollectBoxDetailData | None = None
    reason: str | None = None


# ---- get_shop_collect_item_info / get_site_collect_item_info ----


class GetCollectItemInfoData(BaseModel):
    """get_shop_collect_item_info + get_site_collect_item_info 共用."""

    model_config = ConfigDict(extra="allow")
    ossMd5: str | None = None
    editModel: str | None = None
    claimToShopIds: list[int] = Field(default_factory=list)
    isSupportMultiWarehouse: int | None = None
    shopCollectItemInfo: dict[str, Any] = Field(default_factory=dict)
    siteCollectItemInfo: dict[str, Any] = Field(default_factory=dict)


class GetShopCollectItemInfoResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetCollectItemInfoData | None = None
    reason: str | None = None


# ---- save_shop_collect_item_info ----


class SaveShopCollectItemInfoData(BaseModel):
    ossMd5: str = ""


class SaveShopCollectItemInfoResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: SaveShopCollectItemInfoData | None = None
    reason: str | None = None


# ---- get_responsible_person_list ----


class ResponsiblePerson(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    name: str | None = None


class GetResponsiblePersonListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    responsiblePersonList: list[ResponsiblePerson] = Field(default_factory=list)


class GetResponsiblePersonListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetResponsiblePersonListData | None = None
    reason: str | None = None


# ---- claim_to_shop ----


class ClaimToShopResult(BaseModel):
    """SimpleSuccessResponse."""

    model_config = ConfigDict(extra="allow")
    result: str
    code: str


# ---- translate_collect_item_info ----


class TranslateCollectItemInfoData(BaseModel):
    model_config = ConfigDict(extra="allow")
    collectItemInfo: dict[str, Any] = Field(default_factory=dict)


class TranslateCollectItemInfoResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: TranslateCollectItemInfoData | None = None
    reason: str | None = None


# ---- ai_match_cid_by_shop_ids ----


class AiMatchCidData(BaseModel):
    model_config = ConfigDict(extra="allow")
    shopIdAndCidMap: dict[str, int] = Field(default_factory=dict)
    failedShopIds: list[int] = Field(default_factory=list)


class AiMatchCidResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: AiMatchCidData | None = None
    reason: str | None = None


# ---- delete_collect_box_detail ----


class DeleteCollectBoxDetailData(BaseModel):
    model_config = ConfigDict(extra="allow")
    successNum: int | None = None
    failNum: int | None = None
    errorMap: dict[str, str] = Field(default_factory=dict)
    errorMsg: str | None = None


class DeleteCollectBoxDetailResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: DeleteCollectBoxDetailData | None = None
    reason: str | None = None


# ---- ai_match_product_attribute ----


class AiMatchProductAttributeData(BaseModel):
    model_config = ConfigDict(extra="allow")
    productAttributes: list[dict[str, Any]] = Field(default_factory=list)


class AiMatchProductAttributeResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: AiMatchProductAttributeData | None = None
    reason: str | None = None


# ---- get_brand_list ----


class BrandInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    authorizedStatus: str | None = None
    brandId: str | None = None
    brandName: str | None = None


class GetBrandListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    brandList: list[BrandInfo] = Field(default_factory=list)
    nextPageToken: str | None = None
    totalCount: int | None = None


class GetBrandListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetBrandListData | None = None
    reason: str | None = None


# ---- search_move_collect_list ----


class MoveCollectDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    moveCollectTaskDetailId: str | None = None
    collectBoxDetailId: str | None = None
    shopId: str | None = None
    itemNum: str | None = None
    cid: str | None = None
    source: str | None = None
    sourceSite: str | None = None
    sourceItemId: str | None = None
    title: str | None = None
    thumbnail: str | None = None
    isTiming: str | None = None
    status: str | None = None
    reason: str | None = None
    gmtCreate: str | None = None
    gmtModified: str | None = None
    platformItemId: str | None = None
    isRenewItem: bool | None = None
    shopName: str | None = None
    siteName: str | None = None
    site: str | None = None
    sourceItemUrl: str | None = None
    itemEditUrl: str | None = None
    breadcrumb: str | None = None
    ownerSubAppAccountId: int | None = None
    ownerSubAccountAliasName: str | None = None


class SearchMoveCollectListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    moveCollectDetailList: list[MoveCollectDetail] = Field(default_factory=list)
    total: int | None = None


class SearchMoveCollectListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: SearchMoveCollectListData | None = None
    reason: str | None = None


# ---- get_price_template_list ----


class PriceTemplateInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    priceTemplateId: int | None = None
    appAccountId: int | None = None
    subAppAccountId: int | None = None
    platform: str | None = None
    site: str | None = None
    name: str | None = None
    remark: str | None = None
    currency: str | None = None
    displayWeightUnit: str | None = None
    profitType: str | None = None
    profitPercent: float | None = None
    fixedProfitAmount: float | None = None
    exchangeRate: float | None = None
    discount: float | None = None
    priceTailComputeType: str | None = None
    priceTail: str | None = None
    priceProcessDecimalType: str | None = None
    logisticsComputeType: str | None = None
    weightRefType: str | None = None
    firstWeightCharge: float | None = None
    firstWeightInterval: float | None = None
    continuedWeightCharge: float | None = None
    continuedWeightInterval: float | None = None
    logisticsCharge: float | None = None
    platformChargePercent: float | None = None
    paymentChargePercent: float | None = None
    activityChargePercent: float | None = None
    withdrawChargePercent: float | None = None
    otherCharge: float | None = None
    isCalLightCargo: int | None = None
    lightCargoCoefficient: int | None = None
    weightLogisticsChargeList: str | None = None
    domesticLogisticsComputeType: str | None = None
    domesticLogisticsFirstWeightCharge: float | None = None
    domesticLogisticsFirstWeightInterval: float | None = None
    domesticLogisticsContinuedWeightCharge: float | None = None
    domesticLogisticsContinuedWeightInterval: float | None = None
    domesticLogisticsCharge: float | None = None
    buyerLogisticCharge: float | None = None
    sellerLogisticCharge: float | None = None
    hasSellerLogisticCharge: int | None = None
    officialTplMode: str | None = None
    officialTplLogisticsChannel: str | None = None
    snapshotId: int | None = None
    gmtCreate: str | None = None
    gmtModified: str | None = None


class GetPriceTemplateListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    priceTemplateList: list[PriceTemplateInfo] = Field(default_factory=list)
    total: int | None = None
    pageNo: int | None = None
    pageSize: int | None = None


class GetPriceTemplateListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetPriceTemplateListData | None = None
    reason: str | None = None


# ---- get_site_default_setting ----


class GetSiteDefaultSettingData(BaseModel):
    model_config = ConfigDict(extra="allow")
    siteAndBuyerLogisticDefaultChargeMap: dict[str, float] = Field(default_factory=dict)
    siteAndBuyerLogisticDefaultChargeCNYMap: dict[str, float] = Field(
        default_factory=dict
    )
    siteAndDefaultPlatformInfrastructureChargeMap: dict[str, float] = Field(
        default_factory=dict
    )
    siteAndDefaultPlatformInfrastructureChargeCNYMap: dict[str, float] = Field(
        default_factory=dict
    )
    tiktokCbSiteAndDefaultChargePercentMap: dict[str, Any] = Field(default_factory=dict)
    tiktokLocalDefaultChargePercentMap: dict[str, float] = Field(default_factory=dict)
    siteAndDefaultPlatformSupportChargeMap: dict[str, float] = Field(
        default_factory=dict
    )
    siteAndDefaultPlatformSupportChargeCNYMap: dict[str, float] = Field(
        default_factory=dict
    )


class GetSiteDefaultSettingResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetSiteDefaultSettingData | None = None
    reason: str | None = None


# ---- translate_image ----


class TranslateImageUrlResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    oriImageUrl: str | None = None
    newImageUrl: str | None = None
    result: str | None = None


class TranslateImageErrorResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    failImageCount: int | None = None
    failImageUrls: list[str] = Field(default_factory=list)
    reason: str | None = None


class TranslateImageData(BaseModel):
    model_config = ConfigDict(extra="allow")
    translateImageUrlResultList: list[TranslateImageUrlResult] = Field(
        default_factory=list
    )
    translateImageUrlErrorResultList: list[TranslateImageErrorResult] = Field(
        default_factory=list
    )


class TranslateImageResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: TranslateImageData | None = None
    reason: str | None = None


# ============================================================
# Endpoint class
# ============================================================


class TkCollectBoxEndpoint:
    """妙手 ERP 开放平台 · TK 采集箱 + 产品发布 + AI 公共 endpoint."""

    PATH_PREFIX = "/open/v1/product/collect_box/tiktok/collect_box"

    def __init__(self, client: MiaoshouErpClient) -> None:
        self._c = client

    # ---- api-446814588 ----

    def get_category_tree(
        self,
        *,
        site: str,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetCategoryTreeResult:
        if not site:
            raise ValueError("site must not be empty")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_category_tree_by_site",
            body={"site": site},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetCategoryTreeResult)

    # ---- api-446814589 ----

    def get_category_metadata(
        self,
        *,
        cid: int,
        site: str | None = None,
        shop_ids: list[int] | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetCategoryMetadataResult:
        body: dict[str, Any] = {"cid": cid}
        if site is not None:
            body["site"] = site
        if shop_ids is not None:
            body["shopIds"] = shop_ids
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_category_metadata",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetCategoryMetadataResult)

    # ---- api-446814590 ----

    def get_shop_warehouse_list(
        self,
        *,
        shop_ids: list[int],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetShopWarehouseListResult:
        if not shop_ids:
            raise ValueError("shop_ids must not be empty")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_shop_warehouse_list",
            body={"shopIds": shop_ids},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetShopWarehouseListResult)

    # ---- api-446814591 ----

    def search_collect_box_list(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        status: str | None = None,
        source_item_id_keyword: str | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> SearchCollectBoxDetailResult:
        if page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page_no}")
        if page_size < 1 or page_size > 500:
            raise ValueError(f"page_size must be 1..500, got {page_size}")
        body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if status is not None or source_item_id_keyword is not None:
            filter_obj: dict[str, Any] = {}
            if status is not None:
                filter_obj["status"] = status
            if source_item_id_keyword is not None:
                filter_obj["sourceItemIdKeyword"] = source_item_id_keyword
            body["filter"] = filter_obj
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/search_collect_box_detail_list",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, SearchCollectBoxDetailResult)

    # ---- api-446814592 ----

    def get_shop_collect_item_info(
        self,
        *,
        detail_id: int,
        shop_id: int,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetShopCollectItemInfoResult:
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_shop_collect_item_info",
            body={"detailId": detail_id, "shopId": shop_id},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetShopCollectItemInfoResult)

    # ---- api-446814593 ----

    def save_shop_collect_item_info(
        self,
        *,
        oss_md5: str,
        detail_id: int,
        shop_id: int,
        shop_collect_item_info: dict[str, Any],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> SaveShopCollectItemInfoResult:
        """保存采集箱店铺模式详情.

        Args:
            shop_collect_item_info: 完整的 shopCollectItemInfo 字典
                （含 title/notes/imgUrls/weight/packageLength/.../skuMap/skuPropertyList/...）
        """
        body: dict[str, Any] = {
            "ossMd5": oss_md5,
            "shopCollectItemInfo": shop_collect_item_info,
            "detailId": detail_id,
            "shopId": shop_id,
        }
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/save_shop_collect_item_info",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, SaveShopCollectItemInfoResult)

    # ---- api-449869293 ----

    def get_responsible_person_list(
        self,
        *,
        shop_id: int,
        refresh: int = 0,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetResponsiblePersonListResult:
        if refresh not in (0, 1):
            raise ValueError(f"refresh must be 0 or 1, got {refresh}")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_responsible_person_list",
            body={"shopId": shop_id, "refresh": refresh},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetResponsiblePersonListResult)

    # ---- api-446814594 ----

    def claim_to_shop(
        self,
        *,
        shop_ids: list[int],
        detail_ids: list[int],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> ClaimToShopResult:
        if not shop_ids:
            raise ValueError("shop_ids must not be empty")
        if len(shop_ids) > 200:
            raise ValueError(f"shop_ids max 200, got {len(shop_ids)}")
        if not detail_ids:
            raise ValueError("detail_ids must not be empty")
        if len(detail_ids) > 200:
            raise ValueError(f"detail_ids max 200, got {len(detail_ids)}")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/claim_to_shop",
            body={"shopIds": shop_ids, "detailIds": detail_ids},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, ClaimToShopResult)

    # ---- api-449650816 ----

    def get_site_collect_item_info(
        self,
        *,
        detail_id: int,
        site: str,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetShopCollectItemInfoResult:
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_site_collect_item_info",
            body={"detailId": detail_id, "site": site},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetShopCollectItemInfoResult)

    # ---- api-482189160 ----

    def translate_collect_item_info(
        self,
        *,
        source_language: str,
        target_language: str,
        collect_item_info: dict[str, Any],
        is_translate_sku_item_num: int,
        is_translate_sku_property: int,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> TranslateCollectItemInfoResult:
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/translate_collect_item_info",
            body={
                "sourceLanguage": source_language,
                "targetLanguage": target_language,
                "collectItemInfo": collect_item_info,
                "isTranslateSkuItemNum": is_translate_sku_item_num,
                "isTranslateSkuProperty": is_translate_sku_property,
            },
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, TranslateCollectItemInfoResult)

    # ---- api-482189162 ----

    def ai_match_cid_by_shop_ids(
        self,
        *,
        collect_box_detail_id: int,
        shop_ids: list[int],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> AiMatchCidResult:
        if not shop_ids:
            raise ValueError("shop_ids must not be empty")
        if len(shop_ids) > 3:
            raise ValueError(f"shop_ids max 3, got {len(shop_ids)}")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/ai_match_cid_by_shop_ids",
            body={"collectBoxDetailId": collect_box_detail_id, "shopIds": shop_ids},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, AiMatchCidResult)

    # ---- api-496804330 ----

    def delete_collect_box_detail(
        self,
        *,
        detail_ids: list[int],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> DeleteCollectBoxDetailResult:
        if not detail_ids:
            raise ValueError("detail_ids must not be empty")
        if len(detail_ids) > 200:
            raise ValueError(f"detail_ids max 200, got {len(detail_ids)}")
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/delete_collect_box_detail",
            body={"detailIds": detail_ids},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, DeleteCollectBoxDetailResult)

    # ---- api-482189161 ----

    def ai_match_product_attribute(
        self,
        *,
        collect_box_detail_id: int,
        cid: str,
        site: str,
        product_attributes: list[dict[str, Any]],
        shop_ids: list[int] | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> AiMatchProductAttributeResult:
        body: dict[str, Any] = {
            "collectBoxDetailId": collect_box_detail_id,
            "cid": cid,
            "site": site,
            "productAttributes": product_attributes,
        }
        if shop_ids is not None:
            body["shopIds"] = shop_ids
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/ai_match_product_attribute",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, AiMatchProductAttributeResult)

    # ---- api-497001483 ----

    def get_brand_list(
        self,
        *,
        shop_id: int,
        brand_name: str | None = None,
        next_page_token: str | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetBrandListResult:
        body: dict[str, Any] = {"shopId": shop_id}
        if brand_name is not None:
            body["brandName"] = brand_name
        if next_page_token is not None:
            body["nextPageToken"] = next_page_token
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_brand_list",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetBrandListResult)

    # ---- api-482189163 ----

    def search_move_collect_list(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        status: str | None = None,
        item_id: str | None = None,
        source_item_id: str | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> SearchMoveCollectListResult:
        if page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page_no}")
        if page_size < 1 or page_size > 20:
            raise ValueError(f"page_size must be 1..20, got {page_size}")
        body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if status is not None or item_id is not None or source_item_id is not None:
            filter_obj: dict[str, Any] = {}
            if status is not None:
                filter_obj["status"] = status
            if item_id is not None:
                filter_obj["itemId"] = item_id
            if source_item_id is not None:
                filter_obj["sourceItemId"] = source_item_id
            body["filter"] = filter_obj
        payload = self._c._call_erp(
            path="/open/v1/product/collect_box/tiktok/move_collect/search_move_collect_list",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, SearchMoveCollectListResult)

    # ---- api-482189164 ----

    def get_price_template_list(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        name: str | None = None,
        site: str | None = None,
        site_type: str | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetPriceTemplateListResult:
        if page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page_no}")
        if page_size < 1 or page_size > 20:
            raise ValueError(f"page_size must be 1..20, got {page_size}")
        body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if name is not None:
            body["name"] = name
        if site is not None:
            body["site"] = site
        if site_type is not None:
            body["siteType"] = site_type
        payload = self._c._call_erp(
            path="/open/v1/product/collect_box/tiktok/price_template/get_price_template_list",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetPriceTemplateListResult)

    # ---- api-482189165 ----

    def get_site_default_setting(
        self,
        *,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetSiteDefaultSettingResult:
        payload = self._c._call_erp(
            path="/open/v1/product/collect_box/tiktok/price_template/get_site_default_setting",
            body=None,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetSiteDefaultSettingResult)

    # ---- api-484286514 ----

    def translate_image(
        self,
        *,
        image_urls: list[str],
        source_lang: str,
        target_lang: str,
        translate_platform: str,
        no_translate_image_text_options: list[str] | None = None,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> TranslateImageResult:
        body: dict[str, Any] = {
            "imageUrls": image_urls,
            "sourceLang": source_lang,
            "targetLang": target_lang,
            "translatePlatform": translate_platform,
        }
        if no_translate_image_text_options is not None:
            body["noTranslateImageTextOptions"] = no_translate_image_text_options
        payload = self._c._call_erp(
            path="/open/v1/product/common/translate/translate_image",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, TranslateImageResult)

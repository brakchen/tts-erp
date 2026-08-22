"""妙手 ERP 开放平台 · 开放平台/商品/公共采集箱.

Endpoints:
- fetch_item()        通过货源链接采集货源       apifox api-446814581
- create_product()    创建公共采集箱产品         apifox api-446814585
- list_boxes()        获取公共采集箱列表         apifox api-446814582
- get_box_detail()    获取公共采集箱详情         apifox api-446814583
- batch_delete()      批量删除公共采集箱产品     apifox api-446814584
- claim_to_platform() 认领到平台采集箱           apifox api-446814587
- edit_box_product()  编辑公共采集箱产品         apifox api-446814586
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..miaoshou_client import MiaoshouApiError  # type: ignore[reportMissingImports]
from ..miaoshou_erp_client import (
    MiaoshouErpClient,
    _safe_validate,  # type: ignore[reportMissingImports]
)

# ---- fetch_item ----


class FetchItemData(BaseModel):
    """货源链接采集结果: 来源商品ID → 公共采集箱ID."""

    model_config = ConfigDict(extra="allow")
    sourceItemIdAndDetailIdMap: dict[str, int] = Field(default_factory=dict)


class FetchItemResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: FetchItemData | None = None
    reason: str | None = None


# ---- create_product ----


class CreateCollectBoxData(BaseModel):
    commonCollectBoxDetailId: int


class CreateCollectBoxResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: CreateCollectBoxData | None = None
    reason: str | None = None


# ---- list_boxes ----


class CommonCollectBoxDetail(BaseModel):
    """单个公共采集箱产品详情（list/get response）."""

    model_config = ConfigDict(extra="allow")
    commonCollectBoxDetailId: int
    appAccountId: int | None = None
    subAppAccountId: int | None = None
    itemNum: str | None = None
    title: str | None = None
    thumbnail: str | None = None
    listThumbnail: str | None = None
    price: float | None = None
    minSkuPrice: float | None = None
    maxSkuPrice: float | None = None
    stock: int | None = None
    remark: str | None = None
    status: str | None = None
    reason: str | None = None
    gmtCreate: str | None = None
    gmtModified: str | None = None
    weight: float | None = None
    maxSkuWeight: float | None = None
    minSkuWeight: float | None = None
    commonCollectBoxGroupId: int | None = None
    commonCollectBoxGroupName: str | None = None
    ownerSubAccountAliasName: str | None = None
    isMark: str | None = None
    isCb: int | None = None
    isCnsc: int | None = None


class CommonCollectBoxListData(BaseModel):
    model_config = ConfigDict(extra="allow")
    detailList: list[CommonCollectBoxDetail] = Field(default_factory=list)
    total: int = 0
    isCommonCollectHuger: bool = False


class CommonCollectBoxListResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: CommonCollectBoxListData | None = None
    reason: str | None = None


# ---- get_box_detail ----


class GetBoxDetailData(BaseModel):
    model_config = ConfigDict(extra="allow")
    editCommonCollectBoxDetail: dict[str, Any] = Field(default_factory=dict)
    ossMd5: str = ""


class GetBoxDetailResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: GetBoxDetailData | None = None
    reason: str | None = None


# ---- edit_box_product ----


class EditBoxProductData(BaseModel):
    ossMd5: str = ""


class EditBoxProductResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: EditBoxProductData | None = None
    reason: str | None = None


# ---- batch_delete ----


class BatchDeleteResult(BaseModel):
    """批量删除响应（SimpleSuccessResponse: result+code，无 data 字段）."""

    model_config = ConfigDict(extra="allow")
    result: str
    code: str


# ---- claim_to_platform ----


class ClaimedData(BaseModel):
    """认领到平台采集箱响应.

    platformCollectBoxDetailIdMap: 平台 → { collectBoxId → detailId }
    """

    model_config = ConfigDict(extra="allow")
    platformCollectBoxDetailIdMap: dict[str, dict[str, int]] = Field(
        default_factory=dict
    )


class ClaimedResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    code: str
    data: ClaimedData | None = None
    reason: str | None = None


# ---- 公共字段（create + edit 共用）----

_PRODUCT_OPTIONAL_FIELDS: dict[str, str] = {
    "itemNum": "itemNum",
    "notesText": "notesText",
    "notes": "notes",
    "sourceAttrs": "sourceAttrs",
    "price": "price",
    "stock": "stock",
    "imgUrls": "imgUrls",
    "weight": "weight",
    "packageLength": "packageLength",
    "packageWidth": "packageWidth",
    "packageHeight": "packageHeight",
    "colorPropName": "colorPropName",
    "colorMap": "colorMap",
    "sizePropName": "sizePropName",
    "sizeMap": "sizeMap",
    "saleProp3Name": "saleProp3Name",
    "saleProp3Map": "saleProp3Map",
    "skuMap": "skuMap",
    "sizeChart": "sizeChart",
    "mainImgVideoUrl": "mainImgVideoUrl",
    "mainImgAppVideoId": "mainImgAppVideoId",
    "productCertifications": "productCertifications",
    "guideInfo": "guideInfo",
    "sourceList": "sourceList",
}


def _build_product_body(
    title: str,
    item_num: str | None = None,
    notes_text: str | None = None,
    notes: str | None = None,
    source_attrs: list[dict[str, Any]] | None = None,
    price: float | None = None,
    stock: int | None = None,
    img_urls: list[str] | None = None,
    weight: float | None = None,
    package_length: float | None = None,
    package_width: float | None = None,
    package_height: float | None = None,
    color_prop_name: str | None = None,
    color_map: dict[str, Any] | None = None,
    size_prop_name: str | None = None,
    size_map: dict[str, Any] | None = None,
    sale_prop3_name: str | None = None,
    sale_prop3_map: dict[str, Any] | None = None,
    sku_map: dict[str, Any] | None = None,
    size_chart: str | None = None,
    main_img_video_url: str | None = None,
    main_img_app_video_id: str | None = None,
    product_certifications: list[dict[str, Any]] | None = None,
    guide_info: dict[str, Any] | None = None,
    source_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 create / edit 公共采集箱产品的 body（title 必填，其余可选）."""
    body: dict[str, Any] = {"title": title}
    optional_values: dict[str, Any] = {
        "itemNum": item_num,
        "notesText": notes_text,
        "notes": notes,
        "sourceAttrs": source_attrs,
        "price": price,
        "stock": stock,
        "imgUrls": img_urls,
        "weight": weight,
        "packageLength": package_length,
        "packageWidth": package_width,
        "packageHeight": package_height,
        "colorPropName": color_prop_name,
        "colorMap": color_map,
        "sizePropName": size_prop_name,
        "sizeMap": size_map,
        "saleProp3Name": sale_prop3_name,
        "saleProp3Map": sale_prop3_map,
        "skuMap": sku_map,
        "sizeChart": size_chart,
        "mainImgVideoUrl": main_img_video_url,
        "mainImgAppVideoId": main_img_app_video_id,
        "productCertifications": product_certifications,
        "guideInfo": guide_info,
        "sourceList": source_list,
    }
    for k, v in optional_values.items():
        if v is not None:
            body[k] = v
    return body


def _check_erp(payload: dict[str, Any]) -> dict[str, Any]:
    """检查妙手 ERP 响应的 result 字段，失败抛 MiaoshouApiError.

    result 字段 case-insensitive（妙手服务端可能返回 "Success" 或 "SUCCESS"）.
    """
    result = str(payload.get("result", "")).strip().lower()
    if result != "success":
        raise MiaoshouApiError(
            code=500,
            message=f"{payload.get('result', '')}: {payload.get('reason', 'unknown error')}",
            data=payload,
        )
    return payload


def _debug_headers(debug: bool) -> dict[str, str] | None:
    return {"X-Apifox-Debug": "1"} if debug else None


def _timer_query(timer_token: str | None) -> dict[str, Any] | None:
    return {"timerToken": timer_token} if timer_token else None


# ---- Endpoint class ----


class CollectionBoxEndpoint:
    """妙手 ERP 开放平台 · 公共采集箱 endpoint."""

    PATH_PREFIX = "/open/v1/product/common_collect_box/common_collect_box"

    def __init__(self, client: MiaoshouErpClient) -> None:
        self._c = client

    # ---- api-446814581 ----

    def fetch_item(
        self,
        *,
        collect_links: list[str],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> FetchItemResult:
        if not collect_links:
            raise ValueError("collect_links must not be empty")
        if len(collect_links) > 50:
            raise ValueError(f"collect_links max 50, got {len(collect_links)}")

        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/fetch_item",
            body={"collectLinks": collect_links},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, FetchItemResult)

    # ---- api-446814585 ----

    def create_product(
        self,
        *,
        title: str,
        timer_token: str | None = None,
        debug: bool = False,
        **product_fields: Any,
    ) -> CreateCollectBoxResult:
        body = _build_product_body(title=title, **product_fields)
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/add_common_collect_box_detail",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, CreateCollectBoxResult)

    # ---- api-446814582 ----

    def list_boxes(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        tab_pane_name: str | None = None,
        source_item_id_keyword: str | None = None,
        debug: bool = False,
    ) -> CommonCollectBoxListResult:
        if page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page_no}")
        if page_size < 1 or page_size > 500:
            raise ValueError(f"page_size must be 1..500, got {page_size}")

        body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if tab_pane_name is not None or source_item_id_keyword is not None:
            filter_obj: dict[str, Any] = {}
            if tab_pane_name is not None:
                filter_obj["tabPaneName"] = tab_pane_name
            if source_item_id_keyword is not None:
                filter_obj["sourceItemIdKeyword"] = source_item_id_keyword
            body["filter"] = filter_obj

        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_common_collect_box_list",
            body=body,
            query=_timer_query(None),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, CommonCollectBoxListResult)

    # ---- api-446814583 ----

    def get_box_detail(
        self,
        *,
        common_collect_box_detail_id: int,
        timer_token: str | None = None,
        debug: bool = False,
    ) -> GetBoxDetailResult:
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/get_common_collect_box_detail",
            body={"commonCollectBoxDetailId": common_collect_box_detail_id},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, GetBoxDetailResult)

    # ---- api-446814586 ----

    def edit_box_product(
        self,
        *,
        common_collect_box_detail_id: int,
        oss_md5: str,
        title: str,
        timer_token: str | None = None,
        debug: bool = False,
        **product_fields: Any,
    ) -> EditBoxProductResult:
        body: dict[str, Any] = {
            "commonCollectBoxDetailId": common_collect_box_detail_id,
            "editCommonCollectBoxDetail": _build_product_body(
                title=title, **product_fields
            ),
            "ossMd5": oss_md5,
        }
        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/edit_common_collect_box_detail",
            body=body,
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, EditBoxProductResult)

    # ---- api-446814584 ----

    def batch_delete(
        self,
        *,
        common_collect_box_detail_ids: list[int],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> BatchDeleteResult:
        if not common_collect_box_detail_ids:
            raise ValueError("common_collect_box_detail_ids must not be empty")
        if len(common_collect_box_detail_ids) > 200:
            raise ValueError(f"max 200 ids, got {len(common_collect_box_detail_ids)}")

        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/batch_delete_common_collect_box_detail",
            body={"commonCollectBoxDetailIds": common_collect_box_detail_ids},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, BatchDeleteResult)

    # ---- api-446814587 ----

    def claim_to_platform(
        self,
        *,
        detail_serial_number_platform_list: list[dict[str, Any]],
        timer_token: str | None = None,
        debug: bool = False,
    ) -> ClaimedResult:
        if not detail_serial_number_platform_list:
            raise ValueError("detail_serial_number_platform_list must not be empty")
        if len(detail_serial_number_platform_list) > 100:
            raise ValueError(
                f"max 100 items, got {len(detail_serial_number_platform_list)}"
            )

        payload = self._c._call_erp(
            path=f"{self.PATH_PREFIX}/claimed",
            body={"detailSerialNumberPlatformList": detail_serial_number_platform_list},
            query=_timer_query(timer_token),
            extra_headers=_debug_headers(debug),
        )
        return _safe_validate(payload, ClaimedResult)

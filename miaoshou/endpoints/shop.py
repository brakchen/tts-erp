"""妙手 ERP 开放平台 · 开放平台/商品/店铺.

Endpoints:
- list()  获取店铺数据列表  apifox api-446814596
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..miaoshou_client import MiaoshouApiError  # type: ignore[reportMissingImports]
from ..miaoshou_erp_client import MiaoshouErpClient, _safe_validate


class Shop(BaseModel):
    """单个店铺.

    字段命名严格遵循 apifox api-446814596 响应 schema（驼峰）。
    """

    model_config = ConfigDict(extra="allow")

    shopId: int
    site: str
    siteName: str | None = None
    platformShopName: str | None = None
    shopNick: str | None = None
    platform: str
    parentShopId: int | None = None
    isCb: int | None = None  # 0=否 1=是（是否跨境）
    isCnsc: int | None = None  # 0=否 1=是（是否全球店铺）
    status: str | None = None
    gmtExpire: str | None = None
    gmtLastAuth: str | None = None


class ShopListData(BaseModel):
    shopList: list[Shop] = Field(default_factory=list)


class ShopListResult(BaseModel):
    """获取店铺数据列表的完整响应（apifox api-446814596）。"""

    model_config = ConfigDict(extra="allow")

    result: str
    code: str
    data: ShopListData | None = None
    reason: str | None = None


def _check_erp(payload: dict) -> dict:
    """检查妙手 ERP 响应的 result 字段，失败抛 MiaoshouApiError."""
    result = str(payload.get("result", ""))
    if result != "success":
        raise MiaoshouApiError(
            500,
            f"{result}: {payload.get('reason', 'unknown error')}",
            payload,
        )
    return payload


class ShopEndpoint:
    """妙手 ERP 开放平台 · 店铺 endpoint."""

    def __init__(self, client: MiaoshouErpClient) -> None:
        self._c = client

    def list(
        self,
        *,
        platform: str,
        site: str,
        page_no: int = 1,
        page_size: int = 20,
    ) -> ShopListResult:
        """获取店铺数据列表 · apifox api-446814596.

        path:  ``POST /open/v1/product/shop/shop/get_shop_list``
        body:  ``{"platform", "site", "pageNo", "pageSize"}``（4 个必填）

        Args:
            platform: 平台代号。常见值:
                - ``tiktok`` / ``tiktokGlobal``（TK 普通 / TK 全球）
                - ``shopee`` / ``shopeeGlobal``（虾皮 / 虾皮全球）
                - ``mercadolibre``（美客多）
                - ``ozon``
                - ``pddkj`` / ``pddkjChoice``（TEMU 全托 / TEMU 半托）
            site: 站点代号。TikTok 越南 = ``VN``，印尼 = ``ID``，美国 = ``US`` 等等
            page_no: 当前页码（>=1）
            page_size: 每页数量（1..100）

        Returns:
            ShopListResult: ``data.shopList`` 是店铺列表

        Raises:
            MiaoshouApiError: 当 ``result != 'success'`` 时
            ValueError: 参数越界
        """
        if page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page_no}")
        if page_size < 1 or page_size > 100:
            raise ValueError(f"page_size must be 1..100, got {page_size}")

        # 分页循环拿全（默认 max_pages=10 防呆）
        max_pages = 10
        all_shops: list[dict] = []
        current_page = page_no
        payload: dict = {}
        data: dict = {}
        for _ in range(max_pages):
            payload = self._c._call_erp(
                path="/open/v1/product/shop/shop/get_shop_list",
                body={
                    "platform": platform,
                    "site": site,
                    "pageNo": current_page,
                    "pageSize": page_size,
                },
            )
            from .collection_box import _check_erp as _ce
            _ce(payload)
            data = payload.get("data") or {}
            batch = data.get("shopList") or []
            if not batch:
                break  # 空页 → 结束
            all_shops.extend(batch)
            if len(batch) < page_size:
                break  # 最后一页不满 → 结束
            current_page += 1
        # 构造最终响应（保留原始 result/code）
        merged = {**payload, "data": {**data, "shopList": all_shops}}
        return _safe_validate(merged, ShopListResult)

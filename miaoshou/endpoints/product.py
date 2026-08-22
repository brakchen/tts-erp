"""商品库查询 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class ProductEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def query_user_goods(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sku: str | None = None,
        **extra: Any,
    ) -> MiaoshouApiResponse:
        """商品库查询 · doc-4180787."""
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if sku:
            params["sku"] = sku
        params.update(extra)
        return self._c._call(
            path="/user-open/order/queryUserGoods", business_params=params
        )

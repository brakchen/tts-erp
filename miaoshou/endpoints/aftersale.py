"""售后单 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class AftersaleEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def create_aftersale_order(self, **payload: Any) -> MiaoshouApiResponse:
        """创建售后单 · doc-4560094. payload 字段见 doc 表格。"""
        return self._c._call(
            path="/user-open/order/createAfterSaleOrder", business_params=payload
        )

    def fetch_aftersale_order(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        page_size: int = 20,
        **extra: Any,
    ) -> MiaoshouApiResponse:
        """拉取售后单 · doc-4560106."""
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        params.update(extra)
        return self._c._call(
            path="/user-open/order/getAfterSaleOrder", business_params=params
        )

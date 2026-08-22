"""物流 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class LogisticsEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def order_arrived_sync(self, **payload: Any) -> MiaoshouApiResponse:
        """物流到货信息更新 · doc-802856. payload 字段见 doc 表格。"""
        return self._c._call(
            path="/user-open/logistics/orderArrivedSync", business_params=payload
        )

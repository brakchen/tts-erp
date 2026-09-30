"""Typed SDK coverage for the Miaoshou package query endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miaoshou.endpoints.package import (
    DETAIL_PATH,
    SEARCH_PATH,
    PackageEndpoint,
)
from miaoshou.miaoshou_client import MiaoshouApiError
from miaoshou.miaoshou_erp_client import MiaoshouErpClient

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]


def test_search_uses_documented_body_and_parses_live_shape() -> None:
    client = MagicMock()
    client._call_erp.return_value = {
        "result": "success",
        "code": "success",
        "message": "",
        "data": {
            "orderPackageList": [
                {
                    "opOrderPackageId": "1947370063",
                    "appPackageStatus": "wait_seller_send",
                    "orderInfo": {
                        "platformOrderSn": "586302066965120850",
                        "currency": "VND",
                    },
                    "items": [
                        {
                            "platformOrderItemIndex": "line-1",
                            "platformItemId": 1736929955366339831,
                            "quantity": 1,
                        }
                    ],
                }
            ],
            "total": 1,
            "page": 1,
            "pageSize": 100,
        },
    }

    result = PackageEndpoint(client).search(
        page=1,
        page_size=100,
        platform="tiktok",
        shop_ids=["17060852"],
        gmt_modified_from="2026-09-29 00:00:00",
    )

    client._call_erp.assert_called_once_with(
        path=SEARCH_PATH,
        body={
            "page": 1,
            "pageSize": 100,
            "gmtModifiedFrom": "2026-09-29 00:00:00",
            "shopIds": ["17060852"],
            "platform": "tiktok",
        },
    )
    assert result.data is not None
    assert result.data.total == 1
    assert result.data.orderPackageList[0].opOrderPackageId == "1947370063"
    assert result.data.orderPackageList[0].items[0].platformOrderItemIndex == "line-1"


def test_search_normalizes_documented_array_data_shape() -> None:
    client = MagicMock()
    client._call_erp.return_value = {
        "result": "success",
        "code": "success",
        "data": [
            {
                "orderPackageList": [{"opOrderPackageId": "1"}],
                "total": 2,
                "page": 1,
                "pageSize": 1,
            },
            {
                "orderPackageList": [{"opOrderPackageId": "2"}],
                "total": 2,
                "page": 2,
                "pageSize": 1,
            },
        ],
    }

    result = PackageEndpoint(client).search(page=1, page_size=1)

    assert result.data is not None
    assert [row.opOrderPackageId for row in result.data.orderPackageList] == ["1", "2"]
    assert result.data.total == 2


def test_get_info_uses_documented_path_and_parses_detail() -> None:
    client = MagicMock()
    client._call_erp.return_value = {
        "result": "success",
        "code": "success",
        "message": "",
        "data": {
            "orderPackageInfo": {
                "opOrderPackageId": "1947370063",
                "logisticsNo": "TRACK-1",
                "orderInfo": {"platformOrderSn": "ORDER-1"},
            }
        },
    }

    result = PackageEndpoint(client).get_info(op_order_package_id="1947370063")

    client._call_erp.assert_called_once_with(
        path=DETAIL_PATH,
        body={"opOrderPackageId": "1947370063"},
    )
    assert result.data is not None
    assert result.data.orderPackageInfo.logisticsNo == "TRACK-1"


def test_package_endpoint_rejects_invalid_arguments() -> None:
    endpoint = PackageEndpoint(MagicMock())
    with pytest.raises(ValueError, match="must not be empty"):
        endpoint.get_info(op_order_package_id=" ")
    with pytest.raises(ValueError, match="page must be"):
        endpoint.search(page=0)
    with pytest.raises(ValueError, match="page_size"):
        endpoint.search(page_size=101)


def test_package_endpoint_raises_business_failure() -> None:
    client = MagicMock()
    client._call_erp.return_value = {
        "result": "fail",
        "code": "INVALID_ARGUMENT",
        "message": "bad package",
    }

    with pytest.raises(MiaoshouApiError, match="bad package"):
        PackageEndpoint(client).search()


def test_erp_client_exposes_package_namespace() -> None:
    client = MiaoshouErpClient(
        app_id="TEST_app",
        app_secret="TEST_secret",
        base_url="https://example.invalid",
    )
    assert isinstance(client.packages, PackageEndpoint)

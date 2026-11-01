"""Optional image presentation must not take down catalogue/candidate reads."""

from __future__ import annotations

from decimal import Decimal
from types import ModuleType, SimpleNamespace

import pytest

from tts_erp_v2.api.v2 import commerce, reporting
from tts_erp_v2.storage.minio_client import MinioConfigError


def test_catalogue_dto_keeps_cost_facts_without_optional_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commerce, "_minio_client_singleton", None)

    def unconfigured() -> None:
        raise MinioConfigError("TEST_storage_not_configured")

    monkeypatch.setattr(commerce.MinioClient, "from_env", unconfigured)
    row = SimpleNamespace(
        id=7,
        shop_pk=3,
        spu_id="TEST_product",
        title="TEST title",
        status="ACTIVATE",
        source_created_at=None,
        source_updated_at=None,
        unit_cost=Decimal("12.5000"),
        currency="CNY",
        cost_method="MANUAL_ENTRY",
        source_unit_cost=Decimal("10.0000"),
        mirror_object_key="TEST_existing_mirror.jpg",
        main_image_url=None,
    )
    product = commerce._row_to_channel_product(row)
    assert product.id == 7
    assert product.unit_cost == Decimal("12.5000")
    assert product.source_unit_cost == Decimal("10.0000")
    assert product.image_url is None


@pytest.mark.parametrize("module", [commerce, reporting])
def test_optional_image_without_storage_config(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_minio_client_singleton", None)

    def unconfigured() -> None:
        raise MinioConfigError("TEST_storage_not_configured")

    monkeypatch.setattr(module.MinioClient, "from_env", unconfigured)
    assert module._resolve_mirror_url("TEST_existing_mirror.jpg") is None


@pytest.mark.parametrize("module", [commerce, reporting])
def test_configured_optional_image_keeps_the_public_url(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    client = SimpleNamespace(
        public_url_or_none=lambda _key: "https://test.invalid/TEST_image.jpg"
    )
    monkeypatch.setattr(module, "_minio_client_singleton", client)
    assert (
        module._resolve_mirror_url("TEST_existing_mirror.jpg")
        == "https://test.invalid/TEST_image.jpg"
    )


@pytest.mark.parametrize("module", [commerce, reporting])
def test_unexpected_image_errors_are_not_hidden(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_minio_client_singleton", None)

    def broken() -> None:
        raise RuntimeError("TEST_unexpected_failure")

    monkeypatch.setattr(module.MinioClient, "from_env", broken)
    with pytest.raises(RuntimeError, match="TEST_unexpected_failure"):
        module._resolve_mirror_url("TEST_existing_mirror.jpg")

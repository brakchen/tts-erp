"""XLSX import preserves identity, exact averages and transactional history."""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import HTTPException, Request
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tests.api.test_frontend_remaining_manual_identity import _facts
from tests.api.test_frontend_remaining_manual_identity import products as _products
from tts_erp_v2.api.v2 import manual_cost_import as routes
from tts_erp_v2.db.models import ChannelAccount, ChannelProduct
from tts_erp_v2.reporting.manual_cost_import import CostImportError, parse_cost_file

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
products = _products
BASE = "/v2/reporting/manual-costs/import/"
HEADERS = ["采购金额(产品-RMB)", "店铺名称", "产品ID"]
SHARED = "TEST_remaining_shared_spu"
UNIQUE = "TEST_remaining_unique_spu"
SHOP_A = "TEST remaining shop 0"
SHOP_B = "TEST remaining shop 1"


def xlsx(rows, headers=HEADERS):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def post(client, key, content, action="preview"):
    return client.post(BASE + action, content=content, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    })


@pytest.mark.layer_unit
def test_parser_preserves_text_ids_and_excludes_only_blank_amounts():
    parsed = parse_cost_file(xlsx([
        ["0.1", "TEST shop", "1730000000000000001"],
        ["0.2", "TEST shop", "1730000000000000001"],
        [None, "TEST shop", "1730000000000000001"],
        [0, "TEST shop", "TEST_zero"],
        ["=1+2", "TEST shop", "TEST_formula"],
    ]))
    assert parsed.total_rows == 5 and parsed.blank_rows == 1
    assert str(parsed.groups[0].total) == "0.3"
    assert parsed.groups[0].count == 2
    assert parsed.groups[0].spu_id == "1730000000000000001"
    assert parsed.groups[1].invalid and parsed.groups[2].invalid


@pytest.mark.layer_unit
def test_parser_rejects_rounded_numeric_ids_and_accepts_header_punctuation():
    parsed = parse_cost_file(xlsx([["12", "TEST", 1730000000000000001]],
                                  [" 采购金额（产品-RMB） ", "店铺名称", "产品ID，"]))
    assert not parsed.groups
    assert "文本" in parsed.issues[0]["reason"]


@pytest.mark.layer_unit
@pytest.mark.parametrize("content", [b"not an xlsx", xlsx([], ["TEST wrong header"])])
def test_parser_rejects_invalid_file_and_headers(content):
    with pytest.raises(CostImportError):
        parse_cost_file(content)


def test_import_requires_readwrite(api_client, readonly_key):
    assert post(api_client, readonly_key, b"TEST").status_code == 403


@pytest.mark.asyncio
@pytest.mark.layer_unit
async def test_cookie_upload_requires_csrf_before_reading_file():
    request = Request({"type": "http", "method": "POST", "path": BASE + "confirm",
                       "headers": [], "api_key_role": "readwrite", "auth_method": "cookie"})
    with pytest.raises(HTTPException) as error:
        await routes.read_upload(request)
    assert error.value.status_code == 403


def test_preview_matches_shops_averages_and_never_writes(api_client, readwrite_key, db_engine, products):
    before = _facts(db_engine, products)
    content = xlsx([
        ["10", SHOP_A, SHARED], ["20", SHOP_A, SHARED], ["20", SHOP_A, SHARED],
        [None, SHOP_A, SHARED], ["8.12", SHOP_B, SHARED],
        ["7", "TEST wrong shop", SHARED], ["9", None, SHARED],
        ["0.1", None, UNIQUE], ["0.2", None, UNIQUE],
    ])
    response = post(api_client, readwrite_key, content)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["readyCount"] == 3
    assert preview["blankCostRows"] == 1 and preview["skippedRows"] == 2
    assert preview["importRows"] == 6
    items = {item["spuPk"]: item for item in preview["items"]}
    assert items[str(products[0])]["unitCost"] == "16.6667"
    assert items[str(products[1])]["unitCost"] == "8.1200"
    assert items[str(products[2])]["unitCost"] == "0.1500"
    assert items[str(products[0])]["sampleCount"] == 3
    assert _facts(db_engine, products) == before


def test_confirm_preserves_old_costs_and_rejects_replay(api_client, readwrite_key, db_engine, products):
    content = xlsx([["10", SHOP_A, SHARED], ["20", SHOP_A, SHARED], ["5", SHOP_B, SHARED]])
    preview = post(api_client, readwrite_key, content).json()
    path = "confirm?fingerprint=" + preview["fingerprint"]
    response = post(api_client, readwrite_key, content, path)
    assert response.status_code == 201, response.text
    assert response.json()["importedCount"] == 2
    facts = _facts(db_engine, products)
    for pk, cost in ((products[0], "15.0000"), (products[1], "5.0000")):
        rows = [row for row in facts if row.spu_pk == pk]
        assert len(rows) == 2
        assert rows[0].valid_to is not None
        assert rows[1].valid_to is None
        assert str(rows[1].unit_cost) == cost and rows[1].currency == "CNY"
    assert post(api_client, readwrite_key, content, path).status_code == 409
    assert _facts(db_engine, products) == facts


def test_changed_file_or_current_price_requires_new_preview(api_client, readwrite_key, db_engine, products):
    content = xlsx([["10", SHOP_A, SHARED]])
    preview = post(api_client, readwrite_key, content).json()
    path = "confirm?fingerprint=" + preview["fingerprint"]
    changed = xlsx([["99", SHOP_A, SHARED]])
    before = _facts(db_engine, products)
    assert post(api_client, readwrite_key, changed, path).status_code == 409
    assert _facts(db_engine, products) == before
    response = api_client.post("/v2/reporting/manual-costs", headers={"X-API-Key": readwrite_key},
                               json={"spu_pk": products[0], "spu_id": SHARED, "unit_cost": "20", "currency": "CNY"})
    assert response.status_code == 201
    changed_facts = _facts(db_engine, products)
    assert post(api_client, readwrite_key, content, path).status_code == 409
    assert _facts(db_engine, products) == changed_facts


def test_failure_rolls_back_whole_import(api_client, readwrite_key, db_engine, products, monkeypatch):
    content = xlsx([["10", SHOP_A, SHARED], ["20", SHOP_B, SHARED]])
    preview = post(api_client, readwrite_key, content).json()
    before = _facts(db_engine, products)
    append = routes.append_manual_cost
    calls = []

    def fail_second(session, **kwargs):
        calls.append(kwargs["spu_pk"])
        if len(calls) == 2:
            raise RuntimeError("TEST rollback whole import")
        return append(session, **kwargs)

    monkeypatch.setattr(routes, "append_manual_cost", fail_second)
    with pytest.raises(RuntimeError, match="TEST rollback"):
        post(api_client, readwrite_key, content, "confirm?fingerprint=" + preview["fingerprint"])
    assert len(calls) == 2
    assert _facts(db_engine, products) == before


def test_missing_shop_column_only_resolves_unique_spu(api_client, readwrite_key, products):
    content = xlsx([["10", SHARED], ["12.12345", UNIQUE]], [HEADERS[0], HEADERS[2]])
    response = post(api_client, readwrite_key, content)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["readyCount"] == 1 and preview["skippedRows"] == 1
    assert preview["items"][0]["spuPk"] == str(products[2])
    assert preview["items"][0]["unitCost"] == "12.1235"


def test_invalid_nonblank_amount_blocks_entire_product_group(api_client, readwrite_key, db_engine, products):
    before = _facts(db_engine, products)
    content = xlsx([["10", SHOP_A, SHARED], ["TEST invalid", SHOP_A, SHARED]])
    response = post(api_client, readwrite_key, content)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["readyCount"] == 0 and preview["skippedRows"] == 2
    response = post(api_client, readwrite_key, content, "confirm?fingerprint=" + preview["fingerprint"])
    assert response.status_code == 422
    assert _facts(db_engine, products) == before


def test_shop_alias_samples_merge_before_averaging(api_client, readwrite_key, db_engine, products):
    with Session(db_engine) as session:
        shop_pk = session.scalar(select(ChannelProduct.shop_pk).where(ChannelProduct.id == products[0]))
        session.execute(update(ChannelAccount).where(ChannelAccount.id == shop_pk).values(shop_alias="TEST alias"))
        session.commit()
    content = xlsx([["10", SHOP_A, SHARED], ["20", "TEST alias", SHARED], ["30", "TEST alias", SHARED]])
    response = post(api_client, readwrite_key, content)
    assert response.status_code == 200, response.text
    item, = response.json()["items"]
    assert item["spuPk"] == str(products[0])
    assert item["sampleCount"] == 3 and item["unitCost"] == "20.0000"


def test_unauthorized_shops_are_not_import_targets(api_client, readwrite_key, db_engine, products):
    with Session(db_engine) as session:
        shop_pk = session.scalar(select(ChannelProduct.shop_pk).where(ChannelProduct.id == products[0]))
        session.execute(update(ChannelAccount).where(ChannelAccount.id == shop_pk).values(credential_id=None))
        session.commit()
    response = post(api_client, readwrite_key, xlsx([["10", SHOP_A, SHARED]]))
    assert response.status_code == 200, response.text
    assert response.json()["readyCount"] == 0


def test_unnamed_shop_can_use_an_unambiguous_spu(api_client, readwrite_key, db_engine, products):
    with Session(db_engine) as session:
        shop_pk = session.scalar(select(ChannelProduct.shop_pk).where(ChannelProduct.id == products[2]))
        session.execute(update(ChannelAccount).where(ChannelAccount.id == shop_pk).values(account_name=None, shop_alias=None))
        session.commit()
    response = post(api_client, readwrite_key, xlsx([["10", "TEST export shop", UNIQUE]]))
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["spuPk"] == str(products[2])


def test_shared_writer_keeps_single_entry_response_at_database_precision(api_client, readwrite_key, db_engine, products):
    response = api_client.post("/v2/reporting/manual-costs", headers={"X-API-Key": readwrite_key},
                               json={"spu_pk": products[2], "spu_id": UNIQUE, "unit_cost": "12.123456", "currency": "CNY"})
    assert response.status_code == 201, response.text
    assert response.json()["unit_cost"] == "12.1235"
    rows = [row for row in _facts(db_engine, products) if row.spu_pk == products[2]]
    assert len(rows) == 1 and str(rows[0].unit_cost) == response.json()["unit_cost"]

"""XLSX purchase-cost import: parsing, matching and Decimal averages.

openpyxl 3.1.5 (MIT), https://openpyxl.readthedocs.io/ — read-only parser.
defusedxml (PSF) hardens its XML reader; ZIP/row limits bound uploaded work.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tts_erp_v2.commerce.shop_access import authorized_shop_clause
from tts_erp_v2.db.models import ChannelAccount, ChannelProduct
from tts_erp_v2.reporting.purchase_cost import load_effective_purchase_prices

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50_000
MAX_PRODUCTS = 5_000
AMOUNT_HEADER = "采购金额(产品-RMB)"


class CostImportError(ValueError):
    """A readable file validation error; no rows have been written."""


def normalized(value: Any) -> str:
    return unicodedata.normalize("NFKC", "" if value is None else str(value)).strip()


@dataclass
class Samples:
    shop: str
    spu_id: str
    count: int = 0
    total: Decimal = field(default_factory=lambda: Decimal(0))
    invalid: bool = False


@dataclass
class ParsedCosts:
    groups: list[Samples]
    total_rows: int
    blank_rows: int
    issues: list[dict[str, Any]]


def parse_cost_file(content: bytes) -> ParsedCosts:
    """Read the first worksheet. Blank costs never contribute to the mean."""
    if len(content) > MAX_FILE_BYTES:
        raise CostImportError("文件不能超过 8 MB")
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1_000 or sum(e.file_size for e in entries) > 64 * 1024 * 1024:
                raise CostImportError("工作簿解压后过大，请拆分文件")
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except CostImportError:
        raise
    except (BadZipFile, OSError, ValueError, KeyError, SyntaxError, DefusedXmlException, InvalidFileException) as exc:
        raise CostImportError("无法读取文件，请上传有效的 .xlsx 工作簿") from exc
    groups: dict[tuple[str, str], Samples] = {}
    issues: list[dict[str, Any]] = []
    total_rows = blank_rows = 0
    try:
        sheet = workbook.worksheets[0]
        if sheet.max_column and sheet.max_column > 256:
            raise CostImportError("工作表超过 256 列，请仅保留需要的列")
        # Ignore incorrect exporter dimensions, while bounding actual iteration.
        sheet.reset_dimensions()
        rows = sheet.iter_rows(max_col=256)
        header_row = next(rows, ())
        headers = [normalized(cell.value).rstrip(",，") for cell in header_row]
        for required in (AMOUNT_HEADER, "产品ID"):
            if headers.count(required) != 1:
                raise CostImportError(f"表头必须包含且仅包含一列：{required}")
        if headers.count("店铺名称") > 1:
            raise CostImportError("店铺名称列重复")
        amount_col = headers.index(AMOUNT_HEADER)
        product_col = headers.index("产品ID")
        shop_col = headers.index("店铺名称") if "店铺名称" in headers else None
        for row_number, cells in enumerate(rows, 2):
            if row_number > MAX_ROWS + 1:
                raise CostImportError("最多支持 50,000 行，请拆分文件")
            if all(cell.value is None for cell in cells):
                continue
            total_rows += 1
            amount_cell = cells[amount_col]
            if amount_cell.value is None or not normalized(amount_cell.value):
                blank_rows += 1
                continue
            product_cell = cells[product_col]
            spu_id = normalized(product_cell.value)
            shop = normalized(cells[shop_col].value) if shop_col is not None else ""
            # Excel numeric cells cannot reliably carry 19-digit TikTok IDs.
            bad_identity = (
                product_cell.data_type in ("f", "e", "b")
                or not spu_id or len(spu_id) > 128
                or (not isinstance(product_cell.value, str) and (
                    not isinstance(product_cell.value, int)
                    or abs(product_cell.value) > 999_999_999_999_999
                ))
            )
            if bad_identity:
                issues.append({"row": row_number, "spuId": spu_id, "shopName": shop,
                               "count": 1, "reason": "产品ID缺失或精度不可靠，请将产品ID列保存为文本"})
                continue
            key = (shop, spu_id)
            sample = groups.setdefault(key, Samples(shop, spu_id))
            sample.count += 1
            try:
                if amount_cell.data_type in ("f", "e", "b"):
                    raise InvalidOperation
                amount = Decimal(normalized(amount_cell.value))
                if not amount.is_finite() or amount <= 0 or amount >= Decimal("1e16"):
                    raise InvalidOperation
                sample.total += amount
            except (InvalidOperation, ValueError):
                sample.invalid = True
                issues.append({"row": row_number, "spuId": spu_id, "shopName": shop,
                               "count": 1, "reason": "采购金额必须为正数；该商品组暂不导入，请修正后重新上传"})
    except CostImportError:
        raise
    except (ValueError, KeyError, IndexError, SyntaxError, BadZipFile, DefusedXmlException) as exc:
        raise CostImportError("工作表数据损坏，请重新导出 .xlsx 文件") from exc
    finally:
        workbook.close()
    if not total_rows:
        raise CostImportError("工作表没有数据")
    return ParsedCosts(list(groups.values()), total_rows, blank_rows, issues)


def preview_cost_import(session: Session, content: bytes, parsed: ParsedCosts) -> dict[str, Any]:
    """Resolve a unique authorized product, using shop names when available."""
    candidates: dict[str, list[Any]] = defaultdict(list)
    spu_ids = list({group.spu_id for group in parsed.groups})
    if spu_ids:
        rows = session.execute(
            select(ChannelProduct.id, ChannelProduct.spu_id, ChannelProduct.shop_pk,
                   ChannelProduct.title, ChannelAccount.account_name, ChannelAccount.shop_alias)
            .join(ChannelAccount, ChannelAccount.id == ChannelProduct.shop_pk)
            .where(ChannelProduct.spu_id.in_(spu_ids), ChannelAccount.platform == "tiktok",
                   text(authorized_shop_clause()))
        ).all()
        for row in rows:
            candidates[row.spu_id].append(row)
    resolved: dict[int, tuple[Any, Samples]] = {}
    issues = list(parsed.issues)
    for group in parsed.groups:
        matches = candidates[group.spu_id]
        if group.shop:
            matches = [row for row in matches if
                       not (normalized(row.account_name) or normalized(row.shop_alias))
                       or group.shop.casefold() in {
                           normalized(row.account_name).casefold(), normalized(row.shop_alias).casefold()}]
        if len(matches) != 1:
            issues.append({"row": None, "spuId": group.spu_id, "shopName": group.shop,
                           "count": group.count,
                           "reason": "店铺或商品未匹配" if not matches else "商品对应多个店铺，无法唯一匹配"})
            continue
        row = matches[0]
        if row.id not in resolved:
            resolved[row.id] = (row, Samples(group.shop, group.spu_id))
        aggregate = resolved[row.id][1]
        aggregate.count += group.count
        aggregate.total += group.total
        aggregate.invalid |= group.invalid
    if len(resolved) > MAX_PRODUCTS:
        raise CostImportError("每次最多导入 5,000 个商品，请拆分文件")
    current = load_effective_purchase_prices(session, list(resolved))
    items = []
    for pk, (product, sample) in sorted(resolved.items()):
        if sample.invalid:
            issues.append({"row": None, "spuId": product.spu_id, "shopName": sample.shop,
                           "count": sample.count, "reason": "含无效采购金额，整组跳过"})
            continue
        mean = (sample.total / sample.count).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if mean <= 0 or mean >= Decimal("1e16"):
            issues.append({"row": None, "spuId": product.spu_id, "shopName": sample.shop,
                           "count": sample.count, "reason": "平均采购价超出可保存范围"})
            continue
        old = current.get(pk)
        items.append({"spuPk": str(pk), "spuId": product.spu_id, "shopPk": str(product.shop_pk),
                      "shopName": product.account_name or product.shop_alias or sample.shop,
                      "title": product.title, "sampleCount": sample.count,
                      "unitCost": str(mean), "currency": "CNY",
                      "previousId": str(old.record_id) if old and old.record_id is not None else None,
                      "previousCost": str(old.unit_cost) if old else None,
                      "previousCurrency": old.currency if old else None})
    import_rows = sum(item["sampleCount"] for item in items)
    result = {"totalRows": parsed.total_rows, "blankCostRows": parsed.blank_rows,
              "importRows": import_rows, "readyCount": len(items),
              "skippedRows": parsed.total_rows - parsed.blank_rows - import_rows,
              "items": items, "issues": issues}
    fingerprint = hashlib.sha256(content + json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**result, "fingerprint": fingerprint}

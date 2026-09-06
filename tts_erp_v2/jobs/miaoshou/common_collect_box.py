"""Miaoshou sync job: common_collect_box (6h cadence).

Syncs the **公共采集箱（货源采集箱）列表** from
``get_common_collect_box_list`` into ``integration.raw_records`` +
``procurement.procurement_products``, filling the **货源价** columns
(``source_unit_cost`` / ``source_min_unit_cost`` / ``source_max_unit_cost``).

Why this job exists
-------------------
TK 平台采集箱（``miaoshou.collect_box``）SKU 上的 ``originPrice`` 标注为
“货源价格”，但实测会被人工/模板改掉（同一 1688 offer 的 originPrice=244.72，
而公共采集箱与 1688 官网挂牌价都是 ¥29~34，差 ~7×）。因此货源价的可信位在
**公共采集箱列表的 ``price`` / ``minSkuPrice`` / ``maxSkuPrice``**
（采集时从 1688 抓的挂牌价）。本 job 把这份可信货源价落库，供
``reporting.product_cost_snapshots`` 的 ``SOURCE_PRICE`` 口径（兜底估算，
优先级低于采购单成交价 / 人工填写，见 tech-doc/data-model-target-v3.md §11）。

⚠ 口径提醒：``source_unit_cost`` 是 1688 挂牌标价，不是采购单成交价
（成交价在 ``purchase_order_lines.unit_cost``）。标价≠成本（§11.3），
只在无采购单无人工时作估算兜底，报表须以“估算成本”命名。

Endpoint
--------
``POST /open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list``
(apifox api-446814582). Body: ``{"pageNo", "pageSize"}``. Response:
``{"result":"success","data":{"detailList":[...], "total": N}}`` ——
注意该端点**没有** ``totalPage``（``total`` 是行数不是页数），分页在
空页自然终止，不能把 ``total`` 当总页数传（会被 retry 层当成“还有更多页”
而逐空页重试）。

字段映射（list 行 → procurement_products）
------------------------------------------
| API 字段 | DB 列 | 语义 |
| --- | --- | --- |
| ``commonCollectBoxDetailId`` | ``external_product_id`` | 货源采集条目 id（可重复采集同 offer → 多行） |
| ``title`` | ``title`` | 商品标题（中文源标题） |
| ``price`` | ``source_unit_cost``（缺省回退 ``minSkuPrice``） | SPU 货源价 |
| ``minSkuPrice`` / ``maxSkuPrice`` | ``source_min_unit_cost`` / ``source_max_unit_cost`` | SKU 价格区间 |
| ``sourceList[0].source`` | ``source_platform`` | 货源平台（1688 等） |
| ``sourceList[0].sourceItemId`` | ``source_item_id`` | 1688 offer id |
| ``sourceList[0].sourceItemUrl`` | ``source_item_url`` | 1688 offer URL |
| ``status`` | ``status`` | 条目状态（skip=重复采集等） |
| ``gmtModified``/``gmtCreate`` | ``source_updated_at`` | 上游时间（naive→UTC 惯例） |

``product_type`` 沿用 ``COLLECTED_PRODUCT``（与 TK 采集箱 job 一致，靠
``external_product_id`` 的 id 空间区分货源侧/发布侧）。

Failure mode contract
---------------------
空页 = 自然翻页终止（无 totalPage，行数不为页数）。缺
``commonCollectBoxDetailId`` 的行 → ``COMMON_BOX_MISSING_ID`` 写
``sync_issues`` 并跳过。上游失败 → ``run_job`` → SyncJob failed, re-raise。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.procurement import ProcurementProduct
from tts_erp_v2.jobs.miaoshou._common import resolve_miaoshou_context
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.common_collect_box")

JOB_NAME = "miaoshou.common_collect_box"
ENDPOINT = "miaoshou.common_collect_box.get_common_collect_box_list"
PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
PAGE_SIZE = 50  # upstream default for this endpoint; no documented cap
MAX_PAGES = 1000


class _MiaoshouClientProto(Protocol):
    def _call_erp(
        self,
        *,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict[str, Any]: ...


def _fetch_page(
    client: _MiaoshouClientProto, *, page_no: int, page_size: int = PAGE_SIZE
) -> dict[str, Any]:
    return client._call_erp(
        path=PATH,
        body={"pageNo": page_no, "pageSize": page_size},
    )


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _first_source(item: dict[str, Any]) -> dict[str, Any]:
    sources = item.get("sourceList") or []
    return sources[0] if isinstance(sources, list) and sources else {}


def _parse_product_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map a common-collect-box row → procurement_products upsert values.

    Returns ``None`` when the row lacks a ``commonCollectBoxDetailId``
    (nothing sane to key an upsert on).
    """
    common_id = item.get("commonCollectBoxDetailId")
    if common_id is None:
        return None
    src = _first_source(item)
    unit_cost = _to_decimal(item.get("price"))
    if unit_cost is None:
        unit_cost = _to_decimal(item.get("minSkuPrice"))
    updated_raw = item.get("gmtModified") or item.get("gmtCreate")
    return {
        "external_product_id": str(common_id),
        "product_type": "COLLECTED_PRODUCT",
        "title": item.get("title"),
        "source_platform": src.get("source"),
        "source_item_id": src.get("sourceItemId"),
        "source_item_url": src.get("sourceItemUrl"),
        "source_unit_cost": unit_cost,
        "source_min_unit_cost": _to_decimal(item.get("minSkuPrice")),
        "source_max_unit_cost": _to_decimal(item.get("maxSkuPrice")),
        "status": item.get("status"),
        "source_updated_at": _parse_naive_utc(updated_raw),
    }


def _parse_naive_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip().replace("T", " ").split(".")[0]
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    return None


def sync_common_collect_box(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Sync the miaoshou common-collect-box (货源) list.

    Args:
        session: SQLAlchemy session (caller commits).
        client: optional fake / real ``MiaoshouErpClient``.
        license_id: explicit license id; falls back to env.

    Returns:
        Dict with ``pages_walked`` / ``items_seen`` / ``products_upserted`` /
        ``rate_limit_retries`` / ``issues``.
    """
    from tts_erp_v2.proxy.miaoshou.retry import PageResult, paginate_with_retry

    with run_job(session, job_name=JOB_NAME) as job:
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError("no miaoshou credentials row; cannot construct context")
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)

        rate_limit_retries = 0

        def _on_retry(attempt: int, err: BaseException) -> None:
            nonlocal rate_limit_retries
            rate_limit_retries += 1
            log.warning(
                "miaoshou.common_collect_box page retry attempt=%d err=%r",
                attempt, err,
            )

        def fetch_page(page: int) -> PageResult:
            payload = _fetch_page(client, page_no=page)  # type: ignore[arg-type]
            data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
            items = data.get("detailList") or []
            # NOTE: 上游无 totalPage，total=行数；一律不传，空页即自然终止
            # （传了会被 retry 层当成"还有更多页"逐空页重试到 max_pages）。
            return PageResult(
                items=list(items) if isinstance(items, list) else [],
                page=page,
                total_pages=None,
                total_count=None,
            )

        items, last_page = paginate_with_retry(
            fetch_page,
            start_page=1,
            max_pages=MAX_PAGES,
            max_retries=max_retries,
            on_retry=_on_retry,
        )

        products_upserted = 0
        issues = 0

        for item in items:
            if not isinstance(item, dict):
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="COMMON_BOX_PARSE_FAILED",
                    details={"item": repr(item)[:300]},
                )
                issues += 1
                continue
            try:
                parsed = _parse_product_row(item)
                if parsed is None:
                    record_sync_issue(
                        session,
                        job_name=JOB_NAME,
                        issue_type="COMMON_BOX_MISSING_ID",
                        details={"item_keys": list(item.keys())[:10]},
                    )
                    issues += 1
                    continue

                record_raw_payload(
                    session,
                    endpoint=ENDPOINT,
                    payload=item,
                    external_id=parsed["external_product_id"],
                    credential_id=ctx.credentials.id if ctx else None,
                )

                assert ctx is not None  # narrowed by the early-return above
                _upsert_product(
                    session,
                    procurement_account_id=ctx.account_id,
                    parsed=parsed,
                )
                products_upserted += 1
            except Exception as e:  # noqa: BLE001
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="COMMON_BOX_PARSE_FAILED",
                    external_id=str(item.get("commonCollectBoxDetailId")),
                    details={"error": f"{type(e).__name__}: {e}"},
                )
                issues += 1

        job.rows_total = len(items)
        job.rows_inserted = products_upserted
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "rate_limit_retries": rate_limit_retries,
            "finished_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "items_seen": len(items),
            "products_upserted": products_upserted,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


def _upsert_product(
    session: Session,
    *,
    procurement_account_id: int,
    parsed: dict[str, Any],
) -> ProcurementProduct:
    """Idempotent upsert keyed by (procurement_account_id, external_product_id)."""
    values = {
        "procurement_account_id": procurement_account_id,
        "external_product_id": parsed["external_product_id"],
        "product_type": parsed.get("product_type"),
        "title": parsed.get("title"),
        "source_platform": parsed.get("source_platform"),
        "source_item_id": parsed.get("source_item_id"),
        "source_item_url": parsed.get("source_item_url"),
        "source_unit_cost": parsed.get("source_unit_cost"),
        "source_min_unit_cost": parsed.get("source_min_unit_cost"),
        "source_max_unit_cost": parsed.get("source_max_unit_cost"),
        "status": parsed.get("status"),
        "source_updated_at": parsed.get("source_updated_at"),
        "synced_at": datetime.now(timezone.utc),
    }
    insert_stmt = pg_insert(ProcurementProduct).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["procurement_account_id", "external_product_id"],
        set_={
            "product_type": values["product_type"],
            "title": values["title"],
            "source_platform": values["source_platform"],
            "source_item_id": values["source_item_id"],
            "source_item_url": values["source_item_url"],
            "source_unit_cost": values["source_unit_cost"],
            "source_min_unit_cost": values["source_min_unit_cost"],
            "source_max_unit_cost": values["source_max_unit_cost"],
            "status": values["status"],
            "source_updated_at": values["source_updated_at"],
            "synced_at": values["synced_at"],
        },
    )
    session.execute(upsert_stmt)
    row = session.execute(
        select(ProcurementProduct)
        .where(ProcurementProduct.procurement_account_id == procurement_account_id)
        .where(ProcurementProduct.external_product_id == parsed["external_product_id"])
    ).scalar_one()
    return row


__all__ = ["ENDPOINT", "JOB_NAME", "PATH", "sync_common_collect_box"]

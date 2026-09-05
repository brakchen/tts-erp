"""tiktok.products — products_spu + products_sku sync.

Pulls the active catalog and upserts into
``commerce.products_spu`` / ``commerce.products_sku``.
Writes unknown product_ids surfaced by the orders job back to
``integration.sync_issues`` so the next run can resolve them.

Incremental strategy
--------------------
Cursor: ``integration.sync_cursors.cursor_epoch_ms`` for scope=shop_id.
Upstream ``update_time_ge`` is epoch seconds; cursor stores ms.

Pagination
----------
``next_page_token`` (same as orders). Empty/falsy token ends the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    ChannelProductVariant,
    RawRecord,
    SyncIssue,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

ENDPOINT = "/product/202309/products/search"
JOB_NAME = "tiktok.products"
ProxyCall = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_seconds_to_utc(seconds: int | None):
    if seconds is None or seconds <= 0:
        return None
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(_safe_int(seconds), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce to int without raising; ``None``/garbage → ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _walk_pages(proxy_call, *, base_body):
    collected: list[dict] = []
    next_token: str | None = None
    body = dict(base_body)
    while True:
        page_body = dict(body)
        if next_token:
            page_body["next_page_token"] = next_token
        resp = proxy_call("POST", ENDPOINT, body=page_body)
        code = resp.get("code", -1)
        if code != 0:
            raise UpstreamJobError(
                f"upstream returned non-zero code={code} message={resp.get('message')!r}"
            )
        data = resp.get("data") or {}
        products = data.get("products") or []
        collected.extend(products)
        next_token = data.get("next_page_token") or None
        if not next_token:
            break
    return collected


def _parse_product(raw: dict) -> dict:
    # TikTok 202309 /product/202309/products/search returns ``id``
    # (verified live 2026-08-30); ``product_id`` no longer exists.
    pid = raw.get("product_id") or raw.get("id")
    if not pid:
        raise ParseError("product_id missing")
    return {
        "spu_id": str(pid),
        "title": raw.get("title"),
        "category_id": raw.get("category_id"),
        "status": raw.get("status"),
        "main_image_url": raw.get("main_image_url"),
        "source_created_at": _epoch_seconds_to_utc(raw.get("create_time")),
        "source_updated_at": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _parse_variant(raw: dict) -> dict:
    vid = raw.get("id") or raw.get("variant_id") or raw.get("sku_id")
    if not vid:
        raise ParseError("variant id missing")
    return {
        "sku_id": str(vid),
        "seller_sku": raw.get("seller_sku"),
        "variant_name": raw.get("sku_name") or raw.get("variant_name"),
        "attributes": raw.get("attributes"),
        "image_url": raw.get("sku_image_url") or raw.get("image_url"),
        "status": raw.get("status"),
        "source_updated_at": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _upsert_product(
    session, *, account_id: int, fields: dict, raw_record_id: int
) -> int:
    insert_values = {
        "shop_pk": account_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(ChannelProduct)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["shop_pk", "spu_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(ChannelProduct).where(
            ChannelProduct.shop_pk == account_id,
            ChannelProduct.spu_id == fields["spu_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_variant(
    session, *, spu_pk: int, fields: dict, raw_record_id: int
) -> None:
    insert_values = {
        "spu_pk": spu_pk,
        **fields,
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(ChannelProductVariant)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["spu_pk", "sku_id"],
            set_=update_cols,
        )
    )


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 50,
    scope: str | None = None,
) -> JobResult:
    from tts_erp_v2.sync_worker import watermarks

    cursor_scope = scope or shop_id
    account = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == shop_id,
        )
    ).scalar_one_or_none()
    if account is None:
        raise UpstreamJobError(
            f"shops row missing for tiktok shop_id={shop_id!r}"
        )

    watermark_ms = watermarks.get_cursor(session, job_name=JOB_NAME, scope=cursor_scope)

    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = _safe_int(watermark_ms) // 1000

    products = _walk_pages(proxy_call, base_body=base_body)

    rows_total = len(products)
    rows_inserted = 0
    rows_failed = 0
    max_update_ms: int | None = None

    for raw in products:
        ext_product_id = str(raw.get("product_id") or raw.get("id") or "<unknown>")
        try:
            p_fields = _parse_product(raw)
        except ParseError as exc:
            rows_failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=ext_product_id,
                    details={"error": str(exc)},
                )
            )
            continue

        raw_row = RawRecord(
            endpoint=ENDPOINT,
            external_id=p_fields["spu_id"],
            payload=raw,
        )
        session.add(raw_row)
        session.flush()
        cp_id = _upsert_product(
            session,
            account_id=account.id,
            fields=p_fields,
            raw_record_id=raw_row.id,
        )

        for raw_v in raw.get("skus") or raw.get("variants") or []:
            try:
                v_fields = _parse_variant(raw_v)
            except ParseError as exc:
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="PARSE_ERROR",
                        external_id=f"{p_fields['spu_id']}:<sku>",
                        details={"error": str(exc)},
                    )
                )
                continue
            _upsert_variant(
                session,
                spu_pk=cp_id,
                fields=v_fields,
                raw_record_id=raw_row.id,
            )

        rows_inserted += 1
        update_ms = p_fields.get("source_updated_at") and _safe_int(
            p_fields["source_updated_at"].timestamp() * 1000
        )
        if update_ms and (max_update_ms is None or update_ms > max_update_ms):
            max_update_ms = update_ms

    new_cursor_ms: int | None = None
    if max_update_ms is not None and (
        watermark_ms is None or max_update_ms > _safe_int(watermark_ms)
    ):
        watermarks.set_cursor(
            session,
            job_name=JOB_NAME,
            scope=cursor_scope,
            cursor_epoch_ms=max_update_ms,
        )
        new_cursor_ms = max_update_ms

    return JobResult(
        rows_total=rows_total,
        rows_inserted=rows_inserted,
        rows_failed=rows_failed,
        cursor=new_cursor_ms,
    )


__all__ = ["run", "ENDPOINT", "JOB_NAME", "ProxyCall", "UpstreamJobError", "ParseError"]

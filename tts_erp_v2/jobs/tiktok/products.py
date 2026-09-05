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

Image fetch (2026-09-05)
------------------------
``/product/202309/products/search`` returns products WITHOUT any image
fields (verified live against production). To populate
``products_spu.main_image_url`` and ``products_sku.image_url``, after
each search hit we additionally call
``GET /product/202309/products/{product_id}`` (one round-trip per SPU),
which returns ``main_images[].urls[]`` for the SPU and
``skus[].sales_attributes[].sku_img.urls[]`` for each variant.
A failed detail fetch does NOT abort the run — we still insert the SPU
row with core fields from search and write an ``IMAGE_FETCH_ERROR``
``SyncIssue`` for operator triage.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
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
# Per-SPU image fetch: takes internal shop_pk + upstream product_id,
# returns the unpacked `data` dict from Get Product.
ImageFetcher = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


class ImageFetchError(RuntimeError):
    """Get Product call failed for one SPU; core fields still get written."""


def _epoch_seconds_to_utc(seconds: int | None):
    if seconds is None or seconds <= 0:
        return None
    from datetime import datetime

    try:
        return datetime.fromtimestamp(_safe_int(seconds), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce to int without raising; ``None``/garbage → ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_main_image_url(raw: dict) -> str | None:
    """Pull the first original-size URL from ``main_images[].urls[]``.

    TikTok returns a list of image objects; each carries ``urls[]`` (original
    size) and ``thumb_urls[]`` (300×300 thumbnail). TikTok typically
    includes 2 CDN hosts per array for redundancy — we take index 0.

    Verified live 2026-09-05 against product 1737133011046401271
    (6 main images, each with 2 urls + 2 thumb_urls).
    """
    images = raw.get("main_images") or []
    if not images:
        return None
    first = images[0]
    if not isinstance(first, dict):
        return None
    urls = first.get("urls") or []
    return urls[0] if urls else None


def _extract_sku_image_url(raw_sku: dict) -> str | None:
    """Pull the first image URL from any ``sales_attributes[].sku_img``.

    TikTok's Get Product endpoint puts the variant image under
    ``sales_attributes[].sku_img.urls[]`` — one image per primary sales
    attribute (typically the colour, e.g. ``name="Màu sắc"``). The
    "primary" attribute isn't formally tagged, so we scan all entries
    and take the first one that has a ``sku_img`` with URLs.

    Single-attribute SKUs may have no ``sales_attributes`` at all
    (returns ``None`` — variant row still gets upserted, just without
    an image).
    """
    for attr in raw_sku.get("sales_attributes") or []:
        if not isinstance(attr, dict):
            continue
        img = attr.get("sku_img")
        if not isinstance(img, dict):
            continue
        urls = img.get("urls") or []
        if urls:
            return urls[0]
    return None


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
        # Search payload has no image fields today; the image fetch
        # step in :func:`run` overwrites this with the Get Product
        # ``main_images[0].urls[0]`` when the detail call succeeds.
        "main_image_url": _extract_main_image_url(raw),
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
        # Search payload omits ``sales_attributes``; the image fetch
        # step in :func:`run` overwrites this with the Get Product
        # ``sales_attributes[].sku_img.urls[0]`` when available.
        "image_url": _extract_sku_image_url(raw),
        "status": raw.get("status"),
        "source_updated_at": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _build_default_image_fetcher(session: Session) -> ImageFetcher:
    """Return a fetcher that calls TikTok Get Product via the signed proxy.

    Production path for :func:`run`; the closure captures the session so
    credential lookup happens per call (tokens refresh out-of-band).
    """
    from tts_erp_v2.proxy.tts_shop.products_api import get_product

    def fetch(*, shop_pk: int, product_id: str) -> dict:
        return get_product(session=session, shop_pk=shop_pk, product_id=product_id)

    return fetch


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
    image_fetcher: ImageFetcher | None = None,
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

    # Default fetcher: signed Get Product call (production path).
    # Tests inject their own ``image_fetcher`` to avoid hitting upstream.
    if image_fetcher is None:
        image_fetcher = _build_default_image_fetcher(session)

    watermark_ms = watermarks.get_cursor(session, job_name=JOB_NAME, scope=cursor_scope)

    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = _safe_int(watermark_ms) // 1000

    products = _walk_pages(proxy_call, base_body=base_body)

    rows_total = len(products)
    rows_inserted = 0
    rows_failed = 0
    max_update_ms: int | None = None
    rows_image_fetch_failed = 0

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

        # Fetch full product for image URLs (search payload has none).
        # A failure here is non-fatal — we still write the SPU row from
        # search data and log an IMAGE_FETCH_ERROR SyncIssue.
        full_product: dict | None = None
        try:
            full_product = image_fetcher(
                shop_pk=account.id, product_id=p_fields["spu_id"]
            )
            fetched_url = _extract_main_image_url(full_product)
            if fetched_url:
                p_fields["main_image_url"] = fetched_url
        except Exception as exc:  # noqa: BLE001 — boundary
            rows_image_fetch_failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="IMAGE_FETCH_ERROR",
                    external_id=p_fields["spu_id"],
                    details={
                        "error": str(exc),
                        "type": type(exc).__name__,
                    },
                )
            )
            full_product = None

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

        # Variants: prefer the detail payload's SKUs (which carry
        # sales_attributes[].sku_img) and fall back to the search list
        # when the detail fetch failed.
        search_skus = raw.get("skus") or raw.get("variants") or []
        detail_skus = (
            (full_product or {}).get("skus") if isinstance(full_product, dict) else None
        )
        # Match by variant ID; if detail didn't carry a particular SKU,
        # we still write it from search data (no image in that case).
        detail_by_id: dict[str, dict] = {}
        if isinstance(detail_skus, list):
            for fs in detail_skus:
                if isinstance(fs, dict) and fs.get("id") is not None:
                    detail_by_id[str(fs["id"])] = fs
        for raw_v in search_skus:
            try:
                # Prefer detail payload (has sales_attributes); fall back
                # to search payload (id/seller_sku/sku_name only).
                detail_match = None
                if isinstance(raw_v, dict) and raw_v.get("id") is not None:
                    detail_match = detail_by_id.get(str(raw_v["id"]))
                sku_input = detail_match or raw_v
                v_fields = _parse_variant(sku_input)
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
        rows_image_fetch_failed=rows_image_fetch_failed,
        cursor=new_cursor_ms,
    )


__all__ = [
    "ENDPOINT",
    "JOB_NAME",
    "ImageFetchError",
    "ImageFetcher",
    "ParseError",
    "ProxyCall",
    "UpstreamJobError",
    "run",
]

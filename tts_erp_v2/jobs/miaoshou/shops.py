"""Miaoshou sync job: shops (1h cadence).

Syncs the miaoshou ERP shop list endpoint into
``procurement.procurement_accounts``. The list is small (one shop per
platform/site combination per license) so we fetch all pages up front
and upsert in a single transaction.

Endpoint
--------
``POST /open/v1/product/shop/shop/get_shop_list`` (apifox api-446814596).
Body: ``{"platform", "site", "pageNo", "pageSize"}``. We paginate
``pageSize=100`` (the documented upper bound) and walk until the
upstream returns an empty ``shopList`` or we hit the advertised
``total`` (if present).

Output
------
* Raw payloads → ``integration.raw_records`` (one row per page).
* Upserts → ``procurement.procurement_accounts``.
* Hard parse failures → ``integration.sync_issues`` with
  ``issue_type='SHOP_PARSE_FAILED'``; job continues.

Failure mode contract
---------------------
If the upstream is unreachable / rate-limit-rejected beyond the retry
budget, the job raises and ``run_job`` marks the SyncJob row
``failed``. Idempotency: re-running picks up where the previous
run left off because ``ON CONFLICT DO UPDATE`` replaces in-place.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from tts_erp_v2.jobs.miaoshou._common import (
    MIAOSHOU_PROVIDER,
    MiaoshouContext,
    ensure_procurement_account,
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.shops")

JOB_NAME = "miaoshou.shops"
ENDPOINT = "miaoshou.shop.get_shop_list"
PAGE_SIZE = 100  # documented upper bound (apifox api-446814596)
MAX_PAGES = 50  # safety cap; production count is single-digit


class _MiaoshouClientProto(Protocol):
    """Minimal protocol — the job only needs ``_call_erp``."""

    def _call_erp(self, *, path: str, body: dict | None = None, query: dict | None = None,
                  extra_headers: dict | None = None) -> dict[str, Any]: ...


def _fetch_shop_page(
    client: _MiaoshouClientProto,
    *,
    platform: str,
    site: str,
    page_no: int,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    """Single page call. Body keys: ``platform``, ``site``, ``pageNo``, ``pageSize``."""
    return client._call_erp(
        path="/open/v1/product/shop/shop/get_shop_list",
        body={
            "platform": platform,
            "site": site,
            "pageNo": page_no,
            "pageSize": page_size,
        },
    )


def _parse_shop_row(shop: dict[str, Any]) -> dict[str, Any]:
    """Map a raw shop JSON into the ``procurement_accounts`` upsert shape.

    Defensive coercion — anything missing/None becomes NULL on the
    DB row instead of raising. The miaoshou-side external_account_id
    is the ``shopId`` (string), not the ``licenseId``; one licenseId
    maps to many shops.
    """
    shop_id = shop.get("shopId")
    return {
        "provider": MIAOSHOU_PROVIDER,
        "external_account_id": str(shop_id) if shop_id is not None else "",
        "account_name": shop.get("platformShopName") or shop.get("shopNick"),
        "status": shop.get("status"),
    }


def sync_shops(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    platforms: tuple[tuple[str, str], ...] = (("tiktok", "VN"),),
    license_id: str | None = None,
) -> dict[str, Any]:
    """Sync the miaoshou shop list.

    Args:
        session: SQLAlchemy session (caller commits).
        client: optional ``MiaoshouErpClient``-like object. If omitted,
            :func:`resolve_miaoshou_context` + the default factory are
            used.
        platforms: ``((platform, site), ...)`` pairs to query. Defaults
            to ``(("tiktok", "VN"),)`` matching the single shop in
            production.
        license_id: explicit license id; falls back to env.

    Returns:
        Dict with ``pages`` / ``shops_seen`` / ``upserted`` / ``issues``.
    """
    with run_job(session, job_name=JOB_NAME) as job:
        ctx: MiaoshouContext | None = None
        if client is None:
            ctx = resolve_miaoshou_context(session, license_id=license_id)
            if ctx is None:
                raise RuntimeError(
                    "no miaoshou credentials row; cannot construct client"
                )
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)

        pages_walked = 0
        shops_seen = 0
        upserted = 0
        issues = 0
        total_pages_walked = 0

        for platform, site in platforms:
            page_no = 1
            while page_no <= MAX_PAGES:
                try:
                    payload = _fetch_shop_page(
                        client, platform=platform, site=site, page_no=page_no
                    )
                except Exception as e:
                    record_sync_issue(
                        session,
                        job_name=JOB_NAME,
                        issue_type="SHOP_FETCH_FAILED",
                        external_id=f"{platform}:{site}:{page_no}",
                        details={
                            "platform": platform,
                            "site": site,
                            "page_no": page_no,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                    issues += 1
                    raise  # let run_job mark failed; caller decides retry

                raw = record_raw_payload(
                    session,
                    endpoint=ENDPOINT,
                    payload=payload,
                    external_id=f"{platform}:{site}:{page_no}",
                    credential_id=ctx.credentials.id if ctx else None,
                )
                raw_id = raw.id

                data = payload.get("data") or {}
                shop_list = data.get("shopList") or []
                if not isinstance(shop_list, list):
                    record_sync_issue(
                        session,
                        job_name=JOB_NAME,
                        issue_type="SHOP_PARSE_FAILED",
                        external_id=f"{platform}:{site}:{page_no}",
                        details={
                            "raw_record_id": raw_id,
                            "reason": "data.shopList is not a list",
                        },
                    )
                    issues += 1
                    break  # malformed → stop paging this platform

                page_no += 1
                pages_walked += 1

                if not shop_list:
                    break  # natural end-of-data

                for shop in shop_list:
                    shops_seen += 1
                    if not isinstance(shop, dict):
                        record_sync_issue(
                            session,
                            job_name=JOB_NAME,
                            issue_type="SHOP_PARSE_FAILED",
                            details={"raw_record_id": raw_id, "shop": repr(shop)[:300]},
                        )
                        issues += 1
                        continue
                    try:
                        parsed = _parse_shop_row(shop)
                        if not parsed["external_account_id"]:
                            raise ValueError("missing shopId")
                        ensure_procurement_account(
                            session,
                            credential_id=ctx.credentials.id if ctx else None,
                            **parsed,
                        )
                        upserted += 1
                    except Exception as e:  # noqa: BLE001
                        record_sync_issue(
                            session,
                            job_name=JOB_NAME,
                            issue_type="SHOP_PARSE_FAILED",
                            external_id=str(shop.get("shopId")),
                            details={
                                "raw_record_id": raw_id,
                                "error": f"{type(e).__name__}: {e}",
                            },
                        )
                        issues += 1

                # Optional early termination when upstream advertises total.
                total = data.get("total")
                if isinstance(total, int) and total > 0 and shops_seen >= total:
                    break

            total_pages_walked += pages_walked

        job.rows_total = shops_seen
        job.rows_inserted = upserted
        job.rows_failed = issues
        job.extra = {
            "pages": total_pages_walked,
            "platforms": [f"{p}:{s}" for p, s in platforms],
            "finished_at_iso": datetime.utcnow().isoformat(),
        }
        return {
            "pages": total_pages_walked,
            "shops_seen": shops_seen,
            "upserted": upserted,
            "issues": issues,
        }


__all__ = ["ENDPOINT", "JOB_NAME", "sync_shops"]

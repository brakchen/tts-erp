"""Miaoshou ERP sync functions — extracted from legacy tts_erp.py Handler.

Each function takes a params dict (query-string-style, scalar or single-item
list) and returns (http_status_code, body_dict). No HTTP framework dependency.

W4.1: these were Handler._sync_miaoshou_* / _db_list_miaoshou_* methods on the
legacy stdlib service. Extracted to module level so tts_erp_fastapi calls them
directly (no _StubHandler).
"""
from __future__ import annotations

import psycopg
import psycopg.rows
from psycopg import sql

from tts_erp import (
    _safe_int,
    db_connect,
    log_sync,
    persist_miaoshou_collect_box_detail,
    persist_miaoshou_move_collect_task,
    persist_miaoshou_price_template,
    persist_miaoshou_shop,
)


def _q(params: dict, key, default=None):
    v = params.get(key)
    if isinstance(v, list):
        return v[0] if v else default
    return v if v is not None else default


def sync_miaoshou_shops(params: dict):
    """从妙手 ERP 拉取店铺列表 → upsert 到 miaoshou_shops 表.

    query params:
        platform: 平台代号（默认 tiktok）
        site: 站点代号（默认 VN）
        page_no: 当前页码（默认 1）
        page_size: 每页数量（默认 100）
    """


    platform = _q(params, "platform", "tiktok")
    site = _q(params, "site", "VN")
    try:
        page_no = _safe_int(_q(params, "page_no", "1"), default=1, source="qs.page_no")
        page_size = _safe_int(
            _q(params, "page_size", "100"), default=100, source="qs.page_size"
        )
    except (TypeError, ValueError):
        return (400, {"_error": "page_no/page_size must be int"})

    try:
        from miaoshou import MiaoshouErpClient

        client = MiaoshouErpClient.from_env()
    except Exception as e:  # noqa: BLE001
        return (500, {"_error": f"create MiaoshouErpClient failed: {e}"})

    try:
        result = client.shops.list(
            platform=platform, site=site, page_no=page_no, page_size=page_size
        )
    except Exception as e:  # noqa: BLE001
        return (502, {"_error": f"miaoshou api error: {e}"})

    shops = result.data.shopList if result.data else []
    saved = 0
    for shop in shops:
        if persist_miaoshou_shop(platform, site, shop):
            saved += 1

    log_sync(f"{platform}:{site}", "miaoshou_shops", "ok", rows=saved)
    return (
        200,
        {
            "platform": platform,
            "site": site,
            "saved": saved,
            "total_in_page": len(shops),
        },
    )




def sync_miaoshou_price_templates(params: dict):
    """从妙手 ERP 拉取定价模板列表 → upsert 到 miaoshou_price_templates 表.

    query params:
        platform: 平台代号（默认 tiktok）
        site: 站点（可选，过滤 SDK 调用）
        page_size: 每页数量（默认 20，SDK 上限 20）
    """


    platform = _q(params, "platform", "tiktok")
    site = _q(params, "site")
    try:
        page_size = _safe_int(
            _q(params, "page_size", "20"), default=20, source="qs.page_size"
        )
    except (TypeError, ValueError):
        return (400, {"_error": "page_size must be int"})

    try:
        from miaoshou import MiaoshouErpClient

        client = MiaoshouErpClient.from_env()
    except Exception as e:  # noqa: BLE001
        return (500, {"_error": f"create MiaoshouErpClient failed: {e}"})

    saved = 0
    total = 0
    page_no = 1
    max_pages = 50  # safety cap
    try:
        while page_no <= max_pages:
            sdk_kwargs: dict = {
                "page_no": page_no,
                "page_size": page_size,
            }
            if site:
                sdk_kwargs["site"] = site
            result = client.tk_collect_box.get_price_template_list(**sdk_kwargs)
            templates = (
                result.data.priceTemplateList if result and result.data else []
            )
            if (
                page_no == 1
                and result
                and result.data
                and result.data.total is not None
            ):
                total = _safe_int(result.data.total, default=0, source="sdk.total")
            if not templates:
                break
            for t in templates:
                if persist_miaoshou_price_template(platform, t):
                    saved += 1
            # 末页判定：仅在空页停止，或 total>0 && saved>=total。
            # （price_template SDK 上限 20，可能 total>page_size 仍需多页。
            #  total None/0 → 一直探测直到空页，与 move_collect test 一致。）
            if total > 0 and saved >= total:
                break
            page_no += 1
    except StopIteration:
        # SDK 側返回列表耗尽，视为页面结束（测试用 side_effect=[] 场景）
        pass
    except Exception as e:  # noqa: BLE001
        return (502, {"_error": f"miaoshou api error: {e}"})

    log_sync(platform, "miaoshou_price_templates", "ok", rows=saved)
    return (
        200,
        {
            "platform": platform,
            "saved": saved,
            "total": total,
        },
    )




def sync_miaoshou_collect_box_details(params: dict):
    """从妙手 ERP 拉取公共采集箱详情列表 → upsert 到 miaoshou_collect_box_details 表.

    query params:
        platform: 平台代号（默认 tiktok）
        page_size: 每页数量（默认 50，SDK 上限 500）
        status: 可选过滤（normal / abnormal 等）
    """


    platform = _q(params, "platform", "tiktok")
    status = _q(params, "status")
    try:
        page_size = _safe_int(
            _q(params, "page_size", "50"), default=50, source="qs.page_size"
        )
    except (TypeError, ValueError):
        return (400, {"_error": "page_size must be int"})

    try:
        from miaoshou import MiaoshouErpClient

        client = MiaoshouErpClient.from_env()
    except Exception as e:  # noqa: BLE001
        return (500, {"_error": f"create MiaoshouErpClient failed: {e}"})

    try:
        sdk_kwargs: dict = {
            "page_no": 1,
            "page_size": page_size,
        }
        if status:
            sdk_kwargs["status"] = status
        result = client.tk_collect_box.search_collect_box_list(**sdk_kwargs)
    except Exception as e:  # noqa: BLE001
        return (502, {"_error": f"miaoshou api error: {e}"})

    details = result.data.detailList if result and result.data else []
    saved = 0
    for d in details:
        if persist_miaoshou_collect_box_detail(platform, d):
            saved += 1

    log_sync(platform, "miaoshou_collect_box_details", "ok", rows=saved)
    return (
        200,
        {
            "platform": platform,
            "saved": saved,
            "total_in_page": len(details),
        },
    )




def sync_miaoshou_move_collect_tasks(params: dict):
    """从妙手 ERP 拉取发布任务列表 → upsert 到 miaoshou_move_collect_tasks 表.

    query params:
        platform: 平台代号（默认 tiktok）
        page_size: 每页数量（默认 20，SDK 上限 20）
        status: 可选过滤
    """


    platform = _q(params, "platform", "tiktok")
    status = _q(params, "status")
    try:
        page_size = _safe_int(
            _q(params, "page_size", "20"), default=20, source="qs.page_size"
        )
    except (TypeError, ValueError):
        return (400, {"_error": "page_size must be int"})

    try:
        from miaoshou import MiaoshouErpClient

        client = MiaoshouErpClient.from_env()
    except Exception as e:  # noqa: BLE001
        return (500, {"_error": f"create MiaoshouErpClient failed: {e}"})

    saved = 0
    total = 0
    page_no = 1
    max_pages = 50  # safety cap
    try:
        while page_no <= max_pages:
            sdk_kwargs: dict = {
                "page_no": page_no,
                "page_size": page_size,
            }
            if status:
                sdk_kwargs["status"] = status
            result = client.tk_collect_box.search_move_collect_list(**sdk_kwargs)
            tasks = (
                result.data.moveCollectDetailList if result and result.data else []
            )
            if (
                page_no == 1
                and result
                and result.data
                and result.data.total is not None
            ):
                total = _safe_int(result.data.total, default=0, source="sdk.total")
            if not tasks:
                break
            for t in tasks:
                if persist_miaoshou_move_collect_task(platform, t):
                    saved += 1
            # 末页判定：仅在空页停止，或 total>0 && saved>=total。
            # （SDK 上限 20，可能 total>page_size 仍需多页；
            #  total None/0 → 一直探测直到空页。）
            if total > 0 and saved >= total:
                break
            page_no += 1
    except StopIteration:
        # SDK 側返回列表耗尽，视为页面结束（测试用 side_effect=[] 场景）
        pass
    except Exception as e:  # noqa: BLE001
        return (502, {"_error": f"miaoshou api error: {e}"})

    log_sync(platform, "miaoshou_move_collect_tasks", "ok", rows=saved)
    return (
        200,
        {
            "platform": platform,
            "saved": saved,
            "total": total,
        },
    )




def db_list_miaoshou_shops(params: dict):
    """GET /db/miaoshou_shops?platform=&site=&limit="""


    platform = _q(params, "platform")
    site = _q(params, "site")
    try:
        limit = _safe_int(_q(params, "limit", "100"), default=100, source="qs.limit")
    except (TypeError, ValueError):
        return (400, {"_error": "limit must be int"})

    wh = []
    args: list = []
    if platform:
        wh.append("platform = %s")
        args.append(platform)
    if site:
        wh.append("site = %s")
        args.append(site)

    sql_query = "SELECT * FROM miaoshou_shops"
    if wh:
        sql_query += " WHERE " + " AND ".join(wh)
    sql_query += " ORDER BY synced_at DESC NULLS LAST LIMIT %s"
    args.append(limit)

    try:
        with (
            db_connect() as conn,
            conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
        ):
            # pi-lens-ignore: python-sql-injection
            cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]
            rows = cur.fetchall()
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        return (200, {"count": len(rows), "items": rows})
    except Exception as e:  # noqa: BLE001
        return (500, {"_error": str(e)})



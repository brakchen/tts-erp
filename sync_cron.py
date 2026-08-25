#!/usr/bin/env python3
"""
tts-erp cron 同步脚本 — 每 10 分钟跑一次
=============================================

策略：
  1. 调 GET /shops 自动发现已授权 shop_id
  2. 对每个 (shop, sync_type)：
     - 查 PG sync_log 表里这个 shop+type 最后一次成功 finished_at
     - 下界 = last_at - 5min（防 TikTok 时钟漂移漏单）
     - 如果没有历史，回退到 7 天前
  3. 调 POST /sync/<type> 传 {shop_id, <time_field>: ge, page_size: 50}
     tts-erp 自己会分页最多 50 页，写入 PG + 写 sync_log
     （logistics 例外：不走时间窗口，每轮把"有运单号且未到终态"的
       订单作为 order_ids 传给 /sync/logistics_tracking）
  4. 汇总结果写 logs/cron_sync_<date>.log

不要在这做：
  - 直接调 TikTok API（这层是 tts_erp.py 的活）
  - 直接写 PG 业务表（也是 tts_erp.py 的活）
  - 改 /sync/* 端点逻辑（除非有 bug）

依赖：psycopg 3.x + Python 3.10+（用了 match-case / 类型注解 syntax）
"""

from __future__ import annotations

import sys as _sys

_sys.path.insert(0, "/home/schan/setup/lib")
_sys.path.insert(
    0, "/home/schan/tts-erp/tdd"
)  # for oauth_receiver_core (Wave 3 Slice 2)
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from log_helper import (
    setup_logging as _helper_setup,  # pyright: ignore[reportMissingImports]  # noqa: E402
)
from psycopg import (
    sql,  # psycopg3 SQL composition (Identifier for safe table/col names)
)

# ─── 配置 ────────────────────────────────────────────────────────────
TTS_ERP_DIR = Path("/home/schan/tts-erp")
ENV_FILE = TTS_ERP_DIR / ".env"
LOGS_DIR = TTS_ERP_DIR / "logs"


# ─── .env 加载 ────────────────────────────────────────────────────────
def load_env(path: Path) -> dict[str, str]:
    """简单 .env 解析 — 只读 KEY=VALUE 行，# 起头忽略"""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# Inject OAuth DB creds into os.environ BEFORE oauth_receiver_core is imported.
# oauth_receiver_core's module-load db_init() reads OAUTH_DB_URL /
# OAUTH_DB_ENCRYPTION_KEY / OAUTH_DB_TABLE; without them _db_ok stays False
# and db_list_shops() silently returns [] — cron would do nothing.
for _k, _v in load_env(ENV_FILE).items():
    if _k.startswith("OAUTH_"):
        os.environ.setdefault(_k, _v)

import oauth_receiver_core  # noqa: E402, F401  -- Wave 3 Slice 2 in-process (used in discover_shops)

# 5 分钟缓冲，防止 TikTok 服务端时钟漂移或异步落库导致漏边界订单
WINDOW_BACKOFF_SEC = 5 * 60
# 首次跑 / 新 shop 没有 sync_log 历史时的回退窗口（7 天）
FALLBACK_LOOKBACK_SEC = 7 * 24 * 3600
# ─── L1 watermark optimization ────────────────────────────────────────
# When local MAX(update_time) is within FRESHNESS_THRESHOLD_SEC of now,
# skip the upstream call entirely — the data is already current.
# Conservative default: 60s covers one full cron tick (cron runs every 10min).
FRESHNESS_THRESHOLD_SEC = 60
# Overlap subtracted from local watermark when sending update_time_ge to
# TikTok. Compensates for clock drift + eventual consistency on the
# upstream's update_time field. Same magnitude as WINDOW_BACKOFF_SEC.
WATERMARK_OVERLAP_SEC = 5 * 60
# HTTP 超时（秒）—— _sync_* 会翻页最多 50 次，单次给 3 分钟
HTTP_TIMEOUT = 180
# /sync/* 端点的 sync_type 映射 + 它们用哪个时间字段
# time_field=None 的 plan 不走时间窗口，由 logistics_target_ids 自己选目标
# `watermark` 字段（可选）：None 表示不启用 L1；dict 表示启用：
#   - table:    本地表名，查 MAX 用的源
#   - column:   本地表的时间列名（必须是 update_time 语义）
#   - body_field: 上游 body 字段名（如 "update_time_ge"）
# 仅 orders / returns / cancellations 这 3 个有 update_time 列的表启用
SYNC_PLANS = [
    {
        "key": "orders",
        "path": "/sync/orders",
        "time_field": "create_time_ge",
        "watermark": {
            "table": "orders",
            "column": "update_time",
            "body_field": "update_time_ge",
        },
        "log_type": "orders_search",
    },
    {
        "key": "payments",
        "path": "/sync/payments",
        "time_field": "create_time_ge",
        "watermark": None,  # payments 表没有 update_time 列
        "log_type": "payments",
    },
    {
        "key": "statements",
        "path": "/sync/statements",
        "time_field": "statement_time_ge",
        "watermark": None,  # statements 表用 statement_time，无 update_time 列
        "log_type": "statements",
    },
    {
        "key": "returns",
        "path": "/sync/returns",
        "time_field": "create_time_ge",
        "watermark": {
            "table": "returns",
            "column": "update_time",
            "body_field": "update_time_ge",
        },
        "log_type": "returns",
    },
    {
        "key": "cancellations",
        "path": "/sync/cancellations",
        "time_field": "create_time_ge",
        "watermark": {
            "table": "cancellations",
            "column": "update_time",
            "body_field": "update_time_ge",
        },
        "log_type": "cancellations",
    },
    {
        "key": "logistics",
        "path": "/sync/logistics_tracking",
        "time_field": None,
        "watermark": None,  # 物流按 order_ids 选目标，不走时间窗口
        "log_type": "logistics_tracking",
    },
    {
        "key": "stmt_txns",
        "path": "/sync/statement_transactions",
        "time_field": "statement_time_ge",
        "watermark": None,  # statement_transactions 用 statement_time
        "log_type": "statement_transactions",
    },
]

# 物流终态：到这些状态后轨迹不再变化，停止重复拉取
LOGISTICS_FINAL_STATUSES = ("DELIVERED", "RETURNED_TO_SELLER")
# 每轮物流追踪的订单数上限（防御，正常远达不到）
LOGISTICS_TARGET_LIMIT = 300


# ─── HTTP 客户端 ──────────────────────────────────────────────────────
def http_json(
    method: str,
    url: str,
    body: dict | None = None,
    timeout: int = 30,
    api_key: str | None = None,
) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    try:
        # URL is internal http://127.0.0.1:9877 only — no scheme allowlist needed.
        # TODO(hardening): replace urllib with httpx + explicit URL validation
        # (currently https-only in spirit but urllib allows file:// etc.).
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.load(r)
    except urllib.error.HTTPError as e:
        # 把 4xx/5xx 响应体也读回来（tts-erp 的 _error 字段有用）
        try:
            payload = json.load(e)
        except Exception:
            payload = {"_raw": e.read().decode("utf-8", errors="replace")}
        return {"_http_status": e.code, "_error": True, **payload}
    except urllib.error.URLError as e:
        return {"_error": True, "_reason": f"URLError: {e.reason}"}


# ─── 业务函数 ────────────────────────────────────────────────────────
def discover_shops(provider: str = "tiktok") -> list[str]:
    """拿所有已授权 shop_id。

    Wave 3 Slice 2 起 /shops HTTP 路由被删;这里直接 in-process 调
    oauth_receiver_core.db_list_shops()。DB 不可达时返回 [] 而不是抛异常,
    让 main() 自然走到 "no shops authorized yet — nothing to sync" 分支。
    """
    if not oauth_receiver_core.is_db_ok():
        return []
    items = oauth_receiver_core.db_list_shops(provider=provider)
    # Filter test/mock shop_ids (e.g. MOCK_SHOP_12345 from
    # oauth_receiver_core.py:569 hardcoded fallback that leaked into
    # production on 2026-08-25). Real TikTok shop_ids are 19-digit
    # numbers; MOCK_ prefix is the test sentinel. Without this filter
    # cron runs 7 plans × 144 ticks/day = 1008 wasted upstream calls
    # against TikTok that all fail with 'Invalid shop_cipher' and burn
    # the rate limit budget (the actual root cause of the 429 errors
    # we kept seeing).
    return [
        s["shop_id"]
        for s in items
        if s.get("shop_id") and not s["shop_id"].startswith("MOCK_")
    ]


def last_sync_epoch(
    conn: psycopg.Connection, shop_id: str, log_type: str
) -> int | None:
    """查 sync_log 表里这个 shop+log_type 最近一次成功 finished_at（unix epoch sec）"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM MAX(finished_at))::bigint
            FROM sync_log
            WHERE shop_id = %s AND sync_type = %s AND status = 'ok'
              AND finished_at IS NOT NULL
            """,
            (shop_id, log_type),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    # Pre-existing code; defensive against unexpected non-int value (shouldn't
    # happen since finished_at is NOT NULL BIGINT in sync_log schema).
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def compute_window(last_epoch: int | None, now_epoch: int) -> int:
    """计算本次同步的下界（unix epoch sec）"""
    if last_epoch is None:
        return now_epoch - FALLBACK_LOOKBACK_SEC
    # 减 5 分钟防漂移；但不早于 FALLBACK_LOOKBACK
    floor = now_epoch - FALLBACK_LOOKBACK_SEC
    return max(last_epoch - WINDOW_BACKOFF_SEC, floor)


# ─── L1 watermark helpers ─────────────────────────────────────────────


def watermark_value(
    conn: psycopg.Connection,
    shop_id: str,
    table: str,
    column: str,
) -> int | None:
    """查本地表里这个 shop 的 MAX(<column>)（unix epoch sec），无数据返回 None.

    table/column 来自 SYNC_PLANS 配置，但仍走 psycopg.sql.Identifier
    来做正确引用 + 防注入 + 让类型检查器满意。
    L1 watermark 优化用：让 cron 知道本地数据"新到什么时候"，从而：
      - 跳过太新鲜的数据（避免无意义的调用）
      - 用 update_time_ge 替代 create_time_ge 做增量同步
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT MAX({col}) FROM {tbl} WHERE shop_id = %s").format(
                col=sql.Identifier(column),
                tbl=sql.Identifier(table),
            ),
            (shop_id,),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def compute_l1_body(
    plan: dict,
    shop_id: str,
    local_watermark: int | None,
    now_epoch: int,
) -> dict | None:
    """决定 cron 本轮 sync 该发给上游什么 body。

    返回 None 表示跳过整个 HTTP 调用（L1 主收益）。

    四种分支：
      1) plan 没有 watermark 配置（payments/statements/物流/stmt_txns）
         → 7 天 create_time 回填，保持原行为
      2) plan 有 watermark 但 local_watermark=None（首次同步 / 新 shop）
         → 7 天 create_time 回填（没有锚点可用）
      3) plan 有 watermark 且 local 存在，但 (now - watermark) < FRESHNESS_THRESHOLD_SEC
         → 返回 None，跳过调用（L1 主收益）
      4) plan 有 watermark 且 local 存在且 stale
         → 用 update_time_ge = watermark - WATERMARK_OVERLAP_SEC 增量同步
    """
    wm = plan.get("watermark")
    # 分支 1: 没有 watermark 配置 → 7 天回填（保持原行为）
    if not wm:
        return {
            "shop_id": shop_id,
            plan["time_field"]: now_epoch - FALLBACK_LOOKBACK_SEC,
            "page_size": 50,
        }
    # 分支 2: 有配置但无本地数据 → 7 天 create_time 回填
    if local_watermark is None:
        return {
            "shop_id": shop_id,
            plan["time_field"]: now_epoch - FALLBACK_LOOKBACK_SEC,
            "page_size": 50,
        }
    # 分支 3: 本地数据太新鲜 → 跳过整个调用
    if (now_epoch - local_watermark) <= FRESHNESS_THRESHOLD_SEC:
        return None
    # 分支 4: stale → 用 update_time_ge 增量同步
    return {
        "shop_id": shop_id,
        wm["body_field"]: local_watermark - WATERMARK_OVERLAP_SEC,
        "page_size": 50,
    }


def logistics_target_ids(conn: psycopg.Connection, shop_id: str) -> list[str]:
    """本轮要追踪物流的订单：有运单号，且还没同步过物流 / 未到终态。

    终态（DELIVERED / RETURNED_TO_SELLER）的订单轨迹不再变化，不再重复拉。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.order_id
            FROM order_shippings s
            LEFT JOIN logistics_tracking lt ON lt.order_id = s.order_id
            WHERE s.shop_id = %s
              AND s.tracking_number IS NOT NULL AND s.tracking_number <> ''
              AND (lt.order_id IS NULL
                   OR lt.final_status IS NULL
                   OR lt.final_status <> ALL(%s))
            ORDER BY s.order_id DESC
            LIMIT %s
            """,
            (shop_id, list(LOGISTICS_FINAL_STATUSES), LOGISTICS_TARGET_LIMIT),
        )
        return [r[0] for r in cur.fetchall()]


# ─── 主流程 ──────────────────────────────────────────────────────────
def setup_logging() -> logging.Logger:
    """Configure tts-erp cron logger using the shared log_helper.

    Writes to logs/tts-erp-cron.log (daily rotated, keep 7 days).
    The daily suffix (.YYYYMMDD) replaces the old cron_sync_<date>.log files.
    """
    return _helper_setup("tts-erp-cron", LOGS_DIR, level=logging.INFO, backup_days=7)


def main() -> int:
    log = setup_logging()
    started = time.time()
    log.info("=" * 60)
    log.info("tts-erp cron sync start")

    env = load_env(ENV_FILE)
    db_url = env.get("TTS_ERP_DB_URL")
    port = env.get("TTS_ERP_PORT", "9877")
    service_key = env.get("TTS_ERP_SERVICE_KEY")
    if not db_url:
        log.error("TTS_ERP_DB_URL missing in .env — abort")
        return 2
    if not service_key:
        log.warning(
            "TTS_ERP_SERVICE_KEY missing in .env — fine while auth is off/shadow, BREAKS under enforce"
        )
    base_url = f"http://127.0.0.1:{port}"
    log.info("tts-erp base = %s", base_url)

    # 1) 发现 shops（in-process via oauth_receiver_core; Wave 3 Slice 2）
    try:
        shops = discover_shops()
    except Exception as e:
        log.error("discover_shops failed: %s", e)
        return 3
    log.info("discovered %d shop(s): %s", len(shops), shops)
    if not shops:
        log.warning("no shops authorized yet — nothing to sync")
        return 0

    # 2) 遍历 (shop, sync_type) 同步
    summary: dict[str, dict[str, int]] = {}
    # time.time() returns float; int(float) cannot raise in practice.
    # Defensive only against future time.* refactors (e.g. Decimal return).
    try:
        now_epoch = int(time.time())
    except (TypeError, ValueError, OverflowError):
        now_epoch = 0

    with psycopg.connect(db_url, connect_timeout=10) as conn:
        for shop_id in shops:
            for plan in SYNC_PLANS:
                key = plan["key"]
                summary.setdefault(key, {"ok": 0, "err": 0, "saved": 0, "shop_err": 0})

                if plan["time_field"]:
                    # L1 watermark optimization: for plans with watermark
                    # config, ask compute_l1_body to decide body / skip.
                    # Plans without watermark (payments/statements/etc.)
                    # keep the original compute_window() behavior.
                    wm = plan.get("watermark")
                    if wm:
                        local_wm = watermark_value(
                            conn, shop_id, wm["table"], wm["column"]
                        )
                        body = compute_l1_body(plan, shop_id, local_wm, now_epoch)
                        if body is None:
                            log.info(
                                "[%s] shop=%s local_wm=%s fresh (<%ds) — skip",
                                key,
                                shop_id,
                                datetime.fromtimestamp(
                                    local_wm, tz=timezone.utc
                                ).isoformat()
                                if local_wm
                                else "None",
                                FRESHNESS_THRESHOLD_SEC,
                            )
                            summary[key]["ok"] += 1
                            continue
                        log.info(
                            "[%s] shop=%s local_wm=%s body=%s",
                            key,
                            shop_id,
                            datetime.fromtimestamp(
                                local_wm, tz=timezone.utc
                            ).isoformat()
                            if local_wm
                            else "None",
                            body,
                        )
                    else:
                        last_epoch = last_sync_epoch(conn, shop_id, plan["log_type"])
                        ge = compute_window(last_epoch, now_epoch)
                        body = {
                            "shop_id": shop_id,
                            plan["time_field"]: ge,
                            "page_size": 50,
                        }
                        log.info(
                            "[%s] shop=%s last=%s ge=%d body=%s",
                            key,
                            shop_id,
                            datetime.fromtimestamp(
                                last_epoch, tz=timezone.utc
                            ).isoformat()
                            if last_epoch
                            else "None",
                            ge,
                            body,
                        )
                else:
                    # 物流追踪：不走时间窗口，按"有运单号且未到终态"选目标
                    order_ids = logistics_target_ids(conn, shop_id)
                    if not order_ids:
                        log.info(
                            "[%s] shop=%s no active logistics targets — skip",
                            key,
                            shop_id,
                        )
                        summary[key]["ok"] += 1
                        continue
                    body = {"shop_id": shop_id, "order_ids": order_ids}
                    log.info("[%s] shop=%s targets=%d", key, shop_id, len(order_ids))
                # Anti-burst jitter: spread 6 plans across ~30s per tick
                # so we don't fire 6 different TikTok endpoints inside 10s,
                # which is what was triggering 429 "Too many requests".
                # Sleeping BEFORE the call (not after) keeps the post-
                # call branches deterministic.
                time.sleep(random.uniform(2, 8))
                t0 = time.time()
                result = http_json(
                    "POST",
                    f"{base_url}{plan['path']}",
                    body,
                    timeout=HTTP_TIMEOUT,
                    api_key=service_key,
                )
                dt = time.time() - t0

                if result.get("_error"):
                    log.error(
                        "[%s] shop=%s FAIL in %.1fs — %s",
                        key,
                        shop_id,
                        dt,
                        json.dumps(result, ensure_ascii=False)[:400],
                    )
                    summary[key]["err"] += 1
                    continue

                saved = result.get("saved", 0)
                total = result.get("total", 0)
                pages = result.get("pages", 1)
                code = result.get("code", 0)
                log.info(
                    "[%s] shop=%s OK in %.1fs saved=%d total=%d pages=%d code=%s",
                    key,
                    shop_id,
                    dt,
                    saved,
                    total,
                    pages,
                    code,
                )
                summary[key]["ok"] += 1
                summary[key]["saved"] += saved

    elapsed = time.time() - started
    log.info("done in %.1fs", elapsed)
    for key, s in summary.items():
        log.info("  %-13s ok=%d err=%d saved=%d", key, s["ok"], s["err"], s["saved"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.getLogger("tts-erp-cron").exception(
            "unhandled exception"
        )  # matches logger name in setup_logging()
        sys.exit(1)

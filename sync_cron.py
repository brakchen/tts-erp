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
  4. 汇总结果写 logs/cron_sync_<date>.log

不要在这做：
  - 直接调 TikTok API（这层是 tts_erp.py 的活）
  - 直接写 PG 业务表（也是 tts_erp.py 的活）
  - 改 /sync/* 端点逻辑（除非有 bug）

依赖：psycopg 3.x + Python 3.10+（用了 match-case / 类型注解 syntax）
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg

# ─── 配置 ────────────────────────────────────────────────────────────
TTS_ERP_DIR = Path("/home/schan/tts-erp")
ENV_FILE = TTS_ERP_DIR / ".env"
LOGS_DIR = TTS_ERP_DIR / "logs"

# 5 分钟缓冲，防止 TikTok 服务端时钟漂移或异步落库导致漏边界订单
WINDOW_BACKOFF_SEC = 5 * 60
# 首次跑 / 新 shop 没有 sync_log 历史时的回退窗口（7 天）
FALLBACK_LOOKBACK_SEC = 7 * 24 * 3600
# HTTP 超时（秒）—— _sync_* 会翻页最多 50 次，单次给 3 分钟
HTTP_TIMEOUT = 180
# /sync/* 端点的 sync_type 映射 + 它们用哪个时间字段
SYNC_PLANS = [
    {"key": "orders",        "path": "/sync/orders",        "time_field": "create_time_ge",     "log_type": "orders_search"},
    {"key": "payments",      "path": "/sync/payments",      "time_field": "create_time_ge",     "log_type": "payments"},
    {"key": "statements",    "path": "/sync/statements",    "time_field": "statement_time_ge",  "log_type": "statements"},
    {"key": "returns",       "path": "/sync/returns",       "time_field": "create_time_ge",     "log_type": "returns"},
    {"key": "cancellations", "path": "/sync/cancellations", "time_field": "create_time_ge",     "log_type": "cancellations"},
]


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


# ─── HTTP 客户端 ──────────────────────────────────────────────────────
def http_json(method: str, url: str, body: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
def discover_shops(base_url: str) -> list[str]:
    """调 GET /shops 拿所有已授权 shop_id（oauth-receiver 是 SSoT）"""
    data = http_json("GET", f"{base_url}/shops", timeout=10)
    if data.get("_error"):
        raise RuntimeError(f"/shops failed: {data}")
    items = data.get("items") or []
    return [s["shop_id"] for s in items if s.get("shop_id")]


def last_sync_epoch(conn: psycopg.Connection, shop_id: str, log_type: str) -> int | None:
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
    return int(row[0])


def compute_window(last_epoch: int | None, now_epoch: int) -> int:
    """计算本次同步的下界（unix epoch sec）"""
    if last_epoch is None:
        return now_epoch - FALLBACK_LOOKBACK_SEC
    # 减 5 分钟防漂移；但不早于 FALLBACK_LOOKBACK
    floor = now_epoch - FALLBACK_LOOKBACK_SEC
    return max(last_epoch - WINDOW_BACKOFF_SEC, floor)


# ─── 主流程 ──────────────────────────────────────────────────────────
def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path = LOGS_DIR / f"cron_sync_{today}.log"

    fmt = "%(asctime)s %(levelname)-5s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("sync_cron")


def main() -> int:
    log = setup_logging()
    started = time.time()
    log.info("=" * 60)
    log.info("tts-erp cron sync start")

    env = load_env(ENV_FILE)
    db_url = env.get("TTS_ERP_DB_URL")
    port = env.get("TTS_ERP_PORT", "9877")
    if not db_url:
        log.error("TTS_ERP_DB_URL missing in .env — abort")
        return 2
    base_url = f"http://127.0.0.1:{port}"
    log.info("tts-erp base = %s", base_url)

    # 1) 发现 shops
    try:
        shops = discover_shops(base_url)
    except Exception as e:
        log.error("discover_shops failed: %s", e)
        return 3
    log.info("discovered %d shop(s): %s", len(shops), shops)
    if not shops:
        log.warning("no shops authorized yet — nothing to sync")
        return 0

    # 2) 遍历 (shop, sync_type) 同步
    summary: dict[str, dict[str, int]] = {}
    now_epoch = int(time.time())

    with psycopg.connect(db_url, connect_timeout=10) as conn:
        for shop_id in shops:
            for plan in SYNC_PLANS:
                key = plan["key"]
                summary.setdefault(key, {"ok": 0, "err": 0, "saved": 0, "shop_err": 0})

                last_epoch = last_sync_epoch(conn, shop_id, plan["log_type"])
                ge = compute_window(last_epoch, now_epoch)
                body = {
                    "shop_id": shop_id,
                    plan["time_field"]: ge,
                    "page_size": 50,
                }
                log.info(
                    "[%s] shop=%s last=%s ge=%d body=%s",
                    key, shop_id,
                    datetime.fromtimestamp(last_epoch, tz=timezone.utc).isoformat() if last_epoch else "None",
                    ge, body,
                )
                t0 = time.time()
                result = http_json("POST", f"{base_url}{plan['path']}", body, timeout=HTTP_TIMEOUT)
                dt = time.time() - t0

                if result.get("_error"):
                    log.error(
                        "[%s] shop=%s FAIL in %.1fs — %s",
                        key, shop_id, dt, json.dumps(result, ensure_ascii=False)[:400],
                    )
                    summary[key]["err"] += 1
                    continue

                saved = result.get("saved", 0)
                total = result.get("total", 0)
                pages = result.get("pages", 1)
                code = result.get("code", 0)
                log.info(
                    "[%s] shop=%s OK in %.1fs saved=%d total=%d pages=%d code=%s",
                    key, shop_id, dt, saved, total, pages, code,
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
        logging.getLogger("sync_cron").exception("unhandled exception")
        sys.exit(1)

"""oneoff_ad_raw_report — 从 analytics.ad_raw 生成「campaign_id × spu 广告消耗 / 出单数」报告。

数据源：post_product_list（productAnalyses）dumps。每行 dump 的 response.body.data.table[]
每个元素 = 该 campaign 下一个 spu 的当日指标（TikTok 商城广告 ROI2 口径）：
  mixed_real_cost                    = 广告消耗（花费）
  onsite_roi2_shopping_sku           = 出单（ROI2 商城成交件数）
  onsite_roi2_shopping_value         = GMV（ROI2 商城成交金额）
spu_id / product_id 为同一 SPU id（table_v2 里 name=spu_id 与 product_id 同值），
product_name 为 SPU 标题。行粒度 = (seller, advertiser, day, campaign_id)，可跨日累加。

产出（输出目录 reports/）：
  ad_spu_report.csv            ★主表：按 spu_id 聚合（跨全部 campaign/天累计）
  ad_campaign_spu_report.csv  明细：每个 campaign_id × spu_id 一行
  ad_campaign_report.csv      汇总：每个 campaign_id 一行（spu 聚合）
用法: PYTHONPATH=. .venv/bin/python scripts/oneoff_ad_raw_report.py
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text  # noqa: E402

from tts_erp_v2.db.base import get_engine  # noqa: E402

EP = "/oec_ads/shopping/v1/oec/stat/post_product_list"
OUT_DIR = Path(__file__).resolve().parent.parent / "reports"


@dataclass
class SpuStat:
    spu_id: str
    product_name: str
    cost: float = 0.0      # 广告消耗
    sku: int = 0           # 出单（成交件数）
    gmv: float = 0.0       # 成交金额
    days: set = field(default_factory=set)


def _f(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows(resp: dict) -> list[dict]:
    """从 dump response 里取 product 行（table 优先，退 table_v2 name/data 映射）。"""
    body = (resp or {}).get("body", {}) or {}
    data = body.get("data", {}) or {}
    table = data.get("table", []) or []
    if not table:
        flat: dict[str, object] = {}
        for item in (data.get("table_v2", []) or []):
            for kv in item or []:
                if isinstance(kv, dict) and kv.get("name"):
                    flat[kv["name"]] = kv.get("data")
        if flat:
            table = [flat]
    return [r for r in table if isinstance(r, dict)]


def main() -> None:
    eng = get_engine()
    # campaign_id -> spu_id -> SpuStat
    by_campaign: dict[str, dict[str, SpuStat]] = defaultdict(dict)
    shop = {"seller_id": None, "advertiser_id": None}
    day_range = {"min": None, "max": None}

    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT seller_id, advertiser_id, day, campaign_id, response "
                "FROM analytics.ad_raw WHERE endpoint = :ep ORDER BY day"
            ),
            {"ep": EP},
        ).all()
        for seller_id, advertiser_id, day, campaign_id, resp in rows:
            shop["seller_id"] = seller_id
            shop["advertiser_id"] = advertiser_id
            d = day_range["min"]
            day_range["min"] = day if d is None or day < d else d
            d = day_range["max"]
            day_range["max"] = day if d is None or day > d else d
            for r in _rows(resp):
                spu = r.get("spu_id") or r.get("product_id")
                if not spu:
                    continue
                st = by_campaign[campaign_id].setdefault(
                    str(spu),
                    SpuStat(spu_id=str(spu), product_name=str(r.get("product_name") or "")),
                )
                st.cost += _f(r.get("mixed_real_cost"))
                st.sku += int(_f(r.get("onsite_roi2_shopping_sku")))
                st.gmv += _f(r.get("onsite_roi2_shopping_value"))
                st.days.add(str(day))

    if not by_campaign:
        print("no data rows", file=sys.stderr)
        return

    # 落盘目录
    OUT_DIR.mkdir(exist_ok=True)
    seller = shop["seller_id"]
    adv = shop["advertiser_id"]

    # ── CSV 1: campaign × spu ──────────────────────────────────────
    f1 = OUT_DIR / "ad_campaign_spu_report.csv"
    with f1.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "campaign_id", "spu_id", "product_name", "day_from", "day_to",
                "days_with_data", "ad_spend", "orders_sku", "gmv", "roi",
            ]
        )
        for cid in sorted(by_campaign, key=lambda c: -sum(s.cost for s in by_campaign[c].values())):
            for spu in sorted(
                by_campaign[cid].values(), key=lambda s: -s.cost
            ):
                roi = spu.gmv / spu.cost if spu.cost else 0.0
                w.writerow(
                    [
                        cid, spu.spu_id, spu.product_name,
                        min(spu.days), max(spu.days), len(spu.days),
                        round(spu.cost, 2), spu.sku, round(spu.gmv, 2),
                        round(roi, 2),
                    ]
                )

    # ── CSV 2: campaign 汇总（含 spu_ids 列）─────────────────────────
    f2 = OUT_DIR / "ad_campaign_report.csv"
    with f2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "campaign_id", "spu_ids", "spu_count", "day_from", "day_to",
                "days_with_data", "ad_spend", "orders_sku", "gmv", "roi",
            ]
        )
        for cid in sorted(by_campaign, key=lambda c: -sum(s.cost for s in by_campaign[c].values())):
            sts = sorted(by_campaign[cid].values(), key=lambda s: -s.cost)
            cost = sum(s.cost for s in sts)
            sku = sum(s.sku for s in sts)
            gmv = sum(s.gmv for s in sts)
            all_days = set().union(*(s.days for s in sts)) if sts else set()
            w.writerow(
                [
                    cid,
                    ";".join(s.spu_id for s in sts),
                    len(sts), min(all_days), max(all_days), len(all_days),
                    round(cost, 2), sku, round(gmv, 2),
                    round(gmv / cost, 2) if cost else 0.0,
                ]
            )

    # ── CSV 3: spu_id 聚合（跨全部 campaign 汇总，主表）─────────────
    by_spu: dict[str, SpuStat] = {}
    spu_campaigns: dict[str, set[str]] = defaultdict(set)
    for cid, m in by_campaign.items():
        for s in m.values():
            t = by_spu.setdefault(s.spu_id, SpuStat(spu_id=s.spu_id, product_name=s.product_name))
            t.cost += s.cost
            t.sku += s.sku
            t.gmv += s.gmv
            t.days |= s.days
            spu_campaigns[s.spu_id].add(cid)

    f3 = OUT_DIR / "ad_spu_report.csv"
    with f3.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "spu_id", "product_name", "campaign_count", "campaign_ids",
                "day_from", "day_to", "days_with_data",
                "ad_spend", "orders_sku", "gmv", "roi",
            ]
        )
        for s in sorted(by_spu.values(), key=lambda s: -s.cost):
            roi = s.gmv / s.cost if s.cost else 0.0
            w.writerow(
                [
                    s.spu_id, s.product_name, len(spu_campaigns[s.spu_id]),
                    ";".join(sorted(spu_campaigns[s.spu_id])),
                    min(s.days), max(s.days), len(s.days),
                    round(s.cost, 2), s.sku, round(s.gmv, 2), round(roi, 2),
                ]
            )

    total_cost = sum(s.cost for m in by_campaign.values() for s in m.values())
    total_sku = sum(s.sku for m in by_campaign.values() for s in m.values())
    total_gmv = sum(s.gmv for m in by_campaign.values() for s in m.values())
    n_campaign = len(by_campaign)
    n_spu = len(by_spu)
    print(f"shop: seller={seller} advertiser={adv}")
    print(f"period: {day_range['min']} .. {day_range['max']}")
    print(
        f"campaigns={n_campaign} spus={n_spu} | "
        f"total spend={total_cost:.2f} orders(sku)={total_sku} gmv={total_gmv:.2f} "
        f"roi={total_gmv / total_cost:.2f}" if total_cost else "no spend"
    )
    print(f"\nwrote:\n  {f1}  (campaign × spu 明细)\n  {f2}  (campaign 汇总)\n  {f3}  (spu 聚合 ★)")


if __name__ == "__main__":
    main()

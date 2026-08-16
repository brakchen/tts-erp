#!/usr/bin/env python3
"""Export order_ids that have cancellations and/or returns/refunds.

Output (all to F:\\MiniMax Work Result\\tts-erp\\exports\\):
  - cancellations.csv   取消的订单 + 详情
  - returns.csv         退货/退款的订单 + 详情
  - both.csv            同时有取消和退款的（按理不该有，验证用）
  - cancellations_ids.txt    每行一个 order_id（喂其他脚本）
  - returns_ids.txt          每行一个 order_id
  - either_ids.txt           取消 OR 退款 的并集
  - summary.txt              数字 + 时间戳
"""
import csv
import os
import sys
from datetime import datetime

import psycopg

# Load .env
env = {}
for ln in open("/home/schan/tts-erp/.env"):
    ln = ln.strip()
    if "=" in ln and not ln.startswith("#"):
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip()
DB = env["TTS_ERP_DB_URL"]
SHOP = "7494763368967603447"

OUT_DIR = "F:/MiniMax Work Result/tts-erp/exports"
os.makedirs(OUT_DIR, exist_ok=True)

with psycopg.connect(DB) as c, c.cursor() as cur:
    # 取消
    cur.execute("""
        SELECT order_id, cancel_id, cancel_status, cancel_type, cancel_reason,
               cancel_reason_text, role, to_timestamp(create_time) AT TIME ZONE 'UTC' AS created_utc
        FROM cancellations
        WHERE shop_id = %s
        ORDER BY create_time DESC
    """, (SHOP,))
    cancels = cur.fetchall()

    # 退货/退款
    cur.execute("""
        SELECT order_id, return_id, return_status, return_type, return_reason,
               (raw->'refund_amount'->>'refund_total')::numeric(18,2) AS refund_total,
               raw->'refund_amount'->>'currency' AS currency,
               to_timestamp(create_time) AT TIME ZONE 'UTC' AS created_utc
        FROM returns
        WHERE shop_id = %s
        ORDER BY create_time DESC
    """, (SHOP,))
    rets = cur.fetchall()

# 写 CSV
with open(f"{OUT_DIR}/cancellations.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "cancel_id", "cancel_status", "cancel_type", "cancel_reason", "cancel_reason_text", "role", "created_utc"])
    w.writerows(cancels)

with open(f"{OUT_DIR}/returns.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "return_id", "return_status", "return_type", "return_reason", "refund_total", "currency", "created_utc"])
    w.writerows(rets)

# 仅 ID（每行一个，方便喂给其他工具）
cancel_ids = sorted({r[0] for r in cancels})
return_ids = sorted({r[0] for r in rets})
both_ids  = sorted(set(cancel_ids) & set(return_ids))
either_ids = sorted(set(cancel_ids) | set(return_ids))

with open(f"{OUT_DIR}/cancellations_ids.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(cancel_ids))
with open(f"{OUT_DIR}/returns_ids.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(return_ids))
with open(f"{OUT_DIR}/both_ids.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(both_ids))
with open(f"{OUT_DIR}/either_ids.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(either_ids))

# 同时有取消和退款的（理论上不该有）
with open(f"{OUT_DIR}/both.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "in_cancellations", "in_returns"])
    for oid in either_ids:
        w.writerow([oid, oid in cancel_ids, oid in return_ids])

# summary
with open(f"{OUT_DIR}/summary.txt", "w", encoding="utf-8") as f:
    f.write(f"导出时间: {datetime.utcnow().isoformat()}Z\n")
    f.write(f"shop_id: {SHOP}\n")
    f.write(f"\n")
    f.write(f"取消订单 (unique order_id): {len(cancel_ids)}\n")
    f.write(f"取消记录 (total rows):     {len(cancels)}\n")
    f.write(f"退款订单 (unique order_id): {len(return_ids)}\n")
    f.write(f"退款记录 (total rows):     {len(rets)}\n")
    f.write(f"同时有取消和退款的订单:    {len(both_ids)}\n")
    f.write(f"取消 OR 退款 (并集):       {len(either_ids)}\n")

print(f"=== 导出完成 ===")
print(f"输出目录: {OUT_DIR}")
print(f"取消订单 (unique): {len(cancel_ids)}")
print(f"退款订单 (unique): {len(return_ids)}")
print(f"同时有两者:        {len(both_ids)}")
print(f"取消 OR 退款:      {len(either_ids)}")
print()
print("生成的文件:")
for f in sorted(os.listdir(OUT_DIR)):
    sz = os.path.getsize(f"{OUT_DIR}/{f}")
    print(f"  {f:30s}  {sz:>8} bytes")

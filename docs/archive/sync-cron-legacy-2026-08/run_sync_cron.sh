#!/usr/bin/env bash
# tts-erp cron 包装脚本 — 让 cron 调这个，不直接调 python
# 作用：固定 cwd、把 exit code 透传给 cron（不用 tee，crontab 自己 >> 到 logs/cron.log）

set -euo pipefail

TTS_ERP_DIR="/home/schan/tts-erp"

mkdir -p "$TTS_ERP_DIR/logs"

# 跑到 tts-erp 目录是为了 sync_cron.py 能用相对路径 .env 和 logs/
cd "$TTS_ERP_DIR"

# W1.7: 慢 tick（50 页翻页 + 物流大单量）可能超过 10 分钟与下一轮重叠。
# upsert 幂等所以无正确性问题，但重叠会双倍打 TikTok 429 配额。
# flock -n：拿不到锁直接退出（exit 0，非错误——上一轮还在跑）。
exec flock -n /tmp/tts-erp-sync.lock /usr/bin/python3 "$TTS_ERP_DIR/sync_cron.py"

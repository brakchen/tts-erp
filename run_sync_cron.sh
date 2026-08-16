#!/usr/bin/env bash
# tts-erp cron 包装脚本 — 让 cron 调这个，不直接调 python
# 作用：固定 cwd、把 exit code 透传给 cron（不用 tee，crontab 自己 >> 到 logs/cron.log）

set -euo pipefail

TTS_ERP_DIR="/home/schan/tts-erp"

mkdir -p "$TTS_ERP_DIR/logs"

# 跑到 tts-erp 目录是为了 sync_cron.py 能用相对路径 .env 和 logs/
cd "$TTS_ERP_DIR"

# 跑同步；python 内部 logging 已写 logs/cron_sync_<date>.log，
# crontab 配置 `>> /home/schan/tts-erp/logs/cron.log` 会把这份 stdout 也保存。
exec /usr/bin/python3 "$TTS_ERP_DIR/sync_cron.py"

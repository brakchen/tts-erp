#!/usr/bin/env bash
# install-sync-worker.sh — 安装 sync-worker 的 systemd 单元
# 用法：bash /home/schan/tts-erp/prod-switch/install-sync-worker.sh
# 一次性，永久生效
set -euo pipefail

REPO=/home/schan/tts-erp
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/tts-erp-sync.service" <<EOF
[Unit]
Description=tts-erp v2 sync-worker (APScheduler)
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$REPO/.venv/bin/python -m tts_erp_v2.sync_worker.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable tts-erp-sync.service
echo "→ Installed tts-erp-sync.service"
echo "  Start with:  systemctl --user start tts-erp-sync.service"
echo "  Status:       systemctl --user status tts-erp-sync.service"
#!/bin/bash
# Restart tts-erp service (managed by systemd --user since 2026-08-18).
# The app reads a local .env first, then the secure reloadable runtime file.
set -e
cd /home/schan/tts-erp

RUNTIME_ENV_FILE="${TTS_ERP_RUNTIME_ENV_FILE:-$HOME/.config/tts-erp/runtime.env}"
CONFIG_FILE=/home/schan/tts-erp/.env
if [[ ! -r "$CONFIG_FILE" ]]; then
  CONFIG_FILE="$RUNTIME_ENV_FILE"
fi

PORT="$(grep -E '^TTS_ERP_PORT=' "$CONFIG_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
PORT="${PORT:-9877}"

echo "restarting via systemd --user..."
systemctl --user restart tts-erp.service
sleep 2

echo
echo "=== service ==="
systemctl --user --no-pager status tts-erp.service | head -5
echo
echo "=== port ==="
ss -tlnp | grep "$PORT" || echo "NO_PORT"
echo
echo "=== last 5 stdout lines ==="
tail -5 /home/schan/tts-erp/logs/stdout.log
echo
echo "=== healthz ==="
curl -s "http://127.0.0.1:$PORT/healthz" || echo "HEALTHZ_FAILED"

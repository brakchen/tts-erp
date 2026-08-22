#!/bin/bash
# Restart tts-erp service (managed by systemd --user since 2026-08-18).
# The unit loads .env via EnvironmentFile and runs uvicorn from tdd/;
# logs still go to logs/{stdout,stderr}.log.
set -e
cd /home/schan/tts-erp

PORT="$(grep -E '^TTS_ERP_PORT=' .env | cut -d= -f2)"
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

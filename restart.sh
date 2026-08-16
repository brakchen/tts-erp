#!/bin/bash
# Restart tts-erp service (FastAPI version since 2026-08-16)
set -e
cd /home/schan/tts-erp

echo "killing old tts_erp + uvicorn processes..."
pkill -9 -f "tts_erp.py" 2>/dev/null || true
pkill -9 -f "uvicorn.*tts_erp_fastapi" 2>/dev/null || true
sleep 1

echo "loading .env..."
set -a
. ./.env
set +a

# Ensure log dir exists
mkdir -p logs

# Choose port: TTS_ERP_PORT env var (default 9877)
PORT="${TTS_ERP_PORT:-9877}"

echo "starting FastAPI on port $PORT..."
cd /home/schan/tts-erp/tdd
nohup python3 -m uvicorn tts_erp_fastapi:app --host 0.0.0.0 --port "$PORT" \
    >> /home/schan/tts-erp/logs/stdout.log 2>> /home/schan/tts-erp/logs/stderr.log < /dev/null &
disown
sleep 2

echo
echo "=== process ==="
pgrep -af "uvicorn.*tts_erp_fastapi" | grep -v "bash\|pgrep\|restart" || echo "NO_PID"
echo
echo "=== port ==="
ss -tlnp | grep "$PORT" || echo "NO_PORT"
echo
echo "=== last 5 stdout lines ==="
tail -5 /home/schan/tts-erp/logs/stdout.log
echo
echo "=== healthz ==="
curl -s "http://127.0.0.1:$PORT/healthz" || echo "HEALTHZ_FAILED"

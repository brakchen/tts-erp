#!/usr/bin/env bash
# One-command, human-operated rollout for migration 0045.
#
# Production:
#   ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0045_miaoshou_package_schema.sh --confirm
#
# The script stops the sync worker, writes a scoped gzip JSONL backup, applies
# exactly revision 0045, verifies relocation/cleanup, performs one package sync,
# and restores the worker. It never sets ALLOW_PROD_DESTRUCTIVE itself.
set -euo pipefail

SCRIPT_PATH=$(realpath "$0")
REPO_ROOT=$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)
cd "$REPO_ROOT"

TARGET="0045_miaoshou_package_schema"
MIGRATION_FILE="alembic/versions/0045_miaoshou_package_schema.py"
PYTHON=".venv/bin/python"
ALEMBIC=".venv/bin/alembic"
CONFIRMED=0

usage() {
    cat <<EOF
用法：
  ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0045_miaoshou_package_schema.sh --confirm

步骤：停止 sync worker → scoped backup → migration 0045 → 验证 → 补跑 package sync → 恢复 worker
EOF
}

for arg in "$@"; do
    case "$arg" in
    --confirm) CONFIRMED=1 ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "❌ 未知参数: $arg" >&2
        usage >&2
        exit 2
        ;;
    esac
done

if [[ "$CONFIRMED" -ne 1 ]]; then
    echo "❌ 必须显式传入 --confirm" >&2
    echo "用法: ALLOW_PROD_DESTRUCTIVE=1 bash $SCRIPT_PATH --confirm" >&2
    exit 2
fi
if [[ ! -f .env ]]; then
    echo "❌ .env 不存在" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a
if [[ ! -x "$PYTHON" || ! -x "$ALEMBIC" ]]; then
    echo "❌ .venv 不完整" >&2
    exit 1
fi
if [[ ! -f "$MIGRATION_FILE" ]]; then
    echo "❌ 当前代码不包含 $MIGRATION_FILE" >&2
    exit 1
fi
if [[ -z "${TTS_ERP_DB_URL:-}" ]]; then
    echo "❌ TTS_ERP_DB_URL 未设置" >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "❌ 工作区不干净；请在已部署且 clean 的 master 上执行" >&2
    exit 1
fi

read -r DB_NAME DB_HOST < <(
    "$PYTHON" - <<'PY'
import os
from sqlalchemy.engine import make_url
url = make_url(os.environ["TTS_ERP_DB_URL"])
print(url.database or "<unknown>", url.host or "<local>")
PY
)

printf '%s\n' \
    "╔════════════════════════════════════════════════════════════════╗" \
    "║ Migration 0045: 妙手包裹数据归位 miaoshou schema              ║" \
    "╚════════════════════════════════════════════════════════════════╝" \
    "数据库:   ${DB_NAME} @ ${DB_HOST}" \
    "目标版本: ${TARGET}" \
    "备份:     required" \
    "worker:   coordinated stop/start"

# Shared production guard. The human must provide ALLOW_PROD_DESTRUCTIVE=1.
"$PYTHON" - <<'PY'
from tts_erp_v2.api.deps import require_destructive_script_guard
require_destructive_script_guard(
    script_name="oneoff_migrate_0045_miaoshou_package_schema",
    confirmation=True,
    dangerous=True,
    allow_env="ALLOW_PROD_DESTRUCTIVE",
)
print("✅ destructive guard passed")
PY

HEADS=$(timeout 30 "$ALEMBIC" heads)
if [[ "$HEADS" != *"$TARGET"* ]]; then
    echo "❌ Alembic head 不是 $TARGET；停止，避免带入后续未知迁移" >&2
    printf '%s\n' "$HEADS" >&2
    exit 1
fi
CURRENT=$(timeout 30 "$ALEMBIC" current | tail -1 | awk '{print $1}')
if [[ "$CURRENT" != "0044_drop_linkage_schema" && "$CURRENT" != "$TARGET" ]]; then
    echo "❌ 当前 revision=$CURRENT；只允许从 0044 或已完成的 0045 执行" >&2
    exit 1
fi

echo "当前 revision: $CURRENT"

WORKER_WAS_ACTIVE=0
if timeout 10 systemctl --user is-active --quiet tts-erp-sync.service; then
    WORKER_WAS_ACTIVE=1
fi
restore_worker() {
    if [[ "$WORKER_WAS_ACTIVE" -eq 1 ]] && ! systemctl --user is-active --quiet tts-erp-sync.service; then
        echo "⚠️  恢复 sync worker..." >&2
        timeout 45 systemctl --user start tts-erp-sync.service || true
    fi
}
trap restore_worker EXIT

if [[ "$WORKER_WAS_ACTIVE" -eq 1 ]]; then
    echo "── 停止 sync worker，冻结 package 写入 ──"
    timeout 300 systemctl --user stop tts-erp-sync.service
fi

BACKUP_DIR=${TTS_ERP_MANUAL_BACKUP_DIR:-$HOME/backups/tts_erp_manual}
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
export BACKUP_FILE="$BACKUP_DIR/miaoshou_package_0045_${STAMP}.jsonl.gz"
export BACKUP_META="$BACKUP_DIR/miaoshou_package_0045_${STAMP}.meta.json"

echo "── 备份受影响行 ──"
"$PYTHON" - <<'PY'
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text


def encode(value):
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return {"__base64__": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


queries = {
    "integration.raw_records": """
        SELECT * FROM integration.raw_records
        WHERE endpoint IN (
          'miaoshou.package.search_package_list',
          'miaoshou.package.get_package_info'
        ) ORDER BY id
    """,
    "fulfillment.shipments": """
        SELECT s.* FROM fulfillment.shipments s
        JOIN integration.raw_records r ON r.id=s.raw_record_id
        WHERE r.endpoint IN (
          'miaoshou.package.search_package_list',
          'miaoshou.package.get_package_info'
        ) ORDER BY s.id
    """,
    "fulfillment.shipment_lines": """
        SELECT sl.* FROM fulfillment.shipment_lines sl
        JOIN fulfillment.shipments s ON s.id=sl.shipment_id
        JOIN integration.raw_records r ON r.id=s.raw_record_id
        WHERE r.endpoint IN (
          'miaoshou.package.search_package_list',
          'miaoshou.package.get_package_info'
        ) ORDER BY sl.shipment_id, sl.sales_order_line_id
    """,
    "integration.sync_cursors": """
        SELECT * FROM integration.sync_cursors
        WHERE job_name='miaoshou.packages' ORDER BY id
    """,
    "integration.sync_issues": """
        SELECT * FROM integration.sync_issues
        WHERE job_name IN ('miaoshou.packages','miaoshou.package_detail')
        ORDER BY id
    """,
}
engine = create_engine(os.environ["TTS_ERP_DB_URL"])
counts: dict[str, int] = {}
ids: dict[str, list[int]] = {}
backup = Path(os.environ["BACKUP_FILE"])
with engine.connect() as conn, gzip.open(backup, "wt", encoding="utf-8") as out:
    database = conn.execute(text("SELECT current_database()" )).scalar_one()
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    for table_name, sql in queries.items():
        rows = conn.execute(text(sql)).mappings()
        count = 0
        table_ids: list[int] = []
        for row in rows:
            data = dict(row)
            out.write(json.dumps({"table": table_name, "row": data}, ensure_ascii=False, default=encode) + "\n")
            if isinstance(data.get("id"), int):
                table_ids.append(data["id"])
            count += 1
        counts[table_name] = count
        ids[table_name] = table_ids
meta = {
    "database": database,
    "revision": revision,
    "counts": counts,
    "ids": ids,
    "backup": str(backup),
}
Path(os.environ["BACKUP_META"]).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
os.chmod(backup, 0o600)
os.chmod(os.environ["BACKUP_META"], 0o600)
sha = hashlib.sha256(backup.read_bytes()).hexdigest()
print(json.dumps(meta, ensure_ascii=False))
print(f"sha256={sha}")
if counts["integration.raw_records"] == 0 and revision != "0045_miaoshou_package_schema":
    raise SystemExit("❌ 0044 状态却没有 package raw rows，停止确认异常")
PY

echo "备份文件: $BACKUP_FILE"
echo "备份元数据: $BACKUP_META"

echo "── 执行 migration 0045 ──"
timeout 600 "$ALEMBIC" upgrade "$TARGET"

echo "── 验证迁移和定向清理 ──"
"$PYTHON" - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

meta = json.loads(Path(os.environ["BACKUP_META"]).read_text())
expected_raw = meta["counts"]["integration.raw_records"]
engine = create_engine(os.environ["TTS_ERP_DB_URL"])
with engine.connect() as conn:
    revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if revision != "0045_miaoshou_package_schema":
        raise SystemExit(f"❌ revision 异常: {revision}")
    tables = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='miaoshou'"
            )
        )
    }
    expected_tables = {
        "package_raw_records", "packages", "package_items",
        "package_gift_items", "sync_cursors", "sync_issues",
    }
    missing = expected_tables - tables
    if missing:
        raise SystemExit(f"❌ miaoshou 缺表: {sorted(missing)}")
    shipment_ids = meta["ids"].get("fulfillment.shipments", [])
    old_shipment_count = (
        conn.execute(
            text("SELECT count(*) FROM fulfillment.shipments WHERE id=ANY(:ids)"),
            {"ids": shipment_ids},
        ).scalar_one()
        if shipment_ids
        else 0
    )
    counts = {
        "new_raw": conn.execute(text("SELECT count(*) FROM miaoshou.package_raw_records")).scalar_one(),
        "packages": conn.execute(text("SELECT count(*) FROM miaoshou.packages")).scalar_one(),
        "items": conn.execute(text("SELECT count(*) FROM miaoshou.package_items")).scalar_one(),
        "gifts": conn.execute(text("SELECT count(*) FROM miaoshou.package_gift_items")).scalar_one(),
        "old_raw": conn.execute(text("SELECT count(*) FROM integration.raw_records WHERE endpoint LIKE 'miaoshou.package.%'")).scalar_one(),
        "old_shipments": old_shipment_count,
        "old_cursors": conn.execute(text("SELECT count(*) FROM integration.sync_cursors WHERE job_name='miaoshou.packages'")).scalar_one(),
        "old_issues": conn.execute(text("SELECT count(*) FROM integration.sync_issues WHERE job_name IN ('miaoshou.packages','miaoshou.package_detail')")).scalar_one(),
    }
print(json.dumps(counts, ensure_ascii=False))
if counts["new_raw"] < expected_raw:
    raise SystemExit(f"❌ raw 搬迁不足: new={counts['new_raw']} expected>={expected_raw}")
for key in ("old_raw", "old_shipments", "old_cursors", "old_issues"):
    if counts[key] != 0:
        raise SystemExit(f"❌ 定向清理未完成: {key}={counts[key]}")
print("✅ schema / raw history / normalized rows / cleanup verified")
PY

echo "── 立即补跑 miaoshou.packages ──"
timeout 900 "$PYTHON" -m tts_erp_v2.sync_worker.main run miaoshou.packages

if [[ "$WORKER_WAS_ACTIVE" -eq 1 ]]; then
    echo "── 启动 sync worker ──"
    timeout 60 systemctl --user start tts-erp-sync.service
    timeout 20 systemctl --user is-active --quiet tts-erp-sync.service
fi

trap - EXIT
restore_worker

echo
echo "✅ Migration 0045 完成"
echo "✅ 妙手 package 数据已归位 miaoshou.*，旧 fulfillment/integration 投影已清理"
echo "备份: $BACKUP_FILE"

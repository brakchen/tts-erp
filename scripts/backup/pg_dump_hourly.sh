#!/usr/bin/env bash
# pg_dump_hourly.sh — 每小时 pg_dump + 自动轮转（保留最近 N 份）
# 用法: bash scripts/backup/pg_dump_hourly.sh
# 依赖: .env（TTS_ERP_DB_URL 或 PG* 变量）、docker（容器内执行）

set -euo pipefail

# ─── 配置 ─────────────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/home/schan/backups/tts_erp_pgdump}"
KEEP_COUNT="${KEEP_COUNT:-3}"    # 保留最近 N 份
DB_NAME="${PGDATABASE:-tts_erp}" # 要 dump 的库
CONTAINER="postgres"             # Docker 容器名
LOG_FILE="${BACKUP_DIR}/pgbackup.log"
# ──────────────────────────────────────────────────────────────────────────────

mkdir -p "$BACKUP_DIR"

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }

log() { echo "[$(ts)] $*" >>"$LOG_FILE"; }

die() {
  log "ERROR: $*"
  exit 1
}

# ─── 执行 dump ────────────────────────────────────────────────────────────────
DUMP_FILE="${BACKUP_DIR}/${DB_NAME}_$(date -u '+%Y%m%d_%H%M%S').sql.gz"

log "START dump ${DB_NAME} → ${DUMP_FILE}"

# 在容器内 pg_dump，通过管道 gzip 压缩
docker exec "$CONTAINER" pg_dump -U postgres --no-owner --no-privileges "$DB_NAME" |
  gzip >"$DUMP_FILE" ||
  {
    rm -f "$DUMP_FILE"
    die "pg_dump failed"
  }

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
log "DONE  ${DUMP_FILE} (${SIZE})"

# ─── 轮转：删除超出保留数的旧文件 ─────────────────────────────────────────────
# 按文件名排序（时间戳在文件名里），保留最新 KEEP_COUNT 个
DUMP_FILES=($(ls -1t "${BACKUP_DIR}/${DB_NAME}_"*.sql.gz 2>/dev/null))

if ((${#DUMP_FILES[@]} > KEEP_COUNT)); then
  for old in "${DUMP_FILES[@]:${KEEP_COUNT}}"; do
    log "ROTATE delete ${old}"
    rm -f "$old"
  done
  log "ROTATE kept ${KEEP_COUNT}, deleted $((${#DUMP_FILES[@]} - KEEP_COUNT))"
fi

log "END   dump ${DB_NAME}"

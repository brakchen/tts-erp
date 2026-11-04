#!/usr/bin/env bash
# scripts/test_deploy.sh — 一键部署 HP desk 测试环境。
#
# 部署内容：
#   1. 在临时 worktree 中按指定 commit 构建前端（typecheck + vite build + 打包）；
#   2. release worktree（$TTS_ERP_TEST_RELEASE_DIR，API 服务实际运行目录）
#      checkout 到该 commit，并同步构建产物 tts_erp_v2/static/frontend/；
#   3. 重启用户级 tts-erp.service（API）；
#      若主 worktree HEAD 与部署 commit 一致，同时重启 tts-erp-sync / tts-erp-publish
#      （这两个 worker 运行的是主 worktree 代码）；
#   4. 本机只读校验：healthz + 登录页 + releaseId。
#
# 用法：
#   bash scripts/test_deploy.sh [选项] [ref]
#     ref           要部署的 git 引用（分支/tag/commit），默认 HEAD
#     --dry-run     只打印计划，不做任何改动
#     --verify-only 只做部署后的只读校验
#     --sync-deps   pyproject 变化时用主 venv 执行 pip install -e .（默认只警告）
#     -h, --help    显示帮助
#
# 边界：不执行数据库迁移（一律人工）；不改 .env / .venv / logs；
# release worktree 有未提交改动时拒绝 checkout。
set -euo pipefail

DL_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/deploy_lib.sh
. "$DL_REPO_ROOT/scripts/deploy_lib.sh"

RELEASE_DIR="${TTS_ERP_TEST_RELEASE_DIR:-/home/schan/tts-erp-releases/erp-master-01a16e3a}"
MAIN_DIR="${TTS_ERP_TEST_MAIN_DIR:-/home/schan/tts-erp}"

REF="HEAD"
DRY_RUN=0
VERIFY_ONLY=0
SYNC_DEPS=0

usage() { sed -n '2,28p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --sync-deps) SYNC_DEPS=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) dl_die "未知选项：$1（-h 查看帮助）" ;;
    *) [ "$REF" = "HEAD" ] || dl_die "只能指定一个 ref"; REF="$1" ;;
  esac
  shift
done

PORT="$(grep -E '^TTS_ERP_PORT=' "$MAIN_DIR/.env" 2>/dev/null | cut -d= -f2 || true)"
PORT="${PORT:-9877}"
ORIGIN="http://127.0.0.1:$PORT"

if [ "$VERIFY_ONLY" = "1" ]; then
  dl_log "release worktree 当前 commit：$(git -C "$RELEASE_DIR" rev-parse --short HEAD 2>/dev/null || echo 未知)"
  dl_verify "HP desk 测试环境" "$ORIGIN"
  exit $?
fi

COMMIT="$(dl_resolve_commit "$REF")"
SUBJECT="$(git -C "$DL_REPO_ROOT" log -1 --format='%s' "$COMMIT")"
dl_log "目标 commit：${COMMIT:0:12} $SUBJECT"

[ -e "$RELEASE_DIR/.git" ] || dl_die "release worktree 不存在或不是 git worktree：$RELEASE_DIR"
OLD_COMMIT="$(git -C "$RELEASE_DIR" rev-parse HEAD 2>/dev/null || true)"

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
[dry-run] 将执行以下步骤：
  1. 临时 worktree 构建 $COMMIT 的前端产物
  2. release worktree $RELEASE_DIR checkout 到该 commit（当前 ${OLD_COMMIT:0:12}）
  3. 同步 tts_erp_v2/static/frontend/ 构建产物
  4. 重启 systemctl --user tts-erp.service
  5. 主 worktree HEAD 与之一致时重启 tts-erp-sync / tts-erp-publish
  6. 校验 $ORIGIN 的 healthz / 登录页 / releaseId
  不执行：数据库迁移、.env/.venv/logs 修改
EOF
  exit 0
fi

# release worktree 必须是干净的，避免覆盖别人未提交的改动
if [ -n "$(git -C "$RELEASE_DIR" status --porcelain 2>/dev/null)" ]; then
  dl_die "release worktree 有未提交改动，拒绝覆盖：$RELEASE_DIR（请先人工处理）"
fi

trap dl_cleanup EXIT
WT="$(dl_prepare_build_worktree "$COMMIT")"
RELEASE_ID="$(dl_build_frontend "$WT")"

# 代码与依赖变化提示
if [ -n "$OLD_COMMIT" ]; then
  if git -C "$DL_REPO_ROOT" diff --name-only "$OLD_COMMIT" "$COMMIT" -- \
      pyproject.toml uv.lock requirements.txt 2>/dev/null | grep -q .; then
    if [ "$SYNC_DEPS" = "1" ]; then
      dl_log "同步 Python 依赖（主 venv pip install -e release worktree）…"
      "$MAIN_DIR/.venv/bin/pip" install -e "$RELEASE_DIR" >&2 || dl_die "依赖安装失败"
    else
      dl_warn "pyproject/依赖清单有变化；如需安装新依赖请加 --sync-deps 重跑"
    fi
  fi
  if git -C "$DL_REPO_ROOT" diff --name-only "$OLD_COMMIT" "$COMMIT" -- \
      alembic/versions 2>/dev/null | grep -q .; then
    dl_warn "本次部署包含新 alembic 迁移；数据库迁移由人工执行，本脚本不会代跑"
  fi
fi

dl_log "release worktree checkout 到 ${COMMIT:0:12} …"
git -C "$RELEASE_DIR" checkout --detach "$COMMIT" >&2 || dl_die "checkout 失败"

dl_log "同步前端构建产物到 release worktree …"
rm -rf "$RELEASE_DIR/tts_erp_v2/static/frontend"
cp -a "$WT/tts_erp_v2/static/frontend" "$RELEASE_DIR/tts_erp_v2/static/frontend"

dl_log "重启 tts-erp.service（API）…"
systemctl --user restart tts-erp.service
sleep 2
systemctl --user is-active tts-erp.service | grep -q '^active$' \
  || dl_die "tts-erp.service 重启后未 active，请查 journalctl --user -u tts-erp -n 50"
dl_wait_healthz "$ORIGIN" 90 \
  || dl_die "tts-erp.service 重启后 90 秒内 healthz 未就绪，请查 journalctl --user -u tts-erp -n 50"

# sync / publish worker 运行的是主 worktree 代码
MAIN_HEAD="$(git -C "$MAIN_DIR" rev-parse HEAD 2>/dev/null || true)"
if [ "$MAIN_HEAD" = "$COMMIT" ]; then
  dl_log "主 worktree HEAD 与部署 commit 一致，同步前端产物并重启 sync/publish worker …"
  rm -rf "$MAIN_DIR/tts_erp_v2/static/frontend"
  cp -a "$WT/tts_erp_v2/static/frontend" "$MAIN_DIR/tts_erp_v2/static/frontend"
  systemctl --user restart tts-erp-sync.service tts-erp-publish.service
else
  dl_warn "tts-erp-sync / tts-erp-publish 运行主 worktree 代码（HEAD ${MAIN_HEAD:0:12}），未随本次部署更新；"
  dl_warn "如需让 worker 也运行 ${COMMIT:0:12}，请人工在主 worktree checkout 后重启这两个服务。"
fi

dl_verify "HP desk 测试环境" "$ORIGIN" "$RELEASE_ID" \
  || dl_die "部署后校验未通过，请人工检查（journalctl --user -u tts-erp -n 50）"

dl_log "完成：HP desk 测试环境已部署 ${COMMIT:0:12}，前端 $RELEASE_ID"

#!/usr/bin/env bash
# scripts/oneoff_deploy_media_assets_0073.sh — 媒体资产库与数字人上线执行脚本
#
# 【人工执行】AGENTS.md §3：生产迁移与服务重启是 human-operated，agent 只在
# 测试形态库验证。本脚本把该人工流程编排为一步，含显式确认护栏。
#
# 依据：
#   docs/design/media-asset-library.md            本功能的契约与数据模型
#   alembic/versions/0073_media_assets.py         新增 media schema（3 张表）
#   docs/guides/agent-safety.md §2/§3             迁移护栏（ALLOW_PROD_DESTRUCTIVE=1
#                                                仅注入本脚本的 alembic 子进程）
#   scripts/oneoff_deploy_spu_deterioration_alert_0068.sh  同仓部署脚本的确认/输出约定
#
# 前置条件（脚本会逐条检查，缺哪条就停在哪条）：
#   1) 本功能已合并到 origin/master（含 alembic/versions/0073_media_assets.py）；
#   2) 部署目录 /home/schan/tts-erp（services 的 WorkingDirectory）已同步到该 master
#      —— 本脚本会自己完成同步（第 0 步），无需操作者手动 pull；
#   3) .env 存在且含 TTS_ERP_DB_URL；对象存储凭据沿用既有 MINIO_* 变量。
#
# 用法：
#   bash scripts/oneoff_deploy_media_assets_0073.sh              # 交互式（每步确认）
#   bash scripts/oneoff_deploy_media_assets_0073.sh --yes        # 跳过确认
#   bash scripts/oneoff_deploy_media_assets_0073.sh --dry-run    # 只打印将执行的命令
#   bash scripts/oneoff_deploy_media_assets_0073.sh --verify-only  # 只跑验证段（第 8 步）
#   bash scripts/oneoff_deploy_media_assets_0073.sh --skip-frontend-build
#
# 步骤：
#   0 预检     部署目录 / .env / venv / docker / node / 同步代码到 origin/master
#   1 迁移     alembic upgrade head（0072 → 0073，纯增量；逐字确认库名）
#   2 权限     accounts.cli sync-permissions（幂等）→ 校验两个权限点已授权
#   3 对象存储  确保 bucket tts-erp-media 存在（幂等，加法操作）
#   4 上传上限  校验 nginx /minio/ 的 client_max_body_size（不改主机配置，只校验并给修复指引）
#   5 前端     构建并发布新的前端 release（两个新页面需要新产物才可见）
#   6 重启     restart.sh（API）+ tts-erp-sync.service（db/models 变更后需重载）
#   7 验证     /endpoints 路由、/healthz、带 service key 的只读冒烟
#
# 安全约束：
#   * 不执行任何 DELETE/TRUNCATE/DROP；0073 是纯增量（CREATE SCHEMA/TABLE/INDEX
#     + INSERT 权限点），无数据破坏。
#   * 生产迁移需在提示后【逐字输入目标库名】确认；--dry-run 不触碰数据库。
#   * ALLOW_PROD_DESTRUCTIVE=1 只注入本脚本的 alembic 子进程（同
#     scripts/envsetup/install.sh 的迁移步骤）。
#   * 本脚本不打印任何密钥：.env 只按需解析 TTS_ERP_DB_URL / TTS_ERP_PORT /
#     TTS_ERP_SERVICE_KEY 三项，MinIO 凭据由 python 侧 load_dotenv 自行读取。
#   * 验证段只打 GET，不做任何写操作（同 tests/e2e 的约定）。
set -euo pipefail

DEPLOY_DIR="${TTS_ERP_DEPLOY_DIR:-/home/schan/tts-erp}"  # 与 restart.sh 一致；services 的 WorkingDirectory
ASSUME_YES=0
DRY_RUN=0
VERIFY_ONLY=0
SKIP_FRONTEND_BUILD=0

EXPECTED_PRE=0072_shop_app_credentials
EXPECTED_POST=0073_media_assets
MEDIA_BUCKET=tts-erp-media

# ---------- 输出（与 scripts/envsetup/install.sh 同风格） ----------
if [[ -t 1 ]]; then
  C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
err()  { printf '%s✗%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; }
die()  { err "$*"; exit 1; }
step() { printf '\n%s== %s ==%s\n' "$C_DIM" "$*" "$C_OFF"; }

run() {
  printf '  %s$ %s%s\n' "$C_DIM" "$*" "$C_OFF"
  (( DRY_RUN )) && return 0
  "$@"
}
fail_pre() {
  if (( DRY_RUN )); then warn "预检失败（dry-run 仅提示，不中断）：$*"
  else die "$*"; fi
}
confirm() { # 提示文本
  if (( ASSUME_YES )); then return 0; fi
  local ans
  read -r -p "$1 [y/N] " ans
  [[ $ans == y || $ans == Y ]]
}
confirm_db() { # 逐字输入目标库名
  if (( DRY_RUN )); then info "（dry-run 跳过库名确认）"; return 0; fi
  local answer
  printf '请逐字输入目标库名 [%s] 以确认生产迁移（其他输入取消）: ' "$DB_NAME"
  read -r answer
  [[ "$answer" == "$DB_NAME" ]] || die "确认不匹配（输入了「$answer」），已取消"
}

# ---------- 选项 ----------
# 先存下原始参数：下面的 shift 会清空 "$@"，而第 0 步 pull 后要原样 re-exec 自己。
ORIGINAL_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --skip-frontend-build) SKIP_FRONTEND_BUILD=1 ;;
    -h|--help)
      awk '/^set -euo/{exit} NR>1 && /^#/{sub(/^# ?/,""); print}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) die "未知参数: $1（-h 看用法）" ;;
  esac
  shift
done

cd "$DEPLOY_DIR" || die "部署目录不存在: $DEPLOY_DIR"

# .env 只按需解析三项；不 source 整个文件（避免特殊字符被 shell 解释），不回显。
env_get() {
  grep -E "^$1=" "$DEPLOY_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- \
    | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'
}

prod_version() {
  docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT version_num FROM alembic_version" 2>/dev/null | tr -d '[:space:]'
}

# ---------- 0. 预检 ----------
step "0 preflight"
[[ -f "$DEPLOY_DIR/.env" ]] || die "缺 .env（运维提供，本脚本不代管）"
[[ -x "$DEPLOY_DIR/.venv/bin/alembic" ]] || die "缺 .venv/bin/alembic（先 bash scripts/envsetup/install.sh）"
[[ -x "$DEPLOY_DIR/.venv/bin/python" ]] || die "缺 .venv/bin/python"
command -v docker >/dev/null 2>&1 || die "缺 docker（本脚本用 docker exec postgres 校验库）"
docker exec postgres true 2>/dev/null || die "docker 容器 postgres 不可用"

DB_URL="$(env_get TTS_ERP_DB_URL)"
PORT="${TTS_ERP_PORT:-$(env_get TTS_ERP_PORT)}"
PORT="${PORT:-9877}"
[[ -n "$DB_URL" ]] || die ".env 缺 TTS_ERP_DB_URL"
DB_NAME="${DB_URL##*/}"; DB_NAME="${DB_NAME%%\?*}"
[[ "$DB_NAME" == "tts_erp" ]] || die "TTS_ERP_DB_URL 指向库 '$DB_NAME'，本脚本只允许操作 tts_erp"
ok "目标库：$DB_NAME（连接串不回显）"
# 连接串只通过环境变量传给子进程，绝不进入命令行（dry-run 会打印命令）。
export TTS_ERP_DB_URL="$DB_URL"

# 代码同步：本脚本自己把部署目录同步到 origin/master。
#
# 为什么必须自同步：脚本自己就住在被 pull 的树里（scripts/oneoff_deploy_media_assets_0073.sh
# 与 alembic/versions/0073_media_assets.py 都只在 master 上）。若要求操作者先手动
# pull，「脚本不存在 → 跑不了」会变成死循环（No such file or directory）。
#
# 为什么 pull 后要重新 exec 自己：bash 按字节增量读取脚本文件，运行中 pull 改写脚本
# 本体会让 bash 从错误的偏移继续读。pull 完成后用新文件重新 exec 一次，避免读到半截内容。
#
# 安全性：git pull --ff-only 只快进，不会产生合并提交，也不会覆盖工作区改动；
# 若工作区有会被本次更新覆盖的本地修改，git 自己会拒绝（此时脚本中止并提示）。
if git -C "$DEPLOY_DIR" fetch origin -q 2>/dev/null; then
  if git -C "$DEPLOY_DIR" merge-base --is-ancestor origin/master HEAD; then
    ok "部署目录已同步 origin/master（HEAD=$(git -C "$DEPLOY_DIR" rev-parse --short HEAD)）"
  else
    info "部署目录落后 origin/master，正在同步（fast-forward）…"
    if (( DRY_RUN )); then
      info "（dry-run：跳过 pull）"
    else
      git -C "$DEPLOY_DIR" merge --ff-only origin/master \
        || die "git pull --ff-only 失败（可能有本地改动会被覆盖）。请先处理：git -C $DEPLOY_DIR status"
      ok "已同步到 $(git -C "$DEPLOY_DIR" rev-parse --short HEAD)"
      # 重新 exec：让 bash 从更新后的脚本文件重新开始（避免增量读取半截内容）。
      # ${ORIGINAL_ARGS[@]+...} 避免 set -u 下空数组展开报错。
      exec bash "$DEPLOY_DIR/scripts/oneoff_deploy_media_assets_0073.sh" \
        ${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"}
    fi
  fi
else
  warn "git fetch 失败（离线？）——继续，但请确认代码已含 alembic/versions/0073_media_assets.py"
fi
[[ -f "$DEPLOY_DIR/alembic/versions/0073_media_assets.py" ]] \
  || fail_pre "缺 alembic/versions/0073_media_assets.py。请先把本功能合并到 master 并在部署目录 git pull"
[[ -d "$DEPLOY_DIR/tts_erp_v2/media" ]] \
  || fail_pre "缺 tts_erp_v2/media/（媒体域模块）。请先合并本功能到 master 并 git pull"
if [[ -f "$DEPLOY_DIR/alembic/versions/0073_media_assets.py" && -d "$DEPLOY_DIR/tts_erp_v2/media" ]]; then
  ok "本功能代码已在部署目录"
fi

# 节点工具链（前端构建用）
if (( SKIP_FRONTEND_BUILD )); then
  info "跳过前端构建（--skip-frontend-build）"
else
  command -v node >/dev/null 2>&1 || fail_pre "缺 node（前端构建需要）"
  command -v npm  >/dev/null 2>&1 || fail_pre "缺 npm（前端构建需要）"
  [[ -d "$DEPLOY_DIR/frontend/node_modules" ]] \
    || fail_pre "缺 frontend/node_modules（先 bash scripts/envsetup/install.sh 或 npm ci --prefix frontend）"
  ok "node $(node --version) / npm $(npm --version)"
fi

CUR="$(prod_version)"
[[ -n "$CUR" ]] || die "读不到生产库 alembic 版本"
info "当前生产 alembic 版本：$CUR"

# ---------- 1. 生产迁移 ----------
if (( VERIFY_ONLY )); then
  info "（--verify-only：跳过迁移/权限/构建/重启，只跑验证段）"
elif [[ "$CUR" == "$EXPECTED_POST" ]]; then
  step "1 database migration"
  ok "已是 $EXPECTED_POST，跳过迁移（幂等）"
else
  step "1 database migration (alembic upgrade head)"
  info "0073 为纯增量迁移：新建 media schema（media_assets / digital_humans /"
  info "digital_human_assets）+ 两个页面权限点，无 DROP、无数据破坏。"
  info "alembic/env.py 对生产形态库默认拒绝执行；本步在逐字确认库名后，"
  info "仅为该 alembic 子进程注入 ALLOW_PROD_DESTRUCTIVE=1。"
  [[ "$CUR" == "$EXPECTED_PRE" ]] \
    || die "当前版本 '$CUR' 不是预期的 $EXPECTED_PRE，不自动处理，请人工排查"
  confirm_db
  # DB URL 走环境变量导出，绝不出现在命令行（dry-run 会打印命令）——否则连接串含密码会泄露到日志。
  export TTS_ERP_DB_URL="$DB_URL"
  run env ALLOW_PROD_DESTRUCTIVE=1 "$DEPLOY_DIR/.venv/bin/alembic" upgrade head
  NEW="$(prod_version)"
  if (( DRY_RUN )); then
    info "（dry-run：未真正迁移，跳过版本/表结构断言）"
  else
    [[ "$NEW" == "$EXPECTED_POST" ]] || die "迁移后版本是 '$NEW'，预期 $EXPECTED_POST"
    ok "迁移完成：$EXPECTED_PRE → $EXPECTED_POST"
  fi
fi

# 迁移结果核对（dry-run 下只提示，因为库尚未迁移）
for tbl in media.media_assets media.digital_humans media.digital_human_assets; do
  found="$(docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT to_regclass('$tbl')" 2>/dev/null | tr -d '[:space:]')"
  if [[ -n "$found" && "$found" != "null" ]]; then
    ok "表就位 $tbl"
  elif (( DRY_RUN )); then
    info "（dry-run：$tbl 尚未创建，跳过）"
  else
    die "库里找不到表 $tbl"
  fi
done

# ---------- 2. 权限点 ----------
if (( VERIFY_ONLY )); then
  info "（--verify-only：跳过权限播种）"
else
  step "2 page permissions (sync-permissions, idempotent)"
  info "侧边栏按会话用户的角色权限过滤；新页面权限点不进库，浏览器看不到入口。"
  info "seed_builtin_roles 是幂等 upsert，只补缺失行。"
  run "$DEPLOY_DIR/.venv/bin/python" -m tts_erp_v2.accounts.cli sync-permissions
fi

for code in page:media-assets page:digital-humans; do
  cnt="$(docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT count(*) FROM security.permissions WHERE code='$code'" 2>/dev/null | tr -d '[:space:]')"
  grants="$(docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT string_agg(role_code, ',' ORDER BY role_code) FROM security.role_permissions WHERE permission_code='$code'" \
    2>/dev/null | tr -d '[:space:]')"
  if [[ "${cnt:-0}" -ge 1 ]]; then
    ok "$code → 授权角色：${grants:-（无）}"
  elif (( DRY_RUN )); then
    info "（dry-run：$code 尚未播种，跳过）"
  else
    die "权限点缺失：$code（权限播种未生效）"
  fi
done

# ---------- 3. 对象存储 bucket ----------
step "3 object storage bucket ($MEDIA_BUCKET)"
info "媒体资产库使用独立 bucket，与 SPU 图片桶、发布视频桶的生命周期隔离。"
info "ensure_bucket 是幂等的（存在即跳过）；这是加法操作，不触碰任何已有对象。"
if (( DRY_RUN )); then
  printf '  %s$ python -c "media_store_from_env().ensure_bucket()"%s\n' "$C_DIM" "$C_OFF"
else
  bucket_out="$(cd "$DEPLOY_DIR" && "$DEPLOY_DIR/.venv/bin/python" - <<'PY'
from dotenv import load_dotenv

load_dotenv(".env", override=False)
from tts_erp_v2.media.object_store import media_store_from_env

store = media_store_from_env()
store.ensure_bucket()
print(store.bucket())
PY
)"
  ok "bucket 就绪：$bucket_out"
fi

# ---------- 4. nginx 上传上限（只校验） ----------
step "4 nginx upload limit (verify only)"
limit_m="$(docker exec nginx-gw nginx -T 2>/dev/null \
  | grep -A 60 'location /minio/' | grep -m1 -oE 'client_max_body_size[[:space:]]+[0-9]+[mM]' \
  | grep -oE '[0-9]+' || true)"
if [[ -z "$limit_m" ]]; then
  warn "读不到 nginx /minio/ 的 client_max_body_size（容器名可能不同）——请人工确认"
elif (( limit_m >= 200 )); then
  ok "nginx /minio/ client_max_body_size = ${limit_m}m（≥ 视频上限 200 MiB）"
else
  warn "nginx /minio/ client_max_body_size = ${limit_m}m，小于视频上限 200 MiB。修复（主机改动）："
  info "  1) 编辑 ~/setup/nginx/conf.d/services.conf 的 location /minio/ 段："
  info "     client_max_body_size     10m;  →  client_max_body_size     500m;"
  info "  2) docker exec nginx-gw nginx -t && docker exec nginx-gw nginx -s reload"
fi

# ---------- 5. 前端构建 ----------
if (( VERIFY_ONLY )); then
  info "（--verify-only：跳过前端构建）"
elif (( SKIP_FRONTEND_BUILD )); then
  info "跳过前端构建（--skip-frontend-build）"
else
  step "5 frontend build and publish"
  info "两个新页面需要新的前端产物才可见；构建脚本会写 tts_erp_v2/static/frontend/<release> 并更新 release.json。"
  run bash "$DEPLOY_DIR/scripts/build_frontend.sh"
fi

# ---------- 6. 重启服务 ----------
if (( VERIFY_ONLY )); then
  info "（--verify-only：跳过重启）"
else
  step "6 restart services"
  info "db/models 变更后 sync-worker 也需要重载（新模型在 import 期注册）。"
  run bash "$DEPLOY_DIR/restart.sh"
  run systemctl --user restart tts-erp-sync.service
  if (( DRY_RUN )); then
    info "（dry-run：跳过服务状态检查）"
  elif systemctl --user is-active tts-erp.service tts-erp-sync.service; then
    ok "tts-erp.service 与 tts-erp-sync.service 均 active"
  else
    fail_pre "服务未全部 active——用 systemctl --user status 查看"
  fi
fi

# ---------- 7. 验证 ----------
step "7 verify"
sleep 2
ENDPOINTS="$(curl -sf --max-time 10 "http://127.0.0.1:$PORT/endpoints" || true)"
[[ -n "$ENDPOINTS" ]] || die "服务不可达：http://127.0.0.1:$PORT/endpoints"
for route in \
  "/v2/media/assets" \
  "/v2/media/digital-humans" \
  "/v2/pages/media-assets" \
  "/v2/pages/digital-humans" ; do
  if echo "$ENDPOINTS" | grep -q "$route"; then
    ok "路由存在 $route"
  elif (( DRY_RUN )); then
    info "（dry-run：服务尚未重启到新代码，$route 暂未出现，跳过）"
  else
    die "路由缺失：$route（服务可能还没跑新代码）"
  fi
done

smoke() { # 名称 url 期望子串
  local body
  if body=$(curl -sf --max-time 10 "$2" 2>/dev/null) && [[ "$body" == *"$3"* ]]; then
    ok "$1"
  else
    fail_pre "$1 未通过：$2（期望包含 $3）"
  fi
}
smoke "healthz" "http://127.0.0.1:$PORT/healthz" "tts-erp"

SERVICE_KEY="$(env_get TTS_ERP_SERVICE_KEY)"
if [[ -z "$SERVICE_KEY" ]]; then
  warn ".env 无 TTS_ERP_SERVICE_KEY —— 跳过鉴权冒烟（只读 GET，未做任何写操作）"
else
  api_get() { # 路径 → body
    curl -sf --max-time 15 -H "Authorization: Bearer $SERVICE_KEY" \
      "http://127.0.0.1:$PORT$1" 2>/dev/null || true
  }
  body="$(api_get /v2/media/assets)"
  if [[ "$body" == *'"items"'* ]]; then
    ok "GET /v2/media/assets → items 契约"
  else
    fail_pre "GET /v2/media/assets 未返回 items（body 前 200 字符：${body:0:200}）"
  fi
  body="$(api_get /v2/media/digital-humans)"
  if [[ "$body" == *'['* ]]; then
    ok "GET /v2/media/digital-humans → 数组契约"
  else
    fail_pre "GET /v2/media/digital-humans 未返回数组（body 前 200 字符：${body:0:200}）"
  fi
  body="$(api_get /v2/pages/media-assets)"
  if [[ "$body" == *'media-assets'* ]]; then
    ok "GET /v2/pages/media-assets → 页面可渲染"
  else
    fail_pre "GET /v2/pages/media-assets 未返回该页面"
  fi
fi

# ---------- 8. 摘要 ----------
step "8 done"
ok "媒体资产库与数字人上线流程执行完毕。"
info ""
info "后续手动验证清单："
info "  1) 浏览器登录 → 侧边栏应出现「资产管理」「数字人」（admin/operator 可见）"
info "  2) 资产管理页：上传一张图片 → 列表出现 → 点开预览 → 改名 → 删除"
info "  3) 数字人页：新建数字人 → 挂载上面那张资产 → 详情可见 → 归档"
info "  4) 大视频：上传一个 >10 MiB 的 mp4，确认 nginx 上限已放开（否则 413）"
info "  5) viewer 角色：侧边栏不应出现这两个页面（未授权）"
info ""
info "若需回滚：停用页面入口（撤销 role_permissions 中两个 page:* 授权）即可；"
info "media schema 的表可保留——资产是用户数据，不做自动清理。"

#!/usr/bin/env bash
# scripts/deploy_lib.sh — test_deploy.sh / prod_deploy.sh 的公共函数库。
# 只用于被 source，不单独执行。调用方须先设置 DL_REPO_ROOT（仓库根目录）。
#
# 部署拓扑（2026-11-03 起，详见 AGENTS.md §2）：
#   - HP desk（本机）= 测试/数据机：API 用户级服务从 release worktree 运行，
#     sync/publish worker 从主 worktree 运行；
#   - 207.57.126.199（ssh alias tts-erp-prod）= 生产应用机，系统级 tts-erp.service。
#
# 安全约定：部署脚本永不执行 alembic upgrade（迁移一律人工）、不覆盖目标机
# .env/.venv/logs、不打印任何密钥。

if [ -n "${DL_LIB_LOADED:-}" ]; then
  return 0
fi
DL_LIB_LOADED=1

dl_log()  { printf '[deploy] %s\n' "$*" >&2; }
dl_warn() { printf '[deploy][警告] %s\n' "$*" >&2; }
dl_die()  { printf '[deploy][错误] %s\n' "$*" >&2; exit 1; }

# 等待 healthz 就绪（服务重启后 uvicorn 启动需要数秒到数十秒）。
# 用法: dl_wait_healthz <origin> [超时秒数，默认 90]
dl_wait_healthz() {
  local origin="$1" timeout="${2:-90}" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if curl -fsS --max-time 5 "$origin/healthz" >/dev/null 2>&1; then
      dl_log "$origin 已就绪（等待 ${waited}s）"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# 解析 ref 为完整 commit sha；失败即退出。
dl_resolve_commit() {
  local ref="$1" sha
  sha="$(git -C "$DL_REPO_ROOT" rev-parse --verify --quiet "${ref}^{commit}" || true)"
  [ -n "$sha" ] || dl_die "无法解析 git 引用：$ref"
  printf '%s' "$sha"
}

# 在临时 worktree 中检出指定 commit（保证只构建/发布已提交内容）。
# stdout 为 worktree 路径；调用方用 trap dl_cleanup EXIT 负责清理。
DL_BUILD_WORKTREE=""
dl_prepare_build_worktree() {
  local commit="$1" dir
  dir="$(mktemp -d /tmp/tts-erp-deploy-build.XXXXXX)"
  if ! git -C "$DL_REPO_ROOT" worktree add --detach "$dir" "$commit" >&2; then
    rm -rf "$dir"
    dl_die "创建临时构建 worktree 失败（commit $commit）"
  fi
  DL_BUILD_WORKTREE="$dir"
  printf '%s' "$dir"
}

dl_cleanup() {
  if [ -n "$DL_BUILD_WORKTREE" ]; then
    git -C "$DL_REPO_ROOT" worktree remove --force "$DL_BUILD_WORKTREE" >/dev/null 2>&1 || true
    DL_BUILD_WORKTREE=""
  fi
}

# 在指定 worktree 内构建前端（复用 scripts/build_frontend.sh：typecheck + vite build + 打包）。
# stdout 最后一行为 releaseId；日志走 stderr。
dl_build_frontend() {
  local dir="$1" release
  if [ ! -d "$dir/frontend/node_modules" ]; then
    dl_log "安装前端依赖（npm ci，首次较慢）…"
    npm --prefix "$dir/frontend" ci >&2 || dl_die "npm ci 失败"
  fi
  dl_log "构建前端（typecheck + vite build + package-build）…"
  bash "$dir/scripts/build_frontend.sh" >&2 || dl_die "前端构建失败"
  release="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["releaseId"])' \
    "$dir/tts_erp_v2/static/frontend/release.json" 2>/dev/null || true)"
  [ -n "$release" ] || dl_die "构建产物缺少 release.json（tts_erp_v2/static/frontend/release.json）"
  dl_log "前端构建完成：$release"
  printf '%s' "$release"
}

# 把目录打成 tar 流到 stdout。排除所有本地状态与依赖目录，
# 确保 .env / .venv / logs / node_modules 永远不会被带上目标机。
dl_tar_stream() {
  local dir="$1"
  tar -C "$dir" \
    --exclude='./.git' \
    --exclude='./.env' --exclude='./.env.*' \
    --exclude='./logs' \
    --exclude='./.venv' \
    --exclude='./frontend/node_modules' \
    --exclude='./frontend/dist' \
    --exclude='./.pytest_cache' --exclude='./.ruff_cache' \
    --exclude='*.pyc' --exclude='*/__pycache__' \
    -cf - .
}

# 只读校验：健康检查 + 登录页可达 + 线上 releaseId（可选与期望值比对）。
# 用法: dl_verify <名称> <origin> [期望releaseId]
#   origin 不带 /tts 后缀，例如 http://127.0.0.1:9877 或 https://chen.daqiangplatform.com
dl_verify() {
  local name="$1" origin="$2" expect="${3:-}" rc=0
  local body rel status

  if body="$(curl -fsS --max-time 15 "$origin/healthz" 2>/dev/null)"; then
    dl_log "$name 健康检查通过：$origin/healthz → $body"
  else
    dl_warn "$name 健康检查失败：$origin/healthz"
    rc=1
  fi

  status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
    "$origin/tts/v2/auth/login?next=/v2/pages/dashboard" 2>/dev/null || true)"
  if [ "$status" = "200" ]; then
    dl_log "$name 登录页可达（HTTP 200）"
  else
    dl_warn "$name 登录页异常（HTTP ${status:-无法连接}）"
    rc=1
  fi

  if rel="$(curl -fsS --max-time 15 "$origin/tts/static/frontend/release.json" 2>/dev/null)"; then
    rel="$(printf '%s' "$rel" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("releaseId",""))' 2>/dev/null || true)"
    if [ -n "$expect" ]; then
      if [ "$rel" = "$expect" ]; then
        dl_log "$name 前端版本一致：$rel"
      else
        dl_warn "$name 前端版本不符：期望 $expect，实际 ${rel:-未知}"
        rc=1
      fi
    else
      dl_log "$name 当前前端版本：${rel:-未知}"
    fi
  else
    dl_warn "$name 无法读取 release.json（$origin/tts/static/frontend/release.json）"
    rc=1
  fi

  return "$rc"
}

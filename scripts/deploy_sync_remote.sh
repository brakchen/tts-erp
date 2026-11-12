#!/usr/bin/env bash
# Production-side Sync Worker release switch. Invoked by prod_sync_deploy.sh.
set -euo pipefail

mode="$1"
sha="${2:-}"
unit=tts-erp-sync.service
releases=/opt/tts-erp-sync/releases
python=/opt/tts-erp/.venv/bin/python
env_file=/opt/tts-erp/.env
drop_in_dir=/etc/systemd/system/$unit.d
drop_in="$drop_in_dir/90-release.conf"

fail() { printf '[sync-deploy] %s\n' "$*" >&2; exit 1; }
prop() { systemctl show "$unit" --property="$1" --value; }

running_release() {
  local expected="$1" pid
  systemctl is-active --quiet "$unit" || return 1
  pid="$(prop MainPID)" || return 1
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  [ "$(readlink -f "/proc/$pid/cwd")" = "$expected" ]
}

if [ "$mode" = verify-only ]; then
  current="$(prop WorkingDirectory)"
  running_release "$current" || fail "Worker 未从 systemd 配置的发布目录运行"
  current_sha="$(cat "$current/.deployed-commit")" || fail "当前版本标记不可读"
  [[ "$current_sha" =~ ^[0-9a-f]{40}$ ]] || fail "当前版本标记不是完整提交 SHA"
  printf 'Sync Worker %s\n目录 %s\n提交 %s\n' \
    "$(systemctl is-enabled "$unit" || true)" "$current" "$current_sha"
  exit 0
fi

[[ "$mode" = deploy || "$mode" = rollback ]] || fail "未知发布操作"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "无效的提交 SHA"
[ "$(prop LoadState)" = loaded ] || fail "请先安装 $unit"
[ -x "$python" ] && [ -r "$env_file" ] || fail "生产 Python 环境或配置文件不可用"
install -d "$releases" "$drop_in_dir"
exec 9>/run/lock/tts-erp-sync-deploy.lock
flock -n 9 || fail "已有 Sync Worker 发布或回退在进行"

previous="$(prop WorkingDirectory)"
previous_state="$(prop ActiveState)"
target="$releases/$sha"
scratch="$(mktemp -d "$releases/.stage.XXXXXXXX")"
pending_drop_in=
cleanup() {
  [ -z "$pending_drop_in" ] || rm -f -- "$pending_drop_in"
  rm -rf -- "$scratch"
}
trap cleanup EXIT

if [ "$mode" = deploy ] && [ ! -e "$target" ]; then
  mkdir "$scratch/release"
  tar -xf - -C "$scratch/release"
  test -f "$scratch/release/tts_erp_v2/sync_worker/main.py"
  printf '%s\n' "$sha" > "$scratch/release/.deployed-commit"
  mv -T -- "$scratch/release" "$target"
elif [ "$mode" = deploy ]; then
  tar -tf - >/dev/null
fi
[ -f "$target/tts_erp_v2/sync_worker/main.py" ] \
  && [ "$(cat "$target/.deployed-commit")" = "$sha" ] \
  || fail "目标发布目录不存在或版本标记不符"

if running_release "$target"; then
  printf 'Sync Worker 已运行 %s\n' "$sha"
  exit 0
fi

# This transient check imports modules and confirms PostgreSQL connectivity
# using a read-only transaction. It does not start the scheduler or a job.
systemd-run --quiet --wait --pipe --collect \
  --property="WorkingDirectory=$target" --property="EnvironmentFile=$env_file" \
  --property=RuntimeMaxSec=45s --property=MemoryMax=200M \
  --setenv="TTS_ERP_DOTENV_FILE=$env_file" --setenv=PYTHONDONTWRITEBYTECODE=1 \
  "$python" - <<'PY'
from importlib import import_module

try:
    from tts_erp_v2.db.base import get_engine
    from tts_erp_v2.sync_worker.main import _require_env
    from tts_erp_v2.sync_worker.scheduler import JOBS

    _require_env()
    for spec in JOBS.values():
        module = import_module(spec.module_path)
        getattr(module, "run" if spec.is_tiktok else spec.entrypoint)
    with get_engine().connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SELECT 1")
except Exception as exc:
    raise SystemExit(f"Sync Worker 预检失败（{type(exc).__name__}）") from None

print(f"Sync Worker 预检通过（{len(JOBS)} 个任务）")
PY

if [ -f "$drop_in" ]; then
  cp -p -- "$drop_in" "$scratch/previous.conf"
fi
pending_drop_in="$(mktemp "$drop_in_dir/.release.XXXXXXXX")"
printf '[Service]\nWorkingDirectory=%s\n' "$target" > "$pending_drop_in"
chmod 0644 "$pending_drop_in"
mv -T -- "$pending_drop_in" "$drop_in"
pending_drop_in=

wait_for_scheduler() {
  local expected="$1" invocation
  for _ in {1..30}; do
    if running_release "$expected"; then
      invocation="$(prop InvocationID)" || return 1
      if [ -n "$invocation" ] && journalctl --quiet --no-pager -o cat \
        "_SYSTEMD_INVOCATION_ID=$invocation" | grep -F 'Scheduler started' >/dev/null; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

if systemctl daemon-reload && systemctl restart "$unit" && wait_for_scheduler "$target"; then
  printf 'Sync Worker 已启动调度器 %s\n此前发布目录 %s\n' "$sha" "$previous"
  exit 0
fi

printf '[sync-deploy] 新版本启动未通过，恢复此前服务配置\n' >&2
if [ -f "$scratch/previous.conf" ]; then
  pending_drop_in="$(mktemp "$drop_in_dir/.release.XXXXXXXX")"
  cp -p -- "$scratch/previous.conf" "$pending_drop_in"
  mv -T -- "$pending_drop_in" "$drop_in"
  pending_drop_in=
else
  rm -- "$drop_in"
fi
if systemctl daemon-reload; then
  if [ "$previous_state" = active ]; then
    if systemctl restart "$unit" && wait_for_scheduler "$previous"; then
      fail "新版本发布失败，已恢复此前的 Worker"
    fi
  elif systemctl stop "$unit"; then
    fail "新版本发布失败，已恢复此前配置并停止 Worker"
  fi
fi
fail "发布和自动恢复均未通过，请检查 systemctl status $unit 和 journalctl -u $unit"

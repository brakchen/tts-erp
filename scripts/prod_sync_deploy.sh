#!/usr/bin/env bash
# Deploy only the production Sync Worker from a committed ref.
set -euo pipefail

DL_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$DL_REPO_ROOT/scripts/deploy_lib.sh"

usage() {
  cat <<'USAGE'
用法：
  bash scripts/prod_sync_deploy.sh [--dry-run | --yes] [ref]
  bash scripts/prod_sync_deploy.sh [--dry-run | --yes] --rollback <已安装的完整 SHA>
  bash scripts/prod_sync_deploy.sh --verify-only

发布默认使用最新 origin/master。联合发布时，将同一完整 SHA 传给 API 与 Worker 脚本。
--yes 跳过生产确认。--dry-run 只解析本地引用和显示计划。
目标主机由 TTS_ERP_PROD_HOST 指定，默认 tts-erp-prod。
USAGE
}

host="${TTS_ERP_PROD_HOST:-tts-erp-prod}"
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=15)
mode=deploy
ref=
dry_run=0
assume_yes=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --yes) assume_yes=1 ;;
    --verify-only|--rollback)
      [ "$mode" = deploy ] || dl_die "只能选择一个操作"
      mode="${1#--}"
      ;;
    -h|--help) usage; exit 0 ;;
    -*) dl_die "未知选项：$1" ;;
    *) [ -z "$ref" ] || dl_die "只能指定一个版本"; ref="$1" ;;
  esac
  shift
done

sha=
case "$mode" in
  verify-only)
    [ -z "$ref" ] && [ "$dry_run" = 0 ] && [ "$assume_yes" = 0 ] \
      || dl_die "--verify-only 不接受版本或发布选项"
    ;;
  rollback)
    sha="$ref"
    ;;
  deploy)
    ref="${ref:-origin/master}"
    if [ "$dry_run" = 0 ] && [ "$ref" = origin/master ]; then
      git -C "$DL_REPO_ROOT" fetch origin master >&2 || dl_die "无法同步 origin/master，请核对已验证的目标 SHA"
    fi
    sha="$(dl_resolve_commit "$ref")"
    ;;
esac

if [ "$mode" != verify-only ]; then
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || dl_die "需要完整的 Git 提交 SHA"
  dl_log "生产 Sync Worker $mode：$host $sha"
  [ "$dry_run" = 0 ] || exit 0
  if [ "$assume_yes" = 0 ]; then
    [ -t 0 ] || dl_die "非交互执行必须显式传 --yes"
    read -r -p "请输入 85.189.59.112 以确认: " answer
    [ "$answer" = 85.189.59.112 ] || dl_die "确认字符串不匹配，已中止"
  fi
fi

remote_script="$(cat "$DL_REPO_ROOT/scripts/deploy_sync_remote.sh")"
# OpenSSH joins its remote arguments into a shell command. Quote the complete
# script and its arguments so the archive can use stdin directly.
printf -v remote_command 'bash -c %q -- %q %q' "$remote_script" "$mode" "$sha"
if [ "$mode" = deploy ]; then
  git -C "$DL_REPO_ROOT" archive --format=tar "$sha" tts_erp_v2 miaoshou pyproject.toml \
    | ssh "${ssh_opts[@]}" "$host" "$remote_command"
else
  ssh "${ssh_opts[@]}" "$host" "$remote_command" </dev/null
fi

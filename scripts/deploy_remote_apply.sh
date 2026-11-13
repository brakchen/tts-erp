#!/usr/bin/env bash
# scripts/deploy_remote_apply.sh — prod_deploy.sh 在目标生产机上执行的应用步骤。
# 用法：ssh <host> bash -s -- <commit> <target_dir> < scripts/deploy_remote_apply.sh
# 前置：目标目录下已存在 .deploy-staging/（完整新代码树的 tar 解包结果）。
# 行为：
#   1. 原子替换 tts_erp_v2/（旧目录保留为 tts_erp_v2.prev 作回滚副本，失败自动恢复）；
#   2. 其余顶层文件覆盖式同步（.env / .venv / logs 不在部署包内，不受影响）；
#   3. 写 .deployed-commit 作为下次部署的差异基线。
set -euo pipefail

commit="${1:?缺少 commit 参数}"
dir="${2:?缺少目标目录参数}"
cd "$dir"

[ -d .deploy-staging ] || { echo "[apply][错误] 缺少 .deploy-staging（传输未完成？）" >&2; exit 1; }
[ -d .deploy-staging/tts_erp_v2 ] || { echo "[apply][错误] .deploy-staging 中没有 tts_erp_v2" >&2; exit 1; }

rm -rf tts_erp_v2.prev
if ! { mv tts_erp_v2 tts_erp_v2.prev && mv .deploy-staging/tts_erp_v2 tts_erp_v2; }; then
  # 失败恢复：新目录没就位就把旧目录放回去
  [ -d tts_erp_v2 ] || mv tts_erp_v2.prev tts_erp_v2
  echo "[apply][错误] tts_erp_v2 替换失败，已回滚" >&2
  exit 1
fi

# staging 中已不含 tts_erp_v2（被 mv 走了），直接整体覆盖
cp -a .deploy-staging/. .
rm -rf .deploy-staging
printf '%s\n' "$commit" > .deployed-commit
echo "[apply] 已应用 $commit 到 $dir"

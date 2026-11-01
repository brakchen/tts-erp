#!/usr/bin/env bash
# Finish this specific submodule merge using the reviewed source resolution.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
root=$(pwd)
plugin="$root/plugins/monitor"
ours=841d53c81100f65bbb328ccb412cb95723fd40f2
theirs=eb0c773a6f4222d894c93ed55b166f0619e588b2
parent_merge=8b082808a025c4250852c683ae2ebc54c11df47b
patch="$root/scripts/monitor-merge-resolution.patch"

# This resolution belongs to the currently reported merge, not a later branch.
if [[ $(git rev-parse MERGE_HEAD) != "$parent_merge" ]]; then
    echo "The main repository is not in the expected merge. No changes made." >&2
    exit 1
fi
if [[ $(git -C "$plugin" rev-parse HEAD) != "$ours" ]]; then
    echo "The monitor HEAD differs from the reviewed commit. No changes made." >&2
    exit 1
fi
git -C "$plugin" fetch origin
pending=$(git -C "$plugin" rev-parse -q --verify MERGE_HEAD || true)
if [[ -n "$pending" && "$pending" != "$theirs" ]]; then
    echo "Monitor is merging another commit; preserve it for review." >&2
    exit 1
fi
if [[ -z "$pending" ]]; then
    if [[ -n $(git -C "$plugin" status --porcelain) ]]; then
        git -C "$plugin" stash push --include-untracked -m "preserve monitor work before reviewed merge"
        echo "Existing monitor work was saved in a stash and will be retained."
    fi
    if ! git -C "$plugin" merge --no-commit --no-ff "$theirs"; then
        git -C "$plugin" rev-parse --verify MERGE_HEAD >/dev/null
    fi
fi

# Restore the known patch base only for the overlapping files, then apply
# the combination of remote Cookie/health fixes and local shop discovery.
files=(entrypoints/background.ts src/extension/messages.ts entrypoints/popup/health-settings.tsx)
git -C "$plugin" restore --source="$theirs" --staged --worktree -- "${files[@]}"
git -C "$plugin" apply --check "$patch"
git -C "$plugin" apply "$patch"
git -C "$plugin" add -- "${files[@]}"
if [[ -n $(git -C "$plugin" diff --name-only --diff-filter=U) ]]; then
    echo "Other monitor conflicts remain; no commit or push performed." >&2
    exit 1
fi
git -C "$plugin" commit --no-edit

# Publish submodule commits before the parent repository references them.
ads_commit=$(git rev-parse :plugins/ads-data-sync)
git -C plugins/ads-data-sync push origin "$ads_commit:refs/heads/feat/shop-onboarding"
git -C "$plugin" push origin HEAD:refs/heads/integrate/shop-onboarding
git add -- plugins/monitor scripts/finalize-monitor-merge.sh scripts/monitor-merge-resolution.patch
git commit --no-edit
git push origin master
echo "Monitor and main repository merges committed and pushed. Any saved stash was retained."

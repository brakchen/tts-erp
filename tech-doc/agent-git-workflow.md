# Agent Git and worktree workflow

This is the canonical lifecycle for agent-owned repository changes.

## 1. Before starting

In the master worktree:

```bash
git status --short --branch
git worktree list
git fetch origin
```

Then:

1. Read `handoff/ACTIVE.md` and confirm no active lane owns the files you need.
2. Compare master with `origin/master`; do not assume existing local commits or files are yours.
3. If unowned WIP exists, preserve it before touching anything:

   ```bash
   git diff > /tmp/<topic>-wip.patch
   ```

   Archive untracked files separately. Do not stash, restore, or delete another owner's work.
4. Add one lane row to `handoff/ACTIVE.md` and stage it immediately.
5. Commit the registration so the new worktree inherits it.

`handoff/ACTIVE.md` has a single-writer rule. Before editing it, confirm `git diff handoff/ACTIVE.md` is empty. When several sessions need it, serialize edits; use `flock /tmp/active-md.lock` if necessary.

## 2. Create the worktree

Use a kebab-case topic and one of `fix/`, `feature/`, `redesign/`, or `chore/`:

```bash
git worktree add .worktrees/<slug> -b <prefix>/<slug>
cd .worktrees/<slug>
ln -s ../../.venv .venv
ln -s ../../.env.test .env.test
```

Do not share a writable production-shaped `.env` between worktrees. If non-test commands require additional local settings:

```bash
cp ../../.env .env
chmod u-w .env
```

- Never edit the main repository's `.env` through a symlink.
- Never commit `.env`, `.env.test`, or secrets.
- Use the worktree path explicitly in tool calls to avoid editing the master worktree by mistake.

Run the narrowest safe smoke check for the task. Tests must follow `tech-doc/agent-testing.md`.

## 3. Ownership and parallel work

- One writer owns each worktree.
- Declare file ownership in the lane row before implementation.
- Avoid assigning multiple lanes to shared hotspots such as:
  - `tts_erp_v2/sync_worker/scheduler.py`
  - `tests/conftest.py`
  - `tts_erp_v2/db/models/`
  - schema SQL and schema generators
  - `restart.sh`
  - `handoff/ACTIVE.md`
- If overlap is unavoidable, assign one lane as the sole writer for the shared file and have other lanes produce recommendations or patches.
- Tests using the shared database are serialized according to `tech-doc/agent-testing.md`.

## 4. Commit and push policy

Commit atomic, reviewable units. Commit messages use a supported type plus a concise Chinese description:

```text
feat: ...
fix: ...
chore: ...
docs: ...
style: ...
merge: ...
```

Before pausing for user input, interruption, review, or merge:

1. Commit all task-owned changes.
2. Push the lane branch to `origin`.
3. Report the branch and commit.

If the network is unavailable, commit locally, report the exact unpushed commit, and push when connectivity returns. Never leave uncommitted WIP as the only copy.

Do not force-push shared branches. A private lane may be rebased on master before review, but its remote update must use the repository's accepted non-destructive policy; ask if that policy is unclear.

## 5. Keeping a lane current

Before final validation:

```bash
git fetch origin
git diff --stat <branch-base>..master
```

If master contains overlapping work:

1. Check whether the lane's intended change was already implemented.
2. Drop duplicate changes and keep only real incremental value.
3. Rebase the private lane on current master when needed.
4. Resolve conflicts by preserving both sides' intent.

Never use blanket conflict choices such as:

```bash
git checkout --ours -- .
git checkout --theirs -- .
git merge -X theirs ...
```

If a conflict cannot be resolved confidently, abort the merge/rebase and ask rather than producing a behaviorally uncertain result.

## 6. Validation before merge

1. Run diagnostics or link/path checks appropriate to the changed files.
2. Run the narrow relevant tests.
3. For code/test changes, run the fast suite under the shared lock.
4. Confirm the lane introduces zero new stable failures. See `tech-doc/agent-testing.md` for baseline and flake handling.
5. Review `git diff --check` and the final diff.
6. Update affected documentation.
7. Mark the lane `done` in `handoff/ACTIVE.md`, commit, and push the branch.

Documentation/config-only lanes do not need application tests when they change no code or tests, but must validate their own links, paths, syntax, and commands.

## 7. Merge and master validation

In the master worktree:

```bash
git merge <branch> --no-ff -m "merge: <slug> (lane <lane-id>)"
```

After merge:

- Documentation/config-only lane: confirm the merge contains no changes under `tts_erp_v2/**` or `tests/**`, then rerun documentation validation.
- Code/test lane: rerun the fast suite using the same baseline command used before merge; zero new stable failures are allowed.
- If two lanes changed the same module, run the full fast suite even when Git reported no textual conflict.
- Remove the merged lane row from `handoff/ACTIVE.md`, commit the registry cleanup, and push master.

Do not push master when merge validation introduces a new stable failure.

If `git push origin master` is rejected as non-fast-forward:

1. `git fetch origin`.
2. Integrate `origin/master` without discarding either side.
3. Resolve conflicts and rerun validation.
4. Push normally; never use `--force`.

## 8. Cleanup

Only after the merge, master validation, registry cleanup, and successful push:

```bash
git log --oneline master..<branch>
git worktree remove .worktrees/<slug>
git branch -D <branch>
git worktree prune
git worktree list
```

`git log --oneline master..<branch>` must be empty before deleting the branch. If it is not empty, stop: the branch still contains unmerged commits.

## 9. Safe Git operations

Allowed alternatives to destructive repository-wide resets:

- Revert a public commit: `git revert <commit>`.
- Unstage a file without changing its bytes: `git restore --staged <file>`.
- Restore one owned file from a known commit: `git restore --source=<commit> -- <file>`.
- Discard an owned file's local change only after confirming ownership: `git checkout -- <file>`.
- Stash only your own lane's work, never a shared or foreign worktree.

Forbidden:

- `git reset --hard`
- `git checkout -- .`
- `git clean -f`
- blanket `--ours`, `--theirs`, or `-X theirs`
- `git add -A && git commit` as a substitute for a real merge
- deleting a worktree or branch before proving all commits are merged

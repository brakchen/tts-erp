# Agent Git and worktree workflow

The canonical lifecycle for agent-owned repository changes. **Branches and commits carry work; sessions do not.** The tower-do board is the only live task and lane registry. `docs/handoff/ACTIVE.md` is a static relocation notice, not a second registry.

## 1. One canonical board

Coordinate from a Pi session rooted at `/home/schan/tts-erp`. Run source/Git commands with explicit worktree paths (`git -C <worktree> ...`); creating a checkout does not require moving the coordination session into it.

The installed tower-do resolves the nearest checkout path, **not Git's common directory**. Another worktree, a plugin repository, or another machine does not automatically share this board. Before writes, call `tower_do_status` and confirm its board path matches the canonical root session:

```text
/home/schan/.pi/tower-do/home-schan-tts-erp-a0cde689/board.jsonl
```

A session attached to another board must arrange a canonical-root coordinator/parent to register and relay updates, or resume coordination from the canonical root before writing. Read access to the canonical file is not write authority. If this board is unavailable, stop coordinated writes; do not fall back to ACTIVE or create an independent registry. Include this host-local board in host backups; Git branches preserve work, not future live board records.

## 2. Lane records using supported tools

The installed tools support task `key`, `subject`, `description`, `status`, `owner`, `dependsOn`, and `blockedBy`. They do **not** expose task `scope`, `changedFiles`, lane phases, or a project override. Do not send invented tool arguments.

One permanent task, `lane-registry`, is the discoverable index. It is owned by `tower`, remains `in_progress` while the registry is in use, and consumes one of the 50 open-task slots. It is not a deliverable to mark completed. Each lane is a message in this task's thread, not an extra task slot:

```json
{
  "action": "send",
  "to": "tower",
  "taskKey": "lane-registry",
  "subject": "lane:example-fix",
  "body": "<JSON-encoded complete lane record>"
}
```

Example record (placeholders must be replaced before sending):

```json
{
  "schema": 1,
  "laneId": "example-fix",
  "previous": null,
  "ownerSession": "<real Pi session UUID>",
  "boardIdentity": "<identity reported by tower_do_status>",
  "checkout": "fix/example-fix / /absolute/worktree/path",
  "scope": "explicit owned files/directories; include exclusions and handoffs",
  "state": "draft",
  "stateAt": "<aware UTC timestamp>",
  "headCommit": null,
  "syncedMaster": "<full origin/master SHA at creation>",
  "readyAt": null,
  "validation": "not-run",
  "reservations": []
}
```

- Records are complete replacements, not patches. Use `previous: <current message id>` for every update, including closure. The first record has `previous: null` and a previously unused lane ID. Keep terminal records as receipts; do not reuse IDs.
- `checkout` identifies both branch and absolute worktree; identify the separate repository for plugin lanes. `scope` is the actual file ownership contract, not a task title. `ownerSession` is the real UUID, not `本 session`; `boardIdentity` explicitly captures a configured/session-name alias.
- `reservations` holds child UUID, identity, branch/worktree, exact files, restrictions and release conditions. Those paths are reserved to the child, not concurrent parent writers. Keep any protected foreign WIP and release prohibitions explicit.
- Only the current owner identity may change ownership, scope, checkout or reservations. A handoff records old/new owner, exact files and release conditions, with the prior owner's consent message referenced. Do not use `as` to impersonate a peer. Imported legacy identity labels require confirmation before a handoff; migration is not consent or takeover.
- An integrator may revalidate an imported legacy-ready branch (`blocked` → `ready`), or close a verified ready lane (`ready` → `merged`), without its original session returning. Such a record must retain owner, scope, checkout and reservations, include `integratorSession`, `integratorIdentity` and `integrationEvidence`, and satisfy §6–7. Other non-owner changes are rejected; a merge receipt preserves the tested `headCommit` and `syncedMaster`.
- Task ownership's six-hour takeover exception does **not** automatically release a lane's files or foreign WIP. Request a handoff; unresolved ownership blocks dependent edits. A ready lane remains integrable by anyone under §7.
- Message records are a workflow contract, **not an extension-enforced schema or ownership lock**. Serialize the short read/check/send/read-back step with `/tmp/tts-erp-lane-registry.lock` across canonical-host coordinators. If two records cite the same predecessor, fail closed and reconcile with the owner; never silently choose the last message. Re-read the accepted record before dependent edits.
- Use `tower_do_talk` for all writes. Never append/edit/truncate `board.jsonl` directly. The registry lock covers only reading and sending records, not implementation or tests.

### Read current records, not the default inbox window

`tower_do_status(taskKey="lane-registry")` locates the index, but does not return its full message history. The default message view retires old messages after 14 days; inbox access also depends on the recipient. The append-only file retains them. Project **only this thread**, deduplicate message IDs (read acknowledgements may repeat them), and follow predecessor chains. Example read-only projection:

```bash
python3 - /home/schan/.pi/tower-do/home-schan-tts-erp-a0cde689/board.jsonl <<'PY'
import json
import sys

lanes, seen = {}, set()
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        event = json.loads(line)  # malformed log: fail closed
        message = event.get("message", {})
        if event.get("kind") != "message" or message.get("taskKey") != "lane-registry":
            continue
        subject = message.get("subject", "")
        if not subject.startswith("lane:") or message["id"] in seen:
            continue
        seen.add(message["id"])
        record = json.loads(message["body"])
        lane = record["laneId"]
        assert record["schema"] == 1 and subject == "lane:" + lane
        prior = lanes.get(lane)
        assert record["previous"] == (prior["messageId"] if prior else None), lane
        if prior and message["from"] != prior["boardIdentity"]:
            assert record["integratorIdentity"] == message["from"], lane
            assert record["integratorSession"] and record["integrationEvidence"], lane
            for field in ("ownerSession", "boardIdentity", "checkout", "scope", "reservations"):
                assert record[field] == prior[field], (lane, field)
            closing = prior["state"] == "ready" and record["state"] == "merged"
            revalidating = (prior["state"] == "blocked" and record["state"] == "ready"
                            and prior.get("legacy", {}).get("state") == "ready")
            assert closing or revalidating, lane
            if closing:
                assert record["headCommit"] == prior["headCommit"], lane
                assert record["syncedMaster"] == prior["syncedMaster"], lane
        elif not prior and record.get("legacySource"):
            assert message["from"] == "01a16851-2ab0-7605-a9b1-1e390a2830c5", lane
        elif not prior:
            assert message["from"] == record["boardIdentity"], lane
        lanes[lane] = dict(record, messageId=message["id"])
for lane, record in sorted(lanes.items()):
    if record["state"] not in {"merged", "abandoned"}:
        print(json.dumps(record, ensure_ascii=False))
PY
```

This is an inspection example, not a security/authentication service. Do not run Python with optimization (`-O`); the example uses assertions as fail-closed consistency checks. Check handoff evidence and scope conflicts before action. For one lane, filter the final output by its ID; avoid dumping unrelated board history. Generated views are disposable/read-only and must not be committed as another live registry.

## 3. States and registration before editing

| Lane state | Meaning | File ownership |
| --- | --- | --- |
| `draft` | Registered, implementation not started | Declared files reserved |
| `active` | Implementation in progress | Declared files reserved |
| `blocked` | Named dependency or external action | Files remain reserved unless explicitly released |
| `ready` | Committed, synchronized, validated, pushed | Branch immutable; any session may integrate |
| `merged` / `abandoned` | Terminal receipt | Ownership released; preserve record and evidence |

The task status `completed` does not mean a lane is `ready` or merged. `stateAt` is the lane's last state change, not message delivery time. `readyAt` is its FIFO timestamp; ready records require full immutable `headCommit`, `syncedMaster`, successful validation evidence, and a pushed branch. Later fixes move the lane to active, clear ready fields, and repeat validation.

Before the first task edit:

1. Read the canonical board, current lane records and child reservations. Confirm no draft/active/blocked owner reserves the required paths. For overlapping historical claims, obtain explicit owner reconciliation rather than assuming the import released them.
2. `git fetch origin`, inspect `git status --short --branch` and `git worktree list`. Do not modify, stash, stage, restore, or remove foreign WIP.
3. Under the short lane registry lock, send a draft record with real owner, intended branch/worktree, precise scope, UTC time, and source master SHA. Read it back and confirm no competing registration. No registry-only Git commit or push is required.
4. Create a dedicated worktree/branch from current `origin/master` (or an explicitly recorded ready predecessor commit). Do not implement in the main master worktree. Use `fix/`, `feature/`, `redesign/`, `chore/`, or `docs/` plus a kebab-case topic.
5. Record active status before starting. If creation fails, retain an honest blocked/abandoned record instead of leaving a phantom active checkout.

```bash
git fetch origin
git worktree add .worktrees/<slug> -b <prefix>/<slug> origin/master
```

Use worktree-specific environment setup from the testing guide. Never share a writable production-shaped `.env`, edit the main `.env` through a symlink, or commit credentials. Do not initialize plugin submodules unless needed for the task.

## 4. Ownership and task coordination

One writer owns each writable checkout. File ownership, not session presence, is the conflict boundary. Keep a single writer for hotspots such as scheduler, models, fixtures, schema generators, and deployment scripts.

Do independent work first. For a hotspot, exchange a focused patch/request with its owner, explicitly hand off the file, or create a successor lane from the predecessor's recorded ready commit. Do not wait for a session when a committed ready branch can be integrated. Child reservations belong in the parent's full lane record before child writes; give children the canonical board path and have the parent relay updates when their session has a different board.

Use ordinary board tasks for decomposition, dependencies and progress. `tower_do` is an **upsert**: read status, pass its `baseRevision`, and list only changed rows. Omitted keys/fields are preserved; removal is explicit. On stale rejection re-read and retry. Native owner guards apply to every task field; the documented idle-task adoption is owner-only first, then content changes separately. Do not confuse task adoption with permission to edit another worktree.

Completion evidence and actual `changedFiles` are JSON/text in a threaded message to the work task, not unsupported tool fields. Complete only verified tasks; leave blockers explicit. Scope and file transfers remain in lane records, not transient task descriptions alone.

## 5. Commit and synchronize before ready

Use atomic commits with `feat/fix/chore/docs/style/merge` and concise Chinese text. Stage explicit task-owned paths only: never `git add -A`, `git add .`, or broad equivalents. Before pausing or handoff, commit and push the task branch; if the network is unavailable, report the exact locally committed but unpushed HEAD.

In the lane worktree:

```bash
git fetch origin
git merge --no-edit origin/master
```

Resolve both sides' intent, never blanket `--ours`, `--theirs`, or `-X theirs`. Abort and ask if intent is unclear. Then:

1. Run diagnostics/link/path checks and the narrowest relevant safe tests.
2. For code/test changes, run `bash scripts/test_baseline.sh`; all tests use `scripts/test_isolated.sh` as required by the testing guide. Zero new stable failures are required.
3. Review `git diff --check`, branch diff and staged file list; exclude secrets and foreign WIP.
4. Push the synchronized lane branch. Record full HEAD and `origin/master` SHA, exact validation evidence and a fresh UTC `readyAt` in its full ready record.

Documentation/config-only lanes that change no code/tests validate their own links, syntax, paths and commands; no application suite is needed. After ready, the branch is immutable. New fixes must clear its old ready fields and resynchronize.

## 6. Ready queue and legacy import

Integrate ready lanes in ascending `readyAt` order by default. A queue jump needs an explicit reason in the lane record (for example an urgent production fix or a registry-authority cutover, not a hidden ordering change). Any session may integrate a verified ready lane from its recorded commit; uncommitted files and session logs are not integration inputs.

Migration records carry `legacySource` naming the exact source commit and ACTIVE row. All legacy scope exclusions, handoffs, foreign-WIP protections and child release conditions remain binding until explicitly superseded. Long historical notes remain in that immutable Git source, not as a second maintained registry. An imported ready claim is **not fresh verification**: it is conservatively blocked with `legacyState: ready`, old timestamp/commit claims preserved, and no current `readyAt`. Revalidate before returning it to the ready queue. Never fabricate a missing UUID or upgrade a short/stale commit claim to verified evidence.

The old registry lock `/tmp/tts-erp-active-md.lock` is used once at cutover to capture the final remote snapshot and any concurrent old-rule registrations. It is retired after cutover. Old sessions must read the relocation notice and board before their next write; they must not restore old ACTIVE rows during a merge.

## 7. Final integration into master

Serialize prospective-master creation, post-merge validation, push, and board closure with `/tmp/tts-erp-master-merge.lock`. The lane registry lock is separate; never hold it for testing or conflict resolution.

Before integration, re-read the accepted ready record and queue, fetch origin, and require:

- branch HEAD equals the full recorded `headCommit`;
- **no master commit advanced beyond `syncedMaster`** (compare commit IDs); board updates no longer create Git commits and need no registry-only exception;
- validation evidence applies to that exact branch/commit and the integration path is unused.

If master advanced, release the integration lock, move the lane back to active, merge the new master, resolve conflicts, rerun required checks, push and publish fresh ready fields. Do not hold the master lock during lane preflight or conflict resolution.

Build a prospective master in a clean detached worktree:

```bash
git worktree add --detach .worktrees/.integrate-<slug> origin/master
git -C .worktrees/.integrate-<slug> merge <branch> --no-ff -m "merge: <slug> (lane <lane-id>)"
```

Validate that exact prospective commit. Documentation-only integration confirms no application/test changes and repeats documentation checks. Code/test integration runs the required isolated fast-suite baseline; if lanes overlap in a module, textual conflict freedom does not waive that check.

Push only the validated commit, without force:

```bash
git -C .worktrees/.integrate-<slug> push origin HEAD:master
```

After successful push, append the full merged lane record with integrated master SHA, verification evidence and actual changed paths; close delivered work tasks. **There is no ACTIVE cleanup commit.** A failed push leaves the lane unmerged: resynchronize and repeat normally. A failed validation does not alter the shared master checkout. If push succeeded but board closure failed, keep the checkout/branch, report the gap, and reconcile the exact remote commit before any retry or cleanup; do not merge again blindly.

## 8. Cleanup and safe Git operations

After remote merge, verification and board reconciliation:

```bash
git fetch origin
git log --oneline origin/master..<branch>
```

The log must be empty before removing the task's own clean worktrees/branch. Inspect their status first; never remove foreign WIP. Then remove the task/integration worktrees, delete the fully merged task branch, prune, and inspect `git worktree list`. Preserve foreign changes in main; report task-clean separately from repository-clean instead of pretending a shared dirty checkout is clean.

Allowed scoped operations include `git revert <commit>`, `git restore --staged <owned-file>`, and restoring one owned file from a known commit after confirming ownership. Forbidden: repository-wide destructive reset/checkout/clean, blanket conflict winners, force-pushing master, staging all files, or deleting branches before proving their commits merged.

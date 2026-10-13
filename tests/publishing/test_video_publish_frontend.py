"""视频发布页面的前端契约（02 §14.1 / §17.2 / §18）。

这一层断言页面**怎么消费**服务端契约：多设备轨道、固定 5 秒刷新、
确认已发布单按钮、提交前参数编辑、提交后的取消边界、清理资源收窄。
资格 / 状态判定一律来自后端 ``allowedActions`` 与后端字段，页面只做展示。
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_publishing]

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tts_erp_v2/static/js/video-publish.js"
TEMPLATE = ROOT / "tts_erp_v2/templates/pages/video-publish.html"

# A DOM stub small enough to keep the page script honest: it only ever reaches
# for getElementById / createElement plus the listeners it wires while loading.
# Injected *before* the script boots so the first paint already sees the payload.
_HARNESS_HEAD = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("tts_erp_v2/static/js/video-publish.js", "utf8");
const requests = [];
const elements = new Map();
const created = [];
let holdConfirm = false;
let releaseConfirm = null;
function element() {
  const node = {
    textContent: "", value: "", disabled: false, hidden: false, tagName: "",
    style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    querySelectorAll() { return []; }, replaceChildren() {}, append() {},
    addEventListener(type, handler) { this[`on${type}`] = handler; },
    removeEventListener() {}, setAttribute(name, value) { this[name] = value; }, removeAttribute() {},
    showModal() {
      // The action dialog is auto-confirmed so mutation flows reach the wire;
      // the detail drawer stays open so later refreshes keep re-rendering it.
      if (this.id === "publish-action-dialog") {
        this.returnValue = "confirm";
        setImmediate(() => this.onclose?.());
        return;
      }
      this.returnValue = "cancel";
    },
    close() {}, click() {},
  };
  created.push(node);
  return node;
}
global.location = { pathname: "/v2/pages/video-publish/" };
global.window = global;
global.window.addEventListener = () => {};
global.document = {
  hidden: false,
  getElementById(id) {
    // A real document indexes elements by id; created nodes get registered the
    // moment the page assigns one, so resolve them the same way here.
    const registered = created.find((node) => node.id === id);
    if (registered) return registered;
    const node = element();
    node.id = id;
    if (id === "publish-refresh-mode") node.value = "on";
    elements.set(id, node);
    return node;
  },
  querySelectorAll() { return []; },
  querySelector() { return element(); },
  createElement(tagName) { const node = element(); node.tagName = tagName.toUpperCase(); return node; },
  addEventListener() {},
};
global.crypto = { randomUUID: () => "00000000-0000-0000-0000-000000000000" };
global.URL = { createObjectURL: () => "blob:TEST", revokeObjectURL() {} };
global.localStorage = { getItem: () => null, setItem() {} };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = () => 1;
global.clearTimeout = () => {};
global.fetch = async (url, options) => {
  const method = (options.method || "GET").toUpperCase();
  requests.push({ url, method, headers: options.headers || {}, body: options.body });
  if (method === "PATCH") {
    if (settingsResponse) return settingsResponse();
    return {
      status: 200, ok: true, headers: { get: () => null },
      json: async () => ({ ...files.detail, rowVersion: files.detail.rowVersion + 1 }),
    };
  }
  if (url.endsWith("/confirm-published")) {
    if (holdConfirm) await new Promise((resolve) => { releaseConfirm = resolve; });
    return {
      status: 200, ok: true, headers: { get: () => null },
      json: async () => ({
        ...files.detail,
        status: "succeeded",
        confirmation: { confirmedBy: "user:42", confirmedAt: "2026-10-04T00:00:11Z", fromStatus: "needs_review" },
      }),
    };
  }
  const payload = url.endsWith("/config")
    ? files.config
    : url.includes("/tasks/current")
      ? files.current
      : url.match(/\/tasks\/TEST-/)
        ? files.detail
        : files.list;
  return { status: 200, ok: true, headers: { get: () => null }, json: async () => payload };
};
"""

_HARNESS_BOOT = r"""
vm.runInThisContext(source);
await new Promise((resolve) => setImmediate(resolve));
function nodeById(id) { return created.find((node) => node.id === id); }
function nodeByText(text) { return created.find((node) => node.textContent === text); }
function requestsTo(suffix) { return requests.filter((request) => request.url.endsWith(suffix)); }
function byId(id) { return document.getElementById(id); }
"""


def _config() -> dict:
    return {
        "maxVideoBytes": 100,
        "maxCaptionCharacters": 4000,
        "target": {"album": "TEST", "deviceSerialMasked": "D123…00AC"},
        "device": {},
        "worker": {"status": "ready"},
        "canWrite": True,
    }


def _empty_current() -> dict:
    return {
        "tasks": [],
        "pollState": {"running": False, "cleaning": False, "queued": False},
        "serverTime": "2026-10-04T00:00:09Z",
    }


def _empty_list() -> dict:
    return {
        "items": [],
        "nextCursor": None,
        "pollState": {"running": False, "cleaning": False, "queued": False},
    }


def _run(files: dict, assertions: str) -> subprocess.CompletedProcess[str]:
    script = (
        "(async () => {\n"
        + _HARNESS_HEAD
        + "const files = "
        + json.dumps(files, ensure_ascii=False)
        + ";\n"
        + "let settingsResponse = null;\n"
        + _HARNESS_BOOT
        + textwrap.dedent(assertions)
        + "\n})();"
    )
    return subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_current_renders_one_track_per_device() -> None:
    """02 §17.2: /current 的 tasks[] 每项是一条设备轨道。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "current.tasks" in source
    assert "current.task;" not in source
    assert "renderRail(state.currentTasks)" in source

    files = {
        "config": _config(),
        "current": {
            "tasks": [
                {
                    "taskId": "TEST-aaaa",
                    "filename": "TEST-a.mp4",
                    "status": "running",
                    "stage": "waiting_artemis",
                    "operationalStage": "waiting_artemis",
                    "deviceSerialMasked": "D123…00AC",
                    "currentAttempt": {
                        "kind": "publish",
                        "kindLabel": "正式发布",
                        "artemisSessionId": "session-a",
                        "startedAt": "2026-10-04T00:00:00Z",
                    },
                    "publishAttemptCount": 1,
                    "verifyAttemptCount": 0,
                },
                {
                    "taskId": "TEST-bbbb",
                    "filename": "TEST-b.mp4",
                    "status": "running",
                    "stage": "staging_device",
                    "operationalStage": "staging_device",
                    "deviceSerialMasked": "E456…11BD",
                    "currentAttempt": None,
                    "publishAttemptCount": 1,
                    "verifyAttemptCount": 0,
                },
            ],
            "pollState": {"running": True, "cleaning": False, "queued": False},
            "serverTime": "2026-10-04T00:00:09Z",
            "suggestedPollSeconds": 2,
        },
        "list": _empty_list(),
        "detail": None,
    }
    result = _run(
        files,
        r"""
        const names = created.filter((node) => node.className === "device-track-name");
        if (names.length !== 2) throw new Error(`expected one track per device, got ${names.length}`);
        if (names[0].textContent !== "D123…00AC" || names[1].textContent !== "E456…11BD") {
          throw new Error(`tracks are not keyed by masked serial: ${names.map((node) => node.textContent)}`);
        }
        if (elements.get("publish-rail-empty").hidden !== true) throw new Error("empty state was shown while tracks exist");
        const metas = created.filter((node) => node.className === "device-track-meta");
        if (metas.length !== 2 || !metas[0].textContent.includes("TEST-aaaa")) throw new Error("tracks did not render their own progress line");
        const currentChips = created.filter((node) => node["aria-current"] === "step");
        if (currentChips.length !== 2) throw new Error(`each track must mark its own current stage, got ${currentChips.length}`);
        if (currentChips[0].dataset.stage !== "waiting_artemis" || currentChips[1].dataset.stage !== "staging_device") {
          throw new Error(`tracks marked the wrong stage: ${currentChips.map((node) => node.dataset.stage)}`);
        }
        """,
    )
    assert result.returncode == 0, result.stderr


def test_empty_current_shows_the_empty_state() -> None:
    """02 §18: 没有运行任务时轨道区显示空态，而不是残留上一台设备。"""
    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": _empty_list(),
        "detail": None,
    }
    result = _run(
        files,
        r"""
        if (created.some((node) => node.className === "device-track-name")) throw new Error("empty /current still rendered a device track");
        const empty = elements.get("publish-rail-empty");
        if (empty.hidden !== false) throw new Error("empty state was not shown");
        if (!empty.textContent.includes("当前没有运行中的发布任务")) throw new Error(`unexpected empty copy: ${empty.textContent}`);
        """,
    )
    assert result.returncode == 0, result.stderr


def test_page_markup_offers_the_on_off_switch_only() -> None:
    """02 §18: 刷新开关只有 开/关，没有 smart / 2s / 10s / 30s。"""
    markup = TEMPLATE.read_text(encoding="utf-8")
    switch = markup[markup.index('id="publish-refresh-mode"') :]
    switch = switch[: switch.index("</select>")]
    assert 'value="on"' in switch and 'value="off"' in switch
    for retired in ('value="smart"', 'value="2"', 'value="10"', 'value="30"'):
        assert retired not in switch

    source = SCRIPT.read_text(encoding="utf-8")
    assert "const REFRESH_SECONDS = 5;" in source
    assert "backoffSeconds" not in source
    assert "sharedPollState" not in source
    # suggestedPollSeconds 仍是后端字段，但调度代码不再用它决定刷新间隔。
    scheduler = source[
        source.index("const REFRESH_SECONDS") : source.index("function renderDetail")
    ]
    assert "suggestedPollSeconds" not in scheduler


def test_confirm_published_posts_once_and_shows_the_audit() -> None:
    """02 §7.4/§18: 单按钮、幂等、成功后展示确认审计。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'confirm_published: "确认已发布"' in source
    assert 'button.id = "publish-confirm-published"' in source
    dialog = source[
        source.index("const CONFIRM_ACTIONS") : source.index("const CLEANUP_RESOURCES")
    ]
    assert "confirm_published" not in dialog

    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": {
            "items": [
                {
                    "taskId": "TEST-cccc",
                    "filename": "TEST.mp4",
                    "status": "needs_review",
                    "stage": "done",
                    "rowVersion": 12,
                    "createdAt": "2026-10-04T00:00:00Z",
                    "caption": "TEST caption",
                    "latestArtemisSessionId": "session-a",
                    "allowedActions": [
                        "view",
                        "retry",
                        "verify",
                        "confirm_published",
                        "copy_artemis_id",
                    ],
                }
            ],
            "nextCursor": None,
            "pollState": {"running": False, "cleaning": False, "queued": False},
        },
        "detail": {
            "taskId": "TEST-cccc",
            "filename": "TEST.mp4",
            "status": "needs_review",
            "stage": "done",
            "rowVersion": 12,
            "caption": "TEST caption",
            "paramsLockedAt": "2026-10-04T00:00:06Z",
            "confirmation": None,
            "target": {
                "appName": "TikTok",
                "appPackage": "com.zhiliaoapp.musically",
                "deviceSerialMasked": "D123…00AC",
                "album": "TEST",
            },
            "attempts": [],
            "cleanup": {
                "device": {"status": "succeeded", "error": None},
                "spool": {"status": "succeeded", "error": None},
            },
            "allowedActions": ["view", "confirm_published", "copy_artemis_id"],
        },
    }
    result = _run(
        files,
        r"""
        const confirm = nodeByText("确认已发布");
        if (!confirm) throw new Error("confirm_published did not render its button");
        holdConfirm = true;
        const first = confirm.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const second = confirm.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        if (requestsTo("/confirm-published").length !== 1) throw new Error("repeat click was not suppressed while pending");
        if (!confirm.disabled) throw new Error("confirm button stayed clickable while the POST was in flight");
        releaseConfirm();
        await Promise.all([first, second]);
        const posted = requestsTo("/confirm-published")[0];
        if (!posted) throw new Error("confirm_published did not POST");
        if (posted.headers["X-Requested-With"] !== "tts-erp") throw new Error("confirm omitted the CSRF header");
        if (JSON.parse(posted.body).rowVersion !== 12) throw new Error("confirm omitted the rendered rowVersion");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_confirmed_success_shows_operator_and_timestamp() -> None:
    """02 §9.6: 有审计就如实展示操作人与点击时间。"""
    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": _empty_list(),
        "detail": {
            "taskId": "TEST-eeee",
            "filename": "TEST.mp4",
            "status": "succeeded",
            "stage": "done",
            "rowVersion": 13,
            "caption": "TEST caption",
            "paramsLockedAt": "2026-10-04T00:00:06Z",
            "confirmation": {
                "confirmedBy": "user:42",
                "confirmedAt": "2026-10-04T00:00:11Z",
                "fromStatus": "needs_review",
            },
            "target": {
                "appName": "TikTok",
                "appPackage": "com.zhiliaoapp.musically",
                "deviceSerialMasked": "D123…00AC",
                "album": "TEST",
            },
            "attempts": [],
            "cleanup": {
                "device": {"status": "succeeded", "error": None},
                "spool": {"status": "succeeded", "error": None},
            },
            "allowedActions": ["view", "copy_artemis_id"],
        },
    }
    # Seed the drawer by letting the list carry the same task and clicking 查看.
    files["list"] = {
        "items": [
            {
                "taskId": "TEST-eeee",
                "filename": "TEST.mp4",
                "status": "succeeded",
                "stage": "done",
                "rowVersion": 13,
                "createdAt": "2026-10-04T00:00:00Z",
                "caption": "TEST caption",
                "allowedActions": ["view", "copy_artemis_id"],
            }
        ],
        "nextCursor": None,
        "pollState": {"running": False, "cleaning": False, "queued": False},
    }
    result = _run(
        files,
        r"""
        await nodeByText("查看").onclick();
        if (!created.some((node) => node.textContent.includes("user:42"))) throw new Error("confirmation audit omitted the operator");
        if (created.some((node) => node.textContent.includes("历史自动成功"))) throw new Error("a confirmed task was reported as legacy auto-success");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_auto_success_is_reported_without_a_fake_operator() -> None:
    """02 §9.6: confirmation 为 null 的旧成功任务不得伪造操作人与时间。"""
    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": {
            "items": [
                {
                    "taskId": "TEST-1010",
                    "filename": "TEST.mp4",
                    "status": "succeeded",
                    "stage": "done",
                    "rowVersion": 2,
                    "createdAt": "2026-10-04T00:00:00Z",
                    "caption": "TEST caption",
                    "allowedActions": ["view", "copy_artemis_id"],
                }
            ],
            "nextCursor": None,
            "pollState": {"running": False, "cleaning": False, "queued": False},
        },
        "detail": {
            "taskId": "TEST-1010",
            "filename": "TEST.mp4",
            "status": "succeeded",
            "stage": "done",
            "rowVersion": 2,
            "caption": "TEST caption",
            "paramsLockedAt": "2026-10-04T00:00:06Z",
            "confirmation": None,
            "target": {
                "appName": "TikTok",
                "appPackage": "com.zhiliaoapp.musically",
                "deviceSerialMasked": "D123…00AC",
                "album": "TEST",
            },
            "attempts": [],
            "cleanup": {
                "device": {"status": "succeeded", "error": None},
                "spool": {"status": "succeeded", "error": None},
            },
            "allowedActions": ["view", "copy_artemis_id"],
        },
    }
    result = _run(
        files,
        r"""
        await nodeByText("查看").onclick();
        if (!created.some((node) => node.textContent.includes("历史自动成功"))) throw new Error("legacy auto-success was not reported honestly");
        if (!created.some((node) => node.textContent.includes("没有人工确认记录"))) throw new Error("legacy row did not state that it has no audit");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_settings_editor_sends_row_version_and_freezes_for_good() -> None:
    """02 §9.5: 未锁定可编辑并携带 rowVersion；锁定后本页永久只读。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'edit_settings: "修改发布设置"' in source
    assert 'method: "PATCH"' in source
    assert "/settings`" in source
    assert 'error.code === "TASK_ALREADY_SUBMITTED"' in source
    assert 'action === "cancel" && paramsLocked(detail)' in source

    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": {
            "items": [
                {
                    "taskId": "TEST-dddd",
                    "filename": "TEST.mp4",
                    "status": "pending",
                    "stage": "queued",
                    "rowVersion": 3,
                    "createdAt": "2026-10-04T00:00:00Z",
                    "caption": "TEST caption",
                    "allowedActions": [
                        "view",
                        "edit_settings",
                        "cancel",
                        "copy_artemis_id",
                    ],
                }
            ],
            "nextCursor": None,
            "pollState": {"running": False, "cleaning": False, "queued": True},
        },
        "detail": {
            "taskId": "TEST-dddd",
            "filename": "TEST.mp4",
            "status": "pending",
            "stage": "queued",
            "rowVersion": 3,
            "paramsLockedAt": None,
            "confirmation": None,
            "caption": "TEST caption",
            "target": {
                "appName": "TikTok",
                "appPackage": "com.zhiliaoapp.musically",
                "deviceSerialMasked": "D123…00AC",
                "album": "TEST",
            },
            "attempts": [],
            "cleanup": {
                "device": {"status": "not_started", "error": None},
                "spool": {"status": "not_started", "error": None},
            },
            "allowedActions": ["view", "edit_settings", "cancel", "copy_artemis_id"],
        },
    }
    result = _run(
        files,
        r"""
        await nodeByText("查看").onclick();
        const save = nodeById("publish-settings-save");
        if (!save) throw new Error("unlocked pending task did not offer the settings editor");
        if (!created.some((node) => node.textContent === "取消")) throw new Error("unlocked task hid its cancel entry");
        byId("publish-settings-prompt").value = "TEST 正文";
        await save.onclick();
        const patch = requests.filter((request) => request.method === "PATCH").at(-1);
        if (!patch || !patch.url.endsWith("/tasks/TEST-dddd/settings")) throw new Error("settings were not saved with PATCH");
        const body = JSON.parse(patch.body);
        if (body.rowVersion !== 3) throw new Error("settings save omitted the rendered rowVersion");
        if (body.prompt !== "TEST 正文") throw new Error("edited prompt was not sent");
        if ("deviceSerial" in body) throw new Error("untouched masked device serial was sent back");
        if (patch.headers["X-Requested-With"] !== "tts-erp") throw new Error("settings save omitted the CSRF header");
        // A refused edit is permanent: the editor and the cancel entry both go.
        const createdBeforeRefusal = created.length;
        settingsResponse = () => ({
          status: 409, ok: false, headers: { get: () => null },
          json: async () => ({
            detail: {
              code: "TASK_ALREADY_SUBMITTED",
              message: "任务已成功提交给发布端，无法再取消或修改参数。",
              allowedActions: ["view"],
              rowVersion: 9,
            },
          }),
        });
        byId("publish-settings-prompt").value = "TEST 再次修改";
        await save.onclick();
        const refused = requests.filter((request) => request.method === "PATCH").at(-1);
        if (!refused) throw new Error("refused settings edit was never sent");
        const afterRefusal = created.slice(createdBeforeRefusal);
        if (afterRefusal.some((node) => node.id === "publish-settings-save")) throw new Error("TASK_ALREADY_SUBMITTED did not retire the settings editor");
        if (!afterRefusal.some((node) => node.textContent.includes("不可再修改"))) throw new Error("refused edit did not switch the section to read-only");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_locked_task_is_read_only_and_has_no_cancel_entry() -> None:
    """02 §18: 锁定后展示锁定时间，且不再提供取消/停止入口。"""
    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": {
            "items": [
                {
                    "taskId": "TEST-ffff",
                    "filename": "TEST.mp4",
                    "status": "running",
                    "stage": "waiting_artemis",
                    "rowVersion": 7,
                    "createdAt": "2026-10-04T00:00:00Z",
                    "caption": "TEST caption",
                    "allowedActions": ["view", "copy_artemis_id"],
                }
            ],
            "nextCursor": None,
            "pollState": {"running": True, "cleaning": False, "queued": False},
        },
        "detail": {
            "taskId": "TEST-ffff",
            "filename": "TEST.mp4",
            "status": "running",
            "stage": "waiting_artemis",
            "rowVersion": 7,
            "caption": "TEST caption",
            "paramsLockedAt": "2026-10-04T00:00:06Z",
            "confirmation": None,
            "target": {
                "appName": "TikTok",
                "appPackage": "com.zhiliaoapp.musically",
                "deviceSerialMasked": "D123…00AC",
                "album": "TEST",
            },
            "attempts": [],
            "cleanup": {
                "device": {"status": "pending", "error": None},
                "spool": {"status": "not_started", "error": None},
            },
            "allowedActions": ["view", "edit_settings", "cancel", "copy_artemis_id"],
        },
    }
    result = _run(
        files,
        r"""
        await nodeByText("查看").onclick();
        if (nodeById("publish-settings-save")) throw new Error("locked task still offered the settings editor");
        if (created.some((node) => node.className === "btn-secondary drawer-action" && node.textContent === "取消")) {
          throw new Error("locked task still offered 取消");
        }
        if (!created.some((node) => node.textContent.includes("参数已于"))) throw new Error("locked task did not show when its parameters froze");
        if (!created.some((node) => node.textContent.includes("原视频 已保留（不删除）"))) throw new Error("cleanup line omitted the retained object");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_retry_only_names_device_and_spool() -> None:
    """02 §14.1: 清理重试只提交 device/spool，原视频固定为「已保留（不删除）」。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'const CLEANUP_RESOURCES = ["device", "spool"];' in source
    assert "body.resources = CLEANUP_RESOURCES.filter" in source
    assert "cleanupBlock.object" not in source
    assert "原视频 已保留（不删除）" in source

    files = {
        "config": _config(),
        "current": _empty_current(),
        "list": {
            "items": [
                {
                    "taskId": "TEST-9999",
                    "filename": "TEST.mp4",
                    "status": "failed",
                    "stage": "done",
                    "rowVersion": 5,
                    "createdAt": "2026-10-04T00:00:00Z",
                    "caption": "TEST caption",
                    "cleanupRetryableResources": ["device"],
                    "allowedActions": ["view", "retry_cleanup", "copy_artemis_id"],
                }
            ],
            "nextCursor": None,
            "pollState": {"running": False, "cleaning": False, "queued": False},
        },
        "detail": None,
    }
    result = _run(
        files,
        r"""
        await nodeByText("重试清理").onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const posted = requestsTo("/cleanup/retry")[0];
        if (!posted) throw new Error("cleanup retry was not requested");
        const resources = JSON.parse(posted.body).resources;
        if (JSON.stringify(resources) !== JSON.stringify(["device"])) throw new Error(`unexpected cleanup resources: ${resources}`);
        if (resources.includes("object")) throw new Error("retired object resource was sent");
        """,
    )
    assert result.returncode == 0, result.stderr


def test_retired_fields_are_gone_from_the_page() -> None:
    """预算 / 安全重试 / 诊断门是退役字段，页面不得再读取或展示。"""
    source = SCRIPT.read_text(encoding="utf-8")
    for retired in (
        "retryBudgetUsed",
        "maxPublishAttempts",
        "retrySafe",
        "canViewDiagnostics",
    ):
        assert retired not in source
    # 诊断是业务记录的一部分，页面直接请求，不再有角色门。
    assert "?includeDiagnostics=true" in source

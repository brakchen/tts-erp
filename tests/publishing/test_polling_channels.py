from __future__ import annotations

import subprocess
import textwrap

import pytest

pytestmark = [pytest.mark.domain_publishing]


def test_frontend_polling_channels_keep_independent_abort_state() -> None:
    script = (
        "(async () => {\n"
        + textwrap.dedent(
            r"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync("tts_erp_v2/static/js/video-publish.js", "utf8");
        const timers = [];
        const delays = [];
        const requests = [];
        const elements = new Map();
        const created = [];
        let confirmCalls = 0;
        const confirmMessages = [];
        const confirmTitles = [];
        const confirmButtons = [];
        let filePickerClicks = 0;
        let appendMode = "success";
        let holdRetry = false;
        let releaseRetry = null;
        function element() {
          const node = {
            textContent: "", value: "", disabled: false, hidden: false, tagName: "",
            style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
            querySelectorAll() { return []; }, replaceChildren() {}, append() {},
            addEventListener(type, handler) { this[`on${type}`] = handler; }, removeEventListener(type, handler) { if (this[`on${type}`] === handler) this[`on${type}`] = null; }, setAttribute(name, value) { this[name] = value; }, removeAttribute() {},
            showModal() {
              if (this.id === "publish-action-dialog") {
                confirmCalls += 1;
                confirmMessages.push(elements.get("publish-action-evidence").textContent);
                confirmTitles.push(elements.get("publish-action-title").textContent);
                confirmButtons.push(elements.get("publish-action-confirm").textContent);
                this.returnValue = "confirm";
                setImmediate(() => this.onclose?.());
              }
            },
            close() {}, click() { if (this.id === "publish-video-file") filePickerClicks += 1; },
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
            if (!elements.has(id)) {
              const node = element();
              node.id = id;
              if (id === "publish-refresh-mode") node.value = "on";
              elements.set(id, node);
            }
            return elements.get(id);
          },
          querySelectorAll() { return []; },
          querySelector() { return element(); },
          createElement(tagName) { const node = element(); node.tagName = tagName.toUpperCase(); return node; },
          addEventListener() {},
        };
        global.crypto = { randomUUID: () => "00000000-0000-0000-0000-000000000000" };
        global.URL = { createObjectURL: () => "blob:test", revokeObjectURL() {} };
        global.localStorage = { getItem: () => null, setItem() {} };
        global.navigator.clipboard = { writeText: async () => {} };
        global.setTimeout = (callback, delay) => { timers.push(callback); delays.push(delay); return timers.length; };
        global.clearTimeout = () => {};
        global.fetch = async (url, options) => {
          requests.push({ url, method: (options.method || "GET").toUpperCase(), signal: options.signal, headers: options.headers || {}, body: options.body });
          if (url.includes("cursor=cursor-2")) {
            if (appendMode === "error") throw new Error("append failed");
            if (appendMode === "abort") { const error = new Error("aborted"); error.name = "AbortError"; throw error; }
            if (appendMode === "304") return { status: 304, ok: false, headers: { get: () => null } };
          }
          if (holdRetry && url.endsWith("/tasks/task-1/retry")) {
            await new Promise((resolve) => { releaseRetry = resolve; });
          }
          const payload = url.endsWith("/config")
            ? { maxVideoBytes: 100, maxCaptionCharacters: 4000, target: { album: "TEST" }, device: {}, worker: { status: "ready" }, canWrite: true }
            : url.includes("/tasks/current")
              ? { tasks: [{ taskId: "running-1", filename: "TEST-running.mp4", stage: "verifying", operationalStage: "verifying", stageStartedAt: "2026-10-04T00:00:00Z", status: "running", deviceSerialMasked: "D123…00AC", publishAttemptCount: 2, verifyAttemptCount: 1, currentAttempt: { kind: "verify", kindLabel: "自动核验", artemisSessionId: "verify-session-id", startedAt: "2026-10-04T00:00:01Z" } }, { taskId: "running-2", filename: "TEST-second-device.mp4", stage: "staging_device", operationalStage: "staging_device", stageStartedAt: "2026-10-04T00:00:05Z", status: "running", deviceSerialMasked: "E456…11BD", publishAttemptCount: 1, verifyAttemptCount: 0, currentAttempt: null }], pollState: { running: true, cleaning: false, queued: true }, serverTime: "2026-10-04T00:00:09Z", suggestedPollSeconds: 2 }
              : url.includes("/tasks/task-1")
                ? { taskId: "task-1", filename: "TEST.mp4", status: "succeeded", caption: "TEST", rowVersion: 3, paramsLockedAt: "2026-10-04T00:00:06Z", confirmation: { confirmedBy: "user:42", confirmedAt: "2026-10-04T00:00:07Z", fromStatus: "needs_review" }, target: { appName: "TikTok", appPackage: "com.zhiliaoapp.musically", deviceSerialMasked: "D123…00AC", album: "TEST" }, allowedActions: ["view", "copy_artemis_id"], attempts: [{ sequenceNo: 1, kind: "publish", kindLabel: "正式发布", status: "success", artemisSessionId: "full-artemis-session-id", retryClassification: "publish_action_observed", promptSnapshot: "SECRET_PROMPT", artemisOutput: { verdict: "published" } }], cleanup: { device: { status: "succeeded" }, spool: { status: "succeeded" } } }
                : { items: [{ taskId: "task-1", status: "needs_review", filename: "TEST.mp4", latestArtemisSessionId: "verify-session-id", relatedPublishAttempt: { artemisSessionId: "publish-session-id" }, rowVersion: 3, createdAt: "2026-10-04T00:00:00Z", clientRequestId: "client-1", caption: "TEST caption", cleanupRetryableResources: ["device"], allowedActions: ["view", "continue_upload", "edit_settings", "cancel", "retry", "verify", "confirm_published", "retry_cleanup", "copy_artemis_id"] }, { taskId: "task-2", status: "pending", stage: "queued", filename: "TEST-queued.mp4", latestArtemisSessionId: null, rowVersion: 1, createdAt: "2026-10-04T00:00:01Z", clientRequestId: "client-2", caption: "TEST queued", allowedActions: ["view", "cancel"] }], nextCursor: "cursor-2", pollState: { running: true, cleaning: false, queued: true } };
          return { status: 200, ok: true, headers: { get: () => '\"TEST-etag\"' }, json: async () => payload };
        };
        vm.runInThisContext(source);
        await new Promise((resolve) => setImmediate(resolve));
        if (timers.length < 2) throw new Error("current/list timers were not scheduled");
        // 02 §18: one fixed 5s cadence for every channel — no smart/interval mode
        // and no server-suggested interval.
        if (delays[0] !== 5000 || delays[1] !== 5000) throw new Error(`channels did not use the fixed 5s cadence: ${delays.slice(0, 2)}`);
        if (delays.some((delay) => delay === 2000 || delay === 30000 || delay === 8000)) throw new Error("a retired dynamic polling interval was scheduled");
        const trackNames = created.filter((node) => node.className === "device-track-name").map((node) => node.textContent);
        if (trackNames.length !== 2 || trackNames[0] !== "D123…00AC" || trackNames[1] !== "E456…11BD") throw new Error(`one track per device was not rendered: ${trackNames}`);
        const railMetas = created.filter((node) => node.className === "device-track-meta").map((node) => node.textContent);
        if (railMetas.length !== 2) throw new Error("device tracks did not render their own progress line");
        if (!railMetas[0].includes("自动核验 #1") || !railMetas[0].includes("2026-10-04T00:00:00Z")) throw new Error(`rail attempt type/count or stage start is wrong: ${railMetas[0]}`);
        const listRequest = requests.find((request) => request.url.includes("/tasks?") || request.url.endsWith("/tasks"));
        const currentRequest = requests.find((request) => request.url.includes("/tasks/current"));
        if (!listRequest || !currentRequest || listRequest.signal === currentRequest.signal) {
          throw new Error("current and list did not receive distinct signals");
        }
        document.hidden = true;
        const refreshMode = elements.get("publish-refresh-mode");
        const timersBeforeHidden = timers.length;
        await refreshMode.onchange();
        if (timers.length !== timersBeforeHidden) throw new Error("hidden page kept scheduling polling timers");
        document.hidden = false;
        await timers[0]();
        if (delays.at(-1) !== 5000) throw new Error("refresh cadence drifted away from the fixed 5 seconds");
        if (listRequest.signal.aborted) throw new Error("current refresh aborted list channel");
        const latestCurrent = requests.filter((request) => request.url.includes("/tasks/current")).at(-1);
        await timers[1]();
        if (latestCurrent.signal.aborted) throw new Error("list refresh aborted current channel");
        const expectedLabels = ["查看", "继续上传", "修改发布设置", "取消", "重试", "再次自动核验", "确认已发布", "重试清理"];
        for (const label of expectedLabels) {
          if (!created.some((node) => node.textContent === label)) throw new Error(`missing action ${label}`);
        }
        const uncreatedAttempt = created.find((node) => node.className === "artemis-cell" && node.textContent === "尚未创建");
        if (!uncreatedAttempt) throw new Error("queued task without an Artemis ID did not render 尚未创建");
        const adjacentCopy = created.find((node) => node.textContent === "复制");
        if (!adjacentCopy) throw new Error("Artemis copy control was not rendered in the ID cell");
        await adjacentCopy.onclick();
        if (adjacentCopy.textContent !== "已复制") throw new Error("copy control omitted temporary success state");
        const copyTimerIndex = delays.findIndex((delay) => delay === 1500);
        if (copyTimerIndex < 0) throw new Error("copy success state was not temporary");
        timers[copyTimerIndex]();
        if (adjacentCopy.textContent !== "复制") throw new Error("copy control did not restore its label");
        const initialActions = expectedLabels.map((label) => created.find((node) => node.textContent === label));
        for (const button of initialActions) await button.onclick();
        if (confirmCalls !== 4) throw new Error(`unexpected confirmation count ${confirmCalls}`);
        for (const required of ["已上传的原视频（对象不删除）", "请先自行核验作品页再决定是否重试", "publish-session-id", "不会再次点击发布", "不会改变业务发布结果"]) {
          if (!confirmMessages.some((message) => message.includes(required))) throw new Error(`missing safety confirmation: ${required}`);
        }
        if (!confirmTitles.includes("再次自动核验") || !confirmButtons.includes("开始核验")) throw new Error("verify dialog labels are incorrect");
        if (filePickerClicks !== 1) throw new Error("continue upload did not reopen file picker");
        const mutationRequests = requests.filter((request) => request.method === "POST");
        for (const request of mutationRequests) {
          if (request.headers["X-Requested-With"] !== "tts-erp") throw new Error(`mutation omitted CSRF header: ${request.url}`);
          if (!request.body || JSON.parse(request.body).rowVersion !== 3) throw new Error(`mutation omitted list rowVersion: ${request.url}`);
        }
        const cleanupRequest = mutationRequests.find((request) => request.url.endsWith("/cleanup/retry"));
        if (!cleanupRequest || JSON.stringify(JSON.parse(cleanupRequest.body).resources) !== JSON.stringify(["device"])) throw new Error("cleanup retry was not resource scoped");
        // 02 §18: 确认已发布 是单按钮幂等动作，不再套确认弹窗。
        const confirmRequest = mutationRequests.find((request) => request.url.endsWith("/confirm-published"));
        if (!confirmRequest) throw new Error("确认已发布 did not POST to /confirm-published");
        if (!requests.some((request) => request.url.includes("/tasks/task-1?includeDiagnostics=true"))) throw new Error("detail did not request the business-record diagnostics");
        holdRetry = true;
        const retryButton = initialActions[4];
        const duplicateBefore = requests.filter((request) => request.url.endsWith("/tasks/task-1/retry")).length;
        const firstRetry = retryButton.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const secondRetry = retryButton.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const duplicateDuring = requests.filter((request) => request.url.endsWith("/tasks/task-1/retry")).length;
        if (duplicateDuring !== duplicateBefore + 1) throw new Error("duplicate mutation POST was not suppressed");
        if (!retryButton.disabled) throw new Error("task mutation control was not disabled while pending");
        releaseRetry();
        await Promise.all([firstRetry, secondRetry]);
        if (retryButton.disabled) throw new Error("task mutation control was not restored after settlement");
        holdRetry = false;
        const loadMore = elements.get("publish-load-more");
        for (const mode of ["error", "304", "abort"]) {
          appendMode = mode;
          await loadMore.onclick({ currentTarget: loadMore });
          const appendRequest = requests.filter((request) => request.url.includes("cursor=cursor-2")).at(-1);
          if (appendRequest.headers["If-None-Match"]) throw new Error(`append request reused ETag during ${mode}`);
          if (loadMore.disabled) throw new Error(`load-more remained disabled after ${mode}`);
        }
        appendMode = "success";
        const actionDialog = elements.get("publish-action-dialog");
        const postsBeforeEscape = requests.filter((request) => request.method === "POST").length;
        actionDialog.showModal = function() {
          this.oncancel?.({});
          setImmediate(() => this.onclose?.());
        };
        await created.find((node) => node.textContent === "重试").onclick();
        const postsAfterEscape = requests.filter((request) => request.method === "POST").length;
        if (postsAfterEscape !== postsBeforeEscape) throw new Error("reopened dialog Escape reused stale confirmation");
        const view = created.find((node) => node.textContent === "查看");
        if (!view) throw new Error("detail trigger was not rendered");
        await view.onclick();
        if (!created.some((node) => node.textContent === "full-artemis-session-id")) throw new Error("detail hid the full Artemis session ID");
        if (!created.some((node) => node.textContent.includes("正式发布"))) throw new Error("detail omitted localized attempt kind");
        // P2-15: the cleanup line must carry the localized status label, not the
        // raw backend token ("succeeded"), which is the defect P2-15 reports.
        if (!created.some((node) => node.textContent.includes("清理：设备 已清理"))) throw new Error("detail omitted cleanup sections or leaked a raw cleanup status token");
        if (created.some((node) => /清理：设备 (succeeded|failed|pending|not_started)/.test(node.textContent))) throw new Error("detail leaked a raw cleanup status token to the operator");
        // 02 §14.1: object cleanup is retired — no object status, a fixed retained fact.
        if (!created.some((node) => node.textContent.includes("原视频 已保留（不删除）"))) throw new Error("detail did not state that the source object is retained");
        if (created.some((node) => /清理：.*对象 (succeeded|failed|pending|not_started)/.test(node.textContent))) throw new Error("detail still renders a retired object cleanup status");
        // 02 §18: the drawer shows the confirmation audit, and a locked task offers
        // no settings editor at all.
        if (!created.some((node) => node.textContent.includes("已确认发布：user:42"))) throw new Error("drawer omitted the confirmation audit");
        if (created.some((node) => node.id === "publish-settings-save")) throw new Error("locked task still offered the settings editor");
        if (!created.some((node) => node.textContent.includes("参数已于"))) throw new Error("locked task did not show when its parameters froze");
        if (!created.some((node) => node.tagName === "SUMMARY" && node.textContent === "管理员诊断")) throw new Error("admin diagnostics were not folded");
        if (!created.some((node) => node.tagName === "PRE" && node.textContent.includes("SECRET_PROMPT"))) throw new Error("admin diagnostics were not rendered as text");
        const latestList = requests.filter((request) => request.method === "GET" && request.url.includes("/tasks") && !request.url.includes("/tasks/current") && !request.url.includes("/tasks/task-1")).at(-1);
        const latestCurrentAfterActions = requests.filter((request) => request.url.includes("/tasks/current")).at(-1);
        const latestDetail = requests.filter((request) => request.url.includes("/tasks/task-1")).at(-1);
        const detailTimer = timers.at(-1);
        await detailTimer();
        if (latestList.signal.aborted || latestCurrentAfterActions.signal.aborted) throw new Error("detail refresh aborted another channel");
        if (!latestDetail.signal.aborted) throw new Error("detail refresh did not replace its own request");
        const mode = elements.get("publish-refresh-mode");
        const timerCount = timers.length;
        mode.value = "off";
        await mode.onchange();
        if (timers.length !== timerCount) throw new Error("refresh-off scheduled another timer");
        const refreshStatus = elements.get("publish-refresh-status");
        if (!refreshStatus || refreshStatus.textContent !== "自动刷新已暂停" || refreshStatus.hidden) throw new Error("refresh-off indicator is missing");
        mode.value = "on";
        await mode.onchange();
        if (timers.length <= timerCount || !refreshStatus.hidden) throw new Error("refresh-on did not restore polling state");
        """
        )
        + "\n})();"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

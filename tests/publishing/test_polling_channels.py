from __future__ import annotations

import subprocess
import textwrap


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
        let filePickerClicks = 0;
        function element() {
          const node = {
            textContent: "", value: "", disabled: false, hidden: false, tagName: "",
            style: {}, dataset: {}, classList: { toggle() {} },
            querySelectorAll() { return []; }, replaceChildren() {}, append() {},
            addEventListener(type, handler) { this[`on${type}`] = handler; }, setAttribute(name, value) { this[name] = value; }, removeAttribute() {},
            showModal() {
              if (this.id === "publish-action-dialog") {
                confirmCalls += 1;
                confirmMessages.push(elements.get("publish-action-evidence").textContent);
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
              if (id === "publish-refresh-mode") node.value = "smart";
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
          const payload = url.endsWith("/config")
            ? { maxVideoBytes: 100, maxCaptionCharacters: 4000, target: { album: "TEST" }, device: {}, worker: { status: "ready" }, canWrite: true }
            : url.includes("/tasks/current") ? { task: { taskId: "running-1", filename: "TEST-running.mp4", stage: "verifying", operationalStage: "verifying", stageStartedAt: "2026-10-04T00:00:00Z", status: "running", publishAttemptCount: 2, verifyAttemptCount: 1, currentAttempt: { kind: "verify", artemisSessionId: "verify-session-id", startedAt: "2026-10-04T00:00:01Z" } }, pollState: { running: true, cleaning: false, queued: true } }
            : url.includes("/tasks/task-1")
              ? { taskId: "task-1", filename: "TEST.mp4", status: "succeeded", caption: "TEST", attempts: [{ sequenceNo: 1, kind: "publish", status: "success", artemisSessionId: "full-artemis-session-id", promptSnapshot: "SECRET_PROMPT", artemisOutput: { verdict: "published" } }], cleanup: { device: { status: "succeeded" }, spool: { status: "succeeded" }, object: { status: "failed" } } }
              : { items: [{ taskId: "task-1", status: "pending", filename: "TEST.mp4", latestArtemisSessionId: "artemis-1", rowVersion: 3, createdAt: "2026-10-04T00:00:00Z", clientRequestId: "client-1", caption: "TEST caption", cleanupRetryableResources: ["object"], allowedActions: ["view", "continue_upload", "cancel", "retry", "verify", "retry_cleanup", "copy_artemis_id"] }], pollState: { running: true, cleaning: false, queued: true } };
          return { status: 200, ok: true, headers: { get: () => null }, json: async () => payload };
        };
        vm.runInThisContext(source);
        await new Promise((resolve) => setImmediate(resolve));
        if (timers.length < 2) throw new Error("current/list timers were not scheduled");
        if (delays[0] !== 2000 || delays[1] !== 5000) throw new Error("running cadence did not take precedence over queued work");
        const railMeta = elements.get("publish-rail-meta").textContent;
        if (!railMeta.includes("verify #1") || !railMeta.includes("2026-10-04T00:00:00Z")) throw new Error(`rail attempt type/count or stage start is wrong: ${railMeta}`);
        const listRequest = requests.find((request) => request.url.includes("/tasks?") || request.url.endsWith("/tasks"));
        const currentRequest = requests.find((request) => request.url.includes("/tasks/current"));
        if (!listRequest || !currentRequest || listRequest.signal === currentRequest.signal) {
          throw new Error("current and list did not receive distinct signals");
        }
        document.hidden = true;
        await timers[0]();
        if (delays.at(-1) < 30000) throw new Error("hidden polling is too frequent");
        if (listRequest.signal.aborted) throw new Error("current refresh aborted list channel");
        const latestCurrent = requests.filter((request) => request.url.includes("/tasks/current")).at(-1);
        await timers[1]();
        if (latestCurrent.signal.aborted) throw new Error("list refresh aborted current channel");
        const expectedLabels = ["查看", "继续上传", "取消", "重试", "核验", "重试清理", "复制 Artemis ID"];
        for (const label of expectedLabels) {
          if (!created.some((node) => node.textContent === label)) throw new Error(`missing action ${label}`);
        }
        const initialActions = expectedLabels.map((label) => created.find((node) => node.textContent === label));
        for (const button of initialActions) await button.onclick();
        if (confirmCalls !== 4) throw new Error(`unexpected confirmation count ${confirmCalls}`);
        for (const required of ["清理已生成的对象", "不会自动确认模糊结果", "不会再次点击发布", "不会改变业务发布结果"]) {
          if (!confirmMessages.some((message) => message.includes(required))) throw new Error(`missing safety confirmation: ${required}`);
        }
        if (filePickerClicks !== 1) throw new Error("continue upload did not reopen file picker");
        const mutationRequests = requests.filter((request) => request.method === "POST");
        for (const request of mutationRequests) {
          if (request.headers["X-Requested-With"] !== "tts-erp") throw new Error(`mutation omitted CSRF header: ${request.url}`);
          if (!request.body || JSON.parse(request.body).rowVersion !== 3) throw new Error(`mutation omitted list rowVersion: ${request.url}`);
        }
        const cleanupRequest = mutationRequests.find((request) => request.url.endsWith("/cleanup/retry"));
        if (!cleanupRequest || JSON.stringify(JSON.parse(cleanupRequest.body).resources) !== JSON.stringify(["object"])) throw new Error("cleanup retry was not resource scoped");
        const view = created.find((node) => node.textContent === "查看");
        if (!view) throw new Error("detail trigger was not rendered");
        await view.onclick();
        if (!created.some((node) => node.textContent === "full-artemis-session-id")) throw new Error("detail hid the full Artemis session ID");
        if (!created.some((node) => node.textContent.includes("清理：设备 succeeded"))) throw new Error("detail omitted cleanup sections");
        if (!created.some((node) => node.tagName === "SUMMARY" && node.textContent === "管理员诊断")) throw new Error("admin diagnostics were not folded");
        if (!created.some((node) => node.tagName === "PRE" && node.textContent.includes("SECRET_PROMPT"))) throw new Error("admin diagnostics were not rendered as text");
        const latestList = requests.filter((request) => request.url.endsWith("/tasks")).at(-1);
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
        mode.value = "smart";
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

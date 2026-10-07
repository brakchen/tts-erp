from __future__ import annotations

import subprocess
import textwrap

import pytest

pytestmark = [pytest.mark.domain_publishing]


def test_upload_cancellation_aborts_xhr_and_handles_terminal_and_failed_cancel() -> (
    None
):
    script = (
        "(async () => {\n"
        + textwrap.dedent(
            r"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync("tts_erp_v2/static/js/video-publish.js", "utf8");
        const elements = new Map();
        const created = [];
        const timers = [];
        const posts = [];
        let confirms = 0;
        let uploadConfirmations = 0;
        let draftRequestId = "generated-id";
        let serverCancelled = false;
        let cancelFailure = false;
        const events = [];
        function element() {
          const node = {
            textContent: "", value: "", disabled: false, hidden: false,
            style: {}, dataset: {}, classList: { toggle() {} }, files: [], children: [],
            querySelectorAll() { return []; },
            replaceChildren(...nodes) { this.children = nodes; },
            append(...nodes) { this.children.push(...nodes); },
            addEventListener(type, handler) { this[`on${type}`] = handler; },
            removeEventListener(type, handler) { if (this[`on${type}`] === handler) this[`on${type}`] = null; },
            setAttribute(name, value) { this[name] = value; },
            removeAttribute() {}, close() {}, click() { this.clicked = true; },
            showModal() { this.returnValue = "confirm"; uploadConfirmations += 1; setImmediate(() => this.onclose?.()); },
          };
          created.push(node);
          return node;
        }
        class FakeXHR {
          static latest = null;
          constructor() { this.upload = {}; this.headers = {}; this.abortCalls = 0; FakeXHR.latest = this; }
          open(method, url) { this.method = method; this.url = url; }
          setRequestHeader(name, value) { this.headers[name] = value; }
          send(file) { this.file = file; }
          progress(loaded, total) { if (this.upload.onprogress) this.upload.onprogress({ lengthComputable: true, loaded, total }); }
          abort() { this.abortCalls += 1; events.push("xhr.abort"); if (this.onabort) this.onabort(); }
          finish() { this.status = 200; if (this.onload) this.onload(); }
          fail() { if (this.onerror) this.onerror(); }
        }
        global.location = { pathname: "/v2/pages/video-publish/" };
        global.window = global;
        global.window.addEventListener = () => {};
        global.document = {
          hidden: false,
          getElementById(id) {
            if (!elements.has(id)) { const node = element(); node.id = id; elements.set(id, node); }
            return elements.get(id);
          },
          querySelectorAll() { return []; },
          querySelector() { return element(); },
          createElement() { return element(); },
          addEventListener() {},
        };
        global.crypto = { randomUUID: () => "generated-id" };
        global.URL = { createObjectURL: () => "blob:test", revokeObjectURL() {} };
        global.localStorage = { getItem: () => null, setItem() {} };
        global.navigator = { clipboard: { writeText: async () => {} } };
        global.setTimeout = (callback, delay) => { timers.push(callback); return timers.length; };
        global.clearTimeout = () => {};
        global.XMLHttpRequest = FakeXHR;
        global.fetch = async (url, options = {}) => {
          const method = (options.method || "GET").toUpperCase();
          if (method === "POST") {
            posts.push({ url, body: JSON.parse(options.body || "{}"), headers: options.headers || {} });
            events.push(`post:${url}`);
          }
          let payload;
          if (url.endsWith("/config")) {
            payload = { maxVideoBytes: 100, maxCaptionCharacters: 4000, target: { album: "TEST" }, device: { message: "ready" }, worker: { status: "ready" }, canWrite: true };
          } else if (url.includes("/tasks/current")) {
            payload = { task: null, pollState: { running: false, queued: false } };
          } else if (url.endsWith("/tasks") && method === "GET") {
            payload = { items: [{ taskId: "draft-1", status: serverCancelled ? "cancelled" : "pending", stage: serverCancelled ? "done" : "awaiting_upload", filename: "TEST.mp4", caption: "TEST caption", captionPreview: "TEST caption", clientRequestId: draftRequestId, createdAt: "2026-10-04T00:00:00Z", allowedActions: serverCancelled ? ["view"] : ["view", "continue_upload"] }], pollState: { running: false, queued: false } };
          } else if (url.endsWith("/tasks") && method === "POST") {
            draftRequestId = posts.at(-1).body.clientRequestId;
            payload = { taskId: "draft-1", rowVersion: 1, upload: { url: "https://upload.test/draft-1", headers: {} } };
          } else if (url.endsWith("/cancel")) {
            if (cancelFailure) payload = { detail: "CANCEL_FAILED" };
            else {
              serverCancelled = true;
              payload = { status: "cancelled" };
            }
          } else if (url.includes("confirm-upload")) {
            confirms += 1;
            payload = { status: "pending" };
          } else {
            payload = {};
          }
          return { status: url.endsWith("/cancel") && cancelFailure ? 500 : 200, ok: !(url.endsWith("/cancel") && cancelFailure), headers: { get: () => null }, json: async () => payload };
        };
        vm.runInThisContext(source);
        await new Promise((resolve) => setImmediate(resolve));
        if (timers[0] < 30000 || timers[1] < 30000) throw new Error("awaiting-upload draft slowed as queued work");
        const fileInput = elements.get("publish-video-file");
        const caption = elements.get("publish-caption");
        const submit = elements.get("publish-submit");
        const cancel = elements.get("publish-upload-cancel");
        const file = { name: "TEST.mp4", type: "video/mp4", size: 10 };
        fileInput.onchange({ target: { files: [file] } });
        caption.value = "TEST caption";
        caption.oninput();
        const firstRun = submit.onclick();
        if (uploadConfirmations !== 1) throw new Error("upload did not require pre-upload confirmation");
        await new Promise((resolve) => setImmediate(resolve));
        if (!FakeXHR.latest || cancel.hidden || cancel.disabled) throw new Error("cancel control was not shown for active upload");
        if (!fileInput.disabled || !caption.disabled) throw new Error("upload lifecycle did not lock form controls");
        const firstXhr = FakeXHR.latest;
        firstXhr.progress(5, 10);
        const progress = elements.get("publish-upload-progress");
        if (progress.value !== 50 || progress["aria-valuenow"] !== "50" || progress["aria-valuetext"] !== "50%") throw new Error("upload progressbar did not expose accessible value");
        await cancel.onclick();
        await firstRun;
        firstXhr.finish();
        firstXhr.fail();
        if (firstXhr.abortCalls !== 1) throw new Error("cancel did not abort the active XHR");
        if (firstXhr.headers["X-Requested-With"]) throw new Error("presigned PUT received ttsERP CSRF header");
        if (posts.some((post) => post.headers["X-Requested-With"] !== "tts-erp")) throw new Error("publishing API POST omitted CSRF header");
        const abortIndex = events.indexOf("xhr.abort");
        const cancelIndex = events.findIndex((event) => event.endsWith("/tasks/draft-1/cancel"));
        if (abortIndex < 0 || cancelIndex < 0 || abortIndex >= cancelIndex) throw new Error("cancel API did not follow XHR abort");
        if (posts.find((post) => post.url.endsWith("/cancel")).url !== "/v2/video-publish/tasks/draft-1/cancel") throw new Error("cancel API used the wrong task ID");
        if (confirms !== 0) throw new Error("cancelled upload reached confirm endpoint");
        if (!serverCancelled || !cancel.hidden || !submit.disabled) throw new Error("cancel did not reach terminal cancelled state");
        if (!elements.get("publish-notice").textContent.includes("任务已终止")) throw new Error("terminal cancellation was not visible");
        const taskRows = elements.get("publish-task-list").children;
        if (taskRows.some((row) => row.children.some((cell) => cell.children?.some((node) => node.textContent === "继续上传")))) throw new Error("server-cancelled draft remained resumable");

        serverCancelled = false;
        cancelFailure = true;
        fileInput.onchange({ target: { files: [file] } });
        caption.value = "TEST caption";
        caption.oninput();
        const failedCancelRun = submit.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const failedCancelXhr = FakeXHR.latest;
        await cancel.onclick();
        await failedCancelRun;
        failedCancelXhr.finish();
        failedCancelXhr.fail();
        if (confirms !== 0) throw new Error("failed cancellation reached confirm endpoint");
        if (!elements.get("publish-notice").textContent.includes("取消上传失败")) throw new Error("cancel API failure was not visible");
        if (!cancel.hidden || submit.disabled) throw new Error("cancel API failure did not preserve resumability");
        cancelFailure = false;
        fileInput.onchange({ target: { files: [file] } });
        caption.value = "TEST caption";
        caption.oninput();
        const successfulRun = submit.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const successfulXhr = FakeXHR.latest;
        successfulXhr.finish();
        await successfulRun;
        const confirmPost = posts.find((post) => post.url.endsWith("/confirm-upload"));
        if (!confirmPost || confirmPost.headers["X-Requested-With"] !== "tts-erp") throw new Error("confirm API omitted CSRF header");
        if (successfulXhr.headers["X-Requested-With"]) throw new Error("successful presigned PUT received ttsERP CSRF header");
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

import subprocess
import textwrap


def test_upload_cancellation_aborts_xhr_and_preserves_resumable_request() -> None:
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
        let draftRequestId = "generated-id";
        function element() {
          const node = {
            textContent: "", value: "", disabled: false, hidden: false,
            style: {}, dataset: {}, classList: { toggle() {} }, files: [],
            querySelectorAll() { return []; }, replaceChildren() {}, append() {},
            addEventListener(type, handler) { this[`on${type}`] = handler; },
            removeAttribute() {}, showModal() {}, close() {}, click() { this.clicked = true; },
          };
          created.push(node);
          return node;
        }
        class FakeXHR {
          static latest = null;
          constructor() { this.upload = {}; this.abortCalls = 0; FakeXHR.latest = this; }
          open(method, url) { this.method = method; this.url = url; }
          setRequestHeader() {}
          send(file) { this.file = file; }
          abort() { this.abortCalls += 1; if (this.onabort) this.onabort(); }
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
        global.confirm = () => true;
        global.navigator = { clipboard: { writeText: async () => {} } };
        global.setTimeout = (callback, delay) => { timers.push(callback); return timers.length; };
        global.clearTimeout = () => {};
        global.XMLHttpRequest = FakeXHR;
        global.fetch = async (url, options = {}) => {
          const method = (options.method || "GET").toUpperCase();
          if (method === "POST") posts.push({ url, body: JSON.parse(options.body || "{}") });
          let payload;
          if (url.endsWith("/config")) {
            payload = { maxVideoBytes: 100, maxCaptionCharacters: 4000, target: { album: "TEST" }, device: { message: "ready" }, worker: {} };
          } else if (url.includes("/tasks/current")) {
            payload = { task: null, pollState: { running: false, queued: false } };
          } else if (url.endsWith("/tasks") && method === "GET") {
            payload = { items: [{ taskId: "draft-1", status: "pending", stage: "awaiting_upload", filename: "TEST.mp4", caption: "TEST caption", captionPreview: "TEST caption", clientRequestId: draftRequestId, createdAt: "2026-10-04T00:00:00Z", allowedActions: ["view", "continue_upload"] }], pollState: { running: false, queued: false } };
          } else if (url.endsWith("/tasks") && method === "POST") {
            draftRequestId = posts.at(-1).body.clientRequestId;
            payload = { taskId: "draft-1", upload: { url: "https://upload.test/draft-1", headers: {} } };
          } else if (url.includes("confirm-upload")) {
            confirms += 1;
            payload = { status: "pending" };
          } else {
            payload = {};
          }
          return { status: 200, ok: true, headers: { get: () => null }, json: async () => payload };
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
        await new Promise((resolve) => setImmediate(resolve));
        if (!FakeXHR.latest || cancel.hidden || cancel.disabled) throw new Error("cancel control was not shown for active upload");
        const firstXhr = FakeXHR.latest;
        const firstRequestId = posts.at(-1).body.clientRequestId;
        await cancel.onclick();
        await firstRun;
        firstXhr.finish();
        firstXhr.fail();
        if (firstXhr.abortCalls !== 1) throw new Error("cancel did not abort the active XHR");
        if (confirms !== 0) throw new Error("cancelled upload reached confirm endpoint");
        if (!cancel.hidden || submit.disabled) throw new Error("cancel did not restore form state");
        const continueButtons = created.filter((node) => node.textContent === "继续上传");
        const continueButton = continueButtons.at(-1);
        if (!continueButton) throw new Error("cancelled draft was not resumable");
        await continueButton.onclick();
        fileInput.onchange({ target: { files: [file] } });
        const secondRun = submit.onclick();
        await new Promise((resolve) => setImmediate(resolve));
        const secondXhr = FakeXHR.latest;
        secondXhr.finish();
        await secondRun;
        const resumedPost = posts.filter((post) => post.url.endsWith("/tasks")).at(-1);
        if (resumedPost.body.clientRequestId !== firstRequestId) throw new Error("resume changed clientRequestId");
        if (confirms !== 1) throw new Error("resumed upload did not confirm exactly once");
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

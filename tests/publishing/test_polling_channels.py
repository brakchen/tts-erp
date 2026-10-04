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
        const requests = [];
        const elements = new Map();
        const created = [];
        function element() {
          const node = {
            textContent: "", value: "", disabled: false, hidden: false,
            style: {}, dataset: {}, classList: { toggle() {} },
            querySelectorAll() { return []; }, replaceChildren() {}, append() {},
            addEventListener() {}, removeAttribute() {}, showModal() {}, close() {},
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
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
          },
          querySelectorAll() { return []; },
          querySelector() { return element(); },
          createElement() { return element(); },
          addEventListener() {},
        };
        global.crypto = { randomUUID: () => "00000000-0000-0000-0000-000000000000" };
        global.URL = { createObjectURL: () => "blob:test", revokeObjectURL() {} };
        global.localStorage = { getItem: () => null, setItem() {} };
        global.setTimeout = (callback) => { timers.push(callback); return timers.length; };
        global.clearTimeout = () => {};
        global.fetch = async (url, options) => {
          requests.push({ url, signal: options.signal });
          const payload = url.endsWith("/config")
            ? { maxVideoBytes: 100, maxCaptionCharacters: 4000, target: { album: "TEST" }, device: {}, worker: {} }
            : url.includes("/tasks/current") ? { task: null }
            : url.includes("/tasks/task-1")
              ? { taskId: "task-1", filename: "TEST.mp4", status: "succeeded", caption: "TEST", attempts: [] }
              : { items: [{ taskId: "task-1", status: "succeeded", filename: "TEST.mp4", latestArtemisSessionId: "", createdAt: "2026-10-04T00:00:00Z", allowedActions: [] }] };
          return { status: 200, ok: true, headers: { get: () => null }, json: async () => payload };
        };
        vm.runInThisContext(source);
        await new Promise((resolve) => setImmediate(resolve));
        if (timers.length < 2) throw new Error("current/list timers were not scheduled");
        const listRequest = requests.find((request) => request.url.includes("/tasks?") || request.url.endsWith("/tasks"));
        const currentRequest = requests.find((request) => request.url.includes("/tasks/current"));
        if (!listRequest || !currentRequest || listRequest.signal === currentRequest.signal) {
          throw new Error("current and list did not receive distinct signals");
        }
        await timers[0]();
        if (listRequest.signal.aborted) throw new Error("current refresh aborted list channel");
        const latestCurrent = requests.filter((request) => request.url.includes("/tasks/current")).at(-1);
        await timers[1]();
        if (latestCurrent.signal.aborted) throw new Error("list refresh aborted current channel");
        const view = created.find((node) => node.textContent === "查看");
        if (!view) throw new Error("detail trigger was not rendered");
        await view.onclick();
        const latestList = requests.filter((request) => request.url.endsWith("/tasks")).at(-1);
        const latestDetail = requests.filter((request) => request.url.includes("/tasks/task-1")).at(-1);
        const detailTimer = timers.at(-1);
        await detailTimer();
        if (latestList.signal.aborted || latestCurrent.signal.aborted) throw new Error("detail refresh aborted another channel");
        if (!latestDetail.signal.aborted) throw new Error("detail refresh did not replace its own request");
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

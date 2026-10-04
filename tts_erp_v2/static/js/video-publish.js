/* Native controller: browser talks only to ttsERP endpoints. */
(function () {
  "use strict";
  const root = location.pathname.replace(/\/v2\/pages\/video-publish\/?$/, "");
  const api = root + "/v2/video-publish";
  const $ = (id) => document.getElementById(id);
  const REFRESH_KEY = "tts-erp.video-publish.refresh-preference";
  const state = {
    config: null,
    file: null,
    url: null,
    filter: "",
    timer: null,
    generation: 0,
    upload: null,
    creating: false,
    clientRequestId: null,
    detail: null,
    etags: new Map(),
    refreshController: null,
    refreshFailures: 0,
    currentTask: null,
  };

  function notice(message, error) {
    const n = $("publish-notice");
    n.textContent = message;
    n.style.color = error ? "var(--danger)" : "var(--accent)";
  }

  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = {
      ...(method !== "GET" ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    };
    if (method === "GET" && state.etags.has(path)) {
      headers["If-None-Match"] = state.etags.get(path);
    }
    const response = await fetch(api + path, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    if (response.status === 304) return { notModified: true };
    if (response.status === 401) {
      location.href = root + "/v2/auth/login";
      throw Error("登录已过期");
    }
    if (!response.ok) {
      let detail = {};
      try {
        detail = await response.json();
      } catch (_) {
        // Keep the HTTP status as the useful error when no JSON exists.
      }
      throw Error(detail.detail?.message || detail.detail?.code || detail.detail || "请求失败");
    }
    if (method === "GET") {
      const etag = response.headers.get("ETag");
      if (etag) state.etags.set(path, etag);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function valid() {
    return state.config && state.file && state.file.type === "video/mp4" &&
      state.file.size <= state.config.maxVideoBytes &&
      $("publish-caption").value.length > 0 &&
      $("publish-caption").value.length <= state.config.maxCaptionCharacters;
  }

  function renderForm() {
    const file = state.file;
    $("publish-submit").disabled = !valid() || !!state.upload || state.creating;
    $("publish-caption-count").textContent = `${$("publish-caption").value.length} / ${state.config?.maxCaptionCharacters || 4000}`;
    $("publish-summary-file").textContent = file ? `${file.name} · ${Math.ceil(file.size / 1024 / 1024 * 10) / 10} MB` : "—";
    $("publish-summary-caption").textContent = `${$("publish-caption").value.length} 字`;
    if (file) $("publish-file-name").textContent = file.name;
  }

  function pick(file) {
    if (!file) return;
    state.file = file;
    state.clientRequestId = crypto.randomUUID();
    if (file.type !== "video/mp4" || !file.name.toLowerCase().endsWith(".mp4")) {
      notice("只接受 MP4 视频", true);
      state.file = null;
      renderForm();
      return;
    }
    if (file.size > state.config.maxVideoBytes) {
      notice("视频超过服务端大小上限", true);
      state.file = null;
      renderForm();
      return;
    }
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(file);
    const video = $("publish-video-preview");
    video.src = state.url;
    video.hidden = false;
    renderForm();
  }

  async function init() {
    try {
      state.config = await request("/config");
      $("publish-size-hint").textContent = `MP4 · 最大 ${Math.round(state.config.maxVideoBytes / 1024 / 1024)} MB`;
      $("publish-target-device").textContent = state.config.target.deviceSerialMasked || "未配置";
      $("publish-device-status").textContent = `设备 ${state.config.target.deviceSerialMasked || "—"} · ${state.config.device.message}`;
      const saved = localStorage.getItem(REFRESH_KEY);
      if (saved && ["off", "smart", "5", "15", "30"].includes(saved)) {
        $("publish-refresh-mode").value = saved;
      }
      renderForm();
      await refresh();
      schedule();
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function create() {
    if (!valid() || state.creating) return;
    state.creating=true;
    state.upload = { xhr: null };
    renderForm();
    const caption = $("publish-caption").value;
    const file = state.file;
    state.clientRequestId = state.clientRequestId || crypto.randomUUID();
    try {
      const ticket = await request("/tasks", {
        method: "POST",
        body: JSON.stringify({
          clientRequestId: state.clientRequestId,
          filename: file.name,
          contentType: "video/mp4",
          sizeBytes: file.size,
          caption,
        }),
      });
      if (ticket.upload?.url) {
        const xhr = new XMLHttpRequest();
        state.upload.xhr = xhr;
        $("publish-submit").textContent = "上传中 0%";
        xhr.open("PUT", ticket.upload.url);
        Object.entries(ticket.upload.headers || {}).forEach(([key, value]) => xhr.setRequestHeader(key, value));
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) $("publish-submit").textContent = `上传中 ${Math.round(event.loaded / event.total * 100)}%`;
        };
        await new Promise((resolve, reject) => {
          xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(Error("视频上传失败"));
          xhr.onerror = () => reject(Error("视频上传中断，可以重试上传"));
          xhr.onabort = () => reject(Error("上传已取消"));
          xhr.send(file);
        });
      }
      await request(`/tasks/${ticket.taskId}/confirm-upload`, { method: "POST", body: "{}" });
      notice("已加入发布队列");
      clearForm();
      await refresh();
    } catch (error) {
      notice(error.message, true);
    } finally {
      state.upload = null;
      state.creating = false;
      $("publish-submit").textContent = "上传并加入发布队列";
      renderForm();
    }
  }

  function clearForm() {
    if (state.url) URL.revokeObjectURL(state.url);
    state.file = null;
    state.url = null;
    state.clientRequestId = null;
    $("publish-video-preview").hidden = true;
    $("publish-video-preview").removeAttribute("src");
    $("publish-caption").value = "";
    renderForm();
  }

  function short(id) { return id ? `${id.slice(0, 8)}…${id.slice(-5)}` : "尚未创建"; }

  function renderRail(task) {
    const rail = $("publish-rail");
    rail.querySelectorAll("[data-stage]").forEach((node) => node.classList.toggle("is-current", !!task && node.dataset.stage === task.stage));
    $("publish-rail-summary").textContent = task ? `${task.filename || ""} · ${task.stage}` : "当前无运行任务";
    const id = task?.currentAttempt?.artemisSessionId || task?.latestArtemisSessionId || "";
    $("active-artemis-id").textContent = short(id);
    $("active-artemis-id").title = id;
    const button = document.querySelector("[data-copy-artemis-id]");
    button.dataset.copyArtemisId = id;
    button.disabled = !id;
  }

  function renderTasks(payload) {
    const body = $("publish-task-list");
    body.replaceChildren();
    if (!payload.items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "op-empty";
      cell.textContent = "还没有发布任务。选择一个 MP4 并填写文案开始。";
      row.append(cell);
      body.append(row);
      return;
    }
    payload.items.forEach((task) => {
      const row = document.createElement("tr");
      [task.status, task.filename, short(task.latestArtemisSessionId), new Date(task.createdAt).toLocaleString()].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        cell.dataset.label = ["状态", "视频", "Artemis ID", "时间"][index] || "";
        if (index === 2) cell.className = "artemis-cell";
        row.append(cell);
      });
      const actions = document.createElement("td");
      const view = document.createElement("button");
      view.className = "btn-secondary";
      view.textContent = "查看";
      view.onclick = () => openDetail(task.taskId);
      actions.append(view);
      if (task.allowedActions.includes("retry")) {
        const retry = document.createElement("button");
        retry.className = "btn-secondary";
        retry.textContent = "重试";
        retry.onclick = () => action(task.taskId, "retry");
        actions.append(retry);
      }
      row.append(actions);
      body.append(row);
    });
  }

  async function refresh() {
    const generation = ++state.generation;
    if (state.refreshController) state.refreshController.abort();
    state.refreshController = new AbortController();
    const signal = state.refreshController.signal;
    try {
      const query = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
      const [current, list] = await Promise.all([
        request("/tasks/current", { signal }),
        request(`/tasks${query}`, { signal }),
      ]);
      if (generation !== state.generation) return;
      state.refreshFailures = 0;
      if (!current.notModified) {
        state.currentTask = current.task;
        renderRail(current.task);
      }
      if (!list.notModified) renderTasks(list);
      $("publish-last-refreshed").textContent = `上次刷新 ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      if (error.name === "AbortError") return;
      if (generation !== state.generation) return;
      state.refreshFailures += 1;
      notice("状态刷新失败，正在退避重试", true);
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    const mode = $("publish-refresh-mode").value;
    localStorage.setItem(REFRESH_KEY, mode);
    if (mode === "off") return;
    let seconds = mode === "smart" ? (state.currentTask ? 2 : 15) : Number(mode);
    if (document.hidden) seconds *= 4;
    seconds = Math.min(120, seconds * Math.pow(2, Math.min(state.refreshFailures, 3)));
    state.timer = setTimeout(async () => { await refresh(); schedule(); }, seconds * 1000);
  }

  async function openDetail(id) {
    try {
      state.detail = await request(`/tasks/${id}`);
      const drawer = $("publish-drawer-content");
      drawer.replaceChildren();
      const heading = document.createElement("h2");
      heading.textContent = `${state.detail.filename} · ${state.detail.status}`;
      drawer.append(heading);
      const caption = document.createElement("p");
      caption.textContent = state.detail.caption;
      drawer.append(caption);
      (state.detail.attempts || []).forEach((attempt) => {
        const section = document.createElement("section");
        section.className = "drawer-attempt";
        section.textContent = `第 ${attempt.sequenceNo} 次 ${attempt.kind} · ${attempt.status} · Artemis ID ${attempt.artemisSessionId}`;
        drawer.append(section);
      });
      $("publish-task-drawer").showModal();
    } catch (error) { notice(error.message, true); }
  }

  async function action(id, verb) {
    try { await request(`/tasks/${id}/${verb}`, { method: "POST", body: "{}" }); await refresh(); }
    catch (error) { notice(error.message, true); }
  }

  $("publish-video-file").addEventListener("change", (event) => pick(event.target.files[0]));
  $("publish-caption").addEventListener("input", renderForm);
  $("publish-submit").addEventListener("click", create);
  $("publish-refresh-now").addEventListener("click", async () => { await refresh(); schedule(); });
  $("publish-refresh-mode").addEventListener("change", schedule);
  $("publish-drawer-close").addEventListener("click", () => $("publish-task-drawer").close());
  document.querySelectorAll("[data-task-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.taskFilter;
    document.querySelectorAll("[data-task-filter]").forEach((node) => node.classList.toggle("is-active", node === button));
    refresh();
  }));
  document.querySelector("[data-copy-artemis-id]").addEventListener("click", (event) => {
    const id = event.currentTarget.dataset.copyArtemisId;
    if (id) navigator.clipboard.writeText(id).then(() => notice("Artemis ID 已复制"));
  });
  $("publish-video-drop").addEventListener("dragover", (event) => { event.preventDefault(); $("publish-video-drop").classList.add("is-dragging"); });
  $("publish-video-drop").addEventListener("dragleave", () => $("publish-video-drop").classList.remove("is-dragging"));
  $("publish-video-drop").addEventListener("drop", (event) => { event.preventDefault(); pick(event.dataTransfer.files[0]); $("publish-video-drop").classList.remove("is-dragging"); });
  window.addEventListener("beforeunload", (event) => { if (state.upload) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); schedule(); } else schedule(); });
  init();
})();

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
    currentTimer: null,
    listTimer: null,
    detailTimer: null,
    currentChannel: { controller: null, generation: 0, failures: 0 },
    listChannel: { controller: null, generation: 0, failures: 0, items: [] },
    pollState: { running: false, queued: false },
    detailChannel: { controller: null, generation: 0, failures: 0 },
    upload: null,
    creating: false,
    clientRequestId: null,
    resumeTask: null,
    detail: null,
    detailTaskId: null,
    etags: new Map(),
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
      } catch {
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
    if (!state.resumeTask) state.clientRequestId = crypto.randomUUID();
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
      $("publish-target-album").textContent = state.config.target.album || "未配置";
      $("publish-device-status").textContent = `设备 ${state.config.target.deviceSerialMasked || "—"} · ${state.config.device.message}`;
      const saved = localStorage.getItem(REFRESH_KEY);
      if (saved && ["off", "smart", "2", "5", "10", "30"].includes(saved)) {
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
    state.resumeTask = null;
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

  const ACTION_LABELS = {
    view: "查看",
    continue_upload: "继续上传",
    cancel: "取消",
    retry: "重试",
    verify: "核验",
    retry_cleanup: "重试清理",
    copy_artemis_id: "复制 Artemis ID",
  };
  const CONFIRM_ACTIONS = new Set(["cancel", "retry", "verify", "retry_cleanup"]);

  function resumeUpload(task) {
    state.resumeTask = task;
    state.clientRequestId = task.clientRequestId;
    $("publish-caption").value = task.caption || task.captionPreview || "";
    renderForm();
    notice("请选择原视频以继续上传");
    $("publish-video-file").click();
  }

  async function runTaskAction(task, actionName) {
    if (CONFIRM_ACTIONS.has(actionName) && !window.confirm(`确认${ACTION_LABELS[actionName] || actionName}？`)) return;
    try {
      if (actionName === "view") return openDetail(task.taskId);
      if (actionName === "copy_artemis_id") {
        const id = task.latestArtemisSessionId;
        if (id) await navigator.clipboard.writeText(id);
        return;
      }
      if (actionName === "continue_upload") {
        return resumeUpload(task);
      }
      const endpoint = actionName === "retry_cleanup"
        ? `/tasks/${task.taskId}/cleanup/retry`
        : `/tasks/${task.taskId}/${actionName.replaceAll("_", "-")}`;
      await request(endpoint, { method: "POST", body: "{}" });
      await refresh();
    } catch (error) {
      notice(error.message, true);
    }
  }

  function renderTasks(payload) {
    state.listChannel.items = payload.items || [];
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
      (task.allowedActions || []).forEach((actionName) => {
        const button = document.createElement("button");
        button.className = "btn-secondary";
        button.textContent = ACTION_LABELS[actionName] || actionName;
        button.onclick = () => runTaskAction(task, actionName);
        actions.append(button);
      });
      row.append(actions);
      body.append(row);
    });
  }

  async function refreshCurrent() {
    const channel = state.currentChannel;
    const generation = ++channel.generation;
    if (channel.controller) channel.controller.abort();
    channel.controller = new AbortController();
    try {
      const current = await request("/tasks/current", { signal: channel.controller.signal });
      if (generation !== channel.generation) return;
      channel.failures = 0;
      if (!current.notModified) {
        state.currentTask = current.task;
        state.pollState = current.pollState || { running: Boolean(current.task), queued: false };
        renderRail(current.task);
      }
      $("publish-last-refreshed").textContent = `上次刷新 ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return;
      channel.failures += 1;
      notice("当前任务刷新失败，正在退避重试", true);
    }
  }

  async function refreshList() {
    const channel = state.listChannel;
    const generation = ++channel.generation;
    if (channel.controller) channel.controller.abort();
    channel.controller = new AbortController();
    try {
      const query = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
      const list = await request(`/tasks${query}`, { signal: channel.controller.signal });
      if (generation !== channel.generation) return;
      channel.failures = 0;
      if (!list.notModified) {
        state.pollState = list.pollState || { running: false, queued: false };
        renderTasks(list);
      }
      $("publish-last-refreshed").textContent = `上次刷新 ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return;
      channel.failures += 1;
      notice("任务列表刷新失败，正在退避重试", true);
    }
  }

  async function refreshDetail() {
    const taskId = state.detailTaskId;
    if (!taskId) return;
    const channel = state.detailChannel;
    const generation = ++channel.generation;
    if (channel.controller) channel.controller.abort();
    channel.controller = new AbortController();
    try {
      const detail = await request(`/tasks/${taskId}`, { signal: channel.controller.signal });
      if (generation !== channel.generation || taskId !== state.detailTaskId) return;
      channel.failures = 0;
      if (!detail.notModified) {
        state.detail = detail;
        renderDetail(detail);
      }
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return;
      channel.failures += 1;
      notice("任务详情刷新失败，正在退避重试", true);
    }
  }

  async function refresh(options = {}) {
    const jobs = [];
    if (options.current !== false) jobs.push(refreshCurrent());
    if (options.list !== false) jobs.push(refreshList());
    if (options.detail !== false) jobs.push(refreshDetail());
    await Promise.all(jobs);
  }

  function backoffSeconds(seconds, failures) {
    if (document.hidden) seconds = Math.max(30, seconds * 4);
    return Math.min(120, seconds * Math.pow(2, Math.min(failures, 3)));
  }

  function renderRefreshState(mode) {
    const indicator = $("publish-refresh-status");
    const paused = mode === "off";
    indicator.textContent = paused ? "自动刷新已暂停" : "自动刷新已启用";
    indicator.hidden = !paused;
  }

  function schedule() {
    clearTimeout(state.currentTimer);
    clearTimeout(state.listTimer);
    clearTimeout(state.detailTimer);
    const mode = $("publish-refresh-mode").value;
    localStorage.setItem(REFRESH_KEY, mode);
    renderRefreshState(mode);
    if (mode === "off") return;
    scheduleCurrent(mode);
    scheduleList(mode);
    scheduleDetail(mode);
  }

  function sharedPollState() {
    return state.pollState;
  }

  function scheduleCurrent(mode) {
    const shared = sharedPollState();
    const base = mode === "smart"
      ? shared.running ? 2 : shared.queued ? 8 : 30
      : Number(mode);
    const seconds = backoffSeconds(base, state.currentChannel.failures);
    state.currentTimer = setTimeout(async () => {
      await refreshCurrent();
      scheduleCurrent(mode);
    }, seconds * 1000);
  }

  function scheduleList(mode) {
    const shared = sharedPollState();
    const base = mode === "smart"
      ? shared.running ? 5 : shared.queued ? 8 : 30
      : Number(mode);
    const seconds = backoffSeconds(base, state.listChannel.failures);
    state.listTimer = setTimeout(async () => {
      await refreshList();
      scheduleList(mode);
    }, seconds * 1000);
  }

  function scheduleDetail(mode) {
    if (!state.detailTaskId) return;
    const base = mode === "smart" ? (state.currentTask ? 5 : 30) : Number(mode);
    const seconds = backoffSeconds(base, state.detailChannel.failures);
    state.detailTimer = setTimeout(async () => {
      await refreshDetail();
      scheduleDetail(mode);
    }, seconds * 1000);
  }

  function renderDetail(detail) {
      const drawer = $("publish-drawer-content");
      drawer.replaceChildren();
      const heading = document.createElement("h2");
      heading.textContent = `${detail.filename} · ${detail.status}`;
      drawer.append(heading);
      const caption = document.createElement("p");
      caption.textContent = detail.caption;
      drawer.append(caption);
      (detail.attempts || []).forEach((attempt) => {
        const section = document.createElement("section");
        section.className = "drawer-attempt";
        section.textContent = `第 ${attempt.sequenceNo} 次 ${attempt.kind} · ${attempt.status} · Artemis ID ${attempt.artemisSessionId}`;
        drawer.append(section);
      });
  }

  async function openDetail(id) {
    state.detailTaskId = id;
    try {
      await refreshDetail();
      if (state.detail?.taskId === id) {
        $("publish-task-drawer").showModal();
        schedule();
      }
    } catch (error) { notice(error.message, true); }
  }

  $("publish-video-file").addEventListener("change", (event) => pick(event.target.files[0]));
  $("publish-caption").addEventListener("input", renderForm);
  $("publish-submit").addEventListener("click", create);
  $("publish-refresh-now").addEventListener("click", async () => { await refresh(); schedule(); });
  $("publish-refresh-mode").addEventListener("change", schedule);
  $("publish-drawer-close").addEventListener("click", () => {
    $("publish-task-drawer").close();
    state.detailTaskId = null;
    state.detail = null;
    clearTimeout(state.detailTimer);
  });
  document.querySelectorAll("[data-task-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.taskFilter;
    document.querySelectorAll("[data-task-filter]").forEach((node) => node.classList.toggle("is-active", node === button));
    refresh({ current: false });
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

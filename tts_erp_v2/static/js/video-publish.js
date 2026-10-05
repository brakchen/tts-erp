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
    listChannel: { controller: null, generation: 0, failures: 0, items: [], nextCursor: null },
    pollState: { running: false, cleaning: false, queued: false },
    detailChannel: { controller: null, generation: 0, failures: 0 },
    upload: null,
    uploadConflictActions: [],
    creating: false,
    clientRequestId: null,
    resumeTask: null,
    detail: null,
    detailTaskId: null,
    detailOpener: null,
    etags: new Map(),
    currentTask: null,
    destroyed: false,
  };

  function notice(message, error) {
    const n = $("publish-notice");
    n.textContent = String(message || "").slice(0, 500);
    n.style.color = error ? "var(--danger)" : "var(--accent)";
    n.setAttribute("role", error ? "alert" : "status");
    n.setAttribute("aria-live", error ? "assertive" : "polite");
  }

  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = {
      ...(method !== "GET" ? {
        "Content-Type": "application/json",
        "X-Requested-With": "tts-erp",
      } : {}),
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
      const error = Error(detail.detail?.message || detail.detail?.code || detail.detail || "请求失败");
      error.status = response.status;
      error.code = detail.detail?.code;
      error.retryable = detail.detail?.retryable;
      error.requestId = detail.detail?.requestId;
      error.rowVersion = detail.detail?.rowVersion;
      error.allowedActions = detail.detail?.allowedActions;
      throw error;
    }
    if (method === "GET") {
      const etag = response.headers.get("ETag");
      if (etag) state.etags.set(path, etag);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function codePointLength(value) {
    return Array.from(value).length;
  }

  function valid() {
    const captionLength = codePointLength($("publish-caption").value);
    return state.config && state.file && state.file.type === "video/mp4" &&
      state.file.size <= state.config.maxVideoBytes &&
      captionLength > 0 && captionLength <= state.config.maxCaptionCharacters;
  }

  function renderForm() {
    const file = state.file;
    const activeUpload = Boolean(state.upload?.xhr);
    const lifecycleBusy = Boolean(state.upload) || state.creating;
    $("publish-video-file").disabled = lifecycleBusy;
    $("publish-caption").disabled = lifecycleBusy;
    const writeReady = Boolean(state.config?.canWrite) && state.config?.worker?.status === "ready";
    $("publish-submit").disabled = !valid() || !writeReady || !!state.upload || state.creating;
    const writeStatus = $("publish-write-status");
    if (writeStatus) writeStatus.textContent = writeReady ? "" : state.config?.canWrite === false ? "发布设备未配置，当前为只读模式。" : "发布服务暂不可用，请稍后再试。";
    const progress = $("publish-upload-progress");
    progress.hidden = !activeUpload;
    progress.value = state.upload?.progress || 0;
    progress.setAttribute("aria-valuenow", String(progress.value));
    progress.setAttribute("aria-valuetext", `${progress.value}%`);
    const cancel = $("publish-upload-cancel");
    cancel.hidden = !activeUpload;
    cancel.disabled = !activeUpload;
    const captionLength = codePointLength($("publish-caption").value);
    const overLimit = captionLength > (state.config?.maxCaptionCharacters || 4000);
    $("publish-caption-count").textContent = `${captionLength} / ${state.config?.maxCaptionCharacters || 4000}`;
    $("publish-caption-count").classList.toggle("is-danger", overLimit);
    $("publish-summary-file").textContent = file ? `${file.name} · ${Math.ceil(file.size / 1024 / 1024 * 10) / 10} MB` : "—";
    $("publish-summary-caption").textContent = `${captionLength} 字`;
    if (file) $("publish-file-name").textContent = file.name;
  }

  function pick(file) {
    if (state.upload || state.creating) return;
    if (!file) return;
    if (state.resumeTask && (
      file.name !== state.resumeTask.filename || file.size !== state.resumeTask.sizeBytes
    )) {
      notice(`请选择原文件 ${state.resumeTask.filename}（${state.resumeTask.sizeBytes} 字节）`, true);
      state.file = null;
      renderForm();
      return;
    }
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
    video.onloadedmetadata = () => {
      const duration = Number.isFinite(video.duration)
        ? `${Math.round(video.duration)} 秒`
        : "时长未知";
      $("publish-preview-metadata").textContent = `${duration} · ${video.videoWidth || "?"}×${video.videoHeight || "?"}`;
    };
    renderForm();
  }

  async function init() {
    try {
      state.config = await request("/config");
      $("publish-caption").dataset.maxCodePoints = String(state.config.maxCaptionCharacters);
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

  function confirmPublish(file, caption) {
    const dialog = $("publish-confirm-dialog");
    if (!dialog?.showModal) {
      notice("当前浏览器不支持安全确认对话框，无法提交。", true);
      return Promise.resolve(false);
    }
    $("publish-confirm-file").textContent = `${file.name} · ${Math.ceil(file.size / 1024 / 1024 * 10) / 10} MB`;
    $("publish-confirm-caption").textContent = caption;
    const preview = $("publish-confirm-preview");
    preview.src = state.url || "";
    preview.hidden = !state.url;
    $("publish-confirm-device").textContent = state.config.target.deviceSerialMasked || "—";
    $("publish-confirm-album").textContent = state.config.target.album || "—";
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    });
  }

  async function create() {
    if (!valid() || state.creating) return;
    const caption = $("publish-caption").value;
    const file = state.file;
    if (!await confirmPublish(file, caption)) return;
    state.creating=true;
    const upload = { xhr: null, cancelled: false };
    state.upload = upload;
    renderForm();
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
      upload.taskId = ticket.taskId;
      upload.rowVersion = ticket.rowVersion;
      if (ticket.upload?.url) {
        const xhr = new XMLHttpRequest();
        upload.xhr = xhr;
        $("publish-submit").textContent = "上传中 0%";
        renderForm();
        xhr.open("PUT", ticket.upload.url);
        Object.entries(ticket.upload.headers || {}).forEach(([key, value]) => xhr.setRequestHeader(key, value));
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            upload.progress = Math.round(event.loaded / event.total * 100);
            $("publish-upload-progress").value = upload.progress;
            $("publish-upload-progress").setAttribute("aria-valuenow", String(upload.progress));
            $("publish-upload-progress").setAttribute("aria-valuetext", `${upload.progress}%`);
            $("publish-submit").textContent = `上传中 ${upload.progress}%`;
          }
        };
        await new Promise((resolve, reject) => {
          xhr.onload = () => {
            if (upload.cancelled) return reject(Error("上传已取消"));
            return xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(Error("视频上传失败"));
          };
          xhr.onerror = () => reject(Error("视频上传中断，可以重试上传"));
          xhr.onabort = () => reject(Error("上传已取消"));
          xhr.send(file);
        });
        upload.xhr = null;
        renderForm();
        if (upload.cancelled || state.upload !== upload) return;
      }
      if (upload.cancelled || state.upload !== upload) return;
      await request(`/tasks/${ticket.taskId}/confirm-upload`, {
        method: "POST",
        body: JSON.stringify({ rowVersion: upload.rowVersion }),
      });
      if (upload.cancelled || state.upload !== upload) return;
      notice("已加入发布队列");
      clearForm();
      await refresh();
    } catch (error) {
      if (upload.cancelled) {
        try {
          if (upload.cancelPromise) await upload.cancelPromise;
          if (upload.serverCancelled) {
            clearForm();
            notice("上传已取消，任务已终止");
          } else {
            notice("取消上传失败，任务仍可继续上传", true);
          }
          await refresh();
        } catch (cancelError) {
          notice(`取消上传失败：${cancelError.message}`, true);
          try {
            await refresh();
          } catch (refreshError) {
            notice(refreshError.message, true);
          }
        }
      } else if (error.status === 409) {
        if (Array.isArray(error.allowedActions)) {
          state.uploadConflictActions = error.allowedActions;
        }
        notice("上传任务状态已变化，已刷新服务端允许动作", true);
        await refresh();
      } else {
        notice(error.message, true);
      }
    } finally {
      if (state.upload === upload) state.upload = null;
      state.creating = false;
      $("publish-submit").textContent = "上传并加入发布队列";
      renderForm();
    }
  }

  async function cancelUpload() {
    const upload = state.upload;
    const xhr = upload?.xhr;
    if (!upload || !xhr || upload.cancelled) return;
    upload.cancelled = true;
    upload.cancelPromise = (async () => {
      xhr.abort();
      upload.xhr = null;
      renderForm();
      if (!upload.taskId) throw Error("上传任务尚未创建");
      await request(`/tasks/${upload.taskId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ rowVersion: upload.rowVersion }),
      });
      upload.serverCancelled = true;
    })();
    return upload.cancelPromise.catch((error) => {
      notice(`取消上传失败：${error.message}`, true);
    });
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

  function renderRail(task) {
    const rail = $("publish-rail");
    rail.querySelectorAll("[data-stage]").forEach((node) => {
      const current = Boolean(task && node.dataset.stage === (task.operationalStage || task.stage));
      node.classList.toggle("is-current", current);
      if (current) node.setAttribute("aria-current", "step");
      else node.removeAttribute("aria-current");
    });
    $("publish-rail-summary").textContent = task ? `${task.filename || ""} · ${task.operationalStage || task.stage}` : "当前无运行任务";
    const meta = $("publish-rail-meta");
    const detailButton = $("publish-rail-detail");
    if (task) {
      const attempt = task.currentAttempt;
      const startedAt = task.operationalStageStartedAt || task.stageStartedAt || attempt?.startedAt || task.startedAt;
      const attemptCount = attempt?.kind === "verify" ? task.verifyAttemptCount : task.publishAttemptCount;
      const elapsed = startedAt ? `${Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000))} 秒` : "—";
      meta.textContent = `任务 ${task.taskId} · ${task.operationalStage || task.stage} · ${attempt?.kind || "—"} #${attemptCount || 0} · 开始 ${startedAt || "—"} · 已耗时 ${elapsed}`;
      detailButton.hidden = false;
      detailButton.onclick = () => {
        state.detailOpener = detailButton;
        return openDetail(task.taskId);
      };
    } else {
      meta.textContent = "";
      detailButton.hidden = true;
    }
    const id = task?.currentAttempt?.artemisSessionId || task?.latestArtemisSessionId || "";
    $("active-artemis-id").textContent = id || "尚未创建";
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
    replace_upload: "重新上传",
    copy_artemis_id: "复制 Artemis ID",
  };
  const CONFIRM_ACTIONS = new Set(["cancel", "retry", "verify", "retry_cleanup", "replace_upload"]);

  function confirmationMessage(task, actionName) {
    const attempt = task.currentAttempt || {};
    const attemptId = attempt.artemisSessionId || task.latestArtemisSessionId || "尚未创建";
    const failure = `${task.lastErrorCode || "无错误码"} / ${task.lastErrorMessage || "无错误详情"}`;
    const budget = `${task.attemptCount ?? task.publishAttemptCount ?? 0}/${state.config?.maxPublishAttempts || 3}`;
    return {
      cancel: `取消任务 ${task.taskId}（阶段 ${task.stage}）会保留审计记录，并清理已生成的对象；确认取消？`,
      retry: `重试任务 ${task.taskId}\n前次 Artemis ID：${attemptId}\n失败阶段：${task.stage}\n错误：${failure}\n发布预算：${budget}\n重试会复用已上传对象，且不会自动确认模糊结果。确认重试？`,
      verify: `只读核验任务 ${task.taskId} 的发布 attempt ${attemptId}；不会进入上传页，不会再次点击发布。确认开始核验？`,
      retry_cleanup: `仅重试任务 ${task.taskId} 的失败资源：${(task.cleanupRetryableResources || []).join("、") || "无"}；不会改变业务发布结果。确认重试清理？`,
      replace_upload: `任务 ${task.taskId} 的原对象已删除；当前阶段 ${task.stage}，错误 ${failure}，预算 ${budget}。重新上传会创建新的发布输入。确认重新上传？`,
    }[actionName] || `确认${ACTION_LABELS[actionName] || actionName}？`;
  }

  function confirmTaskAction(task, actionName, trigger) {
    const dialog = $("publish-action-dialog");
    if (!dialog?.showModal) {
      notice("当前浏览器不支持安全确认对话框，操作已取消。", true);
      return Promise.resolve(false);
    }
    $("publish-action-title").textContent = `${ACTION_LABELS[actionName] || actionName}确认`;
    $("publish-action-evidence").textContent = confirmationMessage(task, actionName);
    $("publish-action-confirm").textContent = `确认${ACTION_LABELS[actionName] || "操作"}`;
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => {
        trigger?.focus?.();
        resolve(dialog.returnValue === "confirm");
      }, { once: true });
    });
  }

  function resumeUpload(task) {
    if (task.status === "cancelled" || !(task.allowedActions || []).includes("continue_upload")) {
      notice("任务已取消，不能继续上传", true);
      return;
    }
    state.resumeTask = task;
    state.clientRequestId = task.clientRequestId;
    $("publish-caption").value = task.caption || task.captionPreview || "";
    renderForm();
    notice("请选择原视频以继续上传");
    $("publish-video-file").click();
  }

  async function runTaskAction(task, actionName, trigger = null) {
    if (CONFIRM_ACTIONS.has(actionName)) {
      const confirmed = await confirmTaskAction(task, actionName, trigger);
      if (!confirmed) return;
    }
    try {
      if (actionName === "view") return openDetail(task.taskId);
      if (actionName === "copy_artemis_id") {
        const id = task.latestArtemisSessionId;
        if (id) await copyText(id);
        return;
      }
      if (actionName === "continue_upload") {
        return resumeUpload(task);
      }
      const endpoint = actionName === "retry_cleanup"
        ? `/tasks/${task.taskId}/cleanup/retry`
        : actionName === "replace_upload"
          ? `/tasks/${task.taskId}/replace-upload`
        : `/tasks/${task.taskId}/${actionName.replaceAll("_", "-")}`;
      if (actionName === "replace_upload") {
        const replaced = await request(endpoint, {
          method: "POST",
          body: JSON.stringify({ rowVersion: task.rowVersion }),
        });
        return resumeUpload(replaced);
      }
      const body = { rowVersion: task.rowVersion };
      if (actionName === "retry_cleanup") {
        body.resources = task.cleanupRetryableResources || Object.entries(task.cleanup || {})
          .filter(([, resource]) => resource.status === "failed")
          .map(([name]) => name);
      }
      await request(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      });
      await refresh();
    } catch (error) {
      if (error.status === 409) {
        if (Array.isArray(error.allowedActions)) {
          task.allowedActions = error.allowedActions;
          if (Number.isInteger(error.rowVersion)) task.rowVersion = error.rowVersion;
          if (state.detail?.taskId === task.taskId) {
            state.detail.allowedActions = error.allowedActions;
            state.detail.rowVersion = task.rowVersion;
            renderDetail(state.detail);
          }
        }
        notice("任务状态已更新，已按服务端允许动作刷新", true);
        await refresh();
      } else {
        notice(error.message, true);
      }
    }
  }

  function renderTasks(payload, { append = false } = {}) {
    const incoming = payload.items || [];
    state.listChannel.items = append
      ? [...state.listChannel.items, ...incoming]
      : incoming;
    state.listChannel.nextCursor = payload.nextCursor || null;
    const loadMore = $("publish-load-more");
    loadMore.hidden = !state.listChannel.nextCursor;
    loadMore.disabled = false;
    const body = $("publish-task-list");
    body.replaceChildren();
    if (!state.listChannel.items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.className = "op-empty";
      cell.textContent = "还没有发布任务。选择一个 MP4 并填写文案开始。";
      row.append(cell);
      body.append(row);
      return;
    }
    state.listChannel.items.forEach((task) => {
      const row = document.createElement("tr");
      [
        task.status,
        task.stage,
        task.captionPreview || "—",
        task.filename,
        `${task.publishAttemptCount || 0} / ${task.verifyAttemptCount || 0}`,
        task.createdBy || "—",
        task.latestArtemisSessionId || "—",
        new Date(task.createdAt).toLocaleString(),
      ].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value ?? "—";
        cell.dataset.label = ["状态", "阶段", "文案", "视频", "发布/核验", "创建者", "Artemis ID", "时间"][index] || "";
        if (index === 6) cell.className = "artemis-cell";
        row.append(cell);
      });
      const actions = document.createElement("td");
      (task.allowedActions || []).forEach((actionName) => {
        const button = document.createElement("button");
        button.className = "btn-secondary";
        button.textContent = ACTION_LABELS[actionName] || actionName;
        button.onclick = () => {
        if (actionName === "view") state.detailOpener = button;
        return runTaskAction(task, actionName, button);
      };
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
        state.pollState = current.pollState || { running: Boolean(current.task), cleaning: false, queued: false };
        renderRail(current.task);
      }
      $("publish-last-refreshed").textContent = `上次刷新 ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return;
      channel.failures += 1;
      notice("当前任务刷新失败，正在退避重试", true);
    }
  }

  async function refreshList({ append = false } = {}) {
    const channel = state.listChannel;
    const generation = ++channel.generation;
    if (channel.controller) channel.controller.abort();
    channel.controller = new AbortController();
    try {
      const params = new URLSearchParams();
      if (state.filter) params.set("status", state.filter);
      if (append && channel.nextCursor) params.set("cursor", channel.nextCursor);
      const query = params.toString() ? `?${params}` : "";
      const list = await request(`/tasks${query}`, { signal: channel.controller.signal });
      if (generation !== channel.generation) return;
      channel.failures = 0;
      if (!list.notModified) {
        state.pollState = list.pollState || { running: false, cleaning: false, queued: false };
        renderTasks(list, { append });
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
      let detail;
      try {
        detail = await request(`/tasks/${taskId}?includeDiagnostics=true`, { signal: channel.controller.signal });
      } catch (error) {
        if (error.status !== 403) throw error;
        detail = await request(`/tasks/${taskId}`, { signal: channel.controller.signal });
      }
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
    if (failures > 0) seconds = [5, 10, 30, 60][Math.min(failures - 1, 3)];
    if (document.hidden) seconds = Math.max(30, seconds);
    return seconds;
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
    if (mode === "off" || state.destroyed) return;
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
      ? shared.running || shared.cleaning ? 2 : shared.queued ? 8 : 30
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
      ? shared.running || shared.cleaning ? 5 : shared.queued ? 8 : 30
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
    const metadata = document.createElement("dl");
    const fields = [
      ["任务", `${detail.taskId} · ${detail.stage}`],
      ["目标", `${detail.target?.appName || "TikTok"} · ${detail.target?.deviceSerialMasked || "—"} · ${detail.target?.album || "—"}`],
      ["对象", `${detail.object?.bucket || "—"}/${detail.object?.key || "—"} · ETag ${detail.object?.etag || "—"}`],
      ["文案", detail.caption || detail.captionPreview || ""],
    ];
    fields.forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = value;
      metadata.append(term, description);
    });
    drawer.append(metadata);
    (detail.attempts || []).forEach((attempt) => {
      const section = document.createElement("section");
      section.className = "drawer-attempt";
      const summary = document.createElement("div");
      summary.textContent = `第 ${attempt.sequenceNo} 次 ${attempt.kind} · ${attempt.status} · ${attempt.startedAt || "—"} → ${attempt.finishedAt || "—"}`;
      const diagnostic = document.createElement("p");
      diagnostic.textContent = `错误：${attempt.error || "—"} · 重试分类：${attempt.retryClassification || "—"} · 可重试：${attempt.retrySafe == null ? "—" : attempt.retrySafe ? "是" : "否"}`;
      const id = document.createElement("code");
      id.textContent = attempt.artemisSessionId;
      const copy = document.createElement("button");
      copy.className = "btn-secondary drawer-action";
      copy.textContent = "复制 Artemis ID";
      copy.onclick = () => copyText(attempt.artemisSessionId);
      section.append(summary, diagnostic, id, copy);
      if (attempt.promptSnapshot || attempt.artemisOutput) {
        const diagnostics = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "管理员诊断";
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify({ promptSnapshot: attempt.promptSnapshot, artemisOutput: attempt.artemisOutput }, null, 2);
        diagnostics.append(summary, pre);
        section.append(diagnostics);
      }
      drawer.append(section);
    });
    if (detail.cleanup) {
      const cleanup = document.createElement("section");
      cleanup.className = "drawer-cleanup";
      cleanup.textContent = `清理：设备 ${detail.cleanup.device.status}（${detail.cleanup.device.error || "—"}） · spool ${detail.cleanup.spool.status}（${detail.cleanup.spool.error || "—"}） · 对象 ${detail.cleanup.object.status}（${detail.cleanup.object.error || "—"}）`;
      drawer.append(cleanup);
    }
    const actions = $("publish-drawer-actions");
    actions.replaceChildren();
    (detail.allowedActions || []).filter((action) => action !== "view" && action !== "copy_artemis_id").forEach((action) => {
      const button = document.createElement("button");
      button.className = "btn-secondary drawer-action";
      button.textContent = ACTION_LABELS[action] || action;
      button.onclick = () => runTaskAction(detail, action, button);
      actions.append(button);
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
  $("publish-caption").addEventListener("input", () => {
    if (!state.upload && !state.creating) renderForm();
  });
  $("publish-submit").addEventListener("click", create);
  $("publish-upload-cancel").addEventListener("click", cancelUpload);
  $("publish-refresh-now").addEventListener("click", async () => { await refresh(); schedule(); });
  $("publish-refresh-mode").addEventListener("change", schedule);
  $("publish-drawer-close").addEventListener("click", () => {
    $("publish-task-drawer").close();
    const opener = state.detailOpener;
    state.detailOpener = null;
    state.detailTaskId = null;
    state.detail = null;
    clearTimeout(state.detailTimer);
    opener?.focus?.();
  });
  document.querySelectorAll("[data-task-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.taskFilter;
    state.listChannel.nextCursor = null;
    document.querySelectorAll("[data-task-filter]").forEach((node) => node.classList.toggle("is-active", node === button));
    refresh({ current: false });
  }));
  $("publish-load-more").addEventListener("click", async (event) => {
    if (!state.listChannel.nextCursor) return;
    event.currentTarget.disabled = true;
    await refreshList({ append: true });
  });
  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      notice("Artemis ID 已复制");
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = value;
      fallback.setAttribute("readonly", "");
      document.body.append(fallback);
      fallback.select();
      const copied = document.execCommand("copy");
      fallback.remove();
      notice(copied ? "Artemis ID 已复制" : "复制失败，请手动选择 Artemis ID", !copied);
    }
  }

  document.querySelector("[data-copy-artemis-id]").addEventListener("click", (event) => {
    const id = event.currentTarget.dataset.copyArtemisId;
    if (id) copyText(id);
  });
  $("publish-video-drop").addEventListener("dragover", (event) => { event.preventDefault(); $("publish-video-drop").classList.add("is-dragging"); });
  $("publish-video-drop").addEventListener("dragleave", () => $("publish-video-drop").classList.remove("is-dragging"));
  $("publish-video-drop").addEventListener("drop", (event) => { event.preventDefault(); pick(event.dataTransfer.files[0]); $("publish-video-drop").classList.remove("is-dragging"); });
  function destroy() {
    state.destroyed = true;
    clearTimeout(state.currentTimer);
    clearTimeout(state.listTimer);
    clearTimeout(state.detailTimer);
    [state.currentChannel, state.listChannel, state.detailChannel].forEach((channel) => channel.controller?.abort());
    state.upload?.xhr?.abort();
    if (state.url) URL.revokeObjectURL(state.url);
  }

  window.addEventListener("beforeunload", (event) => { if (state.upload) { event.preventDefault(); event.returnValue = ""; } });
  window.addEventListener("pagehide", destroy);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); schedule(); } else schedule(); });
  init();
})();

/* Native controller: browser talks only to ttsERP endpoints. */
(function () {
  "use strict";
  const root = location.pathname.replace(/\/v2\/pages\/video-publish\/?$/, "");
  const api = root + "/v2/video-publish";
  const $ = (id) => document.getElementById(id);
  const REFRESH_KEY = "tts-erp.video-publish.refresh-preference";
  const state = {
    config: null,
    devices: [],
    deviceSerial: "",
    file: null,
    url: null,
    filter: "",
    currentTimer: null,
    listTimer: null,
    detailTimer: null,
    currentChannel: { controller: null, generation: 0, failures: 0 },
    // renderedPath: which /tasks path produced the rows currently on screen. A 304
    // only proves *that* path's payload is unchanged, so it may not stand in for
    // "the screen is current" when it differs. See refreshList.
    // pageSize: how many of those rows came from page 1; rows beyond that were
    // appended by 加载更多 and must survive a timed refresh. See renderTasks.
    listChannel: { controller: null, generation: 0, failures: 0, items: [], nextCursor: null, renderedPath: null, pageSize: 0 },
    pollState: { running: false, cleaning: false, queued: false },
    detailChannel: { controller: null, generation: 0, failures: 0 },
    // Detail bodies we actually received, keyed by taskId. A 304 carries no body,
    // so this is what lets an explicit reopen render the right task (P1-3/D1).
    detailCache: new Map(),
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
    mutationBusy: new Set(),
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
    // conditional: whether the response may be cached in state.etags at all.
    // reuseEtag: whether we may send If-None-Match from that cache. Keeping the
    // two apart is what lets a request *store* an ETag while still refusing to
    // *reuse* a 304 that was obtained from a different rendered source (P1-2).
    const conditional = options.conditional !== false;
    const reuseEtag = options.reuseEtag !== false;
    const fetchOptions = { ...options };
    delete fetchOptions.conditional;
    delete fetchOptions.reuseEtag;
    const headers = {
      ...(method !== "GET" ? {
        "Content-Type": "application/json",
        "X-Requested-With": "tts-erp",
      } : {}),
      ...(options.headers || {}),
    };
    if (method === "GET" && conditional && reuseEtag && state.etags.has(path)) {
      headers["If-None-Match"] = state.etags.get(path);
    }
    const response = await fetch(api + path, {
      credentials: "same-origin",
      ...fetchOptions,
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
    if (method === "GET" && conditional) {
      const etag = response.headers.get("ETag");
      if (etag) {
        state.etags.set(path, etag);
        rememberEtag(path);
      }
    }
    if (response.status === 204) return null;
    return response.json();
  }

  // state.etags is keyed by request path, so it grows once per distinct path the
  // page ever visits (filters, cursors, opened tasks). Cap it so a long session
  // cannot leak memory; the oldest entry is simply re-fetched unconditionally.
  const MAX_ETAG_ENTRIES = 64;
  const etagOrder = [];
  function rememberEtag(path) {
    const at = etagOrder.indexOf(path);
    if (at >= 0) etagOrder.splice(at, 1);
    etagOrder.push(path);
    while (etagOrder.length > MAX_ETAG_ENTRIES) state.etags.delete(etagOrder.shift());
  }

  // Detail bodies we keep so an explicit reopen can render after a 304. Bounded
  // for the same reason as the ETag cache: one entry per task ever opened.
  const MAX_DETAIL_ENTRIES = 8;
  function rememberDetail(taskId, detail) {
    state.detailCache.delete(taskId);
    state.detailCache.set(taskId, detail);
    while (state.detailCache.size > MAX_DETAIL_ENTRIES) {
      state.detailCache.delete(state.detailCache.keys().next().value);
    }
  }

  function codePointLength(value) {
    return Array.from(value).length;
  }

  function valid() {
    const caption = $("publish-caption").value;
    const captionLength = codePointLength(caption);
    // The server rejects a blank caption with 422 (``caption.strip()``), so the
    // client must not let a whitespace-only caption through (P2-14).
    return state.config && state.file && state.file.type === "video/mp4" &&
      state.file.size <= state.config.maxVideoBytes &&
      caption.trim().length > 0 &&
      captionLength > 0 && captionLength <= state.config.maxCaptionCharacters;
  }

  function renderForm() {
    const file = state.file;
    const activeUpload = Boolean(state.upload?.xhr);
    const lifecycleBusy = Boolean(state.upload) || state.creating;
    $("publish-video-file").disabled = lifecycleBusy;
    $("publish-caption").disabled = lifecycleBusy;
    $("publish-device-select").disabled = lifecycleBusy;
    const reselect = $("publish-file-reselect");
    const clear = $("publish-file-clear");
    // During resume both controls stay reachable even before a file is picked,
    // otherwise a rejected pick leaves the page with no exit at all (P1-4).
    const canAdjust = Boolean(file) || Boolean(state.resumeTask);
    reselect.hidden = !canAdjust;
    clear.hidden = !canAdjust;
    reselect.disabled = lifecycleBusy;
    clear.disabled = lifecycleBusy;
    $("publish-resume-abandon").hidden = !state.resumeTask;
    $("publish-resume-abandon").disabled = lifecycleBusy;
    const writeReady = Boolean(state.config?.canWrite) && state.config?.worker?.status === "ready";
    $("publish-submit").disabled = !valid() || !writeReady || !!state.upload || state.creating;
    const writeStatus = $("publish-write-status");
    if (writeStatus) writeStatus.textContent = writeReady ? "" : (state.config?.writeBlockReason || "发布服务暂不可用，请稍后再试。");
    const progress = $("publish-upload-progress");
    progress.hidden = !activeUpload;
    progress.value = state.upload?.progress || 0;
    progress.setAttribute("aria-valuenow", String(progress.value));
    progress.setAttribute("aria-valuetext", `${progress.value}%`);
    const cancel = $("publish-upload-cancel");
    cancel.hidden = !activeUpload;
    cancel.disabled = !activeUpload;
    const caption = $("publish-caption").value;
    const captionLength = codePointLength(caption);
    const maxCaption = state.config?.maxCaptionCharacters || 4000;
    const overLimit = captionLength > maxCaption;
    const blankCaption = captionLength > 0 && caption.trim().length === 0;
    $("publish-caption-count").textContent = blankCaption
      ? "只有空白字符，请输入文案"
      : `${captionLength} / ${maxCaption}`;
    $("publish-caption-count").classList.toggle("is-danger", overLimit || blankCaption);
    $("publish-summary-file").textContent = file ? `${file.name} · ${Math.ceil(file.size / 1024 / 1024 * 10) / 10} MB` : "—";
    $("publish-summary-caption").textContent = `${captionLength} 字`;
    if (file) $("publish-file-name").textContent = file.name;
  }

  function pick(file) {
    if (state.upload || state.creating) return;
    if (!file) return;
    // Every rejection ends in the same teardown as clearSelection() so the page
    // can never keep showing the previous file's preview/name (P2-1). Staying in
    // resume mode is deliberate: the user must be able to pick the original file.
    const reject = (message) => {
      notice(message, true);
      resetFileSelection();
      renderForm();
    };
    if (state.resumeTask && (
      file.name !== state.resumeTask.filename || file.size !== state.resumeTask.sizeBytes
    )) {
      reject(`请选择原文件 ${state.resumeTask.filename}（${state.resumeTask.sizeBytes} 字节）`);
      return;
    }
    if (!state.config) {
      reject("配置尚未加载完成，请稍候重试");
      return;
    }
    state.file = file;
    if (!state.resumeTask) state.clientRequestId = crypto.randomUUID();
    if (file.type !== "video/mp4" || !file.name.toLowerCase().endsWith(".mp4")) {
      reject("只接受 MP4 视频");
      return;
    }
    if (file.size > state.config.maxVideoBytes) {
      reject("视频超过服务端大小上限");
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

  // The label and the select have one visibility source, and the box is present
  // from the first paint, so loading /devices no longer shifts the form by 63px
  // and a failed load can no longer leave a dangling 目标设备 label (P2-6).
  function showDeviceField(message) {
    const label = document.querySelector('label[for="publish-device-select"]');
    const select = $("publish-device-select");
    if (label) label.textContent = message || "目标设备";
    select.hidden = false;
  }

  async function loadDevices() {
    showDeviceField();
    try {
      const data = await request("/devices");
      const select = $("publish-device-select");
      if (!data.notModified) {
        state.devices = Array.isArray(data.devices) ? data.devices : [];
        const fallback = document.createElement("option");
        fallback.value = "";
        fallback.textContent = `默认 · ${state.config.target.deviceSerialMasked || "未配置"}`;
        select.replaceChildren(fallback);
        state.devices.forEach((device) => {
          const option = document.createElement("option");
          option.value = device.serial;
          const busy = device.isBusy ? " · 忙" : "";
          option.textContent = `${device.serialMasked} · ${device.model || device.product || "未知机型"}${busy}`;
          select.append(option);
        });
        select.onchange = () => {
          state.deviceSerial = select.value;
          renderDeviceTarget();
        };
      }
      select.hidden = false;
      select.disabled = false;
    } catch (error) {
      const select = $("publish-device-select");
      showDeviceField("目标设备（列表暂不可用，使用默认）");
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "默认设备";
      select.replaceChildren(option);
      select.disabled = true;
      notice(`设备列表暂不可用，使用默认设备（${error.message}）`, true);
    }
  }

  function currentDeviceMasked() {
    if (state.deviceSerial) {
      const device = state.devices.find((item) => item.serial === state.deviceSerial);
      return device ? device.serialMasked : state.deviceSerial;
    }
    return state.config?.target?.deviceSerialMasked || "未配置";
  }

  function renderDeviceTarget() {
    $("publish-target-device").textContent = currentDeviceMasked();
  }

  // Single source of truth for which filter is selected, so the first paint and
  // every later click agree on the active tab (P2-19).
  function syncFilterTabs() {
    document.querySelectorAll("[data-task-filter]").forEach((node) => {
      const active = node.dataset.taskFilter === state.filter;
      node.classList.toggle("is-active", active);
      node.setAttribute("aria-pressed", String(active));
    });
  }

  async function init() {
    syncFilterTabs();
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
      await loadDevices();
      renderForm();
      await refresh();
      schedule();
    } catch (error) {
      notice(error.message, true);
      // The list placeholder is a terminal state now, not a spinner that never
      // resolves: /config failed, so no list request will ever be made (P1-6).
      renderTasks(null, { error: error.message || "配置加载失败" });
      // P1-6: the form's own placeholders must leave 「加载中…」 too, otherwise
      // the page claims to be loading limits that can never arrive.
      $("publish-size-hint").textContent = "服务端大小上限加载失败";
      $("publish-caption-count").textContent = "服务端文案上限加载失败";
    }
  }

  function showConfirmationDialog(dialog) {
    dialog.returnValue = "cancel";
    return new Promise((resolve) => {
      const onCancel = () => { dialog.returnValue = "cancel"; };
      const onClose = () => {
        dialog.removeEventListener("cancel", onCancel);
        resolve(dialog.returnValue === "confirm");
      };
      dialog.addEventListener("cancel", onCancel);
      dialog.addEventListener("close", onClose, { once: true });
      dialog.showModal();
    });
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
    $("publish-confirm-device").textContent = currentDeviceMasked();
    $("publish-confirm-album").textContent = state.config.target.album || "—";
    return showConfirmationDialog(dialog);
  }

  // §10.12 requires an upload failure to say where it happened and what to do
  // next, so the HTTP status and the MinIO error code are mapped to actionable
  // copy instead of one opaque string.
  function uploadFailureMessage(xhr) {
    const status = xhr.status;
    const code = (xhr.responseText || "").match(/<Code>([^<]+)<\/Code>/)?.[1] || "";
    const suffix = code ? ` · ${code}` : "";
    if (status === 403) return `上传地址已过期或被拒绝（HTTP 403${suffix}）。请重新提交以获取新的上传地址。`;
    if (status === 413) return "视频超过对象存储的大小上限（HTTP 413）。请压缩视频后再上传。";
    if (status === 400 || status === 422) return `对象校验失败${suffix}：实际大小与所选文件不一致，请重新选择文件上传。`;
    if (status === 404) return `上传地址不存在（HTTP 404${suffix}）。请重新提交以获取新的上传地址。`;
    if (status >= 500) return `对象存储服务端拒绝（HTTP ${status}${suffix}）。文件和文案已保留，请稍后重试上传。`;
    return `视频上传失败（HTTP ${status}${suffix}）。文件和文案已保留，请重试上传。`;
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
          ...(state.deviceSerial ? { deviceSerial: state.deviceSerial } : {}),
        }),
      });
      upload.taskId = ticket.taskId;
      upload.rowVersion = ticket.rowVersion;
      if (ticket.idempotentReplay && ticket.stage !== "awaiting_upload") {
        notice("任务已由服务端确认并进入处理流程");
        clearForm();
        state.clientRequestId = crypto.randomUUID();
        await refresh();
        return;
      }
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
            return xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(Error(uploadFailureMessage(xhr)));
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

  function resetFileSelection() {
    if (state.url) URL.revokeObjectURL(state.url);
    state.file = null;
    state.url = null;
    $("publish-video-file").value = "";
    $("publish-video-preview").hidden = true;
    $("publish-video-preview").removeAttribute("src");
    $("publish-preview-metadata").textContent = "";
    $("publish-file-name").textContent = "";
  }

  // Leaving resume mode must also drop the resumed task's caption, otherwise the
  // next, unrelated task would silently inherit it (P1-4).
  function exitResume() {
    const wasResuming = Boolean(state.resumeTask);
    state.resumeTask = null;
    state.clientRequestId = null;
    if (wasResuming) $("publish-caption").value = "";
    return wasResuming;
  }

  function clearSelection() {
    if (state.upload || state.creating) return;
    const wasResuming = exitResume();
    resetFileSelection();
    renderForm();
    if (wasResuming) notice("已放弃续传，可以重新选择文件创建新任务。");
  }

  function clearForm() {
    state.clientRequestId = null;
    state.resumeTask = null;
    $("publish-caption").value = "";
    resetFileSelection();
    renderForm();
  }

  // The rail and the drawer previously rendered raw backend tokens (stage,
  // detail.status). These helpers add the missing lookup without touching the
  // expressions that already prefer the server's *Label field (P2-15).
  const STATUS_LABELS = {
    pending: "排队", running: "执行中", succeeded: "成功", failed: "失败",
    needs_review: "需核验", cancelled: "已取消",
  };
  const STAGE_LABELS = {
    awaiting_upload: "等待上传", queued: "队列中", waiting_device: "等待设备",
    downloading: "下载视频", staging_device: "写入相册",
    dispatching_artemis: "提交 Artemis", waiting_artemis: "等待 Artemis",
    verifying: "自动核验", cleaning: "清理", done: "已完成",
  };
  // P2-15: the drawer cleanup line rendered raw cleanup.status tokens
  // (succeeded/failed/not_started/pending) straight from the API.
  const CLEANUP_STATUS_LABELS = {
    not_started: "未开始", pending: "待清理", succeeded: "已清理", failed: "清理失败",
  };
  const cleanupStatusLabel = (value) => CLEANUP_STATUS_LABELS[value] || value || "—";

  const statusLabel = (value, provided) => provided || STATUS_LABELS[value] || value || "—";
  const stageLabel = (value, provided) => provided || STAGE_LABELS[value] || value || "—";

  function renderRail(task) {
    const rail = $("publish-rail");
    const currentStage = task ? (task.operationalStage || task.stage) : null;
    rail.querySelectorAll("[data-stage]").forEach((node) => {
      const current = Boolean(task && node.dataset.stage === currentStage);
      node.classList.toggle("is-current", current);
      if (current) node.setAttribute("aria-current", "step");
      else node.removeAttribute("aria-current");
    });
    $("publish-rail-summary").textContent = task
      ? `${task.filename || ""} · ${stageLabel(currentStage)}`
      : "当前无运行任务";
    const meta = $("publish-rail-meta");
    const detailButton = $("publish-rail-detail");
    if (task) {
      const attempt = task.currentAttempt;
      const startedAt = task.operationalStageStartedAt || task.stageStartedAt || attempt?.startedAt || task.startedAt;
      const attemptCount = attempt?.kind === "verify" ? task.verifyAttemptCount : task.publishAttemptCount;
      const elapsed = startedAt ? `${Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000))} 秒` : "—";
      // 清理退避期的“下次重试”与“阶段开始时间是两个字段”，分开展示，
      // 否则把未来时间当成开始时间会让已耗时恒为 0 秒（P2-21）。
      const retryAt = task.cleanupNextAttemptAt ? ` · 下次重试 ${new Date(task.cleanupNextAttemptAt).toLocaleString()}` : "";
      meta.textContent = `任务 ${task.taskId} · ${stageLabel(currentStage)} · ${attempt?.kindLabel || attempt?.kind} #${attemptCount || 0} · 开始 ${startedAt || "—"} · 已耗时 ${elapsed}${retryAt}`;
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
    verify: "再次自动核验",
    retry_cleanup: "重试清理",
    replace_upload: "重新上传",
    copy_artemis_id: "复制 Artemis ID",
  };
  const CONFIRM_ACTIONS = new Set(["cancel", "retry", "verify", "retry_cleanup", "replace_upload"]);

  function confirmationMessage(task, actionName) {
    const attempt = task.currentAttempt || {};
    const attemptId = attempt.artemisSessionId || task.latestArtemisSessionId || "尚未创建";
    const relatedPublishId = task.relatedPublishAttempt?.artemisSessionId || attemptId;
    const failure = `${task.lastErrorCode || "无错误码"} / ${task.lastErrorMessage || "无错误详情"}`;
    const budget = `${task.retryBudgetUsed ?? 0}/${state.config?.maxPublishAttempts || 3}`;
    return {
      cancel: `取消任务 ${task.taskId}（阶段 ${stageLabel(task.stage, task.stageLabel)}）会保留审计记录，并清理已生成的对象；确认取消？`,
      retry: `重试任务 ${task.taskId}\n前次 Artemis ID：${attemptId}\n失败阶段：${stageLabel(task.stage, task.stageLabel)}\n错误：${failure}\n发布预算：${budget}\n重试会复用已上传对象，且不会自动确认模糊结果。确认重试？`,
      verify: `只读核验任务 ${task.taskId} 的发布 attempt ${relatedPublishId}；只查看作品页和草稿箱，不会进入上传页，不会再次点击发布。确认开始核验？`,
      retry_cleanup: `仅重试任务 ${task.taskId} 的失败资源：${(task.cleanupRetryableResources || []).join("、") || "无"}；不会改变业务发布结果。确认重试清理？`,
      replace_upload: `任务 ${task.taskId} 的原对象已删除；当前阶段 ${stageLabel(task.stage, task.stageLabel)}，错误 ${failure}，预算 ${budget}。重新上传会创建新的发布输入。确认重新上传？`,
    }[actionName] || `确认${ACTION_LABELS[actionName] || actionName}？`;
  }

  function confirmTaskAction(task, actionName, trigger) {
    const dialog = $("publish-action-dialog");
    if (!dialog?.showModal) {
      notice("当前浏览器不支持安全确认对话框，操作已取消。", true);
      return Promise.resolve(false);
    }
    $("publish-action-title").textContent = actionName === "verify"
      ? "再次自动核验"
      : `${ACTION_LABELS[actionName] || actionName}确认`;
    $("publish-action-evidence").textContent = confirmationMessage(task, actionName);
    $("publish-action-confirm").textContent = actionName === "verify"
      ? "开始核验"
      : `确认${ACTION_LABELS[actionName] || "操作"}`;
    // Focus is restored here, while the trigger is still focusable. The caller
    // disables it only after this promise settles (P2-16 / §10.13).
    return showConfirmationDialog(dialog).then((confirmed) => {
      trigger?.focus?.();
      return confirmed;
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
    notice("请选择原视频以继续上传；选择其它文件请先点「放弃续传」。");
    $("publish-video-file").click();
  }

  const NON_MUTATING_ACTIONS = new Set(["view", "copy_artemis_id", "continue_upload"]);

  function setTaskMutationBusy(taskId, actionName, busy) {
    const key = `${taskId}:${actionName}`;
    if (busy) state.mutationBusy.add(key);
    else state.mutationBusy.delete(key);
    const taskBusy = Array.from(state.mutationBusy).some((item) => item.startsWith(`${taskId}:`));
    document.querySelectorAll("[data-task-mutation]").forEach((button) => {
      if (button.dataset.taskId === taskId) button.disabled = taskBusy;
    });
  }

  async function runTaskAction(task, actionName, trigger = null) {
    const mutation = !NON_MUTATING_ACTIONS.has(actionName);
    const key = `${task.taskId}:${actionName}`;
    if (mutation && state.mutationBusy.has(key)) return;
    // Busy state is applied *after* the confirmation settles: setTaskMutationBusy
    // disables every [data-task-mutation] button of the row, including the
    // trigger, and focusing a disabled element is a no-op — that is what used to
    // strand focus on <body> after the dialog closed (§10.13 / P2-16).
    let busyApplied = false;
    const applyBusy = () => {
      if (!mutation || busyApplied) return;
      busyApplied = true;
      setTaskMutationBusy(task.taskId, actionName, true);
      if (trigger) trigger.disabled = true;
    };
    try {
      if (CONFIRM_ACTIONS.has(actionName)) {
        const confirmed = await confirmTaskAction(task, actionName, trigger);
        if (!confirmed) return;
      }
      applyBusy();
      if (actionName === "view") return openDetail(task.taskId);
      if (actionName === "copy_artemis_id") {
        const id = task.latestArtemisSessionId;
        if (id) await copyText(id, trigger);
        return;
      }
      if (actionName === "continue_upload") return resumeUpload(task);
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
    } finally {
      if (busyApplied) {
        setTaskMutationBusy(task.taskId, actionName, false);
        if (trigger) {
          trigger.disabled = false;
          if (!document.activeElement || document.activeElement === document.body) {
            trigger.focus?.();
          }
        }
      }
    }
  }

  function renderTasks(payload, { append = false, path = null, error = null } = {}) {
    const channel = state.listChannel;
    if (error) {
      renderListError(error);
      return;
    }
    const incoming = payload?.items || [];
    // 加载更多 already expanded the list: a timed refresh must only replace the
    // page-1 segment instead of dropping the appended rows (P2-10).
    const mergeFirstPage = !append && path && channel.renderedPath === path &&
      channel.pageSize > 0 && channel.items.length > channel.pageSize;
    if (append) {
      channel.items = [...channel.items, ...incoming];
      channel.nextCursor = payload.nextCursor || null;
    } else if (mergeFirstPage) {
      const seen = new Set();
      channel.items = [...incoming, ...channel.items.slice(channel.pageSize)]
        .filter((task) => (seen.has(task.taskId) ? false : seen.add(task.taskId)));
      channel.pageSize = incoming.length;
    } else {
      channel.items = incoming;
      channel.pageSize = incoming.length;
      channel.nextCursor = payload.nextCursor || null;
    }
    // renderedPath tracks the source of the *page-1* segment; an append must not
    // claim the screen was drawn from the cursor path.
    if (path && !append) channel.renderedPath = path;
    const loadMore = $("publish-load-more");
    loadMore.hidden = !channel.nextCursor;
    loadMore.disabled = false;
    const body = $("publish-task-list");
    body.replaceChildren();
    if (!channel.items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.className = "op-empty";
      cell.textContent = "还没有发布任务。选择一个 MP4 并填写文案开始。";
      row.append(cell);
      body.append(row);
      return;
    }
    channel.items.forEach((task) => {
      const row = document.createElement("tr");
      [
        task.queuePosition
          ? `${statusLabel(task.status, task.statusLabel)} · 前面 ${Math.max(0, task.queuePosition - 1)} 个任务`
          : statusLabel(task.status, task.statusLabel),
        task.stageLabel || stageLabel(task.stage),
        task.captionPreview || "—",
        task.filename,
        `${task.publishAttemptCount || 0} / ${task.verifyAttemptCount || 0}`,
        task.createdBy || "—",
        task.latestArtemisSessionId || "尚未创建",
        new Date(task.createdAt).toLocaleString(),
      ].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value ?? "—";
        cell.dataset.label = ["状态", "阶段", "文案", "视频", "发布/核验", "创建者", "Artemis ID", "时间"][index] || "";
        if (index === 6) {
          cell.className = "artemis-cell";
          const id = task.latestArtemisSessionId;
          if (id) {
            const code = document.createElement("code");
            code.textContent = id;
            const copy = document.createElement("button");
            copy.className = "btn-secondary artemis-copy";
            copy.textContent = "复制";
            copy.onclick = () => copyText(id, copy);
            cell.replaceChildren(code, copy);
          }
        }
        row.append(cell);
      });
      const actions = document.createElement("td");
      actions.dataset.label = "操作";
      (task.allowedActions || []).filter((actionName) => actionName !== "copy_artemis_id").forEach((actionName) => {
        const button = document.createElement("button");
        button.className = "btn-secondary";
        button.textContent = ACTION_LABELS[actionName] || actionName;
        if (!NON_MUTATING_ACTIONS.has(actionName)) {
          button.dataset.taskMutation = "true";
          button.dataset.taskId = task.taskId;
          button.disabled = Array.from(state.mutationBusy).some((key) => key.startsWith(`${task.taskId}:`));
        }
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

  // P1-6: a failed load must be visually distinct from "there are no tasks".
  function renderListError(message) {
    const body = $("publish-task-list");
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "op-error";
    cell.textContent = `任务列表加载失败：${message}。正在自动重试，也可以点「立即刷新」。`;
    row.append(cell);
    body.replaceChildren(row);
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
    const params = new URLSearchParams();
    if (state.filter) params.set("status", state.filter);
    if (append && channel.nextCursor) params.set("cursor", channel.nextCursor);
    const query = params.toString() ? `?${params}` : "";
    const path = `/tasks${query}`;
    try {
      // A 304 only means "the payload of *this* path is unchanged". Once the
      // filter or the cursor reset moved the rendered source to another path,
      // reusing its ETag would leave the table showing the previous filter's
      // rows, so this request must not be conditional (P1-2).
      const list = await request(path, {
        signal: channel.controller.signal,
        conditional: !append,
        reuseEtag: !append && channel.renderedPath === path,
      });
      if (generation !== channel.generation) return;
      channel.failures = 0;
      if (!list.notModified) {
        state.pollState = list.pollState || { running: false, cleaning: false, queued: false };
        renderTasks(list, { append, path });
      }
      $("publish-last-refreshed").textContent = `上次刷新 ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return;
      channel.failures += 1;
      // A failed request must not leave a cached validator behind, otherwise the
      // retry could be answered 304 and the error row would never clear (P1-6).
      if (path) state.etags.delete(path);
      notice("任务列表刷新失败，正在退避重试", true);
      renderTasks(null, { error: error.message || "请稍后重试" });
    } finally {
      if (append) $("publish-load-more").disabled = false;
    }
  }

  async function refreshDetail() {
    const taskId = state.detailTaskId;
    if (!taskId) return null;
    const channel = state.detailChannel;
    const generation = ++channel.generation;
    if (channel.controller) channel.controller.abort();
    channel.controller = new AbortController();
    try {
      const diagnostics = state.config?.canViewDiagnostics === true
        ? "?includeDiagnostics=true"
        : "";
      const detail = await request(`/tasks/${taskId}${diagnostics}`, {
        signal: channel.controller.signal,
      });
      if (generation !== channel.generation || taskId !== state.detailTaskId) return null;
      channel.failures = 0;
      // A 304 carries no body; the payload we already hold for *this* task is by
      // definition the current one, so refresh the drawer from that snapshot.
      const body = detail.notModified ? state.detailCache.get(taskId) : detail;
      if (!body || body.taskId !== taskId) return null;
      rememberDetail(taskId, body);
      state.detail = body;
      renderDetail(body);
      return body;
    } catch (error) {
      if (error.name === "AbortError" || generation !== channel.generation) return null;
      channel.failures += 1;
      notice("任务详情刷新失败，正在退避重试", true);
      return null;
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
    heading.textContent = `${detail.filename} · ${statusLabel(detail.status, detail.statusLabel)}`;
    drawer.append(heading);
    const metadata = document.createElement("dl");
    const fields = [
      ["任务", `${detail.taskId} · ${stageLabel(detail.stage, detail.stageLabel)}`],
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
      summary.textContent = `第 ${attempt.sequenceNo} 次 ${attempt.kindLabel || attempt.kind} · ${statusLabel(attempt.status)} · ${attempt.startedAt || "—"} → ${attempt.finishedAt || "—"}`;
      const diagnostic = document.createElement("p");
      diagnostic.textContent = `错误：${attempt.error || "—"} · 重试分类：${attempt.retryClassification || "—"} · 可重试：${attempt.retrySafe == null ? "—" : attempt.retrySafe ? "是" : "否"}`;
      const id = document.createElement("code");
      id.textContent = attempt.artemisSessionId;
      const copy = document.createElement("button");
      copy.className = "btn-secondary drawer-action";
      copy.textContent = "复制 Artemis ID";
      copy.onclick = () => copyText(attempt.artemisSessionId, copy);
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
      const cleanupBlock = detail.cleanup;
      cleanup.textContent = `清理：设备 ${cleanupStatusLabel(cleanupBlock.device.status)}（${cleanupBlock.device.error || "—"}） · spool ${cleanupStatusLabel(cleanupBlock.spool.status)}（${cleanupBlock.spool.error || "—"}） · 对象 ${cleanupStatusLabel(cleanupBlock.object.status)}（${cleanupBlock.object.error || "—"}）`;
      drawer.append(cleanup);
    }
    const actions = $("publish-drawer-actions");
    actions.replaceChildren();
    (detail.allowedActions || []).filter((action) => action !== "view" && action !== "copy_artemis_id").forEach((action) => {
      const button = document.createElement("button");
      button.className = "btn-secondary drawer-action";
      button.textContent = ACTION_LABELS[action] || action;
      button.dataset.taskMutation = "true";
      button.dataset.taskId = detail.taskId;
      button.disabled = Array.from(state.mutationBusy).some((key) => key.startsWith(`${detail.taskId}:`));
      button.onclick = () => runTaskAction(detail, action, button);
      actions.append(button);
    });
  }

  // Opening the drawer is deliberately decoupled from "did we receive a body":
  // a 304 is a valid settle and must still open the window (P1-3). The guard is
  // that the rendered payload must belong to the requested task, so a cached
  // snapshot of another task can never be shown (D1).
  async function openDetail(id) {
    state.detailTaskId = id;
    try {
      const detail = await refreshDetail();
      if (!detail || detail.taskId !== id) return;
      const drawer = $("publish-task-drawer");
      drawer.returnValue = "cancel";
      drawer.showModal();
      schedule();
    } catch (error) { notice(error.message, true); }
  }

  $("publish-video-file").addEventListener("change", (event) => pick(event.target.files[0]));
  $("publish-file-reselect").addEventListener("click", () => $("publish-video-file").click());
  $("publish-file-clear").addEventListener("click", clearSelection);
  $("publish-resume-abandon").addEventListener("click", clearSelection);
  $("publish-caption").addEventListener("input", () => {
    if (!state.upload && !state.creating) renderForm();
  });
  $("publish-submit").addEventListener("click", create);
  $("publish-upload-cancel").addEventListener("click", cancelUpload);
  $("publish-refresh-now").addEventListener("click", async () => { await refresh(); schedule(); });
  $("publish-refresh-mode").addEventListener("change", schedule);
  $("publish-drawer-close").addEventListener("click", () => {
    $("publish-task-drawer").close("cancel");
  });
  $("publish-task-drawer").addEventListener("cancel", () => {
    $("publish-task-drawer").returnValue = "cancel";
  });
  $("publish-task-drawer").addEventListener("close", () => {
    const opener = state.detailOpener;
    state.detailOpener = null;
    state.detailTaskId = null;
    state.detail = null;
    state.detailChannel.generation += 1;
    state.detailChannel.controller?.abort();
    state.detailChannel.controller = null;
    clearTimeout(state.detailTimer);
    opener?.focus?.();
  });
  document.querySelectorAll("[data-task-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.taskFilter;
    state.listChannel.nextCursor = null;
    syncFilterTabs();
    refresh({ current: false });
  }));
  $("publish-load-more").addEventListener("click", async (event) => {
    if (!state.listChannel.nextCursor) return;
    event.currentTarget.disabled = true;
    await refreshList({ append: true });
  });
  async function copyText(value, trigger = null) {
    let copied = false;
    try {
      await navigator.clipboard.writeText(value);
      copied = true;
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = value;
      fallback.setAttribute("readonly", "");
      document.body.append(fallback);
      fallback.select();
      copied = document.execCommand("copy");
      fallback.remove();
    }
    notice(copied ? "Artemis ID 已复制" : "复制失败，请手动选择完整 ID", !copied);
    if (copied && trigger) {
      const original = trigger.textContent;
      trigger.textContent = "已复制";
      trigger.disabled = true;
      setTimeout(() => {
        trigger.textContent = original;
        trigger.disabled = false;
      }, 1500);
    }
    return copied;
  }

  document.querySelector("[data-copy-artemis-id]").addEventListener("click", (event) => {
    const id = event.currentTarget.dataset.copyArtemisId;
    if (id) copyText(id, event.currentTarget);
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

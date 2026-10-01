(() => {
  const marker = '/v2/';
  const markerAt = location.pathname.indexOf(marker);
  const rootPrefix = markerAt >= 0 ? location.pathname.slice(0, markerAt) : '';
  const API = `${rootPrefix}/v2`;
  const state = { jobs: [], shops: [], canAdmin: false, pendingJob: null };
  const els = {
    body: document.getElementById('jobs-body'),
    notice: document.getElementById('notice'),
    identity: document.getElementById('ops-identity'),
    refresh: document.getElementById('refresh-btn'),
  };

  function setNotice(text, kind) {
    els.notice.textContent = text || '';
    els.notice.className = `notice${kind ? ` ${kind}` : ''}`;
  }

  async function api(path, options = {}) {
    const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    if (options.method && options.method !== 'GET') headers['X-Requested-With'] = 'tts-erp';
    const res = await fetch(`${API}${path}`, Object.assign({}, options, { headers }));
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) { /* ignore non-json errors */ }
      throw new Error(Array.isArray(detail) ? JSON.stringify(detail) : detail);
    }
    return res.json();
  }

  function fmtDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  }

  function fmtTime(value) {
    if (!value) return '从未运行';
    try { return new Date(value).toLocaleString('zh-CN', { hour12: false }); }
    catch (_) { return value; }
  }

  function shopOptions(selected) {
    const opts = ['<option value="">全部授权店铺</option>'];
    for (const shop of state.shops) {
      const label = `${shop.account_name || '未命名'} · ${shop.shop_id}`;
      opts.push(`<option value="${escapeHtml(shop.shop_id)}"${shop.shop_id === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`);
    }
    return opts.join('');
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[ch]));
  }

  function labelForStatus(status) {
    if (status.last_status === 'running') return '运行中';
    if (status.last_status === 'failed') return '失败';
    if (status.last_status === 'succeeded') return '成功';
    return status.last_status || 'no run';
  }

  function render() {
    if (!state.jobs.length) {
      els.body.innerHTML = '<tr><td colspan="7" class="empty">暂无注册任务</td></tr>';
      return;
    }
    els.body.innerHTML = state.jobs.map(job => {
      const status = job.last_status || {};
      const severity = status.severity || 'unknown';
      const scope = job.is_tiktok ? 'TikTok 店铺' : '系统级';
      const disabled = state.canAdmin ? '' : ' disabled';
      const triggerControl = job.is_tiktok
        ? `<div class="trigger-controls"><select class="scope-select" data-shop-for="${escapeHtml(job.job_name)}"${disabled}>${shopOptions('')}</select><button class="primary" data-trigger="${escapeHtml(job.job_name)}"${disabled}>执行</button></div>`
        : `<div class="trigger-controls"><button class="primary" data-trigger="${escapeHtml(job.job_name)}"${disabled}>执行</button></div>`;
      const pending = state.pendingJob === job.job_name ? '<span class="pending-dot">已提交</span>' : '';
      return `<tr>
        <td data-label="任务"><span class="job-name">${escapeHtml(job.job_name)}</span><span class="module">${escapeHtml(job.module_path)} · ${escapeHtml(job.entrypoint)}</span>${pending}</td>
        <td data-label="周期">${fmtDuration(job.interval_seconds)}</td>
        <td data-label="范围">${scope}</td>
        <td data-label="状态"><span class="badge ${severity}">${escapeHtml(severity)}</span><span class="module">${escapeHtml(labelForStatus(status))}</span></td>
        <td data-label="上次运行">${escapeHtml(fmtTime(status.last_run_at))}</td>
        <td data-label="启用"><label class="switch"><input type="checkbox" data-enable="${escapeHtml(job.job_name)}" ${job.enabled ? 'checked' : ''}${disabled}>${job.enabled ? '启用' : '停用'}</label></td>
        <td data-label="立即执行">${triggerControl}</td>
      </tr>`;
    }).join('');
  }

  async function loadIdentity() {
    try {
      const me = await api('/auth/me');
      state.canAdmin = me.authenticated && me.role === 'admin';
      els.identity.textContent = me.authenticated ? `role=${me.role}` : '未登录';
      if (me.authenticated && !state.canAdmin) {
        setNotice('当前账号不是 admin：只能查看任务状态，不能启停或立即执行。');
      }
    } catch (_) {
      els.identity.textContent = '身份未知';
    }
  }

  async function loadJobs() {
    setNotice('加载任务状态…');
    const data = await api('/sync/jobs');
    state.jobs = data.jobs || [];
    state.shops = data.tiktok_shops || [];
    render();
    const refreshedAt = new Date(data.server_time).toLocaleString('zh-CN', { hour12: false });
    if (state.canAdmin) {
      setNotice(`已刷新 · ${refreshedAt}`, 'ok');
    } else {
      setNotice(`只读模式（需要 admin 才能操作）· 已刷新 ${refreshedAt}`);
    }
  }

  async function toggleJob(jobName, enabled, input) {
    input.disabled = true;
    input.classList.add('is-busy');
    setNotice(`${enabled ? '启用' : '停用'} ${jobName}…`);
    try {
      await api(`/admin/sync-jobs/${encodeURIComponent(jobName)}/enabled`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      });
      await loadJobs();
    } catch (err) {
      input.checked = !enabled;
      setNotice(`操作失败：${err.message}`, 'error');
    } finally {
      input.classList.remove('is-busy');
      input.disabled = !state.canAdmin;
    }
  }

  async function triggerJob(jobName, button) {
    const selector = Array.from(document.querySelectorAll('[data-shop-for]')).find(
      el => el.getAttribute('data-shop-for') === jobName
    );
    const shopId = selector ? selector.value || null : null;
    if (selector && !shopId) {
      const ok = window.confirm(`将立即执行 ${jobName} 的全部授权店铺。这个操作可能触发多店铺上游同步，确定继续吗？`);
      if (!ok) {
        setNotice('已取消全店铺执行。');
        return;
      }
    }
    button.disabled = true;
    button.classList.add('is-busy');
    setNotice(`提交 ${jobName}${shopId ? ` / ${shopId}` : ' / 全部店铺'}…`);
    try {
      const body = shopId ? { shop_id: shopId } : {};
      const res = await api(`/admin/sync-jobs/${encodeURIComponent(jobName)}/trigger`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      state.pendingJob = jobName;
      render();
      setNotice(`${res.message || '任务已提交'} 可稍后刷新查看 running / succeeded / failed。`, 'ok');
      window.setTimeout(async () => {
        await loadJobs();
        if (state.pendingJob === jobName) state.pendingJob = null;
        render();
      }, 1500);
    } catch (err) {
      setNotice(`触发失败：${err.message}`, 'error');
    } finally {
      button.classList.remove('is-busy');
      button.disabled = !state.canAdmin;
    }
  }

  els.refresh.addEventListener('click', () => loadJobs().catch(err => setNotice(err.message, 'error')));
  els.body.addEventListener('change', event => {
    const input = event.target.closest('[data-enable]');
    if (!input) return;
    toggleJob(input.getAttribute('data-enable'), input.checked, input);
  });
  els.body.addEventListener('click', event => {
    const button = event.target.closest('[data-trigger]');
    if (!button) return;
    triggerJob(button.getAttribute('data-trigger'), button);
  });

  (async () => {
    await loadIdentity();
    await loadJobs();
  })().catch(err => setNotice(`加载失败：${err.message}`, 'error'));
})();

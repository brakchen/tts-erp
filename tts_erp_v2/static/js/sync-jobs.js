(() => {
  const state = { jobs: [], shops: [] };
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
    const res = await fetch(path, Object.assign({}, options, { headers }));
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

  function render() {
    if (!state.jobs.length) {
      els.body.innerHTML = '<tr><td colspan="7" class="empty">暂无注册任务</td></tr>';
      return;
    }
    els.body.innerHTML = state.jobs.map(job => {
      const status = job.last_status || {};
      const severity = status.severity || 'unknown';
      const scope = job.is_tiktok ? 'TikTok 店铺' : '系统级';
      const triggerControl = job.is_tiktok
        ? `<select class="scope-select" data-shop-for="${escapeHtml(job.job_name)}">${shopOptions('')}</select><button class="primary" data-trigger="${escapeHtml(job.job_name)}">执行</button>`
        : `<button class="primary" data-trigger="${escapeHtml(job.job_name)}">执行</button>`;
      return `<tr>
        <td><span class="job-name">${escapeHtml(job.job_name)}</span><span class="module">${escapeHtml(job.module_path)} · ${escapeHtml(job.entrypoint)}</span></td>
        <td>${fmtDuration(job.interval_seconds)}</td>
        <td>${scope}</td>
        <td><span class="badge ${severity}">${escapeHtml(severity)}</span><span class="module">${escapeHtml(status.last_status || 'no run')}</span></td>
        <td>${escapeHtml(fmtTime(status.last_run_at))}</td>
        <td><label class="switch"><input type="checkbox" data-enable="${escapeHtml(job.job_name)}" ${job.enabled ? 'checked' : ''}>${job.enabled ? '启用' : '停用'}</label></td>
        <td>${triggerControl}</td>
      </tr>`;
    }).join('');
  }

  async function loadIdentity() {
    try {
      const me = await api('../../../v2/auth/me');
      els.identity.textContent = me.authenticated ? `role=${me.role}` : '未登录';
    } catch (_) {
      els.identity.textContent = '身份未知';
    }
  }

  async function loadJobs() {
    setNotice('加载任务状态…');
    const data = await api('../../../v2/sync/jobs');
    state.jobs = data.jobs || [];
    state.shops = data.tiktok_shops || [];
    render();
    setNotice(`已刷新 · ${new Date(data.server_time).toLocaleString('zh-CN', { hour12: false })}`, 'ok');
  }

  async function toggleJob(jobName, enabled, input) {
    input.disabled = true;
    setNotice(`${enabled ? '启用' : '停用'} ${jobName}…`);
    try {
      await api(`../../../v2/admin/sync-jobs/${encodeURIComponent(jobName)}/enabled`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      });
      await loadJobs();
    } catch (err) {
      input.checked = !enabled;
      setNotice(`操作失败：${err.message}`, 'error');
    } finally {
      input.disabled = false;
    }
  }

  async function triggerJob(jobName, button) {
    const selector = document.querySelector(`[data-shop-for="${CSS.escape(jobName)}"]`);
    const shopId = selector ? selector.value || null : null;
    button.disabled = true;
    setNotice(`提交 ${jobName}${shopId ? ` / ${shopId}` : ''}…`);
    try {
      const body = shopId ? { shop_id: shopId } : {};
      const res = await api(`../../../v2/admin/sync-jobs/${encodeURIComponent(jobName)}/trigger`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setNotice(res.message || '任务已提交', 'ok');
      window.setTimeout(loadJobs, 1200);
    } catch (err) {
      setNotice(`触发失败：${err.message}`, 'error');
    } finally {
      button.disabled = false;
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

  loadIdentity();
  loadJobs().catch(err => setNotice(`加载失败：${err.message}`, 'error'));
})();

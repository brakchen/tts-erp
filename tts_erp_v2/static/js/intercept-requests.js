/**
 * intercept-requests.js — 拦截记录查询页面行为
 *
 * 功能：
 * 1. 拦截记录列表（表格 + 筛选 + 分页）
 * 2. 详情弹窗（显示完整请求/响应）
 */

(function () {
  'use strict';

  // ---------- CONFIG ----------
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, '');
  if (!/^\/[a-z0-9\/_-]*$/i.test(PREFIX)) PREFIX = '';
  var API = PREFIX;
  const HEADERS = { 'Content-Type': 'application/json' };

  // ---------- STATE ----------
  let requests = [];
  let total = 0;
  let offset = 0;
  let limit = 100;
  let filterHost = '';
  let filterPath = '';
  let filterStatus = '';
  let filterWhitelisted = '';
  let filterMethod = '';
  let filterFrom = '';
  let filterTo = '';

  // ---------- DOM REFS ----------
  const $identity = document.getElementById('ops-identity');
  const $tbody = document.getElementById('req-tbody');
  const $total = document.getElementById('req-total');
  const $pagerLabel = document.getElementById('pager-label');
  const $btnPrev = document.getElementById('btn-prev');
  const $btnNext = document.getElementById('btn-next');
  const $filterHost = document.getElementById('filter-host');
  const $filterPath = document.getElementById('filter-path');
  const $filterStatus = document.getElementById('filter-status');
  const $filterWhitelisted = document.getElementById('filter-whitelisted');
  const $filterMethod = document.getElementById('filter-method');
  const $filterFrom = document.getElementById('filter-from');
  const $filterTo = document.getElementById('filter-to');
  const $btnQuery = document.getElementById('btn-query');
  const $modal = document.getElementById('detail-modal');
  const $modalTitle = document.getElementById('modal-title');
  const $detailContent = document.getElementById('detail-content');
  const $btnModalClose = document.getElementById('btn-modal-close');

  // ---------- INIT ----------
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    await checkAuth();
    bindEvents();
    await loadRequests();
  }

  // ---------- AUTH ----------
  async function checkAuth() {
    try {
      const res = await fetch(`${API}/v2/auth/me`, { credentials: 'same-origin' });
      if (!res.ok) {
        window.location.href = `${API}/v2/auth/login?next=${encodeURIComponent(location.pathname)}`;
        return;
      }
      const user = await res.json();
      if ($identity) {
        $identity.textContent = user.role || 'unknown';
      }
    } catch {
      console.error('Auth check failed');
    }
  }

  // ---------- EVENTS ----------
  function bindEvents() {
    if ($btnQuery) $btnQuery.addEventListener('click', onQuery);
    if ($btnPrev) $btnPrev.addEventListener('click', onPrev);
    if ($btnNext) $btnNext.addEventListener('click', onNext);
    if ($btnModalClose) $btnModalClose.addEventListener('click', hideModal);
    if ($modal) {
      $modal.addEventListener('click', (e) => {
        if (e.target === $modal) hideModal();
      });
    }
  }

  // ---------- LOAD ----------
  async function loadRequests() {
    try {
      const params = new URLSearchParams();
      if (filterHost) params.set('endpoint_host', filterHost);
      if (filterPath) params.set('endpoint_path', filterPath);
      if (filterStatus) params.set('status', filterStatus);
      if (filterWhitelisted) params.set('is_whitelisted', filterWhitelisted);
      if (filterMethod) params.set('method', filterMethod);
      if (filterFrom) params.set('from', filterFrom);
      if (filterTo) params.set('to', filterTo);
      params.set('limit', limit);
      params.set('offset', offset);

      const res = await fetch(`${API}/v2/intercept/requests?${params}`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        renderError(err.detail || `加载失败 (${res.status})`);
        return;
      }
      const data = await res.json();
      requests = data.requests || [];
      total = data.total || 0;
      renderTable();
      renderPager();
    } catch (e) {
      console.error('Failed to load requests:', e);
      renderError('加载失败，请检查网络');
    }
  }

  // ---------- RENDER ----------
  function renderTable() {
    if (!$tbody) return;

    if (requests.length === 0) {
      $tbody.innerHTML = '<tr><td colspan="8" class="op-empty">暂无记录</td></tr>';
      return;
    }

    const rows = requests.map(r => {
      const time = r.captured_at ? new Date(r.captured_at).toLocaleString('zh-CN', { hour12: false }) : '—';
      const method = esc(r.method || '—');
      const host = esc(r.endpoint_host || '—');
      const path = esc(r.endpoint_path || '—');
      const status = r.response_status || '—';
      const statusClass = status >= 200 && status < 300 ? 'status-ok' : status >= 400 ? 'status-err' : '';
      const duration = r.duration_ms != null ? `${r.duration_ms}ms` : '—';
      const whitelistBadge = r.is_whitelisted
        ? '<span class="badge badge-ok">✅</span>'
        : '<span class="badge badge-muted">❌</span>';

      return `
        <tr data-id="${r.id}" class="clickable-row">
          <td class="mono">${time}</td>
          <td><span class="method-badge method-${method.toLowerCase()}">${method}</span></td>
          <td class="mono td-host">${host}</td>
          <td class="mono td-path">${path}</td>
          <td class="mono ${statusClass}">${status}</td>
          <td class="mono">${duration}</td>
          <td>${whitelistBadge}</td>
          <td><button class="btn-icon" data-action="detail" data-id="${r.id}" title="查看详情">👁️</button></td>
        </tr>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $tbody.innerHTML = rows;

    // bind row events
    $tbody.querySelectorAll('.clickable-row').forEach(el => {
      el.addEventListener('click', () => showDetail(Number(el.dataset.id)));
    });
    $tbody.querySelectorAll('[data-action="detail"]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        showDetail(Number(el.dataset.id));
      });
    });
  }

  function renderPager() {
    const totalPages = Math.max(1, Math.ceil(total / limit));
    const currentPage = Math.floor(offset / limit) + 1;
    if ($total) $total.textContent = String(total);
    if ($pagerLabel) $pagerLabel.textContent = `${currentPage} / ${totalPages}`;
    if ($btnPrev) $btnPrev.disabled = offset <= 0;
    if ($btnNext) $btnNext.disabled = offset + limit >= total;
  }

  function renderError(msg) {
    if (!$tbody) return;
    $tbody.innerHTML = `<tr><td colspan="8" class="op-error">${esc(msg)}</td></tr>`;
  }

  // ---------- PAGINATION ----------
  function onQuery() {
    filterHost = $filterHost ? $filterHost.value.trim() : '';
    filterPath = $filterPath ? $filterPath.value.trim() : '';
    filterStatus = $filterStatus ? $filterStatus.value.trim() : '';
    filterWhitelisted = $filterWhitelisted ? $filterWhitelisted.value : '';
    filterMethod = $filterMethod ? $filterMethod.value : '';
    filterFrom = $filterFrom ? $filterFrom.value : '';
    filterTo = $filterTo ? $filterTo.value : '';
    offset = 0;
    loadRequests();
  }

  function onPrev() {
    if (offset <= 0) return;
    offset = Math.max(0, offset - limit);
    loadRequests();
  }

  function onNext() {
    if (offset + limit >= total) return;
    offset += limit;
    loadRequests();
  }

  // ---------- DETAIL MODAL ----------
  async function showDetail(id) {
    try {
      const res = await fetch(`${API}/v2/intercept/requests/${id}`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `加载详情失败 (${res.status})`);
        return;
      }
      const r = await res.json();
      renderDetail(r);
      if ($modalTitle) $modalTitle.textContent = `请求详情 #${r.id}`;
      if ($modal) $modal.classList.add('is-open');
    } catch (e) {
      console.error('Failed to load detail:', e);
      alert('加载详情失败');
    }
  }

  function hideModal() {
    if ($modal) $modal.classList.remove('is-open');
  }

  function renderDetail(r) {
    if (!$detailContent) return;

    const time = r.captured_at ? new Date(r.captured_at).toLocaleString('zh-CN', { hour12: false }) : '—';
    const whitelistText = r.is_whitelisted ? `是 (配置 #${r.matched_config_id || '-'})` : '否';
    const reqHeaders = r.request_headers ? JSON.stringify(r.request_headers, null, 2) : '—';
    const reqBody = r.request_body ? JSON.stringify(r.request_body, null, 2) : '—';
    const resHeaders = r.response_headers ? JSON.stringify(r.response_headers, null, 2) : '—';
    const resBody = r.response_body ? JSON.stringify(r.response_body, null, 2) : '—';

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, HTML-escaped content
    $detailContent.innerHTML = `
      <div class="detail-grid">
        <div class="detail-section">
          <h3 class="detail-section-title">基本信息</h3>
          <div class="detail-row"><span class="detail-label">请求 ID</span><span class="detail-value mono">${esc(r.request_id || '—')}</span></div>
          <div class="detail-row"><span class="detail-label">方法</span><span class="detail-value"><span class="method-badge method-${(r.method || '').toLowerCase()}">${esc(r.method || '—')}</span></span></div>
          <div class="detail-row"><span class="detail-label">URL</span><span class="detail-value mono detail-url">${esc(r.url || '—')}</span></div>
          <div class="detail-row"><span class="detail-label">域名</span><span class="detail-value mono">${esc(r.endpoint_host || '—')}</span></div>
          <div class="detail-row"><span class="detail-label">路径</span><span class="detail-value mono">${esc(r.endpoint_path || '—')}</span></div>
          <div class="detail-row"><span class="detail-label">白名单</span><span class="detail-value">${whitelistText}</span></div>
          <div class="detail-row"><span class="detail-label">耗时</span><span class="detail-value mono">${r.duration_ms != null ? r.duration_ms + 'ms' : '—'}</span></div>
          <div class="detail-row"><span class="detail-label">状态码</span><span class="detail-value mono">${r.response_status || '—'}</span></div>
          <div class="detail-row"><span class="detail-label">时间</span><span class="detail-value mono">${time}</span></div>
          ${r.error_type ? `<div class="detail-row"><span class="detail-label">错误类型</span><span class="detail-value text-danger">${esc(r.error_type)}</span></div>` : ''}
          ${r.error_message ? `<div class="detail-row"><span class="detail-label">错误信息</span><span class="detail-value text-danger">${esc(r.error_message)}</span></div>` : ''}
        </div>
        <div class="detail-section">
          <h3 class="detail-section-title">请求头</h3>
          <pre class="detail-pre"><code>${esc(reqHeaders)}</code></pre>
        </div>
        <div class="detail-section">
          <h3 class="detail-section-title">请求体</h3>
          <pre class="detail-pre"><code>${esc(reqBody)}</code></pre>
        </div>
        <div class="detail-section">
          <h3 class="detail-section-title">响应头</h3>
          <pre class="detail-pre"><code>${esc(resHeaders)}</code></pre>
        </div>
        <div class="detail-section">
          <h3 class="detail-section-title">响应体</h3>
          <pre class="detail-pre"><code>${esc(resBody)}</code></pre>
        </div>
      </div>
    `;
  }

  // ---------- UTILS ----------
  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

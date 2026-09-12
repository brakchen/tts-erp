/**
 * intercept-configs.js — 拦截配置管理页面行为
 *
 * 功能：
 * 1. 配置列表展示（表格 + 筛选 + 分页）
 * 2. 新增/编辑配置弹窗（模态框 + 表单验证）
 * 3. 批量操作（启用/禁用/删除）
 * 4. 导入/导出配置
 */

(function () {
  'use strict';

  // ---------- CONFIG ----------
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, '');
  if (!/^\/[a-z0-9\/_-]*$/i.test(PREFIX)) PREFIX = '';
  var API = PREFIX;
  const HEADERS = { 'Content-Type': 'application/json' };

  // ---------- STATE ----------
  let configs = [];
  let total = 0;
  let offset = 0;
  let limit = 50;
  let filterDomain = '';
  let filterEnabled = '';
  let filterSearch = '';
  let selectedIds = new Set();
  let editingId = null;

  // ---------- DOM REFS ----------
  const $identity = document.getElementById('ops-identity');
  const $tbody = document.getElementById('config-tbody');
  const $total = document.getElementById('config-total');
  const $pagerLabel = document.getElementById('pager-label');
  const $btnPrev = document.getElementById('btn-prev');
  const $btnNext = document.getElementById('btn-next');
  const $btnAdd = document.getElementById('btn-add');
  const $btnImport = document.getElementById('btn-import');
  const $btnExport = document.getElementById('btn-export');
  const $btnBatchEnable = document.getElementById('btn-batch-enable');
  const $btnBatchDisable = document.getElementById('btn-batch-disable');
  const $btnBatchDelete = document.getElementById('btn-batch-delete');
  const $selectAll = document.getElementById('select-all');
  const $filterDomain = document.getElementById('filter-domain');
  const $filterEnabled = document.getElementById('filter-enabled');
  const $filterSearch = document.getElementById('filter-search');
  const $btnQuery = document.getElementById('btn-query');
  const $modal = document.getElementById('config-modal');
  const $modalTitle = document.getElementById('modal-title');
  const $form = document.getElementById('config-form');
  const $formDomain = document.getElementById('form-domain');
  const $formEndpoint = document.getElementById('form-endpoint');
  const $formDescription = document.getElementById('form-description');
  const $formTags = document.getElementById('form-tags');
  const $formCaptureHeaders = document.getElementById('form-capture-headers');
  const $formCaptureBody = document.getElementById('form-capture-body');
  const $formEnabled = document.getElementById('form-enabled');
  const $formError = document.getElementById('form-error');
  const $btnSave = document.getElementById('btn-save');
  const $btnCancel = document.getElementById('btn-cancel');
  const $btnModalClose = document.getElementById('btn-modal-close');

  // ---------- INIT ----------
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    await checkAuth();
    bindEvents();
    await loadConfigs();
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
    if ($btnAdd) $btnAdd.addEventListener('click', onAdd);
    if ($btnImport) $btnImport.addEventListener('click', onImport);
    if ($btnExport) $btnExport.addEventListener('click', onExport);
    if ($btnBatchEnable) $btnBatchEnable.addEventListener('click', () => onBatch('enable'));
    if ($btnBatchDisable) $btnBatchDisable.addEventListener('click', () => onBatch('disable'));
    if ($btnBatchDelete) $btnBatchDelete.addEventListener('click', () => onBatch('delete'));
    if ($selectAll) $selectAll.addEventListener('change', onSelectAll);
    if ($btnSave) $btnSave.addEventListener('click', onSave);
    if ($btnCancel) $btnCancel.addEventListener('click', hideModal);
    if ($btnModalClose) $btnModalClose.addEventListener('click', hideModal);
    if ($modal) {
      $modal.addEventListener('click', (e) => {
        if (e.target === $modal) hideModal();
      });
    }
    if ($filterSearch) {
      $filterSearch.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') onQuery();
      });
    }
  }

  // ---------- LOAD ----------
  async function loadConfigs() {
    try {
      const params = new URLSearchParams();
      if (filterDomain) params.set('domain', filterDomain);
      if (filterEnabled) params.set('enabled', filterEnabled);
      if (filterSearch) params.set('search', filterSearch);
      params.set('limit', limit);
      params.set('offset', offset);

      const res = await fetch(`${API}/v2/intercept/configs?${params}`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        renderError(err.detail || `加载失败 (${res.status})`);
        return;
      }
      const data = await res.json();
      configs = data.configs || [];
      total = data.total || 0;
      selectedIds.clear();
      renderTable();
      renderPager();
    } catch (e) {
      console.error('Failed to load configs:', e);
      renderError('加载失败，请检查网络');
    }
  }

  // ---------- RENDER ----------
  function renderTable() {
    if (!$tbody) return;

    if (configs.length === 0) {
      $tbody.innerHTML = '<tr><td colspan="7" class="op-empty">暂无配置</td></tr>';
      return;
    }

    const rows = configs.map(c => {
      const checked = selectedIds.has(c.id) ? 'checked' : '';
      const statusBadge = c.enabled
        ? '<span class="badge badge-ok">启用</span>'
        : '<span class="badge badge-disabled">禁用</span>';
      const tags = Array.isArray(c.tags) ? c.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('') : '';
      return `
        <tr data-id="${c.id}">
          <td><input type="checkbox" class="row-select" data-id="${c.id}" ${checked}></td>
          <td class="mono">${esc(String(c.id))}</td>
          <td class="mono">${esc(c.domain)}</td>
          <td class="mono">${esc(c.endpoint)}</td>
          <td>${statusBadge}</td>
          <td>${tags}</td>
          <td class="actions">
            <button class="btn-icon" data-action="edit" data-id="${c.id}" title="编辑">✏️</button>
            <button class="btn-icon" data-action="delete" data-id="${c.id}" title="删除">🗑️</button>
          </td>
        </tr>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $tbody.innerHTML = rows;

    // bind row events
    $tbody.querySelectorAll('.row-select').forEach(el => {
      el.addEventListener('change', onRowSelect);
    });
    $tbody.querySelectorAll('[data-action="edit"]').forEach(el => {
      el.addEventListener('click', () => onEdit(Number(el.dataset.id)));
    });
    $tbody.querySelectorAll('[data-action="delete"]').forEach(el => {
      el.addEventListener('click', () => onDelete(Number(el.dataset.id)));
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
    $tbody.innerHTML = `<tr><td colspan="7" class="op-error">${esc(msg)}</td></tr>`;
  }

  // ---------- PAGINATION ----------
  function onQuery() {
    filterDomain = $filterDomain ? $filterDomain.value.trim() : '';
    filterEnabled = $filterEnabled ? $filterEnabled.value : '';
    filterSearch = $filterSearch ? $filterSearch.value.trim() : '';
    offset = 0;
    loadConfigs();
  }

  function onPrev() {
    if (offset <= 0) return;
    offset = Math.max(0, offset - limit);
    loadConfigs();
  }

  function onNext() {
    if (offset + limit >= total) return;
    offset += limit;
    loadConfigs();
  }

  // ---------- SELECTION ----------
  function onSelectAll() {
    const checked = $selectAll ? $selectAll.checked : false;
    configs.forEach(c => {
      if (checked) selectedIds.add(c.id);
      else selectedIds.delete(c.id);
    });
    $tbody.querySelectorAll('.row-select').forEach(el => {
      el.checked = checked;
    });
  }

  function onRowSelect(e) {
    const id = Number(e.target.dataset.id);
    if (e.target.checked) selectedIds.add(id);
    else selectedIds.delete(id);
    updateSelectAllState();
  }

  function updateSelectAllState() {
    if (!$selectAll || configs.length === 0) return;
    $selectAll.checked = configs.every(c => selectedIds.has(c.id));
  }

  // ---------- MODAL ----------
  function showModal(title) {
    if ($modalTitle) $modalTitle.textContent = title;
    if ($modal) $modal.classList.add('is-open');
    if ($formError) $formError.textContent = '';
  }

  function hideModal() {
    if ($modal) $modal.classList.remove('is-open');
    editingId = null;
    if ($form) $form.reset();
    if ($formError) $formError.textContent = '';
  }

  function populateForm(config) {
    if ($formDomain) $formDomain.value = config.domain || '';
    if ($formEndpoint) $formEndpoint.value = config.endpoint || '';
    if ($formDescription) $formDescription.value = config.description || '';
    if ($formTags) $formTags.value = Array.isArray(config.tags) ? config.tags.join(', ') : '';
    if ($formCaptureHeaders) $formCaptureHeaders.checked = config.capture_headers !== false;
    if ($formCaptureBody) $formCaptureBody.checked = config.capture_body !== false;
    if ($formEnabled) $formEnabled.checked = config.enabled !== false;
  }

  // ---------- ADD / EDIT ----------
  function onAdd() {
    editingId = null;
    populateForm({ capture_headers: true, capture_body: true, enabled: true });
    showModal('新增拦截配置');
  }

  async function onEdit(id) {
    try {
      const res = await fetch(`${API}/v2/intercept/configs/${id}`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `加载配置失败 (${res.status})`);
        return;
      }
      const config = await res.json();
      editingId = id;
      populateForm(config);
      showModal('编辑拦截配置');
    } catch (e) {
      console.error('Failed to load config:', e);
      alert('加载配置失败');
    }
  }

  async function onSave() {
    const domain = $formDomain ? $formDomain.value.trim() : '';
    const endpoint = $formEndpoint ? $formEndpoint.value.trim() : '';

    // validation
    if (!domain) {
      showFormError('请输入域名');
      return;
    }
    if (!endpoint) {
      showFormError('请输入 Endpoint');
      return;
    }

    const body = {
      domain,
      endpoint,
      description: $formDescription ? $formDescription.value.trim() : null,
      tags: $formTags ? $formTags.value.split(',').map(s => s.trim()).filter(Boolean) : [],
      capture_headers: $formCaptureHeaders ? $formCaptureHeaders.checked : true,
      capture_body: $formCaptureBody ? $formCaptureBody.checked : true,
      enabled: $formEnabled ? $formEnabled.checked : true,
    };

    try {
      const url = editingId
        ? `${API}/v2/intercept/configs/${editingId}`
        : `${API}/v2/intercept/configs`;
      const method = editingId ? 'PUT' : 'POST';

      const res = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: HEADERS,
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showFormError(err.detail || `保存失败 (${res.status})`);
        return;
      }

      hideModal();
      await loadConfigs();
    } catch (e) {
      console.error('Failed to save config:', e);
      showFormError('保存失败，请检查网络');
    }
  }

  function showFormError(msg) {
    if ($formError) $formError.textContent = msg;
  }

  // ---------- DELETE ----------
  async function onDelete(id) {
    if (!confirm('确定删除该配置？')) return;

    try {
      const res = await fetch(`${API}/v2/intercept/configs/${id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `删除失败 (${res.status})`);
        return;
      }
      await loadConfigs();
    } catch (e) {
      console.error('Failed to delete config:', e);
      alert('删除失败');
    }
  }

  // ---------- BATCH ----------
  async function onBatch(action) {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) {
      alert('请先选择配置');
      return;
    }

    const actionLabel = { enable: '启用', disable: '禁用', delete: '删除' }[action];
    if (!confirm(`确定批量${actionLabel} ${ids.length} 条配置？`)) return;

    try {
      const res = await fetch(`${API}/v2/intercept/configs/batch`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: HEADERS,
        body: JSON.stringify({ action, ids }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `批量${actionLabel}失败 (${res.status})`);
        return;
      }
      await loadConfigs();
    } catch (e) {
      console.error(`Failed to batch ${action}:`, e);
      alert(`批量${actionLabel}失败`);
    }
  }

  // ---------- IMPORT / EXPORT ----------
  async function onImport() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;

      try {
        const text = await file.text();
        const data = JSON.parse(text);
        const configs = Array.isArray(data) ? data : data.configs;
        if (!Array.isArray(configs)) {
          alert('无效的 JSON 格式，期望数组或 { configs: [...] }');
          return;
        }

        const res = await fetch(`${API}/v2/intercept/configs/import`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: HEADERS,
          body: JSON.stringify({ configs }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          alert(err.detail || `导入失败 (${res.status})`);
          return;
        }

        const result = await res.json();
        alert(`导入完成：成功 ${result.imported} 条，跳过 ${result.skipped} 条`);
        await loadConfigs();
      } catch (e) {
        console.error('Failed to import:', e);
        alert('导入失败，请检查 JSON 格式');
      }
    };
    input.click();
  }

  async function onExport() {
    try {
      const res = await fetch(`${API}/v2/intercept/configs/export`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `导出失败 (${res.status})`);
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `intercept-configs-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Failed to export:', e);
      alert('导出失败');
    }
  }

  // ---------- UTILS ----------
  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

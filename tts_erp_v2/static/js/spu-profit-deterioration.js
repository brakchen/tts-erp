/* PRD v3 read-only comparison. Backend owns scope, periods, facts and totals.
 * Only displayChange derives non-authoritative presentation differences.
 */
(() => {
  'use strict';
  const markerAt = location.pathname.indexOf('/v2/');
  const rootPrefix = markerAt >= 0 ? location.pathname.slice(0, markerAt) : '';
  const API = `${rootPrefix}/v2`;
  const DATA_PATH = '/analytics/spu-profit-deterioration';
  const $ = (id) => document.getElementById(id);
  const state = {
    params: new URLSearchParams(location.search), shops: null, version: 0,
    payload: null, latestDate: '', latestShop: '', trigger: null,
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
    (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const raw = (key, fallback = '') => state.params.has(key) ? state.params.get(key) : fallback;
  const present = (value) => value !== null && value !== undefined && value !== '';
  const text = (value) => present(value) ? esc(value) : '—';

  async function get(path) {
    const response = await fetch(`${API}${path}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body?.detail;
      const info = detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : body;
      const message = typeof detail === 'string' ? detail : info?.message;
      const error = new Error(message || `HTTP ${response.status}，请修正条件或重试`);
      error.status = response.status;
      error.requestId = info?.requestId || body?.requestId || response.headers.get('x-request-id');
      error.latestAvailableDate = info?.latestAvailableDate || body?.latestAvailableDate;
      throw error;
    }
    return body;
  }

  function status(kind, message) {
    $('alert-status').dataset.kind = kind;
    $('alert-status').textContent = message;
  }

  function closeSummary(restoreFocus = false) {
    $('alert-summary').hidden = true;
    state.trigger?.setAttribute('aria-expanded', 'false');
    $('alert-summary-body').replaceChildren();
    if (restoreFocus && state.trigger?.isConnected) state.trigger.focus();
    state.trigger = null;
  }

  function clearResults() {
    state.payload = null;
    closeSummary();
    $('alert-rows').replaceChildren();
    $('alert-cards').replaceChildren();
    $('comparison-warnings').replaceChildren();
    for (const id of ['total-spus', 'total-current-orders', 'total-previous-orders']) $(id).textContent = '—';
    $('totals-note').textContent = '统计等待当前条件读取；不是行级订单相加。';
    $('filter-echo').textContent = '当前输入尚未应用，等待确认范围与两段日期。';
    $('alert-freshness').textContent = '等待当前条件的实际数据日期…';
    $('alert-freshness').dataset.kind = 'loading';
    $('alert-pager-label').textContent = '—';
    $('alert-pager-prev').disabled = true;
    $('alert-pager-next').disabled = true;
  }

  function syncUrl() {
    history.replaceState(null, '', `${location.pathname}?${state.params.toString()}`);
  }

  function syncControls() {
    const shop = raw('shop_pk');
    $('filter-shop').replaceChildren();
    for (const option of state.shops || []) {
      $('filter-shop').add(new Option(option.account_name || option.shop_id || String(option.id), String(option.id)));
    }
    if (shop && ![...$('filter-shop').options].some((option) => option.value === shop)) {
      $('filter-shop').add(new Option(`请求的店铺 ${shop}（不可用或待确认）`, shop));
    }
    if (shop) $('filter-shop').value = shop;
    $('filter-spu-ids').value = state.params.getAll('spu_ids').join(',');
    $('filter-activity').checked = raw('activity', 'recent') === 'recent';
    document.querySelectorAll('[data-window-days]').forEach((button) => {
      button.setAttribute('aria-pressed', String(raw('window_days', '7') === button.dataset.windowDays));
    });
    // Date controls sanitize malformed strings. Keep invalid URL input editable, not silently blank.
    const date = raw('anchor_date');
    $('filter-anchor-date').type = !date || validDate(date) ? 'date' : 'text';
    $('filter-anchor-date').value = date;
    const limit = raw('limit', '100');
    if (![...$('filter-limit').options].some((option) => option.value === limit)) {
      $('filter-limit').add(new Option(limit, limit));
    }
    $('filter-limit').value = limit;
  }

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }

  function validationError() {
    const params = state.params;
    for (const key of ['shop_pk', 'window_days', 'activity', 'anchor_date', 'limit', 'offset']) {
      if (params.getAll(key).length > 1) return `${key} 只能指定一次，请修正链接。`;
    }
    const retired = ['layer', 'sample', 'severity', 'state'].filter((key) => params.has(key));
    if (retired.length) return `不支持旧告警筛选 ${retired.join(', ')}，请从链接移除。`;
    if (params.has('shop_pk') && !/^[1-9]\d*$/.test(raw('shop_pk'))) return '店铺必须是正整数，请修正链接。';
    if (!['3', '7'].includes(raw('window_days', '7'))) return `比较窗口 ${raw('window_days')} 无效，仅支持 7 / 3 天，请重新选择。`;
    if (!['recent', 'all'].includes(raw('activity', 'recent'))) return `活动范围 ${raw('activity')} 无效，请重新选择。`;
    if (params.has('spu_ids')) {
      const ids = params.getAll('spu_ids').join(',').split(',').map((id) => id.trim());
      if (ids.length > 100 || ids.some((id) => !/^[1-9]\d*$/.test(id))) {
        return 'SPU 输入无效：最多 100 个正整数内部编号，逗号分隔；未查询整店，请保留输入并修正。';
      }
    }
    if (params.has('anchor_date') && !validDate(raw('anchor_date'))) return `截至日 ${raw('anchor_date')} 无效，请输入当地 ISO 日期。`;
    if (state.latestShop === raw('shop_pk') && state.latestDate && raw('anchor_date') > state.latestDate) {
      return `日期超过最新可用日期 ${state.latestDate}，请修正后重试。`;
    }
    if (!/^[1-9]\d*$/.test(raw('limit', '100')) || Number(raw('limit', '100')) > 500) return '每页条数参数应为 1–500，请修正链接。';
    if (!/^\d+$/.test(raw('offset', '0')) || !Number.isSafeInteger(Number(raw('offset', '0')))) return '分页 offset 参数无效，请修正链接。';
    return '';
  }

  function dateBound(value) {
    if (!validDate(value || '')) return;
    state.latestDate = value;
    state.latestShop = raw('shop_pk');
    $('filter-anchor-date').max = value;
    $('date-limit-note').textContent = `最新可用日期：${value}（店铺当地完整日）；可查更早历史日期，刷新只重新读取。`;
  }

  function errorView(error) {
    if (error.latestAvailableDate) dateBound(error.latestAvailableDate);
    const label = { 401: '未登录，请登录后重试', 403: '无权限读取此范围', 422: '参数或日期错误', 503: '数据不可用' }[error.status] || '读取失败';
    status('error', `${label}：${error.message}。${error.requestId ? `请求编号：${error.requestId}。` : ''}请调整条件或刷新重试。`);
    $('filter-spu-feedback').textContent = error.message;
    $('alert-freshness').textContent = '当前条件未取得可用结果；不展示旧条件数据。';
    $('alert-freshness').dataset.kind = 'error';
  }

  function queryString() {
    const query = new URLSearchParams();
    for (const key of ['shop_pk', 'window_days', 'activity', 'anchor_date', 'limit', 'offset']) {
      if (state.params.has(key)) query.set(key, raw(key));
    }
    query.set('window_days', raw('window_days', '7'));
    query.set('activity', raw('activity', 'recent'));
    query.set('limit', raw('limit', '100'));
    query.set('offset', raw('offset', '0'));
    if (state.params.has('spu_ids')) {
      for (const id of state.params.getAll('spu_ids').join(',').split(',')) query.append('spu_ids', id.trim());
    }
    return query.toString();
  }

  function validPayload(payload) {
    const count = (value) => Number.isSafeInteger(value) && value >= 0;
    const nullableCount = (value) => value === null || count(value);
    const nullableDecimal = (value) => value === null || (typeof value === 'string' && /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value));
    const strings = (value) => Array.isArray(value) && value.every((entry) => typeof entry === 'string');
    const { items, total, totals, meta } = payload || {};
    if (!Array.isArray(items) || !count(total) || !totals || !meta) return false;
    if (!['analysisSpuCount', 'previousOrderCount', 'currentOrderCount'].every((key) => nullableCount(totals[key]))) return false;
    if (!validDate(meta.anchorDate) || !validDate(meta.latestAvailableDate) || !validDate(meta.dataAsOf)
      || ![3, 7].includes(meta.windowDays) || typeof meta.stale !== 'boolean' || !strings(meta.warnings)) return false;
    if (!['current', 'previous'].every((key) => validDate(meta.periods?.[key]?.startDate) && validDate(meta.periods?.[key]?.endDate))) return false;
    return items.every((item) => item && count(item.shopPk) && item.shopPk > 0 && count(item.spuPk) && item.spuPk > 0
      && typeof item.spuId === 'string' && strings(item.warnings)
      && ['previous', 'current'].every((period) => nullableCount(item[`${period}OrderCount`])
        && (item[`${period}NoOrderAdSpend`] === null || typeof item[`${period}NoOrderAdSpend`] === 'boolean')
        && ['Roi', 'NetProfitCny', 'SpendCny'].every((key) => nullableDecimal(item[`${period}${key}`]))));
  }

  async function load() {
    // One generation covers shops, date/default initialization, data, JSON and errors.
    const version = ++state.version;
    clearResults();
    status('loading', '正在加载当前范围…');
    $('filter-spu-feedback').textContent = '';
    syncControls();
    const invalid = validationError();
    if (invalid) { errorView(new Error(invalid)); return; }
    try {
      if (!state.shops) {
        const shops = await get('/commerce/channel-accounts?platform=tiktok&limit=500');
        if (version !== state.version) return;
        if (!Array.isArray(shops) || !shops.length) throw new Error('没有可用店铺，请确认访问权限；未自动切换店铺');
        state.shops = shops;
      }
      if (!state.params.has('shop_pk')) state.params.set('shop_pk', String(state.shops[0].id));
      syncControls();
      if (!state.shops.some((shop) => String(shop.id) === raw('shop_pk'))) {
        throw new Error(`请求的店铺 ${raw('shop_pk')} 不可用或无权限，请选择店铺；未自动切换`);
      }
      const payload = await get(`${DATA_PATH}?${queryString()}`);
      if (version !== state.version) return;
      if (!validPayload(payload)) {
        throw new Error('响应缺少有效对比事实、统计或日期依据，数据不可用');
      }
      state.payload = payload;
      dateBound(payload.meta.latestAvailableDate);
      if (!state.params.has('anchor_date')) state.params.set('anchor_date', payload.meta.anchorDate);
      if (!state.params.has('window_days')) state.params.set('window_days', '7');
      if (!state.params.has('activity')) state.params.set('activity', 'recent');
      syncControls();
      syncUrl();
      render(payload);
    } catch (error) {
      if (version !== state.version) return;
      clearResults();
      errorView(error);
    }
  }

  function displayNumber(value) {
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4, useGrouping: false }).format(value);
  }

  // 展示值 only: never used for scope, facts, totals, eligibility or persistence.
  function displayChange(previous, current, unit = '') {
    if (!present(previous) || !present(current)) return { difference: '—', percent: '—', direction: '不可比较' };
    const before = Number(previous), after = Number(current);
    if (!Number.isFinite(before) || !Number.isFinite(after)) return { difference: '—', percent: '—', direction: '不可比较' };
    const delta = after - before;
    const signed = (value) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${displayNumber(Math.abs(value))}`;
    return {
      difference: `${signed(delta)}${unit ? ` ${unit}` : ''}`,
      percent: before > 0 ? `${signed(delta / before * 100)}%` : '—',
      direction: delta > 0 ? '↑ 上升' : delta < 0 ? '↓ 下降' : '→ 持平',
    };
  }

  function metric(item, previousKey, currentKey, label, displayKey, unit = '') {
    const previousReason = previousKey === 'previousRoi' ? item.previousRoiUnavailableReason : null;
    const currentReason = currentKey === 'currentRoi' ? item.currentRoiUnavailableReason : null;
    let html = `<div class="metric"><strong>${esc(label)}</strong>
      <div>上期 <span class="mono" data-value="${previousKey}">${text(item[previousKey])}</span>${!present(item[previousKey]) ? `<small>${esc(previousReason || `上期${label}事实缺失`)}</small>` : ''}</div>
      <div>本期 <span class="mono" data-value="${currentKey}">${text(item[currentKey])}</span>${!present(item[currentKey]) ? `<small>${esc(currentReason || `本期${label}事实缺失`)}</small>` : ''}</div>`;
    if (displayKey) {
      const change = displayChange(item[previousKey], item[currentKey], unit);
      html += `<div class="display-change">${change.direction} · 展示差额 <span data-display="${displayKey}-difference">${change.difference}</span>
        <br>展示变化率 <span data-display="${displayKey}-percent">${change.percent}</span><small>仅上期大于 0 时计算变化率</small></div>`;
    }
    return `${html}</div>`;
  }

  function facts(item) {
    const notes = [];
    for (const [key, label] of [['previous', '上期'], ['current', '本期']]) {
      if (item[`${key}NoOrderAdSpend`] === true) notes.push(`<strong class="no-order-fact">${label}：无单但有广告费</strong>`);
      if (item[`${key}NoOrderAdSpend`] === null) notes.push(`<p>${label}无单投放事实未知，不能以缺失冒充零。</p>`);
    }
    for (const warning of item.warnings || []) notes.push(`<p>${esc(warning)}</p>`);
    notes.push(`<small>计算时间：${text(item.basisCalculatedAt)}（不是数据更新日期）</small>`);
    return notes.join('');
  }

  function periods(meta) {
    const range = (period) => `${period.startDate} 至 ${period.endDate}`;
    return `本期 ${range(meta.periods.current)}；上期 ${range(meta.periods.previous)}（含首尾，${meta.reportingTimezone || '时区未提供'}）`;
  }

  function detailHref(item) {
    // Consume the producer-owned complete target; do not reconstruct IDs/date semantics.
    const supplied = item.drilldown?.pageUrl;
    if (typeof supplied !== 'string' || !supplied.startsWith('/v2/pages/') || supplied.includes('\\')) return null;
    return `${rootPrefix}${supplied}`;
  }

  function detailLabel(meta) {
    const current = meta.periods.current;
    return `本期盈利详情（${current.startDate} 至 ${current.endDate}）`;
  }

  function identity(item, index, meta) {
    const href = detailHref(item);
    return `<strong>${text(item.spuId)}</strong><small>内部编号 ${text(item.spuPk)} · 店铺 ${text(item.shopPk)}</small>
      <button type="button" class="btn-secondary summary-trigger" data-index="${index}" aria-expanded="false" aria-controls="alert-summary">查看对比明细</button>
      ${href ? `<a class="profitability-link" href="${esc(href)}">${esc(detailLabel(meta))}</a>` : '<p>盈利详情链接不可用</p>'}`;
  }

  function metrics(item) {
    return [
      metric(item, 'previousRoi', 'currentRoi', 'ROI（比率）', 'roi'),
      metric(item, 'previousNetProfitCny', 'currentNetProfitCny', '净利润（CNY）', 'profit', 'CNY'),
      metric(item, 'previousOrderCount', 'currentOrderCount', '订单', null),
      metric(item, 'previousSpendCny', 'currentSpendCny', '广告消耗（CNY）', null),
    ];
  }

  function render(payload) {
    const { items, totals, meta } = payload;
    $('filter-echo').textContent = `${meta.scopeDescription || '后台未提供范围说明'}${meta.activityBypassed ? '；精确调查：活动排除不生效' : ''}。${periods(meta)}`;
    $('window-note').textContent = meta.windowDays === 3
      ? '3 天用于观察近期变化，易受少量订单和投放节奏影响，有短期波动；不单独代表持续改善或恶化。'
      : '7 天为主参考，覆盖完整星期，但累计订单不等于两期样本充分；3 天与 7 天方向可不同。';
    $('alert-freshness').textContent = `${meta.stale ? '数据未更新 · ' : ''}实际数据日期：${meta.dataAsOf || '未提供'}；计算时间：${meta.calculatedAt || '未提供'}（不代表数据更新）。`;
    $('alert-freshness').dataset.kind = meta.stale ? 'stale' : 'ready';
    $('comparison-warnings').replaceChildren(...(meta.warnings || []).map((warning) => {
      const paragraph = document.createElement('p');
      paragraph.textContent = warning;
      return paragraph;
    }));
    $('total-spus').textContent = present(totals.analysisSpuCount) ? String(totals.analysisSpuCount) : '—';
    $('total-current-orders').textContent = present(totals.currentOrderCount) ? String(totals.currentOrderCount) : '—';
    $('total-previous-orders').textContent = present(totals.previousOrderCount) ? String(totals.previousOrderCount) : '—';
    $('totals-note').textContent = totals.unavailableReason || '完整范围：SPU 去重，每期订单跨 SPU 全局去重；不是当前页或行级订单相加。';
    // Static markup only; all wire strings/attributes pass through esc().
    // pi-lens-ignore: no-inner-html-js
    $('alert-rows').innerHTML = items.map((item, index) => `<tr data-spu-pk="${esc(item.spuPk)}"><td>${identity(item, index, meta)}</td>${metrics(item).map((html) => `<td>${html}</td>`).join('')}<td>${facts(item)}</td></tr>`).join('');
    // Same escaped comparison renderer as desktop, no untrusted HTML fields.
    // pi-lens-ignore: no-inner-html-js
    $('alert-cards').innerHTML = items.map((item, index) => `<article class="comparison-card" data-spu-pk="${esc(item.spuPk)}"><div>${identity(item, index, meta)}</div><div class="card-metrics">${metrics(item).join('')}</div><div>${facts(item)}</div></article>`).join('');
    const offset = Number(raw('offset', '0')), limit = Number(raw('limit', '100'));
    $('alert-pager-label').textContent = `第 ${Math.floor(offset / limit) + 1} 页 · ${payload.total} 条对比记录（不是订单数）`;
    $('alert-pager-prev').disabled = offset <= 0;
    $('alert-pager-next').disabled = offset + limit >= payload.total;
    status(items.length ? 'ready' : 'empty', items.length ? `已读取 ${items.length} 条 SPU 对比；变化为展示值，请结合订单、消耗和数据限制。` : '当前条件无匹配 SPU，请调整范围或日期；不代表全店正常或无风险。');
  }

  function showSummary(trigger) {
    const item = state.payload?.items[Number(trigger.dataset.index)];
    if (!item) return;
    closeSummary();
    state.trigger = trigger;
    trigger.setAttribute('aria-expanded', 'true');
    $('alert-summary-title').textContent = `SPU ${item.spuId} 对比明细`;
    const href = detailHref(item);
    // All wire values and the validated same-origin page URL are escaped.
    // pi-lens-ignore: no-inner-html-js
    $('alert-summary-body').innerHTML = `<p>店铺 ${text(item.shopPk)} · 内部编号 ${text(item.spuPk)} · ${esc(periods(state.payload.meta))}</p>
      <div class="card-metrics">${metrics(item).join('')}</div>${facts(item)}
      ${href ? `<a class="profitability-link" href="${esc(href)}">${esc(detailLabel(state.payload.meta))}；可通过浏览器返回原查询</a>` : '<p>盈利详情链接不可用</p>'}`;
    $('alert-summary').hidden = false;
    $('alert-summary-title').focus();
  }

  function change(key, value, removeEmpty = false, resetPage = true) {
    if (removeEmpty && value === '') state.params.delete(key);
    else state.params.set(key, value);
    if (resetPage) state.params.set('offset', '0');
    if (key === 'shop_pk') {
      state.latestDate = ''; state.latestShop = '';
      $('filter-anchor-date').removeAttribute('max');
      $('date-limit-note').textContent = '等待后台确认此店铺的最新可用日期。';
    }
    syncUrl();
    void load();
  }

  $('filter-shop').addEventListener('change', (event) => change('shop_pk', event.target.value));
  $('filter-spu-ids').addEventListener('change', (event) => change('spu_ids', event.target.value, true));
  $('filter-anchor-date').addEventListener('change', (event) => change('anchor_date', event.target.value, true));
  $('filter-activity').addEventListener('change', (event) => change('activity', event.target.checked ? 'recent' : 'all'));
  $('filter-limit').addEventListener('change', (event) => change('limit', event.target.value));
  document.querySelectorAll('[data-window-days]').forEach((button) => button.addEventListener('click', () => change('window_days', button.dataset.windowDays)));
  $('btn-refresh').addEventListener('click', () => void load());
  $('alert-pager-prev').addEventListener('click', () => change('offset', String(Math.max(0, Number(raw('offset', '0')) - Number(raw('limit', '100')))), false, false));
  $('alert-pager-next').addEventListener('click', () => change('offset', String(Number(raw('offset', '0')) + Number(raw('limit', '100'))), false, false));
  for (const id of ['alert-rows', 'alert-cards']) {
    $(id).addEventListener('click', (event) => {
      const trigger = event.target.closest('button.summary-trigger');
      if (trigger) showSummary(trigger);
    });
  }
  $('btn-summary-close').addEventListener('click', () => {
    state.trigger?.setAttribute('aria-expanded', 'false');
    closeSummary(true);
  });
  $('alert-summary').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      state.trigger?.setAttribute('aria-expanded', 'false');
      closeSummary(true);
    }
  });
  syncControls();
  void load();
})();

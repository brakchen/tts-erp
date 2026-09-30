(function () {
  'use strict';

  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, '');
  if (!/^\/[a-z0-9\/_-]*$/i.test(PREFIX)) PREFIX = '';

  const LIST_URL = `${PREFIX}/v2/reporting/ad-daily`;
  const OPTIONS_URL = `${LIST_URL}/options`;
  const ALLOWED_LIMITS = new Set([25, 50, 100, 200]);
  const numberFormat = new Intl.NumberFormat('en-US');
  const moneyFormat = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
  const roiFormat = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });

  const elements = {};
  let filterOptions = { sellers: [], advertisers: [], endpoints: [] };
  let state = readState();
  let requestController = null;

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    bindElements();
    bindEvents();
    applyStateToControls();
    await loadOptions();
    await loadRows();
  }

  function bindElements() {
    elements.form = document.getElementById('filters');
    elements.seller = document.getElementById('seller-filter');
    elements.advertiser = document.getElementById('advertiser-filter');
    elements.endpoint = document.getElementById('endpoint-filter');
    elements.dayFrom = document.getElementById('day-from');
    elements.dayTo = document.getElementById('day-to');
    elements.query = document.getElementById('query-filter');
    elements.limit = document.getElementById('page-size');
    elements.reset = document.getElementById('reset-filters');
    elements.body = document.getElementById('ledger-body');
    elements.empty = document.getElementById('empty-state');
    elements.status = document.getElementById('load-status');
    elements.range = document.getElementById('observed-range');
    elements.pageRange = document.getElementById('page-range');
    elements.prev = document.getElementById('prev-page');
    elements.next = document.getElementById('next-page');
    elements.sumRows = document.getElementById('sum-rows');
    elements.sumSpend = document.getElementById('sum-spend');
    elements.sumGmv = document.getElementById('sum-gmv');
    elements.sumOrders = document.getElementById('sum-orders');
    elements.sumRoi = document.getElementById('sum-roi');
  }

  function bindEvents() {
    elements.form.addEventListener('submit', function (event) {
      event.preventDefault();
      state = readControls();
      state.offset = 0;
      void loadRows();
    });
    elements.reset.addEventListener('click', function () {
      state = { seller_id: '', advertiser_id: '', endpoint: '', day_from: '', day_to: '', q: '', limit: 50, offset: 0 };
      applyStateToControls();
      populateAdvertisers();
      void loadRows();
    });
    elements.seller.addEventListener('change', function () {
      const previous = elements.advertiser.value;
      populateAdvertisers(previous);
    });
    elements.prev.addEventListener('click', function () {
      state.offset = Math.max(0, state.offset - state.limit);
      void loadRows();
    });
    elements.next.addEventListener('click', function () {
      state.offset += state.limit;
      void loadRows();
    });
  }

  function readState() {
    const params = new URLSearchParams(location.search);
    const limit = Number(params.get('limit'));
    const offset = Number(params.get('offset'));
    return {
      seller_id: params.get('seller_id') || '',
      advertiser_id: params.get('advertiser_id') || '',
      endpoint: params.get('endpoint') || '',
      day_from: params.get('day_from') || '',
      day_to: params.get('day_to') || '',
      q: params.get('q') || '',
      limit: ALLOWED_LIMITS.has(limit) ? limit : 50,
      offset: Number.isInteger(offset) && offset >= 0 ? offset : 0,
    };
  }

  function readControls() {
    return {
      seller_id: elements.seller.value,
      advertiser_id: elements.advertiser.value,
      endpoint: elements.endpoint.value,
      day_from: elements.dayFrom.value,
      day_to: elements.dayTo.value,
      q: elements.query.value.trim(),
      limit: Number(elements.limit.value),
      offset: state.offset,
    };
  }

  function applyStateToControls() {
    elements.dayFrom.value = state.day_from;
    elements.dayTo.value = state.day_to;
    elements.query.value = state.q;
    elements.limit.value = String(state.limit);
  }

  function syncUrl() {
    const params = new URLSearchParams();
    for (const key of ['seller_id', 'advertiser_id', 'endpoint', 'day_from', 'day_to', 'q']) {
      if (state[key]) params.set(key, state[key]);
    }
    if (state.limit !== 50) params.set('limit', String(state.limit));
    if (state.offset > 0) params.set('offset', String(state.offset));
    const query = params.toString();
    history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}`);
  }

  async function loadOptions() {
    try {
      const response = await fetch(OPTIONS_URL, { credentials: 'same-origin' });
      if (response.status === 401) return redirectToLogin();
      if (!response.ok) throw new Error(`筛选项读取失败 (${response.status})`);
      filterOptions = await response.json();
      populateSellers();
      populateAdvertisers(state.advertiser_id);
      populateEndpoints();
      if (filterOptions.min_day && filterOptions.max_day) {
        elements.range.textContent = `${filterOptions.min_day}  —  ${filterOptions.max_day}`;
        elements.dayFrom.min = filterOptions.min_day;
        elements.dayFrom.max = filterOptions.max_day;
        elements.dayTo.min = filterOptions.min_day;
        elements.dayTo.max = filterOptions.max_day;
      } else {
        elements.range.textContent = '暂无广告日数据';
      }
    } catch (error) {
      elements.range.textContent = '日期范围读取失败';
      setStatus(error.message || '筛选项读取失败', 'error');
    }
  }

  function populateSellers() {
    const fragment = document.createDocumentFragment();
    fragment.append(new Option('全部店铺', ''));
    for (const seller of filterOptions.sellers || []) {
      const label = seller.shop_name
        ? `${seller.shop_name} · ${seller.seller_id}`
        : seller.seller_id;
      fragment.append(new Option(`${label} (${numberFormat.format(seller.row_count)})`, seller.seller_id));
    }
    elements.seller.replaceChildren(fragment);
    elements.seller.value = state.seller_id;
  }

  function populateAdvertisers(preferred) {
    const sellerId = elements.seller.value || state.seller_id;
    const candidates = (filterOptions.advertisers || []).filter(function (item) {
      return !sellerId || item.seller_id === sellerId;
    });
    const fragment = document.createDocumentFragment();
    fragment.append(new Option('全部账户', ''));
    for (const item of candidates) {
      const suffix = sellerId ? '' : ` · ${item.seller_id}`;
      fragment.append(new Option(`${item.advertiser_id}${suffix} (${numberFormat.format(item.row_count)})`, item.advertiser_id));
    }
    elements.advertiser.replaceChildren(fragment);
    const wanted = preferred || state.advertiser_id;
    elements.advertiser.value = candidates.some((item) => item.advertiser_id === wanted) ? wanted : '';
  }

  function populateEndpoints() {
    const fragment = document.createDocumentFragment();
    fragment.append(new Option('全部接口', ''));
    for (const item of filterOptions.endpoints || []) {
      const segments = item.endpoint.split('/').filter(Boolean);
      const shortName = segments[segments.length - 1] || item.endpoint;
      fragment.append(new Option(`${shortName} (${numberFormat.format(item.row_count)})`, item.endpoint));
    }
    elements.endpoint.replaceChildren(fragment);
    elements.endpoint.value = state.endpoint;
  }

  async function loadRows() {
    if (state.day_from && state.day_to && state.day_from > state.day_to) {
      setStatus('开始日期不能晚于结束日期', 'error');
      return;
    }
    if (requestController) requestController.abort();
    requestController = new AbortController();
    syncUrl();
    setLoading(true);

    const params = new URLSearchParams();
    for (const key of ['seller_id', 'advertiser_id', 'endpoint', 'day_from', 'day_to', 'q']) {
      if (state[key]) params.set(key, state[key]);
    }
    params.set('limit', String(state.limit));
    params.set('offset', String(state.offset));

    try {
      const response = await fetch(`${LIST_URL}?${params}`, {
        credentials: 'same-origin',
        signal: requestController.signal,
      });
      if (response.status === 401) return redirectToLogin();
      if (!response.ok) {
        let message = `广告明细读取失败 (${response.status})`;
        try {
          const payload = await response.json();
          if (payload.detail) message = typeof payload.detail === 'string' ? payload.detail : message;
        } catch (_) { /* response is not JSON */ }
        throw new Error(message);
      }
      const payload = await response.json();
      if (state.offset > 0 && payload.total > 0 && state.offset >= payload.total) {
        state.offset = Math.floor((payload.total - 1) / state.limit) * state.limit;
        return loadRows();
      }
      renderSummary(payload.summary);
      renderRows(payload.items);
      renderPagination(payload.total, payload.items.length);
      setStatus(`已读取 ${numberFormat.format(payload.items.length)} 行`, 'ok');
    } catch (error) {
      if (error.name === 'AbortError') return;
      elements.body.replaceChildren();
      elements.empty.hidden = false;
      elements.pageRange.textContent = '读取失败';
      setStatus(error.message || '广告明细读取失败', 'error');
    } finally {
      setLoading(false);
    }
  }

  function renderSummary(summary) {
    elements.sumRows.textContent = numberFormat.format(summary.row_count || 0);
    elements.sumSpend.textContent = formatMoney(summary.spend);
    elements.sumGmv.textContent = formatMoney(summary.attributed_gmv);
    elements.sumOrders.textContent = numberFormat.format(summary.attributed_orders || 0);
    elements.sumRoi.textContent = summary.weighted_roi == null ? '—' : `${roiFormat.format(Number(summary.weighted_roi))}×`;
  }

  function renderRows(items) {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const row = document.createElement('tr');
      row.append(
        cell(item.day || '—', 'ad-day'),
        identityCell(item.shop_name || item.seller_id, item.seller_id, item.advertiser_id),
        copyCell(item.campaign_id),
        productCell(item),
        numberCell(formatMoney(item.mixed_real_cost)),
        numberCell(item.onsite_roi2_shopping_sku == null ? '—' : numberFormat.format(item.onsite_roi2_shopping_sku)),
        numberCell(formatMoney(item.onsite_roi2_shopping_value)),
        numberCell(item.onsite_mixed_real_roi2_shopping == null ? '—' : `${roiFormat.format(Number(item.onsite_mixed_real_roi2_shopping))}×`),
        cell(formatTimestamp(item.updated_at), 'ad-updated')
      );

      const detailCell = document.createElement('td');
      const detailButton = document.createElement('button');
      const detailId = `ad-detail-${item.id}`;
      detailButton.type = 'button';
      detailButton.className = 'ad-metric-button';
      detailButton.textContent = '更多';
      detailButton.setAttribute('aria-expanded', 'false');
      detailButton.setAttribute('aria-controls', detailId);
      detailCell.append(detailButton);
      row.append(detailCell);

      const detailRow = buildDetailRow(item, detailId);
      detailButton.addEventListener('click', function () {
        const open = detailRow.hidden;
        detailRow.hidden = !open;
        detailButton.setAttribute('aria-expanded', String(open));
        detailButton.textContent = open ? '收起' : '更多';
      });
      fragment.append(row, detailRow);
    }
    elements.body.replaceChildren(fragment);
    elements.empty.hidden = items.length !== 0;
  }

  function buildDetailRow(item, id) {
    const row = document.createElement('tr');
    row.id = id;
    row.className = 'ad-detail-row';
    row.hidden = true;
    const td = document.createElement('td');
    td.colSpan = 10;

    const panel = document.createElement('div');
    panel.className = 'ad-detail-panel';
    const list = document.createElement('dl');
    addDefinition(list, '数据接口', item.endpoint);
    addDefinition(list, '首次写入', formatTimestamp(item.created_at));
    addDefinition(list, '最近更新', formatTimestamp(item.updated_at));
    addDefinition(list, '记录 ID', String(item.id));
    const pre = document.createElement('pre');
    pre.className = 'ad-json';
    pre.textContent = JSON.stringify(item.metrics_extra || {}, null, 2);
    panel.append(list, pre);
    td.append(panel);
    row.append(td);
    return row;
  }

  function addDefinition(list, term, description) {
    const dt = document.createElement('dt');
    dt.textContent = term;
    const dd = document.createElement('dd');
    dd.textContent = description || '—';
    list.append(dt, dd);
  }

  function identityCell(name, sellerId, advertiserId) {
    const td = document.createElement('td');
    const block = document.createElement('div');
    block.className = 'ad-id-block';
    const strong = document.createElement('strong');
    strong.textContent = name || sellerId;
    strong.title = name || sellerId;
    const seller = document.createElement('code');
    seller.textContent = `shop ${sellerId}`;
    const advertiser = document.createElement('code');
    advertiser.textContent = `adv ${advertiserId}`;
    block.append(strong, seller, advertiser);
    td.append(block);
    return td;
  }

  function productCell(item) {
    const td = document.createElement('td');
    const block = document.createElement('div');
    block.className = 'ad-id-block';
    if (item.product_title) {
      const strong = document.createElement('strong');
      strong.textContent = item.product_title;
      strong.title = item.product_title;
      block.append(strong);
    }
    block.append(copyButton(item.product_id));
    td.append(block);
    return td;
  }

  function copyCell(value) {
    const td = document.createElement('td');
    td.append(copyButton(value));
    return td;
  }

  function copyButton(value) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ad-copy';
    button.textContent = value || '—';
    button.title = value ? '复制 ID' : '';
    if (value) {
      button.addEventListener('click', async function () {
        try {
          await navigator.clipboard.writeText(value);
          const previous = button.textContent;
          button.textContent = '已复制';
          window.setTimeout(function () { button.textContent = previous; }, 900);
        } catch (_) {
          button.title = '浏览器未允许复制，请手动选择';
        }
      });
    }
    return button;
  }

  function cell(value, className) {
    const td = document.createElement('td');
    td.textContent = value;
    if (className) td.className = className;
    return td;
  }

  function numberCell(value) {
    return cell(value, 'ad-num');
  }

  function renderPagination(total, visibleCount) {
    const start = total === 0 ? 0 : state.offset + 1;
    const end = state.offset + visibleCount;
    elements.pageRange.textContent = `${numberFormat.format(start)}–${numberFormat.format(end)} / ${numberFormat.format(total)} 行`;
    elements.prev.disabled = state.offset === 0;
    elements.next.disabled = state.offset + visibleCount >= total;
  }

  function setLoading(loading) {
    elements.form.querySelectorAll('button, input, select').forEach(function (control) {
      control.disabled = loading;
    });
    elements.prev.disabled = loading || elements.prev.disabled;
    elements.next.disabled = loading || elements.next.disabled;
    if (loading) setStatus('正在读取广告明细…', 'loading');
  }

  function setStatus(message, status) {
    elements.status.textContent = message;
    elements.status.dataset.state = status;
  }

  function formatMoney(value) {
    if (value == null || value === '') return '—';
    const numeric = Number(value);
    return Number.isFinite(numeric) ? moneyFormat.format(numeric) : String(value);
  }

  function formatTimestamp(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(parsed);
  }

  function redirectToLogin() {
    location.href = `${PREFIX}/v2/auth/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
}());

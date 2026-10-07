/* spu-profit-deterioration.js — SPU 利润劣化告警页（只读展示 + 阈值设置抽屉）。
 *
 * 硬规则（docs/design/spu-profit-deterioration-alert.md §6.2 / §9）：
 *   - 数据只来自 GET /v2/analytics/spu-profit-deterioration（只读）；
 *   - 本文件不计算 ROI / 净利润 / 阈值，也不硬编码任何 effective 阈值；
 *     Decimal wire string 原样显示，null 显示为「—」（绝不伪装成 0）；
 *   - 阈值设置抽屉的唯一数据源是主响应的 meta.effectiveConfig
 *     （published 的 readonly-safe 投影；不含 draft / rollout / secret）；
 *   - 编辑走现有 runtime config draft/publish 端点，前端不复制校验公式。
 *
 * 行/卡片/横幅的非颜色信号：文案 + 图标 + 行处理，颜色只作辅助
 * （强警告要求，见 §6.3）。
 */

(() => {
  'use strict';

  const ALERT_CONFIG_KEY = 'analytics.spu_profit_deterioration_alert.v1';
  const DATA_PATH = '/analytics/spu-profit-deterioration';
  const DRAFT_PATH = `/config/runtime/items/${encodeURIComponent(ALERT_CONFIG_KEY)}/draft`;
  const PUBLISH_PATH = `/config/runtime/items/${encodeURIComponent(ALERT_CONFIG_KEY)}/publish`;
  const ITEM_PATH = `/config/runtime/items/${encodeURIComponent(ALERT_CONFIG_KEY)}`;

  const marker = '/v2/';
  const markerAt = location.pathname.indexOf(marker);
  const rootPrefix = markerAt >= 0 ? location.pathname.slice(0, markerAt) : '';
  const API = `${rootPrefix}/v2`;

  const LAYERS = ['fast', 'confirmation'];
  const LAYER_LABELS = { fast: '快层 fast', confirmation: '确认层 confirmation' };
  const WINDOW_DAYS = ['1', '3', '7'];
  /* spu_ids 上限与服务端 API 契约一致（最多 100 个内部 spu_pk）。
     这里只做输入形态校验（正整数 + 条数），不推断任何业务含义。 */
  const MAX_SPU_IDS = 100;
  const SEVERITIES = ['warning', 'critical'];
  const SEVERITY_LABELS = { warning: '告警', critical: '严重告警' };
  const SEVERITY_ENUM = { none: '无告警', warning: '告警', critical: '严重告警' };

  /* 字段顺序 + 客户端只做「类型/范围」校验（范围＝服务端 schema 的上下界，
     不是告警阈值；真正的阈值比较与状态判定全部在服务端）。 */
  const THRESHOLD_FIELDS = [
    { key: 'roiAbsDelta', label: 'ROI 绝对差', kind: 'decimal', max: '10' },
    { key: 'roiRelativeDecline', label: 'ROI 相对降幅', kind: 'ratio', max: '1' },
    { key: 'netProfitDecline', label: '净利润降幅', kind: 'ratio', max: '1' },
    { key: 'minSpendCny', label: '最小消耗 CNY', kind: 'decimal', max: '10000000' },
    { key: 'minOrders', label: '最小订单数', kind: 'int', max: '100000' },
    { key: 'minAdOrders', label: '最小广告订单数', kind: 'int', max: '100000' },
  ];

  /* 非颜色信号：每类状态都有文案 + 图标 + 行处理。 */
  const KINDS = {
    critical: { icon: '⚠', label: '严重告警', note: '数值门槛同时满足 critical 条件。' },
    warning: { icon: '!', label: '告警', note: '数值门槛满足 warning 条件。' },
    sample: {
      icon: 'i',
      label: '样本不足，未触发告警',
      note: '样本门槛未满足（消耗 / 订单 / 广告订单不足或 ROI 无解），不代表健康。',
    },
    unavailable: {
      icon: '?',
      label: '数据不可用',
      note: '比较窗口缺少事实行，无法判断，不代表健康。',
    },
    stable: { icon: '—', label: '无告警', note: '比较窗口内未达到告警门槛。' },
  };

  const BANNER_ICONS = {
    loading: '…',
    ok: '·',
    warning: '!',
    critical: '⚠',
    sample: 'i',
    unavailable: '?',
    stale: '!',
    disabled: '×',
    error: '×',
  };

  const state = {
    shopPk: null,
    spuIds: [],
    windowDays: '',
    /* 层级筛选已从 UI 移除（业务理解不了 fast/confirmation）：固定只看确认层；
       快层数据仍可用 ?layer=fast 或 ?layer=all 排查。 */
    layer: 'confirmation',
    severity: 'all',
    alertState: 'all',
    sample: 'sufficient',
    anchorDate: '',
    limit: 100,
    offset: 0,
    role: '',
    payload: null,
    draftVersion: null,
    canEdit: false,
    selectedKey: null,
    lastSnapshot: null,
  };

  const els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(
      /[&<>'"]/g,
      (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]),
    );
  }

  async function api(path, options = {}) {
    const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    if (options.method && options.method !== 'GET') headers['X-Requested-With'] = 'tts-erp';
    let res;
    try {
      res = await fetch(`${API}${path}`, Object.assign({ credentials: 'same-origin' }, options, { headers }));
    } catch (err) {
      const wrapped = new Error(`网络错误：${err && err.message ? err.message : err}`);
      wrapped.status = 0;
      throw wrapped;
    }
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch {
        /* 非 JSON 错误体：保留状态行。 */
      }
      const wrapped = new Error(Array.isArray(detail) ? JSON.stringify(detail) : String(detail));
      wrapped.status = res.status;
      wrapped.requestId = res.headers.get('x-request-id') || '';
      throw wrapped;
    }
    return res.json();
  }

  /* ---------- 展示辅助：值一律照抄服务端 ---------- */

  function decimalText(value) {
    if (value === null || value === undefined || value === '') return null;
    return String(value);
  }

  function intText(value) {
    if (value === null || value === undefined || value === '') return null;
    return String(value);
  }

  function cell(value) {
    const text = decimalText(value);
    if (text === null) {
      return '<span class="is-null" data-null="1" title="服务端返回 null（数学无解或样本不足），不显示为 0">—</span>';
    }
    return `<span class="mono">${esc(text)}</span>`;
  }

  function intCell(value) {
    const text = intText(value);
    if (text === null) return '<span class="is-null" data-null="1">—</span>';
    return `<span class="mono">${esc(text)}</span>`;
  }

  function itemKind(item) {
    if (!item) return 'stable';
    if (item.sampleStatus === 'unavailable' || item.state === 'unavailable') return 'unavailable';
    if (item.severity === 'critical') return 'critical';
    if (item.severity === 'warning') return 'warning';
    if (item.sampleStatus !== 'sufficient') return 'sample';
    return 'stable';
  }

  function stateLabel(item) {
    const raw = item && item.state ? String(item.state) : '';
    /* 未知枚举原样显示，不静默留空（§6.3）。 */
    return raw || '—';
  }

  function badge(item) {
    const kind = itemKind(item);
    const meta = KINDS[kind];
    const provisional = item.provisionalLabel
      ? `<span class="badge alert-badge alert-badge--provisional">${esc(item.provisionalLabel)}</span>`
      : '';
    return `<span class="alert-signal">
        <span class="alert-icon" role="img" aria-label="${esc(meta.label)}">${esc(meta.icon)}</span>
        <span class="badge alert-badge alert-badge--${kind}">${esc(meta.label)}</span>
        ${provisional}
      </span>`;
  }

  function severityCell(item) {
    const code = item && item.severity ? String(item.severity) : '';
    const label = SEVERITY_ENUM[code] || code || '—';
    return `<span class="mono">${esc(label)}</span> <span class="visually-hidden">severity=${esc(code || 'unknown')}</span>`;
  }

  function sampleCell(item) {
    const code = item && item.sampleStatus ? String(item.sampleStatus) : '';
    return `<span class="mono">${esc(code || '—')}</span>`;
  }

  /* 主 CTA 打开 SPU 盈利详情页；原始 JSON 降为次要链接。
     两者都用服务端下发的 URL，浏览器不拼业务参数以外的东西。 */
  function drillLinks(item) {
    const dd = item && item.drilldown ? item.drilldown : null;
    if (!dd) {
      return '<span class="is-null" data-null="1" title="服务端未提供 drilldown">—</span>';
    }
    const raw = dd.profitabilityUrl ? `${rootPrefix}${dd.profitabilityUrl}` : '';
    const detailPage = dd.pageUrl
      ? `${rootPrefix}${dd.pageUrl}?shop_pk=${encodeURIComponent(item.shopPk)}` +
        `&spu_pk=${encodeURIComponent(item.spuPk)}`
      : '';
    if (detailPage) {
      const rawLink = raw
        ? `<span class="alert-drill-sep"> · </span><a class="alert-drill alert-drill--secondary"` +
          ` data-role="drilldown-json" href="${esc(raw)}">原始 JSON</a>`
        : '';
      return `<a class="alert-drill" data-role="drilldown" href="${esc(detailPage)}">查看利润详情</a>${rawLink}`;
    }
    if (raw) {
      return `<a class="alert-drill" data-role="drilldown" data-role-target="json" href="${esc(raw)}"` +
        ` title="服务端未下发 pageUrl，主链接退回原始 JSON 端点">查看利润详情（原始 JSON）</a>`;
    }
    return '<span class="is-null" data-null="1" title="服务端未提供 drilldown">—</span>';
  }

  /* 行/卡片身份：同一 shop+SPU+窗口+层级的组合。 */
  function itemKey(item) {
    return [item.shopPk, item.spuPk, item.windowDays, item.layer].join(':');
  }

  function summaryPairs(item) {
    return [
      ['上期 ROI', cell(item.previousRoi)],
      ['本期 ROI', cell(item.currentRoi)],
      ['ROI 降幅', cell(item.roiDecline)],
      ['上期净利润(CNY)', cell(item.previousNetProfitCny)],
      ['本期净利润(CNY)', cell(item.currentNetProfitCny)],
      ['净利润降幅', cell(item.netProfitDecline)],
      ['上期消耗(CNY)', cell(item.previousSpendCny)],
      ['本期消耗(CNY)', cell(item.currentSpendCny)],
      ['上期订单数', intCell(item.previousOrderCount)],
      ['本期订单数', intCell(item.currentOrderCount)],
      ['上期广告订单数', intCell(item.previousAdOrderCount)],
      ['本期广告订单数', intCell(item.currentAdOrderCount)],
    ];
  }

  function metricPairs(item) {
    return [
      ['上期 ROI', cell(item.previousRoi)],
      ['本期 ROI', cell(item.currentRoi)],
      ['ROI 降幅', cell(item.roiDecline)],
      ['上期净利润(CNY)', cell(item.previousNetProfitCny)],
      ['本期净利润(CNY)', cell(item.currentNetProfitCny)],
      ['净利润降幅', cell(item.netProfitDecline)],
      ['上期消耗(CNY)', cell(item.previousSpendCny)],
      ['本期消耗(CNY)', cell(item.currentSpendCny)],
      ['订单(上→本)', `${intCell(item.previousOrderCount)} → ${intCell(item.currentOrderCount)}`],
      ['广告订单(上→本)', `${intCell(item.previousAdOrderCount)} → ${intCell(item.currentAdOrderCount)}`],
    ];
  }

  function configText(item) {
    const source = item.configSource ? String(item.configSource) : '—';
    const version = item.configVersion === null || item.configVersion === undefined ? '' : ` v${item.configVersion}`;
    return `<span class="mono">${esc(source + version)}</span>`;
  }

  function rowHtml(item) {
    const kind = itemKind(item);
    const meta = KINDS[kind];
    const pairs = metricPairs(item);
    const byLabel = (label) => (pairs.find((pair) => pair[0] === label) || ['', ''])[1];
    const expanded = state.selectedKey === itemKey(item);
    return `<tr class="alert-row alert-row--${kind}" data-kind="${kind}"
        data-severity="${esc(item.severity)}" data-sample="${esc(item.sampleStatus)}"
        data-item-key="${esc(itemKey(item))}" data-shop-pk="${esc(item.shopPk)}" data-spu-pk="${esc(item.spuPk)}"
        aria-expanded="${expanded ? 'true' : 'false'}"
        title="${esc(meta.note)}">
      <td>${badge(item)}</td>
      <td><span class="mono">#${esc(item.spuPk)}</span>
        <button type="button" class="alert-row__toggle" data-role="row-summary"
          aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="alert-summary"
          aria-label="展开 SPU #${esc(item.spuPk)} 明细">明细</button>
        <div class="visually-hidden">shop_pk=${esc(item.shopPk)}</div></td>
      <td><span class="mono">${esc(item.windowDays)} 天</span></td>
      <td>${sampleCell(item)}
        <div class="alert-row__state mono">${esc(stateLabel(item))} · ${severityCell(item)}</div></td>
      <td>${byLabel('上期 ROI')}</td>
      <td>${byLabel('本期 ROI')}</td>
      <td>${byLabel('ROI 降幅')}</td>
      <td>${byLabel('上期净利润(CNY)')}</td>
      <td>${byLabel('本期净利润(CNY)')}</td>
      <td>${byLabel('净利润降幅')}</td>
      <td>${byLabel('上期消耗(CNY)')}</td>
      <td>${byLabel('本期消耗(CNY)')}</td>
      <td>${byLabel('订单(上→本)')}</td>
      <td>${byLabel('广告订单(上→本)')}</td>
      <td><span class="mono">${esc(item.anchorDate)}</span></td>
      <td>${configText(item)}
        <div class="alert-row__note">${esc(item.warningCode || '')}</div></td>
      <td>${drillLinks(item)}
        <div class="alert-row__note">${esc(item.warningText || meta.note)}</div></td>
    </tr>`;
  }

  function cardHtml(item) {
    const kind = itemKind(item);
    const meta = KINDS[kind];
    const expanded = state.selectedKey === itemKey(item);
    const grid = metricPairs(item)
      .map((pair) => `<dt>${esc(pair[0])}</dt><dd>${pair[1]}</dd>`)
      .join('');
    return `<article class="alert-card alert-card--${kind} mb-3" data-kind="${kind}"
        data-severity="${esc(item.severity)}" data-sample="${esc(item.sampleStatus)}"
        data-item-key="${esc(itemKey(item))}" data-shop-pk="${esc(item.shopPk)}" data-spu-pk="${esc(item.spuPk)}"
        aria-expanded="${expanded ? 'true' : 'false'}">
      <header class="alert-card__head">
        ${badge(item)}
        <span class="alert-card__spu mono">SPU #${esc(item.spuPk)}</span>
        <span class="alert-card__window mono">${esc(item.windowDays)} 天</span>
        <button type="button" class="alert-row__toggle" data-role="row-summary"
          aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="alert-summary"
          aria-label="展开 SPU #${esc(item.spuPk)} 明细">明细</button>
      </header>
      <p class="alert-card__text">${esc(item.warningText || meta.note)}</p>
      <p class="alert-card__meta mono">状态 ${esc(stateLabel(item))} · 样本 ${esc(item.sampleStatus)} ·
        anchor ${esc(item.anchorDate)} · 配置 ${esc(item.configSource)}${item.configVersion ? ` v${esc(item.configVersion)}` : ''}</p>
      <dl class="alert-card__grid">${grid}</dl>
      <footer class="alert-card__foot">${drillLinks(item)}</footer>
    </article>`;
  }

  /* ---------- 状态面板 / 横幅 ---------- */

  function setBanner(kind, text, meta) {
    if (!els.banner) return;
    els.banner.dataset.kind = kind;
    els.bannerIcon.textContent = BANNER_ICONS[kind] || '·';
    els.bannerText.textContent = text;
    els.bannerMeta.textContent = meta || '';
  }

  function setStatus(kind, text) {
    if (!els.status) return;
    els.status.dataset.kind = kind;
    els.status.textContent = text;
  }

  /* 新鲜度提示：只用服务端 meta.calculatedAt / basisCalculatedAt / anchorDate。
     快照 key 变化 = 到达了新的物化结果，提示「快照已更新」（design §6.2 refresh）。 */
  function renderFreshness(payload) {
    if (!els.freshness) return;
    const meta = (payload && payload.meta) || {};
    const items = (payload && payload.items) || [];
    const anchor = meta.anchorDate || '—';
    const calculatedAt = meta.calculatedAt || '—';
    const basis = items.length && items[0].basisCalculatedAt ? items[0].basisCalculatedAt : calculatedAt;
    const key = `${anchor}|${calculatedAt}|${basis}`;
    const previous = state.lastSnapshot;
    state.lastSnapshot = key;
    const updated = previous !== null && previous !== key;
    els.freshness.dataset.kind = updated ? 'updated' : 'stable';
    els.freshness.textContent =
      `${updated ? '快照已更新：' : '快照新鲜度：'}anchor ${anchor} · 物化于 ${calculatedAt} · basis ${basis}` +
      `${meta.stale === true ? ' · 快照已过期（stale）' : ''}`;
  }

  function metaLine(meta, requestId) {
    const parts = [];
    if (meta.anchorDate) parts.push(`anchor ${meta.anchorDate}`);
    if (meta.batchThrough) parts.push(`批次至 ${meta.batchThrough}`);
    if (meta.maturityDays !== undefined && meta.maturityDays !== null) {
      parts.push(`成熟滞后 ${meta.maturityDays} 天`);
    }
    const eff = meta.effectiveConfig || {};
    if (eff.source) parts.push(`配置 ${eff.source}${eff.version ? ` v${eff.version}` : ''}`);
    if (requestId) parts.push(`requestId ${requestId}`);
    return parts.join(' · ');
  }

  function renderTotals(payload) {
    const totals = payload.totals || {};
    els.totalRows.textContent = payload.total === null || payload.total === undefined ? '—' : String(payload.total);
    els.totalWarning.textContent = totals.warningCount === undefined ? '—' : String(totals.warningCount);
    els.totalCritical.textContent = totals.criticalCount === undefined ? '—' : String(totals.criticalCount);
    if (els.totalInsufficient) {
      els.totalInsufficient.textContent =
        totals.insufficientSampleCount === undefined ? '—' : String(totals.insufficientSampleCount);
    }
    els.totalSpus.textContent = totals.shopSpuCount === undefined ? '—' : String(totals.shopSpuCount);
        // pi-lens-ignore: no-inner-html-js
    els.tfoot.innerHTML = `<tr class="alert-total-row">
      <td colspan="17">服务端 totals（完整 scope，不是当前页可见行）：全部 ${esc(payload.total)} 行 ·
        告警 ${esc(totals.warningCount)} · 严重告警 ${esc(totals.criticalCount)} ·
        样本不足 / 不可用 ${esc(totals.insufficientSampleCount)} · 涉及 SPU ${esc(totals.shopSpuCount)}</td>
    </tr>`;
  }

  function renderPager(total) {
    const limit = state.limit;
    const pages = Math.max(1, Math.ceil((total || 0) / limit));
    const page = Math.min(pages, Math.floor(state.offset / limit) + 1);
    const from = total ? state.offset + 1 : 0;
    const to = Math.min(state.offset + limit, total || 0);
    els.pagerLabel.textContent = `第 ${page} / ${pages} 页 · 显示 ${from}-${to} / ${total || 0} 行`;
    els.pagerPrev.disabled = state.offset <= 0;
    els.pagerNext.disabled = state.offset + limit >= (total || 0);
  }

  function renderEmpty(payload, kind, text) {
    hideSummary();
    // pi-lens-ignore: no-inner-html-js
    els.rows.innerHTML = `<tr><td colspan="17" class="op-empty">${esc(text)}</td></tr>`;
    // pi-lens-ignore: no-inner-html-js
    els.cards.innerHTML = `<p class="op-empty alert-card alert-card--${kind}">${esc(text)}</p>`;
    setStatus(kind, text);
    const meta = payload && payload.meta ? payload.meta : {};
    const eff = meta.effectiveConfig || {};
    const checked = payload && payload.total !== undefined ? payload.total : '—';
    setBanner(kind, text, `已检查 ${checked} 行 · anchor ${meta.anchorDate || '—'} · 配置 ${eff.source || '—'}${eff.version ? ` v${eff.version}` : ''}`);
  }

  function renderPayload(payload) {
    const meta = payload.meta || {};
    const totals = payload.totals || {};
    const eff = meta.effectiveConfig || {};
    const items = payload.items || [];

    els.pageNote.textContent =
      `anchor ${meta.anchorDate || '—'} · 批次至 ${meta.batchThrough || '—'} · ` +
      `成熟滞后 ${meta.maturityDays === undefined ? '—' : meta.maturityDays} 天 · ` +
      `物化于 ${meta.calculatedAt || '—'}`;
    renderFreshness(payload);
    els.bannerProvisional.hidden = eff.source !== 'seed_fallback' && !eff.provisionalLabel;
    renderTotals(payload);
    renderPager(payload.total);

    if (meta.enabled === false) {
      renderEmpty(payload, 'disabled', '告警已停用（enabled=false）：不生成新告警，页面只展示历史快照。');
      return;
    }
    if (meta.stale === true || (meta.coverage && meta.coverage.stale === true)) {
      renderEmpty(payload, 'stale', '快照已过期（stale）：显示最近一次成功物化的结果，不冒充最新 anchor。');
      return;
    }
    if (!items.length) {
      renderEmpty(
        payload,
        'ok',
        `当前 scope 没有达到阈值的告警（已检查 anchor ${meta.anchorDate || '—'} 的 ${payload.total} 行）。`,
      );
      return;
    }

    const kinds = items.map(itemKind);
    const allSample = kinds.every((kind) => kind === 'sample' || kind === 'unavailable');
    let bannerKind = 'ok';
    let bannerText = '当前 scope 没有达到阈值的告警';
    let statusText = `已加载 ${items.length} 行（共 ${payload.total} 行）；样本不足 / 不可用 ${totals.insufficientSampleCount} 条。`;
    if (allSample) {
      const onlySample = kinds.includes('sample');
      bannerKind = onlySample ? 'sample' : 'unavailable';
      bannerText = onlySample ? '本页全部为样本不足，未触发告警' : '本页全部为数据不可用';
      statusText = onlySample
        ? '本页全部为样本不足，未触发告警：样本门槛未满足，不代表健康。'
        : '本页全部为数据不可用：比较窗口缺少事实行，无法判断，不代表健康。';
    } else if (totals.criticalCount > 0) {
      bannerKind = 'critical';
      bannerText = `严重告警 ${totals.criticalCount} 条`;
    } else if (totals.warningCount > 0) {
      bannerKind = 'warning';
      bannerText = `告警 ${totals.warningCount} 条`;
    }
    setBanner(bannerKind, bannerText, metaLine(meta, meta.requestId));
    setStatus(bannerKind, statusText);
    // pi-lens-ignore: no-inner-html-js
    els.rows.innerHTML = items.map(rowHtml).join('');
    // pi-lens-ignore: no-inner-html-js
    els.cards.innerHTML = items.map(cardHtml).join('');
    /* 重渲染后保持已展开的 summary：选中的行还在就重建，不在就收起。 */
    const selected = state.selectedKey ? findItem(state.selectedKey) : null;
    if (selected) showSummary(selected, false);
    else hideSummary();
  }

  function renderLoading() {
    setBanner('loading', '正在加载告警…', '');
    setStatus('loading', '正在加载告警…');
    // pi-lens-ignore: no-inner-html-js
    els.rows.innerHTML = `<tr class="alert-row alert-row--loading"><td colspan="17" class="op-loading">正在加载告警…</td></tr>`;
    // pi-lens-ignore: no-inner-html-js
    els.cards.innerHTML = '<p class="op-loading alert-card">正在加载告警…</p>';
    els.bannerProvisional.hidden = true;
    if (els.freshness) {
      els.freshness.dataset.kind = 'loading';
      els.freshness.textContent = '快照新鲜度：正在重新获取…';
    }
  }

  function renderError(err) {
    const rid = err && err.requestId ? ` · requestId ${err.requestId}` : '';
    let text;
    if (err.status === 403) text = `权限不足（403）：${err.message}${rid}`;
    else if (err.status === 401) text = `未登录或会话失效（401）：${err.message}${rid}`;
    else if (err.status === 422) text = `筛选参数非法（422）：${err.message}${rid}`;
    else if (err.status === 503) text = `快照或配置不可用（503）：${err.message}${rid}`;
    else if (!err.status) text = `加载失败：${err.message}${rid}`;
    else text = `加载失败（HTTP ${err.status}）：${err.message}${rid}`;
    setBanner('error', '加载失败', rid.replace(/^ · /, ''));
    // pi-lens-ignore: no-inner-html-js
    els.rows.innerHTML = `<tr><td colspan="17" class="op-error">${esc(text)}</td></tr>`;
    // pi-lens-ignore: no-inner-html-js
    els.cards.innerHTML = `<p class="op-error alert-card alert-card--error">${esc(text)}</p>`;
    setStatus('error', `${text} 可点击「刷新」重试。`);
  }

  /* ---------- summary card（行点击展开） ---------- */

  function setExpandedMarkers(key) {
    document.querySelectorAll('[data-item-key]').forEach((node) => {
      const expanded = node.dataset.itemKey === key;
      node.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      const toggle = node.querySelector('[data-role="row-summary"]');
      if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
  }

  function hideSummary() {
    state.selectedKey = null;
    if (els.summary) els.summary.hidden = true;
    setExpandedMarkers(null);
  }

  /* 只照抄服务端字段：上期/本期 ROI、净利润、消耗、订单/广告订单、降幅、
     状态、样本、anchor/basis 时间与配置来源。浏览器不重算任何百分比。 */
  function showSummary(item, trigger) {
    const kind = itemKind(item);
    const meta = KINDS[kind];
    const key = itemKey(item);
    state.selectedKey = key;
    els.summary.hidden = false;
    els.summary.dataset.kind = kind;
    els.summary.dataset.spuPk = item.spuPk;
    els.summary.dataset.shopPk = item.shopPk;
    // pi-lens-ignore: no-inner-html-js
    els.summaryMeta.innerHTML =
      `${badge(item)} <span class="mono">shop_pk #${esc(item.shopPk)} · SPU #${esc(item.spuPk)} · ` +
      `${esc(item.windowDays)} 天</span>` +
      `<div class="alert-summary__line">状态 <span class="mono">${esc(stateLabel(item))}</span> · ` +
      `severity <span class="mono">${esc(item.severity || '—')}</span> · ` +
      `样本 <span class="mono">${esc(item.sampleStatus || '—')}</span></div>` +
      `<div class="alert-summary__line">anchor <span class="mono">${esc(item.anchorDate)}</span> · ` +
      `basisCalculatedAt <span class="mono">${esc(item.basisCalculatedAt || '—')}</span> · ` +
      `配置基准 <span class="mono">${esc(item.configSource || '—')}` +
      `${item.configVersion === null || item.configVersion === undefined ? '' : ` v${esc(item.configVersion)}`}</span></div>` +
      `<div class="alert-summary__line">warningCode <span class="mono">${esc(item.warningCode || '—')}</span> · ` +
      `${esc(item.warningText || meta.note)}</div>`;
    // pi-lens-ignore: no-inner-html-js
    els.summaryGrid.innerHTML = summaryPairs(item)
      .map((pair) => `<dt>${esc(pair[0])}</dt><dd>${pair[1]}</dd>`)
      .join('');
    // pi-lens-ignore: no-inner-html-js
    els.summaryFoot.innerHTML = drillLinks(item);
    setExpandedMarkers(key);
    if (trigger && typeof trigger.focus === 'function') {
      els.summaryClose.focus();
    }
  }

  function findItem(key) {
    const items = (state.payload && state.payload.items) || [];
    return items.find((item) => itemKey(item) === key) || null;
  }

  function onSummaryTrigger(event) {
    const holder = event.target.closest('[data-item-key]');
    if (!holder) return;
    const key = holder.dataset.itemKey;
    if (!key || !findItem(key)) return;
    if (state.selectedKey === key) {
      hideSummary();
      return;
    }
    showSummary(findItem(key), true);
  }

  /* ---------- filters / URL ---------- */

  /* 粘贴式精确 scope：只接受内部 spu_pk 正整数，上限与服务端 spu_ids 一致。
     非法输入不静默放宽为全 SPU——保持现有 scope 并把原因写进反馈区。 */
  function parseSpuScope(raw) {
    const values = [];
    const seen = new Set();
    String(raw || '')
      .replace(/，/g, ',')
      .split(',')
      .forEach((part) => {
        const value = part.trim();
        if (!value || seen.has(value)) return;
        seen.add(value);
        values.push(value);
      });
    if (!values.length) return { ids: [], error: '' };
    if (values.length > MAX_SPU_IDS) {
      return {
        ids: [],
        error: `SPU scope 最多 ${MAX_SPU_IDS} 个（与服务端 spu_ids 上限一致），收到 ${values.length} 个：筛选未应用。`,
      };
    }
    const bad = values.find((value) => !/^\d+$/.test(value) || Number(value) < 1);
    if (bad) {
      return { ids: [], error: `SPU scope 只能填内部 spu_pk 正整数：「${bad}」非法，筛选未应用。` };
    }
    return { ids: values.map((value) => Number(value)), error: '' };
  }

  function setSpuFeedback(message) {
    if (!els.spuFeedback) return;
    els.spuFeedback.textContent = message || '';
    els.spuFeedback.dataset.kind = message ? 'error' : '';
  }

  function queryString() {
    const params = new URLSearchParams();
    if (state.shopPk) params.set('shop_pk', String(state.shopPk));
    state.spuIds.forEach((spuPk) => params.append('spu_ids', String(spuPk)));
    if (state.windowDays) params.append('window_days', state.windowDays);
    params.set('layer', state.layer);
    if (state.alertState !== 'all') params.append('state', state.alertState);
    params.set('severity', state.severity);
    params.set('sample', state.sample);
    if (state.anchorDate) params.set('anchor_date', state.anchorDate);
    params.set('limit', String(state.limit));
    params.set('offset', String(state.offset));
    return params.toString();
  }

  function filterQueryString() {
    const params = new URLSearchParams();
    if (state.shopPk) params.set('shop_pk', String(state.shopPk));
    if (state.spuIds.length) params.set('spu_ids', state.spuIds.join(','));
    if (state.windowDays) params.append('window_days', state.windowDays);
    params.set('layer', state.layer);
    if (state.alertState !== 'all') params.set('state', state.alertState);
    params.set('severity', state.severity);
    params.set('sample', state.sample);
    if (state.anchorDate) params.set('anchor_date', state.anchorDate);
    return params.toString();
  }

  function syncUrl() {
    const qs = filterQueryString();
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
    if (els.filterEcho) {
      els.filterEcho.textContent = `筛选条件（已写回 URL query）：${qs || '（无）'}；服务端 totals 始终按完整 scope 计算。`;
    }
  }

  function readUrlState() {
    const params = new URLSearchParams(location.search);
    const shop = params.get('shop_pk');
    state.shopPk = shop && Number(shop) >= 1 ? Number(shop) : null;
    /* URL 里的 SPU scope 接受逗号串或重复参数两种写法（与盈利页同一习惯）。 */
    const scope = parseSpuScope(params.getAll('spu_ids').join(','));
    state.spuIds = scope.ids;
    setSpuFeedback(scope.error);
    const windowDays = params.get('window_days') || '';
    state.windowDays = WINDOW_DAYS.includes(windowDays) ? windowDays : '';
    const layer = params.get('layer') || 'confirmation';
    state.layer = ['all', 'fast', 'confirmation'].includes(layer) ? layer : 'confirmation';
    const alertState = params.get('state') || 'all';
    state.alertState = alertStateValues().includes(alertState) ? alertState : 'all';
    const severity = params.get('severity') || 'all';
    state.severity = ['all', 'none', 'warning', 'critical'].includes(severity) ? severity : 'all';
    /* 样本筛选默认 sufficient（页面不再提供该下拉框，owner 2026-10-07）；
       URL ?sample= 仍接受 all/sample_insufficient/unavailable 作为排查入口。 */
    const sample = params.get('sample') || 'sufficient';
    state.sample = ['all', 'sufficient', 'sample_insufficient', 'unavailable'].includes(sample) ? sample : 'sufficient';
    state.anchorDate = params.get('anchor_date') || '';
    const limit = Number(params.get('limit'));
    state.limit = [50, 100, 200].includes(limit) ? limit : 100;
    syncFilterControls();
  }

  /* state 下拉的合法值直接读 DOM option，枚举只维护一处（模板）。 */
  function alertStateValues() {
    if (!els.alertState) return ['all'];
    const values = ['all'];
    els.alertState.querySelectorAll('option').forEach((option) => {
      const value = option.value;
      if (value && value !== 'all') values.push(value);
    });
    return values;
  }

  function syncFilterControls() {
    els.windowTabs.querySelectorAll('.op-window-tab').forEach((tab) => {
      const active = tab.dataset.windowDays === state.windowDays;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    if (els.spuIds) els.spuIds.value = state.spuIds.join(',');
    if (els.alertState) els.alertState.value = state.alertState;
    if (els.severity) els.severity.value = state.severity;
    if (els.anchorDate) els.anchorDate.value = state.anchorDate;
    if (els.limit) els.limit.value = String(state.limit);
  }

  function applySpuScope() {
    const scope = parseSpuScope(els.spuIds.value);
    if (scope.error) {
      setSpuFeedback(scope.error);
      syncFilterControls();
      return;
    }
    setSpuFeedback('');
    const changed =
      scope.ids.length !== state.spuIds.length ||
      scope.ids.some((spuPk, index) => spuPk !== state.spuIds[index]);
    state.spuIds = scope.ids;
    /* 回写规范形式（去空白/去重），使输入框、URL 与请求三处始终同一份 scope。 */
    syncFilterControls();
    if (!changed) return;
    state.offset = 0;
    syncUrl();
    load();
  }

  /* ---------- 数据加载 ---------- */

  async function loadShops() {
    let shops = [];
    try {
      shops = await api('/commerce/channel-accounts?platform=tiktok&limit=500');
    } catch (err) {
      renderError(err);
      return;
    }
    if (!Array.isArray(shops) || !shops.length) {
      renderEmpty(null, 'unavailable', '没有已注册的 TikTok 店铺，无法加载告警。');
      return;
    }
    // pi-lens-ignore: no-inner-html-js
    els.shop.innerHTML = shops
      .map((shop) => {
        const label = `${shop.account_name || '未命名店铺'} · ${shop.shop_id || ''}`;
        return `<option value="${esc(shop.id)}">${esc(label)}</option>`;
      })
      .join('');
    const known = shops.some((shop) => String(shop.id) === String(state.shopPk));
    if (!state.shopPk || !known) state.shopPk = Number(shops[0].id);
    els.shop.value = String(state.shopPk);
    syncUrl();
    await load();
  }

  async function load() {
    if (!state.shopPk) {
      renderEmpty(null, 'unavailable', '未选择店铺，无法加载告警。');
      return;
    }
    renderLoading();
    try {
      const payload = await api(`${DATA_PATH}?${queryString()}`);
      if (!payload || !Array.isArray(payload.items)) {
        throw new Error('响应缺少 items 数组（契约错误）');
      }
      state.payload = payload;
      renderPayload(payload);
      loadConfigProjection(payload);
    } catch (err) {
      renderError(err);
    }
  }

  /* ---------- 阈值设置抽屉 ---------- */

  function drawerElements() {
    return {
      drawer: els.drawer,
      meta: $('drawer-meta'),
      hash: $('drawer-payload-hash'),
      provisional: $('drawer-provisional'),
      readonlyNote: $('drawer-readonly-note'),
      editor: $('drawer-editor'),
      actions: $('drawer-actions'),
      thresholds: $('drawer-thresholds'),
      status: $('drawer-form-status'),
      reload: $('btn-drawer-reload'),
    };
  }

  function fieldId(path) {
    return `err-${path.replace(/\./g, '-')}`;
  }

  function loadConfigProjection(payload) {
    const eff = payload && payload.meta ? payload.meta.effectiveConfig || {} : {};
    els.drawerProvisional.hidden = eff.source !== 'seed_fallback' && !eff.provisionalLabel;
    renderDrawerMeta(eff);
    /* 抽屉在首个载荷到达前就打开时不能被永久标为「已渲染」：只在表格真的
       渲染成功后才能置位，否则这里的提前 return 会让抽屉永远空着。 */
    if (drawerElements().drawer.dataset.rendered !== '1') renderThresholdTables(eff, !state.canEdit);
  }

  function renderDrawerMeta(eff) {
    const d = drawerElements();
    d.meta.textContent =
      `source ${eff.source || '—'}${eff.version ? ` v${eff.version}` : ''} · ` +
      `updatedAt ${eff.updatedAt || '—'} · updatedBy ${eff.updatedBy || '—'} · ` +
      `validation ${eff.validation || '—'} · enabled ${eff.enabled === undefined ? '—' : eff.enabled} · ` +
      `maturityDays ${eff.maturityDays === undefined ? '—' : eff.maturityDays}`;
    const drawer = eff.drawer || {};
    d.hash.textContent =
      `payloadHash ${eff.payloadHash || '—'} · drawer.mode ${drawer.mode || '—'} · ` +
      `canEdit ${drawer.canEdit === undefined ? '—' : drawer.canEdit} · ` +
      `draftIncluded ${drawer.draftIncluded === undefined ? '—' : drawer.draftIncluded} · ` +
      `rolloutIncluded ${drawer.rolloutIncluded === undefined ? '—' : drawer.rolloutIncluded} · ` +
      `secretsIncluded ${drawer.secretsIncluded === undefined ? '—' : drawer.secretsIncluded}`;
    d.provisional.hidden = eff.source !== 'seed_fallback' && !eff.provisionalLabel;
    d.provisional.textContent =
      eff.provisionalLabel || (eff.source === 'seed_fallback' ? '回测暂定' : '');
  }

  function thresholdInput(path, value, disabled) {
    const isDisabled = disabled ? ' disabled aria-disabled="true"' : '';
    const described = disabled ? '' : ` aria-describedby="${fieldId(path)}"`;
    return `<input type="text" class="form-control form-control-sm op-threshold-input mono"
      inputmode="decimal" data-path="${esc(path)}" value="${esc(value)}"${isDisabled}${described}
      aria-label="${esc(path)}">`;
  }

  /* 接收整个 effectiveConfig 投影：enabled / maturityDays 读顶层的显式字段，
     层/窗口/严重度表格读 eff.thresholds（= 完整 published payload）。 */
  function renderThresholdTables(eff, disabled) {
    const d = drawerElements();
    const thresholds = (eff && eff.thresholds) || {};
    if (!thresholds.fast || !thresholds.confirmation) {
      d.editor.hidden = true;
      d.actions.hidden = true;
      // pi-lens-ignore: no-inner-html-js
      d.thresholds.innerHTML = '';
      d.drawer.dataset.rendered = '0';
      setFormStatus('meta.effectiveConfig 缺少 thresholds：抽屉没有权威数据源，仅显示上方元数据。', 'error');
      return false;
    }
    const blocks = LAYERS.map((layer) => {
      const windows = WINDOW_DAYS.map((days) => {
        const entry = (thresholds[layer] || {})[days] || {};
        const groups = SEVERITIES.map((severity) => {
          const values = entry[severity] || {};
          const rows = THRESHOLD_FIELDS.map((field) => {
            const path = `${layer}.${days}.${severity}.${field.key}`;
            return `<tr>
              <th scope="row" class="op-th">${esc(field.label)}</th>
              <td>${thresholdInput(path, values[field.key] === undefined ? '' : values[field.key], disabled)}
                <span class="field-error" id="${fieldId(path)}"></span></td>
            </tr>`;
          }).join('');
          const groupId = `group-${layer}-${days}-${severity}`;
          return `<div class="op-threshold-group" data-group="${groupId}">
            <h5 class="op-threshold-group__title">
              <span class="alert-icon" role="img" aria-label="${esc(SEVERITY_LABELS[severity])}">${severity === 'critical' ? '⚠' : '!'}</span>
              ${esc(SEVERITY_LABELS[severity])}
              <span class="mono">${esc(layer)}.${esc(days)}.${esc(severity)}</span>
            </h5>
            <table class="op-table op-threshold-table"><tbody>${rows}</tbody></table>
            <span class="field-error" id="err-${groupId}"></span>
          </div>`;
        }).join('');
        return `<section class="op-threshold-window">
          <h4 class="op-threshold-window__title">窗口 ${esc(days)} 天</h4>
          ${groups}
        </section>`;
      }).join('');
      return `<details class="op-threshold-layer" open>
        <summary>${esc(LAYER_LABELS[layer])}</summary>
        ${windows}
      </details>`;
    }).join('');
    d.editor.hidden = false;
    d.actions.hidden = !state.canEdit;
    d.drawer.dataset.rendered = '1';
    // pi-lens-ignore: no-inner-html-js
    d.thresholds.innerHTML = `
      <div class="op-threshold-global">
        <label class="op-threshold-toggle">
          <input type="checkbox" id="drawer-enabled" class="form-check-input"
            ${eff.enabled ? 'checked' : ''}${disabled ? ' disabled aria-disabled="true"' : ''}>
          <span>enabled（关闭后不再生成新告警，旧快照只读）</span>
        </label>
        <span class="op-scope-note">maturityDays 固定为服务端值
          <strong class="mono">${esc(eff.maturityDays)}</strong>（v1 不可改，避免破坏业务窗口）；rollout 对本 key 不生效。</span>
      </div>
      ${blocks}`;
    setFormStatus('', '');
    return true;
  }

  function setFormStatus(text, kind) {
    const d = drawerElements();
    if (!d.status) return;
    d.status.textContent = text || '';
    d.status.dataset.kind = kind || '';
  }

  function clearErrors() {
    document.querySelectorAll('.field-error').forEach((node) => {
      node.textContent = '';
    });
    document.querySelectorAll('.op-threshold-input').forEach((node) => {
      node.removeAttribute('aria-invalid');
      node.classList.remove('is-invalid');
    });
  }

  function setFieldError(path, message) {
    const node = $(fieldId(path));
    if (node) node.textContent = message;
    const input = document.querySelector(`[data-path="${path}"]`);
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      input.classList.add('is-invalid');
    }
  }

  function setGroupError(groupId, message) {
    const node = $(`err-${groupId}`);
    if (node) node.textContent = message;
  }

  function parseField(raw, kind, max) {
    if (raw === '') return null;
    if (kind === 'int') {
      if (!/^\d+$/.test(raw)) return null;
      const value = Number(raw);
      return value <= Number(max) ? value : null;
    }
    if (!/^\d+(\.\d+)?$/.test(raw)) return null;
    /* 只做范围校验；返回值保持 wire string（服务端 schema 要求十进制字符串）。 */
    const numeric = Number(raw);
    if (!Number.isFinite(numeric) || numeric > Number(max)) return null;
    return raw;
  }

  function collectPayload() {
    clearErrors();
    const enabledInput = $('drawer-enabled');
    const eff = (state.payload && state.payload.meta ? state.payload.meta.effectiveConfig || {} : {});
    const payload = {
      enabled: enabledInput ? enabledInput.checked : true,
      maturityDays: eff.maturityDays,
      fast: {},
      confirmation: {},
    };
    let firstError = '';
    for (const layer of LAYERS) {
      for (const days of WINDOW_DAYS) {
        payload[layer][days] = { warning: {}, critical: {} };
        for (const severity of SEVERITIES) {
          for (const field of THRESHOLD_FIELDS) {
            const path = `${layer}.${days}.${severity}.${field.key}`;
            const input = document.querySelector(`[data-path="${path}"]`);
            const value = parseField((input ? input.value : '').trim(), field.kind, field.max);
            if (value === null) {
              const expected = field.kind === 'int' ? `0..${field.max} 的整数` : `0..${field.max} 的十进制数值`;
              setFieldError(path, `${field.label} 必须为 ${expected}`);
              if (!firstError) firstError = `${path} 非法：需要 ${expected}`;
              continue;
            }
            payload[layer][days][severity][field.key] = value;
          }
        }
      }
    }
    if (firstError) {
      setFormStatus(`表单校验未通过，未提交：${firstError}`, 'error');
      return null;
    }
    return payload;
  }

  function applyServerError(message) {
    const match = /(fast|confirmation)\.(\d)\.(warning|critical)/.exec(message || '');
    const field = THRESHOLD_FIELDS.map((entry) => entry.key).find((key) => (message || '').includes(key));
    if (match && field) setFieldError(`${match[1]}.${match[2]}.${match[3]}.${field}`, message);
    else if (match) setGroupError(`group-${match[1]}-${match[2]}-${match[3]}`, message);
    else setFormStatus(`服务端拒绝（未写入）：${message}`, 'error');
  }

  function applyConfigDetail(detail) {
    state.draftVersion = typeof detail.draftVersion === 'number' ? detail.draftVersion : null;
    const d = drawerElements();
    d.reload.hidden = true;
    if (state.draftVersion === null) {
      setFormStatus('该 key 还没有可保存的草稿版本（draftVersion 缺失）；请先在运行配置页创建。', 'error');
    } else {
      setFormStatus(
        `草稿版本 draftVersion=${state.draftVersion} · hasDraft=${detail.hasDraft === true} · ` +
          `publishedVersion=${detail.publishedVersion === null || detail.publishedVersion === undefined ? '无' : detail.publishedVersion}`,
        'info',
      );
    }
  }

  async function loadConfigDetail() {
    if (!state.canEdit) return;
    try {
      const detail = await api(ITEM_PATH);
      applyConfigDetail(detail);
    } catch (err) {
      state.draftVersion = null;
      setFormStatus(`无法读取草稿版本（${err.message}）：保存草稿 / 发布已禁用，请到运行配置页处理。`, 'error');
    }
    syncDrawerButtons();
  }

  function syncDrawerButtons() {
    const ready = state.canEdit && state.draftVersion !== null;
    els.draftSave.disabled = !ready;
    els.publish.disabled = !ready;
    els.reset.disabled = !state.canEdit;
    const eff = (state.payload && state.payload.meta ? state.payload.meta.effectiveConfig : null) || {};
    const seedOnly = eff.source === 'seed_fallback';
    els.loadBacktest.disabled = !state.canEdit;
    els.loadBacktest.setAttribute('aria-disabled', seedOnly ? 'false' : 'true');
    els.loadBacktest.title = seedOnly
      ? '载入 meta.effectiveConfig.thresholds（回测暂定 seed）：需二次确认，只写草稿，不自动发布'
      : '当前已有已发布版本；回测暂定 seed 只在配置来源为 seed_fallback 时可用';
    els.actionNote.textContent = seedOnly
      ? '配置来源 = seed_fallback：载入的是服务端回测暂定 seed（二次确认、只写草稿）。'
      : '配置来源 = 已发布版本：重置只会恢复表单为已发布 payload，不会覆盖运行时权威。';
  }

  async function saveDraft() {
    const payload = collectPayload();
    if (!payload) return false;
    if (state.draftVersion === null) {
      setFormStatus('缺少 draftVersion，无法保存草稿。', 'error');
      return false;
    }
    setFormStatus('正在保存草稿…', 'info');
    try {
      const detail = await api(DRAFT_PATH, {
        method: 'PUT',
        body: JSON.stringify({ expectedDraftVersion: state.draftVersion, payload, rollout: [] }),
      });
      applyConfigDetail(detail);
      setFormStatus(`已保存草稿（draftVersion=${state.draftVersion}）；发布后才会成为运行时权威。`, 'ok');
      syncDrawerButtons();
      return true;
    } catch (err) {
      if (err.status === 409) {
        drawerElements().reload.hidden = false;
        setFormStatus(`草稿版本冲突（409）：${err.message}。请点击下方「重新加载草稿版本」后再保存。`, 'conflict');
      } else {
        applyServerError(err.message);
      }
      return false;
    }
  }

  async function publishDraft() {
    if (!(await saveDraft())) return;
    try {
      const result = await api(PUBLISH_PATH, {
        method: 'POST',
        body: JSON.stringify({ expectedDraftVersion: state.draftVersion, comment: 'SPU 利润劣化告警阈值（页面抽屉发布）' }),
      });
      setFormStatus(`已发布 v${result.publishedVersion}；刷新页面读取新的 effective config。`, 'ok');
    } catch (err) {
      if (err.status === 409) {
        drawerElements().reload.hidden = false;
        setFormStatus(`草稿版本冲突（409）：${err.message}。请重新加载草稿版本后再发布。`, 'conflict');
      } else {
        applyServerError(err.message);
      }
    }
  }

  async function loadBacktestSeed() {
    const eff = (state.payload && state.payload.meta ? state.payload.meta.effectiveConfig : null) || {};
    if (eff.source !== 'seed_fallback') {
      setFormStatus('当前配置来源不是 seed_fallback，没有可载入的回测暂定 seed。', 'error');
      return;
    }
    if (!window.confirm('载入回测暂定会覆盖表单里的全部阈值，并且只写入草稿，不会自动发布。继续？')) return;
    if (!window.confirm('二次确认：这些是回测暂定值，不是生产确认阈值；草稿会保留「回测暂定」标签。只写入草稿？')) return;
    const loaded = renderThresholdTables(eff, false);
    if (!loaded) {
      setFormStatus('没有可载入的回测暂定 payload。', 'error');
      return;
    }
    setFormStatus('已载入回测暂定 seed（标签：回测暂定）；正在写入草稿…', 'info');
    await saveDraft();
  }

  function openDrawer() {
    const d = drawerElements();
    if (!d.drawer.open) d.drawer.showModal();
    els.settings.setAttribute('aria-expanded', 'true');
    const eff = (state.payload && state.payload.meta ? state.payload.meta.effectiveConfig : null) || {};
    renderDrawerMeta(eff);
    /* dataset.rendered 只由 renderThresholdTables 置位：首个载荷到达前打开抽屉
       不会把抽屉永久标成已渲染，loadConfigProjection 随后会补上表格。 */
    if (d.drawer.dataset.rendered !== '1' && eff.thresholds) {
      renderThresholdTables(eff, !state.canEdit);
    }
    d.readonlyNote.hidden = state.canEdit;
    d.actions.hidden = !state.canEdit;
    syncDrawerButtons();
    if (state.canEdit) loadConfigDetail();
    else {
      setFormStatus('只读会话：抽屉只显示已发布的有效投影；如需改动请联系运维在运行配置页发布。', 'info');
      els.drawerClose.focus();
    }
  }

  function closeDrawer() {
    const d = drawerElements();
    if (d.drawer.open) d.drawer.close();
    els.settings.setAttribute('aria-expanded', 'false');
    els.settings.focus();
  }

  /* ---------- 事件绑定 ---------- */

  function bindEvents() {
    els.shop.addEventListener('change', () => {
      state.shopPk = Number(els.shop.value);
      state.offset = 0;
      syncUrl();
      load();
    });
    els.spuIds.addEventListener('change', applySpuScope);
    els.spuIds.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        applySpuScope();
      }
    });
    els.alertState.addEventListener('change', () => {
      state.alertState = els.alertState.value;
      state.offset = 0;
      syncUrl();
      load();
    });
    els.windowTabs.addEventListener('click', (event) => {
      const tab = event.target.closest('.op-window-tab');
      if (!tab) return;
      state.windowDays = tab.dataset.windowDays || '';
      state.offset = 0;
      syncFilterControls();
      syncUrl();
      load();
    });
    els.severity.addEventListener('change', () => {
      state.severity = els.severity.value;
      state.offset = 0;
      syncUrl();
      load();
    });
    els.anchorDate.addEventListener('change', () => {
      state.anchorDate = els.anchorDate.value;
      state.offset = 0;
      syncUrl();
      load();
    });
    els.limit.addEventListener('change', () => {
      state.limit = Number(els.limit.value);
      state.offset = 0;
      syncUrl();
      load();
    });
    els.rows.addEventListener('click', onSummaryTrigger);
    els.rows.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
        event.preventDefault();
        onSummaryTrigger(event);
      }
    });
    els.cards.addEventListener('click', onSummaryTrigger);
    els.cards.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
        event.preventDefault();
        onSummaryTrigger(event);
      }
    });
    els.summaryClose.addEventListener('click', hideSummary);
    $('btn-refresh').addEventListener('click', () => load());
    els.pagerPrev.addEventListener('click', () => {
      state.offset = Math.max(0, state.offset - state.limit);
      syncUrl();
      load();
    });
    els.pagerNext.addEventListener('click', () => {
      state.offset += state.limit;
      syncUrl();
      load();
    });
    els.settings.addEventListener('click', openDrawer);
    $('drawer-close').addEventListener('click', closeDrawer);
    $('btn-drawer-cancel').addEventListener('click', closeDrawer);
    els.drawer.addEventListener('close', () => els.settings.setAttribute('aria-expanded', 'false'));
    els.drawer.addEventListener('cancel', (event) => {
      event.preventDefault();
      closeDrawer();
    });
    els.drawer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeDrawer();
      }
    });
    els.draftSave.addEventListener('click', () => saveDraft());
    els.publish.addEventListener('click', () => publishDraft());
    els.loadBacktest.addEventListener('click', () => loadBacktestSeed());
    els.reset.addEventListener('click', () => {
      const eff = (state.payload && state.payload.meta ? state.payload.meta.effectiveConfig : null) || {};
      const loaded = renderThresholdTables(eff, !state.canEdit);
      setFormStatus(
        loaded
          ? '已把表单恢复为当前已发布 payload（只改表单，没有写入运行时权威）。'
          : '缺少已发布 thresholds，无法重置。',
        loaded ? 'ok' : 'error',
      );
    });
    drawerElements().reload.addEventListener('click', () => loadConfigDetail());
  }

  async function initIdentity() {
    try {
      const me = await api('/auth/me');
      state.role = me && me.role ? String(me.role) : '';
      state.canEdit = ['readwrite', 'admin'].includes(state.role);
      if (me && me.authenticated) {
        els.identity.textContent = `${me.displayName || me.username || '会话用户'} · ${state.role || '未知档位'}`;
      }
    } catch {
      state.role = '';
      state.canEdit = false;
    }
  }

  async function init() {
    els.shop = $('filter-shop');
    els.spuIds = $('filter-spu-ids');
    els.spuFeedback = $('filter-spu-feedback');
    els.windowTabs = $('filter-window-days');
    els.alertState = $('filter-state');
    els.severity = $('filter-severity');
    els.anchorDate = $('filter-anchor-date');
    els.limit = $('filter-limit');
    els.filterEcho = $('filter-echo');
    els.banner = $('alert-banner');
    els.bannerIcon = $('alert-banner-icon');
    els.bannerText = $('alert-banner-text');
    els.bannerMeta = $('alert-banner-meta');
    els.bannerProvisional = $('alert-banner-provisional');
    els.pageNote = $('page-anchor-note');
    els.freshness = $('alert-freshness');
    els.totals = $('alert-totals');
    els.totalRows = $('total-rows');
    els.totalWarning = $('total-warning');
    els.totalCritical = $('total-critical');
    els.totalInsufficient = $('total-insufficient');
    els.totalSpus = $('total-spus');
    els.status = $('alert-status');
    els.rows = $('alert-rows');
    els.tfoot = $('alert-tfoot');
    els.cards = $('alert-cards');
    els.summary = $('alert-summary');
    els.summaryMeta = $('alert-summary-meta');
    els.summaryGrid = $('alert-summary-grid');
    els.summaryFoot = $('alert-summary-foot');
    els.summaryClose = $('btn-summary-close');
    els.pagerLabel = $('alert-pager-label');
    els.pagerPrev = $('alert-pager-prev');
    els.pagerNext = $('alert-pager-next');
    els.settings = $('btn-settings');
    els.drawer = $('settings-drawer');
    els.drawerClose = $('drawer-close');
    els.draftSave = $('btn-draft-save');
    els.publish = $('btn-publish');
    els.reset = $('btn-reset');
    els.loadBacktest = $('btn-load-backtest');
    els.actionNote = $('drawer-action-note');
    els.drawerProvisional = $('drawer-provisional');
    els.identity = $('ops-identity');

    readUrlState();
    bindEvents();
    syncUrl();
    await initIdentity();
    await loadShops();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

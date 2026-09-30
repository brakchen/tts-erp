/**
 * dashboard.js — 主页仪表板行为
 *
 * 功能：
 * 1. 身份验证检查
 * 2. 店铺列表加载
 * 3. 数据摘要加载（待处理成本、今日订单等）
 * 4. 系统状态检查
 * 5. 数据同步状态（GET /v2/sync/status：每个周期作业的最近一次同步时间、
 *    预计下次同步时间；落后 ≥2 个周期标红灯。每 60s 自动刷新）
 */

(function () {
  'use strict';

  // ---------- CONFIG ----------
  // Public path prefix: "/tts" behind NGINX, "" on :9877 directly.
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9\/_-]*$/i.test(PREFIX)) PREFIX = "";
  var API = PREFIX;
  const HEADERS = { 'Content-Type': 'application/json' };

  // ---------- STATE ----------
  let currentUser = null;
  let shops = [];
  let shopTotal = null;
  let summaryState = { missingCost: null, totalSpu: null };

  // ---------- DOM REFS ----------
  const $identity = document.getElementById('ops-identity');
  const $shopList = document.getElementById('shop-list');
  const $summaryCards = document.getElementById('summary-cards');
  const $summaryScope = document.getElementById('summary-scope');
  const $systemStatus = document.getElementById('system-status');
  const $syncBody = document.getElementById('sync-status-body');
  const $syncUpdatedAt = document.getElementById('sync-updated-at');


  // ---------- INIT ----------
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    installAdDailyNav();
    await checkAuth();
    await Promise.all([
      loadShops(),
      loadSummary(),
      checkSystemStatus(),
      loadSyncStatus(),
    ]);
    // 同步状态是运维监控面：周期作业可能随时间变红，60s 轮询一次。
    setInterval(loadSyncStatus, 60000);
  }

  // ---------- QUICK NAVIGATION ----------
  function installAdDailyNav() {
    const nav = document.querySelector('.quick-nav .nav-cards');
    if (!nav || nav.querySelector('[data-page="ad-daily"]')) return;

    const link = document.createElement('a');
    link.href = '../../v2/pages/ad-daily';
    link.className = 'nav-card';
    link.dataset.page = 'ad-daily';

    const icon = document.createElement('span');
    icon.className = 'nav-card-icon';
    icon.textContent = '▥';
    const title = document.createElement('span');
    title.className = 'nav-card-title';
    title.textContent = '广告日明细';
    const description = document.createElement('span');
    description.className = 'nav-card-desc';
    description.textContent = '按日期、计划和商品核查插件采集的广告事实';

    link.append(icon, title, description);
    nav.append(link);
  }


  // ---------- AUTH ----------
  async function checkAuth() {
    try {
      const res = await fetch(`${API}/v2/auth/me`, { credentials: 'same-origin' });
      if (!res.ok) {
        window.location.href = `${API}/v2/auth/login?next=${encodeURIComponent(location.pathname)}`;
        return;
      }
      currentUser = await res.json();
      if ($identity) {
        $identity.textContent = currentUser.role || 'unknown';
      }
    } catch {
      console.error('Auth check failed');
    }
  }



  // ---------- SHOPS ----------
  async function loadShops() {
    try {
      const res = await fetch(`${API}/v2/commerce/channel-accounts?limit=500`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) return;
      const totalHeader = res.headers.get('X-Total-Count');
      shopTotal = totalHeader === null ? null : Number(totalHeader);
      if (!Number.isFinite(shopTotal)) shopTotal = null;
      shops = await res.json();
      renderShopList();
      renderSummary(summaryState);
    } catch (e) {
      console.error('Failed to load shops:', e);
    }
  }

  function renderShopList() {
    if (!$shopList) return;

    if (!shops || shops.length === 0) {
      $shopList.innerHTML = `
        <div class="op-empty-state">
          <span class="op-empty-icon">📦</span>
          <span>暂无店铺 — <a href="../../v2/pages/shops">去注册</a></span>
        </div>`;
      return;
    }

    const rows = shops.map(s => {
      const id = s.shop_id || s.external_account_id || '?';
      const name = s.account_name || s.name || '(未命名)';
      const region = s.region || '?';
      const source = s.source || s.data_source || 'api';
      const badgeClass = source === 'plugin' ? 'badge-plugin' : 'badge-api';
      return `
        <div class="shop-item" data-shop-pk="${s.id || s.shop_pk || ''}">
          <div class="shop-item-main">
            <span class="shop-name">${esc(name)}</span>
            <span class="shop-id mono">${esc(id)}</span>
          </div>
          <div class="shop-item-meta">
            <span class="shop-region">${esc(region)}</span>
            <span class="badge ${badgeClass}">${esc(source)}</span>
          </div>
        </div>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $shopList.innerHTML = rows;
  }

  // ---------- SUMMARY ----------
  async function loadSummary() {
    try {
      const res = await fetch(`${API}/v2/reporting/coverage`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) throw new Error(`coverage HTTP ${res.status}`);
      const data = await res.json();
      summaryState = {
        missingCost: data.missing_cost_spus,
        totalSpu: data.total_spus,
      };
      renderSummary(summaryState);
    } catch (e) {
      console.error('Failed to load summary:', e);
      summaryState = { missingCost: null, totalSpu: null };
      renderSummary(summaryState);
    }
  }

  function renderSummary(summary) {
    if (!$summaryCards) return;

    // 更新店铺范围标识
    if ($summaryScope) {
      if (shopTotal === 0) {
        $summaryScope.textContent = '无店铺';
      } else if (shopTotal === 1 && shops.length === 1) {
        const s = shops[0];
        const name = s.account_name || s.name || '(未命名)';
        $summaryScope.textContent = `${name} · ${s.region || '?'}`;
      } else if (shopTotal !== null) {
        $summaryScope.textContent = `全店铺 (${shopTotal})`;
      } else {
        $summaryScope.textContent = '全店铺';
      }
    }

    const cards = [
      {
        label: '待录成本',
        value: summary.missingCost !== null ? summary.missingCost : '—',
        icon: '📝',
        hint: '缺少采购成本的 SPU 数量',
        link: '../../v2/pages/manual-costs',
        accent: summary.missingCost > 0 ? 'warn' : 'ok',
      },
      {
        label: 'SPU 总数',
        value: summary.totalSpu !== null ? summary.totalSpu : '—',
        icon: '📊',
        hint: '已跟踪的 SPU 数量',
        link: '../../v2/pages/spu-roi',
        accent: 'neutral',
      },
      {
        label: '已注册店铺',
        value: shopTotal !== null ? shopTotal : '—',
        icon: '🏪',
        hint: '已登记的店铺数量',
        link: '../../v2/pages/shops',
        accent: 'neutral',
      },
      {
        label: '枚举映射',
        value: '管理',
        icon: '🔤',
        hint: '配置 SPU ROI 钻取面板的枚举中文化翻译',
        link: '../../v2/pages/enum-map',
        accent: 'neutral',
      },
    ];

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data in static template
    $summaryCards.innerHTML = cards.map(c => `
      <a href="${c.link}" class="summary-card summary-${c.accent}">
        <div class="summary-icon">${c.icon}</div>
        <div class="summary-content">
          <div class="summary-value">${c.value}</div>
          <div class="summary-label">${c.label}</div>
        </div>
        <div class="summary-hint">${c.hint}</div>
      </a>
    `).join('');
  }

  // ---------- SYSTEM STATUS ----------
  async function checkSystemStatus() {
    if (!$systemStatus) return;

    try {
      const res = await fetch(`${API}/healthz`, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        // pi-lens-ignore: no-unsafe-innerhtml — trusted healthz response
        $systemStatus.innerHTML = `
          <span class="status-dot status-ok"></span>
          <span>API 正常</span>
          <span class="status-detail mono">auth: ${data.auth_mode || '?'}</span>`;
      } else {
        // pi-lens-ignore: no-unsafe-innerhtml — static error indicator, no-unsafe-innerhtml
        $systemStatus.innerHTML = `
          <span class="status-dot status-err"></span>
          <span>API 异常 (${res.status})</span>`;
      }
    } catch {
      $systemStatus.innerHTML = `
        <span class="status-dot status-err"></span>
        <span>API 不可达</span>`;
    }
  }

  // ---------- SYNC STATUS ----------
  async function loadSyncStatus() {
    if (!$syncBody) return;
    try {
      const res = await fetch(`${API}/v2/sync/status`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        renderSyncError(`加载失败 (${res.status})`);
        return;
      }
      const data = await res.json();
      renderSyncStatus(data);
    } catch (e) {
      console.error('Failed to load sync status:', e);
      renderSyncError('接口不可达');
    }
  }

  function renderSyncError(msg) {
    // pi-lens-ignore: no-unsafe-innerhtml — static template, msg 来自本文件字面量/HTTP 状态码
    $syncBody.innerHTML = `<tr><td colspan="6" class="sync-loading">${esc(msg)}</td></tr>`;
  }

  function renderSyncStatus(data) {
    const jobs = (data && data.jobs) || [];
    if ($syncUpdatedAt && data.server_time) {
      $syncUpdatedAt.textContent = `刷新于 ${fmtTime(data.server_time)}`;
    }
    if (jobs.length === 0) {
      renderSyncError('暂无周期作业');
      return;
    }
    // 红灯排最前，其后按作业名稳定排序。
    const sevRank = { crit: 0, warn: 1, ok: 2, unknown: 3 };
    const sorted = jobs.slice().sort((a, b) =>
      (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9) ||
      String(a.job_name).localeCompare(String(b.job_name))
    );
    const rows = sorted.map(j => {
      const dotClass = `sync-dot-${j.severity || 'unknown'}`;
      let statusText;
      if (j.last_status === 'succeeded') statusText = '成功';
      else if (j.last_status === 'failed') statusText = `<span class="sync-status-text-failed">失败</span>`;
      else if (j.last_status === 'running') statusText = '运行中…';
      else statusText = '从未运行';
      const errHtml = j.last_error
        ? `<span class="sync-err-msg" title="${esc(j.last_error)}">${esc(j.last_error)}</span>`
        : '';
      const lateHint = (j.severity === 'crit' || j.severity === 'warn') && j.cycles_late != null
        ? ` · 落后 ${j.cycles_late.toFixed(1)} 周期`
        : '';
      return `
        <tr class="sync-sev-${j.severity || 'unknown'}">
          <td><span class="sync-dot ${dotClass}" title="${sevLabel(j.severity)}"></span></td>
          <td class="sync-col-name">${esc(j.job_name)}</td>
          <td>${esc(fmtInterval(j.interval_seconds))}</td>
          <td>${esc(fmtTime(j.last_run_at))}${j.last_run_at ? esc(` (${fmtAgo(j.last_run_at)})`) : ''}</td>
          <td>${esc(fmtTime(j.next_expected_at))}</td>
          <td>${statusText}${esc(lateHint)}${errHtml}</td>
        </tr>`;
    }).join('');
    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $syncBody.innerHTML = rows;
  }

  function sevLabel(sev) {
    switch (sev) {
      case 'ok': return '正常（一个周期内）';
      case 'warn': return '落后 1 个周期';
      case 'crit': return '红灯：落后 ≥2 个周期';
      default: return '未知（从未运行或无周期配置）';
    }
  }

  function fmtInterval(secs) {
    if (!secs) return '—';
    if (secs % 3600 === 0) return `${secs / 3600} 小时`;
    if (secs % 60 === 0) return `${secs / 60} 分钟`;
    return `${secs} 秒`;
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtAgo(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  }

  // ---------- UTILS ----------
  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

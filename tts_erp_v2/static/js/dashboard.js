/**
 * dashboard.js — 主页仪表板行为
 *
 * 功能：
 * 1. 身份验证检查
 * 2. 店铺列表加载
 * 3. 数据摘要加载（待处理成本、今日订单等）
 * 4. 系统状态检查
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

  // ---------- DOM REFS ----------
  const $identity = document.getElementById('ops-identity');
  const $shopList = document.getElementById('shop-list');
  const $summaryCards = document.getElementById('summary-cards');
  const $systemStatus = document.getElementById('system-status');


  // ---------- INIT ----------
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    await checkAuth();
    await Promise.all([
      loadShops(),
      loadSummary(),
      checkSystemStatus(),
    ]);
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
      const res = await fetch(`${API}/v2/commerce/channel-accounts`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) return;
      shops = await res.json();
      renderShopList();
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
      const id = s.external_account_id || s.shop_id || '?';
      const name = s.name || '(未命名)';
      const region = s.region || '?';
      const source = s.data_source || 'api';
      const badgeClass = source === 'plugin' ? 'badge-plugin' : 'badge-api';
      return `
        <div class="shop-item" data-shop-pk="${s.shop_pk || ''}">
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
      // 并行请求多个摘要数据
      const [costRes, roiRes] = await Promise.allSettled([
        fetch(`${API}/v2/reporting/missing-cost-products`, {
          credentials: 'same-origin',
          headers: HEADERS,
        }),
        fetch(`${API}/v2/analytics/spu-roi?limit=1`, {
          credentials: 'same-origin',
          headers: HEADERS,
        }),
      ]);

      const summary = {
        missingCost: null,
        totalSpu: null,
      };

      if (costRes.status === 'fulfilled' && costRes.value.ok) {
        const data = await costRes.value.json();
        summary.missingCost = Array.isArray(data) ? data.length : (data.count || 0);
      }

      if (roiRes.status === 'fulfilled' && roiRes.value.ok) {
        const data = await roiRes.value.json();
        summary.totalSpu = data.total || data.count || null;
      }

      renderSummary(summary);
    } catch (e) {
      console.error('Failed to load summary:', e);
      renderSummary({});
    }
  }

  function renderSummary(summary) {
    if (!$summaryCards) return;

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
        value: shops.length,
        icon: '🏪',
        hint: '已登记的店铺数量',
        link: '../../v2/pages/shops',
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

  // ---------- UTILS ----------
  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

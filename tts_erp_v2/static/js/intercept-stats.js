/**
 * intercept-stats.js — 拦截统计概览页面行为
 *
 * 功能：
 * 1. 总览统计卡片（总请求数/白名单命中/今日请求/错误请求）
 * 2. 按域名分布（进度条）
 * 3. 按方法分布（进度条）
 * 4. 按状态码分布（进度条）
 * 5. 最近 7 天数据
 */

(function () {
  'use strict';

  // ---------- CONFIG ----------
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, '');
  if (!/^\/[a-z0-9\/_-]*$/i.test(PREFIX)) PREFIX = '';
  var API = PREFIX;
  const HEADERS = { 'Content-Type': 'application/json' };
  const DAYS = 7;

  // ---------- STATE ----------
  let stats = null;

  // ---------- DOM REFS ----------
  const $identity = document.getElementById('ops-identity');
  const $totalRequests = document.getElementById('stat-total-requests');
  const $whitelisted = document.getElementById('stat-whitelisted');
  const $todayRequests = document.getElementById('stat-today-requests');
  const $errorRequests = document.getElementById('stat-error-requests');
  const $byHost = document.getElementById('dist-by-host');
  const $byMethod = document.getElementById('dist-by-method');
  const $byStatus = document.getElementById('dist-by-status');
  const $dailyChart = document.getElementById('daily-chart');
  const $loading = document.getElementById('loading');
  const $error = document.getElementById('error-msg');

  // ---------- INIT ----------
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    await checkAuth();
    await loadStats();
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

  // ---------- LOAD ----------
  async function loadStats() {
    try {
      showLoading(true);
      const res = await fetch(`${API}/v2/intercept/requests/stats?days=${DAYS}`, {
        credentials: 'same-origin',
        headers: HEADERS,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showError(err.detail || `加载失败 (${res.status})`);
        return;
      }
      stats = await res.json();
      renderAll();
    } catch (e) {
      console.error('Failed to load stats:', e);
      showError('加载失败，请检查网络');
    } finally {
      showLoading(false);
    }
  }

  // ---------- RENDER ----------
  function renderAll() {
    renderOverview();
    renderDistribution($byHost, stats.by_host, '域名');
    renderDistribution($byMethod, stats.by_method, '方法');
    renderDistribution($byStatus, stats.by_status, '状态码');
    renderDailyChart();
  }

  function renderOverview() {
    if (!stats) return;
    const set = (el, val) => { if (el) el.textContent = val != null ? formatNumber(val) : '—'; };
    set($totalRequests, stats.total_requests);
    set($whitelisted, stats.whitelisted_requests);
    set($todayRequests, stats.today_requests);
    set($errorRequests, stats.error_requests);
  }

  function renderDistribution($container, data, label) {
    if (!$container || !data) return;

    const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
    const total = entries.reduce((sum, [, v]) => sum + v, 0);

    if (entries.length === 0) {
      $container.innerHTML = `<div class="dist-empty">暂无${label}数据</div>`;
      return;
    }

    const maxVal = entries[0][1];
    const rows = entries.map(([key, value]) => {
      const pct = total > 0 ? (value / total * 100).toFixed(1) : '0';
      const barWidth = maxVal > 0 ? (value / maxVal * 100) : 0;
      return `
        <div class="dist-row">
          <span class="dist-label">${esc(key)}</span>
          <div class="dist-bar-wrap">
            <div class="dist-bar" style="width: ${barWidth}%"></div>
          </div>
          <span class="dist-value">${formatNumber(value)} (${pct}%)</span>
        </div>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $container.innerHTML = rows;
  }

  function renderDailyChart() {
    if (!$dailyChart || !stats || !stats.daily) return;

    const days = stats.daily;
    if (days.length === 0) {
      $dailyChart.innerHTML = '<div class="dist-empty">暂无每日数据</div>';
      return;
    }

    const maxVal = Math.max(...days.map(d => d.count), 1);
    const rows = days.map(d => {
      const date = d.date || '—';
      const count = d.count || 0;
      const barHeight = (count / maxVal * 100);
      return `
        <div class="daily-col">
          <div class="daily-bar-wrap">
            <div class="daily-bar" style="height: ${barHeight}%"></div>
          </div>
          <span class="daily-count">${formatNumber(count)}</span>
          <span class="daily-date">${esc(date)}</span>
        </div>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $dailyChart.innerHTML = rows;
  }

  // ---------- HELPERS ----------
  function showLoading(show) {
    if ($loading) $loading.style.display = show ? 'block' : 'none';
  }

  function showError(msg) {
    if ($error) {
      $error.textContent = msg;
      $error.style.display = msg ? 'block' : 'none';
    }
  }

  function formatNumber(n) {
    if (n == null) return '—';
    return n.toLocaleString('zh-CN');
  }

  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

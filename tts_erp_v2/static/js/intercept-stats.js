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

  function renderDistribution($container, entries, label) {
    if (!$container || !Array.isArray(entries)) return;

    if (entries.length === 0) {
      $container.innerHTML = `<div class="dist-empty">暂无${label}数据</div>`;
      return;
    }

    const maxVal = Number(entries[0].count) || 0;
    const rows = entries.map(item => {
      const value = Number(item.count) || 0;
      const barWidth = maxVal > 0 ? (value / maxVal * 100) : 0;
      return `
        <div class="dist-row">
          <span class="dist-label">${esc(item.label || '未知')}</span>
          <div class="dist-bar-wrap">
            <div class="dist-bar" style="width: ${barWidth}%"></div>
          </div>
          <span class="dist-value">${formatNumber(value)} (${esc(item.percentage)}%)</span>
        </div>`;
    }).join('');

    // pi-lens-ignore: no-unsafe-innerhtml — trusted backend data, esc()-sanitized
    $container.innerHTML = rows;
  }

  // uPlot 1.6.32 (MIT) — vendored at static/vendor/uplot.iife.min.js,
  // see static/vendor/NOTICE.md. Replaces the hand-rolled div-bar chart.
  let dailyPlot = null;

  function renderDailyChart() {
    if (!$dailyChart || !stats || !stats.daily) return;

    const days = stats.daily;
    if (days.length === 0) {
      if (dailyPlot) { dailyPlot.destroy(); dailyPlot = null; }
      $dailyChart.innerHTML = '<div class="dist-empty">暂无每日数据</div>';
      return;
    }
    if (typeof uPlot === 'undefined') {
      showError('图表库 uPlot 未加载');
      return;
    }

    const xs = days.map(d => {
      const t = Date.parse(`${d.date}T00:00:00`);
      return Number.isFinite(t) ? Math.floor(t / 1000) : 0;
    });
    const ys = days.map(d => Number(d.count) || 0);

    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim() || '#B8390E';

    const opts = {
      width: $dailyChart.clientWidth || 640,
      height: 244,
      padding: [8, 8, 0, 0],
      legend: { show: true },
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      axes: [
        {
          values: (u, splits) => splits.map(v => {
            const d = new Date(v * 1000);
            return `${d.getMonth() + 1}-${d.getDate()}`;
          }),
        },
        { size: 44 },
      ],
      series: [
        {},
        {
          label: '请求数',
          fill: accent,
          paths: uPlot.paths.bars({ size: [0.55, 48] }),
          points: { show: false },
          value: (u, v) => (v == null ? '—' : formatNumber(v)),
        },
      ],
    };

    if (dailyPlot) {
      dailyPlot.setData([xs, ys]);
      dailyPlot.setSize({ width: $dailyChart.clientWidth || 640, height: 244 });
      return;
    }
    $dailyChart.innerHTML = '';
    dailyPlot = new uPlot(opts, [xs, ys], $dailyChart);
  }

  window.addEventListener('resize', () => {
    if (dailyPlot && $dailyChart) {
      dailyPlot.setSize({ width: $dailyChart.clientWidth || 640, height: 244 });
    }
  });

  // ---------- HELPERS ----------
  const FMT_NUM = new Intl.NumberFormat('zh-CN');

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
    return FMT_NUM.format(n);
  }

  function esc(s) {
    const el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }
})();

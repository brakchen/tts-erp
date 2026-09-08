/* tts-erp — SPU 实际 ROI 看板页 JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/spu-roi.
   数据全部来自只读端点 GET /v2/analytics/spu-roi(服务端已算好,§5.1-1),
   本文件只做格式化与展示:金额 $2 位千分位、比率/百分比、红绿判据、分页、
   排序(asc ↔ desc 双向),401 → login。样式复用页面 warm-paper token,零外链。 */

(() => {
  // ---------- constants ----------
  var ENDPOINT_PATH = "/v2/analytics/spu-roi";
  var DEFAULT_SORT = "roi_real";
  var DEFAULT_ORDER = "asc";
  var SORT_LABEL = {
    roi_real: "实际ROI",
    spend: "消耗",
    refund_rate: "退款率",
    refund_rate_qty: "退货率",
    cancel_rate: "取消率",
    net_profit: "净利润",
    sales: "有效销售",
    gmv_sales: "销售",
    gmv_ad: "平台GMV",
    ad_count: "广告数",
    roi_l0: "ROI₀",
    order_count: "有效单",
    cancelled_order_count: "取消单量",
    units_sold: "件数",
    refund_net_amount: "退货",
    return_loss: "全损退款",
    roi_breakeven: "保本ROI",
  };
  var SORTABLE = new Set([
    "roi_real",
    "spend",
    "refund_rate",
    "refund_rate_qty",
    "cancel_rate",
    "net_profit",
    "sales",
    "gmv_sales",
    "gmv_ad",
    "ad_count",
    "order_count",
    "cancelled_order_count",
    "units_sold",
    "return_loss",
    "roi_breakeven",
    "refund_net_amount",
  ]);

  // §7.2 标色默认阈值(常量,页面 ⚙ 可调预留,不锁死)
  var REFUND_RATE_ALERT = 0.3; // 退款率警戒线:> 30% → 标题⚠ + 红字

  // Public path prefix: "/tts" behind NGINX, "" on :9877 directly.
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  // ---------- small helpers ----------
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  }
  // Single choke point for markup writes.
  function html(el, markup) {
    el.innerHTML = markup; // pi-lens-ignore: no-inner-html-js
  }
  // Unwrap API envelopes: { items: [...], total: N, totals, meta }.
  function unwrap(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    return [];
  }
  function fmtMoney(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v);
    if (!Number.isFinite(n)) return "—";
    return (
      "$" +
      n.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }
  function fmtRatio(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  function fmtPct(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v) * 100;
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("en-US", { maximumFractionDigits: 1 }) + "%";
  }
  function fmtInt(v) {
    if (v == null || v === "") return "—";
    var n = parseInt(v, 10);
    if (!Number.isFinite(n)) return "—";
    return String(n);
  }
  function loginUrl() {
    return `${PREFIX}/v2/auth/login?next=${PREFIX}/v2/pages/spu-roi`;
  }

  // ---------- api ----------
  function api(params) {
    var qs = Object.keys(params)
      .filter(
        (k) =>
          params[k] !== null && params[k] !== undefined && params[k] !== "",
      )
      .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(params[k])}`)
      .join("&");
    return fetch(`${PREFIX}${ENDPOINT_PATH}?${qs}`, {
      credentials: "include", // session cookie
      headers: { Accept: "application/json" },
    }).then((r) => {
      // D6: 主表筛选变化 → 清钻取缓存
      clearDrillCache();
      if (r.status === 401) {
        // 401 → 跳登录(console.js 家族行为)
        window.location.href = loginUrl(); // pi-lens-ignore: no-open-redirect-js
        throw new Error("unauthorized");
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
  }

  // ---------- state ----------
  var state = {
    q: "",
    limit: 100,
    includeAll: false,
    shopPk: null, // 店铺筛选(null/""=全部店铺)
    wStart: "", // 日期范围 yyyy-mm-dd(""=不限)
    wEnd: "",
    datesTouched: false, // 用户手动改过日期? (自动回填只发生一次,随后交还用户)
    feeRate: null, // 页面覆写费率(小数),null = 用服务端基线
    // D7 行内 accordion: 一次只展开一行; D6 tab 懒加载缓存,主表筛选变化时清空
    openDrillRow: null,
    drillCache: new Map(),
    sort: DEFAULT_SORT,
    order: DEFAULT_ORDER,
    offset: 0,
    loading: false,
  };
  var lastTotal = 0;

  // ---------- 顶部操作员(/v2/auth/me → {authenticated, role}) ----------
  function loadMe() {
    return fetch(`${PREFIX}/v2/auth/me`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        var el = $("#ops-identity");
        if (!el) return;
        if (me && me.authenticated === true) {
          // /v2/auth/me 只回 {authenticated, role};操作员身份 = role 文本
          var role = esc(me.role || "readonly");
          html(
            el,
            `当前操作员：<code>${role}</code> · <a href="#" id="btn-logout">退出</a>`,
          );
          var lo = $("#btn-logout");
          if (lo) {
            // logout 是 POST(console.js 家族是 <a> 占位);POST 成功后
            // 回登录页(next=当前页),登录后自动回本页
            lo.addEventListener("click", (e) => {
              e.preventDefault();
              fetch(`${PREFIX}/v2/auth/logout`, {
                method: "POST",
                credentials: "include",
              })
                .catch(() => {})
                .then(() => {
                  window.location.href = loginUrl(); // pi-lens-ignore: no-open-redirect-js
                });
            });
          }
        } else {
          html(el, `<a href="${loginUrl()}">登录</a>`);
        }
      })
      .catch(() => {});
  }

  // ---------- 渲染 ----------
  function rowMarkup(it) {
    var np = parseFloat(it.net_profit);
    var npNeg = Number.isFinite(np) && np < 0;
    var isBad = npNeg; // C3: 仅按净利判
    var warnDefault = it.cost_source === "DEFAULT_K1";
    var rr = parseFloat(it.refund_rate);
    var rrHigh = Number.isFinite(rr) && rr > REFUND_RATE_ALERT;
    var settledCount = Number(it.settled_order_count || 0);
    var orderCount = Number(it.order_count || 0);
    var hasUnsettled = settledCount > 0 && settledCount < orderCount;
    var img = it.main_image_url
      ? `<img class="spu-img" alt="" src="${esc(it.main_image_url)}" data-zoom="${esc(it.main_image_url)}">`
      : '<span class="spu-img-missing" aria-hidden="true">无主图</span>';
    var warn =
      '<span class="warn-default" data-tip="无成本记录（人工标注/采购单/货源价均未命中），按默认 40 元/件">⚠</span> ';
    var warnRr = rrHigh
      ? '<span class="warn-rr" data-tip="退款率超过 30% 警戒线">⚠</span> '
      : "";
    var warnUnsettled = hasUnsettled
      ? '<span class="warn-unsettled" data-tip="含未结算订单，净利为估算">≈</span> '
      : "";
    var status =
      it.status === "ACTIVATE" || !it.status
        ? ""
        : `<span class="spu-status is-down">${esc(it.status)}</span>`;
    var costTitle =
      it.cost_source === "MANUAL"
        ? `人工标注的采购成交价(${esc(it.unit_cost_used)} USD/件)`
        : "默认 40 元/件 ≈ $5.95 ⚠";
    var profitClass = npNeg ? ' class="np-red"' : "";
    var adCell =
      it.ad_count === 0 || it.ad_count == null
        ? '<span class="no-ad" data-tip="该 SPU 无广告投放">无投放</span>'
        : fmtInt(it.ad_count);
    var fmtPctOrDash = (v) =>
      v === null || v === undefined || v === "" ? "—" : fmtPct(v);
    // D8 6 列: 商品 / 广告消耗 / 有效GMV / 有效出单量 / 取消率 / 全损退款率% / 净利润
    return (
      `<tr class="${isBad ? "row-bad" : ""}" data-spupk="${esc(it.spu_pk)}">` +
      `<td class="td-left"><span class="td-spu-cell">${img}<span class="td-spu-meta">` +
      `<span class="td-spu">${esc(it.spu_id)}</span>` +
      `<span class="td-title" data-tip="${esc(it.title || "")}">${warnUnsettled}${warnDefault ? warn : ""}${warnRr}${esc(it.title || "")}${status}</span></span></span></td>` +
      `<td>${adCell}</td>` +
      `<td>${fmtMoney(it.spend)}</td>` +
      `<td>${fmtMoney(it.sales)}</td>` +
      `<td>${fmtInt(it.order_count)}</td>` +
      `<td>${fmtPctOrDash(it.cancel_rate)}</td>` +
      `<td>${fmtPctOrDash(it.full_loss_rate)}</td>` +
      `<td${profitClass}>${fmtMoney(it.net_profit)}</td>` +
      "</tr>"
    );
  }


  function renderError(msg) {
    html(
      $("#rows"),
      `<tr><td colspan="25" class="op-error">${esc(msg)} · <a href="#" id="retry-link">重试</a></td></tr>`,
    );
    var link = $("#retry-link");
    if (link) {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        load();
      });
    }
  }

  function renderEmpty() {
    html(
      $("#rows"),
      '<tr><td colspan="25" class="op-empty">没有匹配该 spu_id 的 SPU（试试完整 ID）</td></tr>',
    );
  }

  function render(payload) {
    var items = unwrap(payload);
    var totals = payload.totals || {};
    var meta = payload.meta || {};
    lastTotal = payload.total || 0;

    // 结余带(§7.1 2026-09-06 扩展:去 SPU 数,新增 GMV/有效单量/总单量/取消单量)
    $("#sum-spend").textContent = fmtMoney(totals.spend);
    $("#sum-sales").textContent = fmtMoney(totals.sales);
    $("#sum-gmv").textContent = fmtMoney(totals.gmv);
    $("#sum-orders").textContent = fmtInt(totals.order_count || 0);
    $("#sum-total-orders").textContent = fmtInt(totals.total_orders || 0);
    $("#sum-refund").textContent = fmtMoney(totals.refund_net_amount);
    $("#sum-loss").textContent = fmtMoney(totals.return_loss); // §7.1 全损退款(= return_loss 成本口径)
    $("#sum-cancelled-orders").textContent = fmtInt(
      totals.cancelled_order_count || 0,
    );
    var profit = parseFloat(totals.net_profit);
    var profitEl = $("#sum-profit");
    profitEl.textContent = fmtMoney(totals.net_profit);
    profitEl.classList.toggle("is-err", Number.isFinite(profit) && profit < 0);
    // 整体实际 ROI = totals.roi_real(服务端已算好,§5.1-1 页面不反推)
    var roiEl = $("#sum-roi");
    var roiOverall = totals.roi_real;
    var roiNum = parseFloat(roiOverall);
    if (
      roiOverall !== null &&
      roiOverall !== undefined &&
      roiOverall !== "" &&
      Number.isFinite(roiNum)
    ) {
      roiEl.textContent = fmtRatio(roiOverall);
      roiEl.classList.toggle("is-err", roiNum < 0);
    } else {
      roiEl.textContent = "—"; // Σspend=0 → null
      roiEl.classList.remove("is-err");
    }
    $("#sum-stamp").textContent =
      `全表 USD · 固定汇率 ${meta.fx ? meta.fx.as_of : ""} · ROI 账页`;

    // 表格
    if (items.length) {
      html($("#rows"), items.map(rowMarkup).join(""));
    } else {
      renderEmpty();
    }

    // 分页
    var page = Math.floor(state.offset / state.limit) + 1;
    var pages = Math.max(1, Math.ceil(lastTotal / state.limit));
    $("#pager-label").textContent =
      `第 ${page} / ${pages} 页 · 共 ${lastTotal} 行 · 每页 ${state.limit} 条`;
    var prev = $("#btn-prev");
    var next = $("#btn-next");
    prev.disabled = state.offset <= 0;
    next.disabled = state.offset + state.limit >= lastTotal;

    // 页脚口径行
    var notes = [];
    if (meta.computed_at) {
      notes.push(
        `数据截至 ${esc(String(meta.computed_at).replace("T", " ").slice(0, 19))}`,
      );
    }
    if (meta.window && meta.window.first_day) {
      notes.push(
        `ad 窗口 ${esc(meta.window.first_day)} ~ ${esc(meta.window.last_day)}`,
      );
    }
    if (meta.fee) {
      notes.push(
        `平台佣金费率 ${meta.fee.override === null ? "基线" : "页面覆写"} ${esc(meta.fee.rate)}${meta.fee.mode === "override" ? "(覆写)" : ""}`,
      );
    }
    if (typeof meta.unattributed_refund_lines === "number") {
      notes.push(`未归属退款 ${meta.unattributed_refund_lines} 行`);
    }
    notes.push("默认 30元/件成本(⚠) 行会标注 · 金额已由服务端换算 USD");
    $("#foot-meta").textContent = notes.join(" · ");

    // 起始/截止日真实呈现(2026-09-06):数据有可裁剪跨度(销售∪退款覆盖)且
    // 用户未手动改过日期 → 把输入框回填成当前数据的真实时间范围。全跨度 ≡
    // 不限,结果不变;回填仅作如实呈现,不让 ad 口径产生误解。
    var cw = meta.window || {};
    if (
      cw.coverage_first_day &&
      cw.coverage_last_day &&
      !state.datesTouched &&
      $("#filter-w-start").value === "" &&
      $("#filter-w-end").value === ""
    ) {
      $("#filter-w-start").value = cw.coverage_first_day;
      $("#filter-w-end").value = cw.coverage_last_day;
    }

    applyColToggles(); // 新渲染的行/空态要重新应用 ⚙ 列开关
    updateSortMarkers();
    bindRowAccordion(items);
  }

  function updateSortMarkers() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".op-th-sort"),
      (th) => {
        var field = th.getAttribute("data-sort");
        var mark = th.querySelector(".arrow");
        if (mark) mark.remove();
        if (field === state.sort) {
          var span = document.createElement("span");
          span.className = "arrow";
          span.textContent = state.order === "asc" ? " ▲" : " ▼";
          th.appendChild(span);
        }
      },
    );
    $("#sort-note").textContent =
      `当前排序：${SORT_LABEL[state.sort] || state.sort}${state.order === "asc" ? " ↑" : " ↓"}`;
  }

  // ---------- 钻取面板 (D7 行内 accordion + D6 tab 懒加载) ----------
  function clearDrillCache() { state.drillCache = new Map(); }
  function _drillCacheKey(spuPk, tab) { return [spuPk, tab, state.wStart, state.wEnd].join("|"); }
  function _drillUrl(tab, spuPk) {
    var base = PREFIX + "/v2/analytics/spu-roi/" + encodeURIComponent(spuPk) + "/" + tab;
    var qs = [];
    if (state.wStart) qs.push("w_start=" + encodeURIComponent(state.wStart));
    if (state.wEnd) qs.push("w_end=" + encodeURIComponent(state.wEnd));
    return qs.length ? base + "?" + qs.join("&") : base;
  }
  function fetchDrillTab(spuPk, tab) {
    var key = _drillCacheKey(spuPk, tab);
    if (state.drillCache.has(key)) return Promise.resolve(state.drillCache.get(key));
    return fetch(_drillUrl(tab, spuPk), {
      credentials: "include",
      headers: { Accept: "application/json" },
    }).then(function (r) {
      if (r.status === 401) { window.location.href = loginUrl(); throw new Error("unauthorized"); }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      state.drillCache.set(key, data);
      return data;
    });
  }
  function closeDrillPanel() {
    if (state.openDrillRow) {
      var nxt = state.openDrillRow.nextElementSibling;
      if (nxt && nxt.classList && nxt.classList.contains("op-drill-row")) nxt.remove();
      state.openDrillRow = null;
    }
  }
  function renderProfitSummary(it) {
    function m(v) { return v == null || v === "" ? "—" : fmtMoney(v); }
    function cell(label, value, hint) {
      return '<div class="op-drill-cell"><span class="op-drill-lbl">' + esc(label) +
        (hint ? '<span class="op-hint" data-tip="' + esc(hint) + '">?</span>' : "") +
        '</span><span class="op-drill-val">' + (value == null ? "—" : value) + "</span></div>";
    }
    return '<div class="op-drill-grid">' +
      cell("ROI 实际", fmtRatio(it.roi_real)) +
      cell("ROI 保本", fmtRatio(it.roi_breakeven)) +
      cell("平台佣金", m(it.platform_fee), "平台从销售额直接扣除的全部费用（抽佣/联盟/运费类）") +
      cell("CPA", m(it.cpa)) +
      cell("全损货损$", m(it.return_loss)) +
      cell("单位成本", m(it.unit_cost_used) + " " + (it.cost_source || "")) +
      cell("已结算单", String(it.settled_order_count || 0)) +
      cell("已结 GMV", m(it.settled_sales)) +
      cell("未结 GMV", m(it.unsettled_sales)) +
      cell("全损件数", String(it.full_loss_qty || 0)) +
      cell("全损取消", String(it.full_loss_cancelled_qty || 0)) +
      cell("净收入", m(it.net_revenue)) +
      "</div>";
  }
  function renderProfitTab(it) {
    var settled = Number(it.settled_sales || 0);
    var unsettled = Number(it.unsettled_sales || 0);
    var netRevenue = Number(it.net_revenue || 0);
    var settledNet = settled + unsettled > 0 ? (settled / (settled + unsettled)) * netRevenue : netRevenue;
    var unsettledNet = netRevenue - settledNet;
    var flc = Number(it.full_loss_cancelled_qty || 0);
    var unitCost = Number(it.unit_cost_used || 0);
    var cogsSold = Number(it.units_sold || 0) * unitCost;
    var cogsFlc = flc * unitCost;
    var spend = Number(it.spend || 0);
    var np = Number(it.net_profit || 0);
    function row(label, val, klass, hint) {
      return '<tr' + (klass ? ' class="' + klass + '"' : "") + '>' +
        "<td>" + esc(label) + (hint ? ' <span class="op-hint" data-tip="' + esc(hint) + '">?</span>' : "") +
        "</td><td>" + fmtMoney(val) + "</td></tr>";
    }
    return '<table class="op-pnl-table"><tbody>' +
      row("净收入·已结算(SETTLEMENT 分摊)", settledNet, null, "v7 D2 零值落库后,有交易必有 SETTLEMENT 行;amount=0 即已结算到手 0") +
      row("净收入·未结算(×(1−r̂)×(1−退货率))", unsettledNet, null, "v7 D5:未结算按基线 30.8% × (1−本 SPU 退款率) 估算") +
      row("− 货本·售出件", -cogsSold) +
      row("− 货本·全损取消件(D4 B 补扣)", -cogsFlc, null, "D4 B 切 38301 全损口径:M13b = full_loss_qty × cost,含已出海取消件") +
      row("− 广告消耗", -spend) +
      row("= 净利润", np, np < 0 ? "np-red" : "np-positive", "M18 红绿仅按净利判(C3 拍板);负值红字") +
      "</tbody></table>";
  }
  function renderDrillTabBody(tab, data) {
    if (!data) return '<div class="op-drill-loading">无数据</div>';
    if (tab === "settlements") {
      var ss = data.settlements || [];
      if (!ss.length) return '<div class="op-drill-loading">未结算订单不展示（基线估算见利润构成 tab）。</div>';
      return '<table class="op-pnl-table"><thead><tr><th>订单号</th><th>SETTLEMENT</th><th>分摊比</th><th>statement</th></tr></thead><tbody>' +
        ss.map(function (s) {
          var comp = (s.components || []).filter(function (c) { return c.code === "SETTLEMENT"; })[0];
          var set = comp ? fmtMoney(comp.amount) : "—";
          return "<tr><td>" + esc(s.order_id) + "</td><td>" + set +
            "</td><td>" + esc(s.share_ratio || "—") +
            "</td><td>" + esc(s.statement_time || "—") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    if (tab === "orders") {
      var orders = data.orders || [];
      if (!orders.length) return '<div class="op-drill-loading">无订单</div>';
      return '<table class="op-pnl-table"><thead><tr><th>订单号</th><th>状态</th><th>件</th><th>is_settled</th><th>38301</th><th>全损</th></tr></thead><tbody>' +
        orders.map(function (o) {
          return "<tr><td>" + esc(o.order_id) + "</td><td>" + esc(o.status) +
            "</td><td>" + esc(o.qty) +
            "</td><td>" + (o.is_settled ? "✓" : "—") +
            "</td><td>" + (o.arrived_overseas ? "✓" : "—") +
            "</td><td>" + (o.full_loss ? "⚠" : "—") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    if (tab === "cases") {
      var cases = data.cases || [];
      if (!cases.length) return '<div class="op-drill-loading">无售后 case</div>';
      return '<table class="op-pnl-table"><thead><tr><th>case</th><th>订单</th><th>类型</th><th>状态</th><th>退款</th></tr></thead><tbody>' +
        cases.map(function (c) {
          return "<tr><td>" + esc(c.case_id) + "</td><td>" + esc(c.order_id || "—") +
            "</td><td>" + esc(c.type) + "</td><td>" + esc(c.status) +
            "</td><td>" + fmtMoney(c.refund_amount) + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    if (tab === "ads") {
      var ads = data.ads || [];
      if (!ads.length) return '<div class="op-drill-loading">无广告投放</div>';
      return '<table class="op-pnl-table"><thead><tr><th>campaign_id</th><th>spend</th><th>orders</th><th>窗口</th></tr></thead><tbody>' +
        ads.map(function (a) {
          return "<tr><td>" + esc(a.campaign_id) + "</td><td>" + fmtMoney(a.spend) +
            "</td><td>" + esc(a.orders) +
            "</td><td>" + esc(a.first_day || "—") + " ~ " + esc(a.last_day || "—") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    return '<div class="op-drill-loading">未知 tab</div>';
  }
  function renderDrillLazy(drill, it, which) {
    var body = drill.querySelector('[data-region="body"]');
    body.textContent = "加载中…";
    fetchDrillTab(it.spu_pk, which)
      .then(function (data) {
        body.innerHTML = renderDrillTabBody(which, data);
        if (typeof wireTooltips === "function") wireTooltips();
      })
      .catch(function (e) {
        body.textContent = "加载失败：" + (e && e.message ? e.message : e);
      });
  }
  function openDrillPanel(row, it) {
    if (state.openDrillRow === row) { closeDrillPanel(); return; }
    closeDrillPanel();
    if (!it || !it.spu_pk) return;
    var tpl = document.getElementById("tpl-drilldown-panel");
    if (!tpl) return;
    var frag = tpl.content.cloneNode(true);
    var drillRow = frag.querySelector(".op-drill-row");
    var drill = drillRow.querySelector(".op-drill");
    var banner = drill.querySelector('[data-banner="warn"]');
    var settledCount = Number(it.settled_order_count || 0);
    var orderCount = Number(it.order_count || 0);
    if (settledCount > 0 && settledCount < orderCount) {
      banner.hidden = false;
      banner.textContent = "含未结算订单，净收入为估算（基线 ×(1−r̂)×(1−退货率)）。";
    }
    drill.querySelector('[data-region="summary"]').innerHTML = renderProfitSummary(it);
    drill.querySelector('[data-region="body"]').innerHTML = renderProfitTab(it);
    var tabs = drill.querySelectorAll(".op-drill-tab");
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener("click", function () {
        Array.prototype.forEach.call(tabs, function (t) { t.classList.remove("is-active"); });
        tab.classList.add("is-active");
        var which = tab.getAttribute("data-tab");
        if (which === "pnl") {
          drill.querySelector('[data-region="body"]').innerHTML = renderProfitTab(it);
        } else {
          renderDrillLazy(drill, it, which);
        }
      });
    });
    row.insertAdjacentElement("afterend", drillRow);
    state.openDrillRow = row;
  }
  var _rowAccordionBound = false;
  function bindRowAccordion(items) {
    window.__lastPayloadItems = items || [];
    if (_rowAccordionBound) return;
    var tbody = document.getElementById("rows");
    if (!tbody) return;
    _rowAccordionBound = true;
    tbody.addEventListener("click", function (e) {
      var tr = e.target;
      while (tr && tr.tagName !== "TR") tr = tr.parentElement;
      if (!tr || !tr.dataset || !tr.dataset.spupk) return;
      if (tr.classList && tr.classList.contains("op-drill-row")) return;
      var spuPk = tr.dataset.spupk;
      var it = (window.__lastPayloadItems || []).filter(
        function (i) { return String(i.spu_pk) === String(spuPk); }
      )[0];
      if (it) openDrillPanel(tr, it);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeDrillPanel();
    });
  }

  // ---------- 店铺下拉(GET /v2/commerce/channel-accounts,readonly) ----------
  // 值 = 内部 id/shop_pk,显示 = account_name;默认"全部店铺"(不传 shop_pk =
  // 全部店铺全历史)。401 → 跳登录(与主表 load() 一致);失败只留占位项,
  // 不阻塞主表(空态文案即占位项"全部店铺")。
  function loadShops() {
    fetch(
      `${PREFIX}/v2/commerce/channel-accounts?platform=tiktok&limit=500`,
      { credentials: "include", headers: { Accept: "application/json" } },
    )
      .then((r) => {
        if (r.status === 401) {
          // 401 → 跳登录(console.js 家族行为)
          window.location.href = loginUrl(); // pi-lens-ignore: no-open-redirect-js
          throw new Error("unauthorized");
        }
        if (!r.ok) throw new Error(`shops HTTP ${r.status}`);
        return r.json();
      })
      .then((shops) => {
        var sel = $("#filter-shop");
        if (!sel || !Array.isArray(shops)) return;
        // 保留 HTML 里的"全部店铺"占位项,其后追加店铺选项
        shops.forEach((s) => {
          var opt = document.createElement("option");
          opt.value = String(s.id);
          opt.textContent = s.account_name || `#${s.id}`;
          sel.appendChild(opt);
        });
      })
      .catch(() => {});
  }

  // ---------- load ----------
  function load() {
    if (state.loading) return;
    hideTip(); // 重拉前收起可能悬浮的说明气泡
    state.loading = true;
    html(
      $("#rows"),
      '<tr><td colspan="25" class="op-loading">加载中…</td></tr>',
    );
    var feeParam = null;
    if (state.feeRate !== null && state.feeRate !== "") {
      var f = parseFloat(state.feeRate);
      feeParam = Number.isFinite(f) ? String(f) : null;
    }
    api({
      q: state.q || null,
      sort: state.sort,
      order: state.order,
      limit: state.limit,
      offset: state.offset,
      include_all: state.includeAll ? "true" : "false",
      shop_pk: state.shopPk || null,
      w_start: state.wStart || null,
      w_end: state.wEnd || null,
      fee_rate: feeParam,
    })
      .then((payload) => {
        render(payload);
        state.loading = false;
      })
      .catch((err) => {
        state.loading = false;
        if (err && err.message === "unauthorized") return;
        renderError(
          `加载失败 · ${err && err.message ? err.message : "未知错误"}`,
        );
      });
  }

  function debounce(fn, ms) {
    var t;
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  // ---------- hover 说明气泡([data-tip] 委托;替代原生 title,见页面样式) ----------
  // 页面里任何带 data-tip 的元素(⚠ 图标、标色格、格头/字段口径说明、被截断的
  // 商品全名…)悬停即在此气泡展示文案;位置贴元素上方(顶部空间不足翻到下方),
  // 不跟手、不挡锚点,滚动/点击/离开即收起。行重新渲染也无需重绑(事件委托)。
  var tipEl = null;
  var tipAnchor = null;
  function ensureTip() {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.id = "ops-tip";
      tipEl.setAttribute("role", "tooltip");
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }
  function hideTip() {
    tipAnchor = null;
    if (tipEl) tipEl.hidden = true;
  }
  function tipHit(el) {
    if (!el || !el.closest) return null;
    var t = el.closest("[data-tip]");
    if (!t) return null;
    var text = t.getAttribute("data-tip");
    return text ? { el: t, text: text } : null;
  }
  function showTip(anchor, text) {
    var tip = ensureTip();
    tip.textContent = text; // 纯文本,防注入
    tip.hidden = false;
    var r = anchor.getBoundingClientRect();
    var tw = tip.offsetWidth;
    var th = tip.offsetHeight;
    var x = Math.round(r.left + r.width / 2 - tw / 2);
    x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
    var y = Math.round(r.top - th - 8);
    if (y < 8) y = Math.round(r.bottom + 8); // 上方放不下 → 下方
    tip.style.left = x + "px";
    tip.style.top = y + "px";
    tipAnchor = anchor;
  }
  function wireTooltips() {
    document.addEventListener("mouseover", (e) => {
      var hit = tipHit(e.target);
      if (hit) {
        if (hit.el !== tipAnchor) showTip(hit.el, hit.text);
      } else {
        hideTip();
      }
    });
    document.addEventListener("mouseout", (e) => {
      if (!tipAnchor) return;
      var rel = tipHit(e.relatedTarget);
      if (!rel) hideTip(); // 指针离开所有 data-tip 区域
    });
    window.addEventListener("scroll", hideTip, true); // capture: 容器内滚动也收起,防错位
    window.addEventListener("resize", hideTip);
    document.addEventListener("click", hideTip);
  }

  // Lightbox:click 主图(spu-img[data-zoom])→ 全屏叠层;点背景(非放大图
  // 本身)/×/Esc 关闭。单例叠层,与 manual-costs console.js 同款交互。
  // 事件委托:行图在 render() 里注入,不逐行绑定;隐藏行/翻页自动生效。
  var _roiLightbox = null;
  function openLightbox(url) {
    if (!url) return;
    if (!_roiLightbox) {
      _roiLightbox = document.createElement("div");
      _roiLightbox.className = "op-lightbox";
      // DOM API 构建,不用 innerHTML(保持无 innerHTML 审计面)
      var close = document.createElement("button");
      close.type = "button";
      close.className = "op-lightbox-close";
      close.setAttribute("aria-label", "关闭");
      close.textContent = "×";
      var lightImg = document.createElement("img");
      lightImg.alt = "";
      _roiLightbox.appendChild(close);
      _roiLightbox.appendChild(lightImg);
      _roiLightbox.addEventListener("click", (ev) => {
        if (ev.target === _roiLightbox) closeLightbox();
      });
      lightImg.addEventListener("click", (ev) => {
        ev.stopPropagation();
      });
      close.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeLightbox();
      });
      document.body.appendChild(_roiLightbox);
    }
    _roiLightbox.querySelector("img").src = url;
    _roiLightbox.classList.add("is-open");
    document.addEventListener("keydown", lightboxEsc);
  }
  function closeLightbox() {
    if (!_roiLightbox) return;
    _roiLightbox.classList.remove("is-open");
    _roiLightbox.querySelector("img").src = "";
    document.removeEventListener("keydown", lightboxEsc);
  }
  function lightboxEsc(ev) {
    if (ev.key === "Escape") closeLightbox();
  }
  function wireZoom() {
    document.addEventListener("click", (e) => {
      var t = e.target && e.target.closest ? e.target.closest("[data-zoom]") : null;
      if (t) {
        e.preventDefault();
        openLightbox(t.getAttribute("data-zoom"));
      }
    });
  }

  // ---------- 交互绑定 ----------
  function bindControls() {
    var q = $("#filter-q");
    q.addEventListener(
      "input",
      debounce(() => {
        state.q = q.value.trim();
        state.offset = 0;
        load();
      }, 300),
    );

    $("#filter-limit").addEventListener("change", (e) => {
      state.limit = parseInt(e.target.value, 10) || 100;
      state.offset = 0;
      load();
    });

    var feeInput = $("#filter-fee");
    feeInput.addEventListener(
      "input",
      debounce(() => {
        var v = feeInput.value.trim();
        if (v === "") {
          state.feeRate = null;
        } else {
          var pct = parseFloat(v);
          // 输入是百分比(11.56 = 11.56%),API 层收小数
          state.feeRate = Number.isFinite(pct) ? String(pct / 100) : v;
        }
        load();
      }, 400),
    );

    $("#filter-include-all").addEventListener("change", (e) => {
      state.includeAll = e.target.checked;
      state.offset = 0;
      load();
    });

    // 店铺筛选:空值 = 全部店铺(不传 shop_pk = 全历史语义)
    $("#filter-shop").addEventListener("change", (e) => {
      var v = e.target.value;
      state.shopPk = v === "" ? null : v;
      state.offset = 0;
      load();
    });

    // 日期范围:空 = 不限;yyyy-mm-dd 直接作 w_start/w_end(含 w_end 当日)
    $("#filter-w-start").addEventListener("change", (e) => {
      state.wStart = e.target.value || "";
      if (e.target.value) state.datesTouched = true; // 用户已接管,不再自动回填
      state.offset = 0;
      load();
    });
    $("#filter-w-end").addEventListener("change", (e) => {
      state.wEnd = e.target.value || "";
      if (e.target.value) state.datesTouched = true; // 用户已接管,不再自动回填
      state.offset = 0;
      load();
    });

    $("#btn-refresh").addEventListener("click", () => load());
    $("#btn-prev").addEventListener("click", () => {
      if (state.offset > 0) {
        state.offset = Math.max(0, state.offset - state.limit);
        load();
      }
    });
    $("#btn-next").addEventListener("click", () => {
      if (state.offset + state.limit < lastTotal) {
        state.offset += state.limit;
        load();
      }
    });

    // 列头排序:同列 asc ↔ desc 双向切换;新列首方向 asc
    Array.prototype.forEach.call(
      document.querySelectorAll(".op-th-sort[data-sort]"),
      (th) => {
        th.addEventListener("click", () => {
          var field = th.getAttribute("data-sort");
          if (!SORTABLE.has(field)) return;
          if (field === state.sort) {
            state.order = state.order === "asc" ? "desc" : "asc";
          } else {
            state.sort = field;
            state.order = "asc";
          }
          state.offset = 0;
          load();
        });
      },
    );

    wireTooltips(); // 悬停说明气泡(data-tip 委托,含重渲染后的新行)
    wireZoom(); // 主图点击放大(委托)
    loadMe();
    loadShops(); // 店铺选项异步填充;失败不影响主表
    load();
  }

  document.addEventListener("DOMContentLoaded", bindControls);
})();

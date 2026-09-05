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
    net_profit: "净利润",
    sales: "销售",
    gmv_ad: "平台GMV",
    ad_count: "广告数",
    roi_l0: "ROI₀",
    order_count: "有效单",
    units_sold: "件数",
    refund_net_amount: "退款净额",
    return_loss: "货损",
    roi_breakeven: "保本",
  };
  var SORTABLE = new Set([
    "roi_real",
    "spend",
    "refund_rate",
    "net_profit",
    "sales",
    "gmv_ad",
    "ad_count",
    "order_count",
    "units_sold",
    "return_loss",
    "roi_breakeven",
    "refund_net_amount",
  ]);

  // §7.2 标色默认阈值(常量,页面 ⚙ 可调预留,不锁死)
  var ROI_HARD_LOSS = 1.0; // 广告回本线:实际 ROI < 1.0 = 连广告费都带不回
  var PASS_LINE = 1.5; // 心理及格线:≥保本但 < 1.5 → 浅橙,不标红
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
    feeRate: null, // 页面覆写费率(小数),null = 用服务端基线
    cols: {}, // ⚙ 列开关: {cg-refundsplit|cg-cancel|cg-fee: true=显示}(默认隐藏)
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
    var roiReal = parseFloat(it.roi_real);
    var hasRoi =
      it.roi_real !== null &&
      it.roi_real !== undefined &&
      it.roi_real !== "" &&
      Number.isFinite(roiReal);
    var be = parseFloat(it.roi_breakeven);
    var hasBe =
      it.roi_breakeven !== null &&
      it.roi_breakeven !== undefined &&
      it.roi_breakeven !== "" &&
      Number.isFinite(be);
    var losing = hasRoi && hasBe && roiReal < be;
    var hardLoss = hasRoi && roiReal < ROI_HARD_LOSS; // §7.2 广告回本线
    var np = parseFloat(it.net_profit);
    var npNeg = Number.isFinite(np) && np < 0;
    var isBad = losing || npNeg || hardLoss;
    var warnDefault = it.cost_source === "DEFAULT_K1";
    var rr = parseFloat(it.refund_rate);
    var rrHigh = Number.isFinite(rr) && rr > REFUND_RATE_ALERT; // §7.2
    var img = it.main_image_url
      ? `<img class="spu-img" alt="" src="${esc(it.main_image_url)}">`
      : "";
    var warn =
      '<span class="warn-default" title="无人工成本记录，按默认 30元/件计算，可去 manual-costs 补录">⚠</span> ';
    var warnRr = rrHigh
      ? '<span class="warn-rr" title="退款率超过 30% 警戒线">⚠</span> '
      : "";
    var status =
      it.status === "ACTIVATE" || !it.status
        ? ""
        : `<span class="spu-status is-down">${esc(it.status)}</span>`;
    var costTitle =
      it.cost_source === "MANUAL"
        ? `人工成本(${esc(it.unit_cost_used)} USD/件)`
        : "默认 30元/件 ≈ $4.43 ⚠";
    var roiCell;
    if (!hasRoi) {
      roiCell = "—"; // 除数为 0 → null → —
    } else if (hardLoss) {
      roiCell = `<span class="roi-hard" title="实际 ROI < ${ROI_HARD_LOSS.toFixed(1)}：连广告费都带不回">${fmtRatio(it.roi_real)}</span>`;
    } else if (losing) {
      roiCell = `<span class="roi-red" title="该 SPU 亏损">实际 ${fmtRatio(it.roi_real)} &lt; 保本 ${fmtRatio(it.roi_breakeven)}</span>`;
    } else if (roiReal < PASS_LINE) {
      roiCell = `<span class="roi-subpar" title="≥ 保本但低于心理及格线 ${PASS_LINE.toFixed(1)}">${fmtRatio(it.roi_real)}</span>`;
    } else {
      roiCell = fmtRatio(it.roi_real);
    }
    var profitClass = npNeg ? ' class="np-red"' : "";
    var adCell =
      it.ad_count === 0 || it.ad_count == null
        ? '<span class="no-ad" title="该 SPU 无广告投放">无投放</span>'
        : fmtInt(it.ad_count);
    var rrCell = rrHigh
      ? `<td class="rr-high" title="退款率超过 30% 警戒线">${fmtPct(it.refund_rate)}</td>`
      : `<td>${fmtPct(it.refund_rate)}</td>`;
    return (
      `<tr class="${isBad ? "row-bad" : ""}">` +
      `<td class="td-left">${img}<div class="td-spu">${esc(it.spu_id)}</div>` +
      `<div class="td-title" title="${esc(it.title || "")}">${warnDefault ? warn : ""}${warnRr}${esc(it.title || "")}${status}</div></td>` +
      `<td>${adCell}</td>` +
      `<td>${fmtMoney(it.spend)}</td>` +
      `<td>${fmtMoney(it.gmv_ad)}</td>` +
      `<td>${fmtRatio(it.roi_l0)}</td>` +
      `<td>${fmtInt(it.order_count)}</td>` +
      `<td>${fmtInt(it.units_sold)}</td>` +
      `<td>${fmtMoney(it.sales)}</td>` +
      `<td class="col-hidden" data-cg="cg-refundsplit">${fmtInt(it.refund_only_qty)}</td>` +
      `<td class="col-hidden" data-cg="cg-refundsplit">${fmtMoney(it.refund_only_amount)}</td>` +
      `<td class="col-hidden" data-cg="cg-refundsplit">${fmtInt(it.refund_return_qty)}</td>` +
      `<td class="col-hidden" data-cg="cg-refundsplit">${fmtMoney(it.refund_return_amount)}</td>` +
      `<td>${fmtMoney(it.refund_net_amount)}</td>` +
      rrCell +
      `<td class="col-hidden" data-cg="cg-cancel" title="已付被取消订单退款(信息列,不计净额)">${fmtInt(it.refund_cancelled_qty)}</td>` +
      `<td class="col-hidden" data-cg="cg-cancel">${fmtMoney(it.refund_cancelled_amount)}</td>` +
      `<td class="col-hidden" data-cg="cg-cancel" title="${it.refund_cancelled_missing_lines ? "另有行金额未知(不造数)" : ""}">${fmtInt(it.refund_cancelled_missing_lines)}</td>` +
      `<td${profitClass}>${fmtMoney(it.net_profit)}</td>` +
      `<td title="${costTitle}">${fmtMoney(it.return_loss)}</td>` +
      `<td class="col-hidden" data-cg="cg-fee" title="平台佣金=平台从销售额直接扣除的全部费用(抽佣/联盟/运费类)">${fmtMoney(it.platform_fee)}</td>` +
      `<td>${fmtRatio(it.roi_breakeven)}</td>` +
      `<td>${roiCell}</td>` +
      "</tr>"
    );
  }

  function renderError(msg) {
    html(
      $("#rows"),
      `<tr><td colspan="22" class="op-error">${esc(msg)} · <a href="#" id="retry-link">重试</a></td></tr>`,
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
      '<tr><td colspan="22" class="op-empty">没有匹配该 spu_id 的 SPU（试试完整 ID）</td></tr>',
    );
  }

  function render(payload) {
    var items = unwrap(payload);
    var totals = payload.totals || {};
    var meta = payload.meta || {};
    lastTotal = payload.total || 0;

    // 结余带
    $("#sum-n").textContent = String(totals.row_count || 0);
    $("#sum-spend").textContent = fmtMoney(totals.spend);
    $("#sum-sales").textContent = fmtMoney(totals.sales);
    $("#sum-refund").textContent = fmtMoney(totals.refund_net_amount);
    $("#sum-loss").textContent = fmtMoney(totals.return_loss); // §7.1 全损货损
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

    applyColToggles(); // 新渲染的行/空态要重新应用 ⚙ 列开关
    updateSortMarkers();
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

  // ---------- ⚙ 列开关(§7.5 默认折叠) ----------
  function applyColToggles() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".col-toggle[data-colgroup]"),
      (cb) => {
        var group = cb.getAttribute("data-colgroup");
        var on = !!state.cols[group];
        Array.prototype.forEach.call(
          document.querySelectorAll(`[data-cg="${group}"]`),
          (el) => el.classList.toggle("col-hidden", !on),
        );
      },
    );
  }

  function bindColToggles() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".col-toggle[data-colgroup]"),
      (cb) => {
        cb.addEventListener("change", () => {
          state.cols[cb.getAttribute("data-colgroup")] = cb.checked;
          applyColToggles();
        });
      },
    );
  }

  // ---------- load ----------
  function load() {
    if (state.loading) return;
    state.loading = true;
    html(
      $("#rows"),
      '<tr><td colspan="22" class="op-loading">加载中…</td></tr>',
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

    applyColToggles();
    bindColToggles();
    loadMe();
    load();
  }

  document.addEventListener("DOMContentLoaded", bindControls);
})();

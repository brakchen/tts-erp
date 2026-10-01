/* tts-erp — SPU 实际 ROI 看板页 JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/spu-roi.
   数据全部来自只读端点 GET /v2/analytics/spu-roi(服务端已算好,§5.1-1),
   本文件只做格式化与展示:金额纯数字 · 2 位千分位、比率/百分比、红绿判据、分页、
   排序(asc ↔ desc 双向),401 → login。样式复用页面 warm-paper token,零外链。 */

(() => {
  var profile = null;
  var selectionAdapter = null;
  var selectionCleanup = null;
  var mounted = false;

  // ---------- constants ----------
  var ENDPOINT_PATH = "/v2/analytics/spu-roi";
  var SPU_OPTIONS_PATH = "/v2/commerce/channel-product-options";
  var MAX_SELECTED_SPUS = 100;
  var DEFAULT_SORT = "roi_real";
  var DEFAULT_ORDER = "asc";
  var DATE_VALUE_RE = /^\d{4}-\d{2}-\d{2}$/;
  var PAGE_LIMITS = new Set([50, 100, 200]);
  // commerce.shops.region 是 ISO 3166-1 国家码；仅映射确定的单时区市场。
  // 多时区或未知地区必须暂停，不能猜测默认时区。
  var REGION_TIME_ZONES = Object.freeze({
    VN: "Asia/Ho_Chi_Minh",
    TH: "Asia/Bangkok",
    SG: "Asia/Singapore",
    MY: "Asia/Kuala_Lumpur",
    PH: "Asia/Manila",
    CN: "Asia/Shanghai",
    JP: "Asia/Tokyo",
    KR: "Asia/Seoul",
    GB: "Europe/London",
  });
  var SORT_LABEL = {
    roi_real: "实际ROI",
    spend: "消耗",
    refund_rate: "退款率",
    refund_rate_qty: "退货率",
    cancel_rate: "取消率",
    net_profit: "净利润",
    sales: "有效销售 GMV",
    effective_sales: "有效销售",
    gmv_sales: "销售",
    gmv_ad: "平台GMV",
    roi_l0: "ROI₀",
    order_count: "有效销售订单",
    effective_order_count: "有效单量",
    cancelled_order_count: "取消单量",
    units_sold: "件数",
    refund_net_amount: "退货",
    return_loss: "全损退款",
    roi_breakeven: "保本ROI",
  };
  // Public path prefix: "/tts" behind NGINX, "" on :9877 directly.
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  // ---------- small helpers ----------
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  // ---------- 表格列定义（唯一事实源） ----------
  // Tabulator 6.3.1 (MIT) — vendored at static/vendor/tabulator.min.* 。
  // columnId 供视图配置显隐；sortField 非空即可点表头触发服务端排序。
  // 新增指标列只需在此加一行；测试会校验 sortField ∈ SortField 且是盈利行字段。
  var COLUMN_DEFS = [
    { columnId: "product", field: "spu_id", title: "商品", sortField: null, tip: "" },
    { columnId: "spend", field: "spend", title: "广告消耗", sortField: "spend", tip: "广告消耗（源数据 USD，服务端按汇率快照换算为 CNY；随选中日期窗口裁剪；作为减项计入净利润）" },
    { columnId: "ad-actual-roi", field: "ad_system_actual_roi", title: "广告系统实际ROI", sortField: "ad_system_actual_roi", tip: "广告系统实际ROI = 广告归因GMV ÷ 广告实际消耗；无广告消耗时显示 —" },
    { columnId: "ad-breakeven-roi", field: "ad_system_breakeven_roi", title: "广告系统保本ROI", sortField: "ad_system_breakeven_roi", tip: "广告系统保本ROI = 广告归因GMV ÷ 最大可承受广告费；当前以 ≈ 标记已知成本下限估算，分母≤0或无归因GMV时显示 —" },
    { columnId: "effective-sales", field: "effective_sales", title: "有效销售", sortField: "effective_sales", tip: "有效销售 = 有效销售订单 GMV − 退款金额（CNY）；与大盘 totals.effective_sales 同口径" },
    { columnId: "total-orders", field: "total_orders", title: "总单量", sortField: "total_orders", tip: "总单量 = 有效销售订单 + 国内取消订单 + 海外取消订单；在当前店铺和日期范围内按订单去重" },
    { columnId: "effective-orders", field: "effective_order_count", title: "有效单量", sortField: "effective_order_count", tip: "有效单量 = 有效销售订单数 − 退款订单数；与大盘 totals.effective_order_count 同口径" },
    { columnId: "cancel-rate", field: "cancel_rate", title: "取消率%", sortField: "cancel_rate", tip: "取消率 = 国内取消订单数 ÷ 全部订单；全部订单 = 有效销售订单 + 国内取消 + 海外取消，海外取消只进入全损分子" },
    { columnId: "full-loss-rate", field: "full_loss_rate", title: "全损率%", sortField: "full_loss_rate", tip: "全损率 = (退款订单数 + 海外取消订单数) ÷ 全部订单；订单维度按当前 SPU 去重，与大盘同口径" },
    { columnId: "net-profit", field: "net_profit", title: "净利润", sortField: "net_profit", tip: "净利润 v7(M18):已结算 SETTLEMENT + 未结算 ×(1−r̂)×(1−退款率) − 货本含全损取消 − 广告;r̂=店铺实测(近180天已结算单 Σ|FEE|/Σ行GMV,每24h重算,有一单已结算即产出)或基线30.8%;负值红字。Red/green 仅按净利判(C3 拍板,删 ROI<1 硬亏档)" },
  ];

  function supportsSortField(field) {
    if (!field) return false;
    if (field === DEFAULT_SORT) return true;
    return COLUMN_DEFS.some((def) => def.sortField === field);
  }

  function sortLabel(field) {
    var matched = COLUMN_DEFS.find((def) => def.sortField === field);
    return matched ? matched.title : SORT_LABEL[field] || field;
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

  function el(tag, props, ...children) {
    var node = document.createElement(tag);
    if (props)
      for (var k in props) {
        if (k === "class") node.className = props[k];
        else if (k === "text") node.textContent = props[k];
        else if (k === "hidden" && !props[k]) continue;
        else node.setAttribute(k, props[k]);
      }
    var flat =
      children.length === 1 && Array.isArray(children[0])
        ? children[0]
        : children;
    for (var i = 0; i < flat.length; i++) {
      var c = flat[i];
      if (c == null) continue;
      // el() 子元素兜底: string → TextNode; Node → 直接 append; 其他(number/boolean) → 走 String → TextNode
      // 修复 orders/settlements/cases/ads tab 的 appendChild 报错(o.qty/s.order_id/a.orders 等是 number)
      if (typeof c === "string") {
        node.appendChild(document.createTextNode(c));
      } else if (c instanceof Node) {
        node.appendChild(c);
      } else {
        node.appendChild(document.createTextNode(String(c)));
      }
    }
    return node;
  }
  // Single choke point for markup writes.
  function html(el, markup) {
    // pi-lens-ignore: no-inner-html-js
    el.innerHTML = markup;
  }
  // Unwrap API envelopes: { items: [...], total: N, totals, meta }.
  function unwrap(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    return [];
  }
  // Cached Intl formatters — Intl.NumberFormat instances are reusable and
  // safe to share; per-call toLocaleString would rebuild one per cell.
  var FMT_MONEY = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  var FMT_RATIO = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  var FMT_PCT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
  var FMT_QTY = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

  function fmtMoney(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v);
    if (!Number.isFinite(n)) return "—";
    return FMT_MONEY.format(n);
  }
  function fmtRatio(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v);
    if (!Number.isFinite(n)) return "—";
    return FMT_RATIO.format(n);
  }
  function fmtPct(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v) * 100;
    if (!Number.isFinite(n)) return "—";
    return FMT_PCT.format(n) + "%";
  }
  function fmtInt(v) {
    if (v == null || v === "") return "—";
    var n = parseInt(v, 10);
    if (!Number.isFinite(n)) return "—";
    return String(n);
  }
  function fmtQty(v) {
    if (v == null || v === "") return "—";
    var n = parseFloat(v);
    if (!Number.isFinite(n)) return "—";
    return FMT_QTY.format(n);
  }
  function projectionStatusLabel(status) {
    if (status === "available") return "可预测";
    if (status === "no_unsettled_orders") return "无未结算订单";
    if (status === "insufficient_sample") return "样本不足";
    return "—";
  }
  function setTextIfPresent(selector, value) {
    var target = $(selector);
    if (target) target.textContent = value;
  }

  function ensureDeliveredUnsettledSummary() {
    if ($("#sum-delivered-unsettled-orders")) return;
    var unresolved = $("#sum-unresolved-orders");
    var unresolvedCell = unresolved && unresolved.closest(".col");
    if (!unresolvedCell || !unresolvedCell.parentNode) return;
    var deliveredCell = el(
      "div",
      { class: unresolvedCell.className },
      el(
        "span",
        { class: "op-counter-item h-100 p-2 p-lg-3" },
        el(
          "span",
          { class: "op-counter-label" },
          "未结算已送达订单",
          el(
            "span",
            {
              class: "op-hint",
              "data-tip":
                "尚未 SETTLEMENT，但订单状态或物流已确认送达的订单数；不进入未来全损预测",
            },
            "?",
          ),
        ),
        el("span", {
          class: "op-counter-num",
          id: "sum-delivered-unsettled-orders",
          text: "—",
        }),
      ),
    );
    unresolvedCell.parentNode.insertBefore(
      deliveredCell,
      unresolvedCell.nextSibling,
    );
  }

  function loginUrl() {
    var pagePath = profile && profile.pagePath ? profile.pagePath : "/v2/pages/spu-roi";
    return `${PREFIX}/v2/auth/login?next=${PREFIX}${pagePath}`;
  }

  function preferenceStorageKey() {
    var preferences = profile && profile.preferences;
    return preferences && preferences.storageKey ? preferences.storageKey : "";
  }

  function readPagePreferences() {
    var key = preferenceStorageKey();
    if (!key) return null;
    try {
      var saved = JSON.parse(window.localStorage.getItem(key) || "null");
      return saved && typeof saved === "object" ? saved : null;
    } catch {
      return null;
    }
  }

  function restorePagePreferences() {
    var saved = readPagePreferences();
    if (!saved) return;
    if (typeof saved.shopPk === "string" && /^\d+$/.test(saved.shopPk)) {
      state.shopPk = saved.shopPk;
    }
    if (saved.datesTouched === true) {
      var savedStart = typeof saved.wStart === "string" ? saved.wStart : "";
      var savedEnd = typeof saved.wEnd === "string" ? saved.wEnd : "";
      var datesValid =
        (!savedStart || DATE_VALUE_RE.test(savedStart)) &&
        (!savedEnd || DATE_VALUE_RE.test(savedEnd)) &&
        (!savedStart || !savedEnd || savedStart <= savedEnd);
      if (datesValid) {
        state.datesTouched = true;
        state.wStart = savedStart;
        state.wEnd = savedEnd;
      }
    }
    if (typeof saved.includeAll === "boolean") state.includeAll = saved.includeAll;
    if (PAGE_LIMITS.has(saved.limit)) state.limit = saved.limit;
    if (supportsSortField(saved.sort)) state.sort = saved.sort;
    if (saved.order === "asc" || saved.order === "desc") state.order = saved.order;
  }

  function persistPagePreferences() {
    var key = preferenceStorageKey();
    if (!key) return;
    try {
      window.localStorage.setItem(
        key,
        JSON.stringify({
          shopPk: state.shopPk || null,
          wStart: state.wStart,
          wEnd: state.wEnd,
          datesTouched: state.datesTouched,
          includeAll: state.includeAll,
          limit: state.limit,
          sort: state.sort,
          order: state.order,
        }),
      );
    } catch {
      // 隐私模式、存储配额或浏览器策略禁用 localStorage 时保持页面可用。
    }
  }

  // ---------- api ----------
  function api(params, signal) {
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
      signal: signal,
    }).then((r) => {
      // D6: 主表筛选变化 → 清钻取缓存
      clearDrillCache();
      if (r.status === 401) {
        // 401 → 跳登录(console.js 家族行为)
        // pi-lens-ignore: no-open-redirect-js
        window.location.href = loginUrl();
        throw new Error("unauthorized");
      }
      return r.json().then((payload) => {
        if (!r.ok) {
          var err = new Error(
            payload.detail || payload.message || `HTTP ${r.status}`,
          );
          err.code = payload.code || null;
          err.status = r.status;
          throw err;
        }
        return payload;
      });
    });
  }

  // ---------- state ----------
  var state = {
    q: "",
    spuIds: [], // 已应用到大盘的精确 SPU scope；Tom Select 内部值是待应用草稿
    spuSelect: null,
    spuSelectionVersion: 0, // 清空/切店后使旧的批量粘贴请求失效
    pendingSpuIds: new Set(), // 正在校验、已预留配额的 SPU id
    spuResolveControllers: new Set(),
    limit: 100,
    includeAll: false,
    shopPk: null, // 店铺筛选(null/""=全部店铺)
    shops: [],
    shopsByPk: new Map(),
    shopRegion: null,
    reportingTimeZone: null,
    wStart: "", // 日期范围 yyyy-mm-dd(""=不限)
    wEnd: "",
    datesTouched: false, // 仅用户主动选过日期时为 true；自动 T-1 不算用户选择
    feeRate: null, // 页面覆写费率(小数),null = 店铺实测/服务端基线
    // D7 行内 accordion: 一次只展开一行; D6 tab 懒加载缓存,主表筛选变化时清空
    openDrillRow: null,
    drillCache: new Map(),
    table: null, // Tabulator 实例（buildTable 建立）
    sort: DEFAULT_SORT,
    order: DEFAULT_ORDER,
    offset: 0,
    loading: false,
    loadVersion: 0, // 仅最新主表请求可写入页面
    loadController: null,
    selectionQueryable: true,
    meta: {}, // 后端拥有业务状态、阈值与公式说明；前端只渲染
    enumMap: {}, // 枚举中文化映射,page load 时从 /v2/config/enum-map 获取
  };
  var lastTotal = 0;

  var ALLOWED_SUMMARIES = new Set([
    "sum-total-orders",
    "sum-spend",
    "sum-orders",
    "sum-sales",
    "sum-refund-count",
    "sum-refund-rate",
    "sum-loss-qty",
    "sum-loss-rate",
    "sum-cancel-count",
    "sum-cancel-rate",
    "sum-net-profit",
    "sum-roi",
    "sum-roi-breakeven",
    "sum-roi-ad-actual",
    "sum-roi-ad",
    "sum-projection-status",
    "sum-projection-completed-basis-orders",
    "sum-projection-completed-loss-orders",
    "sum-projection-full-loss-rate",
    "sum-unresolved-orders",
    "sum-delivered-unsettled-orders",
    "sum-full-loss-exposure-unsettled-orders",
    "sum-projected-future-loss-qty",
    "sum-projected-net-revenue",
    "sum-projected-net-profit",
    "sum-projected-roi",
    "sum-projected-breakeven-roi",
    "sum-projected-ad-roi",
    "sum-projected-ad-breakeven-roi",
  ]);
  var ALLOWED_COLUMNS = new Set([
    "product",
    "spend",
    "ad-actual-roi",
    "ad-breakeven-roi",
    "effective-sales",
    "total-orders",
    "effective-orders",
    "cancel-rate",
    "full-loss-rate",
    "net-profit",
  ]);
  var ALLOWED_DRILL_TABS = new Set([
    "pnl",
    "orders",
    "settlements",
    "cases",
    "ads",
  ]);

  function selectedViewIds(name, allowed) {
    var values = profile && profile.view && profile.view[name];
    if (!Array.isArray(values)) return new Set(allowed);
    values.forEach((value) => {
      if (!allowed.has(value)) throw new Error(`unknown ${name} id: ${value}`);
    });
    return new Set(values);
  }

  function applyViewProfile() {
    var summaries = selectedViewIds("summaryIds", ALLOWED_SUMMARIES);
    ALLOWED_SUMMARIES.forEach((id) => {
      var target = document.getElementById(id);
      var cell = target && target.closest(".col");
      if (cell) cell.hidden = !summaries.has(id);
    });
    var columns = selectedViewIds("columnIds", ALLOWED_COLUMNS);
    if (state.table) {
      COLUMN_DEFS.forEach((def) => {
        var col = state.table.getColumn(def.field);
        if (!col) return;
        if (columns.has(def.columnId)) col.show();
        else col.hide();
      });
    }
    var tabs = selectedViewIds("drillTabIds", ALLOWED_DRILL_TABS);
    document.querySelectorAll(".op-drill-tab[data-tab]").forEach((tab) => {
      tab.hidden = !tabs.has(tab.getAttribute("data-tab"));
    });
  }

  // ---------- Bootstrap 5 SPU 多选(Tom Select 官方 Bootstrap 主题) ----------
  function selectedSpuDraft() {
    if (!state.spuSelect) return [];
    var value = state.spuSelect.getValue();
    return Array.isArray(value) ? value : value ? [value] : [];
  }

  function setSpuFeedback(message) {
    var feedback = $("#spu-filter-feedback");
    if (!feedback) return;
    feedback.textContent = message || "";
    feedback.classList.toggle("d-block", Boolean(message));
  }

  function updateSpuSelectionUi() {
    var selected = selectedSpuDraft();
    var dirty = selected.join("\u0000") !== state.spuIds.join("\u0000");
    var hasAppliedSpuScope = state.spuIds.length > 0;
    var pendingCount = state.pendingSpuIds.size;
    var canEditSpuFilter = Boolean(state.shopPk);
    var canApplySpuFilter = canEditSpuFilter && !state.loading && !pendingCount;
    var count = $("#spu-selection-count");
    if (count) {
      count.textContent =
        `已选择 ${selected.length} 个${dirty ? "（待查询）" : ""}` +
        (pendingCount ? ` · 正在校验 ${pendingCount} 个` : "");
    }
    var clear = $("#btn-spu-clear");
    var apply = $("#btn-spu-apply");
    // 清空要同时考虑草稿、已应用 scope 与正在校验的粘贴；后者可由清空取消。
    if (clear)
      clear.disabled =
        !canEditSpuFilter ||
        (!selected.length && !hasAppliedSpuScope && !pendingCount);
    if (apply) apply.disabled = !canApplySpuFilter || !dirty;
  }

  function parsePastedSpuIds(raw) {
    var ids = [];
    var seen = new Set();
    String(raw || "")
      .replace(/，/g, ",")
      .split(",")
      .forEach((part) => {
        var value = part.trim();
        if (value && !seen.has(value)) {
          seen.add(value);
          ids.push(value);
        }
      });
    return ids;
  }

  function fetchSpuOptions(params, signal) {
    if (!state.shopPk) return Promise.resolve([]);
    var qs = new URLSearchParams({
      shop_pk: String(state.shopPk),
      limit: String(params.limit || 50),
    });
    if (params.q) qs.set("q", params.q);
    if (params.spuIds && params.spuIds.length) {
      qs.set("spu_ids", params.spuIds.join(","));
    }
    return fetch(`${PREFIX}${SPU_OPTIONS_PATH}?${qs.toString()}`, {
      credentials: "include",
      headers: { Accept: "application/json" },
      signal: signal,
    }).then((response) => {
      if (response.status === 401) {
        // pi-lens-ignore: no-open-redirect-js
        window.location.href = loginUrl();
        throw new Error("unauthorized");
      }
      if (!response.ok) throw new Error(`SPU options HTTP ${response.status}`);
      return response.json();
    });
  }

  function cancelPendingSpuResolutions() {
    state.spuSelectionVersion += 1;
    state.spuResolveControllers.forEach((controller) => controller.abort());
    state.spuResolveControllers.clear();
    state.pendingSpuIds.clear();
  }

  function resolvePastedSpuIds(raw) {
    var pasted = parsePastedSpuIds(raw);
    if (!pasted.length) {
      setSpuFeedback("请粘贴至少一个有效 SPU");
      return;
    }
    var selected = new Set(selectedSpuDraft());
    var requested = pasted.filter(
      (value) => !selected.has(value) && !state.pendingSpuIds.has(value),
    );
    if (!requested.length) {
      setSpuFeedback("粘贴的 SPU 已在选择或校验中");
      return;
    }
    if (selected.size + state.pendingSpuIds.size + requested.length > MAX_SELECTED_SPUS) {
      setSpuFeedback(`最多选择 ${MAX_SELECTED_SPUS} 个 SPU`);
      return;
    }

    var requestedShop = state.shopPk;
    var selectionVersion = state.spuSelectionVersion;
    var controller = new AbortController();
    requested.forEach((value) => state.pendingSpuIds.add(value));
    state.spuResolveControllers.add(controller);
    setSpuFeedback("");
    updateSpuSelectionUi();

    fetchSpuOptions(
      { spuIds: requested, limit: MAX_SELECTED_SPUS },
      controller.signal,
    )
      .then((options) => {
        if (
          !state.spuSelect ||
          state.shopPk !== requestedShop ||
          state.spuSelectionVersion !== selectionVersion
        )
          return;
        var matched = new Set();
        var available = MAX_SELECTED_SPUS - selectedSpuDraft().length;
        var accepted = options.slice(0, Math.max(0, available));
        accepted.forEach((option) => {
          matched.add(option.spu_id);
          state.spuSelect.addOption(option);
          state.spuSelect.addItem(option.spu_id, true);
        });
        state.spuSelect.refreshItems();
        var missing = requested.filter((value) => !matched.has(value));
        if (missing.length) {
          setSpuFeedback(
            `未找到或未加入 ${missing.length} 个 SPU：${missing.join("、")}`,
          );
        }
      })
      .catch((error) => {
        if (
          !error ||
          error.name === "AbortError" ||
          error.message === "unauthorized" ||
          state.spuSelectionVersion !== selectionVersion
        )
          return;
        setSpuFeedback("SPU 列表解析失败，请重试");
      })
      .finally(() => {
        state.spuResolveControllers.delete(controller);
        if (state.spuSelectionVersion === selectionVersion) {
          requested.forEach((value) => state.pendingSpuIds.delete(value));
          updateSpuSelectionUi();
        }
      });
  }

  function resetSpuSelectForShop() {
    cancelPendingSpuResolutions();
    state.spuIds = [];
    if (!state.spuSelect) return;
    state.spuSelect.clear(true);
    state.spuSelect.clearOptions();
    setSpuFeedback("");
    if (state.shopPk) {
      state.spuSelect.enable();
      state.spuSelect.load("");
    } else {
      state.spuSelect.disable();
    }
    updateSpuSelectionUi();
  }

  function initSpuSelect() {
    var select = $("#filter-spu-ids");
    var TomSelectClass = window["TomSelect"];
    if (!select || typeof TomSelectClass !== "function") {
      setSpuFeedback("SPU 多选组件加载失败，请刷新页面");
      return;
    }
    state.spuSelect = new TomSelectClass(select, {
      plugins: { remove_button: { title: "移除 SPU" } },
      valueField: "spu_id",
      labelField: "spu_id",
      searchField: ["spu_id", "title"],
      maxItems: MAX_SELECTED_SPUS,
      create: false,
      closeAfterSelect: false,
      hideSelected: true,
      preload: "focus",
      loadThrottle: 250,
      placeholder: "请选择 SPU（支持搜索或批量粘贴）",
      shouldLoad: () => Boolean(state.shopPk),
      load: (query, callback) => {
        var requestedShop = state.shopPk;
        fetchSpuOptions({ q: query, limit: 50 })
          .then((options) => {
            callback(state.shopPk === requestedShop ? options : []);
          })
          .catch(() => callback());
      },
      render: {
        option: (data, escapeHtml) =>
          `<div><div class="d-flex justify-content-between gap-2"><span class="op-spu-option-id">${escapeHtml(data.spu_id)}</span><span class="badge text-bg-light">${escapeHtml(data.status || "未知")}</span></div><div class="op-spu-option-title">${escapeHtml(data.title || "无标题")}</div></div>`,
        item: (data, escapeHtml) =>
          `<div title="${escapeHtml(data.title || data.spu_id)}">${escapeHtml(data.spu_id)}</div>`,
        no_results: () => '<div class="no-results">没有匹配的 SPU</div>',
      },
      onChange: updateSpuSelectionUi,
      onItemRemove: updateSpuSelectionUi,
      onClear: updateSpuSelectionUi,
    });
    state.spuSelect.disable();
    state.spuSelect.control_input.addEventListener("paste", (event) => {
      var text = event.clipboardData ? event.clipboardData.getData("text") : "";
      if (!text || !/[,，]/.test(text)) return;
      event.preventDefault();
      resolvePastedSpuIds(text);
    });
    updateSpuSelectionUi();
  }

  // ---------- 枚举翻译 (GET /v2/config/enum-map) ----------
  function loadEnumMap() {
    return fetch(`${PREFIX}/v2/config/enum-map`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then((r) => (r.ok ? r.json() : {}))
      .then((data) => {
        state.enumMap = data || {};
      })
      .catch(() => {});
  }
  // 用 enumMap 翻译枚举值;type = enum_type, val = 原始英文值
  function tr(type, val) {
    if (val == null || val === "") return "—";
    var map = state.enumMap[type];
    return map && map[val] ? map[val] : val; // 无映射 → 原值兜底
  }
  // 用 column_header 翻译表头
  function th(label) {
    var ch = state.enumMap.column_header;
    return ch && ch[label] ? ch[label] : label;
  }

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
                  // pi-lens-ignore: no-open-redirect-js
                  window.location.href = loginUrl();
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
  // ---------- Tabulator 表格 ----------
  function fmtPctOrDash(v) {
    return v === null || v === undefined || v === "" ? "—" : fmtPct(v);
  }

  function productCellFormatter(cell) {
    var it = cell.getData();
    var presentation = state.meta.presentation || {};
    var warnDefault = it.uses_default_unit_cost === true;
    var rrHigh = it.refund_rate_alert === true;
    var hasUnsettled = it.has_unsettled_orders === true;
    var img = it.main_image_url
      ? `<img class="spu-img" alt="" src="${esc(it.main_image_url)}" data-zoom="${esc(it.main_image_url)}">`
      : '<span class="spu-img-missing" aria-hidden="true">无主图</span>';
    var warnCost =
      '<span class="warn-default" data-tip="' +
      esc(presentation.default_cost_alert_message || "") +
      '">缺成本</span> ';
    var warnRr = rrHigh
      ? '<span class="warn-rr" data-tip="' +
        esc(presentation.refund_rate_alert_message || "") +
        '">高退款</span> '
      : "";
    var warnUnsettled = hasUnsettled
      ? '<span class="warn-unsettled" data-tip="' +
        esc(presentation.unsettled_alert_message || "") +
        '">≈</span> '
      : "";
    var status =
      it.status === "ACTIVATE" || !it.status
        ? ""
        : `<span class="spu-status is-down">${esc(it.status)}</span>`;
    return (
      `<span class="td-spu-cell">${img}<span class="td-spu-meta">` +
      `<span class="td-spu">${esc(it.spu_id)}</span>` +
      `<span class="td-title" data-tip="${esc(it.title || "")}">${warnUnsettled}${warnDefault ? warnCost : ""}${warnRr}${esc(it.title || "")}${status}</span></span></span>`
    );
  }

  function moneyCell(cell) {
    return fmtMoney(cell.getValue());
  }
  function ratioCell(cell) {
    return fmtRatio(cell.getValue());
  }
  function intCell(cell) {
    return fmtInt(cell.getValue());
  }
  function pctCell(cell) {
    return fmtPctOrDash(cell.getValue());
  }
  function breakevenRoiCell(cell) {
    var it = cell.getData();
    var v = it.ad_system_breakeven_roi;
    if (v === null || v === undefined || v === "") return "—";
    return (
      (it.ad_system_breakeven_roi_status === "estimated_known_costs"
        ? "≈"
        : "") + fmtRatio(v)
    );
  }
  function netProfitCell(cell) {
    var it = cell.getData();
    var text = fmtMoney(it.net_profit);
    return it.profit_status === "loss"
      ? `<span class="np-red">${text}</span>`
      : text;
  }

  var CELL_FORMATTERS = {
    spu_id: productCellFormatter,
    spend: moneyCell,
    ad_system_actual_roi: ratioCell,
    ad_system_breakeven_roi: breakevenRoiCell,
    effective_sales: moneyCell,
    total_orders: intCell,
    effective_order_count: intCell,
    cancel_rate: pctCell,
    full_loss_rate: pctCell,
    net_profit: netProfitCell,
  };

  // true while we mirror state.sort/order into Tabulator — its dataSorting
  // event must not retrigger a server fetch in that window.
  var _applyingServerSort = false;
  function _withSortSuppressed(fn) {
    _applyingServerSort = true;
    try {
      fn();
    } finally {
      _applyingServerSort = false;
    }
  }

  function buildTable() {
    var host = document.getElementById("rows");
    if (!host || state.table) return;
    var columns = COLUMN_DEFS.map((def) => ({
      title: def.title,
      field: def.field,
      headerSort: Boolean(def.sortField),
      headerTooltip: def.tip || undefined,
      headerHozAlign: def.columnId === "product" ? "left" : "right",
      hozAlign: def.columnId === "product" ? "left" : "right",
      frozen: def.columnId === "product",
      minWidth: def.columnId === "product" ? 170 : 120,
      // fitColumns 把余量按 widthGrow 分给各列：商品列标题长，多吃 3 份。
      widthGrow: def.columnId === "product" ? 3 : 1,
      formatter: CELL_FORMATTERS[def.field],
    }));
    // 默认排序可能是不对应任何列的字段（如 roi_real）：无列可标时跳过 initialSort。
    var initialDef = COLUMN_DEFS.find((d) => d.sortField === state.sort);
    state.table = new Tabulator(host, {
      columns: columns,
      // 默认 fitData 只按内容定宽，宽屏下表体右侧留大片空白（表头/行背景
      // 都是全宽，唯独单元格列不铺满）。fitColumns 使列宽铺满容器。
      layout: "fitColumns",
      data: [],
      placeholder: "加载中…",
      // 行内下钻直接往行 DOM 后插 div，禁用虚拟滚动防止行被重排回收。
      renderVertical: "basic",
      maxHeight: "min(72vh, 880px)",
      initialSort: initialDef
        ? [{ column: initialDef.field, dir: state.order }]
        : [],
      rowFormatter: (row) => {
        row
          .getElement()
          .classList.toggle("row-bad", row.getData().profit_status === "loss");
      },
    });
    // 用 table.on 订阅（与 dataSorting 同一机制）；6.3 对 options 回调的订阅不可靠。
    state.table.on("rowClick", (e, row) => {
      var it = row.getData();
      if (!it || !it.spu_pk) return;
      openDrillPanel(row.getElement(), it);
    });
    // 表头点击只改状态并触发服务端重取（本地排序对同字段幂等）。
    state.table.on("dataSorting", (sorters) => {
      if (_applyingServerSort) return;
      var s = sorters && sorters[0];
      if (!s) return;
      var def = COLUMN_DEFS.find((d) => d.field === s.field);
      if (!def || !def.sortField) return;
      var dir = s.dir === "asc" ? "asc" : "desc";
      // 内部 clearData/replaceData 在排序列激活时会重派发本事件；幂等跳过。
      if (def.sortField === state.sort && dir === state.order) return;
      state.sort = def.sortField;
      state.order = dir;
      state.offset = 0;
      persistPagePreferences();
      // 推迟到事件派发结束后重取：同步 clearData/replaceData 会环 Tabulator
      // 进行中的本地排序重渲染（styleRow 报 undefined.add）。
      setTimeout(() => load(), 0);
    });
    // 错误占位里的「重试」链接（placeholder 是 innerHTML 注入，走委托）。
    host.addEventListener("click", (e) => {
      var link = e.target.closest && e.target.closest("#retry-link");
      if (link) {
        e.preventDefault();
        load();
      }
    });
    // Esc 关闭下钻（原 bindRowAccordion 里的全局监听）。
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDrillPanel();
    });
  }

  function tableShowPlaceholder(message) {
    if (!state.table) return;
    closeDrillPanel();
    state.table.options.placeholder = message;
    // 排序列激活时 clearData 会同步重派发 dataSorting；置抑制位防递归。
    _withSortSuppressed(() => state.table.clearData());
  }

  function updateSortNote() {
    $("#sort-note").textContent =
      `当前排序：${sortLabel(state.sort)}${state.order === "asc" ? " ↑" : " ↓"}`;
  }

  function renderError(msg, wholePage) {
    closeDrillPanel();
    if (wholePage) {
      var summaries = $("#summaries");
      var pager = document.querySelector("main .op-pager");
      var footnotes = $("#footnotes");
      if (summaries) summaries.hidden = true;
      if (pager) pager.hidden = true;
      if (footnotes) footnotes.hidden = true;
      $("#sum-stamp").textContent = "";
    }
    tableShowPlaceholder(
      `${esc(msg)} · <a href="#" id="retry-link">重试</a>`,
    );
  }

  function renderEmpty(message) {
    var text =
      message ||
      (profile && profile.emptyMessage) ||
      "没有匹配所选 SPU 和当前条件的数据";
    tableShowPlaceholder(esc(text));
  }

  function renderEmptySelection() {
    closeDrillPanel();
    var summaries = $("#summaries");
    var pager = document.querySelector("main .op-pager");
    var footnotes = $("#footnotes");
    var feeCard = $("#fee-card");
    if (summaries) summaries.hidden = true;
    if (pager) pager.hidden = true;
    if (footnotes) footnotes.hidden = true;
    if (feeCard) feeCard.hidden = true;
    $("#sum-stamp").textContent = "";
    renderEmpty(profile && profile.emptySelectionMessage);
  }

  // 费率来源中文标签（meta.fee.source）
  var FEE_SOURCE_LABEL = {
    user_override: "页面覆写",
    shop_estimate: "店铺实测",
    baseline: "全局基线",
    mixed: "混合口径",
  };

  // 店铺费率状态卡（feature/shop-fee-rate）：把后端 meta.fee 的口径如实呈现
  // —— 来源 / 实测样本量 / 覆盖率 / 快照日期 / 降级原因。
  // 只用 textContent 写入，不引入 innerHTML 的 XSS 面。
  function renderFeeCard(fee) {
    var card = $("#fee-card");
    if (!card) return;
    if (!fee) {
      card.hidden = true;
      return;
    }
    var rateNum = parseFloat(fee.rate);
    $("#fee-card-source").textContent =
      FEE_SOURCE_LABEL[fee.source] || fee.source || "—";
    $("#fee-card-rate").textContent = Number.isFinite(rateNum)
      ? (rateNum * 100).toFixed(2) + "%"
      : "—";

    // 逐店铺明细：实测口径列样本量/覆盖率/窗口/快照日；基线口径说明降级原因。
    var parts = [];
    (fee.per_shop || []).forEach((s) => {
      var name = s.shop_name || String(s.shop_pk);
      if (s.source === "shop_estimate" && s.estimate) {
        var kept = (parseFloat(s.estimate.kept_share) * 100).toFixed(1);
        parts.push(
          name +
            " 实测 " +
            (parseFloat(s.rate) * 100).toFixed(2) +
            "%（未退款订单 " +
            s.estimate.kept_order_count +
            " 单 · 占窗口 GMV " +
            kept +
            "% · 近 " +
            s.estimate.lookback_days +
            " 天 · " +
            String(s.estimate.calculated_on) +
            " 重算）",
        );
      } else if (s.source === "baseline") {
        parts.push(
          name +
            " 回退基线（" +
            (s.fallback_reason === "stale_estimate"
              ? "快照已过期"
              : "无可用实测样本") +
            "）",
        );
      } else if (s.source === "user_override") {
        parts.push(name + " 页面覆写");
      }
      // source === "mixed" 不会出现在 per_shop（那是聚合层标记）
    });
    var estEl = $("#fee-card-estimate");
    estEl.textContent = parts.join(" · ");
    estEl.hidden = parts.length === 0;

    var fbEl = $("#fee-card-fallback");
    fbEl.textContent = fee.degraded ? fee.fallback_message || "" : "";
    fbEl.hidden = !fee.degraded;

    card.hidden = false;
  }

  function pagerSequence(current, total) {
    var candidates = [1, current - 1, current, current + 1, total]
      .filter((page) => page >= 1 && page <= total)
      .sort((a, b) => a - b)
      .filter((page, index, pages) => index === 0 || page !== pages[index - 1]);
    var sequence = [];
    candidates.forEach((page) => {
      var previous = sequence.length ? sequence[sequence.length - 1] : null;
      if (previous !== null && page - previous > 1) sequence.push(null);
      sequence.push(page);
    });
    return sequence;
  }

  function renderPager(page, pages) {
    var pager = $("#pager-pages");
    if (!pager) return;
    pager.replaceChildren();

    function addButton(label, target, options) {
      var isActive = options && options.active;
      var isDisabled = options && options.disabled;
      var item = el("li", {
        class:
          "page-item" +
          (isActive ? " active" : "") +
          (isDisabled && !isActive ? " disabled" : ""),
      });
      var button = el(
        "button",
        {
          type: "button",
          class: "page-link",
          "data-page": String(target),
          "aria-label": options.label,
        },
        label,
      );
      button.disabled = Boolean(isDisabled);
      if (isActive) button.setAttribute("aria-current", "page");
      item.appendChild(button);
      pager.appendChild(item);
    }

    addButton("‹", "previous", {
      disabled: page <= 1,
      label: "上一页",
    });
    pagerSequence(page, pages).forEach((target) => {
      if (target === null) {
        pager.appendChild(
          el(
            "li",
            { class: "page-item disabled", "aria-hidden": "true" },
            el("span", { class: "page-link" }, "…"),
          ),
        );
        return;
      }
      addButton(String(target), target, {
        active: target === page,
        disabled: target === page,
        label: `跳转到第 ${target} 页`,
      });
    });
    addButton("›", "next", {
      disabled: page >= pages,
      label: "下一页",
    });
  }

  function render(payload) {
    var summaries = $("#summaries");
    var pager = document.querySelector("main .op-pager");
    var footnotes = $("#footnotes");
    if (summaries) summaries.hidden = false;
    if (pager) pager.hidden = false;
    if (footnotes) footnotes.hidden = false;
    var items = unwrap(payload);
    var totals = payload.totals || {};
    var meta = payload.meta || {};
    state.meta = meta;
    lastTotal = payload.total || 0;

    // 结余带(全部由后端 totals 提供，前端只做格式化，禁止前端计算)
    // 总单量 = 有效订单 + 取消订单(后端全局 distinct)
    $("#sum-total-orders").textContent = fmtInt(totals.total_orders || 0);
    // 有效单量 = 有效订单 − 退款订单(后端 effective_order_count)
    $("#sum-orders").textContent = fmtInt(totals.effective_order_count || 0);
    // 有效销售额 = 有效GMV − 退款金额(后端 effective_sales)
    $("#sum-sales").textContent = fmtMoney(totals.effective_sales);
    // 退款数 = 全局 distinct 退款订单数(后端)
    $("#sum-refund-count").textContent = fmtInt(totals.refund_order_count || 0);
    // 退款率(后端)
    $("#sum-refund-rate").textContent = fmtPct(totals.refund_rate);
    // 全损量 = 退款订单 + 海外取消订单(后端 full_loss_order_count)
    $("#sum-loss-qty").textContent = fmtInt(totals.full_loss_order_count || 0);
    // 全损率(后端)
    $("#sum-loss-rate").textContent = fmtPct(totals.full_loss_rate);
    // 取消量 = 国内取消(后端)
    $("#sum-cancel-count").textContent = fmtInt(
      totals.domestic_cancelled_order_count || 0,
    );
    // 取消率(后端)
    $("#sum-cancel-rate").textContent = fmtPct(totals.cancel_rate);
    // 广告消耗(后端)
    $("#sum-spend").textContent = fmtMoney(totals.spend);
    // 净利润状态由后端返回，前端只应用视觉 class。
    var netProfitEl = $("#sum-net-profit");
    netProfitEl.textContent = fmtMoney(totals.net_profit);
    netProfitEl.classList.toggle("is-err", totals.profit_status === "loss");
    netProfitEl.classList.toggle("is-ok", totals.profit_status === "profit");
    // 实际ROI(后端计算)
    var roiEl = $("#sum-roi");
    var roiOverall = totals.roi_real;
    roiEl.textContent = fmtRatio(roiOverall);
    roiEl.classList.toggle("is-err", totals.roi_status === "negative");
    // 实际保本ROI(后端计算)
    var roiBreakevenEl = $("#sum-roi-breakeven");
    roiBreakevenEl.textContent = fmtRatio(totals.roi_breakeven);
    // 广告系统实际ROI：广告归因GMV ÷ 广告实际消耗。
    var roiAdActualEl = $("#sum-roi-ad-actual");
    roiAdActualEl.textContent = fmtRatio(totals.ad_system_actual_roi);
    // 广告系统保本ROI：广告归因GMV ÷ 已知成本下最大可承受广告费。
    // estimated_known_costs 表示尚未纳入结算外必要成本，必须用 ≈ 明示估算。
    var roiAdEl = $("#sum-roi-ad");
    var roiAdStatus = totals.ad_system_breakeven_roi_status;
    var roiAdValue = totals.ad_system_breakeven_roi;
    roiAdEl.textContent =
      roiAdValue === null || roiAdValue === undefined || roiAdValue === ""
        ? "—"
        : (roiAdStatus === "estimated_known_costs" ? "≈" : "") +
          fmtRatio(roiAdValue);

    // 预测由后端基于同一日期窗口计算；当前实际卡片保持不变。
    // StaticFiles 会即时读取新 JS，而 HTML 模板要等 API 进程重启才更新；
    // 部署窗口内新 hook 可能暂时不存在，不能让整页渲染因此中断。
    setTextIfPresent(
      "#sum-projection-status",
      projectionStatusLabel(totals.projection_status),
    );
    setTextIfPresent(
      "#sum-projection-completed-basis-orders",
      fmtInt(totals.projection_completed_basis_order_count),
    );
    setTextIfPresent(
      "#sum-projection-completed-loss-orders",
      fmtInt(totals.projection_completed_full_loss_order_count),
    );
    setTextIfPresent(
      "#sum-projection-full-loss-rate",
      fmtPct(totals.completed_full_loss_rate),
    );
    setTextIfPresent(
      "#sum-unresolved-orders",
      fmtInt(totals.unsettled_order_count),
    );
    setTextIfPresent(
      "#sum-delivered-unsettled-orders",
      fmtInt(totals.delivered_unsettled_order_count),
    );
    setTextIfPresent(
      "#sum-full-loss-exposure-unsettled-orders",
      fmtInt(totals.full_loss_exposure_unsettled_order_count),
    );
    setTextIfPresent(
      "#sum-projected-future-loss-qty",
      fmtQty(totals.projected_future_full_loss_qty),
    );
    setTextIfPresent(
      "#sum-projected-net-revenue",
      fmtMoney(totals.projected_net_revenue),
    );
    setTextIfPresent(
      "#sum-projected-net-profit",
      fmtMoney(totals.projected_net_profit),
    );
    setTextIfPresent(
      "#sum-projected-roi",
      fmtRatio(totals.projected_roi_real),
    );
    setTextIfPresent(
      "#sum-projected-breakeven-roi",
      fmtRatio(totals.projected_roi_breakeven),
    );
    setTextIfPresent(
      "#sum-projected-ad-roi",
      fmtRatio(totals.projected_ad_system_actual_roi),
    );
    setTextIfPresent(
      "#sum-projected-ad-breakeven-roi",
      fmtRatio(totals.projected_ad_system_breakeven_roi),
    );
    var rubricLabel =
      (meta.presentation && meta.presentation.rubric_label) ||
      meta.rubric_version ||
      "";
    $("#sum-stamp").textContent =
      `全表 ${(meta.currency && meta.currency.display) || "CNY"} · 数据库汇率快照 ${meta.fx ? meta.fx.as_of : ""} · ${rubricLabel}`;

    // 表格（Tabulator）
    closeDrillPanel();
    state.openDrillRow = null;
    if (state.table) {
      state.table.options.placeholder = items.length
        ? "没有匹配所选 SPU 和当前条件的数据"
        : esc(
            (profile && profile.emptyMessage) ||
              "没有匹配所选 SPU 和当前条件的数据",
          );
      _withSortSuppressed(() => {
        // 只有存在对应列时才镜像表头箭头（roi_real 等服务端字段无列）。
        var sortDef = COLUMN_DEFS.find((d) => d.sortField === state.sort);
        if (sortDef) state.table.setSort(sortDef.field, state.order);
        else state.table.setSort([]);
        state.table.replaceData(items);
      });
    }

    // 分页：服务端仍使用 offset + limit；前端仅把它呈现为可跳转的 Bootstrap 页码。
    var page = Math.floor(state.offset / state.limit) + 1;
    var pages = Math.max(1, Math.ceil(lastTotal / state.limit));
    $("#pager-label").textContent =
      `第 ${page} / ${pages} 页 · 共 ${lastTotal} 个 SPU`;
    renderPager(page, pages);

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
      // 逐店铺样本/覆盖率细节在新费率状态卡里；页脚只留一行聚合口径。
      renderFeeCard(meta.fee);
      notes.push(
        `平台佣金费率 ${esc(meta.fee.rate)}（${
          FEE_SOURCE_LABEL[meta.fee.source] || meta.fee.source
        }）`,
      );
      // 费率输入框 placeholder 跟随当前口径(仅影响空输入时的灰字提示)
      var feeInputEl = $("#filter-fee");
      if (feeInputEl && meta.fee.source !== "user_override") {
        feeInputEl.placeholder = (parseFloat(meta.fee.rate) * 100).toFixed(1);
      }
    }
    if (typeof meta.unattributed_refund_lines === "number") {
      notes.push(`未归属退款 ${meta.unattributed_refund_lines} 行`);
    }
    if (meta.cost_assumption) notes.push(meta.cost_assumption);
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
      // 同步 state(修复:之前只设 DOM.value,state.wStart/wEnd 仍是 "",
      // api() 用 state.wStart || null 发请求,导致 w_start/w_end 参数不传、过滤不生效)
      state.wStart = cw.coverage_first_day;
      state.wEnd = cw.coverage_last_day;
      updateDatePresetUi();
      // 首次 load() 是在 state 同步前发的(无日期参数,全历史);
      // 现在 state 有了,重发一次让过滤生效(不重发用户点刷新才能看到过滤结果)
      load();
    }

    // D8(2026-09-07):⚙ 列开关组全删,applyColToggles 不再调用
    // (94afd70 删定义/绑定/state.cols 时漏删了这处调用,跑起来 ReferenceError)
    applyViewProfile();
    updateSortNote();
  }

  // ---------- 钻取面板 (D7 行内 accordion + D6 tab 懒加载) ----------
  function clearDrillCache() {
    state.drillCache = new Map();
  }
  function _drillCacheKey(spuPk, tab) {
    return [spuPk, tab, state.wStart, state.wEnd].join("|");
  }
  function _drillUrl(tab, spuPk) {
    var base =
      PREFIX + "/v2/analytics/spu-roi/" + encodeURIComponent(spuPk) + "/" + tab;
    var qs = [];
    if (state.wStart) qs.push("w_start=" + encodeURIComponent(state.wStart));
    if (state.wEnd) qs.push("w_end=" + encodeURIComponent(state.wEnd));
    return qs.length ? base + "?" + qs.join("&") : base;
  }
  function fetchDrillTab(spuPk, tab) {
    var key = _drillCacheKey(spuPk, tab);
    if (state.drillCache.has(key))
      return Promise.resolve(state.drillCache.get(key));
    return fetch(_drillUrl(tab, spuPk), {
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then((r) => {
        if (r.status === 401) {
          // pi-lens-ignore: no-open-redirect-js
          window.location.href = loginUrl();
          throw new Error("unauthorized");
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((data) => {
        state.drillCache.set(key, data);
        return data;
      });
  }
  function closeDrillPanel() {
    if (state.openDrillRow) {
      var nxt = state.openDrillRow.nextElementSibling;
      if (nxt && nxt.classList && nxt.classList.contains("op-drill-row"))
        nxt.remove();
      state.openDrillRow = null;
    }
  }
  // hintSpan: 钻取面板各 tab 共用的 hint 标记（? 图标 + data-tip 气泡）
  function hintSpan(hint) {
    return hint
      ? el("span", { class: "op-hint", "data-tip": hint }, "?")
      : null;
  }
  function renderProfitSummary(it) {
    // 明细大盘与页首实际大盘完全同组同口径；值均由后端行字段给出，前端只格式化。
    function money(value) {
      return value == null || value === "" ? "—" : fmtMoney(value);
    }
    function cell(label, value, hint) {
      var hintEl = hint
        ? el("span", { class: "op-hint", "data-tip": hint }, "?")
        : null;
      return el(
        "div",
        { class: "col" },
        el(
          "div",
          { class: "op-drill-cell h-100" },
          el("span", { class: "op-drill-lbl" }, label, hintEl),
          el(
            "span",
            { class: "op-drill-val" },
            String(value == null ? "—" : value),
          ),
        ),
      );
    }
    var adSystemMeta = state.meta.ad_system_roi || {};
    var adSystemBreakeven =
      it.ad_system_breakeven_roi == null || it.ad_system_breakeven_roi === ""
        ? "—"
        : (it.ad_system_breakeven_roi_status === "estimated_known_costs"
            ? "≈"
            : "") + fmtRatio(it.ad_system_breakeven_roi);
    return el(
      "div",
      {
        class:
          "row row-cols-2 row-cols-sm-3 row-cols-lg-4 row-cols-xxl-4 g-2 op-drill-grid",
      },
      cell("总单量", String(it.total_orders || 0)),
      cell("广告消耗", money(it.spend)),
      cell("有效单量", String(it.effective_order_count || 0)),
      cell("有效销售", money(it.effective_sales)),
      cell("退款数", String(it.refund_order_count || 0)),
      cell("退款率", fmtPct(it.refund_rate)),
      cell("全损量", String(it.full_loss_order_count || 0)),
      cell("全损率", fmtPct(it.full_loss_rate)),
      cell("国内取消量", String(it.domestic_cancelled_order_count || 0)),
      cell("国内取消率", fmtPct(it.cancel_rate)),
      cell("净利润", money(it.net_profit)),
      cell("实际ROI", fmtRatio(it.roi_real)),
      cell("实际保本ROI", fmtRatio(it.roi_breakeven)),
      cell(
        "广告系统实际ROI",
        fmtRatio(it.ad_system_actual_roi),
        adSystemMeta.actual_formula,
      ),
      cell(
        "广告系统保本ROI",
        adSystemBreakeven,
        [adSystemMeta.breakeven_formula, adSystemMeta.warning]
          .filter(Boolean)
          .join("；"),
      ),
      cell("预测状态", projectionStatusLabel(it.projection_status)),
      cell(
        "已完结样本订单",
        fmtInt(it.projection_completed_basis_order_count),
      ),
      cell(
        "已完结全损订单",
        fmtInt(it.projection_completed_full_loss_order_count),
      ),
      cell(
        "其中终局物流全损订单",
        fmtInt(it.projection_terminal_full_loss_order_count),
      ),
      cell("已完结订单全损率", fmtPct(it.completed_full_loss_rate)),
      cell("未结算订单", fmtInt(it.unsettled_order_count)),
      cell("待确认未结算订单", fmtInt(it.unresolved_unsettled_order_count)),
      cell("待确认未结算件", fmtQty(it.unresolved_unsettled_qty)),
      cell("待确认未结算销售", money(it.unresolved_unsettled_sales)),
      cell("未送达风险销售", money(it.full_loss_exposure_unsettled_sales)),
      cell("已确认未结算退款", money(it.confirmed_unsettled_refund_amount)),
      cell(
        "已确认风险池退款",
        money(it.confirmed_full_loss_exposure_refund_amount),
      ),
      cell(
        "已确认未结算全损单",
        fmtInt(it.confirmed_unsettled_full_loss_order_count),
      ),
      cell("已确认未结算全损件", fmtQty(it.confirmed_unsettled_full_loss_qty)),
      cell("预计未来新增退款", money(it.projected_future_refund_amount)),
      cell(
        "预计未来新增全损单",
        fmtQty(it.projected_future_full_loss_order_count),
      ),
      cell("预计未来新增全损件", fmtQty(it.projected_future_full_loss_qty)),
      cell("预计全损成本", money(it.projected_full_loss_cost)),
      cell("预计未结算净收入", money(it.projected_unsettled_net)),
      cell("预计净收入", money(it.projected_net_revenue)),
      cell("预计净利润", money(it.projected_net_profit)),
      cell("预计ROI", fmtRatio(it.projected_roi_real)),
      cell("预计保本ROI", fmtRatio(it.projected_roi_breakeven)),
      cell("预计广告系统ROI", fmtRatio(it.projected_ad_system_actual_roi)),
      cell(
        "预计广告系统保本ROI",
        fmtRatio(it.projected_ad_system_breakeven_roi),
      ),
      cell("预计NC′", money(it.projected_nc_prime)),
      cell("预计保留货本", money(it.projected_cogs_kept)),
    );
  }
  function renderProfitTab(it) {
    // B1 fix: 用后端暴露的 settled_net(SETTLEMENT 净额),不用 gross settled_sales 比例拆
    // 金额分解全部由后端 v10 module 给出；前端只格式化，不重算盈利。
    var settledNet = Number(it.settled_net || 0);
    var unsettledNet = Number(it.unsettled_net || 0);
    var cogsSold = Number(it.cogs_sold || 0);
    var cogsFlc = Number(it.cogs_full_loss_cancelled || 0);
    var spend = Number(it.spend || 0);
    var np = Number(it.net_profit || 0);

    // 瀑布分层结构(<div>,不用 <table>):加项 / 减项 / 结果三段式,
    // 与设计稿 §6.2 「P&L 分解瀑布」对齐;其余 4 tab 是行级列表保留 <table>。
    function row(label, val, sign, hint) {
      // sign = "add" | "sub" | "result" —— 控制前缀 +/-/= 颜色。
      var signChar = sign === "sub" ? "−" : sign === "result" ? "=" : "+";
      var klass = "op-pnl-row op-pnl-" + sign;
      return el(
        "div",
        { class: klass },
        el("span", { class: "op-pnl-row-label" }, label, " ", hintSpan(hint)),
        el(
          "span",
          { class: "op-pnl-row-val" },
          el("span", { class: "op-pnl-sign" }, signChar + " "),
          fmtMoney(val),
        ),
      );
    }
    function layer(title, hint, rows) {
      // title 左对齐(人类阅读习惯);hint 为层口径说明
      return el(
        "div",
        { class: "col" },
        el(
          "section",
          { class: "op-pnl-layer h-100" },
          el(
            "div",
            { class: "op-pnl-layer-title" },
            el("span", { class: "op-pnl-layer-titletext" }, title),
            hintSpan(hint),
          ),
          el("div", { class: "op-pnl-rows" }, rows),
        ),
      );
    }

    var pnlHints =
      (state.meta.presentation && state.meta.presentation.pnl_hints) || {};
    var layerRev = layer("净收入", pnlHints.net_revenue, [
      row("已结算", settledNet, "add", pnlHints.settled),
      row("未结算", unsettledNet, "add", pnlHints.unsettled),
    ]);
    var layerCogs = layer("货本", pnlHints.cogs, [
      row("售出件", cogsSold, "sub", pnlHints.cogs_sold),
      row("全损取消件", cogsFlc, "sub", pnlHints.cogs_full_loss),
    ]);
    var layerAd = layer("广告消耗", pnlHints.ad_spend, [
      row("消耗", spend, "sub", pnlHints.ad_spend),
    ]);
    var layerResult = el(
      "div",
      { class: "col" },
      el(
        "section",
        { class: "op-pnl-layer op-pnl-layer-result h-100" },
        row(
          "净利润",
          np,
          it.profit_status === "loss"
            ? "result op-pnl-row-neg"
            : "result op-pnl-row-pos",
          pnlHints.net_profit,
        ),
      ),
    );

    return el(
      "div",
      { class: "row row-cols-1 row-cols-lg-2 row-cols-xxl-4 g-2 op-pnl" },
      layerRev,
      layerCogs,
      layerAd,
      layerResult,
    );
  }
  function renderDrillTabBody(tab, data) {
    function loading(msg) {
      return el("div", { class: "op-drill-loading" }, msg);
    }
    if (!data) return loading("无数据");
    if (tab === "settlements") {
      var ss = data.settlements || [];
      if (!ss.length)
        return loading("未结算订单不展示（基线估算见利润构成 tab）。");
      var srows = ss.map((s) => {
        var comp = (s.components || []).filter(
          (c) => c.code === "SETTLEMENT",
        )[0];
        return el(
          "tr",
          null,
          el("td", null, s.order_id),
          el("td", null, comp ? fmtMoney(comp.amount) : "—"),
          el("td", null, s.share_ratio || "—"),
          el("td", null, s.statement_time || "—"),
        );
      });
      return el(
        "table",
        { class: "op-tab-table" },
        el(
          "thead",
          null,
          el(
            "tr",
            null,
            el("th", null, "订单号"),
            el("th", null, "SETTLEMENT"), // 表头用 column_header 翻译
            el(
              "th",
              null,
              "分摊比",
              hintSpan(
                "分摊比 = line_gmv / order_gmv;SETTLEMENT 按这个比例分到各行",
              ),
            ),
            el("th", null, "statement"), // 表头用 column_header 翻译
          ),
        ),
        el("tbody", null, srows),
      );
    }
    if (tab === "orders") {
      var orders = data.orders || [];
      if (!orders.length) return loading("无订单");
      var orows = orders.map((o) =>
        el(
          "tr",
          null,
          el("td", null, o.order_id),
          el("td", null, tr("order_status", o.status)),
          el("td", null, o.qty),
          el("td", null, o.is_settled ? "✓" : "—"),
          el("td", null, o.arrived_overseas ? "✓" : "—"),
          el("td", null, o.full_loss ? "⚠" : "—"),
        ),
      );
      return el(
        "table",
        { class: "op-tab-table" },
        el(
          "thead",
          null,
          el(
            "tr",
            null,
            el("th", null, "订单号"),
            el("th", null, "状态"),
            el("th", null, "件"),
            el("th", null, th("is_settled")),
            el("th", null, th("38301")),
            el("th", null, "全损"),
          ),
        ),
        el("tbody", null, orows),
      );
    }
    if (tab === "cases") {
      var cases = data.cases || [];
      if (!cases.length) return loading("无售后 case");
      var crows = cases.map((c) =>
        el(
          "tr",
          null,
          el("td", null, c.case_id),
          el("td", null, c.order_id || "—"),
          el("td", null, tr("case_type", c.type)),
          el("td", null, tr("case_status", c.status)),
          el("td", null, fmtMoney(c.refund_amount)),
        ),
      );
      return el(
        "table",
        { class: "op-tab-table" },
        el(
          "thead",
          null,
          el(
            "tr",
            null,
            el(
              "th",
              null,
              th("case"),
              hintSpan(
                "case_id = 售后 case 外部 ID(after_sales.cases.external_case_id)",
              ),
            ),
            el(
              "th",
              null,
              "订单",
              hintSpan(
                "order_id = 关联订单 ID(after_sales.cases.order_pk → commerce.sales_orders.order_id)",
              ),
            ),
            el(
              "th",
              null,
              "类型",
              hintSpan(
                "case_type = RETURN_AND_REFUND(退货退款) / REFUND_ONLY(仅退款) / CANCELLATION(取消)",
              ),
            ),
            el(
              "th",
              null,
              "状态",
              hintSpan(
                "case 状态;RETURN_OR_REFUND_REQUEST_COMPLETE / CANCELLATION_REQUEST_COMPLETE 表示完结",
              ),
            ),
            el(
              "th",
              null,
              "退款",
              hintSpan(
                "refund_amount = 该 case 退款金额(CNY,服务端由VND换算,已从 GMV 减除)",
              ),
            ),
          ),
        ),
        el("tbody", null, crows),
      );
    }
    if (tab === "ads") {
      var ads = data.ads || [];
      if (!ads.length) return loading("无广告投放");
      var arows = ads.map((a) =>
        el(
          "tr",
          null,
          el("td", null, a.campaign_id),
          el("td", null, fmtMoney(a.spend)),
          el("td", null, a.orders),
          el("td", null, (a.first_day || "—") + " ~ " + (a.last_day || "—")),
        ),
      );
      return el(
        "table",
        { class: "op-tab-table" },
        el(
          "thead",
          null,
          el(
            "tr",
            null,
            el("th", null, th("campaign_id")),
            el("th", null, th("spend")),
            el("th", null, th("orders")),
            el("th", null, "窗口"),
          ),
        ),
        el("tbody", null, arows),
      );
    }
    return loading("未知 tab");
  }
  function renderDrillLazy(drill, it, which) {
    var body = drill.querySelector('[data-region="body"]');
    body.textContent = "加载中…";
    fetchDrillTab(it.spu_pk, which)
      .then((data) => {
        var content = renderDrillTabBody(which, data);
        if (content && content.tagName === "TABLE") {
          content.classList.add(
            "table",
            "table-sm",
            "table-hover",
            "align-middle",
            "mb-0",
            "op-tab-table",
          );
          content = el("div", { class: "table-responsive" }, content);
        }
        body.replaceChildren(content);
        if (typeof wireTooltips === "function") wireTooltips();
      })
      .catch((e) => {
        body.textContent = "加载失败：" + (e && e.message ? e.message : e);
      });
  }
  function openDrillPanel(row, it) {
    if (state.openDrillRow === row) {
      closeDrillPanel();
      return;
    }
    closeDrillPanel();
    if (!it || !it.spu_pk) return;
    var tpl = document.getElementById("tpl-drilldown-panel");
    if (!tpl) return;
    var frag = tpl.content.cloneNode(true);
    var drillRow = frag.querySelector(".op-drill-row");
    var drill = drillRow.querySelector(".op-drill");
    var banner = drill.querySelector('[data-banner="warn"]');
    if (it.has_unsettled_orders === true) {
      banner.hidden = false;
      banner.textContent =
        (state.meta.presentation &&
          state.meta.presentation.unsettled_alert_message) ||
        "";
    }
    drill
      .querySelector('[data-region="summary"]')
      .replaceChildren(renderProfitSummary(it));
    drill
      .querySelector('[data-region="body"]')
      .replaceChildren(renderProfitTab(it));
    var tabs = drill.querySelectorAll(".op-drill-tab");
    Array.prototype.forEach.call(tabs, (tab) => {
      tab.addEventListener("click", () => {
        Array.prototype.forEach.call(tabs, (t) => {
          t.classList.remove("active", "is-active");
          t.setAttribute("aria-selected", "false");
        });
        tab.classList.add("active", "is-active");
        tab.setAttribute("aria-selected", "true");
        var which = tab.getAttribute("data-tab");
        if (which === "pnl") {
          drill
            .querySelector('[data-region="body"]')
            .replaceChildren(renderProfitTab(it));
        } else {
          renderDrillLazy(drill, it, which);
        }
      });
    });
    var visibleTabs = selectedViewIds("drillTabIds", ALLOWED_DRILL_TABS);
    Array.prototype.forEach.call(tabs, (tab) => {
      tab.hidden = !visibleTabs.has(tab.getAttribute("data-tab"));
    });
    row.insertAdjacentElement("afterend", drillRow);
    state.openDrillRow = row;
  }

  // ---------- shop_pk URL param (必选) ----------
  function getShopPkFromUrl() {
    var u = new URLSearchParams(location.search);
    var v = u.get("shop_pk");
    return v && v !== "" ? v : null;
  }
  function setShopPkInUrl(pk) {
    var u;
    try {
      u = new URL(location.href);
    } catch {
      // 可选 catch 绑定（ES2019）：这里不需要错误对象，且避免 unused-var 告警。
      return;
    }
    if (pk) u.searchParams.set("shop_pk", pk);
    else u.searchParams.delete("shop_pk");
    history.replaceState(null, "", u.toString());
  }

  function getSpuIdsFromUrl() {
    var u = new URLSearchParams(location.search);
    return parsePastedSpuIds(u.get("spu_ids"));
  }

  function setSpuIdsInUrl(spuIds) {
    var u;
    try {
      u = new URL(location.href);
    } catch {
      return;
    }
    if (spuIds.length) u.searchParams.set("spu_ids", spuIds.join(","));
    else u.searchParams.delete("spu_ids");
    history.replaceState(null, "", u.toString());
  }

  function restoreSpuScopeFromUrl() {
    var requested = getSpuIdsFromUrl();
    if (!requested.length) return Promise.resolve();
    if (
      requested.length > MAX_SELECTED_SPUS ||
      requested.some((spuId) => spuId.length > 128)
    ) {
      setSpuFeedback("URL 中的 SPU 筛选无效，已恢复为全部 SPU");
      setSpuIdsInUrl([]);
      return Promise.resolve();
    }
    var requestedShop = state.shopPk;
    var selectionVersion = state.spuSelectionVersion;
    return fetchSpuOptions({ spuIds: requested, limit: MAX_SELECTED_SPUS })
      .then((options) => {
        if (
          !state.spuSelect ||
          state.shopPk !== requestedShop ||
          state.spuSelectionVersion !== selectionVersion
        )
          return;
        var bySpuId = new Map(options.map((option) => [option.spu_id, option]));
        var matched = requested.filter((spuId) => bySpuId.has(spuId));
        state.spuIds = matched;
        matched.forEach((spuId) => {
          var option = bySpuId.get(spuId);
          state.spuSelect.addOption(option);
          state.spuSelect.addItem(spuId, true);
        });
        state.spuSelect.refreshItems();
        var missing = requested.filter((spuId) => !bySpuId.has(spuId));
        setSpuFeedback(
          missing.length
            ? `未找到 ${missing.length} 个 SPU：${missing.join("、")}`
            : "",
        );
        setSpuIdsInUrl(matched);
        updateSpuSelectionUi();
      })
      .catch((error) => {
        if (error && error.message === "unauthorized") return;
        setSpuFeedback("URL 中的 SPU 筛选恢复失败，请刷新页面重试");
      });
  }

  // ---------- 店铺选择弹窗(shop_pk 缺失/无效时强制选择) ----------
  // 2026-09-28 用户拍板:URL 拿不到店铺时弹窗让用户选,不再 toast + 60s 倒计时强跳首页。
  function showShopModal(shops, note) {
    var modal = $("#ops-shop-modal");
    if (!modal) return;
    $("#shop-modal-note").textContent = note || "";
    var list = $("#shop-modal-list");
    list.textContent = "";
    shops.forEach((s) => {
      var label = s.account_name || `#${s.id} (${s.region || "?"})`;
      var btn = el("button", {
        type: "button",
        class: "btn btn-outline-secondary op-shop-modal-item",
        text: label,
      });
      btn.addEventListener("click", () => {
        hideShopModal();
        var sel = $("#shop-switcher");
        if (sel) sel.value = String(s.id);
        selectShop(String(s.id));
      });
      list.appendChild(btn);
    });
    modal.hidden = false;
  }
  function hideShopModal() {
    var modal = $("#ops-shop-modal");
    if (modal) modal.hidden = true;
  }
  function prepareSelection() {
    if (!selectionAdapter || !selectionAdapter.onShopChanged) {
      state.selectionQueryable = true;
      return restoreSpuScopeFromUrl();
    }
    return Promise.resolve(selectionAdapter.onShopChanged(state.shopPk)).then(
      (snapshot) => {
        state.selectionQueryable = !snapshot || snapshot.queryable !== false;
        return snapshot;
      },
    );
  }

  // 下拉切换 / 弹窗点选共用的选中逻辑:写 state + 回写 URL,解析页面自己的
  // selection,必要时补拉枚举映射,然后加载。
  function selectShop(pk) {
    if (state.loadController) state.loadController.abort();
    state.loadVersion += 1;
    state.loadController = null;
    clearDrillCache();
    closeDrillPanel();
    state.shopPk = pk;
    setShopPkInUrl(pk);
    if (
      requiresShopReportingTimeZone() &&
      !applyShopReportingContext(pk)
    ) {
      persistPagePreferences();
      renderError(shopTimeZoneError(pk), true);
      showShopModal(state.shops, shopTimeZoneError(pk));
      return;
    }
    persistPagePreferences();
    state.offset = 0;
    if (!selectionAdapter) {
      resetSpuSelectForShop();
      setSpuIdsInUrl([]);
    }
    prepareSelection().then(() => {
      if (Object.keys(state.enumMap).length) {
        load();
      } else {
        loadEnumMap().then(() => load());
      }
    });
  }

  // ---------- 店铺下拉(必选:shop_pk 来自 URL → 切换写回 URL) ----------
  function loadShops() {
    return fetch(
      `${PREFIX}/v2/commerce/channel-accounts?platform=tiktok&limit=500`,
      {
        credentials: "include",
        headers: { Accept: "application/json" },
      },
    )
      .then((r) => {
        if (r.status === 401) {
          // pi-lens-ignore: no-open-redirect-js
          window.location.href = loginUrl();
          throw new Error("unauthorized");
        }
        if (!r.ok) throw new Error(`shops HTTP ${r.status}`);
        return r.json();
      })
      .then((shops) => {
        var sel = $("#shop-switcher");
        if (!sel || !Array.isArray(shops)) return null;
        // 先绑切换事件,再做任何 early return —— 否则 URL 无 shop_pk / shop_pk 无效
        // 时提前 return null,change listener 永远没绑上,用户选店铺无任何反应
        // (2026-09-28 用户反馈 bug)。
        sel.addEventListener("change", () => selectShop(sel.value));
        if (!shops.length) {
          showShopModal([], "当前没有可用店铺，请先在「店铺注册」页完成注册");
          return null;
        }
        state.shops = shops;
        state.shopsByPk = new Map(
          shops.map((shop) => [String(shop.id), shop]),
        );
        // 填充下拉选项
        shops.forEach((s) => {
          var opt = document.createElement("option");
          opt.value = String(s.id);
          opt.textContent = s.account_name || `#${s.id} (${s.region || "?"})`;
          sel.appendChild(opt);
        });
        // URL 优先；没有 URL 参数时恢复这个页面上次选择的店铺。
        var urlPk = getShopPkFromUrl();
        var preferredPk = urlPk || state.shopPk;
        if (preferredPk) {
          var found = shops.some((s) => String(s.id) === preferredPk);
          if (!found) {
            sel.value = ""; // 避免视觉上默认显示第一个店铺造成误导
            state.shopPk = null;
            persistPagePreferences();
            showShopModal(
              shops,
              urlPk
                ? `URL 中的店铺 ${urlPk} 不存在，请重新选择`
                : "上次选择的店铺已不可用，请重新选择",
            );
            return null;
          }
          sel.value = preferredPk;
          state.shopPk = sel.value;
          setShopPkInUrl(sel.value);
          if (
            requiresShopReportingTimeZone() &&
            !applyShopReportingContext(sel.value)
          ) {
            persistPagePreferences();
            renderError(shopTimeZoneError(sel.value), true);
            showShopModal(shops, shopTimeZoneError(sel.value));
            return null;
          }
          persistPagePreferences();
          if (!selectionAdapter) resetSpuSelectForShop();
        } else {
          // URL 与页面偏好均无 shop_pk → 弹窗让用户选店铺。
          showShopModal(shops, "");
          return null;
        }
        return prepareSelection().then(() => sel.value);
      })
      .catch((err) => {
        // 401 已在 loadShops 内跳转登录,不要为它弹错
        if (err && err.message === "unauthorized") return null;
        // 非 401 失败(网络/5xx/反序列化):不能静默卡死。弹错并提供重试入口
        // (2026-09-28 review P2)。
        showShopModal([], "店铺列表加载失败，请检查网络后重试");
        var list = $("#shop-modal-list");
        if (list) {
          var retry = el("button", {
            type: "button",
            class: "btn btn-outline-secondary op-shop-modal-item",
            text: "重试",
          });
          retry.addEventListener("click", () => {
            hideShopModal();
            loadShops().then((pk) => {
              if (pk) loadEnumMap().then(() => load());
            });
          });
          list.appendChild(retry);
        }
        return null;
      });
  }

  // ---------- load ----------
  function cancelCurrentLoad() {
    if (state.loadController) state.loadController.abort();
    state.loadController = null;
    state.loadVersion += 1;
    state.loading = false;
    updateSpuSelectionUi();
  }

  function load() {
    // 控件变化后最新条件必须立刻生效：取消旧请求并仅允许最新请求渲染。
    if (state.loadController) state.loadController.abort();
    if (requiresShopReportingTimeZone() && !state.reportingTimeZone) {
      state.loading = false;
      state.loadController = null;
      renderError(shopTimeZoneError(state.shopPk), true);
      return;
    }
    if (!state.selectionQueryable) {
      state.loading = false;
      state.loadController = null;
      renderEmptySelection();
      return;
    }
    var controller = new AbortController();
    var loadVersion = state.loadVersion + 1;
    state.loadVersion = loadVersion;
    state.loadController = controller;
    hideTip(); // 重拉前收起可能悬浮的说明气泡
    state.loading = true;
    tableShowPlaceholder("加载中…");
    var feeParam = null;
    if (state.feeRate !== null && state.feeRate !== "") {
      var f = parseFloat(state.feeRate);
      feeParam = Number.isFinite(f) ? String(f) : null;
    }
    updateSpuSelectionUi();
    var selectionParams = selectionAdapter
      ? selectionAdapter.analyticsParams()
      : { spu_ids: state.spuIds.length ? state.spuIds.join(",") : null };
    var protectedKeys = new Set([
      "q",
      "sort",
      "order",
      "limit",
      "offset",
      "include_all",
      "shop_pk",
      "w_start",
      "w_end",
      "fee_rate",
    ]);
    Object.keys(selectionParams || {}).forEach((key) => {
      if (protectedKeys.has(key)) throw new Error(`selection cannot override ${key}`);
    });
    api(
      Object.assign(
        {
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
        },
        selectionParams || {},
      ),
      controller.signal,
    )
      .then((payload) => {
        if (state.loadVersion !== loadVersion) return;
        state.loading = false;
        state.loadController = null;
        render(payload);
        updateSpuSelectionUi();
      })
      .catch((err) => {
        if (state.loadVersion !== loadVersion) return;
        state.loading = false;
        state.loadController = null;
        updateSpuSelectionUi();
        if (
          (err && err.name === "AbortError") ||
          (err && err.message === "unauthorized")
        )
          return;
        if (err && err.code === "FX_RATE_UNAVAILABLE") {
          renderError("汇率数据缺失，无法计算结果", true);
          return;
        }
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
      var t =
        e.target && e.target.closest ? e.target.closest("[data-zoom]") : null;
      if (t) {
        e.preventDefault();
        openLightbox(t.getAttribute("data-zoom"));
      }
    });
  }

  function reportingDateValue(value) {
    if (!state.reportingTimeZone) return null;
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.reportingTimeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(value);
    var values = {};
    parts.forEach((part) => {
      if (part.type !== "literal") values[part.type] = part.value;
    });
    if (!values.year || !values.month || !values.day) return null;
    return `${values.year}-${values.month}-${values.day}`;
  }

  function shiftDateValue(value, days) {
    if (!DATE_VALUE_RE.test(value)) return null;
    var parts = value.split("-").map((part) => parseInt(part, 10));
    var shifted = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + days));
    return [
      shifted.getUTCFullYear(),
      String(shifted.getUTCMonth() + 1).padStart(2, "0"),
      String(shifted.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }

  function reportingEndDate(now) {
    var today = reportingDateValue(now || new Date());
    return today ? shiftDateValue(today, -1) : null;
  }

  function dateRangeHelpText() {
    var shopContext =
      state.shopRegion && state.reportingTimeZone
        ? `（按店铺地区 ${state.shopRegion} · ${state.reportingTimeZone} 计算）`
        : "";
    return `截止日包含当天；快捷范围均截止 T-1${shopContext}，“不限”只取消起始日；手动清空日期表示不限制该端。销售、退款与广告按同一范围统计。`;
  }

  function syncDateInputsFromState() {
    var start = $("#filter-w-start");
    var end = $("#filter-w-end");
    var help = $("#date-range-help");
    if (start) start.value = state.wStart;
    if (end) end.value = state.wEnd;
    if (help) help.textContent = dateRangeHelpText();
    updateDatePresetUi();
  }

  function shopReportingTimeZone(shop) {
    var region =
      shop && typeof shop.region === "string"
        ? shop.region.trim().toUpperCase()
        : "";
    return region ? REGION_TIME_ZONES[region] || null : null;
  }

  function requiresShopReportingTimeZone() {
    var dateConfig = profile.dateRangeControl || {};
    return dateConfig.enabled === true;
  }

  function applyShopReportingContext(pk) {
    var shop = state.shopsByPk.get(String(pk));
    state.shopRegion =
      shop && typeof shop.region === "string"
        ? shop.region.trim().toUpperCase() || null
        : null;
    state.reportingTimeZone = shopReportingTimeZone(shop);
    if (!state.reportingTimeZone) return false;
    applyDefaultDateRange();
    syncDateInputsFromState();
    return true;
  }

  function shopTimeZoneError(pk) {
    var shop = state.shopsByPk.get(String(pk));
    var region =
      shop && typeof shop.region === "string" && shop.region.trim()
        ? shop.region.trim().toUpperCase()
        : "未填写";
    return `店铺 ${pk} 的地区 ${region} 无法确定报表时区，请先补充受支持的店铺地区`;
  }

  function applyDefaultDateRange() {
    var dateConfig = profile.dateRangeControl || {};
    if (state.datesTouched || dateConfig.defaultRange !== "t-1") return;
    var yesterday = reportingEndDate(new Date());
    if (!yesterday) return;
    state.wStart = yesterday;
    state.wEnd = yesterday;
  }

  function dateRangeForPreset(preset, now) {
    var end = reportingEndDate(now);
    if (!end) return null;
    if (preset === "all") return { start: "", end: "" };
    if (preset === "month") return { start: `${end.slice(0, 8)}01`, end: end };
    var days = parseInt(preset, 10);
    if (!Number.isFinite(days)) return null;
    return { start: shiftDateValue(end, -days + 1), end: end };
  }

  function datePresetRangeKey(range) {
    return range ? `${range.start}|${range.end}` : null;
  }

  function preferredDatePresetButtons(buttons, now) {
    var winners = new Map();
    Array.prototype.forEach.call(buttons, (button) => {
      var preset = button.getAttribute("data-date-preset");
      var key = datePresetRangeKey(dateRangeForPreset(preset, now));
      if (!key) return;
      var candidate = {
        button: button,
        priority: preset === "month" ? 2 : 1,
      };
      var winner = winners.get(key);
      if (!winner || candidate.priority > winner.priority) {
        winners.set(key, candidate);
      }
    });
    return winners;
  }

  function updateDatePresetUi() {
    var buttons = document.querySelectorAll("[data-date-preset]");
    var now = new Date();
    var winners = preferredDatePresetButtons(buttons, now);
    Array.prototype.forEach.call(buttons, (button) => {
      var preset = button.getAttribute("data-date-preset");
      var range = dateRangeForPreset(preset, now);
      var winner = winners.get(datePresetRangeKey(range));
      var duplicate = Boolean(winner && winner.button !== button);
      button.hidden = duplicate;
      button.setAttribute("aria-hidden", duplicate ? "true" : "false");
      var active = Boolean(
        !duplicate &&
          range &&
          range.start === state.wStart &&
          range.end === state.wEnd,
      );
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function applyDatePreset(preset) {
    var range = dateRangeForPreset(preset);
    if (!range) return;
    state.wStart = range.start;
    state.wEnd = range.end;
    state.datesTouched = true;
    $("#filter-w-start").value = range.start;
    $("#filter-w-end").value = range.end;
    state.offset = 0;
    persistPagePreferences();
    updateDatePresetUi();
    load();
  }

  function enhanceDateRangeControl() {
    if (
      !profile.dateRangeControl ||
      profile.dateRangeControl.enabled !== true
    )
      return;
    var start = $("#filter-w-start");
    var end = $("#filter-w-end");
    if (!start || !end || !start.parentElement || !end.parentElement) return;
    var startCol = start.parentElement;
    var endCol = end.parentElement;
    if (startCol.parentElement !== endCol.parentElement) return;

    var wrapper = el("div", { class: "col-12 col-lg-auto op-date-range-col" });
    var control = el("div", {
      class: "op-date-range p-2",
      "aria-labelledby": "date-range-label",
      "aria-describedby": "date-range-help",
    });
    var heading = el("div", {
      class: "op-date-range__heading d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2",
    });
    heading.appendChild(
      el("span", {
        id: "date-range-label",
        class: "form-label op-fld-label mb-0",
        text: "日期范围",
      }),
    );
    var presets = el("div", {
      class: "btn-group btn-group-sm op-date-presets",
      role: "group",
      "aria-label": "快捷日期范围",
    });
    [
      ["7", "近 7 天"],
      ["30", "近 30 天"],
      ["month", "本月"],
      ["all", "不限"],
    ].forEach((item) => {
      var button = el("button", {
        type: "button",
        class: "btn btn-outline-secondary",
        "data-date-preset": item[0],
        "aria-pressed": "false",
        text: item[1],
      });
      button.addEventListener("click", () => applyDatePreset(item[0]));
      presets.appendChild(button);
    });
    heading.appendChild(presets);

    var inputRow = el("div", { class: "row g-2 op-date-input-groups" });
    [
      ["起始", start],
      ["截止", end],
    ].forEach((item) => {
      var column = el("div", { class: "col-12 col-sm-6" });
      var inputGroup = el("div", {
        class: "input-group input-group-sm op-date-input-group",
        role: "group",
        "aria-label": `${item[0]}日期`,
      });
      inputGroup.appendChild(
        el("span", { class: "input-group-text", text: item[0] }),
      );
      inputGroup.appendChild(item[1]);
      column.appendChild(inputGroup);
      inputRow.appendChild(column);
    });
    start.setAttribute("aria-describedby", "date-range-help");
    end.setAttribute("aria-describedby", "date-range-help");
    control.appendChild(heading);
    control.appendChild(inputRow);
    control.appendChild(
      el("div", {
        id: "date-range-help",
        class: "form-text op-date-range__help mt-2",
        text: dateRangeHelpText(),
      }),
    );
    wrapper.appendChild(control);
    startCol.parentElement.insertBefore(wrapper, startCol);
    startCol.remove();
    endCol.remove();
    updateDatePresetUi();
  }

  // ---------- 交互绑定 ----------
  function bindControls() {
    if (selectionAdapter) {
      selectionCleanup = selectionAdapter.mount({
        root: document,
        prefix: PREFIX,
        loginUrl: loginUrl,
        getShopPk: () => state.shopPk,
        setSelectionQueryable: (queryable) => {
          state.selectionQueryable = Boolean(queryable);
        },
        reload: () => {
          state.offset = 0;
          load();
        },
      });
    } else {
      initSpuSelect();
      $("#btn-spu-apply").addEventListener("click", () => {
        state.spuIds = selectedSpuDraft();
        state.q = "";
        state.offset = 0;
        setSpuFeedback("");
        setSpuIdsInUrl(state.spuIds);
        load();
      });
      $("#btn-spu-clear").addEventListener("click", () => {
        cancelPendingSpuResolutions();
        if (state.spuSelect) state.spuSelect.clear(true);
        state.spuIds = [];
        state.offset = 0;
        setSpuFeedback("");
        setSpuIdsInUrl([]);
        updateSpuSelectionUi();
        load();
      });
    }

    $("#filter-limit").addEventListener("change", (e) => {
      state.limit = parseInt(e.target.value, 10) || 100;
      state.offset = 0;
      persistPagePreferences();
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
      persistPagePreferences();
      load();
    });

    // 店铺切换:已由 loadShops() 内部绑定(#shop-switcher change → selectShop)

    // 日期范围:空 = 不限;yyyy-mm-dd 直接作 w_start/w_end(含 w_end 当日)
    // 校验: w_start 不能晚于 w_end(否则报错并重置为当前输入的字段)
    function _dateFieldChanged(which, e) {
      var v = e.target.value || "";
      if (which === "start") state.wStart = v;
      else state.wEnd = v;
      state.datesTouched = true;
      // 起始 > 截止 → 拒绝这次查询、重置该输入、提示错误
      if (state.wStart && state.wEnd && state.wStart > state.wEnd) {
        // 先废弃在途请求，避免旧响应随后 render() 覆盖本次校验错误。
        cancelCurrentLoad();
        renderError(
          "起始日期不能晚于截止日期（当前：" +
            state.wStart +
            " ~ " +
            state.wEnd +
            "）",
        );
        e.target.value = "";
        if (which === "start") state.wStart = "";
        else state.wEnd = "";
        persistPagePreferences();
        updateDatePresetUi();
        return;
      }
      state.offset = 0;
      persistPagePreferences();
      updateDatePresetUi();
      load();
    }
    $("#filter-w-start").addEventListener("change", (e) =>
      _dateFieldChanged("start", e),
    );
    $("#filter-w-end").addEventListener("change", (e) =>
      _dateFieldChanged("end", e),
    );

    $("#btn-refresh").addEventListener("click", () => load());
    $("#pager-pages").addEventListener("click", (event) => {
      var button = event.target.closest("button[data-page]");
      if (!button || button.disabled || state.loading) return;
      var target = button.dataset.page;
      var current = Math.floor(state.offset / state.limit) + 1;
      var pages = Math.max(1, Math.ceil(lastTotal / state.limit));
      var nextPage =
        target === "previous"
          ? current - 1
          : target === "next"
            ? current + 1
            : parseInt(target, 10);
      if (!Number.isInteger(nextPage) || nextPage < 1 || nextPage > pages) return;
      state.offset = (nextPage - 1) * state.limit;
      load();
    });

    // 列头排序由 Tabulator 驱动（COLUMN_DEFS.sortField → 服务端重取，见 buildTable）。

    wireTooltips(); // 悬停说明气泡(data-tip 委托,含重渲染后的新行)
    wireZoom(); // 主图点击放大(委托)
    loadMe();
    // 店铺必选:先加载店铺,再加载数据;loadShops 内部处理 shop_pk 校验 + 选择弹窗
    loadShops().then((pk) => {
      if (pk) loadEnumMap().then(() => load());
    });
  }

  function mount(options) {
    if (mounted) throw new Error("SPU profitability page is already mounted");
    profile = (options && options.profile) || {};
    selectionAdapter = profile.selectionAdapter || null;
    var defaults = profile.defaults || {};
    ensureDeliveredUnsettledSummary();
    applyViewProfile();
    state.limit = parseInt(defaults.limit, 10) || 100;
    state.includeAll = Boolean(defaults.includeAll);
    state.sort = defaults.sort || DEFAULT_SORT;
    state.order = defaults.order || DEFAULT_ORDER;
    restorePagePreferences();
    var limitInput = $("#filter-limit");
    if (limitInput) limitInput.value = String(state.limit);
    var includeInput = $("#filter-include-all");
    if (includeInput) includeInput.checked = state.includeAll;
    syncDateInputsFromState();
    enhanceDateRangeControl();
    mounted = true;
    buildTable();
    bindControls();
    return {
      reload: load,
      destroy: () => {
        if (state.loadController) state.loadController.abort();
        cancelPendingSpuResolutions();
        closeDrillPanel();
        closeLightbox();
        if (typeof selectionCleanup === "function") selectionCleanup();
        if (selectionAdapter && selectionAdapter.destroy) selectionAdapter.destroy();
        if (state.spuSelect && state.spuSelect.destroy) state.spuSelect.destroy();
        mounted = false;
      },
    };
  }

  window.ttsErp = window.ttsErp || {};
  window.ttsErp.spuProfitability = { mount: mount };
  document.addEventListener("DOMContentLoaded", () => {
    if (window.ttsErpPageProfile) {
      mount({ root: document, profile: window.ttsErpPageProfile });
    }
  });
})();

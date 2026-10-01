#!/usr/bin/env node
/* Deep headless QA for the SPU ROI Tabulator page.
 *
 * Requires a local app server serving this checkout, for example:
 *   BASE_URL=http://127.0.0.1:9982 NODE_PATH=/home/schan/pi-web/node_modules node scripts/probe_spu_tabulator_deep_qa.js
 *
 * The probe routes all business/API calls in-memory and never writes data. It
 * exercises visible buttons, pagination, sorting, drill tabs, shop selection,
 * SPU filter buttons, retry placeholder, lightbox, and logout.
 */
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:9982";
const CHROME =
  process.env.PLAYWRIGHT_CHROMIUM ||
  "/home/schan/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell";
const PNG = process.env.SCREENSHOT || "/tmp/spu-tabulator-deep-qa.png";
const DATA_IMG =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function asUrl(raw) {
  try {
    return new URL(raw);
  } catch (error) {
    throw new Error(`invalid URL in probe: ${raw}`);
  }
}

function makeItem(i, overrides = {}) {
  const loss = i % 2 === 1;
  return {
    spu_pk: 1000 + i,
    spu_id: `TEST_SPU_${String(i).padStart(3, "0")}`,
    title: `深测商品 ${i}`,
    status: i % 5 === 0 ? "INACTIVE" : "ACTIVE",
    main_image_url: i <= 2 ? DATA_IMG : null,
    uses_default_unit_cost: i === 1,
    refund_rate_alert: i === 1,
    has_unsettled_orders: i === 1,
    profit_status: loss ? "loss" : "profit",
    spend: String(20 + i),
    ad_system_actual_roi: (0.7 + i / 100).toFixed(2),
    ad_system_breakeven_roi: (1.1 + i / 100).toFixed(2),
    ad_system_breakeven_roi_status:
      i === 1 ? "estimated_known_costs" : "exact",
    effective_sales: String(1000 + i * 10),
    total_orders: 10 + i,
    effective_order_count: 8 + i,
    cancel_rate: (0.01 * (i % 10)).toFixed(3),
    full_loss_rate: (0.005 * (i % 10)).toFixed(3),
    net_profit: loss ? String(-10 - i) : String(30 + i),
    refund_order_count: i % 4,
    refund_rate: (0.01 * (i % 8)).toFixed(3),
    full_loss_order_count: i % 3,
    domestic_cancelled_order_count: i % 5,
    roi_real: loss ? "0.70" : "1.80",
    roi_breakeven: "1.10",
    projected_roi_real: "1.20",
    ...overrides,
  };
}

const ALL_ITEMS = Array.from({ length: 240 }, (_, idx) => makeItem(idx + 1));
// Ensure the first row of page 2 under total_orders asc is healthy. This catches
// stale row-bad class if Tabulator reuses DOM nodes but rowFormatter only adds.
ALL_ITEMS[50] = makeItem(51, { profit_status: "profit", net_profit: "88.00" });

function sortItems(items, sort, order) {
  if (!sort) return items;
  const copy = [...items];
  copy.sort((a, b) => {
    const av = a[sort];
    const bv = b[sort];
    const na = Number(av);
    const nb = Number(bv);
    let cmp;
    if (Number.isFinite(na) && Number.isFinite(nb)) cmp = na - nb;
    else cmp = String(av ?? "").localeCompare(String(bv ?? ""));
    return order === "desc" ? -cmp : cmp;
  });
  return copy;
}

function analyticsPayload(url) {
  const params = url.searchParams;
  let items = ALL_ITEMS;
  const spuIds = params.get("spu_ids");
  if (spuIds) {
    const wanted = new Set(spuIds.split(","));
    items = items.filter((item) => wanted.has(item.spu_id));
  }
  items = sortItems(items, params.get("sort"), params.get("order"));
  const total = items.length;
  const limit = Number(params.get("limit") || 100);
  const offset = Number(params.get("offset") || 0);
  items = items.slice(offset, offset + limit);
  return {
    items,
    total,
    totals: {
      total_orders: 999,
      spend: "1234.56",
      effective_order_count: 888,
      effective_sales: "54321.10",
      refund_order_count: 12,
      refund_rate: "0.034",
      full_loss_order_count: 7,
      full_loss_rate: "0.019",
      domestic_cancelled_order_count: 9,
      cancel_rate: "0.024",
      net_profit: "4567.89",
      profit_status: "profit",
      roi_real: "1.23",
      roi_breakeven: "1.08",
      ad_system_actual_roi: "2.34",
      ad_system_breakeven_roi: "1.56",
      ad_system_breakeven_roi_status: "estimated_known_costs",
      projection_status: "projectable",
      projection_terminal_basis_order_count: 50,
      projection_terminal_full_loss_order_count: 3,
      projection_refund_amount_rate: "0.012",
      pre_delivery_full_loss_rate: "0.060",
      unsettled_order_count: 14,
      projected_future_full_loss_qty: "4.5",
      projected_net_revenue: "1111.11",
      projected_net_profit: "222.22",
      projected_roi_real: "1.40",
      projected_roi_breakeven: "1.05",
      projected_ad_system_actual_roi: "2.50",
      projected_ad_system_breakeven_roi: "1.60",
    },
    meta: {
      currency: { display: "CNY" },
      computed_at: "2026-10-01T12:00:00+00:00",
      rubric_version: "v10",
      reporting_timezone: "Asia/Ho_Chi_Minh",
      window: {
        first_day: params.get("w_start") || "2026-09-01",
        last_day: params.get("w_end") || "2026-09-30",
      },
      fee: {
        rate: params.get("fee_rate") || "0.308",
        source: params.get("fee_rate") ? "user_override" : "baseline",
        sample_order_count: 20,
        coverage_rate: "0.80",
        as_of: "2026-10-01",
      },
      presentation: {
        rubric_label: "盈利 v10",
        default_unit_cost_message: "缺少成本，使用默认成本",
        refund_rate_alert_message: "退款率偏高",
        unsettled_alert_message: "含未结算估算",
      },
    },
  };
}

function drillPayload(tab) {
  if (tab === "orders") {
    return {
      orders: [
        {
          order_id: "ORD-001",
          status: "COMPLETED",
          qty: 2,
          is_settled: true,
          arrived_overseas: true,
          full_loss: false,
        },
      ],
    };
  }
  if (tab === "settlements") {
    return {
      settlements: [
        {
          order_id: "ORD-001",
          share_ratio: "1.0",
          statement_time: "2026-10-01",
          components: [{ code: "SETTLEMENT", amount: "12.34" }],
        },
      ],
    };
  }
  if (tab === "cases") {
    return {
      cases: [
        {
          case_id: "CASE-1",
          order_id: "ORD-001",
          type: "REFUND_ONLY",
          status: "RETURN_OR_REFUND_REQUEST_COMPLETE",
          refund_amount: "5.00",
        },
      ],
    };
  }
  if (tab === "ads") {
    return {
      ads: [
        {
          campaign_id: "CAMP-1",
          spend: "77.70",
          orders: 3,
          first_day: "2026-09-01",
          last_day: "2026-09-30",
        },
      ],
    };
  }
  return { items: [] };
}

async function installRoutes(page, state) {
  const fulfillJson = (route, obj, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(obj),
    });

  await page.route("**/v2/auth/me**", (route) =>
    fulfillJson(route, { authenticated: true, role: "admin" }),
  );
  await page.route("**/v2/auth/logout**", (route) => {
    state.logoutCount += 1;
    return fulfillJson(route, { ok: true });
  });
  await page.route("**/v2/auth/login**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>login</h1>" }),
  );
  await page.route("**/v2/config/enum-map**", (route) =>
    fulfillJson(route, {
      order_status: { COMPLETED: "已完成" },
      case_type: { REFUND_ONLY: "仅退款" },
      case_status: { RETURN_OR_REFUND_REQUEST_COMPLETE: "已完结" },
    }),
  );
  await page.route("**/v2/commerce/channel-accounts**", (route) =>
    fulfillJson(route, [
      { id: 7, account_name: "QA 店 7", region: "VN" },
      { id: 8, account_name: "QA 店 8", region: "VN" },
    ]),
  );
  await page.route("**/v2/commerce/channel-product-options**", (route) => {
    const url = asUrl(route.request().url());
    const ids = url.searchParams.get("spu_ids");
    let options = ALL_ITEMS.slice(0, 20).map((item) => ({
      spu_id: item.spu_id,
      title: item.title,
      status: item.status,
    }));
    if (ids) {
      const wanted = new Set(ids.split(","));
      options = options.filter((option) => wanted.has(option.spu_id));
    }
    state.optionRequests.push(url);
    return fulfillJson(route, options);
  });
  await page.route("**/v2/analytics/spu-roi/*/*", (route) => {
    const url = asUrl(route.request().url());
    const tab = url.pathname.split("/").pop();
    state.drillRequests.push(url);
    return fulfillJson(route, drillPayload(tab));
  });
  await page.route("**/v2/analytics/spu-roi?**", (route) => {
    const url = asUrl(route.request().url());
    state.analyticsRequests.push(url);
    if (state.failNextAnalytics) {
      state.failNextAnalytics = false;
      return fulfillJson(route, { detail: "QA forced error" }, 500);
    }
    return fulfillJson(route, analyticsPayload(url));
  });
}

function lastAnalytics(state) {
  assert(state.analyticsRequests.length > 0, "no analytics request recorded");
  return state.analyticsRequests[state.analyticsRequests.length - 1];
}

async function waitForAnalytics(state, count, timeoutMs = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (state.analyticsRequests.length >= count) return lastAnalytics(state);
    await sleep(50);
  }
  throw new Error(`analytics request count ${state.analyticsRequests.length} < ${count}`);
}

async function clickAndWait(page, state, locator) {
  const before = state.analyticsRequests.length;
  await locator.evaluate((el) => el.click());
  const url = await waitForAnalytics(state, before + 1);
  await sleep(200);
  return url;
}

async function deepFlow(page, state) {
  const consoleErrors = state.consoleErrors;
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(`console: ${msg.text()}`);
  });

  await installRoutes(page, state);
  await page.goto(`${BASE}/v2/pages/spu-roi?shop_pk=7`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForSelector("#rows .tabulator-row", { timeout: 15000 });
  await waitForAnalytics(state, 1);

  assert((await page.locator("#rows .tabulator-row").count()) === 100, "default page should render 100 rows");
  assert((await page.locator("#rows .tabulator-col.tabulator-sortable").count()) === 9, "expected 9 sortable headers");
  assert(await page.locator("#rows .tabulator-row.row-bad").first().isVisible(), "loss row should be highlighted");
  const firstHtml = await page.locator("#rows .tabulator-row").first().innerHTML();
  for (const token of ["缺成本", "高退款", "≈"]) {
    assert(firstHtml.includes(token), `first row missing ${token}`);
  }

  // Product image lightbox button path.
  await page.locator("#rows [data-zoom]").first().click();
  await page.waitForSelector(".op-lightbox.is-open", { timeout: 3000 });
  await page.locator(".op-lightbox-close").click();
  await page.waitForFunction(() => !document.querySelector(".op-lightbox.is-open"));

  // Sort every sortable header once and assert server params.
  const sorts = [
    ["广告消耗", "spend"],
    ["广告系统实际ROI", "ad_system_actual_roi"],
    ["广告系统保本ROI", "ad_system_breakeven_roi"],
    ["有效销售", "effective_sales"],
    ["总单量", "total_orders"],
    ["有效单量", "effective_order_count"],
    ["取消率%", "cancel_rate"],
    ["全损率%", "full_loss_rate"],
    ["净利润", "net_profit"],
  ];
  for (const [title, field] of sorts) {
    const url = await clickAndWait(
      page,
      state,
      page.locator("#rows .tabulator-col", { hasText: title }).first(),
    );
    assert(url.searchParams.get("sort") === field, `sort ${title} did not request ${field}`);
    assert(["asc", "desc"].includes(url.searchParams.get("order")), `sort ${field} missing order`);
  }

  // Server error placeholder + retry link.
  state.failNextAnalytics = true;
  await clickAndWait(page, state, page.locator("#btn-refresh"));
  await page.waitForSelector("#retry-link", { timeout: 5000 });
  await clickAndWait(page, state, page.locator("#retry-link"));
  await page.waitForSelector("#rows .tabulator-row", { timeout: 5000 });

  // Visible date preset buttons. Some presets are intentionally hidden when
  // they duplicate another range (e.g. 30 days equals current month on 10/01).
  for (const preset of ["7", "30", "month", "all"]) {
    const button = page.locator(`[data-date-preset="${preset}"]`);
    if (!(await button.isVisible())) continue;
    const url = await clickAndWait(page, state, button);
    if (preset === "all") {
      assert(!url.searchParams.has("w_start") && !url.searchParams.has("w_end"), "all preset should clear dates");
    } else {
      assert(url.searchParams.has("w_start") && url.searchParams.has("w_end"), `${preset} preset should set dates`);
    }
  }

  // Invalid date shows error placeholder; retry recovers after field reset.
  const setDate = (selector, value) =>
    page.evaluate(
      ([sel, val]) => {
        const input = document.querySelector(sel);
        input.value = val;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
      [selector, value],
    );
  const beforeEndDate = state.analyticsRequests.length;
  await setDate("#filter-w-end", "2026-09-01");
  await waitForAnalytics(state, beforeEndDate + 1);
  await page.waitForSelector("#rows .tabulator-row", { timeout: 5000 });
  await setDate("#filter-w-start", "2026-10-01");
  await page.waitForSelector("#retry-link", { timeout: 5000 });
  const retryText = (await page.locator("#rows").textContent()) || "";
  assert(retryText.includes("起始日期不能晚于截止日期"), "invalid date error missing");
  await clickAndWait(page, state, page.locator("#retry-link"));

  // Fee input debounce, include-all checkbox, and refresh button.
  const beforeFee = state.analyticsRequests.length;
  await page.fill("#filter-fee", "12.5");
  await waitForAnalytics(state, beforeFee + 1, 2500);
  assert(lastAnalytics(state).searchParams.get("fee_rate") === "0.125", "fee_rate param mismatch");
  const includeUrl = await clickAndWait(page, state, page.locator("#filter-include-all"));
  assert(includeUrl.searchParams.get("include_all") === "true", "include_all param missing");
  const refreshUrl = await clickAndWait(page, state, page.locator("#btn-refresh"));
  assert(refreshUrl.searchParams.get("include_all") === "true", "refresh should preserve include_all");

  // SPU filter query/clear buttons via TomSelect instance attached to the select.
  await page.evaluate(() => {
    const ts = document.querySelector("#filter-spu-ids").tomselect;
    ["TEST_SPU_001", "TEST_SPU_002"].forEach((id) => {
      ts.addOption({ spu_id: id, title: id, status: "ACTIVE" });
      ts.addItem(id, true);
    });
    ts.refreshItems();
    ts.trigger("change", ts.getValue());
  });
  await page.waitForFunction(() => !document.querySelector("#btn-spu-apply").disabled);
  const spuUrl = await clickAndWait(page, state, page.locator("#btn-spu-apply"));
  assert(spuUrl.searchParams.get("spu_ids") === "TEST_SPU_001,TEST_SPU_002", "SPU apply did not send spu_ids");
  const clearUrl = await clickAndWait(page, state, page.locator("#btn-spu-clear"));
  assert(!clearUrl.searchParams.has("spu_ids"), "SPU clear should remove spu_ids");

  // Page-size selector and all pagination buttons. Normalize sort first so the
  // first row of page 1 is loss and page 2 is healthy for row-bad reuse checks.
  await clickAndWait(
    page,
    state,
    page.locator("#rows .tabulator-col", { hasText: "总单量" }).first(),
  );
  const beforeLimit = state.analyticsRequests.length;
  await page.selectOption("#filter-limit", "50");
  const limitUrl = await waitForAnalytics(state, beforeLimit + 1);
  assert(limitUrl.searchParams.get("limit") === "50", "limit=50 not requested");
  assert(limitUrl.searchParams.get("offset") === "0", "limit change should reset offset");
  const nextUrl = await clickAndWait(page, state, page.locator('button[data-page="next"]'));
  assert(nextUrl.searchParams.get("offset") === "50", "next should request offset=50");
  const page3Url = await clickAndWait(page, state, page.locator('button[data-page="3"]'));
  assert(page3Url.searchParams.get("offset") === "100", "page 3 should request offset=100");
  const prevUrl = await clickAndWait(page, state, page.locator('button[data-page="previous"]'));
  assert(prevUrl.searchParams.get("offset") === "50", "previous should request offset=50");
  // Row class must not stick when a recycled first row becomes healthy on page 2.
  const firstRowIsBad = await page
    .locator("#rows .tabulator-row")
    .first()
    .evaluate((el) => el.classList.contains("row-bad"));
  assert(!firstRowIsBad, "row-bad stuck on healthy row after paging");

  // Drill panel + every drill tab.
  await page.locator("#rows .tabulator-row").first().evaluate((el) => el.click());
  await page.waitForSelector(".op-drill-row", { timeout: 5000 });
  for (const [tab, expected] of [
    ["orders", "ORD-001"],
    ["settlements", "12.34"],
    ["cases", "CASE-1"],
    ["ads", "CAMP-1"],
    ["pnl", "利润构成"],
  ]) {
    await page.locator(`.op-drill-tab[data-tab="${tab}"]`).click();
    await page.waitForFunction(
      (text) => document.querySelector(".op-drill-row")?.innerText.includes(text),
      expected,
      { timeout: 5000 },
    );
  }
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelectorAll(".op-drill-row").length === 0);

  // Shop switcher: changing shop resets and reloads with new shop_pk.
  const shopBefore = state.analyticsRequests.length;
  await page.selectOption("#shop-switcher", "8");
  const shopUrl = await waitForAnalytics(state, shopBefore + 1);
  assert(shopUrl.searchParams.get("shop_pk") === "8", "shop switch should reload shop_pk=8");
  assert(asUrl(page.url()).searchParams.get("shop_pk") === "8", "shop switch should update URL");
  await page.screenshot({ path: PNG, fullPage: true });

  // Logout link posts logout and navigates away.
  await page.locator("#btn-logout").click();
  await page.waitForURL(/\/v2\/auth\/login/, { timeout: 5000 });
  assert(state.logoutCount === 1, "logout POST not called once");

  // Separate fresh context: no shop_pk and no localStorage preference opens modal.
  const modalContext = await page.context().browser().newContext({
    viewport: { width: 1200, height: 800 },
  });
  const modalPage = await modalContext.newPage();
  modalPage.on("pageerror", (e) => consoleErrors.push(`modal pageerror: ${e.message}`));
  modalPage.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(`modal console: ${msg.text()}`);
  });
  await installRoutes(modalPage, state);
  await modalPage.goto(`${BASE}/v2/pages/spu-roi`, { waitUntil: "domcontentloaded" });
  await modalPage.waitForSelector("#ops-shop-modal:not([hidden])", { timeout: 5000 });
  const beforeModalSelect = state.analyticsRequests.length;
  await modalPage.locator("#shop-modal-list button").first().click();
  const modalUrl = await waitForAnalytics(state, beforeModalSelect + 1);
  assert(modalUrl.searchParams.get("shop_pk") === "7", "modal shop select should load shop 7");
  await modalContext.close();

  const realErrors = consoleErrors.filter(
    (line) =>
      !/favicon|net::ERR_ABORTED|Failed to load resource: the server responded with a status of 500/.test(line),
  );
  assert(realErrors.length === 0, `browser errors:\n${realErrors.join("\n")}`);
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });
  const context = await browser.newContext({ viewport: { width: 1500, height: 980 } });
  const page = await context.newPage();
  const state = {
    analyticsRequests: [],
    drillRequests: [],
    optionRequests: [],
    consoleErrors: [],
    failNextAnalytics: false,
    logoutCount: 0,
  };
  try {
    await deepFlow(page, state);
    console.log(
      `DEEP-QA-OK analytics=${state.analyticsRequests.length} drill=${state.drillRequests.length} option=${state.optionRequests.length} screenshot=${PNG}`,
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error("DEEP-QA-FAIL", error && error.stack ? error.stack : error);
  process.exit(1);
});

#!/usr/bin/env node
/* Measure SPU ROI main-table header vs body column alignment (read-only).
 *
 *   BASE_URL=http://127.0.0.1:9877 NODE_PATH=/home/schan/pi-web/node_modules \
 *     node scripts/probe_spu_table_alignment.js
 *
 * All business/API calls are stubbed in-memory; nothing is written.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const REPO_ROOT = path.resolve(__dirname, "..");
const PAGES_DIR = process.env.PAGES_DIR || "/tmp/ui-audit/pages";
const STATIC_ROOT = path.join(REPO_ROOT, "tts_erp_v2", "static");
const BASE = process.env.BASE_URL || ""; // 本地静态服务（渲染产物），无需登录
const CHROME =
  process.env.PLAYWRIGHT_CHROMIUM ||
  "/home/schan/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell";
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

function startServer() {
  const server = http.createServer((req, res) => {
    const urlPath = req.url.split("?")[0];
    const pageName = urlPath.replace(/^\/v2\/pages\//, "").replace(/\/$/, "");
    let filePath = null;
    if (urlPath.startsWith("/v2/pages/") && fs.existsSync(path.join(PAGES_DIR, pageName + ".html"))) {
      filePath = path.join(PAGES_DIR, pageName + ".html");
    } else if (urlPath.startsWith("/static/")) {
      filePath = path.join(STATIC_ROOT, urlPath.slice("/static".length));
    }
    if (!filePath) {
      res.writeHead(404);
      res.end();
      return;
    }
    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end();
        return;
      }
      res.writeHead(200, { "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream" });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}
const PNG = process.env.SCREENSHOT || "/tmp/spu-table-alignment.png";
const DATA_IMG =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeItem(i) {
  return {
    spu_pk: 1000 + i,
    spu_id: `TEST_SPU_${String(i).padStart(3, "0")}`,
    title: `对齐探针商品 ${i}`,
    status: "ACTIVE",
    main_image_url: DATA_IMG,
    uses_default_unit_cost: false,
    refund_rate_alert: false,
    has_unsettled_orders: false,
    profit_status: "profit",
    spend: String(20 + i),
    ad_system_actual_roi: "1.70",
    ad_system_breakeven_roi: "3.01",
    ad_system_breakeven_roi_status: "exact",
    effective_sales: String(1000 + i),
    total_orders: 10,
    effective_order_count: 8,
    cancel_rate: "0.010",
    full_loss_rate: "0.005",
    net_profit: "30.00",
    roi_real: "1.80",
    roi_breakeven: "1.10",
  };
}
const ITEMS = Array.from({ length: 30 }, (_, idx) => makeItem(idx + 1));

async function installRoutes(page) {
  const json = (route, obj, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(obj) });
  await page.route("**/v2/auth/me**", (r) => json(r, { authenticated: true, role: "admin" }));
  await page.route("**/v2/auth/logout**", (r) => json(r, { ok: true }));
  await page.route("**/v2/auth/login**", (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: "<h1>login</h1>" }),
  );
  await page.route("**/v2/config/enum-map**", (r) => json(r, { order_status: {}, case_type: {}, case_status: {} }));
  await page.route("**/v2/commerce/channel-accounts**", (r) =>
    json(r, [{ id: 7, account_name: "QA 店 7", region: "VN" }]),
  );
  await page.route("**/v2/commerce/channel-product-options**", (r) =>
    json(r, ITEMS.slice(0, 20).map((i) => ({ spu_id: i.spu_id, title: i.title, status: i.status }))),
  );
  await page.route("**/v2/analytics/spu-roi?**", (r) =>
    json(r, {
      items: ITEMS,
      total: ITEMS.length,
      totals: {
        total_orders: 999, spend: "1234.56", effective_order_count: 888,
        effective_sales: "54321.10", refund_order_count: 12, refund_rate: "0.034",
        full_loss_order_count: 7, full_loss_rate: "0.019", domestic_cancelled_order_count: 9,
        cancel_rate: "0.024", net_profit: "4567.89", profit_status: "profit",
        roi_real: "1.23", roi_breakeven: "1.08", ad_system_actual_roi: "2.34",
        ad_system_breakeven_roi: "1.56", ad_system_breakeven_roi_status: "exact",
        projection_status: "projectable", projection_terminal_basis_order_count: 50,
        projection_terminal_full_loss_order_count: 3, projection_refund_amount_rate: "0.012",
        pre_delivery_full_loss_rate: "0.060", unsettled_order_count: 14,
        projected_future_full_loss_qty: "4.5", projected_net_revenue: "1111.11",
        projected_net_profit: "222.22", projected_roi_real: "1.40",
        projected_roi_breakeven: "1.05", projected_ad_system_actual_roi: "2.50",
        projected_ad_system_breakeven_roi: "1.60",
      },
      meta: {
        currency: { display: "CNY" },
        computed_at: "2026-10-01T12:00:00+00:00",
        rubric_version: "v10",
        reporting_timezone: "Asia/Ho_Chi_Minh",
        window: { first_day: "2026-09-01", last_day: "2026-09-30" },
        fee: { rate: "0.308", source: "baseline", sample_order_count: 20, coverage_rate: "0.80", as_of: "2026-10-01" },
        presentation: {
          rubric_label: "盈利 v10",
          default_unit_cost_message: "缺少成本，使用默认成本",
          refund_rate_alert_message: "退款率偏高",
          unsettled_alert_message: "含未结算估算",
        },
      },
    }),
  );
}

async function main() {
  const server = await startServer();
  const origin = BASE || `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage({ viewport: { width: 2560, height: 1271 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`));

  await installRoutes(page);
  await page.goto(`${origin}/v2/pages/spu-roi?shop_pk=7`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });
  await sleep(600);

  const report = await page.evaluate(() => {
    const host = document.getElementById("rows");
    const rect = (el) => {
      const r = el.getBoundingClientRect();
      return { l: +r.left.toFixed(1), r: +r.right.toFixed(1), w: +r.width.toFixed(1), t: +r.top.toFixed(1), h: +r.height.toFixed(1) };
    };
    const sections = {};
    for (const sel of ["main.op-main", "#summaries", "#summaries .row", "#toolbar", "#selection-slot", ".op-table-wrap", "#rows", ".op-pager", ".op-pager .d-flex"]) {
      const el = document.querySelector(sel);
      if (el) sections[sel] = { rect: rect(el), pad: getComputedStyle(el).paddingLeft + "/" + getComputedStyle(el).paddingRight, cls: el.className };
    }
    const headRow = host.querySelector(".tabulator-header .tabulator-header-contents .tabulator-headers");
    const firstBodyRow = host.querySelector(".tabulator-tableholder .tabulator-row");
    const out = {
      sections,
      host: rect(host),
      hostOverflow: host.scrollWidth - host.clientWidth,
      headerRow: headRow ? rect(headRow) : null,
      bodyRow: firstBodyRow ? rect(firstBodyRow) : null,
      headerHolder: host.querySelector(".tabulator-tableholder") ? rect(host.querySelector(".tabulator-tableholder")) : null,
      cols: [],
    };
    const hcols = [...host.querySelectorAll(".tabulator-header .tabulator-col")];
    const bcells = firstBodyRow ? [...firstBodyRow.querySelectorAll(".tabulator-cell")] : [];
    hcols.forEach((hc, i) => {
      const bc = bcells[i];
      out.cols.push({
        title: (hc.querySelector(".tabulator-col-title") || {}).textContent || "",
        header: rect(hc),
        body: bc ? rect(bc) : null,
        delta: bc ? +(rect(bc).l - rect(hc).l).toFixed(1) : null,
        deltaR: bc ? +(rect(bc).r - rect(hc).r).toFixed(1) : null,
        frozen: hc.classList.contains("tabulator-frozen"),
        hStyle: hc.getAttribute("style"),
        bStyle: bc ? bc.getAttribute("style") : null,
      });
    });
    out.headerScroll = (() => {
      const h = host.querySelector(".tabulator-header-contents");
      return h ? { rect: rect(h), scrollLeft: h.scrollLeft } : null;
    })();
    return out;
  });

  console.log(JSON.stringify(report, null, 2));
  if (errors.length) console.log("PAGE ERRORS:", errors.slice(0, 10));

  await page.screenshot({ path: PNG, fullPage: false });
  console.log("screenshot:", PNG);
  await browser.close();
  server.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

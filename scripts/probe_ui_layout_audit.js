#!/usr/bin/env node
/* UI 布局巡检探针：真实渲染的页面 + mock 业务接口 + 截图 + DOM 度量。
 *
 * 用法（先渲染页面，再巡检）：
 *   .venv/bin/python scripts/probe_ui_layout_pages.py --out /tmp/ui-audit/pages
 *   NODE_PATH=/home/schan/pi-web/node_modules \
 *     node scripts/probe_ui_layout_audit.js
 *
 * 可选环境变量：
 *   PAGES_DIR   渲染产物目录（默认 /tmp/ui-audit/pages）
 *   SHOTS_DIR   截图输出目录（默认 /tmp/ui-audit/shots）
 *   WIDTHS      逗号分隔视口宽度（默认 "2560,1440"）
 *   OUT_JSON    度量结果 JSON（默认 $SHOTS_DIR/../report.json）
 *   CHROME      Chromium 可执行文件路径
 *
 * 度量三类布局缺陷（自动规避两类已知误报）：
 *   1. 横向溢出：documentElement.scrollWidth > clientWidth；
 *   2. 填充失配：表格容器与内容宽度差 > 80px（fitData 类问题），
 *      空表（innerW=0）不计——那是无数据占位，不是失配；
 *   3. 溢出元素：右边界超出视口且未被 overflow 祖先裁剪的最外层元素
 *      （Ace 编辑器内部 100 万 px 文本层会被 .ace_scroller 裁剪，天然排除）。
 *
 * 发现缺陷时退出码 1（可用于回归拦截）；干净退出 0。
 * 全部接口 mock 为内存响应，不发起任何写操作。
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const REPO_ROOT = path.resolve(__dirname, "..");
const PAGES_DIR = process.env.PAGES_DIR || "/tmp/ui-audit/pages";
const SHOTS_DIR = process.env.SHOTS_DIR || "/tmp/ui-audit/shots";
const WIDTHS = (process.env.WIDTHS || "2560,1440")
  .split(",")
  .map((w) => Number(w.trim()))
  .filter((w) => w > 0);
const OUT_JSON =
  process.env.OUT_JSON || path.join(path.dirname(SHOTS_DIR), "report.json");
const CHROME =
  process.env.PLAYWRIGHT_CHROMIUM ||
  process.env.CHROME ||
  "/home/schan/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome";
const STATIC_ROOT = path.join(REPO_ROOT, "tts_erp_v2", "static");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

const server = http.createServer((req, res) => {
  const urlPath = req.url.split("?")[0];
  const pageName = urlPath.replace(/^\/v2\/pages\//, "").replace(/\/$/, "");
  let filePath = null;
  if (
    urlPath.startsWith("/v2/pages/") &&
    fs.existsSync(path.join(PAGES_DIR, pageName + ".html"))
  ) {
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
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream",
    });
    res.end(data);
  });
});

// ---------- 业务接口 mock（只读载荷，字段与线上响应契约对齐） ----------
const IMG =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const NOW = "2026-10-02T12:00:00+00:00";

function mockJson(url) {
  const u = url.replace(/\?.*$/, "");
  const ok = (obj) => JSON.stringify(obj);

  if (u.endsWith("/v2/auth/me"))
    return ok({ authenticated: true, role: "admin" });

  if (u.endsWith("/v2/sync/status"))
    return ok({
      server_time: NOW,
      jobs: [
        {
          name: "tiktok_orders",
          status: "ok",
          last_run_at: NOW,
          last_success_at: NOW,
          last_error: null,
          interval_seconds: 600,
        },
        {
          name: "ad_daily",
          status: "error",
          last_run_at: NOW,
          last_success_at: NOW,
          last_error: "boom " + "x".repeat(60),
          interval_seconds: 1800,
        },
      ],
      total_spus: 1234,
      missing_cost_spus: 89,
      shop_count: 12,
      auth_mode: "enforce",
    });
  if (u.endsWith("/v2/reporting/coverage"))
    return ok({
      costed_spus: 1145,
      linked_spus: 1200,
      total_spus: 1234,
      coverage_rate: "0.928",
      as_of: "2026-10-01",
    });
  if (u.endsWith("/v2/commerce/channel-accounts"))
    return ok([
      { id: 7, account_name: "QA 店 7", region: "VN" },
      {
        id: 8,
        account_name: "QA 店 8（很长的店铺名称测试截断行为）",
        region: "VN",
      },
    ]);
  if (u.endsWith("/v2/commerce/channel-product-options"))
    return ok({ options: [] });

  if (u.endsWith("/v2/reporting/manual-costs"))
    return ok({
      total: 2,
      items: [
        {
          spu_pk: 1,
          spu_id: "TEST_SPU_001",
          title: "Áo Thun Nam Ngân Tây Mùa Hè 2024 Bản Cao Cấp",
          image_url: IMG,
          unit_cost: "12.34",
          currency: "CNY",
          updated_at: NOW,
        },
        {
          spu_pk: 2,
          spu_id: "TEST_SPU_002",
          title: "Áo Ba Lỗ Nam Dệt Kim Mùa Hè Siêu Thoáng Mát",
          image_url: null,
          unit_cost: "999999.99",
          currency: "CNY",
          updated_at: NOW,
        },
      ],
      shops: [{ id: 7, account_name: "QA 店 7" }],
    });
  if (u.endsWith("/v2/commerce/channel-products"))
    return ok({ items: [], total: 0 });

  if (u.endsWith("/v2/config/enum-map"))
    return ok({
      order_status: { COMPLETED: "已完成" },
      case_type: { REFUND_ONLY: "仅退款" },
      case_status: { RETURN_OR_REFUND_REQUEST_COMPLETE: "已完结" },
    });
  if (u.endsWith("/v2/config/enum-map/list"))
    return ok({
      items: [
        {
          id: 1,
          enum_type: "order_status",
          source_value: "COMPLETED",
          display_value: "已完成",
          updated_at: NOW,
        },
        {
          id: 2,
          enum_type: "case_type",
          source_value: "REFUND_ONLY",
          display_value: "仅退款",
          updated_at: NOW,
        },
      ],
      total: 2,
    });

  if (u.endsWith("/v2/config/runtime"))
    return ok({
      items: [
        {
          key: "fee_rate_baseline",
          value: "0.308",
          scope: "global",
          updated_at: NOW,
          description: "基线佣金费率",
        },
        {
          key: "sync_interval_orders",
          value: "600",
          scope: "global",
          updated_at: NOW,
          description: "订单同步间隔（秒）",
        },
      ],
      total: 2,
    });

  if (u.endsWith("/v2/sync/jobs"))
    return ok({
      server_time: NOW,
      tiktok_shops: [{ id: 7, account_name: "QA 店 7" }],
      jobs: [
        {
          name: "tiktok_orders",
          display_name: "TikTok 订单同步",
          status: "ok",
          enabled: true,
          interval_seconds: 600,
          last_run_at: NOW,
          last_success_at: NOW,
          last_duration_ms: 1234,
          last_error: null,
        },
        {
          name: "ad_daily",
          display_name: "广告日数据同步",
          status: "error",
          enabled: true,
          interval_seconds: 1800,
          last_run_at: NOW,
          last_success_at: NOW,
          last_duration_ms: 98765,
          last_error: "HTTP 500 from upstream " + "y".repeat(80),
        },
      ],
    });

  if (u.includes("/v2/intercept/configs"))
    return ok({
      configs: [
        {
          id: 1,
          name: "拦截规则一（名称很长测试截断）",
          enabled: true,
          priority: 10,
          match_field: "url",
          match_op: "contains",
          match_value: "/api/orders",
          action: "block",
          note: "测试备注",
          updated_at: NOW,
        },
        {
          id: 2,
          name: "rule-2",
          enabled: false,
          priority: 20,
          match_field: "body",
          match_op: "equals",
          match_value: "x".repeat(80),
          action: "log",
          note: "",
          updated_at: NOW,
        },
      ],
      total: 2,
    });
  if (u.endsWith("/v2/intercept/requests/stats"))
    return ok({
      buckets: [
        { day: "2026-10-01", total: 120, blocked: 90, logged: 30 },
        { day: "2026-09-30", total: 80, blocked: 10, logged: 70 },
      ],
      totals: { total: 200, blocked: 100, logged: 100 },
    });
  if (u.endsWith("/v2/intercept/requests"))
    return ok({
      requests: [
        {
          id: 1,
          ts: NOW,
          method: "POST",
          url: "https://example.com/api/orders/create",
          status: 200,
          action: "block",
          rule_name: "拦截规则一",
          latency_ms: 12,
        },
        {
          id: 2,
          ts: NOW,
          method: "GET",
          url: "https://example.com/" + "p/".repeat(40),
          status: 500,
          action: "log",
          rule_name: "rule-2",
          latency_ms: 999,
        },
      ],
      total: 2,
    });

  if (u.endsWith("/v2/admin/shops/unregistered"))
    return ok({
      items: [{ shop_id: "S1", shop_name: "未注册店", region: "VN" }],
      total: 1,
    });
  if (/\/v2\/admin\/shops\/[^/]+$/.test(u))
    return ok({
      shop: {
        id: 3,
        shop_id: "S3",
        shop_name: "已注册店",
        credential_id: 9,
        status: "registered",
        region: "VN",
      },
    });

  if (u.endsWith("/v2/reporting/ad-daily"))
    return ok({
      total: 2,
      summary: { spend: "1234.56", gmv: "9876.54", orders: 88 },
      items: [
        {
          stat_date: "2026-10-01",
          shop_id: 7,
          shop_name: "QA 店 7",
          campaign_id: "C1",
          campaign_name: "Campaign One",
          spend: "100.00",
          gmv: "800.00",
          orders: 10,
          ctr: "0.031",
          cvr: "0.102",
          cpc: "0.55",
        },
        {
          stat_date: "2026-09-30",
          shop_id: 8,
          shop_name: "QA 店 8",
          campaign_id: "C2",
          campaign_name: "C2 " + "很长的活动名称".repeat(4),
          spend: "222.22",
          gmv: "1999.99",
          orders: 22,
          ctr: "0.012",
          cvr: "0.088",
          cpc: "0.77",
        },
      ],
    });
  if (u.endsWith("/v2/reporting/ad-daily/options"))
    return ok({
      shops: [{ id: 7, account_name: "QA 店 7" }],
      campaigns: [{ id: "C1", name: "Campaign One" }],
    });

  if (u.includes("/v2/reporting/focused-spus"))
    return ok({
      items: [],
      total: 0,
      totals: {},
      meta: {
        currency: { display: "CNY" },
        window: { first_day: "2026-09-01", last_day: "2026-09-30" },
        presentation: {},
      },
    });

  // spu-roi 主表：30 行覆盖格式化/告警 chip/亏损标红路径。
  if (u.endsWith("/v2/analytics/spu-roi"))
    return ok({
      items: Array.from({ length: 30 }, (_, i) => ({
        spu_pk: 1000 + i,
        spu_id: `TEST_SPU_${String(i).padStart(3, "0")}`,
        title: `Áo Thun Nam Ngân Tây Mùa Hè 2024 Bản Cao Cấp ${i}`,
        status: "ACTIVATE",
        main_image_url: i < 3 ? IMG : null,
        uses_default_unit_cost: i === 1,
        refund_rate_alert: i === 2,
        has_unsettled_orders: i === 3,
        profit_status: i % 2 ? "loss" : "profit",
        spend: (20 + i).toFixed(2),
        ad_system_actual_roi: "1.23",
        ad_system_breakeven_roi: "1.56",
        ad_system_breakeven_roi_status: "estimated_known_costs",
        effective_sales: "1010.00",
        total_orders: 11,
        effective_order_count: 9,
        cancel_rate: "0.010",
        full_loss_rate: "0.005",
        net_profit: (i % 2 ? "-10." : "30.") + i,
        roi_real: "1.80",
      })),
      total: 30,
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
      },
      meta: {
        currency: { display: "CNY" },
        computed_at: NOW,
        rubric_version: "v10",
        reporting_timezone: "Asia/Ho_Chi_Minh",
        window: { first_day: "2026-09-01", last_day: "2026-09-30" },
        fee: {
          rate: "0.308",
          source: "baseline",
          sample_order_count: 20,
          coverage_rate: "0.80",
          as_of: "2026-10-01",
        },
        presentation: {
          rubric_label: "盈利 v10",
          default_unit_cost_message: "缺少成本",
          refund_rate_alert_message: "退款率偏高",
          unsettled_alert_message: "含未结算估算",
        },
      },
    });

  return ok({ items: [], total: 0 });
}

// ---------- DOM 度量（裁剪感知，规避空表/编辑器内部层误报） ----------
function measure() {
  const out = {
    overflowX: false,
    scrollW: 0,
    clientW: 0,
    offenders: [],
    fillGaps: [],
  };
  out.scrollW = document.documentElement.scrollWidth;
  out.clientW = document.documentElement.clientWidth;
  out.overflowX = out.scrollW > out.clientW + 1;

  // 1) 横向溢出元素：未被 overflow 祖先裁剪的最外层越界元素。
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right <= out.clientW + 2 || r.left >= out.clientW) continue;
    if (getComputedStyle(el).position === "fixed") continue;
    let p = el.parentElement;
    let clipped = false;
    let nested = false;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (/(hidden|auto|scroll|clip)/.test(cs.overflowX)) {
        clipped = true;
        break;
      }
      if (p.getBoundingClientRect().right > out.clientW + 2) {
        nested = true;
        break;
      }
      p = p.parentElement;
    }
    if (clipped || nested) continue;
    out.offenders.push({
      sel:
        el.tagName.toLowerCase() +
        "." +
        String(el.className).split(" ").slice(0, 3).join("."),
      w: Math.round(r.width),
      right: Math.round(r.right),
    });
  }

  // 2) 填充失配：表格容器与内容宽度差 > 80px；空表（内容宽 0）不计。
  for (const holder of document.querySelectorAll(
    ".tabulator-tableholder, .table-wrap, .op-table-wrap, .table-responsive",
  )) {
    const hr = holder.getBoundingClientRect();
    if (hr.width < 200) continue;
    const inner = holder.querySelector("table, .tabulator-table");
    if (!inner) continue;
    const ir = inner.getBoundingClientRect();
    if (ir.width < 20) continue; // 空表占位，不是失配
    const gap = Math.round(hr.width - ir.width);
    if (gap > 80)
      out.fillGaps.push({
        holder: String(holder.className).slice(0, 40),
        holderW: Math.round(hr.width),
        innerW: Math.round(ir.width),
        gap,
      });
  }
  return out;
}

const PAGES = [
  "dashboard",
  "shops",
  "enum-map",
  "runtime-configs",
  "sync-jobs",
  "manual-costs",
  "intercept-configs",
  "intercept-requests",
  "intercept-stats",
  "ad-daily",
  "spu-roi",
  "focused-spus",
];

(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  const browser = await chromium.launch({ executablePath: CHROME });
  fs.mkdirSync(SHOTS_DIR, { recursive: true });
  const report = {};
  let hits = 0;

  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 1280 } });
    await page.route("**/v2/**", (route) => {
      if (route.request().url().includes("/v2/pages/")) return route.fallback();
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: mockJson(route.request().url()),
      });
    });
    for (const name of PAGES) {
      // spu-roi/focused-spus 带 shop_pk 才会拉数据渲染主表。
      const url =
        name === "spu-roi" || name === "focused-spus"
          ? `http://127.0.0.1:${port}/v2/pages/${name}?shop_pk=7`
          : `http://127.0.0.1:${port}/v2/pages/${name}`;
      try {
        await page.goto(url, { waitUntil: "networkidle" });
        await page.waitForTimeout(900);
        const m = await page.evaluate(measure);
        report[`${name}@${width}`] = m;
        await page.screenshot({
          path: `${SHOTS_DIR}/${name}-${width}.png`,
          fullPage: true,
        });
        const bad =
          m.overflowX || m.fillGaps.length > 0 || m.offenders.length > 0;
        if (bad) hits += 1;
        console.log(
          bad ? "HIT" : "ok ",
          `${name}@${width}`,
          JSON.stringify({
            overflowX: m.overflowX,
            sw: m.scrollW,
            cw: m.clientW,
            off: m.offenders,
            gaps: m.fillGaps,
          }),
        );
      } catch (e) {
        hits += 1;
        report[`${name}@${width}`] = { error: String(e).slice(0, 200) };
        console.log("ERR", `${name}@${width}`, String(e).slice(0, 200));
      }
    }
    await page.close();
  }
  fs.mkdirSync(path.dirname(OUT_JSON), { recursive: true });
  fs.writeFileSync(OUT_JSON, JSON.stringify(report, null, 2));
  console.log("report:", OUT_JSON);
  await browser.close();
  server.close();
  process.exit(hits > 0 ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

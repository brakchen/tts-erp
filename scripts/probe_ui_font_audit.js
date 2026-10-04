#!/usr/bin/env node
/* 字体/字号巡检探针：跨页面对比「实际命中的字体族 + 计算字号分布」。
 *
 * 用法（先渲染页面，再巡检）：
 *   .venv/bin/python scripts/probe_ui_layout_pages.py --out /tmp/ui-audit/pages
 *   NODE_PATH=/home/schan/pi-web/node_modules \
 *     node scripts/probe_ui_font_audit.js
 *
 * 可选环境变量：
 *   PAGES_DIR  渲染产物目录（默认 /tmp/ui-audit/pages）
 *   OUT_JSON   报告输出（默认 /tmp/ui-audit/font-report.json）
 *   CHROME     Chromium 可执行文件路径
 *
 * 三类证据：
 *   1. platformFonts —— CDP CSS.getPlatformFontsForNode：浏览器**实际**用哪个字体族
 *      渲染这段文字（fontconfig 替换后的真实结果）；
 *   2. computed     —— 页面里所有含文字元素的 computed font-size / font-family 分布；
 *   3. cssRules     —— 各样式表里显式写死的 font-family / font-size 声明（含未渲染态）。
 *
 * 只读：业务接口全部 mock，不发起任何写操作。
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const REPO_ROOT = path.resolve(__dirname, "..");
const PAGES_DIR = process.env.PAGES_DIR || "/tmp/ui-audit/pages";
const OUT_JSON = process.env.OUT_JSON || "/tmp/ui-audit/font-report.json";
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

const NOW = "2026-10-02T12:00:00+00:00";

/** ad-daily 是本次巡检重点：按真实契约返回，让明细表真正渲染出文字。 */
function adDailyMock(url) {
  const u = url.replace(/\?.*$/, "");
  const sellers = [
    { seller_id: "749486486860415", shop_name: "North Nook", row_count: 4038 },
    { seller_id: "749486486860914", shop_name: "QA 店 8（很长的店铺名称测试截断行为）", row_count: 4038 },
  ];
  if (u.endsWith("/v2/reporting/ad-daily/options"))
    return JSON.stringify({
      sellers,
      advertisers: sellers.map((s, i) => ({
        seller_id: s.seller_id,
        advertiser_id: `76793575728722247${i}6`,
        row_count: s.row_count,
      })),
      endpoints: [
        { endpoint: "/open_api/ads/manager/report/integrated", row_count: 5000 },
        { endpoint: "/open_api/ads/manager/report/daily", row_count: 3076 },
      ],
      min_day: "2026-07-01",
      max_day: "2026-10-03",
    });
  if (u.endsWith("/v2/reporting/ad-daily")) {
    const items = Array.from({ length: 12 }, (_, i) => ({
      id: 9000 + i,
      seller_id: sellers[i % 2].seller_id,
      shop_pk: 7,
      shop_name: sellers[i % 2].shop_name,
      advertiser_id: `767935757287222470${i % 2}6`,
      campaign_id: "1877052518779186",
      product_id: `1737532${String(867998766722 + i)}`.slice(0, 19),
      product_title:
        i % 2
          ? "Áo sơ mi nam ngắn tay màu xám, họa tiết nhỏ thanh lịch"
          : "Áo polo nam ngắn tay màu đỏ rượu vang cao cấp",
      endpoint: "/open_api/ads/manager/report/integrated",
      day: `2026-10-0${(i % 3) + 1}`,
      mixed_real_cost: (0.01 + i * 0.03).toFixed(2),
      onsite_roi2_shopping_sku: i % 4,
      onsite_roi2_shopping_value: (i * 12.5).toFixed(2),
      onsite_mixed_real_roi2_shopping: i % 5 === 0 ? null : (i * 0.42).toFixed(2),
      metrics_extra: { clicks: 12 + i, impressions: 900 + i },
      created_at: NOW,
      updated_at: "2026-10-04T01:29:00+00:00",
    }));
    return JSON.stringify({
      items,
      total: 8076,
      limit: 50,
      offset: 0,
      summary: {
        row_count: 8076,
        spend: "6674.41",
        attributed_orders: 881,
        attributed_gmv: "21444.63",
        weighted_roi: "3.213",
        currency: "USD",
      },
    });
  }
  return null;
}

/** 其余接口给最小可用载荷：页面结构是字体采样的主体，表格由 ad-daily 专门覆盖。 */
function mockJson(url) {
  const specific = adDailyMock(url);
  if (specific) return specific;
  const u = url.replace(/\?.*$/, "");
  if (u.endsWith("/v2/auth/me"))
    return JSON.stringify({ authenticated: true, role: "admin" });
  if (u.endsWith("/v2/admin/shops"))
    return JSON.stringify({ items: [], total: 0 });
  if (u.endsWith("/v2/sync/status"))
    return JSON.stringify({
      server_time: NOW,
      jobs: [],
      total_spus: 1234,
      missing_cost_spus: 89,
      shop_count: 12,
      auth_mode: "enforce",
    });
  if (u.endsWith("/v2/reporting/coverage"))
    return JSON.stringify({
      costed_spus: 1145,
      linked_spus: 1200,
      total_spus: 1234,
      coverage_rate: "0.928",
      as_of: "2026-10-01",
    });
  if (u.endsWith("/v2/commerce/channel-accounts"))
    return JSON.stringify([
      { id: 7, account_name: "QA 店 7", region: "VN" },
      { id: 8, account_name: "QA 店 8（很长的店铺名称测试截断行为）", region: "VN" },
    ]);
  if (u.endsWith("/v2/reporting/manual-costs"))
    return JSON.stringify({
      total: 2,
      items: [
        {
          spu_pk: 1,
          spu_id: "TEST_SPU_001",
          title: "Áo Thun Nam Ngân Tây Mùa Hè 2024 Bản Cao Cấp",
          image_url: null,
          unit_cost: "12.34",
          currency: "CNY",
          updated_at: NOW,
        },
      ],
      shops: [{ id: 7, account_name: "QA 店 7" }],
    });
  if (u.endsWith("/v2/sync/jobs"))
    return JSON.stringify({ server_time: NOW, tiktok_shops: [], jobs: [] });
  return JSON.stringify({ items: [], total: 0, options: [], configs: [] });
}

/** 收集页面里含文字元素的 computed 字号/字族分布。 */
function collectComputed() {
  const sizes = {};
  const stacks = {};
  const weights = {};
  const samples = {};
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let node = walk.currentNode;
  while (node) {
    const own = Array.from(node.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent)
      .join("")
      .trim();
    if (own) {
      const cs = getComputedStyle(node);
      if (cs.display !== "none" && cs.visibility !== "hidden") {
        sizes[cs.fontSize] = (sizes[cs.fontSize] || 0) + 1;
        stacks[cs.fontFamily] = (stacks[cs.fontFamily] || 0) + 1;
        weights[cs.fontWeight] = (weights[cs.fontWeight] || 0) + 1;
        const key = `${cs.fontSize}|${cs.fontFamily}`;
        if (!samples[key])
          samples[key] = {
            sel:
              node.tagName.toLowerCase() +
              (node.className && typeof node.className === "string"
                ? "." + node.className.trim().split(/\s+/).slice(0, 2).join(".")
                : ""),
            sample: own.slice(0, 40),
          };
      }
    }
    node = walk.nextNode();
  }
  return { sizes, stacks, weights, samples };
}

/** 收集样式表里显式声明的 font-family / font-size（含未渲染态）。 */
function collectCssRules() {
  const families = {};
  const sizes = {};
  const at = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try {
      rules = sheet.cssRules;
    } catch (e) {
      at.push(`(blocked) ${sheet.href || "inline"}`);
      continue;
    }
    if (!rules) continue;
    const visit = (list, prefix) => {
      for (const rule of Array.from(list)) {
        if (rule.cssRules) {
          visit(rule.cssRules, `${prefix}${rule.conditionText || rule.name || ""} > `);
          continue;
        }
        if (!rule.style) continue;
        const ff = rule.style.getPropertyValue("font-family");
        if (ff)
          families[ff.trim()] = (families[ff.trim()] || 0) + 1;
        const fsz = rule.style.getPropertyValue("font-size");
        if (fsz)
          sizes[`${prefix}${fsz.trim()}`] = (sizes[`${prefix}${fsz.trim()}`] || 0) + 1;
      }
    };
    visit(rules, "");
  }
  return { families, sizes, blocked: at };
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

async function platformFonts(page, cap) {
  const client = await page.context().newCDPSession(page);
  await client.send("DOM.enable");
  await client.send("CSS.enable");
  const { root } = await client.send("DOM.getDocument", { depth: -1 });
  const { nodeIds } = await client.send("DOM.querySelectorAll", {
    nodeId: root.nodeId,
    selector: "body *",
  });
  const acc = {};
  let seen = 0;
  for (const nodeId of nodeIds) {
    if (seen >= cap) break;
    try {
      const res = await client.send("CSS.getPlatformFontsForNode", { nodeId });
      if (!res.fonts || res.fonts.length === 0) continue;
      seen += 1;
      for (const f of res.fonts) {
        acc[f.familyName] = (acc[f.familyName] || 0) + (f.glyphCount || 0);
      }
    } catch (e) {
      /* 节点已失效，忽略 */
    }
  }
  await client.detach();
  return acc;
}

(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 2560, height: 1280 } });
  await page.route("**/v2/**", (route) => {
    if (route.request().url().includes("/v2/pages/")) return route.fallback();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: mockJson(route.request().url()),
    });
  });

  const report = {};
  for (const name of PAGES) {
    const url =
      name === "spu-roi" || name === "focused-spus"
        ? `http://127.0.0.1:${port}/v2/pages/${name}?shop_pk=7`
        : `http://127.0.0.1:${port}/v2/pages/${name}`;
    try {
      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(700);
      const computed = await page.evaluate(collectComputed);
      const css = await page.evaluate(collectCssRules);
      const fonts = await platformFonts(page, 80);
      report[name] = { computed, css, platformFonts: fonts };
      console.log("ok ", name, JSON.stringify(fonts));
    } catch (e) {
      report[name] = { error: String(e).slice(0, 300) };
      console.log("ERR", name, String(e).slice(0, 300));
    }
  }

  fs.mkdirSync(path.dirname(OUT_JSON), { recursive: true });
  fs.writeFileSync(OUT_JSON, JSON.stringify(report, null, 2));
  console.log("report:", OUT_JSON);
  await browser.close();
  server.close();
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

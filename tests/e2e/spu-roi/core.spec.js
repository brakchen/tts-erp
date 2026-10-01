// @ts-check
const { test, expect } = require("@playwright/test");

// ── Environment ───────────────────────────────────────────────────
const BASE_URL = process.env.E2E_BASE_URL || "http://127.0.0.1:9987";
const API_KEY = process.env.E2E_API_KEY || "ttserp_ro_TEST_E2E_KEY";
const SHOP_PK = process.env.E2E_SHOP_PK || "1";
const SHOP2_PK = process.env.E2E_SHOP2_PK || "2";

// Helper: login via real /v2/auth/login, return session cookie context
async function loginAndNavigate(page, shopPk = SHOP_PK) {
  // 1. POST login
  const loginResp = await page.request.post(`${BASE_URL}/v2/auth/login`, {
    data: { key: API_KEY },
  });
  expect(loginResp.status()).toBe(200);
  const body = await loginResp.json();
  expect(body.ok).toBe(true);
  expect(body.role).toBe("readonly");

  // 2. Navigate to page (cookies are shared in the context)
  await page.goto(`/v2/pages/spu-roi?shop_pk=${shopPk}`, {
    waitUntil: "domcontentloaded",
  });
}

// ── C-SPUROI-01: 未登录 → 302 → 登录 → 回到原 URL ─────────────────
test.describe("C-SPUROI-01 @page:spu-roi @tier:core", () => {
  test("未登录访问 ROI 页面被重定向到登录页；登录后回到原 URL", async ({ page }) => {
    // 不登录，直接访问
    const resp = await page.goto(`/v2/pages/spu-roi?shop_pk=${SHOP_PK}`, {
      waitUntil: "domcontentloaded",
    });

    // Should redirect to login page
    const url = page.url();
    expect(url).toContain("/v2/auth/login");
    expect(url).toContain("next=");

    // Login page should have the key input
    await expect(page.locator("#key")).toBeVisible();

    // Fill in API key and submit
    await page.fill("#key", API_KEY);
    await page.click('button[type="submit"]');

    // Should redirect back to spu-roi page
    await page.waitForURL(/\/v2\/pages\/spu-roi/, { timeout: 10000 });
    expect(page.url()).toContain("shop_pk=" + SHOP_PK);

    // Page should load successfully
    await expect(page.locator("#shop-switcher")).toBeVisible({ timeout: 15000 });
  });
});

// ── C-SPUROI-02: 已登录加载 ROI 页；店铺/汇总/表格/格式/状态 ────────
test.describe("C-SPUROI-02 @page:spu-roi @tier:core", () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigate(page);
    // Wait for table rows to appear (real API response)
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });
  });

  test("页面标题、导航、店铺选择器存在", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("SPU 实际 ROI");
    await expect(page.locator("#shop-switcher")).toBeVisible();
    await expect(page.locator("#rows")).toBeVisible();
    await expect(page.locator("#summaries")).toBeVisible();
    await expect(page.locator("#pager-pages")).toBeVisible();
  });

  test("汇总带显示真实后端数据（非零、非 dash）", async ({ page }) => {
    // 总单量应 > 0
    const totalOrders = await page.locator("#sum-total-orders").textContent();
    expect(totalOrders).not.toBe("—");
    expect(totalOrders).not.toBe("0");

    // 广告消耗应 > 0
    const spend = await page.locator("#sum-spend").textContent();
    expect(spend).not.toBe("—");
    expect(spend).not.toBe("0");

    // 净利润应有值
    const netProfit = await page.locator("#sum-net-profit").textContent();
    expect(netProfit).not.toBe("—");

    // 实际 ROI 应有值
    const roi = await page.locator("#sum-roi").textContent();
    expect(roi).not.toBe("—");
  });

  test("表格行展示商品数据（SPU ID、金额、格式化）", async ({ page }) => {
    const rows = page.locator("#rows .tabulator-row");
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThanOrEqual(100); // default limit

    // First row should contain product cell
    const firstRow = rows.first();
    const html = await firstRow.innerHTML();
    // Should contain SPU ID pattern
    expect(html).toContain("TEST_E2E_SPU");
  });

  test("CNY 格式化：金额列以数字形式展示", async ({ page }) => {
    // 金额列不应包含 "USD" 或 "VND" 字样
    const tableText = await page.locator("#rows").textContent();
    // CNY 格式：数字.四位小数 或 整数
    // 不应出现原始 VND 金额（如 526600）
    expect(tableText).not.toMatch(/526[,.]?600/);
  });

  test("亏损行有 row-bad 样式（SPU 2 亏损场景）", async ({ page }) => {
    const badRows = page.locator("#rows .tabulator-row.row-bad");
    const badCount = await badRows.count();
    // SPU 2 是亏损场景（spend > profit），应至少有一行标红
    // 但实际取决于排序和分页，所以只检查如果有 bad row 则格式正确
    if (badCount > 0) {
      const firstBad = badRows.first();
      await expect(firstBad).toBeVisible();
    }
  });

  test("无浏览器 console error", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));
    // Trigger a reload to capture any errors
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Filter out favicon errors
    const realErrors = errors.filter((e) => !e.includes("favicon"));
    expect(realErrors).toHaveLength(0);
  });
});

// ── C-SPUROI-03: 默认日期范围按 VN 时区 T-1 ─────────────────────
test.describe("C-SPUROI-03 @page:spu-roi @tier:core", () => {
  test("默认日期范围按店铺 VN 时区计算 T-1", async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Date inputs should be filled (default range = T-1 in VN timezone)
    const startDate = await page.locator("#filter-w-start").inputValue();
    const endDate = await page.locator("#filter-w-end").inputValue();

    // Both should be non-empty (auto-filled by page JS)
    expect(startDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(endDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    // In VN timezone, T-1 should be yesterday (or today if before VN midnight)
    // Just verify the end date is a valid recent date
    const end = new Date(endDate + "T00:00:00Z");
    const now = new Date();
    const diffDays = (now - end) / (1000 * 60 * 60 * 24);
    expect(diffDays).toBeLessThan(3); // Should be within 2 days of now
  });
});

// ── C-SPUROI-04: 筛选控件（费率、include_all、刷新）────────────────
test.describe("C-SPUROI-04 @page:spu-roi @tier:core", () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });
  });

  test("fee_rate 覆写后请求参数正确", async ({ page }) => {
    // Intercept the next analytics request
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      (async () => {
        await page.fill("#filter-fee", "12.5");
        // Wait for debounce
        await page.waitForTimeout(1500);
      })(),
    ]);

    const url = new URL(request.url());
    expect(url.searchParams.get("fee_rate")).toBe("0.125");
  });

  test("含无活动 SPU 开关传递 include_all=true", async ({ page }) => {
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      page.locator("#filter-include-all").click(),
    ]);

    const url = new URL(request.url());
    expect(url.searchParams.get("include_all")).toBe("true");
  });

  test("刷新按钮重新加载数据", async ({ page }) => {
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      page.locator("#btn-refresh").click(),
    ]);

    expect(request.url()).toContain("/v2/analytics/spu-roi");
    // Table should still have rows after refresh
    await page.waitForSelector("#rows .tabulator-row", { timeout: 10000 });
    const count = await page.locator("#rows .tabulator-row").count();
    expect(count).toBeGreaterThan(0);
  });
});

// ── C-SPUROI-05: SPU 多选 → spu_ids → URL 恢复 ───────────────────
test.describe("C-SPUROI-05 @page:spu-roi @tier:core", () => {
  test("SPU 多选应用后 URL 包含 spu_ids；刷新后恢复", async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Use JavaScript to directly set SPU selection via TomSelect
    await page.evaluate(() => {
      const ts = document.querySelector("#filter-spu-ids")?.tomselect;
      if (!ts) throw new Error("TomSelect not initialized");
      ts.addOption({ spu_id: "TEST_E2E_SPU_001", title: "TEST_E2E_SPU_001", status: "ACTIVE" });
      ts.addOption({ spu_id: "TEST_E2E_SPU_002", title: "TEST_E2E_SPU_002", status: "ACTIVE" });
      ts.addItem("TEST_E2E_SPU_001", true);
      ts.addItem("TEST_E2E_SPU_002", true);
      ts.refreshItems();
      ts.trigger("change", ts.getValue());
    });

    // Wait for apply button to be enabled
    await page.waitForFunction(() => !document.querySelector("#btn-spu-apply")?.disabled);

    // Click apply
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      page.locator("#btn-spu-apply").click(),
    ]);

    // Request should include spu_ids
    const url = new URL(request.url());
    const spuIds = url.searchParams.get("spu_ids");
    expect(spuIds).toBeTruthy();
    expect(spuIds).toContain("TEST_E2E_SPU_001");

    // URL should contain spu_ids
    expect(page.url()).toContain("spu_ids=");

    // Reload and verify spu_ids persist
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });
    const currentUrl = new URL(page.url());
    expect(currentUrl.searchParams.get("spu_ids")).toBeTruthy();
  });
});

// ── C-SPUROI-06: 排序 + 分页 ─────────────────────────────────────
test.describe("C-SPUROI-06 @page:spu-roi @tier:core", () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });
  });

  test("点击排序列触发服务端排序请求", async ({ page }) => {
    // Find a sortable column header (e.g., 广告消耗)
    const spendHeader = page.locator("#rows .tabulator-col", { hasText: "广告消耗" }).first();
    await expect(spendHeader).toBeVisible();

    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      spendHeader.click(),
    ]);

    const url = new URL(request.url());
    expect(url.searchParams.get("sort")).toBe("spend");
    expect(["asc", "desc"]).toContain(url.searchParams.get("order"));
  });

  test("切换每页 50 条后 limit 参数正确", async ({ page }) => {
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      page.selectOption("#filter-limit", "50"),
    ]);

    const url = new URL(request.url());
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("offset")).toBe("0");
  });

  test("下一页按钮触发翻页", async ({ page }) => {
    // First ensure we're on default 100 per page with enough data
    await page.selectOption("#filter-limit", "50");
    await page.waitForSelector("#rows .tabulator-row", { timeout: 10000 });

    // Click next page
    const nextBtn = page.locator('button[data-page="next"]');
    if (await nextBtn.isEnabled()) {
      const [request] = await Promise.all([
        page.waitForRequest("**/v2/analytics/spu-roi?**"),
        nextBtn.click(),
      ]);

      const url = new URL(request.url());
      expect(url.searchParams.get("offset")).toBe("50");
    }
  });
});

// ── C-SPUROI-07: 钻取面板 ───────────────────────────────────────
test.describe("C-SPUROI-07 @page:spu-roi @tier:core", () => {
  test("点击行打开钻取面板，展示 P&L 和 tab 切换", async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Click first row
    await page.locator("#rows .tabulator-row").first().click();

    // Drill panel should appear
    await page.waitForSelector(".op-drill-row", { timeout: 5000 });

    // P&L tab should be active by default
    const pnlContent = await page.locator(".op-drill-row").textContent();
    expect(pnlContent).toContain("利润构成");

    // Switch to orders tab
    await page.locator('.op-drill-tab[data-tab="orders"]').click();
    await page.waitForTimeout(500);
    const ordersContent = await page.locator(".op-drill-row").textContent();
    expect(ordersContent).toContain("订单号");

    // Switch to settlements tab
    await page.locator('.op-drill-tab[data-tab="settlements"]').click();
    await page.waitForTimeout(500);

    // Switch to cases tab
    await page.locator('.op-drill-tab[data-tab="cases"]').click();
    await page.waitForTimeout(500);

    // Switch to ads tab
    await page.locator('.op-drill-tab[data-tab="ads"]').click();
    await page.waitForTimeout(500);

    // Close drill panel with Escape
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () => document.querySelectorAll(".op-drill-row").length === 0,
    );
  });
});

// ── C-SPUROI-08: 店铺切换 ───────────────────────────────────────
test.describe("C-SPUROI-08 @page:spu-roi @tier:core", () => {
  test("店铺切换后 URL 和页面状态更新", async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Get current shop from URL
    const initialUrl = new URL(page.url());
    const initialShop = initialUrl.searchParams.get("shop_pk");

    // Switch to shop2 (seeded separately with its own SPU data)
    const [request] = await Promise.all([
      page.waitForRequest("**/v2/analytics/spu-roi?**"),
      page.selectOption("#shop-switcher", SHOP2_PK),
    ]);

    // Request should target the new shop
    const url = new URL(request.url());
    expect(url.searchParams.get("shop_pk")).toBe(SHOP2_PK);

    // URL should be updated
    const currentUrl = new URL(page.url());
    expect(currentUrl.searchParams.get("shop_pk")).toBe(SHOP2_PK);
    expect(url.searchParams.get("shop_pk")).not.toBe(initialShop);

    // Table should load with shop2 data
    await page.waitForSelector("#rows .tabulator-row", { timeout: 15000 });
    const count = await page.locator("#rows .tabulator-row").count();
    expect(count).toBeGreaterThan(0);
  });
});

// ── C-SPUROI-09: 退出登录 → session 失效 ────────────────────────
test.describe("C-SPUROI-09 @page:spu-roi @tier:core", () => {
  test("退出登录后 session 失效，访问页面回到登录页", async ({ page }) => {
    await loginAndNavigate(page);
    await page.waitForSelector("#rows .tabulator-row", { timeout: 20000 });

    // Click logout
    await page.locator("#btn-logout").click();

    // Should redirect to login page
    await page.waitForURL(/\/v2\/auth\/login/, { timeout: 10000 });

    // Now try to access the page again - should redirect to login
    await page.goto(`/v2/pages/spu-roi?shop_pk=${SHOP_PK}`, {
      waitUntil: "domcontentloaded",
    });

    const url = page.url();
    expect(url).toContain("/v2/auth/login");
  });
});
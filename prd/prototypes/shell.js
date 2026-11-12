/* 原型交互：侧栏、折叠/抽屉、角色切换、对话框、页签。不连接后端。
 * 壳层行为对照 frontend/src/app/AppShell.tsx：桌面 ≥1024px 可折叠，
 * 偏好写入 localStorage 键 sidebar-collapsed（'1'/'0'）；小于 1024px 用抽屉。
 */
(function () {
  const DESKTOP_QUERY = "(min-width: 1024px)";
  const COLLAPSED_KEY = "sidebar-collapsed";

  const PAGES = [
    { id: "dashboard", label: "控制台", group: "总览", href: "dashboard.html",
      desc: "查看店铺数据覆盖、同步状态与常用工作入口。" },
    { id: "focused-spus", label: "重点关注 SPU", group: "经营分析", href: "focused-spus.html",
      desc: "按店铺维护关注清单，用同一套 v10 盈利口径查看这些商品的经营结果。" },
    { id: "spu-roi", label: "SPU ROI", group: "经营分析", href: "spu-roi.html",
      desc: "按店铺与经营日期查看全量或指定 SPU 的盈利大盘与明细。" },
    { id: "spu-profit-deterioration", label: "利润劣化告警", group: "经营分析", href: "spu-profit-deterioration.html",
      desc: "对比 7 天与 3 天窗口的两期盈利表现。本页不做阈值告警或投放建议。" },
    { id: "ad-daily", label: "广告日明细", group: "经营分析", href: "ad-daily.html",
      desc: "按账户、日期与商品筛选广告日数据，结合服务端汇总评估广告表现。" },
    { id: "manual-costs", label: "采购工作台", group: "基础设置", href: "manual-costs.html",
      desc: "按店铺核对货源价与人工成本，编辑草稿后确认提交。" },
    { id: "shops", label: "店铺注册", group: "基础设置", href: "shops.html",
      desc: "从插件发现到 App 配置与授权，完成每家店铺的接入。" },
    { id: "enum-map", label: "枚举映射", group: "基础设置", href: "enum-map.html",
      desc: "集中查看来源字段与标准枚举的映射，确认后维护生效配置。" },
    { id: "runtime-configs", label: "运行配置", group: "基础设置", href: "runtime-configs.html",
      desc: "查看已发布版本、编辑草稿，并在确认后发布或恢复历史。" },
    { id: "sync-jobs", label: "定时任务", group: "基础设置", href: "sync-jobs.html",
      desc: "查看同步任务、执行记录与周期调度，按需手动触发。" },
    { id: "users", label: "用户管理", group: "基础设置", href: "users.html",
      desc: "管理账号、角色与会话，按实际工作职责分配能力。" },
    { id: "intercept-configs", label: "拦截配置", group: "数据工具", href: "intercept-configs.html",
      desc: "集中管理采集规则、启停状态与捕获范围。" },
    { id: "intercept-requests", label: "拦截记录", group: "数据工具", href: "intercept-requests.html",
      desc: "筛选请求记录，查看响应与处理详情，定位采集问题。" },
    { id: "intercept-stats", label: "拦截统计", group: "数据工具", href: "intercept-stats.html",
      desc: "查看请求趋势与分布，识别错误和异常变化。" },
    { id: "video-publish", label: "视频发布", group: "数据工具", href: "video-publish.html",
      desc: "查看设备与任务状态，管理发布队列并核对执行结果。" },
    { id: "media-assets", label: "资产管理", group: "内容资产", href: "media-assets.html",
      desc: "上传并整理图片与视频，维护公共素材包与标签。" },
    { id: "digital-humans", label: "数字人", group: "内容资产", href: "digital-humans.html",
      desc: "维护数字人档案、形象照片，并关联素材包或单条素材。" },
    { id: "runninghub", label: "换装视频", group: "内容资产", href: "runninghub.html",
      desc: "选好服装与人物模板，按批次生成并查看每段成片。" }
  ];

  const ROLES = {
    admin: {
      code: "admin", name: "系统管理员", displayName: "示例管理员",
      pages: PAGES.map((p) => p.id), write: true, admin: true
    },
    operator: {
      code: "operator", name: "运营", displayName: "示例运营",
      pages: PAGES.filter((p) => p.id !== "users" && p.id !== "video-publish").map((p) => p.id),
      write: true, admin: false
    },
    viewer: {
      code: "viewer", name: "只读分析", displayName: "示例分析",
      pages: ["dashboard", "focused-spus", "spu-roi", "ad-daily", "intercept-stats"],
      write: false, admin: false
    }
  };

  function role() {
    const code = sessionStorage.getItem("prd-role") || "admin";
    return ROLES[code] || ROLES.admin;
  }

  function setRole(code) {
    sessionStorage.setItem("prd-role", code);
    location.reload();
  }

  function isDesktop() {
    return window.matchMedia(DESKTOP_QUERY).matches;
  }

  function readCollapsedPreference() {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function writeCollapsedPreference(collapsed) {
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch (err) {
      // Storage denial must not block navigation or the in-memory preference.
    }
  }

  function brandSvg() {
    return '<svg aria-hidden="true" width="30" height="30" viewBox="0 0 34 34" fill="none"><path d="M5 8h24M17 8v21M5 17h7M24 17h5" stroke="currentColor" stroke-width="3.5"/></svg>';
  }

  function navigationIcon(collapsed) {
    const chevron = collapsed ? "m13 9 3 3-3 3" : "m16 9-3 3 3 3";
    return `<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M9 4v16"></path><path d="${chevron}"></path></svg>`;
  }

  function closeIcon() {
    return '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>';
  }

  function renderBrand() {
    return `<div class="erp-brand" aria-label="TTS ERP">${brandSvg()}<span>tts<span class="erp-brand-suffix"> / erp</span></span></div>`;
  }

  function renderNav(currentId, allowed) {
    const groups = [];
    for (const page of PAGES) {
      if (!allowed.includes(page.id)) continue;
      const last = groups[groups.length - 1];
      if (!last || last.name !== page.group) groups.push({ name: page.group, items: [page] });
      else last.items.push(page);
    }
    const sections = groups.map((g) => {
      const items = g.items.map((p) => {
        const current = p.id === currentId ? ' aria-current="page"' : "";
        return `<li class="min-w-0"><a class="erp-nav-link whitespace-normal break-words" href="${p.href}"${current}><span class="erp-nav-indicator" aria-hidden="true"></span><span class="min-w-0 break-words">${p.label}</span></a></li>`;
      }).join("");
      return `<section class="erp-nav-section"><h2 class="erp-nav-group break-words">${g.name}</h2><ul class="erp-nav-list">${items}</ul></section>`;
    }).join("");
    return `<nav aria-label="主导航" class="erp-navigation">${sections}</nav>`;
  }

  function renderSidebar(currentId, allowed) {
    return `<aside id="erp-navigation" class="erp-sidebar">${renderBrand()}${renderNav(currentId, allowed)}<p class="erp-sidebar-note">SHOP OPERATIONS</p></aside>`;
  }

  function renderDrawer(currentId, allowed) {
    return `<div class="erp-drawer-backdrop" id="erp-drawer" aria-hidden="true">
      <aside class="erp-drawer" role="dialog" aria-modal="true" aria-labelledby="erp-drawer-title">
        <header class="erp-drawer-header">
          <h2 id="erp-drawer-title" class="erp-drawer-heading">主导航</h2>
          <button type="button" class="erp-drawer-close" data-drawer-close aria-label="关闭主导航">${closeIcon()}</button>
        </header>
        <div class="erp-drawer-body">
          ${renderBrand()}
          ${renderNav(currentId, allowed)}
        </div>
      </aside>
    </div>`;
  }

  function applyCapabilities(currentRole) {
    document.querySelectorAll("[data-write]").forEach((el) => {
      if (!currentRole.write) {
        el.setAttribute("disabled", "disabled");
        el.setAttribute("aria-disabled", "true");
        if (el.tagName === "A") el.addEventListener("click", (e) => e.preventDefault());
      }
    });
    document.querySelectorAll("[data-admin]").forEach((el) => {
      if (!currentRole.admin) {
        el.setAttribute("disabled", "disabled");
        el.setAttribute("aria-disabled", "true");
        if (el.tagName === "A") el.addEventListener("click", (e) => e.preventDefault());
      }
    });
  }

  function mountChrome(shell, currentId, allowed) {
    const toggle = document.getElementById("erp-nav-toggle");
    const drawer = document.getElementById("erp-drawer");
    let collapsed = readCollapsedPreference();
    let mobileOpen = false;

    function setDrawer(open) {
      mobileOpen = open;
      if (!drawer) return;
      drawer.classList.toggle("is-open", open);
      drawer.setAttribute("aria-hidden", open ? "false" : "true");
    }

    function sync() {
      const desktop = isDesktop();
      shell.classList.toggle("is-sidebar-collapsed", desktop && collapsed);
      if (toggle) {
        if (desktop) {
          toggle.setAttribute("aria-label", collapsed ? "展开侧边栏" : "折叠侧边栏");
          toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
          if (collapsed) toggle.removeAttribute("aria-controls");
          else toggle.setAttribute("aria-controls", "erp-navigation");
          toggle.innerHTML = navigationIcon(collapsed);
          setDrawer(false);
        } else {
          toggle.setAttribute("aria-label", "打开主导航");
          toggle.setAttribute("aria-expanded", mobileOpen ? "true" : "false");
          toggle.setAttribute("aria-controls", "erp-drawer");
          toggle.innerHTML = navigationIcon(true);
          shell.classList.remove("is-sidebar-collapsed");
        }
      }
    }

    if (toggle) {
      toggle.addEventListener("click", () => {
        if (isDesktop()) {
          collapsed = !collapsed;
          writeCollapsedPreference(collapsed);
          sync();
        } else {
          setDrawer(!mobileOpen);
          sync();
        }
      });
    }
    if (drawer) {
      drawer.addEventListener("click", (e) => {
        if (e.target === drawer) { setDrawer(false); sync(); }
      });
      drawer.querySelectorAll("[data-drawer-close]").forEach((btn) => {
        btn.addEventListener("click", () => { setDrawer(false); sync(); });
      });
      drawer.querySelectorAll(".erp-nav-link").forEach((link) => {
        link.addEventListener("click", () => setDrawer(false));
      });
    }

    const media = window.matchMedia(DESKTOP_QUERY);
    const onChange = () => { setDrawer(false); sync(); };
    if (media.addEventListener) media.addEventListener("change", onChange);
    else media.addListener(onChange);
    sync();
  }

  function mountWorkspace() {
    const root = document.querySelector("[data-page]");
    if (!root) return;
    const pageId = root.getAttribute("data-page");
    const page = PAGES.find((p) => p.id === pageId);
    const currentRole = role();
    const allowed = currentRole.pages;
    const canEnter = allowed.includes(pageId);
    const title = root.getAttribute("data-title") || (page && page.label) || "页面";
    const desc = root.getAttribute("data-desc") || (page && page.desc) || "";
    const group = (page && page.group) || "";

    const roleOptions = Object.values(ROLES).map((r) =>
      `<option value="${r.code}"${r.code === currentRole.code ? " selected" : ""}>${r.name}</option>`
    ).join("");

    const header = `<header class="erp-header">
      <button type="button" class="erp-navigation-toggle" id="erp-nav-toggle" aria-label="折叠侧边栏"></button>
      <div class="erp-header-titles">
        <p class="erp-header-context">运营工作台${group ? " / " + group : ""}</p>
        <h1 class="erp-header-title">${title}</h1>
        ${desc ? `<p class="erp-header-desc">${desc}</p>` : ""}
      </div>
      <div class="erp-header-actions erp-session-actions">
        <label class="erp-role-switch">原型角色
          <select id="prd-role" aria-label="切换原型演示角色">${roleOptions}</select>
        </label>
        <span class="erp-user">${currentRole.displayName}</span>
        <a class="btn" href="login.html">退出登录</a>
      </div>
    </header>`;

    const mainContent = canEnter
      ? root.innerHTML
      : `<div class="forbidden"><h2>没有访问该页面的权限</h2><p>当前演示角色「${currentRole.name}」不能进入「${title}」。请切换角色，或返回控制台。</p><p><a class="btn btn-primary" href="dashboard.html">返回控制台</a></p></div>`;

    document.body.className = "erp-shell-page";
    document.body.innerHTML = `<div class="erp-shell">${renderSidebar(pageId, allowed)}<div class="erp-body">${header}<main class="erp-main"><div class="erp-page">${mainContent}</div></main></div>${renderDrawer(pageId, allowed)}</div>`;
    document.getElementById("prd-role").addEventListener("change", (e) => setRole(e.target.value));
    mountChrome(document.querySelector(".erp-shell"), pageId, allowed);
    if (canEnter) {
      applyCapabilities(currentRole);
      document.querySelectorAll("[data-need-page]").forEach((el) => {
        const need = el.getAttribute("data-need-page");
        if (need && !currentRole.pages.includes(need)) el.hidden = true;
      });
    }
    bindInteractions();
  }

  window.prdOpen = function (id) {
    const el = document.getElementById(id);
    if (el) { el.classList.add("is-open"); el.setAttribute("aria-hidden", "false"); }
  };
  window.prdClose = function (id) {
    const el = document.getElementById(id);
    if (el) { el.classList.remove("is-open"); el.setAttribute("aria-hidden", "true"); }
  };

  function bindInteractions() {
    document.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        if (btn.disabled || btn.getAttribute("aria-disabled") === "true") { e.preventDefault(); return; }
        prdOpen(btn.getAttribute("data-open"));
      });
    });
    document.querySelectorAll("[data-close]").forEach((btn) => {
      btn.addEventListener("click", () => prdClose(btn.getAttribute("data-close")));
    });
    document.querySelectorAll(".dialog-backdrop").forEach((el) => {
      el.addEventListener("click", (e) => { if (e.target === el) el.classList.remove("is-open"); });
    });
    document.querySelectorAll("[data-tabs]").forEach((bar) => {
      const key = bar.getAttribute("data-tabs");
      bar.querySelectorAll("[data-tab]").forEach((btn) => {
        btn.addEventListener("click", () => {
          bar.querySelectorAll("[data-tab]").forEach((b) => { b.classList.remove("is-active"); b.setAttribute("aria-pressed", "false"); });
          btn.classList.add("is-active"); btn.setAttribute("aria-pressed", "true");
          document.querySelectorAll(`[data-tab-panel="${key}"]`).forEach((panel) => {
            panel.classList.toggle("hidden", panel.getAttribute("data-panel") !== btn.getAttribute("data-tab"));
          });
        });
      });
    });
    document.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const el = document.getElementById(btn.getAttribute("data-toggle"));
        if (el) el.classList.toggle("hidden");
      });
    });
  }

  window.PRD_PAGES = PAGES;
  window.PRD_ROLES = ROLES;
  window.prdRole = role;
  window.prdBrandSvg = brandSvg;
  document.addEventListener("DOMContentLoaded", mountWorkspace);
})();

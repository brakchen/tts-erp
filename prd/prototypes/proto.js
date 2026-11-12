/* 原型交互补充：口径说明、行展开、密码显示、登录核对、示例状态切换。不连接后端。 */
(function () {
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function ensureHintDialog() {
    let el = document.getElementById("dlg-metric-hint");
    if (el) return el;
    el = document.createElement("div");
    el.id = "dlg-metric-hint";
    el.className = "dialog-backdrop";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-hidden", "true");
    el.setAttribute("aria-labelledby", "dlg-metric-hint-title");
    el.innerHTML = '<div class="dialog"><h2 id="dlg-metric-hint-title">口径说明</h2><p id="dlg-metric-hint-body"></p><div class="dialog-actions"><button class="btn" type="button" data-close="dlg-metric-hint">关闭</button></div></div>';
    document.body.appendChild(el);
    el.addEventListener("click", function (e) { if (e.target === el) closeHint(); });
    el.querySelector("[data-close]").addEventListener("click", closeHint);
    return el;
  }

  function openHint(title, body) {
    const el = ensureHintDialog();
    document.getElementById("dlg-metric-hint-title").textContent = title || "口径说明";
    document.getElementById("dlg-metric-hint-body").textContent = body || "";
    if (window.prdOpen) window.prdOpen("dlg-metric-hint");
    else { el.classList.add("is-open"); el.setAttribute("aria-hidden", "false"); }
  }

  function closeHint() {
    if (window.prdClose) window.prdClose("dlg-metric-hint");
    else {
      const el = document.getElementById("dlg-metric-hint");
      if (el) { el.classList.remove("is-open"); el.setAttribute("aria-hidden", "true"); }
    }
  }

  function bindDemoStates(root) {
    root.querySelectorAll("[data-demo-group]").forEach(function (bar) {
      const group = bar.getAttribute("data-demo-group");
      bar.querySelectorAll("[data-demo]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const value = btn.getAttribute("data-demo");
          bar.querySelectorAll("[data-demo]").forEach(function (b) {
            b.classList.toggle("is-active", b === btn);
            b.setAttribute("aria-pressed", b === btn ? "true" : "false");
          });
          document.querySelectorAll('[data-demo-panel="' + group + '"]').forEach(function (panel) {
            panel.classList.toggle("hidden", panel.getAttribute("data-demo") !== value);
          });
        });
      });
    });
  }

  function bindPasswordToggles(root) {
    root.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = btn.getAttribute("data-password-toggle");
        const input = document.getElementById(id);
        if (!input) return;
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-pressed", show ? "true" : "false");
        btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
        btn.textContent = show ? "隐藏" : "显示";
      });
    });
  }

  function bindExpandRows(root) {
    root.querySelectorAll("[data-expand-row]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = btn.getAttribute("data-expand-row");
        const row = document.getElementById(id);
        if (!row) return;
        const open = row.classList.toggle("hidden") === false;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        const more = btn.getAttribute("data-expand-more") || "更多";
        const less = btn.getAttribute("data-expand-less") || "收起";
        btn.textContent = open ? less : more;
      });
    });
  }

  function bindLogin(root) {
    const form = root.querySelector("#login-form");
    if (!form) return;
    const submit = form.querySelector('[type="submit"]');
    const recovery = document.getElementById("login-recovery");
    const demoUnverified = document.getElementById("demo-login-unverified");
    const pendingLabel = "登录中…";
    const idleLabel = submit ? submit.textContent : "登录 ↗";

    form.addEventListener("submit", function (e) {
      const recoveryOpen = recovery && !recovery.classList.contains("hidden") && recovery.getAttribute("data-retry-allowed") !== "true";
      if (recoveryOpen) { e.preventDefault(); return; }
      e.preventDefault();
      if (submit) { submit.disabled = true; submit.textContent = pendingLabel; form.setAttribute("aria-busy", "true"); }
      setTimeout(function () { window.location.href = "dashboard.html"; }, 600);
    });

    function setPhase(phase) {
      if (!recovery) return;
      recovery.querySelectorAll("[data-recovery-phase]").forEach(function (el) {
        el.classList.toggle("hidden", el.getAttribute("data-recovery-phase") !== phase);
      });
      recovery.setAttribute("data-phase", phase);
      if (phase !== "checked-allowed") recovery.setAttribute("data-retry-allowed", "false");
    }

    if (demoUnverified) {
      demoUnverified.addEventListener("click", function () {
        if (!recovery) return;
        recovery.classList.remove("hidden");
        setPhase("unverified");
        recovery.setAttribute("data-retry-allowed", "false");
      });
    }
    const queryBtn = document.getElementById("btn-query-session");
    if (queryBtn) {
      queryBtn.addEventListener("click", function () {
        setPhase("checking");
        setTimeout(function () { setPhase("checked"); }, 500);
      });
    }
    const allowBtn = document.getElementById("btn-allow-relogin");
    if (allowBtn) {
      allowBtn.addEventListener("click", function () {
        if (recovery) recovery.setAttribute("data-retry-allowed", "true");
        setPhase("checked-allowed");
        if (submit) { submit.disabled = false; submit.textContent = idleLabel; }
      });
    }
    const useSession = document.getElementById("btn-use-session");
    if (useSession) {
      useSession.addEventListener("click", function () { window.location.href = "dashboard.html"; });
    }
  }

  function bindOpenTabs(root) {
    root.querySelectorAll("[data-open-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.disabled || btn.getAttribute("aria-disabled") === "true") return;
        var spec = btn.getAttribute("data-open-tab") || "";
        var parts = spec.split(":");
        if (parts.length !== 2) return;
        var workspace = btn.getAttribute("data-workspace");
        if (workspace) {
          root.querySelectorAll("[data-workspace-panel]").forEach(function (panel) {
            panel.classList.toggle("hidden", panel.getAttribute("data-workspace-panel") !== workspace);
          });
        }
        var bar = root.querySelector('[data-tabs="' + parts[0] + '"]');
        var tab = bar && bar.querySelector('[data-tab="' + parts[1] + '"]');
        if (tab) tab.click();
      });
    });
  }

  function bindCopy(root) {
    root.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const text = btn.getAttribute("data-copy") || "";
        const status = btn.getAttribute("data-copy-status");
        function done(ok) {
          if (!status) return;
          const el = document.getElementById(status);
          if (el) el.textContent = ok ? (btn.getAttribute("data-copy-ok") || "已复制") : (btn.getAttribute("data-copy-fail") || "复制失败，请手动选择");
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
        } else done(false);
      });
    });
  }

  ready(function () {
    const root = document;
    bindDemoStates(root);
    bindPasswordToggles(root);
    bindExpandRows(root);
    bindLogin(root);
    bindCopy(root);
    bindOpenTabs(root);
    root.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-hint]");
      if (!btn) return;
      e.preventDefault();
      openHint(btn.getAttribute("data-hint-title") || "口径说明", btn.getAttribute("data-hint") || "");
    });
  });
})();

/* 店铺注册台 — /v2/pages/shops
 *
 * 数据源：
 *   GET  /v2/commerce/channel-accounts   已注册店铺（readonly）
 *   GET  /v2/admin/shops/unregistered    插件数据里出现但未注册的 shop_id（admin）
 *   POST /v2/admin/shops/register        人工注册（admin，cookie 会话带 CSRF 头）
 *
 * 权限降级：非 admin 会话时 unregistered/register 会 403 —— 页面仍可
 * 只读展示已注册列表，表单区提示需要 admin 登录。
 */
(() => {
  

  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";
  var CSRF_HEADER = "tts-erp";

  function $(sel) { return document.querySelector(sel); }

  function showErr(msg) {
    var el = $("#err");
    el.textContent = msg;
    el.classList.remove("d-none");
  }
  function clearErr() { $("#err").classList.add("d-none"); }

  function api(path, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== "GET") {
      headers["X-Requested-With"] = CSRF_HEADER; // CSRF guard
    }
    if (opts.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(PREFIX + path, {
      method: opts.method || "GET",
      credentials: "include", // session cookie
      headers: headers,
      body: opts.body,
    });
  }

  function loginUrl() {
    return PREFIX + "/v2/auth/login?next=" + PREFIX + "/v2/pages/shops";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- 已注册店铺 ----------
  function loadShops() {
    return api("/v2/commerce/channel-accounts?platform=tiktok&limit=500")
      .then((r) => {
        if (r.status === 401) {
          window.location.href = loginUrl(); // pi-lens-ignore: no-open-redirect-js
          return null;
        }
        if (!r.ok) throw new Error("shops HTTP " + r.status);
        return r.json();
      })
      .then((shops) => {
        if (!shops) return;
        var body = $("#shop-body");
        if (!shops.length) {
          body.innerHTML = '<tr><td colspan="5" class="text-muted">暂无店铺</td></tr>';
          return;
        }
        body.innerHTML = shops.map((s) => {
          // 同步方式：有 credential_id = 已 OAuth 授权走 API 同步，否则仅插件
          // （2026-09-11 migration 0025 删除了 shops.data_source 枚举列）
          var isApi = s.credential_id != null;
          var badge = isApi
            ? '<span class="badge badge-sync-api">API 同步</span>'
            : '<span class="badge badge-sync-plugin">仅插件</span>';
          var pk = s.shop_pk || s.id;
          var dateVal = s.opened_date || "";
          return "<tr data-shop-pk=" + pk + ">" +
            '<td class="mono">' + esc(s.shop_id) + "</td>" +
            "<td>" + esc(s.account_name || "—") + "</td>" +
            "<td>" + esc(s.region || "—") + "</td>" +
            '<td class="opened-date" style="cursor:pointer" title="点击修改">' +
              esc(dateVal || "—") + "</td>" +
            "<td>" + badge + ' <span class="text-muted small">' + esc(s.status || "") + "</span></td>" +
            "</tr>";
        }).join("");
        // 点击 opened_date 列进入编辑模式
        body.querySelectorAll(".opened-date").forEach((td) => {
          td.addEventListener("click", () => {
            if (td.querySelector("input")) return; // 已在编辑
            var row = td.closest("tr");
            // pi-lens-ignore: no-unsafe-innerhtml
            var pk = row.getAttribute("data-shop-pk");
            var current = td.textContent.trim();
            var input = document.createElement("input");
            input.type = "date";
            input.className = "form-control form-control-sm";
            input.value = current === "—" ? "" : current;
            // pi-lens-ignore: no-unsafe-innerhtml
            td.textContent = "";
            // pi-lens-ignore: no-unsafe-innerhtml
            td.appendChild(input);
            input.focus();
            function save() {
              var newVal = input.value || null;
              td.textContent = newVal || "—";
              if (newVal === current || (newVal === null && current === "—")) return;
              api("/v2/admin/shops/" + pk, {
                method: "PATCH",
                body: JSON.stringify({ opened_date: newVal }),
              }).then((r) => {
                if (!r.ok) {
                  showErr("更新失败: HTTP " + r.status);
                  td.textContent = current; // 回滚
                  return;
                }
                return r.json();
              }).then((data) => {
                if (data && data.shop) {
                  td.textContent = data.shop.opened_date || "—";
                }
              }).catch((err) => {
                showErr(err.message || String(err));
                td.textContent = current;
              });
            }
            input.addEventListener("blur", save);
            input.addEventListener("keydown", (e) => {
              if (e.key === "Enter") input.blur();
              if (e.key === "Escape") { td.textContent = current; }
            });
          });
        });
      });
  }

  // ---------- 待注册候选 ----------
  function loadCandidates() {
    return api("/v2/admin/shops/unregistered")
      .then((r) => {
        if (r.status === 403) {
          $("#cand-body").innerHTML =
            '<tr><td colspan="3" class="text-muted">需要 readwrite 及以上会话查看候选列表</td></tr>';
          return null;
        }
        if (!r.ok) throw new Error("unregistered HTTP " + r.status);
        return r.json();
      // pi-lens-ignore: no-unsafe-innerhtml
      })
      .then((data) => {
        // pi-lens-ignore: no-unsafe-innerhtml
        if (!data) return;
        var cands = data.candidates || [];
        $("#cand-count").textContent = cands.length ? "(" + cands.length + ")" : "";
        var body = $("#cand-body");
        if (!cands.length) {
          body.innerHTML = '<tr><td colspan="3" class="text-muted">没有待注册的店铺</td></tr>';
          return;
        }
        // pi-lens-ignore: no-unsafe-innerhtml
        body.innerHTML = cands.map((c) => "<tr>" +
            '<td class="mono">' + esc(c.shop_id) + "</td>" +
            "<td>" + esc((c.sources || []).join(", ")) + "</td>" +
            '<td><button class="btn btn-sm btn-outline-dark btn-fill" data-shop="' +
              esc(c.shop_id) + '">填入表单</button></td>' +
            "</tr>").join("");
        body.querySelectorAll(".btn-fill").forEach((btn) => {
          btn.addEventListener("click", () => {
            $("#f-shop-id").value = btn.getAttribute("data-shop");
            $("#f-shop-id").focus();
          });
        });
      });
  }

  // ---------- 注册提交 ----------
  function bindForm() {
    $("#register-form").addEventListener("submit", (e) => {
      e.preventDefault();
      clearErr();
      var payload = {
        platform: "tiktok",
        shop_id: $("#f-shop-id").value.trim(),
        account_name: $("#f-name").value.trim() || null,
        region: $("#f-region").value.trim() || null,
        opened_date: $("#f-opened").value || null,
      };
      api("/v2/admin/shops/register", {
        method: "POST",
        body: JSON.stringify(payload),
      })
        .then((r) => {
          if (r.status === 403) {
            showErr("需要 readwrite 及以上会话才能注册店铺（当前会话角色不足）。");
            return null;
          }
          return r.json().then((body) => {
            if (!r.ok) {
              var detail = body && body.detail;
              if (Array.isArray(detail)) {
                detail = detail.map((d) => d.msg).join("; ");
              }
              throw new Error("注册失败：" + (detail || "HTTP " + r.status));
            }
            return body;
          });
        })
        .then((body) => {
          if (!body) return;
          $("#f-shop-id").value = "";
          return loadShops().then(loadCandidates);
        })
        .catch((err) => { showErr(err.message || String(err)); });
    });
  }

  // ---------- 权限提示 ----------
  function probeAuth() {
    return api("/v2/auth/me").then((r) => {
      if (!r.ok) return;
      return r.json().then((me) => {
        if (me && me.role === "readonly") {
          var note = $("#auth-note");
          note.textContent =
            "当前会话角色为 readonly — 可以查看已注册列表，注册/候选列表需要 readwrite 及以上会话。";
          note.classList.remove("d-none");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindForm();
    probeAuth()
      .then(loadShops)
      .then(loadCandidates)
      .catch((err) => { showErr(err.message || String(err)); });
  });
})();

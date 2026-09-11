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
(function () {
  "use strict";

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
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------- 已注册店铺 ----------
  function loadShops() {
    return api("/v2/commerce/channel-accounts?platform=tiktok&limit=500")
      .then(function (r) {
        if (r.status === 401) {
          window.location.href = loginUrl(); // pi-lens-ignore: no-open-redirect-js
          return null;
        }
        if (!r.ok) throw new Error("shops HTTP " + r.status);
        return r.json();
      })
      .then(function (shops) {
        if (!shops) return;
        var body = $("#shop-body");
        if (!shops.length) {
          body.innerHTML = '<tr><td colspan="5" class="text-muted">暂无店铺</td></tr>';
          return;
        }
        body.innerHTML = shops.map(function (s) {
          // 同步方式 = shops.data_source 枚举（'api' | 'plugin'）
          var isApi = s.data_source === "api";
          var badge = isApi
            ? '<span class="badge badge-sync-api">API 同步</span>'
            : '<span class="badge badge-sync-plugin">仅插件</span>';
          return "<tr>" +
            '<td class="mono">' + esc(s.shop_id) + "</td>" +
            "<td>" + esc(s.account_name || "—") + "</td>" +
            "<td>" + esc(s.region || "—") + "</td>" +
            "<td>" + esc(s.opened_date || "—") + "</td>" +
            "<td>" + badge + ' <span class="text-muted small">' + esc(s.status || "") + "</span></td>" +
            "</tr>";
        }).join("");
      });
  }

  // ---------- 待注册候选 ----------
  function loadCandidates() {
    return api("/v2/admin/shops/unregistered")
      .then(function (r) {
        if (r.status === 403) {
          $("#cand-body").innerHTML =
            '<tr><td colspan="3" class="text-muted">需要 readwrite 及以上会话查看候选列表</td></tr>';
          return null;
        }
        if (!r.ok) throw new Error("unregistered HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var cands = data.candidates || [];
        $("#cand-count").textContent = cands.length ? "(" + cands.length + ")" : "";
        var body = $("#cand-body");
        if (!cands.length) {
          body.innerHTML = '<tr><td colspan="3" class="text-muted">没有待注册的店铺</td></tr>';
          return;
        }
        body.innerHTML = cands.map(function (c) {
          return "<tr>" +
            '<td class="mono">' + esc(c.shop_id) + "</td>" +
            "<td>" + esc((c.sources || []).join(", ")) + "</td>" +
            '<td><button class="btn btn-sm btn-outline-dark btn-fill" data-shop="' +
              esc(c.shop_id) + '">填入表单</button></td>' +
            "</tr>";
        }).join("");
        body.querySelectorAll(".btn-fill").forEach(function (btn) {
          btn.addEventListener("click", function () {
            $("#f-shop-id").value = btn.getAttribute("data-shop");
            $("#f-shop-id").focus();
          });
        });
      });
  }

  // ---------- 注册提交 ----------
  function bindForm() {
    $("#register-form").addEventListener("submit", function (e) {
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
        .then(function (r) {
          if (r.status === 403) {
            showErr("需要 readwrite 及以上会话才能注册店铺（当前会话角色不足）。");
            return null;
          }
          return r.json().then(function (body) {
            if (!r.ok) {
              var detail = body && body.detail;
              if (Array.isArray(detail)) {
                detail = detail.map(function (d) { return d.msg; }).join("; ");
              }
              throw new Error("注册失败：" + (detail || "HTTP " + r.status));
            }
            return body;
          });
        })
        .then(function (body) {
          if (!body) return;
          $("#f-shop-id").value = "";
          return loadShops().then(loadCandidates);
        })
        .catch(function (err) { showErr(err.message || String(err)); });
    });
  }

  // ---------- 权限提示 ----------
  function probeAuth() {
    return api("/v2/auth/me").then(function (r) {
      if (!r.ok) return;
      return r.json().then(function (me) {
        if (me && me.role === "readonly") {
          var note = $("#auth-note");
          note.textContent =
            "当前会话角色为 readonly — 可以查看已注册列表，注册/候选列表需要 readwrite 及以上会话。";
          note.classList.remove("d-none");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindForm();
    probeAuth()
      .then(loadShops)
      .then(loadCandidates)
      .catch(function (err) { showErr(err.message || String(err)); });
  });
})();

/* 店铺注册台 — /v2/pages/shops
 *
 * 数据源：
 *   GET  /v2/commerce/channel-accounts   已注册店铺（readonly）
 *   GET  /v2/admin/shops/unregistered    插件数据里出现但未注册的 shop_id（admin）
 *   POST /v2/admin/shops/register        人工注册（admin，cookie 会话带 CSRF 头）
 *   PATCH /v2/admin/shops/{shop_pk}      元信息编辑（名称/区域/开店日期/service_id，readwrite+）
 *   GET  /v2/oauth/tiktok/authorize      获取授权链接（readwrite+，format=json）
 *
 * 权限降级：非 admin 会话时 unregistered/register 会 403 —— 页面仍可
 * 只读展示已注册列表，表单区提示需要 admin 登录。
 * 2026-09-28：已注册列表「操作」列新增「获取授权链接」按钮（复用
 * OAuth authorize 端点；有 service_id 的店铺会带上它，callback 后回填）。
 */
(() => {
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";
  var CSRF_HEADER = "tts-erp";

  function $(sel) {
    return document.querySelector(sel);
  }

  function showErr(msg) {
    var el = $("#err");
    el.textContent = msg;
    // HTML 初始隐藏类是 op-hidden（页面 CSS）；d-none 一起清掉防歧义。
    el.classList.remove("d-none");
    el.classList.remove("op-hidden");
  }
  function clearErr() {
    var el = $("#err");
    el.classList.add("d-none");
    el.classList.add("op-hidden");
  }

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

  // ---------- 授权链接 ----------
  function copyToClipboard(text, btn, onDone) {
    function done(ok) {
      btn.textContent = ok ? "已复制" : "复制失败";
      setTimeout(() => { btn.textContent = "复制链接"; }, 1500);
      if (onDone) onDone(ok);
    }
    function legacy() {
      var ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { done(document.execCommand("copy")); } catch (e) { done(false); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => { done(true); },
        legacy,
      );
    } else {
      legacy();
    }
  }

  function renderAuthLink(btn, url, expiresAt) {
    var td = btn.closest("td");
    td.textContent = "";
    var a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "打开授权页";
    var copy = document.createElement("button");
    copy.type = "button";
    copy.className = "btn btn-sm btn-outline-dark ms-1";
    copy.textContent = "复制链接";
    copy.addEventListener("click", () => { copyToClipboard(url, copy); });
    var hint = document.createElement("div");
    hint.className = "text-muted small";
    var exp = expiresAt
      ? expiresAt.replace("T", " ").replace(/\.\d+Z$/, "Z")
      : "";
    var expText = exp ? " · 有效至 " + exp + " UTC" : "";
    hint.textContent = "state 单次使用" + expText;
    td.appendChild(a);
    td.appendChild(copy);
    td.appendChild(hint);
    // 一键化（2026-09-29）：生成成功后立即自动复制，用户无需再点第二次。
    // 注意 transient user activation：fetch 几秒内返回时 clipboard API 仍
    // 视为用户手势；超时/被拒时按钮会显示「复制失败」，用户可手动再点。
    copyToClipboard(url, copy, (ok) => {
      hint.textContent =
        (ok ? "已复制到剪贴板" : "自动复制失败，请点「复制链接」") +
        " · state 单次使用" + expText;
    });
  }

  function fetchAuthLink(btn) {
    clearErr();
    // service_id 点击时从行内单元格实时读（可能被内联编辑改过）；
    // 有值时带给 authorize，callback 后回填到 commerce.shops。
    var row = btn.closest("tr");
    var svcCell = row.querySelector('[data-field="service_id"]');
    var svcId = svcCell ? svcCell.textContent.trim() : "";
    if (svcId === "—") svcId = "";
    btn.disabled = true;
    var path = "/v2/oauth/tiktok/authorize?format=json" +
      (svcId ? "&service_id=" + encodeURIComponent(svcId) : "");
    api(path)
      .then((r) => {
        if (!r) return null;
        if (r.status === 403) {
          showErr("获取授权链接需要 readwrite 及以上会话（当前会话角色不足）。");
          return null;
        }
        return r.json().then((d) => ({ httpOk: r.ok, body: d }));
      })
      .then((res) => {
        if (!res) return;
        var d = res.body || {};
        if (res.httpOk && d.ok && d.authorize_url) {
          renderAuthLink(btn, d.authorize_url, d.state_expires_at);
        } else {
          var detail = d.detail || d.error;
          showErr("获取授权链接失败：" + (detail || "HTTP 错误"));
        }
      })
      .catch((err) => {
        showErr(err.message || String(err));
      })
      .then(() => {
        if (btn.isConnected) btn.disabled = false;
      });
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
          body.innerHTML =
            '<tr><td colspan="7" class="text-muted">暂无店铺</td></tr>';
          return;
        }
        body.innerHTML = shops
          .map((s) => {
            // 同步方式：有 credential_id = 已 OAuth 授权走 API 同步，否则仅插件
            // （2026-09-11 migration 0025 删除了 shops.data_source 枚举列）
            var isApi = s.credential_id != null;
            // 类名必须跟 pages.py shops 页 CSS 对齐（badge-api / badge-plugin；
            // 曾误用 badge-sync-* 导致徽标无底色，2026-09-29 修复）
            var badge = isApi
              ? '<span class="badge badge-api">API 同步</span>'
              : '<span class="badge badge-plugin">仅插件</span>';
            var pk = s.shop_pk || s.id;
            var dateVal = s.opened_date || "";
            var svcId = s.service_id || "";
            return (
              "<tr data-shop-pk=" +
              pk +
              ">" +
              '<td class="mono">' +
              esc(s.shop_id) +
              "</td>" +
              '<td class="editable" data-field="account_name" title="点击修改">' +
              esc(s.account_name || "—") +
              "</td>" +
              '<td class="editable" data-field="region" title="点击修改">' +
              esc(s.region || "—") +
              "</td>" +
              '<td class="editable" data-field="service_id" title="点击修改">' +
              esc(svcId || "—") +
              "</td>" +
              '<td class="editable" data-field="opened_date" title="点击修改">' +
              esc(dateVal || "—") +
              "</td>" +
              "<td>" +
              badge +
              ' <span class="text-muted small">' +
              esc(s.status || "") +
              "</span></td>" +
              '<td><button type="button" class="btn btn-sm btn-outline-dark btn-auth">获取授权链接</button></td>' +
              "</tr>"
            );
          })
          .join("");
        body.querySelectorAll(".btn-auth").forEach((btn) => {
          btn.addEventListener("click", () => fetchAuthLink(btn));
        });
        // 点击 .editable 列进入编辑模式
        body.querySelectorAll(".editable").forEach((td) => {
          td.style.cursor = "pointer";
          td.addEventListener("click", () => {
            if (td.querySelector("input")) return; // 已在编辑
            var row = td.closest("tr");
            var pk = row.getAttribute("data-shop-pk");
            var field = td.getAttribute("data-field");
            var current = td.textContent.trim();
            var input = document.createElement("input");
            input.type = field === "opened_date" ? "date" : "text";
            input.className = "form-control form-control-sm";
            input.value = current === "—" || current === "" ? "" : current;
            input.placeholder = "留空不修改";
            td.textContent = "";
            td.appendChild(input);
            input.focus();
            function save() {
              var raw = input.value.trim();
              var newVal = raw || null;
              td.textContent = newVal || "—";
              if (newVal === current || (newVal === null && current === "—"))
                return;
              var payload = {};
              payload[field] = newVal;
              api("/v2/admin/shops/" + pk, {
                method: "PATCH",
                body: JSON.stringify(payload),
              })
                .then((r) => {
                  if (!r.ok) {
                    showErr("更新失败: HTTP " + r.status);
                    td.textContent = current;
                    return;
                  }
                  return r.json();
                })
                .then((data) => {
                  if (data && data.shop) {
                    td.textContent = data.shop[field] || "—";
                  }
                })
                .catch((err) => {
                  showErr(err.message || String(err));
                  td.textContent = current;
                });
            }
            input.addEventListener("blur", save);
            input.addEventListener("keydown", (e) => {
              if (e.key === "Enter") input.blur();
              if (e.key === "Escape") {
                td.textContent = current;
              }
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
        $("#cand-count").textContent = cands.length
          ? "(" + cands.length + ")"
          : "";
        var body = $("#cand-body");
        if (!cands.length) {
          body.innerHTML =
            '<tr><td colspan="3" class="text-muted">没有待注册的店铺</td></tr>';
          return;
        }
        // pi-lens-ignore: no-unsafe-innerhtml
        body.innerHTML = cands
          .map(
            (c) =>
              "<tr>" +
              '<td class="mono">' +
              esc(c.shop_id) +
              "</td>" +
              "<td>" +
              esc((c.sources || []).join(", ")) +
              "</td>" +
              '<td><button class="btn btn-sm btn-outline-dark btn-fill" data-shop="' +
              esc(c.shop_id) +
              '">填入表单</button></td>' +
              "</tr>",
          )
          .join("");
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
            showErr(
              "需要 readwrite 及以上会话才能注册店铺（当前会话角色不足）。",
            );
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
        .catch((err) => {
          showErr(err.message || String(err));
        });
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
            "当前会话角色为 readonly — 可以查看已注册列表，注册/编辑/候选列表/获取授权链接需要 readwrite 及以上会话。";
          note.classList.remove("d-none");
          note.classList.remove("op-hidden");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindForm();
    probeAuth()
      .then(loadShops)
      .then(loadCandidates)
      .catch((err) => {
        showErr(err.message || String(err));
      });
  });
})();

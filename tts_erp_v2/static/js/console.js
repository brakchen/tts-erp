/* tts-erp operator console — page JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/manual-costs.
   Styling: Bootstrap 5 classes only — no custom stylesheet (2026-08-31).
   Strings: 中文界面 (2026-08-31).
   See tech-doc/procurement-ui-redesign.md §6 for the contract. */

(() => {
  // ---------- constants ----------
  var STORAGE_ACCOUNT_KEY = "mc_active_account";
  var CSRF_HEADER = "tts-erp";
  var TAB_COST = "needs_cost";
  var TAB_PHOTO = "needs_photo";
  var TAB_RECENT = "recent";
  var DEFAULT_LIMIT = 50;

  // Public path prefix: "/tts" behind the NGINX reverse proxy, "" when
  // hitting :9877 directly. Derived from the page URL so every API call
  // and redirect works under both. Same trick as /v2/auth/login's JS.
  // (2026-08-31: absolute /v2/... paths 404'd behind the prefix.)
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  // Same-origin path prefix only ("" or "/tts"); anything else isn't ours.
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  // Bootstrap text classes for per-row status feedback.
  var STATUS_CLASSES = {
    "is-ok": "text-success",
    "is-err": "text-danger",
    "is-saving": "text-secondary",
  };

  // ---------- small helpers ----------
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
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
  // Single choke point for markup writes (linter: one audited site instead
  // of a dozen). Contract: every interpolated value MUST go through esc()
  // first; all static parts are constant strings in this file.
  function html(el, markup) {
    el.innerHTML = markup; // pi-lens-ignore: no-inner-html-js
  }
  // Unwrap API responses that may be either a bare array (legacy) or a
  // { items: [...], total_...: N } envelope (current). 2026-08-31: the
  // backend rolled out the envelope and the JS used to crash with
  // "(items || []).filter is not a function" on the Needs photo tab.
  function unwrap(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    return [];
  }
  function fmtBytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KiB";
    return (n / 1048576).toFixed(2) + " MiB";
  }
  function fmtDate(iso) {
    if (!iso) return "";
    return iso.replace("T", " ").replace(/\.\d+Z$/, "Z");
  }

  // ---------- API surface (cookie auth: withCredentials + CSRF header) ----------
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
    }).then((r) => {
      // 429 from the rate limiter carries Retry-After (seconds). Surface
      // it as a structured field on the error so errorRow() can render a
      // live countdown instead of a generic "重试" link. Without this,
      // rapid clicks on the same tab create a retry storm where every
      // retry attempt also gets 429 and the bucket never drains.
      if (r.status === 429) {
        var raw = r.headers.get("Retry-After");
        var ra = parseInt(raw || "60", 10);
        if (!Number.isFinite(ra) || ra < 1) ra = 60;
        if (ra > 300) ra = 300; // cap so a runaway counter doesn't lock the UI for minutes
        var err = new Error("HTTP 429 \u9650\u6d41\u4e2d");
        err.statusCode = 429;
        err.retryAfter = ra;
        throw err;
      }
      return r;
    });
  }

  function loginUrl() {
    return PREFIX + "/v2/auth/login?next=" + PREFIX + "/v2/pages/manual-costs";
  }

  // ---------- shop switcher ----------
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
        if (!shops) return null;
        var sel = $("#shop-switcher");
        if (!sel) return null;
        var active = parseInt(
          localStorage.getItem(STORAGE_ACCOUNT_KEY) || "0",
          10,
        );
        html(sel, "");
        if (!shops.length) {
          var opt = document.createElement("option");
          opt.textContent = "暂无可用店铺";
          opt.disabled = true;
          opt.selected = true;
          sel.appendChild(opt);
          return null;
        }
        shops.forEach((s) => {
          var opt = document.createElement("option");
          opt.value = String(s.id);
          opt.textContent =
            s.account_name || "#" + s.id + " (" + (s.region || "?") + ")";
          if (s.id === active) opt.selected = true;
          sel.appendChild(opt);
        });
        if (!sel.value && shops[0]) {
          sel.value = String(shops[0].id);
        }
        if (sel.value) {
          localStorage.setItem(STORAGE_ACCOUNT_KEY, sel.value);
        }
        sel.addEventListener("change", () => {
          localStorage.setItem(STORAGE_ACCOUNT_KEY, sel.value);
          refreshActiveTab();
        });
        return parseInt(sel.value, 10) || null;
      });
  }

  function getActiveAccountId() {
    var sel = $("#shop-switcher");
    var raw = sel ? sel.value : localStorage.getItem(STORAGE_ACCOUNT_KEY);
    var id = parseInt(raw || "0", 10);
    return id > 0 ? id : null;
  }

  // ---------- operator identity ----------
  function loadMe() {
    return api("/v2/auth/me")
      .then((r) => {
        if (!r.ok) return null;
        return r.json();
      })
      .then((me) => {
        var el = $("#ops-identity");
        if (!el) return;
        if (me && me.key_prefix) {
          html(
            el,
            "当前操作员：<code>" +
              esc(me.key_prefix) +
              '</code> · <a href="' +
              PREFIX +
              '/v2/auth/logout">退出</a>',
          );
        } else {
          html(el, '<a href="' + loginUrl() + '">登录</a>');
        }
      })
      .catch(() => {
        /* not fatal */
      });
  }

  // ---------- tabs ----------
  var currentTab = TAB_COST;
  var costFilter = "";
  var costOffset = 0;

  function setActiveTab(name) {
    currentTab = name;
    $$(".tab").forEach((btn) => {
      var isActive = btn.getAttribute("data-tab") === name;
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
      btn.classList.toggle("active", isActive); // Bootstrap .nav-link.active
    });
    refreshActiveTab();
  }

  function refreshActiveTab() {
    if (currentTab === TAB_COST) return loadNeedsCost();
    if (currentTab === TAB_PHOTO) return loadNeedsPhoto();
    if (currentTab === TAB_RECENT) return loadRecent();
  }

  // ---------- shared row rendering bits ----------
  function loadingRow() {
    return (
      '<tr><td colspan="6" class="placeholder-glow mb-0">' +
      '<span class="placeholder col-12"></span></td></tr>'
    );
  }
  function emptyRow(text) {
    return (
      '<tr><td colspan="6" class="text-center text-secondary py-5 fst-italic">' +
      text +
      "</td></tr>"
    );
  }
  function errorRow(e, retry) {
    var tbody = $("#grid-rows");
    // 429 path: show a live countdown, then enable the manual retry link
    // once the bucket is expected to have room again. We deliberately do
    // NOT auto-fire the retry — if the user has navigated to a different
    // tab in the meantime, an auto-retry would clobber their current view.
    var ra = e && e.retryAfter;
    if (typeof ra === "number" && ra > 0) {
      var tr = document.createElement("tr");
      tbody.innerHTML = ""; // pi-lens-ignore: no-inner-html-js
      tbody.appendChild(tr);
      var startedAt = Date.now();
      function renderWaiting() {
        html(
          tr,
          '<td colspan="6" class="text-warning small">\u9650\u6d41\u4e2d\uff0c' +
            '<span class="rl-cd">' +
            ra +
            "</span>s \u540e\u53ef\u91cd\u8bd5</td>",
        );
      }
      function renderReady() {
        html(
          tr,
          '<td colspan="6" class="text-secondary small">\u9650\u6d41\u7a7a\u95f2\u00b7' +
            '<a href="#" data-retry>\u70b9\u51fb\u91cd\u8bd5</a></td>',
        );
        tr.querySelector("[data-retry]").addEventListener("click", function (ev) {
          ev.preventDefault();
          retry();
        });
      }
      renderWaiting();
      var iv = setInterval(function () {
        var remain = Math.max(
          0,
          ra - Math.floor((Date.now() - startedAt) / 1000),
        );
        var cd = tr.querySelector(".rl-cd");
        if (cd) cd.textContent = String(remain);
        if (remain <= 0) {
          clearInterval(iv);
          renderReady();
        }
      }, 1000);
      return;
    }
    // Non-429: original "重试" link behaviour.
    html(
      tbody,
      '<tr><td colspan="6" class="text-secondary">\u9519\u8bef\uff1a' +
        esc(e.message) +
        ' \u00b7 <a href="#" data-retry>\u91cd\u8bd5</a></td></tr>',
    );
    tbody.querySelector("[data-retry]").addEventListener("click", (ev) => {
      ev.preventDefault();
      retry();
    });
  }

  // ---------- tab 1: needs cost ----------
  function loadNeedsCost() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    var url =
      "/v2/reporting/missing-cost-products?limit=" +
      DEFAULT_LIMIT +
      "&offset=" +
      costOffset;
    if (acct) url += "&channel_account_id=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        var items = unwrap(payload);
        renderCostRows(items);
        setBadge("badge-cost", items.length);
      })
      .catch((e) => {
        errorRow(e, loadNeedsCost);
      });
  }

  function renderCostRows(items) {
    var tbody = $("#grid-rows");
    html(tbody, "");
    if (!items.length) {
      html(tbody, emptyRow("该店铺所有商品均已填成本并附图。"));
      return;
    }
    items.forEach((it) => {
      var tr = document.createElement("tr");
      tr.dataset.ext = it.external_product_id || "";
      tr.dataset.cpid = it.channel_product_id;
      html(
        tr,
        '<td class="font-monospace small" data-label="SKU">' +
          esc(it.external_product_id || "—") +
          "</td>" +
          '<td data-label="标题">' +
          esc(it.title || "") +
          "</td>" +
          '<td class="font-monospace small text-secondary" data-label="状态">⊘ 缺成本</td>' +
          '<td class="text-end" data-label="单位成本">' +
          '<div class="d-inline-flex align-items-center gap-2">' +
          '<input type="number" class="form-control form-control-sm font-monospace text-end" style="width: 110px" step="0.0001" min="0.0001" data-k="unit_cost" placeholder="0.0000">' +
          '<select class="form-select form-select-sm w-auto font-monospace" data-k="currency">' +
          "<option>USD</option><option>CNY</option><option selected>VND</option><option>EUR</option>" +
          "</select>" +
          "</div>" +
          "</td>" +
          '<td data-label="备注">' +
          '<input type="text" class="form-control form-control-sm" data-k="note" maxlength="500" placeholder="（可选）">' +
          "</td>" +
          '<td data-label="操作">' +
          '<div class="d-inline-flex align-items-center gap-2">' +
          '<button class="btn btn-sm btn-primary" data-act="submit-cost">提交</button>' +
          '<span class="row-status small font-monospace"></span>' +
          "</div>" +
          "</td>",
      );
      tbody.appendChild(tr);
      tr.querySelector('[data-act="submit-cost"]').addEventListener(
        "click",
        () => {
          submitCost(tr);
        },
      );
    });
    applyFilter();
  }

  function submitCost(tr) {
    var inputs = tr.querySelectorAll("input, select");
    var body = { channel_product_external_id: tr.dataset.ext };
    inputs.forEach((i) => {
      body[i.dataset.k] = i.value;
    });
    var unit = parseFloat(body.unit_cost);
    if (!unit || unit <= 0) {
      setRowStatus(tr, "请输入大于 0 的单位成本", "is-err");
      return;
    }
    tr.classList.add("table-active");
    setRowStatus(tr, "保存中…", "is-saving");
    api("/v2/reporting/manual-costs", {
      method: "POST",
      body: JSON.stringify(body),
    })
      .then((r) => {
        if (r.status === 201) return r.json();
        var t = "";
        try {
          return r.text().then((x) => {
            t = x;
            throw new Error("HTTP " + r.status + " · " + t);
          });
        } catch {
          throw new Error("HTTP " + r.status);
        }
      })
      .then(() => {
        fileRow(tr);
      })
      .catch((e) => {
        setRowStatus(tr, "错误：" + e.message, "is-err");
        tr.classList.remove("table-active");
      });
  }

  // ---------- tab 2: needs photo ----------
  function loadNeedsPhoto() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    // Same endpoint shape as tab 1; backend tags with missing_photo flag.
    var url =
      "/v2/reporting/missing-cost-products?limit=" +
      DEFAULT_LIMIT +
      "&offset=0";
    if (acct) url += "&channel_account_id=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        // Server-side count takes precedence when the endpoint provides it
        // (it counts beyond the page limit); otherwise fall back to the
        // filtered client-side count.
        var total =
          payload &&
          typeof payload === "object" &&
          typeof payload.total_missing_photo === "number"
            ? payload.total_missing_photo
            : null;
        var items = unwrap(payload).filter((it) => it.missing_photo);
        renderPhotoRows(items);
        setBadge("badge-photo", total == null ? items.length : total);
      })
      .catch((e) => {
        errorRow(e, loadNeedsPhoto);
      });
  }

  function renderPhotoRows(items) {
    var tbody = $("#grid-rows");
    html(tbody, "");
    if (!items.length) {
      html(tbody, emptyRow("该店铺所有商品均已附图。"));
      return;
    }
    items.forEach((it) => {
      var tr = document.createElement("tr");
      tr.dataset.ext = it.external_product_id || "";
      tr.dataset.cpid = it.channel_product_id;
      tr.dataset.acct = getActiveAccountId() || "";
      html(
        tr,
        '<td class="font-monospace small" data-label="SKU">' +
          esc(it.external_product_id || "—") +
          "</td>" +
          '<td data-label="标题">' +
          esc(it.title || "") +
          "</td>" +
          '<td class="font-monospace small text-secondary" data-label="状态">✚ 仅需附图</td>' +
          '<td data-label="图片" colspan="2">' +
          '<label class="d-inline-block border rounded px-3 py-2 text-secondary small" style="border-style: dashed; cursor: pointer; min-width: 200px" data-act="dropzone">' +
          '📷 拖入图片或点击选择<input type="file" accept="image/*" class="d-none">' +
          "</label>" +
          '<div class="d-flex gap-2 flex-wrap mt-2" data-gallery></div>' +
          "</td>" +
          '<td data-label="状态"><span class="row-status small font-monospace"></span></td>',
      );
      var drop = tr.querySelector('[data-act="dropzone"]');
      var input = drop.querySelector("input");
      input.addEventListener("change", () => {
        if (input.files[0]) uploadPhoto(tr, input.files[0]);
      });
      drop.addEventListener("dragover", (ev) => {
        ev.preventDefault();
        drop.classList.add("border-danger", "text-danger");
      });
      drop.addEventListener("dragleave", () => {
        drop.classList.remove("border-danger", "text-danger");
      });
      drop.addEventListener("drop", (ev) => {
        ev.preventDefault();
        drop.classList.remove("border-danger", "text-danger");
        var f =
          ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (f) uploadPhoto(tr, f);
      });
      tbody.appendChild(tr);
      // load existing photos for this SPU (read-only preview)
      var gallery = tr.querySelector("[data-gallery]");
      api(
        "/v2/spu-images?channel_product_id=" +
          encodeURIComponent(tr.dataset.cpid),
      )
        .then((r) => (r.ok ? r.json() : []))
        .then((list) => {
          (list || []).slice(0, 4).forEach((im) => {
            var img = document.createElement("img");
            img.className = "img-thumbnail";
            img.style.width = "64px";
            img.style.height = "64px";
            img.style.objectFit = "cover";
            img.src = im.url;
            img.alt = esc(im.filename || "");
            img.title =
              esc(im.filename || "") + " · " + fmtBytes(im.size_bytes);
            gallery.appendChild(img);
          });
        })
        .catch(() => {
          /* gallery optional */
        });
    });
    applyFilter();
  }

  function uploadPhoto(tr, file) {
    var acct = parseInt(tr.dataset.acct || "0", 10);
    var cpid = parseInt(tr.dataset.cpid || "0", 10);
    if (!acct || !cpid) {
      setRowStatus(tr, "缺少店铺或商品信息", "is-err");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setRowStatus(tr, "文件超过 8 MiB", "is-err");
      return;
    }
    tr.classList.add("table-active");
    setRowStatus(tr, "申请上传链接…", "is-saving");
    api("/v2/spu-images/upload-url", {
      method: "POST",
      body: JSON.stringify({
        channel_account_id: acct,
        channel_product_id: cpid,
        filename: file.name || "photo.jpg",
        content_type: file.type || "image/jpeg",
        size_bytes: file.size,
      }),
    })
      .then((r) => {
        if (r.status === 201) return r.json();
        return r.text().then((t) => {
          throw new Error("upload-url HTTP " + r.status + " · " + t);
        });
      })
      .then((info) => {
        setRowStatus(tr, "上传到 MinIO…", "is-saving");
        return fetch(info.upload_url, {
          method: "PUT",
          credentials: "include",
          headers: info.required_headers || {
            "Content-Type": file.type || "image/jpeg",
          },
          body: file,
        }).then((upR) => {
          if (!upR.ok) throw new Error("MinIO PUT HTTP " + upR.status);
          return info;
        });
      })
      .then((info) => {
        setRowStatus(tr, "确认中…", "is-saving");
        return api("/v2/spu-images/" + info.image_id + "/confirm", {
          method: "POST",
        });
      })
      .then((r) => {
        if (r.status !== 200)
          return r.text().then((t) => {
            throw new Error("confirm HTTP " + r.status + " · " + t);
          });
        return r.json();
      })
      .then(() => {
        fileRow(tr);
      })
      .catch((e) => {
        setRowStatus(tr, "错误：" + e.message, "is-err");
        tr.classList.remove("table-active");
      });
  }

  // ---------- tab 3: recently filed ----------
  function loadRecent() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    var url = "/v2/reporting/cost-snapshots?limit=" + DEFAULT_LIMIT;
    if (acct) url += "&channel_account_id=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        var items = unwrap(payload);
        renderRecentRows(items);
        setBadge("badge-recent", items.length);
      })
      .catch((e) => {
        errorRow(e, loadRecent);
      });
  }

  function renderRecentRows(items) {
    var tbody = $("#grid-rows");
    html(tbody, "");
    if (!items.length) {
      html(tbody, emptyRow("该店铺暂无提交记录。"));
      return;
    }
    items.forEach((it) => {
      var tr = document.createElement("tr");
      html(
        tr,
        '<td class="font-monospace small" data-label="时间">' +
          esc(fmtDate(it.calculated_at)) +
          "</td>" +
          '<td class="font-monospace small" data-label="渠道商品">' +
          esc(it.channel_product_id) +
          "</td>" +
          '<td data-label="成本方法">' +
          esc(it.cost_method || "—") +
          "</td>" +
          '<td class="font-monospace small text-end" data-label="单位成本">' +
          esc(it.unit_cost) +
          "</td>" +
          '<td class="font-monospace small" data-label="货币">' +
          esc(it.currency || "—") +
          "</td>" +
          '<td class="font-monospace small" data-label="版本">v' +
          esc(it.calculation_version || 1) +
          "</td>",
      );
      tbody.appendChild(tr);
    });
    applyFilter();
  }

  // ---------- shared row state ----------
  function setRowStatus(tr, text, cls) {
    var s = tr.querySelector(".row-status");
    if (!s) return;
    s.textContent = text;
    s.className =
      "row-status small font-monospace " + (STATUS_CLASSES[cls] || "");
  }

  // Client-side row filter for the search box (matches SKU / title text).
  function applyFilter() {
    var q = costFilter;
    $$("#grid-rows tr").forEach((tr) => {
      if (!q) {
        tr.style.display = "";
        return;
      }
      var text = (tr.textContent || "").toLowerCase();
      tr.style.display = text.indexOf(q) === -1 ? "none" : "";
    });
  }

  function setBadge(id, n) {
    var el = document.getElementById(id);
    if (el) el.textContent = String(n);
  }

  function fileRow(tr) {
    setRowStatus(tr, "已提交 ✓", "is-ok");
    var delay =
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 200
        : 600;
    setTimeout(() => {
      tr.style.transition = "opacity 200ms ease";
      tr.style.opacity = "0";
      setTimeout(() => {
        if (tr.parentNode) tr.parentNode.removeChild(tr);
      }, 240);
    }, delay);
  }

  // ---------- boot ----------
  function boot() {
    $$(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        setActiveTab(btn.getAttribute("data-tab"));
      });
    });
    var search = $("#filter-search");
    if (search)
      search.addEventListener("input", () => {
        costFilter = search.value.trim().toLowerCase();
        applyFilter();
      });
    loadShops()
      .then(loadMe)
      .then(refreshActiveTab)
      .catch((e) => {
        // surface auth-misconfig early; the page never silently stays empty
        var main = $("main");
        if (main) {
          var msg = document.createElement("div");
          msg.className = "alert alert-danger mt-3";
          msg.textContent = "无法加载店铺：" + e.message;
          main.appendChild(msg);
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

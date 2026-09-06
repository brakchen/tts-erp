/* tts-erp operator console — page JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/manual-costs.
   Styling: Bootstrap 5 classes only — no custom stylesheet (2026-08-31).
   Strings: 中文界面 (2026-08-31).
   See tech-doc/procurement-ui-redesign.md §6 for the contract. */

(() => {
  // ---------- constants ----------
  var STORAGE_ACCOUNT_KEY = "mc_active_account";
  var CSRF_HEADER = "tts-erp";
  var TAB_PENDING = "pending";
  var TAB_RECENT = "recent";
  var TAB_ALL = "all";
  var DEFAULT_LIMIT = 50;

  // Public path prefix: "/tts" behind the NGINX reverse proxy, "" when
  // hitting :9877 directly. Derived from the page URL so every API call
  // and redirect works under both. Same trick as /v2/auth/login's JS.
  // (2026-08-31: absolute /v2/... paths 404'd behind the prefix.)
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  // Same-origin path prefix only ("" or "/tts"); anything else isn't ours.
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  // Per-row status colour tokens (resolved against the CSS palette).
  var STATUS_CLASSES = {
    "is-ok": "is-ok",
    "is-err": "is-err",
    "is-saving": "is-saving",
    "is-rate-limit": "is-rate-limit",
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
  var currentTab = TAB_PENDING;
  var costFilter = "";
  var costOffset = 0;

  function setActiveTab(name) {
    currentTab = name;
    $$(".op-tab").forEach((btn) => {
      var isActive = btn.getAttribute("data-tab") === name;
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
      btn.classList.toggle("op-tab-active", isActive);
    });
    applyTableHead(name);
    // Submit-all is a pending-tab action — hide it elsewhere.
    var submitAll = $('[data-act="submit-all"]');
    if (submitAll) submitAll.style.display = name === TAB_PENDING ? "" : "none";
    var batchStatus = $(".op-batch-status");
    if (batchStatus)
      batchStatus.style.display = name === TAB_PENDING ? "" : "none";
    refreshActiveTab();
  }

  function refreshActiveTab() {
    if (currentTab === TAB_PENDING) return loadPending();
    if (currentTab === TAB_RECENT) return loadRecent();
    if (currentTab === TAB_ALL) return loadAll();
  }

  // Each tab renders a different column set; swap the <thead> so the
  // header labels always match the rows below.
  var THEAD_BY_TAB = {
    pending:
      '<tr><th scope="col" class="op-th op-th-sku">SKU</th>' +
      '<th scope="col" class="op-th op-th-title">标题</th>' +
      '<th scope="col" class="op-th op-th-cost">单位成本</th>' +
      '<th scope="col" class="op-th op-th-note">备注</th>' +
      '<th scope="col" class="op-th op-th-photo">图片</th>' +
      '<th scope="col" class="op-th op-th-action">操作</th></tr>',
    recent:
      '<tr><th scope="col" class="op-th op-th-sku">时间</th>' +
      '<th scope="col" class="op-th op-th-sku">SKU</th>' +
      '<th scope="col" class="op-th op-th-title">标题</th>' +
      '<th scope="col" class="op-th op-th-cost">单位成本</th>' +
      '<th scope="col" class="op-th op-th-sku">货币</th>' +
      '<th scope="col" class="op-th op-th-note">备注</th></tr>',
    all:
      '<tr><th scope="col" class="op-th op-th-sku">SKU</th>' +
      '<th scope="col" class="op-th op-th-title">标题</th>' +
      '<th scope="col" class="op-th op-th-sku">状态</th>' +
      '<th scope="col" class="op-th op-th-cost">当前成本</th>' +
      '<th scope="col" class="op-th op-th-sku">货币</th>' +
      '<th scope="col" class="op-th op-th-photo">图片</th></tr>',
  };
  function applyTableHead(name) {
    var thead = document.querySelector(".op-table thead");
    if (!thead) return;
    var rows = THEAD_BY_TAB[name];
    if (!rows) return;
    var old = thead.querySelector("tr");
    var fresh = document.createElement("tr");
    fresh.innerHTML = rows; // pi-lens-ignore: no-inner-html-js
    if (old && old.parentNode) old.parentNode.replaceChild(fresh, old);
  }

  // ---------- shared row rendering bits ----------
  function loadingRow() {
    return '<tr><td colspan="6" class="op-loading">加载中…</td></tr>';
  }
  function emptyRow(text) {
    return '<tr><td colspan="6" class="op-empty">' + text + "</td></tr>";
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
          '<td colspan="6" class="op-loading is-rate-limit">\u9650\u6d41\u4e2d\uff0c' +
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
        tr.querySelector("[data-retry]").addEventListener("click", (ev) => {
          ev.preventDefault();
          retry();
        });
      }
      renderWaiting();
      var iv = setInterval(() => {
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
      '<tr><td colspan="6" class="op-error">\u9519\u8bef\uff1a' +
        esc(e.message) +
        ' \u00b7 <a href="#" data-retry>\u91cd\u8bd5</a></td></tr>',
    );
    tbody.querySelector("[data-retry]").addEventListener("click", (ev) => {
      ev.preventDefault();
      retry();
    });
  }

  // ---------- tab 1: pending (cost entry; main-image mirror shown) ----------
  // 2026-09-05 page-rework lane: the photo-upload half of this tab is
  // gone. The row shows the SPU's TikTok main image mirrored into local
  // MinIO (image_url from the backend; fallback icon when the mirror
  // hasn't finished). Each row carries the cost input; submitting costs
  // POSTs /v2/reporting/manual-costs with currency fixed to CNY.
  function loadPending() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    var url =
      "/v2/reporting/missing-cost-products?limit=" +
      DEFAULT_LIMIT +
      "&offset=" +
      costOffset;
    if (acct) url += "&shop_pk=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        var items = unwrap(payload);
        renderPendingRows(items);
        // Server-side total_missing_photo counts beyond the page limit;
        // fall back to page length when the backend omits it.
        var total =
          payload &&
          typeof payload === "object" &&
          typeof payload.total_missing_photo === "number"
            ? payload.total_missing_photo
            : items.length;
        setBadge("badge-pending", total);
        // Signature counter: the oversized mono number at the top
        // of the page. Always reflects the pending queue total —
        // stable across tab switches so the operator's KPI
        // doesn't flicker when they click between 待处理 / 最近提交.
        var counter = $("#op-counter");
        var num = $("#op-counter-num");
        if (num) num.textContent = String(total);
        if (counter) {
          counter.setAttribute("data-state", "ready");
          counter.setAttribute("aria-busy", "false");
        }
      })
      .catch((e) => {
        errorRow(e, loadPending);
      });
  }

  function renderPendingRows(items) {
    var tbody = $("#grid-rows");
    html(tbody, "");
    if (!items.length) {
      html(tbody, emptyRow("该店铺所有商品均已填成本。"));
      return;
    }
    items.forEach((it) => {
      var tr = document.createElement("tr");
      tr.dataset.ext = it.spu_id || "";
      tr.dataset.cpid = it.spu_pk;
      tr.dataset.acct = String(getActiveAccountId() || "");
      html(
        tr,
        '<td class="op-td-sku" data-label="SKU" title="' +
          esc(it.spu_id || "") +
          '">' +
          esc(it.spu_id || "—") +
          "</td>" +
          '<td class="op-td-title" data-label="标题">' +
          esc(it.title || "") +
          "</td>" +
          '<td class="op-td-cost" data-label="单位成本">' +
          '<span class="op-cost-input">' +
          '<input type="number" class="op-input-cost" step="0.0001" min="0.0001" data-k="unit_cost" placeholder="0.0000" aria-label="单位成本">' +
          '<span class="op-currency-fixed" aria-label="货币">CNY</span>' +
          "</span>" +
          "</td>" +
          '<td data-label="备注">' +
          '<input type="text" class="op-input-note" data-k="note" maxlength="500" placeholder="（可选）" aria-label="备注">' +
          "</td>" +
          '<td data-label="图片">' +
          mirrorCellHtml(it) +
          "</td>" +
          '<td class="op-td-action" data-label="操作">' +
          '<button class="op-btn-primary" data-act="submit">提交</button>' +
          '<span class="row-status"></span>' +
          "</td>",
      );
      var zoom = tr.querySelector("[data-zoom]");
      if (zoom) {
        zoom.addEventListener("click", (ev) => {
          ev.preventDefault();
          openLightbox(zoom.getAttribute("data-zoom"));
        });
      }
      bindMirrorErrorFallback(tr);
      tr.querySelector('[data-act="submit"]').addEventListener("click", () => {
        submitPending(tr);
      });
      tbody.appendChild(tr);
    });
    applyFilter();
  }

  // Mirror image cell (2026-09-05 page-rework lane): the operator no
  // longer uploads supplier reference photos. The row shows the SPU's
  // TikTok main image mirrored into local MinIO (resolved server-side to
  // image_url). Missing / not-yet-mirrored rows render a fixed-size
  // fallback box instead of a broken <img>.
  function mirrorCellHtml(it) {
    var url = it.image_url;
    if (url) {
      return (
        '<img class="op-mirror-thumb" data-zoom="' +
        esc(url) +
        '" src="' +
        esc(url) +
        '" alt="" title="点击放大">'
      );
    }
    return '<span class="op-img-fallback" title="主图未同步/镜像未完成"></span>';
  }

  // If a mirror <img> fails to load at render time (presigned URL
  // expired / object missing), swap it for the same fixed-size fallback
  // so the grid never shows a broken image.
  function bindMirrorErrorFallback(tr) {
    var imgs = tr.querySelectorAll("img.op-mirror-thumb");
    imgs.forEach((img) => {
      img.addEventListener("error", () => {
        var holder = document.createElement("span");
        holder.className = "op-img-fallback";
        holder.title = "镜像图加载失败";
        if (img.parentNode) img.parentNode.replaceChild(holder, img);
      });
    });
  }

  // Lightbox: click the row image to open a full-screen overlay; click
  // the overlay background (not the enlarged image itself, nor × / Esc)
  // to close. One shared overlay per page.
  var _lightbox = null;
  function openLightbox(url) {
    if (!url) return;
    if (!_lightbox) {
      _lightbox = document.createElement("div");
      _lightbox.className = "op-lightbox";
      // Build children via DOM API — no innerHTML (static template, and
      // keeps the audit surface free of innerHTML sinks).
      var close = document.createElement("button");
      close.type = "button";
      close.className = "op-lightbox-close";
      close.setAttribute("aria-label", "关闭");
      close.textContent = "×";
      var lightImg = document.createElement("img");
      lightImg.alt = "";
      _lightbox.appendChild(close);
      _lightbox.appendChild(lightImg);
      // Only a click on the backdrop closes — a click on the enlarged
      // image itself stops propagation so the operator can pan/zoom
      // without accidentally dismissing the preview.
      _lightbox.addEventListener("click", (ev) => {
        if (ev.target === _lightbox) closeLightbox();
      });
      lightImg.addEventListener("click", (ev) => {
        ev.stopPropagation();
      });
      close.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeLightbox();
      });
      document.body.appendChild(_lightbox);
    }
    _lightbox.querySelector("img").src = url;
    _lightbox.classList.add("is-open");
    document.addEventListener("keydown", _lightboxEsc);
  }
  function closeLightbox() {
    if (!_lightbox) return;
    _lightbox.classList.remove("is-open");
    _lightbox.querySelector("img").src = "";
    document.removeEventListener("keydown", _lightboxEsc);
  }
  function _lightboxEsc(ev) {
    if (ev.key === "Escape") closeLightbox();
  }

  function submitPending(tr) {
    // Single-row submit: reuse the shared cost POST but keep the
    // row-level status semantics (errors stay on the row).
    return postManualCost(tr).catch(() => {
      /* status already set on the row */
    });
  }

  // POST one row's manual cost. Resolves on success (row is filed);
  // rejects when the row has no valid unit cost or the server errors —
  // the caller decides how to surface the failure (single-row keeps it
  // on the row; submit-all aggregates).
  function postManualCost(tr) {
    var inputs = tr.querySelectorAll("input[data-k]");
    var body = { spu_id: tr.dataset.ext, currency: "CNY" };
    inputs.forEach((i) => {
      body[i.dataset.k] = i.value;
    });
    var unit = parseFloat(body.unit_cost);
    if (!unit || unit <= 0) {
      setRowStatus(tr, "请输入大于 0 的单位成本", "is-err");
      return Promise.reject(new Error("no cost"));
    }
    tr.classList.add("table-active");
    setRowStatus(tr, "保存中…", "is-saving");
    // 2026-09-05 page-rework lane: manual-costs only. The photo upload
    // flow (spu-images upload-url/PUT/confirm) is gone — the page now
    // renders the TikTok main image from its MinIO mirror instead.
    return api("/v2/reporting/manual-costs", {
      method: "POST",
      body: JSON.stringify(body),
    })
      .then((r) => {
        if (r.status === 201) return r.json();
        return r.text().then((t) => {
          throw new Error("成本 HTTP " + r.status + " · " + t);
        });
      })
      .then(() => {
        fileRow(tr);
      })
      .catch((e) => {
        var msg = e && e.message ? e.message : String(e);
        setRowStatus(tr, "错误：" + msg, "is-err");
        tr.classList.remove("table-active");
        throw e;
      });
  }

  // Submit every visible pending row that has a valid unit cost, one
  // after another. Serial (not Promise.all) so a page of 100 rows does
  // not fire 100 parallel POSTs into the per-key rate-limit bucket.
  // Rows without a cost are left for the operator to fill; the summary
  // reports how many were filed vs skipped vs failed.
  function submitAllPending() {
    var rows = $$("#grid-rows tr[data-ext]");
    var targets = rows.filter((tr) => {
      var input = tr.querySelector('input[data-k="unit_cost"]');
      var unit = input ? parseFloat(input.value) : NaN;
      return !!unit && unit > 0;
    });
    var skipped = rows.length - targets.length;
    if (!targets.length) {
      var banner = $(".op-batch-status");
      if (banner) {
        banner.textContent = "没有已填写成本的待提交行（先填写单位成本）";
        banner.classList.add("is-err");
      }
      return;
    }
    var btn = $('[data-act="submit-all"]');
    if (btn) btn.disabled = true;
    var filed = 0;
    var failed = 0;
    var chain = Promise.resolve();
    targets.forEach((tr) => {
      chain = chain
        .then(() => postManualCost(tr))
        .then(() => {
          filed += 1;
        })
        .catch(() => {
          failed += 1;
        });
    });
    chain.then(() => {
      if (btn) btn.disabled = false;
      var banner = $(".op-batch-status");
      if (!banner) return;
      var msg =
        "已提交 " +
        filed +
        " 行" +
        (skipped ? " · 跳过 " + skipped + " 行（未填成本）" : "") +
        (failed ? " · 失败 " + failed + " 行" : "");
      banner.textContent = msg;
      banner.classList.toggle("is-err", failed > 0);
      banner.classList.toggle("is-ok", filed > 0 && failed === 0);
    });
  }
  // ---------- tab 3: recently filed ----------
  function loadRecent() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    // 2026-09-06: recently-filed now reads GET /v2/reporting/manual-costs
    // (the truth table) so a fresh submission shows immediately — the old
    // cost-snapshots source is recomputed every 6 h and stayed empty
    // right after a manual entry.
    var url = "/v2/reporting/manual-costs?limit=" + DEFAULT_LIMIT;
    if (acct) url += "&shop_pk=" + acct;
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
      // manual-costs rows: created_at / spu_id / title / unit_cost /
      // currency / note (see GET /v2/reporting/manual-costs).
      html(
        tr,
        '<td class="op-td-sku" data-label="时间">' +
          esc(fmtDate(it.created_at)) +
          "</td>" +
          '<td class="op-td-sku" data-label="SKU" title="' +
          esc(it.spu_id || "") +
          '">' +
          esc(it.spu_id || "—") +
          "</td>" +
          '<td class="op-td-title" data-label="标题">' +
          esc(it.title || "") +
          "</td>" +
          '<td class="op-td-cost" data-label="单位成本">' +
          esc(it.unit_cost) +
          "</td>" +
          '<td class="op-td-sku" data-label="货币">' +
          esc(it.currency || "—") +
          "</td>" +
          '<td data-label="备注">' +
          esc(it.note || "") +
          "</td>",
      );
      tbody.appendChild(tr);
    });
    applyFilter();
  }

  // ---------- tab 3: all SPUs (read-only catalogue) ----------
  function loadAll() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    var url = "/v2/commerce/channel-products?limit=" + DEFAULT_LIMIT;
    if (acct) url += "&shop_pk=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((items) => {
        renderAllRows(items);
        setBadge("badge-all", items.length);
      })
      .catch((e) => {
        errorRow(e, loadAll);
      });
  }

  function renderAllRows(items) {
    var tbody = $("#grid-rows");
    html(tbody, "");
    if (!items.length) {
      html(tbody, emptyRow("该店铺暂无 SPU。"));
      return;
    }
    items.forEach((it) => {
      var tr = document.createElement("tr");
      var costText = it.unit_cost == null ? "缺" : it.unit_cost;
      html(
        tr,
        '<td class="op-td-sku" data-label="SKU" title="' +
          esc(it.spu_id || "") +
          '">' +
          esc(it.spu_id || "—") +
          "</td>" +
          '<td class="op-td-title" data-label="标题">' +
          esc(it.title || "") +
          "</td>" +
          '<td class="op-td-sku" data-label="状态">' +
          esc(it.status || "—") +
          "</td>" +
          '<td class="op-td-cost" data-label="当前成本">' +
          esc(costText) +
          "</td>" +
          '<td class="op-td-sku" data-label="货币">' +
          esc(it.currency || "—") +
          "</td>" +
          '<td data-label="图片">' +
          mirrorCellHtml(it) +
          "</td>",
      );
      bindMirrorErrorFallback(tr);
      var zoom = tr.querySelector("[data-zoom]");
      if (zoom) {
        zoom.addEventListener("click", (ev) => {
          ev.preventDefault();
          openLightbox(zoom.getAttribute("data-zoom"));
        });
      }
      tbody.appendChild(tr);
    });
    applyFilter();
  }

  // ---------- shared row state ----------
  function setRowStatus(tr, text, cls) {
    var s = tr.querySelector(".row-status");
    if (!s) return;
    s.textContent = text;
    s.className = "row-status " + (STATUS_CLASSES[cls] || "");
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
    // Buttons carry class="op-tab" (not ".tab") — this selector bug meant
    // tab clicks were never bound (2026-09-06 report: recent/all tabs
    // unclickable).
    $$(".op-tab").forEach((btn) => {
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
    var submitAll = $('[data-act="submit-all"]');
    if (submitAll)
      submitAll.addEventListener("click", () => {
        if (currentTab === TAB_PENDING) submitAllPending();
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

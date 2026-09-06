/* tts-erp operator console — page JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/manual-costs.
   Styling: Bootstrap 5 classes only — no custom stylesheet (2026-08-31).
   Strings: 中文界面 (2026-08-31).
   See tech-doc/procurement-ui-redesign.md §6 for the contract. */

(() => {
  // ---------- constants ----------
  var STORAGE_ACCOUNT_KEY = "mc_active_account";
  var CSRF_HEADER = "tts-erp";
  var TAB_RECENT = "recent";
  var TAB_ALL = "all";
  var DEFAULT_LIMIT = 50;

  // TikTok products_spu.status → operator-facing Chinese label
  // (2026-09-06 status column). Centralised so the wording is one edit
  // away. Only the enum values seen in production are mapped; unknown
  // values fall through to the raw code.
  var STATUS_LABELS = {
    ACTIVATE: "在售",
    DELETED: "下架",
    SELLER_DEACTIVATED: "停售",
  };
  // Filter options for the status dropdown: '' = all, otherwise the raw
  // upstream code (the backend channel-products filter expects the raw
  // value, not the Chinese label). Populated into #filter-status at boot
  // so the labels live in exactly one place (STATUS_LABELS).
  var STATUS_ORDER = ["ACTIVATE", "DELETED", "SELLER_DEACTIVATED"];
  function statusLabel(raw) {
    return STATUS_LABELS[raw] || raw || "—";
  }
  function populateStatusFilter(select) {
    if (!select) return;
    var frag = document.createDocumentFragment();
    var all = document.createElement("option");
    all.value = "";
    all.textContent = "全部状态";
    frag.appendChild(all);
    STATUS_ORDER.forEach((code) => {
      var opt = document.createElement("option");
      opt.value = code;
      opt.textContent = STATUS_LABELS[code];
      frag.appendChild(opt);
    });
    select.appendChild(frag);
  }

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

  // Short date for the catalogue's created / updated columns
  // (source timestamps are UTC; the operator's local timezone is what
  // the browser renders, so just clip the full fmtDate to date+time).
  function fmtShortDate(iso) {
    var d = fmtDate(iso);
    if (!d) return "—";
    return d;
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
  var currentTab = TAB_ALL;
  var costFilter = "";
  // All-SPU catalogue sort state (2026-09-06): the active sort column
  // + direction. Column headers carry data-sort; clicking toggles asc→
  // desc→(reload). Default = status asc so in-sale (在售/ACTIVATE)
  // products lead the catalogue; the operator can re-sort by clicking
  // any sortable header.
  var catalogueSort = { key: "status", order: "asc" };
  // Status dropdown filter (2026-09-06): '' = all statuses, otherwise
  // the raw upstream code forwarded as ?status= on channel-products.
  var catalogueStatus = "";

  function setActiveTab(name) {
    currentTab = name;
    $$(".op-tab").forEach((btn) => {
      var isActive = btn.getAttribute("data-tab") === name;
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
      btn.classList.toggle("op-tab-active", isActive);
    });
    // Counter + toolbar copy follow the tab: all = catalogue count,
    // recent = change-log count.
    var label = $("#op-counter-label");
    var sub = $("#op-counter-sub");
    if (label) label.textContent = name === TAB_RECENT ? "最近提交" : "全部 SPU";
    if (sub)
      sub.textContent =
        name === TAB_RECENT
          ? "成本变更记录 · 每次提交一条"
          : "目录 · 编辑成本后提交全部";
    var stamp = $(".op-counter-stamp");
    if (stamp)
      stamp.textContent = name === TAB_RECENT ? "CHANGELOG · N/M" : "CATALOG · ALL";
    // Submit-all is an all-tab action (files the edited rows).
    var submitAll = $('[data-act="submit-all"]');
    if (submitAll) submitAll.style.display = name === TAB_ALL ? "" : "none";
    var batchStatus = $(".op-batch-status");
    if (batchStatus)
      batchStatus.style.display = name === TAB_ALL ? "" : "none";
    applyTableHead(name);
    refreshActiveTab();
  }

  function refreshActiveTab() {
    if (currentTab === TAB_RECENT) return loadRecent();
    return loadAll();
  }

  // Each tab renders a different column set; swap the <thead> so the
  // header labels always match the rows below.
  var THEAD_BY_TAB = {
    // All-SPU catalogue (2026-09-06): SKU / title / status / editable
    // cost / created / updated / image. Sort arrows live on the
    // sortable columns; the active one is marked is-sorted-* by
    // renderAllRows' caller.
    all:
      '<tr><th scope="col" class="op-th op-th-sku">SKU</th>' +
      '<th scope="col" class="op-th op-th-title">标题</th>' +
      '<th scope="col" class="op-th op-th-sku op-th-sortable" data-sort="status" title="按状态排序">状态<span class="op-sort-arrow"></span></th>' +
      '<th scope="col" class="op-th op-th-cost op-th-sortable" data-sort="unit_cost" title="按成本价排序">成本<span class="op-sort-arrow"></span></th>' +
      '<th scope="col" class="op-th op-th-sku op-th-sortable" data-sort="created_at" title="按创建时间排序">创建<span class="op-sort-arrow"></span></th>' +
      '<th scope="col" class="op-th op-th-sku op-th-sortable" data-sort="updated_at" title="按更新时间排序">更新<span class="op-sort-arrow"></span></th>' +
      '<th scope="col" class="op-th op-th-photo">图片</th></tr>',
    // Change log (2026-09-06): one row per cost submission, showing
    // what price it replaced. First submission for a SPU has no
    // predecessor (prev —).
    recent:
      '<tr><th scope="col" class="op-th op-th-sku">变更时间</th>' +
      '<th scope="col" class="op-th op-th-sku">SKU</th>' +
      '<th scope="col" class="op-th op-th-title">标题</th>' +
      '<th scope="col" class="op-th op-th-cost">变更前</th>' +
      '<th scope="col" class="op-th op-th-cost">变更后</th>' +
      '<th scope="col" class="op-th op-th-sku">货币</th>' +
      '<th scope="col" class="op-th op-th-note">备注</th></tr>',
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
    bindSortableHeaders(fresh);
  }

  // Catalogue sort headers (2026-09-06): clicking a sortable th cycles
  // asc → desc → asc… for that column and reloads the all tab. The
  // active column is marked is-sorted-* so the operator sees which way
  // the rows are ordered.
  function bindSortableHeaders(tr) {
    if (!tr || currentTab !== TAB_ALL) return;
    $$(".op-th-sortable", tr).forEach((th) => {
      var key = th.getAttribute("data-sort");
      if (!key) return;
      var isActive = catalogueSort.key === key;
      th.classList.toggle("is-sorted-asc", isActive && catalogueSort.order === "asc");
      th.classList.toggle(
        "is-sorted-desc",
        isActive && catalogueSort.order === "desc",
      );
      th.addEventListener("click", () => {
        if (catalogueSort.key === key) {
          catalogueSort.order =
            catalogueSort.order === "asc" ? "desc" : "asc";
        } else {
          catalogueSort.key = key;
          catalogueSort.order = "desc";
        }
        loadAll();
      });
    });
  }

  // ---------- shared row rendering bits ----------
  function loadingRow() {
    return '<tr><td colspan="7" class="op-loading">加载中…</td></tr>';
  }
  function emptyRow(text) {
    return '<tr><td colspan="7" class="op-empty">' + text + "</td></tr>";
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
          '<td colspan="7" class="op-loading is-rate-limit">\u9650\u6d41\u4e2d\uff0c' +
            '<span class="rl-cd">' +
            ra +
            "</span>s \u540e\u53ef\u91cd\u8bd5</td>",
        );
      }
      function renderReady() {
        html(
          tr,
          '<td colspan="7" class="text-secondary small">\u9650\u6d41\u7a7a\u95f2\u00b7' +
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
      '<tr><td colspan="7" class="op-error">\u9519\u8bef\uff1a' +
        esc(e.message) +
        ' \u00b7 <a href="#" data-retry>\u91cd\u8bd5</a></td></tr>',
    );
    tbody.querySelector("[data-retry]").addEventListener("click", (ev) => {
      ev.preventDefault();
      retry();
    });
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

  function postManualCost(tr) {
    var inputs = tr.querySelectorAll("input[data-k]");
    // Catalogue rows keep their existing currency (dataset.origCurrency,
    // set at render); brand-new rows (no prior cost) file as CNY — the
    // page's fixed entry currency.
    var body = {
      spu_id: tr.dataset.ext,
      currency: tr.dataset.origCurrency || "CNY",
    };
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
        fileRow(tr, /* keepRow */ true);
      })
      .catch((e) => {
        var msg = e && e.message ? e.message : String(e);
        setRowStatus(tr, "错误：" + msg, "is-err");
        tr.classList.remove("table-active");
        throw e;
      });
  }

  // Submit every edited catalogue row that has a valid unit cost, one
  // after another. Serial (not Promise.all) so a page of 100 rows does
  // not fire 100 parallel POSTs into the per-key rate-limit bucket.
  // Only rows the operator actually changed (is-dirty) are filed; a row
  // whose input still equals its server value is left alone. The
  // summary reports how many were filed vs skipped vs failed.
  function submitAllEdited() {
    var rows = $$("#grid-rows tr[data-ext]");
    var targets = rows.filter((tr) => tr.classList.contains("is-dirty"));
    if (!targets.length) {
      var banner = $(".op-batch-status");
      if (banner) {
        banner.textContent = "没有已编辑的行（先改成本，再提交全部）";
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
        (failed ? " · 失败 " + failed + " 行" : "");
      banner.textContent = msg;
      banner.classList.toggle("is-err", failed > 0);
      banner.classList.toggle("is-ok", filed > 0 && failed === 0);
      if (failed === 0) {
        // Everything filed — refresh so 最近提交 shows the changes and
        // the catalogue reflects the new effective costs.
        loadAll();
      }
    });
  }
  // ---------- tab: change log (最近提交) ----------
  // Each row is one cost submission, showing what price it replaced.
  // 2026-09-06: reads GET /v2/reporting/manual-costs (the truth table —
  // manual_product_costs) so a fresh submission shows immediately; the
  // backend pairs each row with the SPU's previous effective price via a
  // LAG window, so the UI renders 变更前 → 变更后.
  function loadRecent() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
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
        setCounterNum(items.length);
        setCounterReady();
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
      var prevTxt =
        it.prev_unit_cost == null ? "—" : esc(it.prev_unit_cost);
      var curTxt = esc(it.unit_cost);
      html(
        tr,
        '<td class="op-td-sku" data-label="变更时间" title="' +
          esc(it.created_at || "") +
          '">' +
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
          '<td class="op-td-cost" data-label="变更前">' +
          prevTxt +
          "</td>" +
          '<td class="op-td-cost op-td-new-cost" data-label="变更后">' +
          curTxt +
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

  // ---------- tab: 全部 SPU (editable catalogue) ----------
  // Reads GET /v2/commerce/channel-products with the active sort. Each
  // row's cost cell is an editable input pre-filled with the current
  // effective cost; editing marks the row is-dirty and 提交全部 files
  // every dirty row (POST /v2/reporting/manual-costs, CNY).
  function loadAll() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    html(tbody, loadingRow());
    var url =
      "/v2/commerce/channel-products?limit=" +
      DEFAULT_LIMIT +
      "&sort=" +
      encodeURIComponent(catalogueSort.key) +
      "&order=" +
      encodeURIComponent(catalogueSort.order);
    if (catalogueStatus) url += "&status=" + encodeURIComponent(catalogueStatus);
    if (acct) url += "&shop_pk=" + acct;
    api(url)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        var items = unwrap(payload);
        renderAllRows(items);
        setBadge("badge-all", items.length);
        setCounterNum(items.length);
        setCounterReady();
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
      // spu_id doubles as the POST key (the backend resolves it to the
      // internal pk); we also stash the SPU's current effective cost so
      // the dirty check can compare the edited value against it.
      tr.dataset.ext = it.spu_id || "";
      tr.dataset.origCost = it.unit_cost == null ? "" : String(it.unit_cost);
      tr.dataset.origCurrency = it.currency || "CNY";
      var currency = it.currency || "CNY";
      var costCell;
      // New row (no cost yet): CNY placeholder, empty input.
      if (it.unit_cost == null) {
        costCell =
          '<span class="op-cost-input" title="输入成本后点提交全部">' +
          '<input type="number" class="op-input-cost" step="0.0001" min="0.0001" data-k="unit_cost" placeholder="缺" aria-label="单位成本">' +
          '<span class="op-currency-fixed" aria-label="货币">CNY</span>' +
          "</span>";
      } else {
        // Existing cost: pre-filled input + its currency badge. Editing
        // the number re-files under the SAME currency (the operator is
        // correcting a value, not changing units). CNY is the page's
        // fixed entry currency, so an existing VND/… row keeps its own.
        costCell =
          '<span class="op-cost-input" title="编辑成本后点提交全部">' +
          '<input type="number" class="op-input-cost" step="0.0001" min="0.0001" data-k="unit_cost" value="' +
          esc(String(it.unit_cost)) +
          '" aria-label="单位成本">' +
          '<span class="op-currency-fixed" aria-label="货币">' +
          esc(currency) +
          "</span>" +
          "</span>";
      }
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
          esc(statusLabel(it.status)) +
          "</td>" +
          '<td class="op-td-cost" data-label="成本">' +
          costCell +
          '<span class="row-status" aria-live="polite"></span>' +
          "</td>" +
          '<td class="op-td-sku" data-label="创建" title="' +
          esc(it.source_created_at || "") +
          '">' +
          esc(fmtShortDate(it.source_created_at)) +
          "</td>" +
          '<td class="op-td-sku" data-label="更新" title="' +
          esc(it.source_updated_at || "") +
          '">' +
          esc(fmtShortDate(it.source_updated_at)) +
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
      // Dirty tracking: any change to the cost input lights the row up
      // and makes it eligible for 提交全部.
      var input = tr.querySelector('input[data-k="unit_cost"]');
      if (input) {
        input.addEventListener("input", () => {
          var raw = input.value.trim();
          var changed = raw !== tr.dataset.origCost;
          tr.classList.toggle("is-dirty", changed);
          if (changed) {
            tr.classList.add("table-active");
          } else {
            tr.classList.remove("table-active");
          }
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

  // Signature counter helpers: the oversized mono number at the top of
  // the page reflects the ACTIVE tab's row count (catalogue SPUs on
  // 全部 SPU, change-log rows on 最近提交) — it stays stable across
  // tab switches so the operator's read of the page doesn't flicker.
  function setCounterNum(n) {
    var num = $("#op-counter-num");
    if (num) num.textContent = String(n);
  }
  function setCounterReady() {
    var counter = $("#op-counter");
    if (!counter) return;
    counter.setAttribute("data-state", "ready");
    counter.setAttribute("aria-busy", "false");
  }

  function fileRow(tr, keepRow) {
    setRowStatus(tr, "已提交 ✓", "is-ok");
    if (keepRow) {
      // Catalogue mode: the row STAYS (the SPU is still part of the
      // directory). Rebase the dirty marker onto the just-filed value so
      // a second identical submit-all pass has nothing to do, and drop
      // the editing highlight.
      var input = tr.querySelector('input[data-k="unit_cost"]');
      if (input) tr.dataset.origCost = input.value.trim();
      tr.classList.remove("is-dirty", "table-active");
      setTimeout(() => {
        var s = tr.querySelector(".row-status");
        if (s) s.textContent = "";
      }, 1800);
      return;
    }
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
        if (currentTab === TAB_ALL) submitAllEdited();
      });
    var statusFilter = $("#filter-status");
    if (statusFilter) {
      // Populate the dropdown from STATUS_LABELS so mapping + options
      // stay in one place; then bind change → reload all tab.
      populateStatusFilter(statusFilter);
      statusFilter.addEventListener("change", () => {
        catalogueStatus = statusFilter.value;
        if (currentTab === TAB_ALL) loadAll();
      });
    }
    loadShops()
      .then(loadMe)
      .then(() => {
        // The static HTML already marks 全部 SPU active, but the <thead>
        // must be swapped to the JS column set + sort bindings attached;
        // setActiveTab handles both and fires the first load.
        setActiveTab(TAB_ALL);
      })
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

/* tts-erp operator console — page JS.
   No frameworks. Plain DOM + fetch. Wired from /v2/pages/manual-costs.
   See tech-doc/procurement-ui-redesign.md §6 for the contract. */

(function () {
  "use strict";

  // ---------- constants ----------
  var STORAGE_ACCOUNT_KEY = "mc_active_account";
  var CSRF_HEADER = "tts-erp";
  var TAB_COST = "needs_cost";
  var TAB_PHOTO = "needs_photo";
  var TAB_RECENT = "recent";
  var DEFAULT_LIMIT = 50;

  // ---------- small helpers ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
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
      headers["X-Requested-With"] = CSRF_HEADER;  // CSRF guard
    }
    if (opts.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(path, {
      method: opts.method || "GET",
      credentials: "include",  // session cookie
      headers: headers,
      body: opts.body
    });
  }

  // ---------- shop switcher ----------
  function loadShops() {
    return api("/v2/commerce/channel-accounts?platform=tiktok&limit=500")
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/v2/auth/login?next=/v2/pages/manual-costs"; return null; }
        if (!r.ok) throw new Error("shops HTTP " + r.status);
        return r.json();
      })
      .then(function (shops) {
        if (!shops) return null;
        var sel = $("#shop-switcher");
        if (!sel) return null;
        var active = parseInt(localStorage.getItem(STORAGE_ACCOUNT_KEY) || "0", 10);
        sel.innerHTML = "";
        if (!shops.length) {
          var opt = document.createElement("option");
          opt.textContent = "no shops available";
          opt.disabled = true; opt.selected = true;
          sel.appendChild(opt);
          return null;
        }
        shops.forEach(function (s) {
          var opt = document.createElement("option");
          opt.value = String(s.id);
          opt.textContent = s.account_name || ("#" + s.id + " (" + (s.region || "?") + ")");
          if (s.id === active) opt.selected = true;
          sel.appendChild(opt);
        });
        if (!sel.value && shops[0]) {
          sel.value = String(shops[0].id);
        }
        if (sel.value) {
          localStorage.setItem(STORAGE_ACCOUNT_KEY, sel.value);
        }
        sel.addEventListener("change", function () {
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
    return api("/v2/auth/me").then(function (r) {
      if (!r.ok) return null;
      return r.json();
    }).then(function (me) {
      var el = $("#ops-identity");
      if (!el) return;
      if (me && me.key_prefix) {
        el.innerHTML = "ops: <code>" + esc(me.key_prefix) + "</code> · <a class=\"logout\" href=\"/v2/auth/logout\">logout</a>";
      } else {
        el.innerHTML = "<a href=\"/v2/auth/login?next=/v2/pages/manual-costs\">log in</a>";
      }
    }).catch(function () { /* not fatal */ });
  }

  // ---------- tabs ----------
  var currentTab = TAB_COST;
  var costFilter = "";
  var costOffset = 0;

  function setActiveTab(name) {
    currentTab = name;
    $$(".tab").forEach(function (btn) {
      var isActive = btn.getAttribute("data-tab") === name;
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    refreshActiveTab();
  }

  function refreshActiveTab() {
    if (currentTab === TAB_COST) return loadNeedsCost();
    if (currentTab === TAB_PHOTO) return loadNeedsPhoto();
    if (currentTab === TAB_RECENT) return loadRecent();
  }

  // ---------- tab 1: needs cost ----------
  function loadNeedsCost() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    tbody.innerHTML = '<tr><td colspan="6" class="shimmer">loading…</td></tr>';
    var url = "/v2/reporting/missing-cost-products?limit=" + DEFAULT_LIMIT + "&offset=" + costOffset;
    if (acct) url += "&channel_account_id=" + acct;
    api(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (items) {
      renderCostRows(items || []);
      setBadge("badge-cost", (items || []).length);
    }).catch(function (e) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">error: ' + esc(e.message) + ' · <a href="#" data-retry>retry</a></td></tr>';
      tbody.querySelector("[data-retry]").addEventListener("click", function (ev) { ev.preventDefault(); loadNeedsCost(); });
    });
  }

  function renderCostRows(items) {
    var tbody = $("#grid-rows");
    tbody.innerHTML = "";
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">All SPUs in this shop have a cost and a photo. Nice.</td></tr>';
      return;
    }
    items.forEach(function (it) {
      var tr = document.createElement("tr");
      tr.dataset.ext = it.external_product_id || "";
      tr.dataset.cpid = it.channel_product_id;
      tr.innerHTML =
        '<td class="sku" data-label="SKU">' + esc(it.external_product_id || "—") + '</td>' +
        '<td data-label="Title">' + esc(it.title || "") + '</td>' +
        '<td class="code" data-label="State">⊘ no cost</td>' +
        '<td class="money" data-label="Unit cost">' +
          '<div class="row-controls">' +
            '<input type="number" step="0.0001" min="0.0001" data-k="unit_cost" placeholder="0.0000">' +
            '<select data-k="currency">' +
              '<option>USD</option><option>CNY</option><option selected>VND</option><option>EUR</option>' +
            '</select>' +
          '</div>' +
        '</td>' +
        '<td data-label="Note">' +
          '<div class="row-controls"><input type="text" data-k="note" maxlength="500" placeholder="optional"></div>' +
        '</td>' +
        '<td data-label="Action">' +
          '<div class="row-controls">' +
            '<button class="btn primary" data-act="submit-cost">Submit</button>' +
            '<span class="row-status"></span>' +
          '</div>' +
        '</td>';
      tbody.appendChild(tr);
      tr.querySelector('[data-act="submit-cost"]').addEventListener("click", function () { submitCost(tr); });
    });
  }

  function submitCost(tr) {
    var inputs = tr.querySelectorAll("input, select");
    var body = { channel_product_external_id: tr.dataset.ext };
    inputs.forEach(function (i) { body[i.dataset.k] = i.value; });
    var unit = parseFloat(body.unit_cost);
    if (!unit || unit <= 0) {
      setRowStatus(tr, "enter unit cost > 0", "is-err");
      return;
    }
    tr.classList.add("is-filing");
    setRowStatus(tr, "saving…", "is-saving");
    api("/v2/reporting/manual-costs", { method: "POST", body: JSON.stringify(body) })
      .then(function (r) {
        if (r.status === 201) return r.json();
        var t = "";
        try { return r.text().then(function (x) { t = x; throw new Error("HTTP " + r.status + " · " + t); }); }
        catch (e) { throw new Error("HTTP " + r.status); }
      })
      .then(function () { fileRow(tr); })
      .catch(function (e) { setRowStatus(tr, "err " + e.message, "is-err"); tr.classList.remove("is-filing"); });
  }

  // ---------- tab 2: needs photo ----------
  function loadNeedsPhoto() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    tbody.innerHTML = '<tr><td colspan="6" class="shimmer">loading…</td></tr>';
    // Same endpoint shape as tab 1; backend tags with missing_photo flag.
    var url = "/v2/reporting/missing-cost-products?limit=" + DEFAULT_LIMIT + "&offset=0";
    if (acct) url += "&channel_account_id=" + acct;
    api(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (items) {
      var onlyPhoto = (items || []).filter(function (it) { return it.missing_photo; });
      renderPhotoRows(onlyPhoto);
      setBadge("badge-photo", onlyPhoto.length);
    }).catch(function (e) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">error: ' + esc(e.message) + ' · <a href="#" data-retry>retry</a></td></tr>';
      tbody.querySelector("[data-retry]").addEventListener("click", function (ev) { ev.preventDefault(); loadNeedsPhoto(); });
    });
  }

  function renderPhotoRows(items) {
    var tbody = $("#grid-rows");
    tbody.innerHTML = "";
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">Every SPU in this shop already has a photo on file.</td></tr>';
      return;
    }
    items.forEach(function (it) {
      var tr = document.createElement("tr");
      tr.dataset.ext = it.external_product_id || "";
      tr.dataset.cpid = it.channel_product_id;
      tr.dataset.acct = getActiveAccountId() || "";
      tr.innerHTML =
        '<td class="sku" data-label="SKU">' + esc(it.external_product_id || "—") + '</td>' +
        '<td data-label="Title">' + esc(it.title || "") + '</td>' +
        '<td class="code" data-label="State">✚ photo only</td>' +
        '<td data-label="Drop" colspan="2">' +
          '<label class="drop" data-act="dropzone">📷 drop photo or click to choose<input type="file" accept="image/*"></label>' +
          '<div class="gallery"></div>' +
        '</td>' +
        '<td data-label="Status"><span class="row-status"></span></td>';
      var drop = tr.querySelector('[data-act="dropzone"]');
      var input = drop.querySelector("input");
      input.addEventListener("change", function () { if (input.files[0]) uploadPhoto(tr, input.files[0]); });
      drop.addEventListener("dragover", function (ev) { ev.preventDefault(); drop.classList.add("is-dragover"); });
      drop.addEventListener("dragleave", function () { drop.classList.remove("is-dragover"); });
      drop.addEventListener("drop", function (ev) {
        ev.preventDefault();
        drop.classList.remove("is-dragover");
        var f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (f) uploadPhoto(tr, f);
      });
      tbody.appendChild(tr);
      // load existing photos for this SPU (read-only preview)
      var gallery = tr.querySelector(".gallery");
      api("/v2/spu-images?channel_product_id=" + encodeURIComponent(tr.dataset.cpid))
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (list) {
          (list || []).slice(0, 4).forEach(function (im) {
            var img = document.createElement("img");
            img.src = im.url; img.alt = esc(im.filename || "");
            img.title = esc(im.filename || "") + " · " + fmtBytes(im.size_bytes);
            gallery.appendChild(img);
          });
        })
        .catch(function () { /* gallery optional */ });
    });
  }

  function uploadPhoto(tr, file) {
    var acct = parseInt(tr.dataset.acct || "0", 10);
    var cpid = parseInt(tr.dataset.cpid || "0", 10);
    if (!acct || !cpid) { setRowStatus(tr, "no shop / SPU context", "is-err"); return; }
    if (file.size > 8 * 1024 * 1024) { setRowStatus(tr, "file > 8 MiB", "is-err"); return; }
    tr.classList.add("is-filing");
    setRowStatus(tr, "requesting upload URL…", "is-saving");
    api("/v2/spu-images/upload-url", {
      method: "POST",
      body: JSON.stringify({
        channel_account_id: acct,
        channel_product_id: cpid,
        filename: file.name || "photo.jpg",
        content_type: file.type || "image/jpeg",
        size_bytes: file.size
      })
    })
    .then(function (r) {
      if (r.status === 201) return r.json();
      return r.text().then(function (t) { throw new Error("upload-url HTTP " + r.status + " · " + t); });
    })
    .then(function (info) {
      setRowStatus(tr, "PUT → MinIO…", "is-saving");
      return fetch(info.upload_url, {
        method: "PUT",
        credentials: "include",
        headers: info.required_headers || { "Content-Type": file.type || "image/jpeg" },
        body: file
      }).then(function (upR) {
        if (!upR.ok) throw new Error("MinIO PUT HTTP " + upR.status);
        return info;
      });
    })
    .then(function (info) {
      setRowStatus(tr, "confirming…", "is-saving");
      return api("/v2/spu-images/" + info.image_id + "/confirm", { method: "POST" });
    })
    .then(function (r) {
      if (r.status !== 200) return r.text().then(function (t) { throw new Error("confirm HTTP " + r.status + " · " + t); });
      return r.json();
    })
    .then(function () { fileRow(tr); })
    .catch(function (e) { setRowStatus(tr, "err " + e.message, "is-err"); tr.classList.remove("is-filing"); });
  }

  // ---------- tab 3: recently filed ----------
  function loadRecent() {
    var acct = getActiveAccountId();
    var tbody = $("#grid-rows");
    tbody.innerHTML = '<tr><td colspan="6" class="shimmer">loading…</td></tr>';
    var url = "/v2/reporting/cost-snapshots?limit=" + DEFAULT_LIMIT;
    if (acct) url += "&channel_account_id=" + acct;
    api(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (items) {
      renderRecentRows(items || []);
      setBadge("badge-recent", (items || []).length);
    }).catch(function (e) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">error: ' + esc(e.message) + ' · <a href="#" data-retry>retry</a></td></tr>';
      tbody.querySelector("[data-retry]").addEventListener("click", function (ev) { ev.preventDefault(); loadRecent(); });
    });
  }

  function renderRecentRows(items) {
    var tbody = $("#grid-rows");
    tbody.innerHTML = "";
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No filings in this shop yet.</td></tr>';
      return;
    }
    items.forEach(function (it) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="code" data-label="When">' + esc(fmtDate(it.calculated_at)) + '</td>' +
        '<td class="sku" data-label="Channel product">' + esc(it.channel_product_id) + '</td>' +
        '<td data-label="Method">' + esc(it.cost_method || "—") + '</td>' +
        '<td class="money" data-label="Unit cost">' + esc(it.unit_cost) + '</td>' +
        '<td class="code" data-label="Currency">' + esc(it.currency || "—") + '</td>' +
        '<td class="code" data-label="Version">v' + esc(it.calculation_version || 1) + '</td>';
      tbody.appendChild(tr);
    });
  }

  // ---------- shared row state ----------
  function setRowStatus(tr, text, cls) {
    var s = tr.querySelector(".row-status");
    if (!s) return;
    s.textContent = text;
    s.className = "row-status" + (cls ? " " + cls : "");
  }

  function setBadge(id, n) {
    var el = document.getElementById(id);
    if (el) el.textContent = String(n);
  }

  function fileRow(tr) {
    setRowStatus(tr, "filed", "is-ok");
    var stamp = document.createElement("div");
    stamp.className = "filed-stamp";
    stamp.textContent = "FILED";
    tr.appendChild(stamp);
    tr.classList.add("filed");
    var delay = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 200 : 600;
    setTimeout(function () {
      tr.style.transition = "opacity 200ms ease, transform 200ms ease";
      tr.style.opacity = "0";
      tr.style.transform = "translateY(-4px)";
      setTimeout(function () { tr.parentNode && tr.parentNode.removeChild(tr); }, 240);
    }, delay);
  }

  // ---------- boot ----------
  function boot() {
    $$(".tab").forEach(function (btn) {
      btn.addEventListener("click", function () { setActiveTab(btn.getAttribute("data-tab")); });
    });
    var search = $("#filter-search");
    if (search) search.addEventListener("input", function () { costFilter = search.value.trim().toLowerCase(); });
    loadShops()
      .then(loadMe)
      .then(refreshActiveTab)
      .catch(function (e) {
        // surface auth-misconfig early; the page never silently stays empty
        var wb = $(".workbench");
        if (wb) {
          var msg = document.createElement("div");
          msg.className = "empty";
          msg.textContent = "Could not load shops: " + e.message;
          wb.appendChild(msg);
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

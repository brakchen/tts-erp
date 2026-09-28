/* tts-erp — 枚举映射管理页面 JS。
   CRUD 管理 config.enum_map 枚举翻译。
   数据来自 /v2/config/enum-map/list (GET), PUT/DELETE 逐条操作。 */

(() => {
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  var allData = []; // 完整列表 [{id, enum_type, enum_value, label_zh, sort_order}]
  var activeType = ""; // 当前选中的 enum_type

  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }

  function toast(msg) {
    var t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(function() { t.classList.remove("show"); }, 2000);
  }

  function loginUrl() {
    return PREFIX + "/v2/auth/login?next=" + PREFIX + "/v2/pages/enum-map";
  }

  // ---------- API ----------
  function apiGet() {
    return fetch(PREFIX + "/v2/config/enum-map/list", {
      credentials: "include",
      headers: { Accept: "application/json" },
    }).then(function(r) {
      if (r.status === 401) { window.location.href = loginUrl(); throw new Error("unauthorized"); }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function apiPut(body) {
    return fetch(PREFIX + "/v2/config/enum-map", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    }).then(function(r) {
      if (r.status === 401) { window.location.href = loginUrl(); throw new Error("unauthorized"); }
      if (r.status === 403) throw new Error("需要 admin 权限");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function apiDelete(id) {
    return fetch(PREFIX + "/v2/config/enum-map/" + id, {
      method: "DELETE",
      credentials: "include",
      headers: { Accept: "application/json" },
    }).then(function(r) {
      if (r.status === 401) { window.location.href = loginUrl(); throw new Error("unauthorized"); }
      if (r.status === 403) throw new Error("需要 admin 权限");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  // ---------- 渲染 ----------
  function typeLabels() {
    return {
      order_status: "订单状态",
      case_type: "售后类型",
      case_status: "售后状态",
      settle_component: "结算组件",
      cost_source: "成本来源",
      shipment_status: "物流状态",
      column_header: "表头翻译",
    };
  }

  function renderTabs() {
    var types = [];
    var seen = {};
    allData.forEach(function(r) {
      if (!seen[r.enum_type]) { types.push(r.enum_type); seen[r.enum_type] = true; }
    });
    // 确保所有已知 type 都出现（即使当前无数据）
    var known = Object.keys(typeLabels());
    known.forEach(function(t) {
      if (!seen[t]) { types.push(t); seen[t] = true; }
    });
    if (!activeType && types.length) activeType = types[0];

    var container = $("#type-tabs");
    container.innerHTML = "";
    types.forEach(function(t) {
      var btn = document.createElement("button");
      btn.textContent = (typeLabels()[t] || t) + " (" + t + ")";
      if (t === activeType) btn.className = "is-active";
      btn.addEventListener("click", function() {
        activeType = t;
        renderTabs();
        renderRows();
      });
      container.appendChild(btn);
    });
  }

  function renderRows() {
    var filtered = allData.filter(function(r) { return r.enum_type === activeType; });
    filtered.sort(function(a, b) { return a.sort_order - b.sort_order || a.id - b.id; });

    var tbody = $("#rows");
    tbody.innerHTML = "";

    if (!filtered.length) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 4;
      td.className = "empty";
      td.textContent = "该分类暂无映射，请在下方添加";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    filtered.forEach(function(r) {
      var tr = document.createElement("tr");

      // 英文值（只读）
      var tdVal = document.createElement("td");
      tdVal.textContent = r.enum_value;
      tdVal.style.fontFamily = "var(--mono)";
      tr.appendChild(tdVal);

      // 中文标签（可编辑）
      var tdLabel = document.createElement("td");
      var inputLabel = document.createElement("input");
      inputLabel.value = r.label_zh;
      inputLabel.dataset.id = r.id;
      inputLabel.dataset.field = "label_zh";
      tdLabel.appendChild(inputLabel);
      tr.appendChild(tdLabel);

      // 排序（可编辑）
      var tdSort = document.createElement("td");
      var inputSort = document.createElement("input");
      inputSort.type = "number";
      inputSort.value = r.sort_order;
      inputSort.style.width = "60px";
      inputSort.dataset.id = r.id;
      inputSort.dataset.field = "sort_order";
      tdSort.appendChild(inputSort);
      tr.appendChild(tdSort);

      // 操作
      var tdActions = document.createElement("td");
      tdActions.className = "actions";

      var btnSave = document.createElement("button");
      btnSave.className = "btn";
      btnSave.textContent = "保存";
      btnSave.addEventListener("click", function() {
        var newLabel = inputLabel.value.trim();
        var newSort = parseInt(inputSort.value, 10) || 0;
        if (!newLabel) { toast("中文标签不能为空"); return; }
        apiPut({
          enum_type: r.enum_type,
          enum_value: r.enum_value,
          label_zh: newLabel,
          sort_order: newSort,
        }).then(function() {
          toast("已更新");
          load();
        }).catch(function(e) { toast("失败: " + e.message); });
      });
      tdActions.appendChild(btnSave);

      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-danger";
      btnDel.textContent = "删除";
      btnDel.addEventListener("click", function() {
        if (!confirm("确认删除 " + r.enum_type + " / " + r.enum_value + " ?")) return;
        apiDelete(r.id).then(function() {
          toast("已删除");
          load();
        }).catch(function(e) { toast("失败: " + e.message); });
      });
      tdActions.appendChild(btnDel);

      tr.appendChild(tdActions);
      tbody.appendChild(tr);
    });
  }

  // ---------- 新增 ----------
  function bindAdd() {
    $("#btn-add").addEventListener("click", function() {
      var val = $("#add-value").value.trim();
      var label = $("#add-label").value.trim();
      var sort = parseInt($("#add-sort").value, 10) || 0;
      if (!val) { toast("英文值不能为空"); return; }
      if (!label) { toast("中文标签不能为空"); return; }
      apiPut({
        enum_type: activeType,
        enum_value: val,
        label_zh: label,
        sort_order: sort,
      }).then(function() {
        toast("已添加");
        $("#add-value").value = "";
        $("#add-label").value = "";
        $("#add-sort").value = "0";
        load();
      }).catch(function(e) { toast("失败: " + e.message); });
    });
  }

  // ---------- load ----------
  function load() {
    apiGet().then(function(data) {
      allData = data || [];
      renderTabs();
      renderRows();
    }).catch(function(e) {
      if (e && e.message === "unauthorized") return;
      toast("加载失败: " + e.message);
    });
  }

  document.addEventListener("DOMContentLoaded", function() {
    bindAdd();
    load();
  });
})();

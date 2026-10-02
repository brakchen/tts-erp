/* tts-erp — 用户管理页面 JS（用户 / 角色权限 两页签）。
   配套 /v2/users、/v2/roles（契约见 tts_erp_v2/api/v2/users.py）。
   用户页签：列表/新建/重置密码/启用停用/分配角色/会话管理；
   角色权限页签：角色列表/新建/编辑（页面权限点勾选）/删除（内置禁删）。 */

(() => {
  var PREFIX = location.pathname.replace(/\/v2\/pages\/.*$/, "");
  if (!/^\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";

  var state = {
    users: [],            // [{id, username, displayName, status, roles[], lastLoginAt, activeSessions}]
    roles: [],            // [{code, name, description, apiTier, isBuiltin, permissions[], userCount}]
    allPermissions: [],   // [{code, label, group}]
    editingRole: null,    // 编辑中的角色 code（null = 新建）
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }

  function toast(msg, isError) {
    var t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("error", !!isError);
    t.classList.add("show");
    setTimeout(function() { t.classList.remove("show"); }, 2500);
  }

  function loginUrl() {
    return PREFIX + "/v2/auth/login?next=" + PREFIX + "/v2/pages/users";
  }

  // ---------- API（错误体 {detail} 原样提示） ----------
  function api(path, opts) {
    opts = opts || {};
    var headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(PREFIX + path, {
      method: opts.method || "GET",
      credentials: "include",
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }).then(function(r) {
      if (r.status === 401) { window.location.href = loginUrl(); throw new Error("unauthorized"); }
      return r.json().catch(function() { return null; }).then(function(data) {
        if (!r.ok) {
          var detail = data && data.detail ? String(data.detail) : "HTTP " + r.status;
          throw new Error(detail);
        }
        return data || {};
      });
    });
  }

  // ---------- 工具 ----------
  function fmtTime(v) {
    if (v == null || v === "") return "—";
    if (typeof v === "string") return v.replace("T", " ").slice(0, 16);
    return String(v);
  }

  var TIER_LABELS = { readonly: "只读", readwrite: "读写", admin: "管理员" };
  var STATUS_LABELS = { active: "启用", disabled: "停用" };

  // 密码策略前端提示（与后端 tts_erp_v2.accounts.passwords 同源：≥6 位 + 大小写 + 数字）
  function passwordProblems(p) {
    var problems = [];
    if (p.length < 6) problems.push("长度至少 6 位");
    if (!/[A-Z]/.test(p) || !/[a-z]/.test(p) || !/[0-9]/.test(p)) {
      problems.push("需同时含大写字母、小写字母和数字");
    }
    return problems;
  }

  function showPasswordTip(tipEl, input) {
    var problems = passwordProblems(input.value || "");
    if (!problems.length) {
      tipEl.classList.remove("error");
      tipEl.textContent = "至少 6 位，需同时含大写字母、小写字母和数字（最终以后端校验为准）。";
    } else {
      tipEl.classList.add("error");
      tipEl.textContent = "密码不符合策略：" + problems.join("；");
    }
    return problems;
  }

  // ---------- 弹窗 ----------
  function openModal(id) { $(id).hidden = false; }
  function closeModal(id) { $(id).hidden = true; }

  function bindModalClose() {
    Array.prototype.forEach.call(document.querySelectorAll(".modal-backdrop"), function(bd) {
      bd.addEventListener("click", function(e) {
        if (e.target === bd) bd.hidden = true;
      });
      Array.prototype.forEach.call(bd.querySelectorAll("[data-close]"), function(btn) {
        btn.addEventListener("click", function() { bd.hidden = true; });
      });
    });
  }

  // ---------- 页签 ----------
  function bindTabs() {
    $("#tab-users").addEventListener("click", function() {
      $("#tab-users").classList.add("is-active");
      $("#tab-roles").classList.remove("is-active");
      $("#panel-users").hidden = false;
      $("#panel-roles").hidden = true;
    });
    $("#tab-roles").addEventListener("click", function() {
      $("#tab-roles").classList.add("is-active");
      $("#tab-users").classList.remove("is-active");
      $("#panel-roles").hidden = false;
      $("#panel-users").hidden = true;
    });
  }

  // ---------- 用户页签 ----------
  function renderUsers() {
    var keyword = ($("#user-keyword").value || "").trim().toLowerCase();
    var status = $("#user-status").value;
    var rows = state.users.filter(function(u) {
      if (status && u.status !== status) return false;
      if (keyword &&
          u.username.toLowerCase().indexOf(keyword) < 0 &&
          String(u.displayName || "").toLowerCase().indexOf(keyword) < 0) return false;
      return true;
    });

    var tbody = $("#user-rows");
    if (!rows.length) {
      // pi-lens-ignore: no-inner-html-js
      tbody.innerHTML = '<tr><td colspan="7" class="empty">' +
        (state.users.length ? "无匹配用户" : "暂无用户") + "</td></tr>";
      return;
    }
    // pi-lens-ignore: no-inner-html-js
    tbody.innerHTML = rows.map(function(u) {
      var isDisabled = u.status !== "active";
      var roles = (u.roles || []).map(function(c) {
        return '<span class="chip">' + esc(c) + "</span>";
      }).join("") || '<span class="chip">—</span>';
      return "<tr>" +
        '<td class="mono">' + esc(u.username) + "</td>" +
        "<td>" + esc(u.displayName) + "</td>" +
        '<td><span class="badge ' + (isDisabled ? "badge-danger" : "badge-ok") + '">' +
          esc(STATUS_LABELS[u.status] || u.status) + "</span></td>" +
        '<td><div class="chips">' + roles + "</div></td>" +
        "<td>" + esc(fmtTime(u.lastLoginAt)) + "</td>" +
        '<td class="mono">' + esc(u.activeSessions) + "</td>" +
        '<td class="actions">' +
          '<button class="btn" data-action="reset-password" data-id="' + u.id + '">重置密码</button>' +
          '<button class="btn" data-action="toggle-status" data-id="' + u.id + '">' +
            (isDisabled ? "启用" : "停用") + "</button>" +
          '<button class="btn" data-action="assign-roles" data-id="' + u.id + '">分配角色</button>' +
          '<button class="btn" data-action="sessions" data-id="' + u.id + '">会话</button>' +
        "</td></tr>";
    }).join("");
  }

  function userById(id) {
    for (var i = 0; i < state.users.length; i++) {
      if (state.users[i].id === Number(id)) return state.users[i];
    }
    return null;
  }

  function bindUsers() {
    $("#btn-refresh-users").addEventListener("click", loadUsers);
    $("#user-keyword").addEventListener("input", renderUsers);
    $("#user-status").addEventListener("change", renderUsers);

    $("#user-rows").addEventListener("click", function(e) {
      var btn = e.target.closest ? e.target.closest("button[data-action]") : null;
      if (!btn) return;
      var user = userById(btn.getAttribute("data-id"));
      if (!user) return;
      var action = btn.getAttribute("data-action");
      if (action === "reset-password") openPasswordModal(user);
      else if (action === "toggle-status") toggleStatus(user);
      else if (action === "assign-roles") openAssignModal(user);
      else if (action === "sessions") openSessionsModal(user);
    });

    $("#btn-new-user").addEventListener("click", openCreateUserModal);
  }

  // 新建用户
  function openCreateUserModal() {
    $("#nu-username").value = "";
    $("#nu-display").value = "";
    $("#nu-password").value = "";
    $("#nu-username-tip").classList.remove("error");
    $("#nu-username-tip").textContent = "创建后不可修改，小写字母开头。";
    showPasswordTip($("#nu-password-tip"), $("#nu-password"));
    // pi-lens-ignore: no-inner-html-js
    $("#nu-roles").innerHTML = state.roles.map(function(r) {
      return '<label><input type="checkbox" value="' + esc(r.code) + '"> ' +
        esc(r.name) + ' <span class="chip">' + esc(r.code) + "</span></label>";
    }).join("") || '<label class="hint">暂无角色，请先在「角色权限」页签创建</label>';
    openModal("#modal-user");
    $("#nu-username").focus();
  }

  function submitCreateUser() {
    var username = $("#nu-username").value.trim();
    var displayName = $("#nu-display").value.trim();
    var password = $("#nu-password").value;
    var roles = Array.prototype.map.call(
      $("#nu-roles").querySelectorAll("input:checked"), function(i) { return i.value; });

    if (!username) { toast("用户名不能为空", true); return; }
    if (!displayName) { toast("显示名不能为空", true); return; }
    var problems = showPasswordTip($("#nu-password-tip"), $("#nu-password"));
    if (problems.length) { toast("密码不符合策略：" + problems.join("；"), true); return; }

    api("/v2/users", { method: "POST", body: {
      username: username, displayName: displayName, password: password, roles: roles,
    }}).then(function() {
      closeModal("#modal-user");
      toast("已创建用户 " + username);
      loadUsers();
    }).catch(function(e) { toast("创建失败: " + e.message, true); });
  }

  // 重置密码
  function openPasswordModal(user) {
    $("#pw-username").textContent = user.username;
    $("#modal-password").dataset.userId = user.id;
    $("#pw-input").value = "";
    showPasswordTip($("#pw-tip"), $("#pw-input"));
    openModal("#modal-password");
    $("#pw-input").focus();
  }

  function submitPassword() {
    var user = userById($("#modal-password").dataset.userId);
    var password = $("#pw-input").value;
    var problems = showPasswordTip($("#pw-tip"), $("#pw-input"));
    if (problems.length) { toast("密码不符合策略：" + problems.join("；"), true); return; }
    api("/v2/users/" + user.id + "/password", { method: "POST", body: { newPassword: password } })
      .then(function() {
        closeModal("#modal-password");
        toast("已重置 " + user.username + " 的密码，请线下告知用户");
      })
      .catch(function(e) { toast("重置失败: " + e.message, true); });
  }

  // 启用 / 停用
  function toggleStatus(user) {
    var next = user.status === "active" ? "disabled" : "active";
    var msg = next === "disabled"
      ? "确认停用 " + user.username + " ? 停用将吊销其全部会话。"
      : "确认启用 " + user.username + " ?";
    if (!confirm(msg)) return;
    api("/v2/users/" + user.id, { method: "PATCH", body: { status: next } })
      .then(function() {
        toast("已" + (next === "active" ? "启用" : "停用") + " " + user.username);
        loadUsers();
      })
      .catch(function(e) { toast("操作失败: " + e.message, true); });
  }

  // 分配角色
  function openAssignModal(user) {
    $("#as-username").textContent = user.username;
    $("#modal-assign").dataset.userId = user.id;
    // pi-lens-ignore: no-inner-html-js
    $("#as-roles").innerHTML = state.roles.map(function(r) {
      var checked = (user.roles || []).indexOf(r.code) >= 0 ? " checked" : "";
      return '<label><input type="checkbox" value="' + esc(r.code) + '"' + checked + "> " +
        esc(r.name) + ' <span class="chip">' + esc(r.code) + "</span></label>";
    }).join("") || '<label class="hint">暂无角色，请先在「角色权限」页签创建</label>';
    openModal("#modal-assign");
  }

  function submitAssign() {
    var user = userById($("#modal-assign").dataset.userId);
    var roles = Array.prototype.map.call(
      $("#as-roles").querySelectorAll("input:checked"), function(i) { return i.value; });
    api("/v2/users/" + user.id, { method: "PATCH", body: { roles: roles } })
      .then(function() {
        closeModal("#modal-assign");
        toast("已更新 " + user.username + " 的角色");
        loadUsers();
      })
      .catch(function(e) { toast("保存失败: " + e.message, true); });
  }

  // 会话管理
  function openSessionsModal(user) {
    $("#se-username").textContent = user.username;
    $("#modal-sessions").dataset.userId = user.id;
    openModal("#modal-sessions");
    loadSessions();
  }

  function loadSessions() {
    var user = userById($("#modal-sessions").dataset.userId);
    var tbody = $("#se-rows");
    tbody.innerHTML = '<tr><td colspan="7" class="empty">加载中…</td></tr>';
    api("/v2/users/" + user.id + "/sessions").then(function(data) {
      var rows = data.sessions || [];
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">该用户暂无会话</td></tr>';
        return;
      }
      // pi-lens-ignore: no-inner-html-js
      tbody.innerHTML = rows.map(function(s) {
        var label = s.revokedAt ? "已吊销" : (s.active ? "活跃" : "已过期");
        var badge = s.active ? "badge-ok" : "badge-off";
        var revokeBtn = s.active
          ? '<button class="btn btn-danger" data-revoke="' + s.id + '">吊销</button>'
          : '<button class="btn" disabled>吊销</button>';
        return "<tr>" +
          '<td class="mono">' + esc(s.id) + "</td>" +
          "<td>" + esc(fmtTime(s.createdAt)) + "</td>" +
          '<td class="mono">' + esc(s.ip || "—") + "</td>" +
          '<td class="session-ua">' + esc(s.userAgent || "—") + "</td>" +
          "<td>" + esc(fmtTime(s.lastSeenAt)) + "</td>" +
          '<td><span class="badge ' + badge + '">' + label + "</span></td>" +
          '<td class="actions">' + revokeBtn + "</td></tr>";
      }).join("");
    }).catch(function(e) {
      // pi-lens-ignore: no-inner-html-js
      tbody.innerHTML = '<tr><td colspan="7" class="empty">加载失败: ' + esc(e.message) + "</td></tr>";
    });
  }

  function bindSessions() {
    $("#se-refresh").addEventListener("click", loadSessions);
    $("#se-rows").addEventListener("click", function(e) {
      var btn = e.target.closest ? e.target.closest("button[data-revoke]") : null;
      if (!btn) return;
      var user = userById($("#modal-sessions").dataset.userId);
      var sid = btn.getAttribute("data-revoke");
      if (!confirm("确认吊销会话 " + sid + " ?")) return;
      api("/v2/users/" + user.id + "/sessions/" + sid, { method: "DELETE" })
        .then(function(data) {
          toast("已吊销 " + (data.revoked || 0) + " 个会话");
          loadSessions();
          loadUsers();
        })
        .catch(function(e) { toast("吊销失败: " + e.message, true); });
    });
    $("#se-revoke-all").addEventListener("click", function() {
      var user = userById($("#modal-sessions").dataset.userId);
      if (!confirm("确认吊销 " + user.username + " 的全部会话?")) return;
      api("/v2/users/" + user.id + "/sessions", { method: "DELETE" })
        .then(function(data) {
          toast("已吊销 " + (data.revoked || 0) + " 个会话");
          loadSessions();
          loadUsers();
        })
        .catch(function(e) { toast("吊销失败: " + e.message, true); });
    });
  }

  // ---------- 角色权限页签 ----------
  function renderRoles() {
    var tbody = $("#role-rows");
    if (!state.roles.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无角色</td></tr>';
      return;
    }
    var labelByCode = {};
    state.allPermissions.forEach(function(p) { labelByCode[p.code] = p.label; });
    // pi-lens-ignore: no-inner-html-js
    tbody.innerHTML = state.roles.map(function(r) {
      var chips = (r.permissions || []).map(function(code) {
        return '<span class="chip" title="' + esc(code) + '">' +
          esc(labelByCode[code] || code) + "</span>";
      }).join("") || '<span class="chip">—</span>';
      var isAdminBuiltin = r.isBuiltin && r.code === "admin";
      return "<tr>" +
        '<td class="mono">' + esc(r.code) + "</td>" +
        "<td>" + esc(r.name) + "</td>" +
        "<td>" + esc(TIER_LABELS[r.apiTier] || r.apiTier) + "</td>" +
        "<td>" + (r.isBuiltin
          ? '<span class="badge badge-off">内置</span>'
          : '<span class="badge badge-ok">自定义</span>') + "</td>" +
        '<td><div class="chips">' + chips + "</div></td>" +
        '<td class="mono">' + esc(r.userCount) + "</td>" +
        '<td class="actions">' +
          '<button class="btn" data-action="edit-role" data-code="' + esc(r.code) + '"' +
            (isAdminBuiltin ? " disabled title=\"内置 admin 角色不可编辑\"" : "") + ">编辑</button>" +
          '<button class="btn btn-danger" data-action="delete-role" data-code="' + esc(r.code) + '"' +
            (r.isBuiltin ? " disabled title=\"内置角色不可删除\"" : "") + ">删除</button>" +
        "</td></tr>";
    }).join("");
  }

  function bindRoles() {
    $("#btn-refresh-roles").addEventListener("click", loadRoles);
    $("#btn-new-role").addEventListener("click", function() { openRoleModal(null); });

    $("#role-rows").addEventListener("click", function(e) {
      var btn = e.target.closest ? e.target.closest("button[data-action]") : null;
      if (!btn || btn.disabled) return;
      var code = btn.getAttribute("data-code");
      var action = btn.getAttribute("data-action");
      if (action === "edit-role") {
        for (var i = 0; i < state.roles.length; i++) {
          if (state.roles[i].code === code) { openRoleModal(state.roles[i]); return; }
        }
      } else if (action === "delete-role") {
        if (!confirm("确认删除角色 " + code + " ? 仍被用户引用时后端会拒绝。")) return;
        api("/v2/roles/" + encodeURIComponent(code), { method: "DELETE" })
          .then(function() {
            toast("已删除角色 " + code);
            loadRoles();
          })
          .catch(function(e) { toast("删除失败: " + e.message, true); });
      }
    });
  }

  // 新建 / 编辑角色（页面权限点按 allPermissions 的 group 分组勾选）
  function openRoleModal(role) {
    state.editingRole = role ? role.code : null;
    $("#ro-title").textContent = role ? "编辑角色 · " + role.code : "新建角色";
    $("#ro-code").value = role ? role.code : "";
    $("#ro-code").disabled = !!role;
    $("#ro-code-tip").textContent = role
      ? "code 创建后不可修改。"
      : "小写字母开头，2-32 位小写字母 / 数字 / _ -，创建后不可修改。";
    $("#ro-name").value = role ? role.name : "";
    $("#ro-tier").value = role ? role.apiTier : "readwrite";
    // 内置角色不可改 api_tier（后端拒绝）；内置 admin 完全不可编辑（按钮已禁用）
    $("#ro-tier").disabled = !!(role && role.isBuiltin);
    $("#ro-tier-tip").textContent = role && role.isBuiltin
      ? "内置角色不可改 API 档位。"
      : "决定该角色调用数据接口的读写级别。";

    var checked = {};
    (role ? role.permissions || [] : []).forEach(function(c) { checked[c] = true; });
    var groups = {};
    var order = [];
    state.allPermissions.forEach(function(p) {
      if (!groups[p.group]) { groups[p.group] = []; order.push(p.group); }
      groups[p.group].push(p);
    });
    // pi-lens-ignore: no-inner-html-js
    $("#ro-perms").innerHTML = order.map(function(g) {
      return '<div class="perm-group"><h4>' + esc(g) + '</h4><div class="check-list">' +
        groups[g].map(function(p) {
          return '<label><input type="checkbox" value="' + esc(p.code) + '"' +
            (checked[p.code] ? " checked" : "") + "> " + esc(p.label) +
            ' <span class="chip">' + esc(p.code) + "</span></label>";
        }).join("") + "</div></div>";
    }).join("") || '<div class="hint">权限点目录为空</div>';

    openModal("#modal-role");
  }

  function submitRole() {
    var code = $("#ro-code").value.trim().toLowerCase();
    var name = $("#ro-name").value.trim();
    var apiTier = $("#ro-tier").value;
    var permissions = Array.prototype.map.call(
      $("#ro-perms").querySelectorAll("input:checked"), function(i) { return i.value; });

    if (!name) { toast("角色名称不能为空", true); return; }

    if (state.editingRole) {
      var body = { name: name, permissions: permissions };
      if (!$("#ro-tier").disabled) body.apiTier = apiTier;
      api("/v2/roles/" + encodeURIComponent(state.editingRole), { method: "PATCH", body: body })
        .then(function() {
          closeModal("#modal-role");
          toast("已更新角色 " + state.editingRole);
          loadRoles();
        })
        .catch(function(e) { toast("保存失败: " + e.message, true); });
    } else {
      if (!code) { toast("角色 code 不能为空", true); return; }
      api("/v2/roles", { method: "POST", body: {
        code: code, name: name, apiTier: apiTier, permissions: permissions,
      }}).then(function() {
        closeModal("#modal-role");
        toast("已创建角色 " + code);
        loadRoles();
      }).catch(function(e) { toast("创建失败: " + e.message, true); });
    }
  }

  // ---------- load ----------
  function loadUsers() {
    api("/v2/users").then(function(data) {
      state.users = data.users || [];
      renderUsers();
    }).catch(function(e) {
      if (e && e.message === "unauthorized") return;
      toast("加载用户失败: " + e.message, true);
    });
  }

  function loadRoles() {
    api("/v2/roles").then(function(data) {
      state.roles = data.roles || [];
      state.allPermissions = data.allPermissions || [];
      renderRoles();
    }).catch(function(e) {
      if (e && e.message === "unauthorized") return;
      toast("加载角色失败: " + e.message, true);
    });
  }

  document.addEventListener("DOMContentLoaded", function() {
    bindModalClose();
    bindTabs();
    bindUsers();
    bindSessions();
    bindRoles();

    $("#nu-submit").addEventListener("click", submitCreateUser);
    $("#pw-submit").addEventListener("click", submitPassword);
    $("#as-submit").addEventListener("click", submitAssign);
    $("#ro-submit").addEventListener("click", submitRole);
    $("#nu-password").addEventListener("input", function() {
      showPasswordTip($("#nu-password-tip"), $("#nu-password"));
    });
    $("#pw-input").addEventListener("input", function() {
      showPasswordTip($("#pw-tip"), $("#pw-input"));
    });

    loadUsers();
    loadRoles();
  });
})();

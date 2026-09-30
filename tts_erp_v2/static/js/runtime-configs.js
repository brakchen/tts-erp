(() => {
  "use strict";

  const state = { item: null, writable: false };
  const $ = (selector) => document.querySelector(selector);
  const api = async (path, options = {}) => {
    const response = await fetch(`../../v2/config/runtime${path}`, {
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "tts-erp",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
      ...options,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `请求失败（${response.status}）`);
    }
    return response.status === 304 ? null : response.json();
  };

  function notice(message, error = false) {
    const element = $("#rc-notice");
    element.hidden = !message;
    element.textContent = message || "";
    element.classList.toggle("error", error);
  }

  function pretty(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  function parseJson(selector, label) {
    try {
      return JSON.parse($(selector).value);
    } catch {
      throw new Error(`${label}必须是有效 JSON`);
    }
  }

  async function loadItems(selectKey) {
    const data = await api("/items");
    const list = $("#rc-items");
    list.replaceChildren();
    for (const item of data.items) {
      const button = document.createElement("button");
      button.className = "rc-item";
      button.type = "button";
      button.innerHTML = `<strong>${escapeHtml(item.displayName)}</strong><span>${escapeHtml(item.configKey)} · ${item.publishedVersion ? `v${item.publishedVersion}` : "未发布"}</span>`;
      button.addEventListener("click", () => loadDetail(item.configKey));
      list.append(button);
    }
    if (selectKey) await loadDetail(selectKey);
  }

  async function loadDetail(key) {
    try {
      const item = await api(`/items/${encodeURIComponent(key)}`);
      state.item = item;
      $("#rc-empty").hidden = true;
      $("#rc-editor").hidden = false;
      $("#rc-key").value = item.configKey;
      $("#rc-name").value = item.displayName;
      $("#rc-schema").value = pretty(item.jsonSchema);
      $("#rc-payload").value = pretty(item.draftPayload ?? item.publishedPayload ?? {});
      $("#rc-rollout").value = pretty(item.draftPayload ? item.draftRollout : item.publishedRollout);
      $("#rc-version").textContent = item.publishedVersion ? `已发布 v${item.publishedVersion}` : "尚未发布";
      $("#rc-key").readOnly = true;
      $("#rc-name").readOnly = true;
      $("#rc-schema").readOnly = true;
      [...document.querySelectorAll("#rc-editor button")].forEach((button) => { button.disabled = !state.writable; });
      await loadHistory();
      document.querySelectorAll(".rc-item").forEach((button) => {
        button.classList.toggle("active", button.textContent.includes(item.configKey));
      });
      notice("");
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function loadHistory() {
    if (!state.item || !state.writable) return;
    const data = await api(`/items/${encodeURIComponent(state.item.configKey)}/revisions`);
    const list = $("#rc-history-list");
    list.replaceChildren();
    for (const revision of data.items) {
      const row = document.createElement("li");
      const comment = revision.comment ? ` · ${revision.comment}` : "";
      row.innerHTML = `<span>v${revision.version}${escapeHtml(comment)}</span><button type="button">恢复为新版本</button>`;
      row.querySelector("button").addEventListener("click", () => rollback(revision.version));
      list.append(row);
    }
  }

  async function saveDraft() {
    if (!state.item) return;
    try {
      const payload = parseJson("#rc-payload", "配置内容");
      const rollout = parseJson("#rc-rollout", "灰度规则");
      const item = await api(`/items/${encodeURIComponent(state.item.configKey)}/draft`, {
        method: "PUT",
        body: JSON.stringify({ expectedDraftVersion: state.item.draftVersion, payload, rollout }),
      });
      state.item = item;
      notice("草稿已保存");
      await loadItems(item.configKey);
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function publish() {
    if (!state.item) return;
    if (!window.confirm("发布后会生成不可变的新版本。确认发布？")) return;
    try {
      const result = await api(`/items/${encodeURIComponent(state.item.configKey)}/publish`, {
        method: "POST",
        body: JSON.stringify({ expectedDraftVersion: state.item.draftVersion, comment: $("#rc-comment").value || null }),
      });
      notice(`已发布 v${result.publishedVersion}`);
      await loadItems(state.item.configKey);
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function rollback(version) {
    if (!state.item || !window.confirm(`将 v${version} 复制并发布为新版本。继续？`)) return;
    try {
      const result = await api(`/items/${encodeURIComponent(state.item.configKey)}/rollback`, {
        method: "POST",
        body: JSON.stringify({ expectedDraftVersion: state.item.draftVersion, targetVersion: version }),
      });
      notice(`已从 v${result.rolledBackFrom} 恢复并发布 v${result.publishedVersion}`);
      await loadItems(state.item.configKey);
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function createItem(event) {
    event.preventDefault();
    if (!state.writable) return;
    try {
      const schema = parseJson("#rc-new-schema", "Schema");
      const payload = parseJson("#rc-new-payload", "初始草稿");
      const item = await api("/items", {
        method: "POST",
        body: JSON.stringify({
          configKey: $("#rc-new-key").value.trim(),
          displayName: $("#rc-new-name").value.trim(),
          jsonSchema: schema,
          draftPayload: payload,
        }),
      });
      event.target.reset();
      notice("已创建草稿；检查后发布即可生效");
      await loadItems(item.configKey);
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function saveSecret(event) {
    event.preventDefault();
    if (!state.writable) return;
    try {
      const name = $("#rc-secret-name").value.trim();
      await api(`/secrets/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: JSON.stringify({ value: $("#rc-secret-value").value }),
      });
      event.target.reset();
      notice(`凭证已加密保存；在配置中填入 secret://${name} 引用它`);
      await loadSecrets();
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function loadSecrets() {
    if (!state.writable) return;
    try {
      const data = await api("/secrets");
      const list = $("#rc-secret-list");
      list.replaceChildren();
      for (const secret of data.items) {
        const row = document.createElement("li");
        row.innerHTML = `<code>${escapeHtml(secret.ref)}</code><span>${escapeHtml(secret.fingerprint)}</span>`;
        list.append(row);
      }
    } catch (error) {
      notice(error.message, true);
    }
  }

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = value;
    return node.innerHTML;
  }

  async function init() {
    try {
      const me = await fetch("../../v2/auth/me", { credentials: "same-origin" }).then((r) => r.json());
      state.writable = me.role === "readwrite" || me.role === "admin";
      $("#rc-readonly").hidden = state.writable;
      $("#rc-create").hidden = !state.writable;
      $("#rc-secrets").hidden = !state.writable;
      $("#rc-history").hidden = !state.writable;
      await loadItems();
      await loadSecrets();
    } catch (error) {
      notice(error.message, true);
    }
  }

  $("#rc-create-form").addEventListener("submit", createItem);
  $("#rc-secret-form").addEventListener("submit", saveSecret);
  $("#rc-save").addEventListener("click", saveDraft);
  $("#rc-publish").addEventListener("click", publish);
  init();
})();

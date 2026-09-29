/* Focused SPUs PageProfile and persistent selection adapter. */

(() => {
  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  function parseIds(raw) {
    var seen = new Set();
    return String(raw || "")
      .replace(/，/g, ",")
      .split(/[\s,]+/)
      .map((value) => value.trim())
      .filter((value) => value && !seen.has(value) && seen.add(value));
  }

  function createFocusedSelectionAdapter() {
    var context = null;
    var shopPk = null;
    var total = 0;
    var query = "";
    var offset = 0;
    var limit = 50;
    var pendingRemovals = new Set();
    var controller = null;
    var elements = {};
    var canEdit = false;

    function api(path, options) {
      var opts = options || {};
      opts.credentials = "include";
      opts.headers = Object.assign(
        { Accept: "application/json" },
        opts.headers || {},
      );
      return fetch(`${context.prefix}${path}`, opts).then((response) => {
        if (response.status === 401) {
          // pi-lens-ignore: no-open-redirect-js
          window.location.href = context.loginUrl();
          throw new Error("unauthorized");
        }
        return response.json().then((payload) => {
          if (!response.ok) {
            var detail = payload && payload.detail;
            var message =
              (detail && detail.message) ||
              (typeof detail === "string" ? detail : null) ||
              `HTTP ${response.status}`;
            throw new Error(message);
          }
          return payload;
        });
      });
    }

    function setFeedback(message, error) {
      if (!elements.feedback) return;
      elements.feedback.textContent = message || "";
      elements.feedback.classList.toggle("text-danger", Boolean(error));
    }

    function updateCount() {
      if (elements.count) elements.count.textContent = `已关注 ${total} 个 SPU`;
      if (elements.edit) {
        elements.edit.disabled = !shopPk || !canEdit;
        elements.edit.title = canEdit ? "编辑关注集合" : "需要 readwrite 权限";
      }
    }

    function renderList(page) {
      elements.list.replaceChildren();
      page.items.forEach((item) => {
        var row = node("div", "focused-spu-item");
        var meta = node("div", "focused-spu-item-meta");
        meta.appendChild(node("strong", "focused-spu-id", item.spuId));
        meta.appendChild(node("span", "focused-spu-title", item.title || "无标题"));
        meta.appendChild(node("span", "badge text-bg-light", item.status || "未知"));
        var remove = node(
          "button",
          "btn btn-sm btn-outline-danger",
          pendingRemovals.has(item.spuId) ? "撤销移除" : "移除",
        );
        remove.type = "button";
        remove.addEventListener("click", () => {
          if (pendingRemovals.has(item.spuId)) pendingRemovals.delete(item.spuId);
          else pendingRemovals.add(item.spuId);
          renderList(page);
          setFeedback(
            pendingRemovals.size ? `待移除 ${pendingRemovals.size} 个 SPU` : "",
            false,
          );
        });
        row.appendChild(meta);
        row.appendChild(remove);
        elements.list.appendChild(row);
      });
      if (!page.items.length) {
        elements.list.appendChild(
          node("div", "focused-spu-list-empty", "没有匹配的关注 SPU"),
        );
      }
      elements.page.textContent = `${page.offset + 1}–${Math.min(
        page.offset + page.items.length,
        page.matchedTotal,
      )} / ${page.matchedTotal}`;
      elements.previous.disabled = page.offset <= 0;
      elements.next.disabled = page.offset + page.limit >= page.matchedTotal;
    }

    function loadPage() {
      if (!shopPk) return Promise.resolve();
      if (controller) controller.abort();
      controller = new AbortController();
      var params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (query) params.set("q", query);
      return api(
        `/v2/reporting/focused-spus/${shopPk}?${params.toString()}`,
        { signal: controller.signal },
      )
        .then((page) => {
          total = page.total;
          updateCount();
          renderList(page);
          return page;
        })
        .catch((error) => {
          if (error && error.name === "AbortError") return null;
          setFeedback(`关注列表加载失败：${error.message}`, true);
          return null;
        });
    }

    function suggestProducts() {
      var value = elements.add.value.trim();
      elements.suggestions.replaceChildren();
      if (!shopPk || !value || /[,，\s]/.test(value)) return;
      var params = new URLSearchParams({
        shop_pk: String(shopPk),
        q: value,
        limit: "8",
      });
      api(`/v2/commerce/channel-product-options?${params.toString()}`)
        .then((items) => {
          items.forEach((item) => {
            var button = node(
              "button",
              "focused-spu-suggestion",
              `${item.spu_id} · ${item.title || "无标题"}`,
            );
            button.type = "button";
            button.addEventListener("click", () => {
              var ids = parseIds(elements.add.value);
              if (!ids.includes(item.spu_id)) ids.push(item.spu_id);
              elements.add.value = ids.join(", ");
              elements.suggestions.replaceChildren();
            });
            elements.suggestions.appendChild(button);
          });
        })
        .catch(() => {});
    }

    function closeEditor() {
      pendingRemovals.clear();
      elements.add.value = "";
      setFeedback("", false);
      if (elements.dialog.open) elements.dialog.close();
      else elements.dialog.hidden = true;
    }

    function save() {
      var addIds = parseIds(elements.add.value);
      var removeIds = Array.from(pendingRemovals);
      if (!addIds.length && !removeIds.length) {
        setFeedback("没有待保存的修改", false);
        return;
      }
      elements.save.disabled = true;
      api(`/v2/reporting/focused-spus/${shopPk}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "tts-erp",
        },
        body: JSON.stringify({ addSpuIds: addIds, removeSpuIds: removeIds }),
      })
        .then((receipt) => {
          total = receipt.total;
          context.setSelectionQueryable(total > 0);
          updateCount();
          closeEditor();
          context.reload();
        })
        .catch((error) => {
          setFeedback(`保存失败：${error.message}`, true);
        })
        .finally(() => {
          elements.save.disabled = false;
        });
    }

    function buildEditor(slot) {
      var bar = node("div", "focused-spu-bar");
      elements.count = node("strong", "focused-spu-count", "已关注 0 个 SPU");
      elements.edit = node(
        "button",
        "btn btn-sm btn-outline-dark",
        "编辑关注 SPU",
      );
      elements.edit.type = "button";
      bar.appendChild(elements.count);
      bar.appendChild(elements.edit);

      elements.dialog = node("dialog", "focused-spu-dialog");
      var header = node("div", "focused-spu-dialog-header");
      header.appendChild(node("h2", "h5 mb-0", "编辑重点关注 SPU"));
      elements.close = node("button", "btn-close", "");
      elements.close.type = "button";
      elements.close.setAttribute("aria-label", "关闭");
      header.appendChild(elements.close);

      var addLabel = node("label", "form-label", "新增 SPU（精确 ID、标题搜索或批量粘贴）");
      elements.add = node("textarea", "form-control focused-spu-add");
      elements.add.rows = 2;
      elements.add.placeholder = "输入 SPU ID；多个 ID 用逗号或空格分隔";
      elements.suggestions = node("div", "focused-spu-suggestions");

      var searchRow = node("div", "focused-spu-search");
      elements.search = node("input", "form-control form-control-sm");
      elements.search.type = "search";
      elements.search.placeholder = "搜索当前关注的 SPU ID / 标题";
      elements.searchButton = node("button", "btn btn-sm btn-outline-secondary", "搜索");
      elements.searchButton.type = "button";
      searchRow.appendChild(elements.search);
      searchRow.appendChild(elements.searchButton);

      elements.list = node("div", "focused-spu-list");
      var pager = node("div", "focused-spu-list-pager");
      elements.previous = node("button", "btn btn-sm btn-outline-secondary", "上一页");
      elements.previous.type = "button";
      elements.page = node("span", "small text-secondary", "0 / 0");
      elements.next = node("button", "btn btn-sm btn-outline-secondary", "下一页");
      elements.next.type = "button";
      pager.append(elements.previous, elements.page, elements.next);

      elements.feedback = node("div", "focused-spu-feedback small");
      var actions = node("div", "focused-spu-dialog-actions");
      elements.cancel = node("button", "btn btn-sm btn-outline-secondary", "取消");
      elements.cancel.type = "button";
      elements.save = node("button", "btn btn-sm btn-dark", "保存修改");
      elements.save.type = "button";
      actions.append(elements.cancel, elements.save);

      elements.dialog.append(
        header,
        addLabel,
        elements.add,
        elements.suggestions,
        searchRow,
        elements.list,
        pager,
        elements.feedback,
        actions,
      );
      slot.replaceChildren(bar, elements.dialog);

      var suggestTimer = null;
      elements.add.addEventListener("input", () => {
        clearTimeout(suggestTimer);
        suggestTimer = setTimeout(suggestProducts, 250);
      });
      elements.edit.addEventListener("click", () => {
        offset = 0;
        query = "";
        elements.search.value = "";
        loadPage();
        if (elements.dialog.showModal) elements.dialog.showModal();
        else elements.dialog.hidden = false;
      });
      elements.close.addEventListener("click", closeEditor);
      elements.cancel.addEventListener("click", closeEditor);
      elements.save.addEventListener("click", save);
      elements.searchButton.addEventListener("click", () => {
        query = elements.search.value.trim();
        offset = 0;
        loadPage();
      });
      elements.previous.addEventListener("click", () => {
        offset = Math.max(0, offset - limit);
        loadPage();
      });
      elements.next.addEventListener("click", () => {
        offset += limit;
        loadPage();
      });
    }

    return {
      mount: (ctx) => {
        context = ctx;
        var slot = document.querySelector("#selection-slot");
        if (slot) buildEditor(slot);
        api("/v2/auth/me")
          .then((me) => {
            canEdit = me && ["readwrite", "admin"].includes(me.role);
            updateCount();
          })
          .catch(() => {});
        return () => {
          if (controller) controller.abort();
        };
      },
      onShopChanged: (nextShopPk) => {
        shopPk = nextShopPk;
        query = "";
        offset = 0;
        pendingRemovals.clear();
        if (!shopPk) {
          total = 0;
          updateCount();
          return Promise.resolve({ queryable: false, count: 0 });
        }
        return api(`/v2/reporting/focused-spus/${shopPk}?limit=1&offset=0`).then(
          (page) => {
            total = page.total;
            updateCount();
            return { queryable: total > 0, count: total };
          },
        );
      },
      analyticsParams: () => ({ scope: "focused" }),
      destroy: () => {
        if (controller) controller.abort();
      },
    };
  }

  window.ttsErpPageProfile = {
    id: "focused-spus",
    pagePath: "/v2/pages/focused-spus",
    defaults: {
      includeAll: true,
      limit: 100,
      sort: "roi_real",
      order: "asc",
    },
    emptyMessage: "当前窗口没有重点关注 SPU 数据",
    emptySelectionMessage: "尚未关注 SPU，请先点击“编辑关注 SPU”添加",
    selectionAdapter: createFocusedSelectionAdapter(),
    view: {
      summaryIds: [
        "sum-total-orders",
        "sum-spend",
        "sum-orders",
        "sum-sales",
        "sum-refund-count",
        "sum-refund-rate",
        "sum-loss-qty",
        "sum-loss-rate",
        "sum-cancel-count",
        "sum-cancel-rate",
        "sum-net-profit",
        "sum-roi",
        "sum-roi-breakeven",
        "sum-roi-ad-actual",
        "sum-roi-ad",
      ],
      columnIds: [
        "product",
        "spend",
        "ad-actual-roi",
        "ad-breakeven-roi",
        "effective-sales",
        "total-orders",
        "effective-orders",
        "cancel-rate",
        "full-loss-rate",
        "net-profit",
      ],
      drillTabIds: ["pnl", "orders", "settlements", "cases", "ads"],
    },
    extensions: [],
  };
})();

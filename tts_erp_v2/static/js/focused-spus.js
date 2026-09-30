/* Focused SPUs PageProfile and realtime persistent selection adapter. */

(() => {
  const MAX_PATCH_IDS = 500;
  const OPTION_CHUNK_SIZE = 25;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  function parseIds(raw) {
    const values = [];
    const seen = new Set();
    String(raw || "")
      .replace(/，/g, ",")
      .split(/[\s,]+/)
      .forEach((part) => {
        const value = part.trim();
        if (value && !seen.has(value)) {
          seen.add(value);
          values.push(value);
        }
      });
    return values;
  }

  function chunks(values, size) {
    const result = [];
    for (let index = 0; index < values.length; index += size) {
      result.push(values.slice(index, index + size));
    }
    return result;
  }

  function createFocusedSelectionAdapter() {
    let context = null;
    let shopPk = null;
    let total = 0;
    let canEdit = false;
    let select = null;
    let suppressEvents = false;
    let selectionVersion = 0;
    let listController = null;
    let mutationQueue = Promise.resolve();
    let pendingMutations = 0;
    const currentIds = new Set();
    const elements = {};

    function api(path, options) {
      const opts = options || {};
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
            const detail = payload && payload.detail;
            const message =
              (detail && detail.message) ||
              (typeof detail === "string" ? detail : null) ||
              `HTTP ${response.status}`;
            throw new Error(message);
          }
          return payload;
        });
      });
    }

    function setFeedback(message, kind) {
      if (!elements.feedback) return;
      elements.feedback.textContent = message || "";
      elements.feedback.classList.toggle("text-danger", kind === "error");
      elements.feedback.classList.toggle("text-success", kind === "success");
    }

    function updateCount() {
      if (elements.count) {
        elements.count.textContent = `已关注 ${total} 个 SPU · 修改会实时保存`;
      }
      if (select) {
        if (shopPk && canEdit) select.enable();
        else select.disable();
      }
    }

    function queueMutation(task) {
      pendingMutations += 1;
      setFeedback(
        pendingMutations > 1 ? `正在保存 ${pendingMutations} 项修改…` : "正在保存…",
      );
      const result = mutationQueue.catch(() => {}).then(task);
      mutationQueue = result.then(
        () => {
          pendingMutations -= 1;
          if (pendingMutations === 0) setFeedback("已实时保存", "success");
          else setFeedback(`正在保存 ${pendingMutations} 项修改…`);
        },
        () => {
          pendingMutations -= 1;
          if (pendingMutations > 0) {
            setFeedback(`正在保存 ${pendingMutations} 项修改…`);
          }
        },
      );
      return result;
    }

    function patchDelta(targetShop, addIds, removeIds) {
      return api(`/v2/reporting/focused-spus/${targetShop}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "tts-erp",
        },
        body: JSON.stringify({
          addSpuIds: addIds,
          removeSpuIds: removeIds,
        }),
      });
    }

    function rollbackAdded(ids, targetShop) {
      if (!select || shopPk !== targetShop) return;
      suppressEvents = true;
      ids.forEach((spuId) => {
        currentIds.delete(spuId);
        select.removeItem(spuId, true);
      });
      suppressEvents = false;
      total = currentIds.size;
      updateCount();
    }

    function rollbackRemoved(options, targetShop) {
      if (!select || shopPk !== targetShop) return;
      suppressEvents = true;
      options.forEach((option) => {
        currentIds.add(option.spu_id);
        select.addOption(option);
        select.addItem(option.spu_id, true);
      });
      suppressEvents = false;
      total = currentIds.size;
      updateCount();
    }

    function saveAdded(ids) {
      if (!ids.length || !shopPk) return;
      const targetShop = shopPk;
      ids.forEach((spuId) => currentIds.add(spuId));
      total = currentIds.size;
      updateCount();
      queueMutation(() =>
        patchDelta(targetShop, ids, []).then(
          (receipt) => {
            if (shopPk === targetShop) {
              total = receipt.total;
              context.setSelectionQueryable(true);
              updateCount();
              context.reload();
            }
          },
          (error) => {
            rollbackAdded(ids, targetShop);
            setFeedback(`保存失败：${error.message}`, "error");
            throw error;
          },
        ),
      );
    }

    function saveRemoved(options) {
      if (!options.length || !shopPk) return;
      const targetShop = shopPk;
      const ids = options.map((option) => option.spu_id);
      ids.forEach((spuId) => currentIds.delete(spuId));
      total = currentIds.size;
      updateCount();
      queueMutation(() =>
        patchDelta(targetShop, [], ids).then(
          (receipt) => {
            if (shopPk === targetShop) {
              total = receipt.total;
              context.setSelectionQueryable(true);
              updateCount();
              context.reload();
            }
          },
          (error) => {
            rollbackRemoved(options, targetShop);
            setFeedback(`保存失败：${error.message}`, "error");
            throw error;
          },
        ),
      );
    }

    function fetchOptions(params, signal) {
      if (!shopPk) return Promise.resolve([]);
      const query = new URLSearchParams({
        shop_pk: String(shopPk),
        limit: String(params.limit || 50),
      });
      if (params.q) query.set("q", params.q);
      if (params.spuIds && params.spuIds.length) {
        query.set("spu_ids", params.spuIds.join(","));
      }
      return api(`/v2/commerce/channel-product-options?${query.toString()}`, {
        signal: signal,
      });
    }

    function resolvePastedIds(raw) {
      const pasted = parseIds(raw).filter((spuId) => !currentIds.has(spuId));
      if (!pasted.length) {
        setFeedback("粘贴的 SPU 已全部关注", "success");
        return;
      }
      if (pasted.length > MAX_PATCH_IDS) {
        setFeedback(`单次最多添加 ${MAX_PATCH_IDS} 个 SPU`, "error");
        return;
      }
      const requestedShop = shopPk;
      const version = selectionVersion;
      const controller = new AbortController();
      setFeedback(`正在解析 ${pasted.length} 个 SPU…`);
      Promise.all(
        chunks(pasted, OPTION_CHUNK_SIZE).map((ids) =>
          fetchOptions({ spuIds: ids, limit: ids.length }, controller.signal),
        ),
      )
        .then((pages) => {
          if (!select || shopPk !== requestedShop || selectionVersion !== version) {
            return;
          }
          const options = pages.flat();
          const byId = new Map(options.map((option) => [option.spu_id, option]));
          const matched = pasted.filter((spuId) => byId.has(spuId));
          suppressEvents = true;
          matched.forEach((spuId) => {
            select.addOption(byId.get(spuId));
            select.addItem(spuId, true);
          });
          suppressEvents = false;
          const missing = pasted.filter((spuId) => !byId.has(spuId));
          if (missing.length) {
            setFeedback(
              `当前店铺未找到 ${missing.length} 个 SPU：${missing.join("、")}`,
              "error",
            );
          }
          saveAdded(matched);
        })
        .catch((error) => {
          if (error && error.name === "AbortError") return;
          setFeedback(`批量解析失败：${error.message}`, "error");
        });
    }

    function clearSelection() {
      if (!select) return;
      suppressEvents = true;
      select.clear(true);
      select.clearOptions();
      suppressEvents = false;
      currentIds.clear();
      total = 0;
      updateCount();
    }

    function addHydratedItems(items) {
      if (!select) return;
      suppressEvents = true;
      items.forEach((item) => {
        const option = {
          spu_id: item.spuId,
          title: item.title,
          status: item.status,
        };
        currentIds.add(item.spuId);
        select.addOption(option);
        select.addItem(item.spuId, true);
      });
      suppressEvents = false;
    }

    function loadAllFocused(targetShop, version) {
      const items = [];
      const pageSize = 500;
      function readPage(offset) {
        return api(
          `/v2/reporting/focused-spus/${targetShop}?limit=${pageSize}&offset=${offset}`,
          { signal: listController.signal },
        ).then((page) => {
          if (shopPk !== targetShop || selectionVersion !== version) return null;
          items.push(...page.items);
          total = page.total;
          if (offset + page.items.length < page.matchedTotal) {
            return readPage(offset + pageSize);
          }
          return items;
        });
      }
      return readPage(0);
    }

    function initSelect(selectElement) {
      const TomSelectClass = window["TomSelect"];
      if (typeof TomSelectClass !== "function") {
        setFeedback("SPU 多选组件加载失败，请刷新页面", "error");
        return;
      }
      select = new TomSelectClass(selectElement, {
        plugins: { remove_button: { title: "取消关注" } },
        valueField: "spu_id",
        labelField: "spu_id",
        searchField: ["spu_id", "title"],
        maxItems: null,
        create: false,
        closeAfterSelect: false,
        hideSelected: true,
        preload: "focus",
        loadThrottle: 250,
        placeholder: "搜索或粘贴 SPU，选择后实时保存",
        shouldLoad: () => Boolean(shopPk && canEdit),
        load: (query, callback) => {
          const requestedShop = shopPk;
          fetchOptions({ q: query, limit: 50 })
            .then((options) => callback(shopPk === requestedShop ? options : []))
            .catch(() => callback());
        },
        render: {
          option: (data, escapeHtml) =>
            `<div><div class="d-flex justify-content-between gap-2"><span class="op-spu-option-id">${escapeHtml(data.spu_id)}</span><span class="badge text-bg-light">${escapeHtml(data.status || "未知")}</span></div><div class="op-spu-option-title">${escapeHtml(data.title || "无标题")}</div></div>`,
          item: (data, escapeHtml) =>
            `<div title="${escapeHtml(data.title || data.spu_id)}">${escapeHtml(data.spu_id)}</div>`,
          no_results: () => '<div class="no-results">没有匹配的 SPU</div>',
        },
        onItemAdd: (value) => {
          if (suppressEvents || currentIds.has(value)) return;
          saveAdded([value]);
        },
        onItemRemove: (value) => {
          if (suppressEvents || !currentIds.has(value)) return;
          const stored = select.options[value] || { spu_id: value };
          saveRemoved([
            {
              spu_id: value,
              title: stored.title || "",
              status: stored.status || "",
            },
          ]);
        },
      });
      select.disable();
      select.control_input.addEventListener("paste", (event) => {
        const text = event.clipboardData
          ? event.clipboardData.getData("text")
          : "";
        if (!text || !/[,，\s]/.test(text)) return;
        event.preventDefault();
        resolvePastedIds(text);
      });
    }

    function buildInlineSelector(slot) {
      const header = node("div", "focused-spu-inline-header");
      const label = node("label", "form-label op-fld-label mb-0", "重点关注 SPU");
      label.htmlFor = "focused-spu-select";
      elements.count = node(
        "span",
        "focused-spu-count",
        "已关注 0 个 SPU · 修改会实时保存",
      );
      header.append(label, elements.count);
      const selectElement = node("select", "form-select");
      selectElement.id = "focused-spu-select";
      selectElement.multiple = true;
      selectElement.disabled = true;
      selectElement.setAttribute("aria-label", "重点关注 SPU 多选");
      const help = node(
        "div",
        "form-text focused-spu-help",
        "支持按 SPU ID / 标题搜索，或批量粘贴中英文逗号、空格分隔的 SPU；新增和移除会实时保存。",
      );
      elements.feedback = node("div", "focused-spu-feedback small");
      const summaries = document.querySelector("#summaries");
      if (summaries && summaries.parentNode) {
        summaries.parentNode.insertBefore(slot, summaries);
      }
      slot.replaceChildren(header, selectElement, help, elements.feedback);
      slot.classList.add(
        "focused-spu-inline",
        "mx-3",
        "mx-lg-4",
        "mt-3",
      );
      initSelect(selectElement);
    }

    return {
      mount: (ctx) => {
        context = ctx;
        const slot = document.querySelector("#selection-slot");
        if (slot) buildInlineSelector(slot);
        api("/v2/auth/me")
          .then((me) => {
            canEdit = Boolean(me && ["readwrite", "admin"].includes(me.role));
            updateCount();
            if (!canEdit) {
              setFeedback("当前账号为只读权限，不能修改重点关注", "error");
            }
          })
          .catch(() => {});
        return () => {
          if (listController) listController.abort();
        };
      },
      onShopChanged: (nextShopPk) => {
        shopPk = nextShopPk;
        selectionVersion += 1;
        const version = selectionVersion;
        if (listController) listController.abort();
        listController = new AbortController();
        clearSelection();
        if (!shopPk) return Promise.resolve({ queryable: false, count: 0 });
        if (select) select.disable();
        setFeedback("正在加载已关注 SPU…");
        return loadAllFocused(shopPk, version).then(
          (items) => {
            if (!items || shopPk !== nextShopPk || selectionVersion !== version) {
              return { queryable: false, count: 0 };
            }
            addHydratedItems(items);
            context.setSelectionQueryable(true);
            updateCount();
            setFeedback("", null);
            return { queryable: true, count: total };
          },
          (error) => {
            if (error && error.name === "AbortError") {
              return { queryable: false, count: 0 };
            }
            setFeedback(`关注列表加载失败：${error.message}`, "error");
            return { queryable: false, count: 0 };
          },
        );
      },
      analyticsParams: () => ({ scope: "focused" }),
      destroy: () => {
        if (listController) listController.abort();
        if (select) select.destroy();
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
    emptyMessage: "尚未关注 SPU，请在上方多选框搜索并添加",
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
        "sum-projection-status",
        "sum-projection-basis-orders",
        "sum-projection-refund-rate",
        "sum-projection-full-loss-rate",
        "sum-unresolved-orders",
        "sum-projected-future-loss-qty",
        "sum-projected-net-revenue",
        "sum-projected-net-profit",
        "sum-projected-roi",
        "sum-projected-breakeven-roi",
        "sum-projected-ad-roi",
        "sum-projected-ad-breakeven-roi",
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

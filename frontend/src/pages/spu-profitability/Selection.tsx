import { useEffect, useRef, useState } from 'react';
import { Button, Input, Label, TextArea, TextField } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { array, decodeOptions, identity, record, type Option } from './contracts';
import { parseIds } from './query';

export default function Selection({ root, shopPk, focused, canWrite, blocked, fence, applied, onApply, onFocused }: {
  root: string; shopPk: string; focused: boolean; canWrite: boolean; blocked: boolean; fence: { current: number }; applied: string[];
  onApply: (ids: string[]) => void; onFocused: (count: number | null) => void;
}) {
  const [draft, setDraft] = useState<string[]>(applied); const [choices, setChoices] = useState<Option[]>([]);
  const [query, setQuery] = useState(''); const [paste, setPaste] = useState(''); const [batchOpen, setBatchOpen] = useState(false);
  const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0); const [needsCheck, setNeedsCheck] = useState(() => focused && performance.now() < fence.current); const [cooldown, setCooldown] = useState(() => performance.now() < fence.current);
  const [forbidden, setForbidden] = useState(false); const [savedCount, setSavedCount] = useState<number | null>(null);
  const lifetime = useRef<AbortController | undefined>(undefined); const pending = useRef(false); const cancelDelay = useRef<(() => void) | undefined>(undefined);
  const notBefore = fence;
  const resolveController = useRef<AbortController | undefined>(undefined); const resolveGeneration = useRef(0);
  function cancelResolution() { resolveGeneration.current += 1; resolveController.current?.abort(); resolveController.current = undefined; pending.current = false; setBusy(false); }
  function hold(error: unknown) { if (error instanceof ApiError && error.retryAfter) { notBefore.current = Math.max(notBefore.current, performance.now() + error.retryAfter * 1000); setCooldown(true); cancelDelay.current?.(); cancelDelay.current = scheduleReadDelay(() => setCooldown(false), notBefore.current - performance.now()); } }
  const focusedCallback = useRef(onFocused); focusedCallback.current = onFocused;
  const optionsPath = '/v2/commerce/channel-product-options';
  useEffect(() => { if (!focused) setDraft(applied); }, [applied, focused]);
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller; pending.current = false;
    const cooling = performance.now() < notBefore.current;
    setDraft(focused ? [] : applied); setChoices([]); setQuery(''); setPaste(''); setBusy(false); setNotice(''); setNeedsCheck(false); setCooldown(cooling); cancelDelay.current?.();
    if (cooling) {
      setLoading(false); setNotice('服务端冷却尚未结束；到期后请明确重试读取或搜索。'); setNeedsCheck(focused); focusedCallback.current(null);
      cancelDelay.current = scheduleReadDelay(() => setCooldown(false), notBefore.current - performance.now());
      return () => { controller.abort(); resolveController.current?.abort(); cancelDelay.current?.(); };
    }
    if (!shopPk || !focused) { setLoading(false); focusedCallback.current(null); return () => controller.abort(); }
    setLoading(true); focusedCallback.current(null);
    void (async () => {
      const items: Option[] = []; let offset = 0; let total = 0;
      do {
        const result = await withinRequestLifetime(new AbortController(), (signal) => requestJson<unknown>(root, `/v2/reporting/focused-spus/${shopPk}?limit=500&offset=${offset}`, { signal }, parseIdentityJson), controller.signal);
        const data = record(result.data); total = Number(data.total); const page = array(data.items);
        if (!Number.isSafeInteger(total) || total < 0 || (page.length === 0 && offset < total)) throw new Error('关注列表不完整，请重新读取');
        items.push(...page.map((item) => { const row = record(item); return { spu_id: identity(row.spuId), title: typeof row.title === 'string' ? row.title : null, status: typeof row.status === 'string' ? row.status : null }; })); offset += page.length;
      } while (offset < total);
      if (controller.signal.aborted) return;
      const unique = [...new Map(items.map((item) => [item.spu_id, item])).values()];
      setChoices(unique); setDraft(unique.map((item) => item.spu_id)); setSavedCount(total); focusedCallback.current(total); setLoading(false);
    })().catch((error: unknown) => { if (!controller.signal.aborted) { hold(error); setNotice(error instanceof Error ? error.message : '关注读取失败'); setLoading(false); setNeedsCheck(true); } });
    return () => { controller.abort(); resolveController.current?.abort(); cancelDelay.current?.(); };
    // An applied standard selection has a separate effect; only shop/profile invalidates this lifetime.
  }, [root, shopPk, focused, revision]);
  useEffect(() => {
    if (!shopPk || !query.trim() || performance.now() < notBefore.current) return;
    const controller = new AbortController(); const timer = setTimeout(() => {
      const params = new URLSearchParams({ shop_pk: shopPk, q: query, limit: '50' });
      void withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, `${optionsPath}?${params}`, { signal }, parseIdentityJson)).then(({ data }) => {
        const result = decodeOptions(data); if (!controller.signal.aborted) setChoices((old) => [...new Map([...old.filter((item) => draft.includes(item.spu_id)), ...result].map((item) => [item.spu_id, item])).values()]);
      }).catch((error: unknown) => { if (!controller.signal.aborted) { hold(error); setNotice(error instanceof Error ? error.message : '商品搜索失败'); } });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [root, shopPk, query, draft]);
  async function change(next: string[]): Promise<boolean> {
    if (pending.current || loading || cooldown || needsCheck || blocked || performance.now() < notBefore.current) return false;
    if (!focused) { if (next.length > 100) { setNotice('最多选择 100 个 SPU'); return false; } setDraft(next); setNotice(''); return true; }
    if (!canWrite || !shopPk || forbidden) return false;
    const add = next.filter((id) => !draft.includes(id)); const remove = draft.filter((id) => !next.includes(id));
    if (!add.length && !remove.length) return true;
    pending.current = true; setBusy(true); setNotice('正在保存…'); const scope = lifetime.current;
    try {
      // One explicit mutation at a time; never replay a failed or uncertain PATCH.
      const { data: raw } = await withinRequestLifetime(new AbortController(), (signal) => requestJson<unknown>(root, `/v2/reporting/focused-spus/${shopPk}`, { method: 'PATCH', signal, body: JSON.stringify({ addSpuIds: add, removeSpuIds: remove }) }, parseIdentityJson), scope?.signal);
      const result = record(raw); if (identity(result.shopPk) !== shopPk || !Number.isSafeInteger(Number(result.total)) || Number(result.total) < 0) throw new Error('保存响应不完整，需重新读取关注事实');
      array(result.addedSpuIds).forEach(identity); array(result.removedSpuIds).forEach(identity);
      if (scope?.signal.aborted) return false;
      setDraft(next); setSavedCount(Number(result.total)); focusedCallback.current(Number(result.total)); setNotice('已实时保存'); return true;
    } catch (error: unknown) {
      if (scope?.signal.aborted) return false;
      setNeedsCheck(true); focusedCallback.current(null); setNotice(`${error instanceof Error ? error.message : '保存未确认'}；原选择保留。请先重新读取关注事实，再决定后续修改。`);
      if (error instanceof ApiError && error.status === 403) setForbidden(true);
      hold(error);
      return false;
    } finally { if (!scope?.signal.aborted) { pending.current = false; setBusy(false); } }
  }
  async function resolvePaste() {
    if (performance.now() < notBefore.current) return;
    const ids = parseIds(paste); const maximum = focused ? 500 : 100;
    const next = [...new Set([...draft, ...ids])]; if (!ids.length || ids.length > maximum || (!focused && next.length > maximum)) { setNotice(`一次最多 ${maximum} 个 SPU${focused ? '' : '，含已选项'}`); return; }
    pending.current = true; setBusy(true); const scope = lifetime.current; const found: Option[] = [];
    const controller = new AbortController(); resolveController.current?.abort(); resolveController.current = controller; const generation = ++resolveGeneration.current;
    const cancel = () => controller.abort(); scope?.signal.addEventListener('abort', cancel, { once: true });
    try {
      for (let offset = 0; offset < ids.length; offset += 25) {
        const params = new URLSearchParams({ shop_pk: shopPk, spu_ids: ids.slice(offset, offset + 25).join(','), limit: '25' });
        const result = await withinRequestLifetime(new AbortController(), (signal) => requestJson<unknown>(root, `${optionsPath}?${params}`, { signal }, parseIdentityJson), controller.signal); found.push(...decodeOptions(result.data));
      }
      if (scope?.signal.aborted || controller.signal.aborted || generation !== resolveGeneration.current) return;
      const returned = new Set(found.map((item) => item.spu_id)); const missing = ids.filter((id) => !returned.has(id));
      setChoices((old) => [...new Map([...old, ...found].map((item) => [item.spu_id, item])).values()]); pending.current = false; setBusy(false);
      const saved = await change([...new Set([...draft, ...ids.filter((id) => returned.has(id))])]);
      if (!saved || scope?.signal.aborted || controller.signal.aborted || generation !== resolveGeneration.current) return;
      if (missing.length) setNotice(`未添加当前店铺无法确认的 SPU：${missing.join('、')}`); else setPaste('');
    } catch (error: unknown) { if (!scope?.signal.aborted && !controller.signal.aborted && generation === resolveGeneration.current) { hold(error); setNotice(error instanceof Error ? error.message : '批量解析失败'); } }
    finally { scope?.signal.removeEventListener('abort', cancel); if (!scope?.signal.aborted && generation === resolveGeneration.current) { pending.current = false; setBusy(false); resolveController.current = undefined; } }
  }
  const disabled = !shopPk || loading || busy || cooldown || needsCheck || blocked || forbidden || focused && !canWrite;
  const selected = draft.map((id) => choices.find((item) => item.spu_id === id) ?? { spu_id: id, title: null, status: null });
  const options = [...new Map([...selected, ...choices].map((item) => [item.spu_id, item])).values()];
  return <section aria-label={focused ? '重点关注 SPU' : 'SPU 范围'} className="erp-panel space-y-3">
    <div className="flex flex-wrap items-center gap-3"><h2 className="erp-section-heading">{focused ? '重点关注 SPU' : 'SPU 范围'}</h2><span id="spu-selection-count">{focused ? '已关注' : '已选择'} {focused ? savedCount ?? '—' : draft.length} 个</span>{focused && <span>修改后实时保存</span>}</div>
    <div className="erp-spu-selection-search"><TextField value={query} onChange={setQuery} isDisabled={disabled}><Label>按 SPU ID / 标题搜索</Label><Input fullWidth /></TextField><Button variant="secondary" isDisabled={disabled} onPress={() => setBatchOpen(!batchOpen)} aria-expanded={batchOpen}>批量粘贴</Button></div>
    {selected.length > 0 && <details open={selected.length <= 6}><summary>{focused ? '当前关注清单' : '当前已选商品'} · {selected.length} 个</summary><ul className="max-h-48 space-y-2 overflow-y-auto">{selected.map((item) => <li key={item.spu_id} className="flex flex-wrap items-center gap-2"><span className="min-w-0 break-all">{item.title ?? item.spu_id} · {item.spu_id}</span><Button size="sm" variant="secondary" aria-label={`移除 SPU ${item.spu_id}`} isDisabled={disabled} onPress={() => { void change(draft.filter((id) => id !== item.spu_id)); }}>移除</Button></li>)}</ul></details>}
    <fieldset id={focused ? 'focused-spu-select' : 'filter-spu-ids'} aria-label={focused ? '重点关注 SPU 多选' : '批量选择 SPU'} disabled={disabled} className="space-y-2"><legend>{query ? '搜索结果 · 勾选添加，取消勾选仅移除此项' : '输入关键词搜索，或批量粘贴 SPU ID'}</legend><div className="max-h-48 space-y-2 overflow-y-auto">{options.filter((item) => query.trim() || !draft.includes(item.spu_id)).map((item) => <label className="flex items-start gap-2" key={item.spu_id}><input type="checkbox" checked={draft.includes(item.spu_id)} onChange={(event) => { void change(event.target.checked ? [...draft, item.spu_id] : draft.filter((id) => id !== item.spu_id)); }} /><span className="min-w-0 break-all">{item.spu_id} · {item.title ?? '无标题'} · {item.status ?? '未知状态'}</span></label>)}</div></fieldset>
    {batchOpen && <div className="space-y-2"><TextField value={paste} onChange={setPaste} isDisabled={disabled}><Label>粘贴 SPU ID（逗号、空格或换行分隔）</Label><TextArea fullWidth rows={3} /></TextField><Button isDisabled={disabled || !paste.trim()} onPress={() => { void resolvePaste(); }}>解析并添加</Button></div>}
    {!focused && <div className="flex flex-wrap items-center gap-2"><Button id="btn-spu-apply" isDisabled={disabled} onPress={() => onApply(draft)}>查询</Button><Button id="btn-spu-clear" variant="secondary" isDisabled={!shopPk || loading || cooldown || needsCheck || blocked} onPress={() => { cancelResolution(); setDraft([]); setPaste(''); setNotice(''); onApply([]); }}>清空</Button><span>选择后点击查询应用；空范围表示店铺全部 SPU。</span></div>}
    {focused && !canWrite && <p>当前为只读权限；可浏览关注商品，修改需要 readwrite。</p>}
    {forbidden && <p role="alert">服务端拒绝修改此店铺的关注清单；重新读取事实不会解除写权限限制。</p>}
    {loading && <p role="status">正在读取完整关注清单…</p>}{notice && <p id="spu-filter-feedback" role={needsCheck ? 'alert' : 'status'} className="break-all">{notice}</p>}
    {focused && needsCheck && <Button variant="secondary" isDisabled={busy || cooldown} onPress={() => { if (performance.now() >= notBefore.current) setRevision((value) => value + 1); }}>重新读取关注事实</Button>}
  </section>;
}

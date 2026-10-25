import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Table } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { count, decodeHistory, decodeMissing, decodeProduct, decodeProducts, decodeSaved, decodeShops, fieldLabels, fieldsEqual, initialFields, validateFields, type Fields, type History, type Product, type Shop } from './contracts';
import CostFields from './CostFields';

type View = 'all' | 'missing' | 'recent';
interface Draft { target: Product; baseline: Fields; fields: Fields; status?: 'saving' | 'success' | 'error' | 'unknown'; failure?: CommandFailure }
interface Attempt { target: Product; fields: Fields }
interface Recovery { attempt: Attempt; phase: 'unverified' | 'checking' | 'checked' | 'failed'; snapshot?: Product; message?: string }
interface List { key: string; loading: boolean; products?: Product[]; history?: History[]; total?: string | null; totalPhoto?: string; error?: string }
const shopPath = '/v2/commerce/channel-accounts?platform=tiktok&limit=500';
const statuses = { ACTIVATE: '在售', DELETED: '下架', SELLER_DEACTIVATED: '停售' };
function statusLabel(status: string | null) { return status && Object.hasOwn(statuses, status) ? statuses[status as keyof typeof statuses] : status ?? '—'; }
function errorMessage(error: unknown): string { return commandFailure(error, {}).message.replace('提交结果未确认，请先查询核对；不要重复提交', '读取失败，请明确重试'); }
function dirty(draft: Draft) { return !fieldsEqual(draft.baseline, draft.fields); }

export default function ManualCosts({ bootstrap }: PageProps) {
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [shops, setShops] = useState<Shop[]>(); const [shopError, setShopError] = useState(''); const [shopRefresh, setShopRefresh] = useState(0);
  const [shopId, setShopId] = useState(''); const [view, setView] = useState<View>('all');
  const [searchInput, setSearchInput] = useState(''); const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(50); const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState(''); const [hasOrders, setHasOrders] = useState(false); const [missingManual, setMissingManual] = useState(false);
  const [sort, setSort] = useState('status'); const [order, setOrder] = useState('asc'); const [onlyPhoto, setOnlyPhoto] = useState(false);
  const [refresh, setRefresh] = useState(0); const [list, setList] = useState<List>({ key: '', loading: false });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({}); const draftsRef = useRef(drafts);
  const [editor, setEditor] = useState<string>(); const [confirmation, setConfirmation] = useState<Attempt[]>();
  const [image, setImage] = useState<string>(); const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(false); const [notice, setNotice] = useState(''); const [cooldown, setCooldown] = useState(false);
  const [denied, setDenied] = useState(false); const [recovery, setRecovery] = useState<Recovery>();
  // React state alone cannot fence two confirmations in the same event turn.
  const singleFlight = useRef(false); const stopBatch = useRef(false); const mounted = useRef(true);
  const recoveryRef = useRef<Recovery | undefined>(undefined); const notBefore = useRef(0); const cancelDelay = useRef<(() => void) | undefined>(undefined);
  const verification = useRef<AbortController | undefined>(undefined); const writeController = useRef<AbortController | undefined>(undefined);
  const canWriteRef = useRef(canWrite); canWriteRef.current = canWrite;
  const gate = useCallback((error: unknown) => {
    const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
    if (delay <= 0) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + delay); cancelDelay.current?.(); setCooldown(true);
    cancelDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, notBefore.current - performance.now());
  }, []);
  function updateDrafts(update: (previous: Record<string, Draft>) => Record<string, Draft>) {
    const next = update(draftsRef.current); draftsRef.current = next; setDrafts(next);
  }
  function updateRecovery(value: Recovery | undefined) { recoveryRef.current = value; setRecovery(value); }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; stopBatch.current = true; cancelDelay.current?.(); verification.current?.abort(); verification.current = undefined; writeController.current?.abort(); writeController.current = undefined; }; }, []);
  useEffect(() => {
    if (performance.now() < notBefore.current) return;
    const controller = new AbortController(); let current = true; setShopError('');
    void requestJson<unknown>(bootstrap.rootPath, shopPath, { signal: controller.signal }, parseIdentityJson).then(({ data }) => {
      const result = decodeShops(data); if (!current) return;
      setShops(result); setShopId((previous) => previous || result[0]?.id || '');
    }).catch((error: unknown) => { if (current) { setShopError(errorMessage(error)); gate(error); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, shopRefresh, gate]);
  const params = new URLSearchParams({ shop_pk: shopId, limit: String(limit), offset: String(offset) });
  if (view !== 'missing' && query) params.set('q', query);
  if (view === 'all') {
    params.set('sort', sort); params.set('order', order);
    if (status) params.set('status', status); if (hasOrders) params.set('has_orders', 'true'); if (missingManual) params.set('missing_manual_cost', 'true');
  }
  const path = `${view === 'all' ? '/v2/commerce/channel-products' : view === 'missing' ? '/v2/reporting/missing-cost-products' : '/v2/reporting/manual-costs'}?${params}`;
  useEffect(() => {
    if (!shopId || performance.now() < notBefore.current) return;
    const controller = new AbortController(); let current = true; setList({ key: path, loading: true });
    void requestJson<unknown>(bootstrap.rootPath, path, { signal: controller.signal }, parseIdentityJson).then(({ data, headers }) => {
      const result: Pick<List, 'products' | 'history' | 'total' | 'totalPhoto'> = view === 'all' ? { products: decodeProducts(data), total: count(headers.get('X-Total-Count')) }
        : view === 'missing' ? (() => { const value = decodeMissing(data); return { products: value.items, totalPhoto: value.totalPhoto }; })()
          : (() => { const value = decodeHistory(data); return { history: value.items, total: value.total }; })();
      if (result.products?.some((row) => row.shop_pk !== shopId) || result.history?.some((row) => row.shop_pk !== shopId)) throw new Error('店铺身份不匹配');
      if (current) setList({ key: path, loading: false, ...result });
    }).catch((error: unknown) => { if (current) { setList({ key: path, loading: false, error: errorMessage(error) }); gate(error); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, path, shopId, view, refresh, gate]);
  const currentList = list.key === path ? list : { key: path, loading: false };
  const products = (currentList.products ?? []).filter((row) => view !== 'missing' || !onlyPhoto || row.missing_photo === true);
  const nextPageExhausted = currentList.total != null ? BigInt(offset) + BigInt(limit) >= BigInt(currentList.total)
    : (currentList.products ?? currentList.history ?? []).length < limit;
  function draftFor(product: Product): Draft { const fields = initialFields(product, view === 'missing' || missingManual); return draftsRef.current[product.id] ?? { target: { ...product }, baseline: fields, fields }; }
  function change(product: Product, key: keyof Fields, value: string) {
    if (singleFlight.current) return;
    updateDrafts((previous) => { const draft = draftFor(product); return { ...previous, [product.id]: { ...draft, fields: { ...draft.fields, [key]: value }, status: draft.status === 'success' ? undefined : draft.status, failure: draft.failure ? { ...draft.failure, fieldErrors: {} } : undefined } }; });
  }
  function undo(id: string) {
    if (singleFlight.current || recoveryRef.current?.attempt.target.id === id) return;
    updateDrafts((previous) => { const draft = previous[id]; return draft ? { ...previous, [id]: { ...draft, fields: { ...draft.baseline }, failure: undefined, status: undefined } } : previous; }); setNotice('已撤销该行草稿');
  }
  function prepare(targets: Product[]) {
    if (!canWrite || denied || singleFlight.current || performance.now() < notBefore.current || recoveryRef.current) return;
    // Capture only these visible dirty rows, including both identities; later reads never retarget a command.
    const attempts = targets.map((product) => draftFor(product)).filter(dirty).map((draft) => ({ target: { ...draft.target }, fields: { ...draft.fields } }));
    if (attempts.length) { setEditor(undefined); setConfirmation(attempts); }
  }
  async function submit(attempts: Attempt[], explicitRecovery = false) {
    if (!canWriteRef.current || denied || singleFlight.current || performance.now() < notBefore.current || (recoveryRef.current && (!explicitRecovery || recoveryRef.current.phase !== 'checked'))) return;
    singleFlight.current = true; stopBatch.current = false; setPending(true); setNotice('逐行提交中；每行独立事务，不是原子批次'); setConfirmation(undefined);
    let succeeded = 0; let failed = 0; let attempted = 0;
    for (const attempt of attempts) {
      if (stopBatch.current || !mounted.current || !canWriteRef.current || performance.now() < notBefore.current) break;
      const id = attempt.target.id;
      const errors = validateFields(attempt.fields);
      if (Object.keys(errors).length) {
        failed++; attempted++;
        const uncertain = recoveryRef.current?.attempt.target.id === id;
        updateDrafts((previous) => ({ ...previous, [id]: { ...previous[id], status: uncertain ? 'unknown' : 'error', failure: { message: '请检查输入', fieldErrors: errors, uncertain, retryAfter: 0 } } })); continue;
      }
      // A local validation failure is not a new command and cannot resolve an older unknown result.
      if (explicitRecovery) updateRecovery(undefined);
      attempted++; updateDrafts((previous) => ({ ...previous, [id]: { ...previous[id], status: 'saving', failure: undefined } }));
      const controller = new AbortController(); writeController.current = controller;
      try {
        const body = { spu_pk: id, spu_id: attempt.target.spu_id, unit_cost: attempt.fields.unit_cost, currency: attempt.fields.currency, note: attempt.fields.note, ...(attempt.fields.valid_from ? { valid_from: attempt.fields.valid_from } : {}) };
        // This bounds local waiting across both fetch and body reads, not server execution or commit.
        const { data, status: responseStatus } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(bootstrap.rootPath, '/v2/reporting/manual-costs', { method: 'POST', body: JSON.stringify(body), signal }, parseIdentityJson));
        if (responseStatus !== 201) throw new Error('提交响应未确认');
        const saved = decodeSaved(data, attempt.target);
        if (!mounted.current || writeController.current !== controller) break;
        succeeded++; updateDrafts((previous) => ({ ...previous, [id]: { target: { ...attempt.target, ...saved }, baseline: { ...attempt.fields }, fields: { ...attempt.fields }, status: 'success' } }));
        setList((previous) => ({ ...previous, products: previous.products?.map((product) => product.id === id ? { ...product, ...saved } : product) }));
      } catch (error: unknown) {
        if (!mounted.current || writeController.current !== controller) break;
        failed++; const failure = commandFailure(error, fieldLabels); gate(error);
        updateDrafts((previous) => ({ ...previous, [id]: { ...previous[id], status: failure.uncertain ? 'unknown' : 'error', failure } }));
        if (failure.uncertain) { updateRecovery({ attempt, phase: 'unverified' }); break; }
        if (error instanceof ApiError && [401, 403].includes(error.status)) { if (error.status === 403) setDenied(true); break; }
        if (failure.retryAfter > 0 || (error instanceof ApiError && error.status === 429)) break;
      } finally { if (writeController.current === controller) writeController.current = undefined; }
    }
    singleFlight.current = false;
    if (mounted.current) { setPending(false); setNotice(`本次已确认 ${succeeded} 行 · 未完成 ${failed} 行 · 未发送 ${attempts.length - attempted} 行。各行独立生效，不会回滚已确认行。`); }
  }
  async function verify() {
    const previous = recoveryRef.current;
    if (!previous || previous.phase === 'checking' || singleFlight.current || performance.now() < notBefore.current) return;
    const controller = new AbortController(); verification.current = controller; updateRecovery({ ...previous, phase: 'checking', message: undefined });
    try {
      const { data } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(bootstrap.rootPath, `/v2/commerce/channel-products/${previous.attempt.target.id}`, { signal }, parseIdentityJson));
      const snapshot = decodeProduct(data); const target = previous.attempt.target;
      if (snapshot.id !== target.id || snapshot.shop_pk !== target.shop_pk || snapshot.spu_id !== target.spu_id) throw new Error('身份不匹配');
      if (mounted.current && verification.current === controller) updateRecovery({ ...previous, phase: 'checked', snapshot, message: '已读取当前公开记录；GET 不能证明原提交是否生效，也不会自动重试。' });
    } catch (error: unknown) {
      // Deadline abort is a usable failed check; only an ended UI lifetime stays quiet.
      if (!mounted.current || verification.current !== controller) return;
      gate(error);
      if (error instanceof ApiError && error.status === 404) updateRecovery({ ...previous, phase: 'checked', message: '当前记录不存在；GET 不能证明原提交是否生效。请先核对身份。' });
      else updateRecovery({ ...previous, phase: 'failed', message: `核对未完成：${errorMessage(error)}。草稿和目标仍保留。` });
    } finally { if (verification.current === controller) verification.current = undefined; }
  }
  const commandBlocked = !canWrite || pending || cooldown || denied || Boolean(recovery);
  const dirtyVisible = products.filter((product) => dirty(draftFor(product)));
  const savedDrafts = Object.values(drafts).filter(dirty);
  const activeDraft = editor ? drafts[editor] : undefined;
  function changeScope(action: () => void) { if (!singleFlight.current) { action(); setOffset(0); } }
  const recoveryPanel = recovery && <section aria-label="提交结果核对" className="space-y-2 break-all rounded border p-3">
    <h2>结果未确认 · 内部 PK {recovery.attempt.target.id}</h2><p>原提交：{recovery.attempt.fields.unit_cost} {recovery.attempt.fields.currency} · SPU {recovery.attempt.target.spu_id}</p>
    <p>剩余批次已停止；已确认成功的行不会重放。先查询公开事实，再独立决定是否重新提交；重复提交会新增历史记录。</p>
    <Button variant="secondary" isDisabled={cooldown || recovery.phase === 'checking' || pending} onPress={() => { void verify(); }}>查询当前事实</Button>
    {recovery.phase === 'checking' && <p role="status">核对中…</p>}{recovery.message && <p role="status">{recovery.message}</p>}
    {recovery.snapshot && <p>当前人工成本：{recovery.snapshot.unit_cost ?? '—'} {recovery.snapshot.currency ?? '—'} · 货源价：{recovery.snapshot.source_unit_cost ?? '—'}（不代表本次提交确认）</p>}
    <Button isDisabled={!canWrite || denied || cooldown || pending || recovery.phase !== 'checked'} onPress={() => { const target = recovery.attempt.target; const draft = draftsRef.current[target.id]; if (draft) setConfirmation([{ target, fields: { ...draft.fields } }]); }}>核对后决定重新提交</Button>
    <p>重新提交使用保留草稿的当前输入；核对不会覆盖草稿。</p>
  </section>;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-0 max-w-full">当前店铺<select className="block max-w-full" aria-label="当前店铺" value={shopId} disabled={pending || !shops?.length} onChange={(event) => changeScope(() => setShopId(event.target.value))}>{shops?.map((shop) => <option key={shop.id} value={shop.id}>{shop.account_name ?? shop.shop_id} · {shop.shop_id} · #{shop.id}</option>)}</select></label>
      <Button variant="secondary" isDisabled={cooldown || pending} onPress={() => { setShopRefresh((value) => value + 1); setRefresh((value) => value + 1); }}>刷新店铺与列表</Button>
    </div>
    {!shops && !shopError && <p role="status">加载店铺中…</p>}{shops?.length === 0 && <p>暂无可选店铺</p>}
    {shopError && <Alert status="danger"><Alert.Content><Alert.Title>店铺读取失败</Alert.Title><Alert.Description>{shopError}</Alert.Description></Alert.Content></Alert>}
    {!canWrite && <p>当前仅浏览；写入需要 readwrite，以服务端判定为准。</p>}{denied && <p role="alert">服务端拒绝写入（HTTP 403）；草稿保留，编辑不会解除权限限制。</p>}
    {cooldown && <p role="status">服务端冷却中：读取、核对及提交均需等待完整 Retry-After；到期后仍须手动操作。</p>}
    <div aria-label="工作台视图" className="flex flex-wrap gap-2">{([['all', '全部 SPU'], ['missing', '缺成本候选'], ['recent', '最近提交']] as const).map(([value, label]) => <Button key={value} variant={view === value ? 'primary' : 'secondary'} aria-pressed={view === value} isDisabled={pending} onPress={() => changeScope(() => setView(value))}>{label}</Button>)}</div>
    <form aria-label="工作台筛选" className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); changeScope(() => setQuery(searchInput)); }}>
      {view !== 'missing' && <><label>搜索<Input aria-label="按 SPU 或标题搜索" maxLength={200} value={searchInput} disabled={pending} onChange={(event) => setSearchInput(event.target.value)} /></label><Button type="submit" isDisabled={pending || cooldown}>搜索</Button></>}
      <label>每页行数<select aria-label="每页行数" value={limit} disabled={pending} onChange={(event) => changeScope(() => setLimit(Number(event.target.value)))}>{[25, 50, 100].map((value) => <option key={value}>{value}</option>)}</select></label>
      {view === 'all' && <>
        <label>状态<select aria-label="状态" value={status} disabled={pending} onChange={(event) => changeScope(() => setStatus(event.target.value))}><option value="">全部</option>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><input type="checkbox" checked={hasOrders} disabled={pending} onChange={(event) => changeScope(() => setHasOrders(event.target.checked))} />仅看有单</label>
        <label><input type="checkbox" checked={missingManual} disabled={pending} onChange={(event) => changeScope(() => setMissingManual(event.target.checked))} />仅展示未登记 SPU</label>
        <label>排序<select aria-label="排序字段" value={sort} disabled={pending} onChange={(event) => changeScope(() => setSort(event.target.value))}>{[['status', '状态'], ['unit_cost', '成本'], ['created_at', '创建时间'], ['updated_at', '更新时间']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>方向<select aria-label="排序方向" value={order} disabled={pending} onChange={(event) => changeScope(() => setOrder(event.target.value))}><option value="asc">升序</option><option value="desc">降序</option></select></label>
      </>}
      {view === 'missing' && <label><input type="checkbox" checked={onlyPhoto} disabled={pending} onChange={(event) => setOnlyPhoto(event.target.checked)} />仅显示本页 missing_photo 候选（展示筛选）</label>}
      <Button variant="secondary" isDisabled={cooldown || pending || !shopId} onPress={() => setRefresh((value) => value + 1)}>刷新当前列表</Button>
    </form>
    {view === 'missing' ? <p>服务端缺图总数：{currentList.totalPhoto ?? '未知'}。缺成本接口不支持搜索或总候选数；缺图筛选仅作用于当前页，翻页仍按服务器原页。</p> : <p>服务端总数：{currentList.total ?? '未知'} · 偏移 {offset}；筛选和排序均由服务端执行。</p>}
    {savedDrafts.length > 0 && <section aria-label="保留草稿"><h2>保留草稿（切换店铺、视图、分页或关闭编辑不会丢失）</h2><ul>{savedDrafts.map((draft) => <li key={draft.target.id} className="flex flex-wrap items-center gap-2"><span className="min-w-0 break-all">店铺 #{draft.target.shop_pk} · PK {draft.target.id} · {draft.target.title ?? draft.target.spu_id}</span><Button size="sm" variant="secondary" isDisabled={pending} onPress={() => setEditor(draft.target.id)}>继续编辑 PK {draft.target.id}</Button><Button size="sm" variant="secondary" isDisabled={pending || recovery?.attempt.target.id === draft.target.id} onPress={() => undo(draft.target.id)}>撤销草稿 PK {draft.target.id}</Button></li>)}</ul><p>批量提交只包含当前页显示的已编辑行，不会扩大到其他页或其他店铺。</p></section>}
    {recoveryPanel}
    {notice && <p role="status">{notice}</p>}
    {pending && <Button variant="secondary" onPress={() => { stopBatch.current = true; setNotice('已请求停止后续行；正在发送的行仍需等待结果，不能假定已撤销。'); }}>停止后续行</Button>}
    {shopId && list.key !== path && !cooldown && <p role="status">当前范围尚未读取；请明确刷新当前列表。</p>}
    {currentList.loading && <p role="status">加载列表中…</p>}{currentList.error && <Alert status="danger"><Alert.Content><Alert.Title>列表不可用</Alert.Title><Alert.Description>{currentList.error}</Alert.Description><Button isDisabled={cooldown} onPress={() => setRefresh((value) => value + 1)}>重试列表</Button></Alert.Content></Alert>}
    {view !== 'recent' && <Button isDisabled={commandBlocked || dirtyVisible.length === 0} onPress={() => prepare(products)}>提交本页已编辑行</Button>}
    {(currentList.products || currentList.history) && <p>表格可横向滚动，全部业务列均保留。金额、零、空值与历史前值均按服务端原值展示。</p>}
    {currentList.products && <Table><Table.ScrollContainer role="region" aria-label="SPU 表格滚动区域"><Table.Content aria-label="SPU 成本工作台" className="min-w-[90rem]"><Table.Header>{['内部 PK / SPU', '标题 / 状态', '当前人工成本 / 货源价', '编辑单位成本', '创建 / 更新', '图片 / 缺图事实', '操作 / 进度'].map((label, i) => <Table.Column key={label} isRowHeader={i === 0}>{label}</Table.Column>)}</Table.Header><Table.Body renderEmptyState={() => '当前页暂无候选'}>{products.map((product) => {
      const draft = draftFor(product); const shown = product;
      return <Table.Row key={product.id} id={product.id}>
        <Table.Cell><p>{product.id}</p><p className="w-48 break-all">{product.spu_id}</p><p>店铺 #{product.shop_pk}</p></Table.Cell>
        <Table.Cell><p className="w-56 whitespace-normal break-words">{product.title ?? '—'}</p><p>{statusLabel(product.status)}</p></Table.Cell>
        <Table.Cell><p>人工：{shown.unit_cost ?? '—'} {shown.currency ?? '—'}</p><p>货源：{product.source_unit_cost ?? '—'}</p><p>来源仅供参考，未编辑不会提交。</p></Table.Cell>
        <Table.Cell focusMode="child"><Input aria-label="单位成本" className="w-48" value={draft.fields.unit_cost} disabled={pending || !canWrite} aria-invalid={Boolean(draft.failure?.fieldErrors.unit_cost)} onChange={(event) => change(product, 'unit_cost', event.target.value)} /><p>{draft.fields.currency}</p>{draft.failure?.fieldErrors.unit_cost && <p>{draft.failure.fieldErrors.unit_cost}</p>}</Table.Cell>
        <Table.Cell><p>{product.source_created_at ?? '—'}</p><p>{product.source_updated_at ?? '—'}</p></Table.Cell>
        <Table.Cell focusMode="child">{product.image_url && !brokenImages[product.id] ? <Button variant="ghost" aria-label={`放大图片 PK ${product.id}`} onPress={() => setImage(product.image_url!)}><img src={product.image_url} alt={`${product.title ?? product.spu_id} 主图`} className="h-16 w-16 object-contain" onError={() => setBrokenImages((previous) => ({ ...previous, [product.id]: true }))} /></Button> : <span>无可用镜像图</span>}{product.missing_photo !== undefined && <p>missing_photo：{String(product.missing_photo)}</p>}</Table.Cell>
        <Table.Cell focusMode="child"><div className="flex flex-wrap gap-2"><Button size="sm" isDisabled={!canWrite || pending} onPress={() => { updateDrafts((previous) => ({ ...previous, [product.id]: draft })); setEditor(product.id); }}>编辑详情</Button><Button size="sm" variant="secondary" isDisabled={pending || !dirty(draft) || recovery?.attempt.target.id === product.id} onPress={() => undo(product.id)}>撤销</Button><Button size="sm" isDisabled={commandBlocked || !dirty(draft)} onPress={() => prepare([product])}>提交此行</Button></div><p role="status">{draft.status === 'saving' ? '提交中…' : draft.status === 'success' ? '已确认成功' : draft.status === 'unknown' ? '结果未确认' : dirty(draft) ? '已编辑' : '未编辑'}</p>{draft.failure && <p role="alert">{draft.failure.message}</p>}</Table.Cell>
      </Table.Row>;
    })}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    {currentList.history && <Table><Table.ScrollContainer role="region" aria-label="历史表格滚动区域"><Table.Content aria-label="最近提交记录" className="min-w-[85rem]"><Table.Header>{['记录 / 内部 PK / SPU', '标题', '变更前', '变更后', '备注', '生效 / 结束', '提交时间', '操作'].map((label, i) => <Table.Column key={label} isRowHeader={i === 0}>{label}</Table.Column>)}</Table.Header><Table.Body renderEmptyState={() => '暂无提交记录'}>{currentList.history.map((row) => <Table.Row key={row.id} id={row.id}><Table.Cell><p>#{row.id} / PK {row.spu_pk}</p><p className="w-48 break-all">{row.spu_id}</p></Table.Cell><Table.Cell><p className="w-56 whitespace-normal break-words">{row.title ?? '—'}</p></Table.Cell><Table.Cell>{row.prev_unit_cost ?? '—'} {row.prev_currency ?? '—'}</Table.Cell><Table.Cell>{row.unit_cost} {row.currency}</Table.Cell><Table.Cell><p className="w-48 whitespace-normal break-words">{row.note ?? '—'}</p></Table.Cell><Table.Cell><p>{row.valid_from ?? '—'}</p><p>{row.valid_to ?? '当前有效'}</p></Table.Cell><Table.Cell>{row.created_at ?? '—'}</Table.Cell><Table.Cell focusMode="child"><Button size="sm" isDisabled={pending} onPress={() => { changeScope(() => { setView('all'); setQuery(row.spu_id); setSearchInput(row.spu_id); }); }}>查看当前商品</Button></Table.Cell></Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    <div aria-label="分页" className="flex flex-wrap items-center gap-3"><Button variant="secondary" isDisabled={pending || cooldown || offset === 0} onPress={() => setOffset((value) => Math.max(0, value - limit))}>上一页</Button><span>当前偏移 {offset} · 每页 {limit}</span><Button variant="secondary" isDisabled={pending || cooldown || !shopId || currentList.loading || Boolean(currentList.error) || nextPageExhausted} onPress={() => setOffset((value) => value + limit)}>下一页</Button></div>
    <Modal.Backdrop isOpen={editor !== undefined} onOpenChange={(open) => { if (!open && !pending) setEditor(undefined); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭编辑并保留草稿" /><Modal.Header><Modal.Heading>编辑成本草稿</Modal.Heading></Modal.Header><Modal.Body className="space-y-3">{activeDraft && <><p className="break-all">店铺 #{activeDraft.target.shop_pk} · PK {activeDraft.target.id} · {activeDraft.target.spu_id}</p><CostFields fields={activeDraft.fields} errors={activeDraft.failure?.fieldErrors ?? {}} disabled={pending || !canWrite} onChange={(key, value) => change(activeDraft.target, key, value)} />{activeDraft.failure && <p role="alert">{activeDraft.failure.message}</p>}<p>取消只关闭编辑，保留草稿；撤销才删除该行未提交输入。</p></>}</Modal.Body><Modal.Footer><Button variant="secondary" isDisabled={pending} onPress={() => setEditor(undefined)}>取消并保留草稿</Button><Button isDisabled={commandBlocked || !activeDraft || !dirty(activeDraft)} onPress={() => { if (activeDraft) prepare([activeDraft.target]); }}>确认此行草稿</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={confirmation !== undefined} isDismissable={!pending} onOpenChange={(open) => { if (!open && !pending) setConfirmation(undefined); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog><Modal.CloseTrigger aria-label="取消提交确认" /><Modal.Header><Modal.Heading>确认逐行写入</Modal.Heading></Modal.Header><Modal.Body><p>各行独立事务，不是原子批次。失败不会撤销已确认行；未知结果将停止剩余提交，不会自动重放。</p>{recovery && <p>这是核对后的独立重试决定；查询不能证明先前是否提交，再次写入会新增成本历史。</p>}<ul>{confirmation?.map((attempt) => <li key={attempt.target.id} className="break-all">店铺 #{attempt.target.shop_pk} · PK {attempt.target.id} · SPU {attempt.target.spu_id} · {attempt.fields.unit_cost} {attempt.fields.currency} · 备注：{attempt.fields.note || '—'} · 生效：{attempt.fields.valid_from || '服务端当前 UTC'}</li>)}</ul></Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setConfirmation(undefined)}>取消</Button><Button isDisabled={!canWrite || denied || cooldown || pending || (Boolean(recovery) && recovery?.phase !== 'checked')} onPress={() => { if (confirmation) void submit(confirmation, Boolean(recovery)); }}>确认逐行提交</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={image !== undefined} onOpenChange={(open) => { if (!open) setImage(undefined); }}><Modal.Container size="lg"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭图片" /><Modal.Header><Modal.Heading>商品图片</Modal.Heading></Modal.Header><Modal.Body>{image && <img src={image} alt="商品主图放大" className="max-h-[70vh] max-w-full object-contain" />}</Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}

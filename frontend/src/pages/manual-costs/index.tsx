import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Label, ListBox, Modal, Select, Table, toast } from '@heroui/react';
import { displayShopName } from '../../shared/shop-name';
import OffsetPagination from '../../shared/OffsetPagination';
import PageIntro from '../../shared/PageIntro';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure } from '../../shared/api/command-errors';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { count, decodeHistory, decodeMissing, decodeProducts, decodeSaved, decodeShops, fieldLabels, fieldsEqual, initialFields, validateFields, type Fields, type History, type Product, type Shop } from './contracts';
import CostFields from './CostFields';
import CostImport from './CostImport';

type View = 'all' | 'missing' | 'recent';
interface Editor { target: Product; fields: Fields; errors: Record<string, string> }
interface List { key: string; loading: boolean; products?: Product[]; history?: History[]; total?: string | null; totalPhoto?: string; error?: string }
const limit = 50;
const shopPath = '/v2/commerce/channel-accounts?platform=tiktok&limit=500';
const statuses = { ACTIVATE: '在售', DELETED: '下架', SELLER_DEACTIVATED: '停售' };
function statusLabel(status: string | null) { return status && Object.hasOwn(statuses, status) ? statuses[status as keyof typeof statuses] : status ?? '—'; }
function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const message = error.status >= 500 ? `数据服务暂不可用（HTTP ${error.status}），请重试或联系管理员`
      : error.status >= 200 && error.status < 300 ? '列表响应格式不兼容，请联系管理员核对服务版本'
        : commandFailure(error, {}).message;
    return `${message}${error.requestId ? ` · 请求编号 ${error.requestId}` : ''}`;
  }
  return '读取失败，请检查网络后重试';
}

export default function PurchasePrices({ bootstrap }: PageProps) {
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [shops, setShops] = useState<Shop[]>(); const [shopError, setShopError] = useState(''); const [shopRefresh, setShopRefresh] = useState(0);
  const [shopId, setShopId] = useState(''); const [view, setView] = useState<View>('all');
  const [searchInput, setSearchInput] = useState(''); const [query, setQuery] = useState(''); const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState(''); const [hasOrders, setHasOrders] = useState(false); const [missingPrice, setMissingPrice] = useState(false);
  const [sort, setSort] = useState('status'); const [order, setOrder] = useState('asc'); const [onlyPhoto, setOnlyPhoto] = useState(false);
  const [refresh, setRefresh] = useState(0); const [list, setList] = useState<List>({ key: '', loading: false });
  const [editor, setEditor] = useState<Editor>(); const [image, setImage] = useState<string>(); const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});
  const [saveBusy, setSaveBusy] = useState(false); const [importBusy, setImportBusy] = useState(false); const pending = saveBusy || importBusy;
  const [writeFenced, setWriteFenced] = useState(false); const [cooldown, setCooldown] = useState(false); const [denied, setDenied] = useState(false);
  const singleFlight = useRef(false); const mounted = useRef(true); const notBefore = useRef(0);
  const cancelDelay = useRef<(() => void) | undefined>(undefined); const writeController = useRef<AbortController | undefined>(undefined);
  const saveToast = useRef<string | undefined>(undefined); const canWriteRef = useRef(canWrite); canWriteRef.current = canWrite;
  const gate = useCallback((error: unknown) => {
    const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
    if (delay <= 0) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + delay); cancelDelay.current?.(); setCooldown(true);
    cancelDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, notBefore.current - performance.now());
  }, []);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; cancelDelay.current?.(); writeController.current?.abort();
    if (saveToast.current) toast.close(saveToast.current);
  }; }, []);
  useEffect(() => {
    if (performance.now() < notBefore.current) return;
    const controller = new AbortController(); let current = true; setShopError('');
    void requestJson<unknown>(bootstrap.rootPath, shopPath, { signal: controller.signal }, parseIdentityJson).then(({ data }) => {
      const result = decodeShops(data); if (current) setShops(result);
    }).catch((error: unknown) => { if (current) { setShopError(errorMessage(error)); gate(error); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, shopRefresh, gate]);
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (shopId) params.set('shop_pk', shopId);
  if (view !== 'missing' && query) params.set('q', query);
  if (view === 'all') {
    params.set('sort', sort); params.set('order', order);
    if (status) params.set('status', status); if (hasOrders) params.set('has_orders', 'true'); if (missingPrice) params.set('missing_purchase_cost', 'true');
  }
  const path = `${view === 'all' ? '/v2/commerce/channel-products' : view === 'missing' ? '/v2/reporting/missing-cost-products' : '/v2/reporting/purchase-prices'}?${params}`;
  useEffect(() => {
    if (performance.now() < notBefore.current) return;
    const controller = new AbortController(); let current = true; setList({ key: path, loading: true });
    void requestJson<unknown>(bootstrap.rootPath, path, { signal: controller.signal }, parseIdentityJson).then(({ data, headers }) => {
      const result: Pick<List, 'products' | 'history' | 'total' | 'totalPhoto'> = view === 'all' ? { products: decodeProducts(data), total: count(headers.get('X-Total-Count')) }
        : view === 'missing' ? (() => { const value = decodeMissing(data); return { products: value.items, totalPhoto: value.totalPhoto }; })()
          : (() => { const value = decodeHistory(data); return { history: value.items, total: value.total }; })();
      if (shopId && (result.products?.some((row) => row.shop_pk !== shopId) || result.history?.some((row) => row.shop_pk !== shopId))) throw new Error('店铺身份不匹配');
      if (current) setList({ key: path, loading: false, ...result });
    }).catch((error: unknown) => { if (current) { setList({ key: path, loading: false, error: errorMessage(error) }); gate(error); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, path, shopId, view, refresh, gate]);
  const currentList = list.key === path ? list : { key: path, loading: false };
  const products = (currentList.products ?? []).filter((row) => view !== 'missing' || !onlyPhoto || row.missing_photo === true);
  const nextPageExhausted = currentList.total != null ? BigInt(offset) + BigInt(limit) >= BigInt(currentList.total)
    : (currentList.products ?? currentList.history ?? []).length < limit;
  const commandBlocked = !canWrite || pending || writeFenced || cooldown || denied;
  function shopName(pk: string) { const shop = shops?.find((item) => item.id === pk); return shop ? displayShopName(shop, `店铺 #${pk}`) : `店铺 #${pk}`; }
  function changeScope(action: () => void) { if (!singleFlight.current && performance.now() >= notBefore.current) { action(); setOffset(0); } }
  function closeEditor() { if (!singleFlight.current) setEditor(undefined); }
  function change(key: keyof Fields, value: string) {
    if (!singleFlight.current) setEditor((previous) => previous ? { ...previous, fields: { ...previous.fields, [key]: value }, errors: {} } : previous);
  }
  async function save() {
    if (!editor || !canWriteRef.current || denied || writeFenced || singleFlight.current || performance.now() < notBefore.current
      || fieldsEqual(initialFields(editor.target), editor.fields)) return;
    const attempt = editor; const errors = validateFields(attempt.fields);
    if (Object.keys(errors).length) {
      setEditor({ ...attempt, errors });
      if (saveToast.current) toast.close(saveToast.current);
      saveToast.current = toast.danger('处理失败', { description: '请检查输入', timeout: 6000 }); return;
    }
    singleFlight.current = true; setSaveBusy(true);
    if (saveToast.current) toast.close(saveToast.current);
    const notification = toast('处理中', { isLoading: true, timeout: 0 }); saveToast.current = notification;
    const controller = new AbortController(); writeController.current = controller;
    try {
      const body = { spu_pk: attempt.target.id, spu_id: attempt.target.spu_id, unit_cost: attempt.fields.unit_cost,
        currency: attempt.fields.currency, note: attempt.fields.note, ...(attempt.fields.valid_from ? { valid_from: attempt.fields.valid_from } : {}) };
      const { data, status: responseStatus } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(bootstrap.rootPath,
        '/v2/reporting/purchase-prices', { method: 'POST', body: JSON.stringify(body), signal }, parseIdentityJson));
      if (responseStatus !== 201) throw new Error('提交响应未确认');
      decodeSaved(data, attempt.target);
      if (!mounted.current || writeController.current !== controller) return;
      setEditor(undefined); setRefresh((value) => value + 1);
      saveToast.current = toast.update(notification, '处理成功', { variant: 'success', isLoading: false, timeout: 4000 });
    } catch (error: unknown) {
      if (!mounted.current || writeController.current !== controller) return;
      gate(error); const failure = commandFailure(error, fieldLabels);
      const uncertain = !(error instanceof ApiError && error.status >= 400 && error.status < 500);
      if (uncertain) setWriteFenced(true);
      if (error instanceof ApiError && error.status === 403) setDenied(true);
      setEditor({ ...attempt, errors: failure.fieldErrors });
      saveToast.current = toast.update(notification, '处理失败', { variant: 'danger', isLoading: false, timeout: uncertain ? 0 : 6000,
        description: uncertain ? '保存结果未确认，请刷新页面核对采购价，避免重复提交。' : failure.message });
    } finally {
      if (writeController.current === controller) writeController.current = undefined;
      singleFlight.current = false; if (mounted.current) setSaveBusy(false);
    }
  }
  return <div className="erp-page">
    <PageIntro eyebrow="PROCUREMENT WORKSPACE" title="维护商品采购价" description="支持单条修改和文件导入。" />
    <section className="erp-panel erp-filter-workspace" aria-label="采购范围与筛选">
      <div className="erp-tabs" role="group" aria-label="工作台视图">
        {([['all', '全部 SPU'], ['missing', '缺采购价候选'], ['recent', '最近提交']] as const).map(([key, label]) =>
          <Button key={key} className="whitespace-nowrap" variant={view === key ? 'primary' : 'ghost'} aria-pressed={view === key}
            isDisabled={pending || cooldown} onPress={() => changeScope(() => setView(key))}>{label}</Button>)}
      </div>
      <form aria-label="工作台筛选" className="space-y-3 border-t border-separator pt-3"
        onSubmit={(event) => { event.preventDefault(); changeScope(() => setQuery(searchInput.trim())); }}>
        <div className="grid min-w-0 gap-3 lg:grid-cols-[20rem_minmax(0,1fr)]" aria-label="店铺与搜索">
          <Select className="w-full min-w-0" value={shopId || 'all'} isDisabled={pending || cooldown} onChange={(key) => {
            if (typeof key === 'string') changeScope(() => setShopId(key === 'all' ? '' : key));
          }}>
            <Label>店铺</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>
              <ListBox.Item id="all" textValue="全部店铺">全部店铺</ListBox.Item>
              {shops?.map((shop) => <ListBox.Item key={shop.id} id={shop.id} textValue={`${displayShopName(shop, shop.shop_id)} · ${shop.shop_id} · #${shop.id}`}>{displayShopName(shop, shop.shop_id)} · {shop.shop_id} · #{shop.id}</ListBox.Item>)}
            </ListBox></Select.Popover>
          </Select>
          {view !== 'missing' && <div className="erp-filter-row flex-nowrap" role="group" aria-label="搜索商品">
            <label className="flex-1">搜索<Input fullWidth aria-label="按 SPU 或标题搜索" placeholder="SPU ID 或商品标题" maxLength={200}
              value={searchInput} disabled={pending || cooldown} onChange={(event) => setSearchInput(event.target.value)} /></label>
            <Button type="submit" isDisabled={pending || cooldown}>搜索</Button>
          </div>}
        </div>
        {view === 'all' && <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="erp-filter-row" role="group" aria-label="商品状态筛选">
            <Select className="w-40 max-w-full" value={`choice:${status}`} isDisabled={pending || cooldown} onChange={(key) => { if (typeof key === 'string') changeScope(() => setStatus(key.slice(7))); }}>
              <Label>状态</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{[['', '全部'], ...Object.entries(statuses)].map(([value, label]) => <ListBox.Item key={value} id={`choice:${value}`} textValue={label}>{label}</ListBox.Item>)}</ListBox></Select.Popover>
            </Select>
            <Checkbox isSelected={hasOrders} isDisabled={pending || cooldown} onChange={(checked) => changeScope(() => setHasOrders(checked))}><Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>仅看有单</Checkbox.Content></Checkbox>
            <Checkbox isSelected={missingPrice} isDisabled={pending || cooldown} onChange={(checked) => changeScope(() => setMissingPrice(checked))}><Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>仅看缺采购价</Checkbox.Content></Checkbox>
          </div>
          <div className="erp-filter-row" role="group" aria-label="排序设置">
            <Select className="w-40 max-w-full" aria-label="排序字段" value={sort} isDisabled={pending || cooldown} onChange={(key) => { if (typeof key === 'string') changeScope(() => setSort(key)); }}>
              <Label>排序</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{[['status', '状态'], ['unit_cost', '采购价'], ['created_at', '创建时间'], ['updated_at', '更新时间']].map(([value, label]) => <ListBox.Item key={value} id={value} textValue={label}>{label}</ListBox.Item>)}</ListBox></Select.Popover>
            </Select>
            <Select className="w-32 max-w-full" aria-label="排序方向" value={order} isDisabled={pending || cooldown} onChange={(key) => { if (typeof key === 'string') changeScope(() => setOrder(key)); }}>
              <Label>方向</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="asc" textValue="升序">升序</ListBox.Item><ListBox.Item id="desc" textValue="降序">降序</ListBox.Item></ListBox></Select.Popover>
            </Select>
          </div>
        </div>}
        {view === 'missing' && <div className="erp-filter-row">
          <Checkbox isSelected={onlyPhoto} isDisabled={pending || cooldown} onChange={setOnlyPhoto}><Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>仅看本页缺图</Checkbox.Content></Checkbox>
          {currentList.totalPhoto != null && <span className="erp-table-meta">缺图总数 {currentList.totalPhoto}</span>}
        </div>}
      </form>
      {shopError && <Alert status="danger"><Alert.Content><Alert.Title>店铺读取失败</Alert.Title><Alert.Description>{shopError}</Alert.Description><Button variant="secondary" isDisabled={cooldown || pending} onPress={() => setShopRefresh((value) => value + 1)}>重试店铺</Button></Alert.Content></Alert>}
      {!canWrite && <p>当前仅浏览</p>}
      {cooldown && <p role="status">请求频繁，请稍后再操作。</p>}
    </section>
    <CostImport rootPath={bootstrap.rootPath} blocked={commandBlocked || Boolean(editor)}
      onBusy={(busy) => { singleFlight.current = busy; setImportBusy(busy); }} onFence={setWriteFenced}
      onFailure={(error) => { gate(error); if (error instanceof ApiError && error.status === 403) setDenied(true); }}
      onImported={() => setRefresh((value) => value + 1)} />
    {currentList.loading && <p role="status">加载列表中…</p>}{currentList.error && <Alert status="danger"><Alert.Content><Alert.Title>列表不可用</Alert.Title><Alert.Description>{currentList.error}</Alert.Description><Button isDisabled={cooldown || pending} onPress={() => setRefresh((value) => value + 1)}>重试列表</Button></Alert.Content></Alert>}
    {view !== 'recent' && <h2 className="erp-section-heading">{view === 'missing' ? '待补全采购价的商品' : '商品采购价明细'}</h2>}
    {currentList.products && <Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" tabIndex={0} role="region" aria-label="SPU 表格滚动区域"><Table.Content aria-label="SPU 采购价工作台" className="min-w-[75rem] erp-table-content"><Table.Header className="erp-table-head">{['店铺 / SPU', '标题 / 状态', '采购价', '创建 / 更新', '图片', '操作'].map((label, i) => <Table.Column className="erp-table-column" key={label} isRowHeader={i === 0}>{label}</Table.Column>)}</Table.Header><Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">当前页暂无商品</div>}>{products.map((product) =>
      <Table.Row className="erp-table-row" key={product.id} id={product.id}>
        <Table.Cell className="erp-table-cell"><p className="w-48 whitespace-normal break-words">{shopName(product.shop_pk)}</p><p className="w-48 break-all">{product.spu_id}</p><p className="erp-table-meta">PK {product.id}</p></Table.Cell>
        <Table.Cell className="erp-table-cell"><p className="w-56 whitespace-normal break-words">{product.title ?? '—'}</p><p>{statusLabel(product.status)}</p></Table.Cell>
        <Table.Cell className="erp-table-cell"><p>{product.unit_cost ?? '—'} {product.currency ?? '—'}</p></Table.Cell>
        <Table.Cell className="erp-table-cell"><p>{product.source_created_at ?? '—'}</p><p>{product.source_updated_at ?? '—'}</p></Table.Cell>
        <Table.Cell className="erp-table-cell" focusMode="child">{product.image_url && !brokenImages[product.id] ? <Button className="erp-table-action" variant="ghost" aria-label={`放大图片 PK ${product.id}`} onPress={() => setImage(product.image_url!)}><img src={product.image_url} alt={`${product.title ?? product.spu_id} 主图`} className="h-16 w-16 object-contain" onError={() => setBrokenImages((previous) => ({ ...previous, [product.id]: true }))} /></Button> : <span>暂无图片</span>}{product.missing_photo !== undefined && <p>{product.missing_photo ? '缺少主图' : '已有主图'}</p>}</Table.Cell>
        <Table.Cell className="erp-table-cell" focusMode="child"><Button className="erp-table-action" variant="secondary" size="sm" isDisabled={commandBlocked} onPress={() => setEditor({ target: { ...product }, fields: initialFields(product), errors: {} })}>编辑详情</Button></Table.Cell>
      </Table.Row>
    )}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    {currentList.history && <Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" tabIndex={0} role="region" aria-label="历史表格滚动区域"><Table.Content aria-label="最近提交记录" className="min-w-[85rem] erp-table-content"><Table.Header className="erp-table-head">{['店铺 / SPU', '标题', '变更前', '变更后', '备注', '生效 / 结束', '提交时间', '操作'].map((label, i) => <Table.Column className="erp-table-column" key={label} isRowHeader={i === 0}>{label}</Table.Column>)}</Table.Header><Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">暂无提交记录</div>}>{currentList.history.map((row) => <Table.Row className="erp-table-row" key={row.id} id={row.id}><Table.Cell className="erp-table-cell"><p className="w-48 whitespace-normal break-words">{shopName(row.shop_pk)}</p><p className="w-48 break-all">{row.spu_id}</p></Table.Cell><Table.Cell className="erp-table-cell"><p className="w-56 whitespace-normal break-words">{row.title ?? '—'}</p></Table.Cell><Table.Cell className="erp-table-cell">{row.prev_unit_cost ?? '—'} {row.prev_currency ?? '—'}</Table.Cell><Table.Cell className="erp-table-cell">{row.unit_cost} {row.currency}</Table.Cell><Table.Cell className="erp-table-cell"><p className="w-48 whitespace-normal break-words">{row.note ?? '—'}</p></Table.Cell><Table.Cell className="erp-table-cell"><p>{row.valid_from ?? '—'}</p><p>{row.valid_to ?? '当前有效'}</p></Table.Cell><Table.Cell className="erp-table-cell">{row.created_at ?? '—'}</Table.Cell><Table.Cell className="erp-table-cell" focusMode="child"><Button className="erp-table-action" variant="secondary" size="sm" isDisabled={pending || cooldown} onPress={() => changeScope(() => { setView('all'); setShopId(row.shop_pk); setQuery(row.spu_id); setSearchInput(row.spu_id); })}>查看当前商品</Button></Table.Cell></Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    <OffsetPagination label="采购工作台分页" offset={offset} pageSize={limit} total={currentList.total} hasNext={!currentList.error && !nextPageExhausted}
      disabled={pending || cooldown || currentList.loading} onPage={setOffset} />
    <Modal.Backdrop isOpen={editor !== undefined} isDismissable={!pending} onOpenChange={(open) => { if (!open) closeEditor(); }}>
      <Modal.Container size="lg" scroll="inside"><Modal.Dialog className="max-w-2xl gap-5 sm:p-8">
        <Modal.CloseTrigger aria-label="关闭编辑" isDisabled={pending} />
        <Modal.Header className="pr-8"><Modal.Heading className="text-xl font-semibold">编辑商品采购价</Modal.Heading></Modal.Header>
        <Modal.Body>{editor && <form id="purchase-price-editor" className="space-y-5" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <div className="space-y-1"><p className="break-words font-medium">{editor.target.title ?? '未命名商品'}</p><p className="text-sm text-muted">{shopName(editor.target.shop_pk)}</p><p className="break-all text-sm text-muted">SPU {editor.target.spu_id}</p></div>
          <CostFields fields={editor.fields} errors={editor.errors} disabled={commandBlocked} onChange={change} />
        </form>}</Modal.Body>
        <Modal.Footer className="gap-3 border-t border-separator pt-5">
          <Button variant="secondary" isDisabled={pending} onPress={closeEditor}>取消</Button>
          <Button type="submit" form="purchase-price-editor" isDisabled={commandBlocked || !editor || fieldsEqual(initialFields(editor.target), editor.fields)} isPending={saveBusy}>保存采购价</Button>
        </Modal.Footer>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <Modal.Backdrop isOpen={image !== undefined} onOpenChange={(open) => { if (!open) setImage(undefined); }}><Modal.Container size="lg"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭图片" /><Modal.Header><Modal.Heading>商品图片</Modal.Heading></Modal.Header><Modal.Body>{image && <img src={image} alt="商品主图放大" className="max-h-[70vh] max-w-full object-contain" />}</Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}

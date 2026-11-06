import { useState, type FormEvent } from 'react';
import { Button, Chip, Input, Label, TextArea, TextField, buttonVariants } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';
import AssetCard from './AssetCard';
import AssetPreview from './AssetPreview';
import OperationConfirmation from './OperationConfirmation';
import TagEditor, { withPendingTag } from './TagEditor';
import UploadAsset from './UploadAsset';
import { assetKinds, decodeAsset, decodeAssetList, type Asset } from './contracts';
import {
  ActionError, AssetTags, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordPage,
  assetStatus, assetType, humanStatus, mediaPageSize, mediaWrite, recordLink, useLeaveGuard,
  useMediaAction, useMediaRead, useRecordSelection,
} from './media-ui';

const emptyFilters = { keyword: '', kind: '' };

function Usage({ asset, bootstrap }: { asset: Asset; bootstrap: PageProps['bootstrap'] }) {
  if (!asset.usedBy) return <p>当前无法确认引用情况。</p>;
  if (asset.usedBy.length === 0) return <p className="text-muted">还没有数字人引用这份素材。</p>;
  return <ul className="media-stack">
    {asset.usedBy.map((human) => {
      const href = recordLink(bootstrap, 'digital-humans', 'human', human.id);
      return <li className="media-row" key={human.id}>
        {href ? <a className={buttonVariants({ variant: 'ghost' }) + ' media-back [overflow-wrap:anywhere]'} href={href}>{human.name}</a>
          : <span className="[overflow-wrap:anywhere]">{human.name}</span>}
        <Chip size="sm" className="media-chip"><Chip.Label>{humanStatus(human.status)}</Chip.Label></Chip>
      </li>;
    })}
  </ul>;
}

function draftFor(asset: Asset) {
  return { filename: asset.filename, description: asset.description ?? '', tags: [...asset.tags], tagInput: '' };
}

function DeleteAsset({ bootstrap, asset, pending, error, onConfirm, onCancel }: PageProps & {
  asset: Asset; pending: boolean; error: string; onConfirm: () => Promise<void>; onCancel: () => void;
}) {
  // Entering this page always checks current references, including archived personas.
  const read = useMediaRead(bootstrap.rootPath, '/assets/' + asset.assetId, decodeAsset);
  const current = read.data?.assetId === asset.assetId && read.data.usedBy ? read.data : undefined;
  const inUse = Boolean(current?.usedBy?.length);
  async function confirm() {
    if (!current || read.loading || inUse || pending) return;
    try { await onConfirm(); }
    catch (failure) {
      // The parent's action owns the error and write lock. A concurrent link
      // must refresh the explanation without dismissing this confirmation page.
      if (failure instanceof ApiError && failure.status === 409) read.retry();
    }
  }
  return <OperationConfirmation title={'从素材库删除「' + asset.filename + '」'}
    description="只有没有任何数字人引用的素材才能删除。确认后，文件将从全局素材库移除。"
    pending={pending} error={error} confirmLabel="确认删除" danger
    confirmDisabled={!current || read.loading || inUse} onConfirm={confirm} onCancel={onCancel}>
    {current && !read.loading ? <>
      {inUse && <p role="status">此素材仍被数字人引用。请先解除全部引用，再从素材库删除。已归档的档案也需解除引用。</p>}
      <Usage asset={current} bootstrap={bootstrap} />
    </> : <ReadStatus title="当前引用" loading={read.loading}
      error={read.error ?? (!read.loading ? '无法确认哪些数字人引用此素材。请重试后再删除。' : undefined)} onRetry={read.retry} />}
  </OperationConfirmation>;
}

function AssetDetail({ bootstrap, id, initialNotice, onClose, onChanged, onDeleted, returnHuman }: PageProps & {
  id: string; initialNotice: string; onClose: () => void; onChanged: () => void;
  onDeleted: (name: string) => void; returnHuman?: string;
}) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const read = useMediaRead(root, '/assets/' + id, decodeAsset);
  const action = useMediaAction();
  const [updated, setUpdated] = useState<Asset>();
  const [draft, setDraft] = useState<ReturnType<typeof draftFor> | null>(null);
  const [discard, setDiscard] = useState<'view' | 'close'>();
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(initialNotice);
  const [tagCatalog, setTagCatalog] = useState<string[]>();
  const asset = updated ?? read.data;
  const original = asset && draftFor(asset);
  const dirty = Boolean(draft && original && (draft.filename !== original.filename || draft.description !== original.description
    || JSON.stringify(draft.tags) !== JSON.stringify(original.tags) || draft.tagInput.trim()));
  useLeaveGuard(dirty || action.pending);

  function finishExit(target: 'view' | 'close') {
    if (target === 'close') {
      if (returnHuman) window.location.assign(returnHuman);
      else onClose();
    } else { setDraft(null); setDiscard(undefined); action.clearError(); }
  }
  function exit(target: 'view' | 'close') {
    if (action.pending) return;
    if (dirty) setDiscard(target); else finishExit(target);
  }
  async function save() {
    if (!asset || !draft || !original || !dirty || !draft.filename.trim() || !canWrite
      || (draft.tagInput.trim() && !tagCatalog)) return;
    const tags = withPendingTag(draft.tags, draft.tagInput, tagCatalog ?? []);
    await action.run(async () => {
      const response = await mediaWrite(root, '/assets/' + id, 'PATCH', decodeAsset, {
        ...(draft.filename !== original.filename && { filename: draft.filename.trim() }),
        ...(draft.description !== original.description && { description: draft.description.trim() }),
        ...(JSON.stringify(tags) !== JSON.stringify(original.tags) && { tags }),
      });
      if (response.assetId !== id) throw new Error('保存结果不符');
      setUpdated({ ...response, usedBy: response.usedBy ?? asset.usedBy, usageCount: response.usageCount ?? asset.usageCount });
      setDraft(null);
      setNotice('素材资料已保存。');
      onChanged();
    });
  }
  async function remove() {
    if (!asset || !canWrite) return;
    await action.run(async () => {
      await mediaWrite(root, '/assets/' + id, 'DELETE', () => undefined);
      onDeleted(asset.filename);
    }, true);
  }
  if (deleting && asset) return <DeleteAsset bootstrap={bootstrap} asset={asset} pending={action.pending}
    error={action.error} onConfirm={remove} onCancel={() => {
      if (!action.pending) { setDeleting(false); action.clearError(); }
    }} />;

  return <>
    <RecordPage eyebrow={draft ? '资产管理 / 素材资料' : '资产管理 / 素材详情'}
      title={draft ? '编辑素材资料' : asset?.filename ?? '素材详情'} description={!draft && asset ? assetType(asset) : undefined}
      pending={action.pending} backLabel={returnHuman ? '返回档案' : '返回素材库'}
      onClose={draft ? undefined : () => exit('close')}>
      {notice && !draft && <p role="status" className="media-status">{notice}</p>}
      {!asset && <ReadStatus title="素材详情" loading={read.loading} error={read.error} onRetry={read.retry} />}
      {asset && (draft ? <form className="media-box media-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <TextField value={draft.filename} isDisabled={action.pending} onChange={(filename) => setDraft({ ...draft, filename })}>
          <Label>素材名称</Label><Input maxLength={255} />
        </TextField>
        <TextField value={draft.description} isDisabled={action.pending} onChange={(description) => setDraft({ ...draft, description })}>
          <Label>素材说明</Label><TextArea rows={3} maxLength={2000} />
        </TextField>
        <TagEditor root={root} selected={draft.tags} input={draft.tagInput} onCatalog={setTagCatalog} disabled={action.pending}
          onChange={(tags) => setDraft((previous) => previous && { ...previous, tags })}
          onInput={(tagInput) => setDraft((previous) => previous && { ...previous, tagInput })} />
        <p className="text-sm text-muted">保存后，所有引用此素材的位置都会显示更新后的资料。</p>
        <ActionError message={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" isDisabled={!dirty || !draft.filename.trim() || Boolean(draft.tagInput.trim() && !tagCatalog)} isPending={action.pending}>保存资料</Button>
          <Button variant="secondary" isDisabled={action.pending} onPress={() => exit('view')}>取消</Button>
        </div>
      </form> : <>
        <section className="media-box media-form" aria-label="素材资料">
          <AssetPreview asset={asset} root={root} />
          <h2>素材资料</h2>
          {asset.status !== 'ready' && <p role="status">{assetStatus(asset.status)}</p>}
          <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{asset.description || '暂无说明'}</p>
          <AssetTags tags={asset.tags} />
          {canWrite && <div className="flex flex-wrap gap-2">
            <Button isDisabled={action.pending || read.loading} onPress={() => {
              setNotice(''); action.clearError(); setDraft(draftFor(asset));
            }}>编辑资料</Button>
            <Button variant="danger" className="media-danger" isDisabled={action.pending || read.loading} onPress={() => {
              setNotice(''); action.clearError(); setDeleting(true);
            }}>从素材库删除</Button>
          </div>}
        </section>
        <section className="media-box media-form" aria-label="哪些数字人在用">
          <h2>哪些数字人在用</h2><Usage asset={asset} bootstrap={bootstrap} />
        </section>
      </>)}
    </RecordPage>
    <DiscardDialog open={Boolean(discard)} description="本次修改尚未保存，放弃后将清除这些输入。"
      onKeep={() => setDiscard(undefined)} onDiscard={() => { if (discard) finishExit(discard); }} />
  </>;
}

export default function MediaAssets({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [filters, setFilters] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selected, select] = useRecordSelection('asset');
  const [upload, setUpload] = useState(false);
  const [notice, setNotice] = useState('');
  const fromHuman = new URLSearchParams(window.location.search).get('fromHuman');
  const returnHuman = fromHuman && /^[1-9]\d*$/.test(fromHuman)
    ? recordLink(bootstrap, 'digital-humans', 'human', fromHuman) : undefined;
  const params = new URLSearchParams({ limit: String(mediaPageSize) });
  if (offset) params.set('offset', String(offset));
  if (applied.keyword) params.set('keyword', applied.keyword);
  if (applied.kind) params.set('kind', applied.kind);
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList, revision);
  const assets = read.data?.items ?? [];
  const filtered = Boolean(applied.keyword || applied.kind);
  const refresh = () => setRevision((value) => value + 1);

  function clearOrigin() {
    const url = new URL(window.location.href);
    url.searchParams.delete('fromHuman');
    window.history.replaceState(window.history.state, '', url);
  }
  function search(event: FormEvent) {
    event.preventDefault();
    setApplied({ keyword: filters.keyword.trim(), kind: filters.kind });
    setOffset(0); setNotice('');
  }
  function uploaded(id: string, filename: string) {
    const resetMessage = filtered || offset ? '已清除筛选并返回第一页。' : '';
    setUpload(false); setFilters(emptyFilters); setApplied(emptyFilters); setOffset(0);
    clearOrigin(); refresh(); setNotice('已入库：' + filename + '。' + resetMessage); select(id);
  }
  if (upload) return <div className="erp-page media-workspace"><UploadAsset bootstrap={bootstrap}
    onClose={() => setUpload(false)} onComplete={uploaded} /></div>;
  if (selected) return <div className="erp-page media-workspace"><AssetDetail key={selected} bootstrap={bootstrap} id={selected}
    returnHuman={returnHuman} initialNotice={notice} onClose={() => { clearOrigin(); select(null); setNotice(''); }}
    onChanged={refresh} onDeleted={() => {
      clearOrigin(); select(null); setOffset(0); refresh(); setNotice('素材已从全局库删除。');
    }} /></div>;
  return <div className="erp-page media-workspace">
    <RecordPage eyebrow="资产管理" title="全局素材库" description="查看图片和视频，也可以查到每份素材被谁引用。"
      actions={canWrite ? <Button onPress={() => { setNotice(''); setUpload(true); }}>上传素材</Button> : undefined}>
      {!canWrite && <p>你可以查看素材资料和预览。上传或修改需要编辑权限。</p>}
      {notice && <p role="status" className="media-status">{notice}</p>}
      <form className="media-filters" onSubmit={search}>
        <TextField value={filters.keyword} className="media-field" onChange={(keyword) => setFilters({ ...filters, keyword })}>
          <Label>搜索素材</Label><Input placeholder="名称、说明或标签" maxLength={120} />
        </TextField>
        <MediaSelect label="素材类型" value={filters.kind} options={assetKinds}
          onChange={(kind) => setFilters({ ...filters, kind })} />
        <Button type="submit">搜索</Button>
      </form>
      <ReadStatus title="素材列表" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && !assets.length && <p className="media-box">没有符合条件的素材。</p>}
      <ul className="media-grid" data-media-grid aria-label="素材列表">
        {assets.map((asset) => <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root}>
          <h3 className="font-semibold [overflow-wrap:anywhere]">{asset.filename}</h3>
          <div className="flex flex-wrap items-center gap-2">
            <Chip size="sm" className="media-chip"><Chip.Label>{assetType(asset)}</Chip.Label></Chip>
            <span className="text-muted">{asset.usageCount === null ? '引用数量未知'
              : asset.usageCount === 0 || asset.usageCount === '0' ? '未被引用' : String(asset.usageCount) + ' 个数字人引用'}</span>
            {asset.status !== 'ready' && <Chip size="sm" className="media-chip"><Chip.Label>{assetStatus(asset.status)}</Chip.Label></Chip>}
          </div>
          <AssetTags tags={asset.tags} />
          <Button variant="secondary" className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
            onPress={() => { clearOrigin(); setNotice(''); select(asset.assetId); }}>查看 {asset.filename}</Button>
        </AssetCard></li>)}
      </ul>
      <MediaPagination name="素材分页" offset={offset} limit={mediaPageSize} total={read.data?.total ?? 0}
        hasNext={offset + mediaPageSize < Number(read.data?.total ?? 0)} loading={read.loading} onChange={setOffset} />
    </RecordPage>
  </div>;
}

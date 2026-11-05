import { useState, type FormEvent } from 'react';
import { Button, Input, Label, TextArea, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';
import PageIntro from '../../shared/PageIntro';
import AssetCard from './AssetCard';
import AssetPreview from './AssetPreview';
import DeleteConfirmation from './DeleteConfirmation';
import TagEditor, { withPendingTag } from './TagEditor';
import UploadAsset from './UploadAsset';
import { assetKinds, decodeAsset, decodeAssetList, decodeTags, type Asset } from './contracts';
import {
  ActionError, AssetFacts, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordPage,
  humanStatus, mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from './media-ui';

const pageSize = 24;
const emptyFilters = { keyword: '', kind: '', tag: '' };

function Usage({ asset, bootstrap }: { asset: Asset; bootstrap: PageProps['bootstrap'] }) {
  if (!asset.usedBy) return <p>当前无法确认引用情况。</p>;
  if (asset.usedBy.length === 0) return <p>当前没有数字人引用此素材。</p>;
  return <ul className="list-inside list-disc space-y-1">
    {asset.usedBy.map((human) => {
      const href = recordLink(bootstrap, 'digital-humans', 'human', human.id);
      return <li className="break-words" key={human.id}>
        {href ? <a className="underline" href={href}>{human.name}</a> : human.name} · {humanStatus(human.status)}
      </li>;
    })}
  </ul>;
}

function draftFor(asset: Asset) {
  return { filename: asset.filename, description: asset.description ?? '', tags: [...asset.tags], tagInput: '' };
}

function DeleteAsset({ bootstrap, asset, onConfirm }: PageProps & { asset: Asset; onConfirm: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const read = useMediaRead(bootstrap.rootPath, open ? '/assets/' + asset.assetId : null, decodeAsset);
  const current = read.data?.assetId === asset.assetId && read.data.usedBy ? read.data : undefined;
  const inUse = Boolean(current?.usedBy?.length);
  async function confirm() {
    try { await onConfirm(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) read.retry();
      throw failure;
    }
  }
  return <DeleteConfirmation label="从素材库删除" name={asset.filename} onVisibilityChange={setOpen}
    confirmDisabled={!current || read.loading || inUse} onConfirm={confirm} description={<>
      <p>删除后，此文件将从全局素材库移除。只有没有任何数字人引用的素材才能删除。</p>
      {current && !read.loading ? <>
        {inUse && <p role="status">此素材仍被数字人引用。请先解除全部引用，再从素材库删除。已归档的档案也需解除引用。</p>}
        <Usage asset={current} bootstrap={bootstrap} />
      </> : <ReadStatus title="当前引用" loading={read.loading}
        error={read.error ?? (!read.loading ? '无法确认哪些数字人引用此素材。请重试后再删除。' : undefined)} onRetry={read.retry} />}
    </>} />;
}

function AssetDetail({ bootstrap, id, initialNotice, onClose, onChanged, onDeleted }: PageProps & {
  id: string; initialNotice: string; onClose: () => void; onChanged: () => void; onDeleted: (name: string) => void;
}) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const read = useMediaRead(root, '/assets/' + id, decodeAsset);
  const action = useMediaAction();
  const [updated, setUpdated] = useState<Asset>();
  const [draft, setDraft] = useState<ReturnType<typeof draftFor> | null>(null);
  const [discard, setDiscard] = useState<'view' | 'close'>();
  const [notice, setNotice] = useState(initialNotice);
  const asset = updated ?? read.data;
  const original = asset && draftFor(asset);
  const dirty = Boolean(draft && original && (draft.filename !== original.filename || draft.description !== original.description
    || JSON.stringify(draft.tags) !== JSON.stringify(original.tags) || draft.tagInput.trim()));
  useLeaveGuard(dirty || action.pending);

  function finishExit(target: 'view' | 'close') {
    if (target === 'close') onClose();
    else { setDraft(null); setDiscard(undefined); action.clearError(); }
  }
  function exit(target: 'view' | 'close') {
    if (action.pending) return;
    if (dirty) setDiscard(target); else finishExit(target);
  }
  async function save() {
    if (!asset || !draft || !original || !dirty || !draft.filename.trim() || !canWrite) return;
    const tags = withPendingTag(draft.tags, draft.tagInput);
    await action.run(async () => {
      const response = await mediaWrite(root, '/assets/' + id, 'PATCH', decodeAsset, {
        ...(draft.filename !== original.filename && { filename: draft.filename.trim() }),
        ...(draft.description !== original.description && { description: draft.description.trim() }),
        ...(JSON.stringify(tags) !== JSON.stringify(original.tags) && { tags }),
      });
      if (response.assetId !== id) throw new Error('保存结果不符');
      setUpdated({ ...response, usedBy: response.usedBy ?? asset.usedBy, usageCount: response.usageCount ?? asset.usageCount });
      setDraft(null);
      setNotice('素材资料已保存。引用它的数字人会显示更新后的资料。');
      onChanged();
    });
  }
  async function remove() {
    if (!asset || !canWrite) return;
    await mediaWrite(root, '/assets/' + id, 'DELETE', () => undefined);
    onDeleted(asset.filename);
  }
  return <>
    <RecordPage title={asset ? (draft ? '编辑素材 · ' : '素材详情 · ') + asset.filename : '素材详情'}
      pending={action.pending} backLabel={draft ? '返回素材详情' : '返回素材库'} onClose={() => exit(draft ? 'view' : 'close')}>
      {notice && !draft && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
      {!asset && <ReadStatus title="素材详情" loading={read.loading} error={read.error} onRetry={read.retry} />}
      {asset && (draft ? <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <p>这里修改显示名称、说明和标签。此处暂不支持替换文件。</p>
        <p>这是素材库的共享资料，保存后所有引用位置都会显示新资料。</p>
        <Usage asset={asset} bootstrap={bootstrap} />
        <TextField value={draft.filename} isDisabled={action.pending} onChange={(filename) => setDraft({ ...draft, filename })}>
          <Label>显示名称</Label><Input maxLength={255} />
        </TextField>
        <TextField value={draft.description} isDisabled={action.pending} onChange={(description) => setDraft({ ...draft, description })}>
          <Label>说明</Label><TextArea rows={3} maxLength={2000} />
        </TextField>
        <TagEditor root={root} selected={draft.tags} input={draft.tagInput} disabled={action.pending}
          onChange={(tags) => setDraft((previous) => previous && { ...previous, tags })}
          onInput={(tagInput) => setDraft((previous) => previous && { ...previous, tagInput })} />
        <ActionError message={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" isDisabled={!dirty || !draft.filename.trim()} isPending={action.pending}>保存资料</Button>
          <Button variant="secondary" isDisabled={action.pending} onPress={() => exit('view')}>取消编辑</Button>
        </div>
      </form> : <>
        <AssetPreview asset={asset} root={root} />
        <AssetFacts asset={asset} />
        <section aria-label="引用此素材的数字人" className="space-y-2">
          <h3 className="font-semibold">引用此素材的数字人</h3><Usage asset={asset} bootstrap={bootstrap} />
        </section>
        {canWrite && <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onPress={() => { setNotice(''); setDraft(draftFor(asset)); }}>编辑资料</Button>
          <DeleteAsset asset={asset} bootstrap={bootstrap} onConfirm={remove} />
        </div>}
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
  const humanPage = recordLink(bootstrap, 'digital-humans');
  const params = new URLSearchParams({ limit: String(pageSize) });
  if (offset) params.set('offset', String(offset));
  if (applied.keyword) params.set('keyword', applied.keyword);
  if (applied.kind) params.set('kind', applied.kind);
  if (applied.tag) params.set('tag', applied.tag);
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList, revision);
  const tags = useMediaRead(root, '/assets/tags', decodeTags, revision);
  const tagOptions = [...new Set([...(tags.data ?? []), ...(filters.tag ? [filters.tag] : [])])];
  const assets = read.data?.items ?? [];
  const filtered = Boolean(applied.keyword || applied.kind || applied.tag);
  const refresh = () => setRevision((value) => value + 1);
  function search(event: FormEvent) {
    event.preventDefault();
    setApplied({ keyword: filters.keyword.trim(), kind: filters.kind, tag: filters.tag });
    setOffset(0); setNotice('');
  }
  function reset() { setFilters(emptyFilters); setApplied(emptyFilters); setOffset(0); }
  function uploaded(id: string, filename: string) {
    const resetMessage = filtered || offset ? '已清除筛选并返回第一页。' : '';
    setUpload(false); reset(); refresh(); setNotice('已入库：' + filename + '。' + resetMessage); select(id);
  }
  if (upload) return <div className="erp-page"><UploadAsset bootstrap={bootstrap} backLabel="返回素材库" onClose={() => setUpload(false)} onComplete={uploaded} /></div>;
  if (selected) return <div className="erp-page"><AssetDetail key={selected} bootstrap={bootstrap} id={selected} initialNotice={notice} onClose={() => { select(null); setNotice(''); }}
    onChanged={refresh} onDeleted={(name) => {
      select(null);
      if (assets.length === 1 && assets[0].assetId === selected) setOffset(Math.max(0, offset - pageSize));
      refresh(); setNotice('素材「' + name + '」已从素材库删除。');
    }} /></div>;
  return <div className="erp-page">
    <PageIntro eyebrow="内容资产" title="全局素材库" description="上传、查找和整理图片与视频。一个素材可独立保存，也可被多个数字人共享。">
      {humanPage && <a className="underline" href={humanPage}>前往数字人</a>}
      {canWrite && <Button onPress={() => { setNotice(''); setUpload(true); }}>上传素材</Button>}
    </PageIntro>
    {!canWrite && <p>你可以查看素材资料和预览。上传或修改需要编辑权限。</p>}
    {notice && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
    <section className="erp-panel space-y-4" aria-label="素材列表">
      <h2 className="text-xl font-semibold">素材列表</h2>
      <form className="erp-filter-row" onSubmit={search}>
        <TextField value={filters.keyword} onChange={(keyword) => setFilters({ ...filters, keyword })}>
          <Label>搜索素材</Label><Input placeholder="输入名称、说明或标签" maxLength={120} />
        </TextField>
        <MediaSelect label="素材类型" value={filters.kind} options={assetKinds}
          onChange={(kind) => { setFilters({ ...filters, kind }); setApplied({ ...applied, kind }); setOffset(0); }} />
        <MediaSelect label="筛选标签" value={filters.tag} options={[{ value: '', label: '全部标签' }, ...tagOptions.map((tag) => ({ value: tag, label: tag }))]}
          onChange={(tag) => { setFilters({ ...filters, tag }); setApplied({ ...applied, tag }); setOffset(0); }} />
        <Button type="submit">搜索</Button><Button variant="secondary" onPress={reset}>清除筛选</Button>
      </form>
      <ReadStatus title="标签筛选" loading={tags.loading} error={tags.error} onRetry={tags.retry} />
      <ReadStatus title="素材列表" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && (assets.length > 0 ? <p>共 {read.data?.total} 项素材</p>
        : <p>{filtered ? '没有符合当前筛选条件的素材。' : '素材库还没有文件。'}</p>)}
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {assets.map((asset) => <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root}
          title={<h3 className="font-semibold [overflow-wrap:anywhere]">{asset.filename}</h3>}>
          <Button variant="secondary" onPress={() => { setNotice(''); select(asset.assetId); }} aria-label={'查看 ' + asset.filename}>查看详情</Button>
        </AssetCard></li>)}
      </ul>
      <MediaPagination name="素材分页" offset={offset} limit={pageSize}
        hasNext={offset + pageSize < Number(read.data?.total ?? 0)} loading={read.loading} onChange={setOffset} />
    </section>
  </div>;
}

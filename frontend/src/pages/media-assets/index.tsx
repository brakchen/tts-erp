import { useState, type FormEvent } from 'react';
import { Button, Input, Label, TextArea, TextField } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import PageIntro from '../../shared/PageIntro';
import AssetPreview from './AssetPreview';
import DeleteConfirmation from './DeleteConfirmation';
import UploadAsset from './UploadAsset';
import { assetKinds, decodeAsset, decodeAssetList, type Asset } from './contracts';
import {
  ActionError, AssetFacts, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordDialog,
  humanStatus, mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from './media-ui';

const pageSize = 24;
const emptyFilters = { keyword: '', kind: '', tag: '' };

function Usage({ asset, bootstrap, links = true }: { asset: Asset; bootstrap: PageProps['bootstrap']; links?: boolean }) {
  if (!asset.usedBy) return <p>当前无法确认引用情况。</p>;
  if (asset.usedBy.length === 0) return <p>当前没有数字人引用此资产。</p>;
  return <ul className="list-inside list-disc space-y-1">
    {asset.usedBy.map((human) => {
      const href = links ? recordLink(bootstrap, 'digital-humans', 'human', human.id) : undefined;
      return <li className="break-words" key={human.id}>
        {href ? <a className="underline" href={href}>{human.name}</a> : human.name} · {humanStatus(human.status)}
      </li>;
    })}
  </ul>;
}

function draftFor(asset: Asset) {
  return { filename: asset.filename, description: asset.description ?? '', tags: asset.tags.join('、') };
}

function DeleteAsset({ bootstrap, asset, onConfirm }: PageProps & { asset: Asset; onConfirm: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const read = useMediaRead(bootstrap.rootPath, open ? '/assets/' + asset.assetId : null, decodeAsset);
  const current = read.data?.assetId === asset.assetId && read.data.usedBy ? read.data : undefined;
  return <DeleteConfirmation label="从资产库删除" name={asset.filename} onVisibilityChange={setOpen}
    confirmDisabled={!current || read.loading} onConfirm={onConfirm} description={<>
      <p>资产将从全局资产库及所有引用它的数字人中移除。数字人档案会保留。</p>
      {current && !read.loading ? <Usage asset={current} bootstrap={bootstrap} links={false} />
        : <ReadStatus title="当前引用" loading={read.loading}
          error={read.error ?? (!read.loading ? '无法确认哪些数字人引用此资产。请重试后再删除。' : undefined)}
          onRetry={read.retry} />}
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
  const dirty = Boolean(draft && original && (draft.filename !== original.filename || draft.description !== original.description || draft.tags !== original.tags));
  useLeaveGuard(dirty || action.pending);

  function exit(target: 'view' | 'close') {
    if (action.pending) return;
    if (dirty) setDiscard(target);
    else finishExit(target);
  }

  function finishExit(target: 'view' | 'close') {
    if (target === 'close') onClose();
    else {
      setDraft(null);
      setDiscard(undefined);
      action.clearError();
    }
  }

  async function save() {
    if (!asset || !draft || !original || !dirty || !draft.filename.trim() || !canWrite) return;
    await action.run(async () => {
      const response = await mediaWrite(root, '/assets/' + id, 'PATCH', decodeAsset, {
        ...(draft.filename !== original.filename && { filename: draft.filename.trim() }),
        ...(draft.description !== original.description && { description: draft.description.trim() }),
        ...(draft.tags !== original.tags && { tags: draft.tags.split(/[、,，]/).map((item) => item.trim()).filter(Boolean) }),
      });
      if (response.assetId !== id) throw new Error('保存结果不符');
      setUpdated({ ...response, usedBy: response.usedBy ?? asset.usedBy });
      setDraft(null);
      setNotice('资产资料已保存。引用它的数字人会显示更新后的资料。');
      onChanged();
    });
  }

  async function remove() {
    if (!asset || !canWrite) return;
    await mediaWrite(root, '/assets/' + id, 'DELETE', () => undefined);
    onDeleted(asset.filename);
  }

  return <>
    <RecordDialog title={asset ? (draft ? '编辑资料 · ' : '资产详情 · ') + asset.filename : '资产详情'}
      pending={action.pending} onClose={() => exit('close')}>
      {notice && !draft && <p role="status">{notice}</p>}
      {!asset && <ReadStatus title="资产详情" loading={read.loading} error={read.error} onRetry={read.retry} />}
      {asset && (draft ? <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <p>这里修改显示名称、说明和标签。此处暂不支持替换文件。</p>
        <p>这是资产库的共享资料，保存后所有引用位置都会显示新资料。</p>
        <Usage asset={asset} bootstrap={bootstrap} links={false} />
        <TextField value={draft.filename} isDisabled={action.pending} onChange={(filename) => setDraft({ ...draft, filename })}>
          <Label>显示名称</Label><Input maxLength={255} />
        </TextField>
        <TextField value={draft.description} isDisabled={action.pending} onChange={(description) => setDraft({ ...draft, description })}>
          <Label>说明</Label><TextArea rows={3} maxLength={2000} />
        </TextField>
        <TextField value={draft.tags} isDisabled={action.pending} onChange={(tags) => setDraft({ ...draft, tags })}>
          <Label>标签（顿号或逗号分隔）</Label><Input />
        </TextField>
        <ActionError message={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" isDisabled={!dirty || !draft.filename.trim()} isPending={action.pending}>保存资料</Button>
          <Button variant="secondary" isDisabled={action.pending} onPress={() => exit('view')}>取消编辑</Button>
        </div>
      </form> : <>
        <AssetPreview asset={asset} root={root} />
        <AssetFacts asset={asset} />
        <section aria-label="引用此资产的数字人" className="space-y-2">
          <h3 className="font-semibold">引用此资产的数字人</h3>
          <Usage asset={asset} bootstrap={bootstrap} />
        </section>
        {canWrite && <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onPress={() => { setNotice(''); setDraft(draftFor(asset)); }}>编辑资料</Button>
          <DeleteAsset asset={asset} bootstrap={bootstrap} onConfirm={remove} />
        </div>}
      </>)}
    </RecordDialog>
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
  const assets = read.data?.items ?? [];
  const filtered = Boolean(applied.keyword || applied.kind || applied.tag);
  const refresh = () => setRevision((value) => value + 1);

  function search(event: FormEvent) {
    event.preventDefault();
    setApplied({ keyword: filters.keyword.trim(), kind: filters.kind, tag: filters.tag.trim() });
    setOffset(0);
    setNotice('');
  }

  function reset() {
    setFilters(emptyFilters);
    setApplied(emptyFilters);
    setOffset(0);
  }

  function uploaded(id: string, filename: string) {
    const resetMessage = filtered || offset ? '已清除筛选并返回第一页。' : '';
    setUpload(false);
    reset();
    refresh();
    setNotice('已入库：' + filename + '。' + resetMessage);
    select(id);
  }

  return <div className="erp-page">
    <PageIntro eyebrow="内容资产" title="全局资产库" description="在这里上传、查找和整理图片与视频。一个资产可独立保存，也可被多个数字人共享。">
      {humanPage && <a className="underline" href={humanPage}>前往数字人</a>}
      {canWrite && <Button onPress={() => { setNotice(''); setUpload(true); }}>上传资产</Button>}
    </PageIntro>
    {!canWrite && <p>你可以查看资产资料和预览。上传或修改需要编辑权限。</p>}
    {notice && !selected && <p role="status">{notice}</p>}
    <section className="erp-panel space-y-4" aria-label="资产列表">
      <h2 className="text-xl font-semibold">资产列表</h2>
      <form className="erp-filter-row" onSubmit={search}>
        <TextField value={filters.keyword} onChange={(keyword) => setFilters({ ...filters, keyword })}>
          <Label>搜索资产</Label><Input placeholder="输入名称或说明" maxLength={120} />
        </TextField>
        <MediaSelect label="资产类型" value={filters.kind} options={assetKinds}
          onChange={(kind) => { setFilters({ ...filters, kind }); setApplied({ ...applied, kind }); setOffset(0); }} />
        <TextField value={filters.tag} onChange={(tag) => setFilters({ ...filters, tag })}>
          <Label>筛选标签</Label><Input placeholder="输入完整标签" maxLength={64} />
        </TextField>
        <Button type="submit">搜索</Button>
        <Button variant="secondary" onPress={reset}>清除筛选</Button>
      </form>
      <ReadStatus title="资产列表" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && (assets.length > 0
        ? <p>共 {read.data?.total} 项资产</p>
        : <div className="space-y-2">
            <p>{filtered ? '没有符合当前筛选条件的资产。' : '资产库还没有文件。'}</p>
            {!filtered && canWrite && <p>点击上方「上传资产」添加第一张图片或第一个视频。</p>}
          </div>)}
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {assets.map((asset) => <li key={asset.assetId} className="min-w-0 space-y-2 rounded-lg border border-default p-3">
          <Button variant="secondary" className="h-auto min-h-9 max-w-full whitespace-normal break-all"
            onPress={() => { setNotice(''); select(asset.assetId); }} aria-label={'查看 ' + asset.filename}>{asset.filename}</Button>
          <AssetFacts asset={asset} />
        </li>)}
      </ul>
      <MediaPagination name="资产分页" offset={offset} limit={pageSize}
        hasNext={offset + pageSize < Number(read.data?.total ?? 0)} loading={read.loading} onChange={setOffset} />
    </section>
    {selected && !upload && <AssetDetail key={selected} bootstrap={bootstrap} id={selected} initialNotice={notice} onClose={() => select(null)}
      onChanged={refresh} onDeleted={(name) => {
        select(null);
        if (assets.length === 1 && assets[0].assetId === selected) setOffset(Math.max(0, offset - pageSize));
        refresh();
        setNotice('资产「' + name + '」已从资产库及引用位置移除。');
      }} />}
    {upload && <UploadAsset bootstrap={bootstrap} onClose={() => setUpload(false)} onComplete={uploaded} />}
  </div>;
}

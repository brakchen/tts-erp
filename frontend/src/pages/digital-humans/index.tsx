import { useState } from 'react';
import { Button, Input, Label, TextArea, TextField } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import PageIntro from '../../shared/PageIntro';
import AssetPreview from '../media-assets/AssetPreview';
import DeleteConfirmation from '../media-assets/DeleteConfirmation';
import UploadAsset from '../media-assets/UploadAsset';
import {
  decodeDigitalHuman, decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset, type DigitalHuman, type DigitalHumanDetail,
} from '../media-assets/contracts';
import {
  ActionError, AssetFacts, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordDialog,
  createdAtLabel, humanStatus, mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from '../media-assets/media-ui';
import AssetPicker from './AssetPicker';

const pageSize = 24;
const statusOptions = [
  { value: 'active', label: '启用中' },
  { value: 'archived', label: '已归档' },
  { value: '', label: '全部状态' },
];
const emptyDraft = { name: '', description: '' };
type Draft = typeof emptyDraft;

function HumanForm({ draft, setDraft, pending, error, submitLabel, cancelLabel, onSubmit, onCancel, unchanged = false }: {
  draft: Draft; setDraft: (draft: Draft) => void; pending: boolean; error: string;
  submitLabel: string; cancelLabel: string; onSubmit: () => void; onCancel: () => void; unchanged?: boolean;
}) {
  return <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <TextField value={draft.name} isDisabled={pending} onChange={(name) => setDraft({ ...draft, name })}>
      <Label>名称</Label><Input maxLength={120} />
    </TextField>
    <TextField value={draft.description} isDisabled={pending} onChange={(description) => setDraft({ ...draft, description })}>
      <Label>人设说明</Label><TextArea rows={3} maxLength={2000} />
    </TextField>
    <ActionError message={error} />
    <div className="flex flex-wrap gap-2">
      <Button type="submit" isDisabled={unchanged || !draft.name.trim()} isPending={pending}>{submitLabel}</Button>
      <Button variant="secondary" isDisabled={pending} onPress={onCancel}>{cancelLabel}</Button>
    </div>
  </form>;
}

function CreateHuman({ root, onClose, onCreated }: {
  root: string; onClose: () => void; onCreated: (human: DigitalHuman) => void;
}) {
  const [draft, setDraft] = useState(emptyDraft);
  const [discard, setDiscard] = useState(false);
  const action = useMediaAction();
  const dirty = Boolean(draft.name || draft.description);
  useLeaveGuard(dirty || action.pending);
  function close() {
    if (action.pending) return;
    if (dirty) setDiscard(true);
    else onClose();
  }
  function create() {
    if (!draft.name.trim()) return;
    void action.run(async () => {
      const created = await mediaWrite(root, '/digital-humans', 'POST', decodeDigitalHuman, {
        name: draft.name.trim(), description: draft.description.trim() || null,
      });
      onCreated(created);
    });
  }
  return <>
    <RecordDialog title="新建数字人" pending={action.pending} onClose={close}>
      <p>先建立人设档案。创建后会打开档案，你可以选择已有资产或上传新资产。</p>
      {action.uncertain && <p>再次提交前，请核对是否已经创建同名档案。 <a className="underline"
        href={pathFor(root, '/v2/pages/digital-humans')} target="_blank" rel="noreferrer">在新标签页核对档案</a></p>}
      <HumanForm draft={draft} setDraft={setDraft} pending={action.pending}
        error={action.error && (action.uncertain ? action.error : '创建未完成。' + action.error)} submitLabel="创建数字人" cancelLabel="取消创建"
        onSubmit={create} onCancel={close} />
    </RecordDialog>
    <DiscardDialog open={discard} description="尚未保存的档案资料将清除。" onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}

function HumanDetail({ bootstrap, id, initialNotice, onClose, onChanged, onDeleted }: PageProps & {
  id: string; initialNotice: string; onClose: () => void; onChanged: () => void; onDeleted: (name: string) => void;
}) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const read = useMediaRead(root, '/digital-humans/' + id, decodeDigitalHumanDetail);
  const action = useMediaAction();
  const [override, setOverride] = useState<DigitalHumanDetail>();
  const [mode, setMode] = useState<'view' | 'pick' | 'upload'>('view');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [discard, setDiscard] = useState<'view' | 'close'>();
  const [preview, setPreview] = useState<string>();
  const [notice, setNotice] = useState(initialNotice);
  const [pickerNotice, setPickerNotice] = useState('');
  const human = override ?? read.data;
  const dirty = Boolean(draft && human && (draft.name !== human.name || draft.description !== (human.description ?? '')));
  useLeaveGuard(dirty || action.pending);

  function leave(target: 'view' | 'close') {
    if (action.pending) return;
    if (dirty) setDiscard(target);
    else finishLeave(target);
  }

  function finishLeave(target: 'view' | 'close') {
    if (target === 'close') onClose();
    else {
      setDraft(null);
      setDiscard(undefined);
      action.clearError();
    }
  }

  function edit() {
    if (!human) return;
    setNotice('');
    action.clearError();
    setDraft({ name: human.name, description: human.description ?? '' });
  }

  function save() {
    if (!human || !draft?.name.trim() || !dirty || !canWrite) return;
    void action.run(async () => {
      const updated = await mediaWrite(root, '/digital-humans/' + id, 'PATCH', decodeDigitalHuman, {
        ...(draft.name !== human.name && { name: draft.name.trim() }),
        ...(draft.description !== (human.description ?? '') && { description: draft.description.trim() }),
      });
      if (updated.digitalHumanId !== id) throw new Error('保存结果不符');
      setOverride({ ...human, ...updated });
      setDraft(null);
      setNotice('数字人资料已保存。');
      onChanged();
    });
  }

  function archive() {
    if (!human || !canWrite) return;
    const archived = human.status !== 'archived';
    void action.run(async () => {
      const updated = await mediaWrite(root, '/digital-humans/' + id, 'PATCH', decodeDigitalHuman, {
        status: archived ? 'archived' : 'active',
      });
      if (updated.digitalHumanId !== id) throw new Error('归档状态结果不符');
      setOverride({ ...human, ...updated });
      setNotice(archived ? '已归档。资产引用已保留，可在「已归档」列表找到此档案。' : '已恢复为启用，可在「启用中」列表找到此档案。');
      onChanged();
    });
  }

  async function remove() {
    if (!human || !canWrite) return;
    await mediaWrite(root, '/digital-humans/' + id, 'DELETE', () => undefined);
    onDeleted(human.name);
  }

  function attach(asset: Asset) {
    if (!human || !canWrite || human.assetIds.includes(asset.assetId)) return;
    setPickerNotice('');
    void action.run(async () => {
      const updated = await mediaWrite(root, '/digital-humans/' + id + '/assets', 'POST', decodeDigitalHuman, { assetId: asset.assetId });
      if (updated.digitalHumanId !== id || !updated.assetIds.includes(asset.assetId)) throw new Error('添加结果不符');
      setOverride({ ...human, ...updated, assets: [...human.assets, asset] });
      setPickerNotice('已添加「' + asset.filename + '」。');
      setNotice('已添加「' + asset.filename + '」。');
      onChanged();
    });
  }

  async function detach(asset: Asset) {
    if (!human || !canWrite) return;
    await mediaWrite(root, '/digital-humans/' + id + '/assets/' + asset.assetId, 'DELETE', () => undefined);
    setOverride({
      ...human, assets: human.assets.filter((item) => item.assetId !== asset.assetId),
      assetIds: human.assetIds.filter((item) => item !== asset.assetId),
    });
    setNotice('已从当前数字人移除「' + asset.filename + '」。资产库中的文件和其他引用仍可使用。');
    onChanged();
  }

  function begin(next: 'pick' | 'upload') {
    setPickerNotice('');
    setNotice('');
    action.clearError();
    setMode(next);
  }

  function backFromPicker() {
    setMode('view');
    action.clearError();
  }

  if (human && mode === 'pick') {
    return <AssetPicker root={root} human={human} pending={action.pending} error={action.error}
      notice={pickerNotice} onAdd={attach} onClose={backFromPicker} />;
  }

  if (human && mode === 'upload') {
    return <UploadAsset bootstrap={bootstrap} target={{ id, name: human.name }}
      onClose={(assetId) => {
        setMode('view');
        if (assetId) setNotice('新资产已保留在全局资产库，可通过「选择已有资产」再次添加。');
      }}
      onComplete={(_assetId, filename) => {
        setMode('view');
        setOverride(undefined);
        read.retry();
        onChanged();
        setNotice('「' + filename + '」已入库并添加到此档案。');
      }} />;
  }

  return <>
    <RecordDialog title={human ? (draft ? '编辑资料 · ' : '数字人详情 · ') + human.name : '数字人详情'}
      pending={action.pending} onClose={() => leave('close')}>
      {notice && !draft && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
      <ReadStatus title="数字人详情" loading={read.loading} error={read.error} onRetry={read.retry} />
      {human && (draft ? <>
        <p>在此修改档案名称与人设说明。素材在详情的「引用资产」中管理。</p>
        <HumanForm draft={draft} setDraft={setDraft} pending={action.pending} unchanged={!dirty}
          error={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} submitLabel="保存资料" cancelLabel="取消编辑"
          onSubmit={save} onCancel={() => leave('view')} />
      </> : <>
        <div className="space-y-2">
          <p className="font-semibold">{humanStatus(human.status)}</p>
          <p className="text-sm text-muted">{createdAtLabel(human.createdAt)}</p>
          <p className="whitespace-pre-wrap break-words">{human.description || '暂未填写人设说明。'}</p>
          <p className="text-sm text-muted">{human.status === 'archived'
            ? '已归档的档案可查看和维护，保留原来的资产引用。可以随时恢复为启用。'
            : '启用中的档案显示在默认列表。暂时不用时可以归档，引用的资产会保留。'}</p>
        </div>
        <ActionError message={action.error} />
        {canWrite && <div className="flex flex-wrap gap-2">
          <Button variant="secondary" isDisabled={action.pending || read.loading} onPress={edit}>编辑资料</Button>
          <Button variant={human.status === 'archived' ? 'primary' : 'secondary'} isPending={action.pending} isDisabled={read.loading} onPress={archive}>
            {human.status === 'archived' ? '恢复为启用' : '归档数字人'}
          </Button>
          <DeleteConfirmation label="删除数字人" name={human.name} isDisabled={action.pending || read.loading}
            description="档案和它的引用关系将被移除。资产库中的文件以及其他数字人的引用会保留。" onConfirm={remove} />
        </div>}
        <section aria-label="引用资产" className="space-y-3 border-t border-default pt-3">
          <h3 className="text-lg font-semibold">引用资产</h3>
          <p>素材来自全局资产库，可以在多个数字人之间共享。</p>
          {canWrite && <div className="flex flex-wrap gap-2">
            <Button isDisabled={action.pending || read.loading} onPress={() => begin('pick')}>选择已有资产</Button>
            <Button variant="secondary" isDisabled={action.pending || read.loading} onPress={() => begin('upload')}>上传新资产</Button>
          </div>}
          {human.assets.length === 0 && !read.loading && <p>此档案还没有引用资产。</p>}
          <ul className="space-y-3">
            {human.assets.map((asset) => {
              const href = recordLink(bootstrap, 'media-assets', 'asset', asset.assetId);
              return <li key={asset.assetId} className="min-w-0 space-y-2 rounded-lg border border-default p-3">
                <h4 className="break-words font-semibold">{asset.filename}</h4>
                <AssetFacts asset={asset} />
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="secondary" aria-expanded={preview === asset.assetId}
                    aria-label={(preview === asset.assetId ? '收起预览 ' : '预览 ') + asset.filename}
                    onPress={() => setPreview(preview === asset.assetId ? undefined : asset.assetId)}>
                    {preview === asset.assetId ? '收起预览' : '预览'}
                  </Button>
                  {href && <a className="underline" href={href} aria-label={'在资产库查看 ' + asset.filename}>在资产库查看</a>}
                  {canWrite && <DeleteConfirmation remove label={'从当前数字人移除 ' + asset.filename} name={asset.filename}
                    isDisabled={action.pending || read.loading}
                    description={'将解除「' + human.name + '」对这个资产的引用。文件和其他数字人的引用会保留。'}
                    onConfirm={() => detach(asset)} />}
                </div>
                {preview === asset.assetId && <AssetPreview asset={asset} root={root} />}
              </li>;
            })}
          </ul>
        </section>
      </>)}
    </RecordDialog>
    <DiscardDialog open={Boolean(discard)} description="本次修改尚未保存，放弃后将清除这些输入。"
      onKeep={() => setDiscard(undefined)} onDiscard={() => { if (discard) finishLeave(discard); }} />
  </>;
}

export default function DigitalHumans({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const assetsPage = recordLink(bootstrap, 'media-assets');
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('active');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selected, select] = useRecordSelection('human');
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState('');
  const params = new URLSearchParams({ limit: String(pageSize + 1) });
  if (query) params.set('keyword', query);
  if (status) params.set('status', status);
  if (offset) params.set('offset', String(offset));
  const read = useMediaRead(root, '/digital-humans?' + params, decodeDigitalHumanList, revision);
  const humans = read.data?.slice(0, pageSize) ?? [];
  const refresh = () => setRevision((value) => value + 1);

  function created(human: DigitalHuman) {
    const reset = Boolean(query || offset || status !== 'active');
    setCreating(false);
    setKeyword('');
    setQuery('');
    setStatus('active');
    setOffset(0);
    refresh();
    setNotice('已创建数字人：' + human.name + '。接下来可以添加素材。' + (reset ? '列表已返回「启用中」首页。' : ''));
    select(human.digitalHumanId);
  }

  return <div className="erp-page">
    <PageIntro eyebrow="内容资产" title="数字人档案" description="在这里建立和维护人设档案，再把适用的图片和视频添加到档案。素材集中保存在全局资产库。">
      {assetsPage && <a className="underline" href={assetsPage}>前往资产库</a>}
      {canWrite && <Button onPress={() => { setNotice(''); setCreating(true); }}>新建数字人</Button>}
    </PageIntro>
    {!canWrite && <p>你可以查看档案和引用资产。创建或修改档案需要编辑权限。</p>}
    {notice && !selected && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
    <section className="erp-panel space-y-4" aria-label="数字人列表">
      <h2 className="text-xl font-semibold">数字人列表</h2>
      <form className="erp-filter-row" onSubmit={(event) => { event.preventDefault(); setQuery(keyword.trim()); setOffset(0); setNotice(''); }}>
        <TextField value={keyword} onChange={setKeyword}>
          <Label>搜索数字人</Label><Input placeholder="输入名称或人设说明" maxLength={120} />
        </TextField>
        <MediaSelect label="档案状态" value={status} options={statusOptions}
          onChange={(next) => { setStatus(next); setOffset(0); setNotice(''); }} />
        <Button type="submit">搜索</Button>
        <Button variant="secondary" onPress={() => {
          setKeyword(''); setQuery(''); setStatus('active'); setOffset(0); setNotice('');
        }}>清除筛选</Button>
      </form>
      <ReadStatus title="数字人列表" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && humans.length === 0 && (
        <p>{query ? '没有符合搜索条件的档案。' : status === 'archived' ? '当前没有已归档的数字人。'
          : '当前列表还没有数字人。' + (canWrite ? '点击「新建数字人」建立档案。' : '')}</p>
      )}
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {humans.map((human) => <li key={human.digitalHumanId} className="min-w-0 space-y-2 rounded-lg border border-default p-3">
          <Button variant="secondary" className="h-auto min-h-9 max-w-full whitespace-normal break-all"
            aria-label={'查看 ' + human.name} onPress={() => { setNotice(''); select(human.digitalHumanId); }}>{human.name}</Button>
          <p>{humanStatus(human.status)} · {human.assetIds.length} 个引用资产</p>
          <p className="text-sm text-muted">{createdAtLabel(human.createdAt)}</p>
          {human.description && <p className="line-clamp-2 break-words">{human.description}</p>}
        </li>)}
      </ul>
      <MediaPagination name="数字人分页" offset={offset} limit={pageSize}
        hasNext={Boolean(read.data && read.data.length > pageSize)} loading={read.loading} onChange={setOffset} />
    </section>
    {selected && !creating && <HumanDetail key={selected} bootstrap={bootstrap} id={selected} initialNotice={notice}
      onClose={() => select(null)} onChanged={refresh} onDeleted={(name) => {
        select(null);
        if (humans.length === 1 && humans[0].digitalHumanId === selected) setOffset(Math.max(0, offset - pageSize));
        refresh();
        setNotice('数字人「' + name + '」已删除。引用过的资产仍在资产库中。');
      }} />}
    {creating && <CreateHuman root={root} onClose={() => setCreating(false)} onCreated={created} />}
  </div>;
}

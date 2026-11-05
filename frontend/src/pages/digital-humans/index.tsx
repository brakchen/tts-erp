import { useEffect, useState } from 'react';
import { Button, Card, Input, Label, TextArea, TextField } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import PageIntro from '../../shared/PageIntro';
import AssetCard from '../media-assets/AssetCard';
import DeleteConfirmation from '../media-assets/DeleteConfirmation';
import UploadAsset from '../media-assets/UploadAsset';
import {
  decodeDigitalHuman, decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset, type DigitalHuman, type DigitalHumanDetail,
} from '../media-assets/contracts';
import {
  ActionError, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordPage,
  createdAtLabel, humanStatus, mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from '../media-assets/media-ui';
import AssetPicker, { emptyPickerDraft } from './AssetPicker';

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
    <RecordPage title="新建数字人" pending={action.pending} onClose={close} backLabel="返回数字人列表">
      <p>先建立人设档案。创建后会打开档案，你可以选择已有素材或上传新素材。</p>
      {action.uncertain && <p>再次提交前，请核对是否已经创建同名档案。 <a className="underline"
        href={pathFor(root, '/v2/pages/digital-humans')} target="_blank" rel="noreferrer">在新标签页核对档案</a></p>}
      <HumanForm draft={draft} setDraft={setDraft} pending={action.pending}
        error={action.error && (action.uncertain ? action.error : '创建未完成。' + action.error)} submitLabel="创建数字人" cancelLabel="取消创建"
        onSubmit={create} onCancel={close} />
    </RecordPage>
    <DiscardDialog open={discard} description="尚未保存的档案资料将清除。" onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}

function HumanDetail({ bootstrap, id, initialNotice, onClose, onChanged, onDeleted, initialMode = 'view' }: PageProps & {
  id: string; initialNotice: string; onClose: () => void; onChanged: () => void; onDeleted: (name: string) => void; initialMode?: 'view' | 'pick';
}) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const read = useMediaRead(root, '/digital-humans/' + id, decodeDigitalHumanDetail);
  const action = useMediaAction();
  const [override, setOverride] = useState<DigitalHumanDetail>();
  const [mode, setMode] = useState<'view' | 'pick' | 'upload'>(initialMode);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [discard, setDiscard] = useState<'view' | 'close'>();
  const [pickerDraft, setPickerDraft] = useState(emptyPickerDraft);
  const [uploadOrigin, setUploadOrigin] = useState<'view' | 'pick'>('view');
  const [notice, setNotice] = useState(initialNotice);
  const [pickerNotice, setPickerNotice] = useState('');
  useEffect(() => { if (read.data) setOverride(undefined); }, [read.data]);
  const human = override ?? read.data;
  const dirty = Boolean(draft && human && (draft.name !== human.name || draft.description !== (human.description ?? '')));
  useLeaveGuard(dirty || action.pending || pickerDraft.selected.some((asset) => !human?.assetIds.includes(asset.assetId)));

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

  function attach(assets: readonly Asset[]) {
    if (!human || !canWrite || !assets.length) return;
    void action.run(async () => {
      setPickerNotice('');
      let current = human;
      let added = 0;
      try {
        for (const asset of assets) {
          if (current.assetIds.includes(asset.assetId)) continue;
          const updated = await mediaWrite(root, '/digital-humans/' + id + '/assets', 'POST', decodeDigitalHuman, { assetId: asset.assetId });
          if (updated.digitalHumanId !== id || !updated.assetIds.includes(asset.assetId)) throw new Error('添加结果不符');
          current = { ...current, ...updated, assets: [...current.assets, asset] };
          setOverride(current);
          added += 1;
        }
      } finally {
        if (added) {
          const message = '已添加 ' + added + ' 项资产。';
          setPickerNotice(message);
          setNotice(message);
          onChanged();
        }
      }
      setPickerDraft(emptyPickerDraft);
      setMode('view');
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
    if (next === 'pick') setPickerDraft(emptyPickerDraft);
    if (next === 'upload') setUploadOrigin('view');
    setMode(next);
  }

  function backFromPicker() {
    setMode('view');
    setPickerDraft(emptyPickerDraft);
    action.clearError();
  }

  if (human && mode === 'pick') {
    return <AssetPicker root={root} human={human} pending={action.pending} error={action.error}
      notice={pickerNotice} onAdd={attach} onClose={backFromPicker} draft={pickerDraft} onDraft={setPickerDraft}
      onUpload={() => { action.clearError(); setUploadOrigin('pick'); setMode('upload'); }} />;
  }

  if (human && mode === 'upload') {
    const selectedCount = pickerDraft.selected.filter((asset) => !human.assetIds.includes(asset.assetId)).length;
    return <UploadAsset bootstrap={bootstrap} target={{ id, name: human.name }}
      backLabel={uploadOrigin === 'pick' ? '返回选择素材' : '返回档案'} selectionCount={selectedCount}
      onClose={(assetId) => {
        setMode(uploadOrigin);
        const message = assetId ? '新素材已保留在全局素材库，尚未确认添加至此档案，可在选择页继续添加。' : '';
        if (uploadOrigin === 'pick') setPickerNotice(message); else setNotice(message);
      }}
      onComplete={(assetId, filename) => {
        // Preserve A/B and the picker query while recording C's successful association.
        setOverride({ ...human, assetIds: [...new Set([...human.assetIds, assetId])] });
        read.retry();
        setMode(uploadOrigin);
        onChanged();
        const message = '「' + filename + '」已入库并添加到此档案。' + (selectedCount ? '原有 ' + selectedCount + ' 项待添加选择已保留，请继续确认。' : '');
        setNotice(message); setPickerNotice(message);
      }} />;
  }

  return <>
    <RecordPage title={human ? (draft ? '编辑数字人 · ' : '数字人档案 · ') + human.name : '数字人档案'}
      target={human?.name} backLabel={draft ? '返回档案' : '返回数字人列表'} pending={action.pending} onClose={() => leave(draft ? 'view' : 'close')}>
      {notice && !draft && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
      <ReadStatus title="数字人档案" loading={read.loading} error={read.error} onRetry={read.retry} />
      {human && (draft ? <>
        <p>在此修改档案名称与人设说明。素材在详情的「引用素材」中管理。</p>
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
        <section aria-label="引用素材" className="space-y-3 border-t border-default pt-3">
          <h3 className="text-lg font-semibold">引用素材</h3>
          <p>素材来自全局资产库，可以在多个数字人之间共享。</p>
          {canWrite && <div className="flex flex-wrap gap-2">
            <Button isDisabled={action.pending || read.loading} onPress={() => begin('pick')}>选择已有素材</Button>
            <Button variant="secondary" isDisabled={action.pending || read.loading} onPress={() => begin('upload')}>上传新素材</Button>
          </div>}
          {human.assets.length === 0 && !read.loading && <p>此档案还没有引用素材。</p>}
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {human.assets.map((asset) => {
              const href = recordLink(bootstrap, 'media-assets', 'asset', asset.assetId);
              return <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root}>
                <div className="flex flex-wrap items-center gap-2">
                  {href && <a className="underline" href={href} aria-label={'在素材库查看 ' + asset.filename}>在素材库查看</a>}
                  {canWrite && <DeleteConfirmation remove label={'从当前数字人移除 ' + asset.filename} name={asset.filename}
                    isDisabled={action.pending || read.loading}
                    description={'将解除「' + human.name + '」对这个素材的引用。文件和其他数字人的引用会保留。'}
                    onConfirm={() => detach(asset)} />}
                </div>
              </AssetCard></li>;
            })}
          </ul>
        </section>
      </>)}
    </RecordPage>
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
  const [openMode, setOpenMode] = useState<'view' | 'pick'>('view');
  const listAction = useMediaAction();
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
    setOpenMode('view');
    select(human.digitalHumanId);
  }

  if (creating) return <div className="erp-page"><CreateHuman root={root} onClose={() => setCreating(false)} onCreated={created} /></div>;
  if (selected) return <div className="erp-page"><HumanDetail key={selected} bootstrap={bootstrap} id={selected} initialNotice={notice} initialMode={openMode}
    onClose={() => { select(null); setNotice(''); }} onChanged={refresh} onDeleted={(name) => {
      select(null);
      if (humans.length === 1 && humans[0].digitalHumanId === selected) setOffset(Math.max(0, offset - pageSize));
      refresh(); setNotice('数字人「' + name + '」已删除。引用过的素材仍在素材库中。');
    }} /></div>;

  return <div className="erp-page">
    <PageIntro eyebrow="内容资产" title="数字人档案" description="在这里建立和维护人设档案，再把适用的图片和视频添加到档案。素材集中保存在全局资产库。">
      {assetsPage && <a className="underline" href={assetsPage}>前往资产库</a>}
      {canWrite && <Button onPress={() => { setNotice(''); setCreating(true); }}>新建数字人</Button>}
    </PageIntro>
    {!canWrite && <p>你可以查看档案和引用素材。创建或修改档案需要编辑权限。</p>}
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
      <ActionError message={listAction.error} />
      <ul className="space-y-3">
        {humans.map((human) => <li key={human.digitalHumanId} className="min-w-0"><Card><Card.Content>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <h3 className="font-semibold [overflow-wrap:anywhere]">{human.name}</h3>
              <p>{humanStatus(human.status)} · {human.assetIds.length} 个引用素材</p>
              <p className="text-sm text-muted">{createdAtLabel(human.createdAt)}</p>
              {human.description && <p className="line-clamp-2 break-words">{human.description}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" aria-label={'查看档案 ' + human.name}
                onPress={() => { setNotice(''); setOpenMode('view'); select(human.digitalHumanId); }}>查看档案</Button>
              {canWrite && <Button aria-label={'添加素材 ' + human.name}
                onPress={() => { setNotice(''); setOpenMode('pick'); select(human.digitalHumanId); }}>添加素材</Button>}
              {canWrite && human.status === 'archived' && <Button variant="secondary" aria-label={'恢复为启用 ' + human.name}
                isPending={listAction.pending} onPress={() => void listAction.run(async () => {
                  const updated = await mediaWrite(root, '/digital-humans/' + human.digitalHumanId, 'PATCH', decodeDigitalHuman, { status: 'active' });
                  if (updated.digitalHumanId !== human.digitalHumanId) throw new Error('恢复结果不符');
                  setNotice('「' + human.name + '」已恢复为启用。'); refresh();
                })}>恢复为启用</Button>}
            </div>
          </div>
        </Card.Content></Card></li>)}
      </ul>
      <MediaPagination name="数字人分页" offset={offset} limit={pageSize}
        hasNext={Boolean(read.data && read.data.length > pageSize)} loading={read.loading} onChange={setOffset} />
    </section>
  </div>;
}

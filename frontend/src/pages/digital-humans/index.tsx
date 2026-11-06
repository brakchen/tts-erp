import { useState } from 'react';
import { Button, Chip, Input, Label, TextArea, TextField, buttonVariants } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import OperationConfirmation from '../media-assets/OperationConfirmation';
import UploadAsset from '../media-assets/UploadAsset';
import {
  decodeDigitalHuman, decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset, type DigitalHuman, type DigitalHumanDetail,
} from '../media-assets/contracts';
import {
  ActionError, AssetTags, DiscardDialog, MediaPagination, ReadStatus, RecordPage,
  assetType, humanStatus, mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from '../media-assets/media-ui';
import AssetPicker, { emptyPickerDraft } from './AssetPicker';

// The persona endpoint is a bounded array contract, unlike the asset total/offset envelope.
const humanPageSize = 24;
const emptyDraft = { name: '', description: '' };
type Draft = typeof emptyDraft;
type HumanStatus = 'active' | 'archived';
type Operation = 'archive' | 'delete' | Asset;

function HumanForm({ draft, setDraft, pending, error, submitLabel, onSubmit, onCancel, unchanged = false }: {
  draft: Draft; setDraft: (draft: Draft) => void; pending: boolean; error: string;
  submitLabel: string; onSubmit: () => void; onCancel: () => void; unchanged?: boolean;
}) {
  return <form className="media-box media-form" aria-label="档案资料" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <TextField value={draft.name} isDisabled={pending} onChange={(name) => setDraft({ ...draft, name })}>
      <Label>数字人名称</Label><Input maxLength={120} />
    </TextField>
    <TextField value={draft.description} isDisabled={pending} onChange={(description) => setDraft({ ...draft, description })}>
      <Label>人设说明</Label><TextArea rows={3} maxLength={2000} />
    </TextField>
    <ActionError message={error} />
    <div className="flex flex-wrap gap-2">
      <Button type="submit" isDisabled={unchanged || !draft.name.trim()} isPending={pending}>{submitLabel}</Button>
      <Button variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button>
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
    <RecordPage eyebrow="数字人 / 档案资料" title="新建数字人">
      {action.uncertain && <p>再次提交前，请核对是否已经创建同名档案。 <a className="underline"
        href={pathFor(root, '/v2/pages/digital-humans')} target="_blank" rel="noreferrer">在新标签页核对档案</a></p>}
      <HumanForm draft={draft} setDraft={setDraft} pending={action.pending}
        error={action.error && (action.uncertain ? action.error : '创建未完成。' + action.error)} submitLabel="创建档案"
        onSubmit={create} onCancel={close} />
    </RecordPage>
    <DiscardDialog open={discard} description="尚未保存的档案资料将清除。" onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}

function HumanDetail({ bootstrap, id, initialNotice, onClose, onChanged, onDeleted, onStatusChanged, initialMode = 'view' }: PageProps & {
  id: string; initialNotice: string; onClose: () => void; onChanged: () => void; onDeleted: (name: string) => void;
  onStatusChanged: (status: HumanStatus, name: string) => void; initialMode?: 'view' | 'pick';
}) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [snapshot, setSnapshot] = useState<{ revision: number; acknowledged?: DigitalHumanDetail }>({ revision: 0 });
  const read = useMediaRead(root, '/digital-humans/' + id, decodeDigitalHumanDetail, snapshot.revision);
  const action = useMediaAction();
  const [mode, setMode] = useState<'view' | 'pick' | 'upload'>(initialMode);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [operation, setOperation] = useState<Operation>();
  const [discard, setDiscard] = useState<'view' | 'close'>();
  const [pickerDraft, setPickerDraft] = useState(emptyPickerDraft);
  const [notice, setNotice] = useState(initialNotice);
  const [pickerNotice, setPickerNotice] = useState('');
  // Only a read from the acknowledged write's generation can replace its result.
  const human = read.data ?? snapshot.acknowledged;
  function acknowledge(value: DigitalHumanDetail) {
    setSnapshot((previous) => ({ revision: previous.revision + 1, acknowledged: value }));
  }
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
      acknowledge({ ...human, ...updated });
      setDraft(null);
      setNotice('档案资料已保存。');
      onChanged();
    });
  }

  async function changeStatus(status: HumanStatus) {
    if (!human || !canWrite) return;
    await action.run(async () => {
      const updated = await mediaWrite(root, '/digital-humans/' + id, 'PATCH', decodeDigitalHuman, { status });
      if (updated.digitalHumanId !== id) throw new Error('归档状态结果不符');
      acknowledge({ ...human, ...updated });
      if (status === 'active') setNotice('档案已恢复，原有引用已保留。');
      onStatusChanged(status, human.name);
    });
  }

  async function remove() {
    if (!human || !canWrite) return;
    await action.run(async () => {
      await mediaWrite(root, '/digital-humans/' + id, 'DELETE', () => undefined);
      onDeleted(human.name);
    });
  }

  function attach(assets: readonly Asset[]) {
    if (!human || !canWrite || !assets.length || read.loading || read.error) return;
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
          acknowledge(current);
          added += 1;
        }
      } finally {
        if (added) {
          const message = '已添加 ' + added + ' 项素材。';
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
    await action.run(async () => {
      await mediaWrite(root, '/digital-humans/' + id + '/assets/' + asset.assetId, 'DELETE', () => undefined);
      acknowledge({
        ...human, assets: human.assets.filter((item) => item.assetId !== asset.assetId),
        assetIds: human.assetIds.filter((item) => item !== asset.assetId),
      });
      setOperation(undefined);
      setNotice('已从当前档案移除。');
      onChanged();
    });
  }

  function ask(next: Operation) {
    if (action.pending || read.loading || !canWrite) return;
    action.clearError();
    setNotice('');
    setOperation(next);
  }

  function beginPicker() {
    setPickerNotice('');
    setNotice('');
    action.clearError();
    setPickerDraft(emptyPickerDraft);
    setMode('pick');
  }

  function backFromPicker() {
    setMode('view');
    setPickerDraft(emptyPickerDraft);
    action.clearError();
  }

  if (human && mode === 'pick') {
    return <AssetPicker root={root} human={human} pending={action.pending} error={action.error}
      notice={pickerNotice} onAdd={attach} onClose={backFromPicker} draft={pickerDraft} onDraft={setPickerDraft}
      targetRead={read}
      onUpload={() => { action.clearError(); setMode('upload'); }} />;
  }

  if (human && mode === 'upload') {
    const selectedCount = pickerDraft.selected.filter((asset) => !human.assetIds.includes(asset.assetId)).length;
    return <UploadAsset bootstrap={bootstrap} target={{ id, name: human.name }} selectionCount={selectedCount}
      onClose={(assetId) => {
        setMode('pick');
        setPickerNotice(assetId ? '新素材已保留在全局素材库，尚未添加至此档案，可继续选择添加。' : '');
      }}
      onComplete={(assetId, filename) => {
        // Keep A/B pending while acknowledging C. Do not synthesize C's file metadata.
        acknowledge({ ...human, assetIds: [...new Set([...human.assetIds, assetId])] });
        const message = '「' + filename + '」已入库并添加到此档案。'
          + (selectedCount ? '原有 ' + selectedCount + ' 项待添加选择已保留，请继续确认。' : '');
        setNotice(message); setPickerNotice(message);
        if (!selectedCount) setPickerDraft(emptyPickerDraft);
        setMode(selectedCount ? 'pick' : 'view');
        onChanged();
      }} />;
  }

  if (human && operation) {
    const unlinkAsset = typeof operation === 'string' ? undefined : operation;
    const archiving = operation === 'archive';
    return <OperationConfirmation
      title={archiving ? '归档「' + human.name + '」'
        : unlinkAsset ? '从「' + human.name + '」移除「' + unlinkAsset.filename + '」' : '删除档案「' + human.name + '」'}
      description={archiving ? '档案移到已归档列表，素材引用保留，可随时恢复。'
        : unlinkAsset ? '仅解除当前档案的引用。素材及其他数字人的引用会保留。'
          : '删除该档案及其引用关系。全局素材库中的文件会保留。'}
      pending={action.pending} error={action.error} confirmDisabled={read.loading}
      confirmLabel={archiving ? '确认归档' : unlinkAsset ? '确认移除' : '确认删除'} danger={!archiving}
      onCancel={() => { if (!action.pending) { action.clearError(); setOperation(undefined); } }}
      onConfirm={() => archiving ? changeStatus('archived') : unlinkAsset ? detach(unlinkAsset) : remove()} />;
  }

  return <>
    <RecordPage eyebrow={draft ? '数字人 / 档案资料' : '数字人 / 档案详情'}
      title={draft ? '编辑档案' : human?.name ?? '数字人档案'}
      description={!draft && human ? human.description || '暂无人设说明' : undefined}
      {...(!draft && { onClose: () => leave('close'), backLabel: '返回数字人列表', pending: action.pending })}>
      {notice && !draft && <p role="status" className="media-status">{notice}</p>}
      <ReadStatus title="数字人档案" loading={read.loading} error={read.error} onRetry={read.retry} />
      {human && (draft ? <HumanForm draft={draft} setDraft={setDraft} pending={action.pending} unchanged={!dirty}
        error={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} submitLabel="保存资料"
        onSubmit={save} onCancel={() => leave('view')} /> : <>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="档案操作">
          <Chip size="sm" className="media-chip"><Chip.Label>{humanStatus(human.status)}</Chip.Label></Chip>
          {canWrite && <>
            <Button variant="secondary" isDisabled={action.pending || read.loading} onPress={edit}>编辑档案</Button>
            {human.status === 'archived'
              ? <Button variant="secondary" isPending={action.pending} isDisabled={read.loading} onPress={() => void changeStatus('active')}>恢复档案</Button>
              : <Button variant="secondary" isDisabled={action.pending || read.loading} onPress={() => ask('archive')}>归档档案</Button>}
            <Button variant="danger" className="media-danger" isDisabled={action.pending || read.loading} onPress={() => ask('delete')}>删除档案</Button>
          </>}
        </div>
        <ActionError message={action.error} />
        <section aria-label="引用素材" className="media-box">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2>引用素材 · {human.assetIds.length} 项</h2>
            {canWrite && <Button isDisabled={action.pending || read.loading} onPress={beginPicker}>添加素材</Button>}
          </div>
          {human.assets.length === 0 && !read.loading && <p className="text-muted">还没有引用素材。点击「添加素材」从素材库选择。</p>}
          <ul aria-label="已引用素材">
            {human.assets.map((asset) => {
              const assetPage = recordLink(bootstrap, 'media-assets', 'asset', asset.assetId);
              const href = assetPage && assetPage + '&fromHuman=' + encodeURIComponent(id);
              return <li key={asset.assetId} className="media-row">
                <strong className="min-w-0 [overflow-wrap:anywhere]">{asset.filename}</strong>
                <Chip size="sm" className="media-chip"><Chip.Label>{assetType(asset)}</Chip.Label></Chip>
                <AssetTags tags={asset.tags} />
                <div className="flex flex-wrap gap-2">
                  {href && <a className={buttonVariants({ variant: 'secondary' })} href={href} aria-label={'查看素材 ' + asset.filename}>查看素材</a>}
                  {canWrite && <Button variant="secondary" isDisabled={action.pending || read.loading}
                    aria-label={'移除引用 ' + asset.filename} onPress={() => ask(asset)}>移除引用</Button>}
                </div>
              </li>;
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
  const [status, setStatus] = useState<HumanStatus>('active');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selected, select] = useRecordSelection('human');
  const [creating, setCreating] = useState(false);
  const [openMode, setOpenMode] = useState<'view' | 'pick'>('view');
  const listAction = useMediaAction();
  const [notice, setNotice] = useState('');
  const params = new URLSearchParams({ limit: String(humanPageSize + 1), status });
  if (offset) params.set('offset', String(offset));
  const read = useMediaRead(root, '/digital-humans?' + params, decodeDigitalHumanList, revision);
  const humans = read.data?.slice(0, humanPageSize) ?? [];
  const refresh = () => setRevision((value) => value + 1);

  function created(human: DigitalHuman) {
    const reset = Boolean(offset || status !== 'active');
    setCreating(false);
    setStatus('active');
    setOffset(0);
    refresh();
    setNotice('档案已创建。现在可以添加素材。' + (reset ? '列表已返回「启用中」首页。' : ''));
    setOpenMode('view');
    select(human.digitalHumanId);
  }

  function statusChanged(next: HumanStatus) {
    if (next === 'archived') select(null);
    setStatus(next);
    setOffset(0);
    refresh();
    setNotice(next === 'archived' ? '档案已移到「已归档」。' : '档案已恢复，原有引用已保留。');
  }

  if (creating) return <div className="erp-page media-workspace"><CreateHuman root={root} onClose={() => setCreating(false)} onCreated={created} /></div>;
  if (selected) return <div className="erp-page media-workspace"><HumanDetail key={selected} bootstrap={bootstrap} id={selected} initialNotice={notice} initialMode={openMode}
    onClose={() => { select(null); setNotice(''); }} onChanged={refresh} onStatusChanged={statusChanged} onDeleted={() => {
      select(null);
      if (humans.length === 1 && humans[0].digitalHumanId === selected) setOffset(Math.max(0, offset - humanPageSize));
      refresh(); setNotice('档案已删除。素材仍在全局库。');
    }} /></div>;

  return <div className="erp-page media-workspace">
    <RecordPage eyebrow="数字人" title="数字人列表" description="从列表直接打开档案或给指定数字人添加素材。"
      actions={canWrite && <Button isDisabled={listAction.pending} onPress={() => { setNotice(''); setCreating(true); }}>新建数字人</Button>}>
      {notice && <p role="status" className="media-status">{notice}</p>}
      <div className="flex flex-wrap gap-2" role="group" aria-label="档案状态">
        {(['active', 'archived'] as const).map((value) => <Button key={value}
          variant={status === value ? 'primary' : 'secondary'} aria-pressed={status === value} isDisabled={listAction.pending}
          onPress={() => { setStatus(value); setOffset(0); setNotice(''); }}>{humanStatus(value)}</Button>)}
      </div>
      <ReadStatus title="数字人列表" loading={read.loading} error={read.error} onRetry={read.retry} />
      <ActionError message={listAction.error} />
      <div className="media-box">
        {!read.loading && !read.error && humans.length === 0 && <p>当前没有档案。</p>}
        <ul aria-label="数字人档案">
          {humans.map((human) => <li key={human.digitalHumanId} className="media-row">
            <strong className="min-w-0 [overflow-wrap:anywhere]">{human.name}</strong>
            <span className="flex flex-wrap items-center gap-2"><Chip size="sm" className="media-chip"><Chip.Label>{humanStatus(human.status)}</Chip.Label></Chip>{human.assetIds.length} 项素材</span>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" isDisabled={listAction.pending} aria-label={'查看档案 ' + human.name}
                onPress={() => { setNotice(''); setOpenMode('view'); select(human.digitalHumanId); }}>查看档案</Button>
              {canWrite && (human.status === 'archived'
                ? <Button variant="secondary" aria-label={'恢复档案 ' + human.name} isPending={listAction.pending}
                    onPress={() => void listAction.run(async () => {
                      const updated = await mediaWrite(root, '/digital-humans/' + human.digitalHumanId, 'PATCH', decodeDigitalHuman, { status: 'active' });
                      if (updated.digitalHumanId !== human.digitalHumanId) throw new Error('恢复结果不符');
                      statusChanged('active');
                      setOpenMode('view');
                      select(human.digitalHumanId);
                    })}>恢复档案</Button>
                : <Button isDisabled={listAction.pending} aria-label={'添加素材 ' + human.name}
                    onPress={() => { setNotice(''); setOpenMode('pick'); select(human.digitalHumanId); }}>添加素材</Button>)}
            </div>
          </li>)}
        </ul>
      </div>
      <MediaPagination name="数字人分页" offset={offset} limit={humanPageSize}
        hasNext={Boolean(read.data && read.data.length > humanPageSize)} loading={read.loading || listAction.pending} onChange={setOffset} />
    </RecordPage>
  </div>;
}

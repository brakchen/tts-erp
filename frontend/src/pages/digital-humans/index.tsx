import { useEffect, useRef, useState } from 'react';
import { Button, Card, Chip } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { CursorTail, useCursorFeed } from '../media-assets/CursorFeed';
import OperationConfirmation from '../media-assets/OperationConfirmation';
import { decodeDigitalHuman, decodeDigitalHumanFeed, type DigitalHuman } from '../media-assets/contracts';
import {
  ActionError, MediaSelect, ReadStatus, humanStatus, mediaWrite, useMediaAction, useRecordSelection,
} from '../media-assets/media-ui';
import HumanEditor, { PortraitTile, type EditorMode } from './HumanEditor';
import HumanMaterials from './HumanMaterials';
import HumanPackWorkspace from './HumanPackWorkspace';
import PickAssets from './PickAssets';

const statusOptions = [
  { value: 'active', label: '启用中' }, { value: 'archived', label: '已归档' }, { value: '', label: '全部状态' },
];

export default function DigitalHumans({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [status, setStatus] = useState('active');
  const [revision, setRevision] = useState(0);
  const [selected, select] = useRecordSelection('human');
  const [legacy, setLegacy] = useState(false);
  const [editor, setEditor] = useState<{ mode: EditorMode; human?: DigitalHuman }>();
  const [picker, setPicker] = useState<DigitalHuman>();
  const [operation, setOperation] = useState<{ mode: 'archive' | 'delete'; human: DigitalHuman }>();
  const [notice, setNotice] = useState('');
  const returning = useRef<string | null>(null);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const action = useMediaAction();
  const filter = status ? '?' + new URLSearchParams({ status }) : '';
  const feed = useCursorFeed(root, '/digital-humans' + filter, decodeDigitalHumanFeed, revision);
  const humans = feed.data?.items ?? [];
  const refresh = () => setRevision((value) => value + 1);

  useEffect(() => {
    if (selected || !returning.current) return;
    const card = document.getElementById('human-' + returning.current);
    (card ?? listHeading.current)?.focus();
    returning.current = null;
  }, [selected]);

  function changeStatus(human: DigitalHuman, next: 'active' | 'archived') {
    void action.run(async () => {
      const updated = await mediaWrite(root, '/digital-humans/' + human.digitalHumanId, 'PATCH', decodeDigitalHuman, { status: next });
      if (updated.digitalHumanId !== human.digitalHumanId || updated.status !== next) throw new Error('档案状态结果不符');
      setNotice(next === 'archived' ? '档案已移到「已归档」。' : '档案已恢复，可在「启用中」查看。');
      setOperation(undefined);
      refresh();
    });
  }
  function remove(human: DigitalHuman) {
    void action.run(async () => {
      await mediaWrite(root, '/digital-humans/' + human.digitalHumanId, 'DELETE', () => undefined);
      setNotice('档案已删除。素材仍在全局库。');
      setOperation(undefined);
      refresh();
    });
  }
  function ask(mode: 'archive' | 'delete', human: DigitalHuman) {
    action.clearError();
    setOperation({ mode, human });
  }

  return <div className="erp-page media-workspace">
    {selected ? (legacy ? <HumanMaterials key={selected} bootstrap={bootstrap} id={selected} revision={revision} canWrite={canWrite}
      onBack={() => setLegacy(false)} onPick={setPicker} onChanged={refresh} /> :
      <HumanPackWorkspace key={selected} bootstrap={bootstrap} id={selected} revision={revision} canWrite={canWrite}
        onBack={() => { returning.current = selected; select(null); }} onLegacy={() => setLegacy(true)} />) : <>
      <div className="media-list-toolbar">
        <h1 ref={listHeading} tabIndex={-1}>档案 {feed.data && <span className="media-count">{feed.data.total} 个</span>}</h1>
        <div className="media-actions">
          <div className="media-status-filter"><MediaSelect label="状态" value={status} options={statusOptions}
            onChange={(next) => { setStatus(next); setNotice(''); action.clearError(); }} /></div>
          {canWrite && <Button aria-haspopup="dialog" onPress={() => setEditor({ mode: 'create' })}>新建数字人</Button>}
        </div>
      </div>
      {notice && <p className="media-status" role="status">{notice}</p>}
      {!operation && <ActionError message={action.error} />}
      <ReadStatus title="档案列表" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
      {feed.data && !humans.length && <p className="media-box">{status === 'archived' ? '暂无已归档的档案。' : '当前没有档案。'}</p>}
      <ul className="media-grid media-people-grid" data-media-grid aria-label="数字人档案">
        {humans.map((human) => <li key={human.digitalHumanId} className="min-w-0">
          <Card className="media-box media-card shadow-none">
            <Card.Content className="media-card-content">
              <div className="media-person-identity">
                <PortraitTile url={human.portraitUrl} label={(human.portraitUrl ? '更换' : '上传') + human.name + '的形象照片'}
                  onPress={canWrite ? () => setEditor({ mode: 'portrait', human }) : undefined} />
                <h2 id={'human-' + human.digitalHumanId} tabIndex={-1} className="min-w-0 self-center [overflow-wrap:anywhere]">
                  {human.name}
                </h2>
                <Chip size="sm" className="media-chip"><Chip.Label>{humanStatus(human.status)}</Chip.Label></Chip>
              </div>
              <div className="media-actions">
                <Button variant="secondary" aria-label={'查看档案 ' + human.name}
                  onPress={() => { select(human.digitalHumanId); document.scrollingElement?.scrollTo(0, 0); }}>查看档案</Button>
              </div>
              {canWrite && <div className="media-card-actions">
                <Button variant="ghost" aria-label={'编辑 ' + human.name} onPress={() => setEditor({ mode: 'edit', human })}>编辑</Button>
                {human.status === 'archived'
                  ? <Button variant="ghost" isDisabled={action.pending} aria-label={'恢复 ' + human.name} onPress={() => changeStatus(human, 'active')}>恢复</Button>
                  : <Button variant="ghost" aria-label={'归档 ' + human.name} onPress={() => ask('archive', human)}>归档</Button>}
                <Button variant="ghost" className="text-danger" aria-label={'删除 ' + human.name} onPress={() => ask('delete', human)}>删除</Button>
              </div>}
            </Card.Content>
          </Card>
        </li>)}
      </ul>
      <CursorTail {...feed} paused={Boolean(editor || picker || operation || action.pending)} />
    </>}
    {canWrite && editor && <HumanEditor root={root} {...editor} onClose={() => setEditor(undefined)} onSaved={() => {
      if (editor.mode === 'create' && status === 'archived') setStatus('active');
      setNotice(editor.mode === 'create' ? '档案已创建。打开档案即可添加素材包。'
        : editor.mode === 'portrait' ? '形象照片已更新。' : '档案资料已保存。');
      setEditor(undefined);
      refresh();
      document.scrollingElement?.scrollTo(0, 0);
    }} />}
    {canWrite && operation && <OperationConfirmation
      title={(operation.mode === 'archive' ? '归档「' : '删除档案「') + operation.human.name + '」'}
      description={operation.mode === 'archive' ? '档案会移到已归档，保留关联素材，可随时恢复。' : '该档案及其关联会被移除，全局素材库中的文件会保留。'}
      confirmLabel={operation.mode === 'archive' ? '确认归档' : '确认删除'} danger={operation.mode === 'delete'}
      pending={action.pending} error={action.error} onCancel={() => { setOperation(undefined); action.clearError(); }}
      onConfirm={() => operation.mode === 'archive' ? changeStatus(operation.human, 'archived') : remove(operation.human)} />}
    {canWrite && picker && <PickAssets bootstrap={bootstrap} target={picker} onChanged={refresh} onClose={(message) => {
      setPicker(undefined);
      if (message) setNotice(message);
    }} />}
  </div>;
}

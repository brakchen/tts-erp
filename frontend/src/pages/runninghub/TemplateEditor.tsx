import { useState } from 'react';
import { Button, buttonVariants } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import { decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset, type DigitalHuman } from '../media-assets/contracts';
import { Choice, WorkModal } from './controls';
import { useRead } from './requests';

type TemplateDraft = { humanId: string; name: string; assetIds: string[] };

export default function TemplateEditor({ root, busy, feedback, close, save }: {
  root: string;
  busy: boolean;
  feedback: string;
  close: () => void;
  save: (draft: TemplateDraft) => Promise<void>;
}) {
  const [humanId, setHumanId] = useState('');
  const [name, setName] = useState('A');
  const [ordered, setOrdered] = useState<Asset[]>([]);
  const [revision, setRevision] = useState(0);
  const people = useRead<unknown>(root, '/v2/media/digital-humans?status=active&limit=200', revision);
  const person = useRead<unknown>(root, humanId ? '/v2/media/digital-humans/' + humanId + '?includePackVideos=true' : null, revision);
  let peopleRows: DigitalHuman[] = [];
  let videos: Asset[] = [];
  let currentPerson = false;
  let decodeError = '';
  try {
    if (people.data) peopleRows = decodeDigitalHumanList(people.data).filter((item) => item.status === 'active');
    if (person.data) {
      const detail = decodeDigitalHumanDetail(person.data);
      currentPerson = detail.digitalHumanId === humanId && detail.status === 'active';
      if (currentPerson) videos = detail.assets.filter((item) => item.kind === 'video' && item.status === 'ready');
    }
  } catch { decodeError = '素材列表格式错误，请重新读取。'; }
  const error = decodeError || people.error || person.error;
  const selectedIds = new Set(ordered.map((clip) => clip.assetId));
  const availableIds = new Set(videos.map((clip) => clip.assetId));
  const canSave = !busy && !error && currentPerson && ordered.length > 0
    && ordered.every((clip) => availableIds.has(clip.assetId));

  function move(index: number, delta: number) {
    setOrdered((items) => {
      const next = [...items];
      [next[index], next[index + delta]] = [next[index + delta], next[index]];
      return next;
    });
  }

  return <WorkModal title="新建人物模板" busy={busy} close={close} dialogClassName="sm:max-w-[48rem]" footer={<>
    <Button variant="secondary" isDisabled={busy} onPress={close}>取消</Button>
    <Button isDisabled={!canSave} onPress={() => { void save({ humanId, name, assetIds: [...selectedIds] }); }}>
      {ordered.length ? '保存 ' + ordered.length + ' 段模板' : '保存模板'}
    </Button>
  </>}>
    <p>选择人物和参考视频，按下方顺序生成。至少选一段。</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <Choice label="人物" value={humanId} placeholder="选择人物" disabled={busy || people.loading}
        options={peopleRows.map((item) => ({ id: item.digitalHumanId, name: item.name }))}
        onChange={(id) => { setHumanId(id); setOrdered([]); }} />
      <Choice label="模板名称" value={name} onChange={setName} disabled={busy}
        options={['A', 'B', 'C'].map((id) => ({ id, name: id }))} />
    </div>
    {error && <div className="rh-empty space-y-2"><p role="alert">{error}</p>
      <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => setRevision((n) => n + 1)}>重新读取</Button>
    </div>}
    {!error && (people.loading || person.loading) && <p role="status">{people.loading ? '正在读取人物…' : '正在读取参考视频…'}</p>}
    {!error && people.data !== undefined && !peopleRows.length && <p className="rh-empty">暂无在用人物。请先在数字人素材库创建人物。</p>}
    {!error && peopleRows.length > 0 && !humanId && <p className="rh-empty">选中人物后查看其就绪参考视频。</p>}
    {!error && currentPerson && !videos.length && <div className="rh-empty space-y-2">
      <p>该人物还没有就绪参考视频。</p>
      <a className={buttonVariants({ size: 'sm', variant: 'secondary' })}
        href={pathFor(root, '/v2/pages/digital-humans')}>去数字人素材库添加视频</a>
    </div>}
    {!error && videos.length > 0 && <div className="grid gap-3 sm:grid-cols-2">
      <section className="rh-editor-panel" aria-labelledby="rh-available-clips">
        <h3 id="rh-available-clips" className="font-semibold">可选参考视频 · {videos.length} 段</h3>
        <ul className="rh-editor-list">{videos.map((video) => {
          const added = selectedIds.has(video.assetId);
          return <li key={video.assetId} className="rh-editor-row">
            <span className="min-w-0 flex-1 break-all">{video.filename}</span>
            <Button size="sm" variant="secondary" isDisabled={busy || added}
              aria-label={(added ? '已加入 ' : '加入 ') + video.filename}
              onPress={() => setOrdered((old) => old.some((clip) => clip.assetId === video.assetId) ? old : [...old, video])}>
              {added ? '已加入' : '加入'}
            </Button>
          </li>;
        })}</ul>
      </section>
      <section className="rh-editor-panel" aria-labelledby="rh-ordered-clips">
        <h3 id="rh-ordered-clips" className="font-semibold">生成顺序 · 已选 {ordered.length} 段</h3>
        {ordered.length ? <ol className="rh-editor-list">{ordered.map((video, index) => <li key={video.assetId} className="rh-editor-row">
          <span className="min-w-0 basis-32 grow break-words"><span className="rh-step mr-2">{String(index + 1).padStart(2, '0')}</span>
            {video.filename}{!availableIds.has(video.assetId) && '（已不可用）'}
          </span>
          <div className="flex flex-wrap gap-1">
            <Button size="sm" variant="ghost" isDisabled={busy || index === 0}
              aria-label={'上移 ' + video.filename} onPress={() => move(index, -1)}>上移</Button>
            <Button size="sm" variant="ghost" isDisabled={busy || index === ordered.length - 1}
              aria-label={'下移 ' + video.filename} onPress={() => move(index, 1)}>下移</Button>
            <Button size="sm" variant="ghost" isDisabled={busy}
              aria-label={'移除 ' + video.filename} onPress={() => setOrdered((old) => old.filter((clip) => clip.assetId !== video.assetId))}>移除</Button>
          </div>
        </li>)}</ol> : <p>从可选列表加入参考视频。</p>}
      </section>
    </div>}
    {feedback && <p role="status">{feedback}</p>}
  </WorkModal>;
}

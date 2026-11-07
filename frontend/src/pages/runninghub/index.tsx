import { useEffect, useRef, useState } from 'react';
import { Button, Chip, Drawer, Label, TextArea, TextField } from '@heroui/react';
import PageIntro from '../../shared/PageIntro';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import { decodeAssetList, decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset } from '../media-assets/contracts';
import BatchRecords from './BatchRecords';
import type { Artifact, Batch, Config, Job, Template } from './contracts';
import { statuses } from './contracts';
import { Check, Choice, Field, SelectTile, WorkModal } from './controls';
import './runninghub.css';

function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403) return '无权执行此操作。';
    if (error.status === 401) return '会话已过期，请重新登录。';
    if (error.status === 429) return '请求过于频繁，请稍后刷新。';
    if (typeof error.detail === 'string' && /^[A-Z0-9_: .-]+$/.test(error.detail)) return `操作未完成：${error.detail}`;
  }
  return '请求未完成。请刷新查看实际状态，写操作不会自动重放。';
}

function useRead<T>(root: string, path: string | null, revision: number, polling = false) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) { setData(undefined); return; }
    const controller = new AbortController();
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    setData(undefined);
    async function read() {
      setLoading(true);
      try {
        const response = await requestJson<T>(root, path!, { signal: controller.signal }, parseIdentityJson);
        if (current) { setData(response.data); setError(''); }
      } catch (failure) { if (current) setError(errorText(failure)); }
      finally { if (current) setLoading(false); }
      if (current && polling) timer = setTimeout(() => { void read(); }, 10000);
    }
    void read();
    return () => { current = false; controller.abort(); clearTimeout(timer); };
  }, [root, path, revision, polling]);
  return { data, error, loading };
}

function JobDetail({ jobId, root, revision, canWrite, admin, run }: {
  jobId: string; root: string; revision: number; canWrite: boolean; admin: boolean;
  run: (path: string, body?: object) => Promise<void>;
}) {
  const detail = useRead<Job>(root, '/v2/runninghub/jobs/' + jobId, revision, true);
  const artifacts = useRead<{ items: Artifact[] }>(root, '/v2/runninghub/jobs/' + jobId + '/artifacts', revision);
  const [taskId, setTaskId] = useState('');
  const [note, setNote] = useState('');
  const [notCreated, setNotCreated] = useState(false);
  const job = detail.data;
  return <section className="space-y-3 rounded-xl border p-4" aria-label="单任务详情">
    <h3 className="erp-section-heading">单任务详情</h3>
    {detail.loading && !job && <p role="status">正在读取任务…</p>}{detail.error && <p role="alert">{detail.error}</p>}
    {job && <><p className="break-all">{job.identity.originalFilename} · {job.identity.humanName} · {job.identity.templateName}{job.identity.clip}</p>
      <p>{statuses[job.status] ?? job.status} · seed {job.seed}</p>{job.error && <p role="status">{job.error}</p>}
      {canWrite && ['DOWNLOAD_FAILED', 'FINALIZE_FAILED', 'TRACKING_INTERRUPTED'].includes(job.status) && <Button onPress={() => { void run('/jobs/' + job.id + '/recover'); }}>恢复原任务</Button>}
      {canWrite && ['FAILED', 'PREPARATION_FAILED'].includes(job.status) && <Button onPress={() => { if (window.confirm('此操作将为明确失败的任务安排新的 Attempt。真实模式可能再次计费，是否继续？')) void run('/jobs/' + job.id + '/retry'); }}>明确失败后重新生成</Button>}
      {job.status === 'SUBMISSION_UNKNOWN' && <div className="space-y-2"><p>请管理员到 RunningHub 任务列表核对。此任务继续占用配额，不会自动重提。</p>
        {admin && <><Field label="核实的云端 taskId" value={taskId} onChange={setTaskId} />
          <Field label="核对说明" value={note} onChange={setNote} />
          <Check label="我已核实云端未创建任务" selected={notCreated} onChange={setNotCreated} />
          <Button isDisabled={!note.trim() || (notCreated ? Boolean(taskId) : !taskId.trim())} onPress={() => { void run('/jobs/' + job.id + '/resolve', { taskId: taskId || null, confirmedNotCreated: notCreated, note }); }}>保存核对结果</Button></>}
      </div>}
      {artifacts.error && <p role="alert">{artifacts.error}</p>}
      {artifacts.data?.items.map((item) => <div key={item.id} className="space-y-2">
        <p>{item.kind === 'final' ? '校验成片' : '原始生成视频'}</p>
        {item.kind === 'final' && <video className="max-h-96 max-w-full" controls preload="metadata" src={item.url} />}
        <a className="underline" href={item.url} target="_blank" rel="noreferrer">下载{item.kind === 'final' ? '成片' : '原始视频'}</a>
      </div>)}
      {job.attempts?.map((attempt) => <details key={attempt.id}><summary>Attempt {attempt.number} · {attempt.cloudStatus ?? '尚未受理'} · {attempt.taskId ?? '无云端 ID'}</summary>
        <p>配额：{attempt.reserved ? '保留' : '已释放'}</p><p>{attempt.error}</p>
        <p>费用：{attempt.usage ? JSON.stringify(attempt.usage) : '未知'}</p>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(attempt.receipt, null, 2)}</pre></details>)}
    </>}
  </section>;
}

const imagePageSize = 6;
const specs = ['5 秒', '24 fps', '576 × 1024', '120 帧'];
const instanceOptions = [
  { id: 'default', name: 'default / 24 GB' },
  { id: 'plus', name: 'plus / 48 GB' },
  { id: 'ultra', name: 'ultra / 84 GB' },
];
type Selection = { asset: Asset; skuId: string; style: string };
type Plan = { total: number; groups: number; paths: string[] };

export default function RunningHub({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const admin = bootstrap.roleCapabilities?.admin === true;
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [feedback, setFeedback] = useState('');
  const [dialog, setDialog] = useState<'config' | 'template' | 'plan' | 'pay'>();
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const assets = useRead<unknown>(root, '/v2/media/assets?kind=image&status=ready&limit=' + imagePageSize + '&offset=' + offset + '&keyword=' + encodeURIComponent(search), revision);
  const humans = useRead<unknown>(root, dialog === 'template' ? '/v2/media/digital-humans?limit=200' : null, revision);
  const templates = useRead<{ items: Template[] }>(root, '/v2/runninghub/templates', revision);
  const config = useRead<Config>(root, '/v2/runninghub/config', revision);
  const [batchOffset, setBatchOffset] = useState(0);
  const batches = useRead<{ items: Batch[] }>(root, '/v2/runninghub/batches?limit=30&offset=' + batchOffset, revision, true);
  const [humanId, setHumanId] = useState('');
  const human = useRead<unknown>(root, dialog === 'template' && humanId ? '/v2/media/digital-humans/' + humanId : null, revision);
  const [templateName, setTemplateName] = useState('A');
  const [clipIds, setClipIds] = useState<string[]>(Array(6).fill(''));
  const [selected, setSelected] = useState<Selection[]>([]);
  const [style, setStyle] = useState('');
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [single, setSingle] = useState(false);
  const [preview, setPreview] = useState<Plan>();
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [startTarget, setStartTarget] = useState<Batch>();
  const [paid, setPaid] = useState(false);
  const [batchId, setBatchId] = useState('');
  const batch = useRead<Batch>(root, batchId ? '/v2/runninghub/batches/' + batchId : null, revision, true);
  const [jobOffset, setJobOffset] = useState(0);
  const jobs = useRead<{ items: Job[] }>(root, batchId ? '/v2/runninghub/batches/' + batchId + '/jobs?limit=100&offset=' + jobOffset : null, revision, true);
  const [jobId, setJobId] = useState('');
  const [credentialRef, setCredentialRef] = useState('');
  const [instanceType, setInstanceType] = useState('plus');
  const [concurrency, setConcurrency] = useState('5');
  const [graph, setGraph] = useState('');

  const body = {
    images: selected.map((entry) => ({
      assetId: entry.asset.assetId, skuId: entry.skuId.trim(), style: entry.style.trim() || style.trim(),
    })),
    templateIds: selectedTemplates,
    single,
  };
  useEffect(() => { setPreview(undefined); }, [selected, style, selectedTemplates, single]);

  async function command<T>(path: string, value: object = {}, method = 'POST'): Promise<T | undefined> {
    if (pending.current) return undefined;
    pending.current = true;
    setBusy(true);
    setFeedback('');
    try {
      const response = await requestJson<T>(root, '/v2/runninghub' + path, { method, body: JSON.stringify(value) });
      if (!mounted.current) return undefined;
      if (path !== '/plan' && path !== '/account') {
        setFeedback('操作已保存。');
        setRevision((n) => n + 1);
      }
      return response.data;
    } catch (failure) {
      if (mounted.current) setFeedback(errorText(failure));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
    return undefined;
  }

  function openConfig() {
    if (config.data) {
      setCredentialRef(config.data.credentialRef);
      setInstanceType(config.data.instanceType);
      setConcurrency(String(config.data.concurrency));
    }
    setFeedback('');
    setDialog('config');
  }

  function openBatch(id: string) {
    setFeedback('');
    setBatchId(id);
    setJobId('');
    setJobOffset(0);
  }

  function prepareStart(current: Batch) {
    setFeedback('');
    setPaid(false);
    setStartTarget(current);
    setDialog('pay');
  }

  async function confirmStart() {
    if (!startTarget || (startTarget.mode === 'live' && !paid)) return;
    const action = startTarget.status === 'PAUSED' ? 'resume' : 'start';
    const result = await command<Batch>('/batches/' + startTarget.id + '/' + action, { confirmPaid: paid });
    if (result) setDialog(undefined);
  }

  async function previewPlan() {
    const result = await command<Plan>('/plan', body);
    if (result) {
      setPreview(result);
      setDialog('plan');
    }
  }

  async function saveBatch() {
    if (!preview || !config.data?.workflowVersionId) return;
    const saved = await command<Batch>('/batches', { ...body, requestId, mode: 'live' });
    if (saved?.id) {
      setBatchOffset(0);
      setRequestId(crypto.randomUUID());
      setFeedback('批次已保存，可在批次记录中启动。');
      setDialog(undefined);
    }
  }

  async function saveWorkflowVersion() {
    let value: unknown;
    try { value = graph.trim() ? JSON.parse(graph) : null; }
    catch { setFeedback('工作流 JSON 无法解析。'); return; }
    await command('/workflow/check', { graph: value });
  }

  let imageRows: Asset[] = [];
  let humanRows: ReturnType<typeof decodeDigitalHumanList> = [];
  let videoRows: Asset[] = [];
  let decodeError = '';
  try {
    if (assets.data) imageRows = decodeAssetList(assets.data).items;
    if (humans.data) humanRows = decodeDigitalHumanList(humans.data).filter((item) => item.status === 'active');
    if (human.data) videoRows = decodeDigitalHumanDetail(human.data).assets.filter((item) => item.kind === 'video' && item.status === 'ready');
  } catch { decodeError = '素材列表格式错误，请刷新重试。'; }

  const disabled = busy || !canWrite;
  const profile = config.data;
  const templateRows = templates.data?.items ?? [];
  const batchRows = batches.data?.items ?? [];
  const selectionHint = single && (selected.length !== 1 || selectedTemplates.length !== 1)
    ? '仅跑首段时，请选择一张图和一个人物模板。'
    : !selected.length || !selectedTemplates.length
      ? '请选择服装图和人物模板。'
      : body.images.some((item) => !item.style || !item.skuId)
        ? '请填写款式编号和 SKU。'
        : '';
  const run = async (path: string, value?: object) => { await command(path, value); };
  const close = () => setDialog(undefined);
  const notice = feedback && <p role="status" className="break-words">{feedback}</p>;
  const selectedBatch = batch.data;
  const startingLive = startTarget?.mode === 'live';
  const resuming = startTarget?.status === 'PAUSED';

  return <div className="erp-page rh-page">
    <PageIntro eyebrow="RUNNINGHUB / VIDEO STUDIO" title="换装视频" description="选好服装与人物模板，按批次生成并查看每段成片。">
      <Button variant="ghost" isDisabled={busy} onPress={() => setRevision((n) => n + 1)}>刷新状态</Button>
      <Button variant="secondary" isDisabled={busy || !profile} onPress={openConfig}>工作流配置</Button>
    </PageIntro>
    {!canWrite && <p role="status">当前为只读权限。</p>}
    {[config.error, templates.error, assets.error, decodeError].filter(Boolean).map((error, index) => <p role="alert" key={index}>{error}</p>)}
    {!dialog && !batchId && notice}
    {profile?.dispatchPausedReason && <p className="rh-empty" role="status">
      账户新任务派发已暂停：{profile.dispatchPausedReason}。已受理任务继续跟踪。
    </p>}

    <div className="rh-workbench">
      <section className="rh-panel space-y-4" aria-labelledby="rh-create-title">
        <div className="rh-panel-heading">
          <h3 id="rh-create-title">创建批次</h3>
          <Chip size="sm" color="success">已选 {selected.length} 张图</Chip>
        </div>
        <fieldset className="min-w-0 space-y-2">
          <legend className="mb-2 font-semibold"><span className="rh-step mr-2">01</span>服装图片</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="默认款式编号" value={style} onChange={setStyle} disabled={disabled} />
            <Field label="搜索服装图片" value={search} onChange={(value) => { setSearch(value); setOffset(0); }} disabled={disabled} />
          </div>
          {imageRows.length ? <div className="rh-choice-grid">{imageRows.map((asset) => (
            <SelectTile key={asset.assetId} label={asset.filename} disabled={disabled}
              selected={selected.some((entry) => entry.asset.assetId === asset.assetId)}
              onChange={(checked) => setSelected((rows) => checked
                ? [...rows, { asset, skuId: asset.assetId, style: '' }]
                : rows.filter((entry) => entry.asset.assetId !== asset.assetId))}>
              <span className="rh-thumb" aria-hidden="true">{asset.url
                ? <img src={asset.url} alt="" loading="lazy" />
                : <span>图片素材</span>}</span>
              <span className="rh-tile-name" title={asset.filename}>{asset.filename}</span>
            </SelectTile>
          ))}</div> : <p className="rh-empty">{assets.loading ? '正在读取图片…' : assets.error ? '图片读取失败。' : <>
            {search ? '没有匹配的就绪图片。' : <>没有就绪图片。请先到 <a className="underline" href={pathFor(root, '/v2/pages/media-assets')}>资产管理</a> 上传服装图。</>}
          </>}</p>}
          {(offset > 0 || imageRows.length === imagePageSize) && <div className="rh-pager">
            <Button size="sm" variant="secondary" isDisabled={disabled || !offset || assets.loading} onPress={() => setOffset((n) => Math.max(0, n - imagePageSize))}>上一页图片</Button>
            <Button size="sm" variant="secondary" isDisabled={disabled || imageRows.length < imagePageSize || assets.loading} onPress={() => setOffset((n) => n + imagePageSize)}>下一页图片</Button>
          </div>}
          {selected.length > 0 && <details className="rh-selection-details">
            <summary>已选图片的款式和 SKU</summary>
            <div className="mt-2 space-y-2">{selected.map((entry) => <div className="grid min-w-0 gap-2 rounded-lg border p-2 sm:grid-cols-3" key={entry.asset.assetId}>
              <p className="break-all">{entry.asset.filename}</p>
              <Field label={'款式 ' + entry.asset.filename} value={entry.style} disabled={disabled}
                onChange={(value) => setSelected((rows) => rows.map((row) => row.asset.assetId === entry.asset.assetId ? { ...row, style: value } : row))} />
              <Field label={'SKU ' + entry.asset.filename} value={entry.skuId} disabled={disabled}
                onChange={(value) => setSelected((rows) => rows.map((row) => row.asset.assetId === entry.asset.assetId ? { ...row, skuId: value } : row))} />
            </div>)}</div>
          </details>}
        </fieldset>
        <fieldset className="min-w-0">
          <legend className="mb-2 font-semibold"><span className="rh-step mr-2">02</span>人物模板</legend>
          {canWrite && <Button size="sm" variant="ghost" className="mb-2" isDisabled={disabled} onPress={() => { setFeedback(''); setDialog('template'); }}>配置六段模板</Button>}
          {templateRows.length ? <div className="rh-template-grid">{templateRows.map((template) => (
            <SelectTile key={template.id} label={template.humanName + ' · 模板 ' + template.name} disabled={disabled}
              selected={selectedTemplates.includes(template.id)}
              onChange={(checked) => setSelectedTemplates((ids) => checked ? [...ids, template.id] : ids.filter((id) => id !== template.id))}>
              <span className="rh-tile-name rh-template-name" title={template.humanName + ' · 模板 ' + template.name}>
                <span className="rh-template-mark mr-2" aria-hidden="true">{template.name}</span>
                {template.humanName} · 模板 {template.name}
              </span>
              <span className="block px-1 text-xs text-muted">{template.clips.length} 段 · {template.id.slice(0, 8)}</span>
            </SelectTile>
          ))}</div> : <p className="rh-empty">{templates.loading ? '正在读取模板…' : templates.error ? '模板读取失败。' : '暂无人物模板。先在数字人素材库挂载参考视频，再配置六段模板。'}</p>}
        </fieldset>
        <div className="border-t pt-3"><Check label="仅跑首段" selected={single} onChange={setSingle} disabled={disabled} /></div>
      </section>

      <section className="rh-panel rh-plan" aria-labelledby="rh-plan-title">
        <p className="rh-step mb-2 font-mono tracking-widest">BATCH PREVIEW</p>
        <h3 id="rh-plan-title">生成计划</h3>
        <div className="rh-plan-total" role="status" aria-label="计划成片数">
          <strong>{preview?.total ?? '—'}</strong><span>段成片</span>
        </div>
        <p className="text-sm">{selected.length} 张图 × {selectedTemplates.length} 个人物模板 × {single ? 1 : 6} 段</p>
        <div className="rh-strip font-mono" aria-hidden="true">
          {(single ? ['01'] : ['01', '02', '03', '04', '05', '06']).map((clip) => <span key={clip}>{clip}</span>)}
        </div>
        <div className="rh-specs">{specs.map((spec) => <span key={spec}>{spec}</span>)}</div>
        <p className="mt-3 break-all text-xs">{profile
          ? profile.instanceType + ' · 并发 ' + profile.concurrency + ' · ' + (profile.workflowVersionId ? '已校验版本' : '待检查工作流')
          : config.loading ? '正在读取工作流…' : '工作流配置暂不可用'}</p>
        <div className="rh-plan-action">
          {canWrite && <Button className="rh-plan-button" isDisabled={disabled || Boolean(selectionHint)} onPress={() => { void previewPlan(); }}>预览计划 <span aria-hidden="true">→</span></Button>}
          <p className="mt-2 text-xs">{canWrite ? selectionHint || '预览后保存批次，启动时确认付费。' : '由有操作权限的人员创建并启动批次。'}</p>
        </div>
      </section>
    </div>

    <BatchRecords batches={batchRows} loading={batches.loading} error={batches.error} root={root}
      canWrite={canWrite} busy={busy} offset={batchOffset} onPage={setBatchOffset} onDetail={openBatch}
      onStart={prepareStart} onPause={(current) => { void run('/batches/' + current.id + '/pause'); }} />

    {dialog === 'config' && <WorkModal title="工作流配置" busy={busy} close={close}>
      <div className="rh-empty">
        <p className="break-all">工作流 <strong>{profile?.workflowId ?? '—'}</strong></p>
        <p>{profile?.workflowVersionId ? '已校验版本' : '待管理员检查工作流'}</p>
      </div>
      {config.error && <p role="alert">{config.error}</p>}
      {admin && profile && <>
        <h4 className="font-semibold">账户与运行配置</h4>
        <Field label="凭据引用" value={credentialRef} onChange={setCredentialRef} disabled={busy} />
        <p className="text-xs text-muted">填写服务端凭据引用，例如 env:RUNNINGHUB_API_KEY。当前{profile.credentialConfigured ? '已配置' : '未配置'}。</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <Choice label="显存档位" value={instanceType} onChange={setInstanceType} options={instanceOptions} disabled={busy} />
          <Choice label="云端并发上限" value={concurrency} onChange={setConcurrency}
            options={['1', '2', '3', '4', '5'].map((id) => ({ id, name: id }))} disabled={busy} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button isDisabled={busy || !credentialRef.trim()} onPress={() => {
            void command('/config', { credentialRef: credentialRef.trim(), workflowId: profile.workflowId, instanceType, concurrency: Number(concurrency) }, 'PUT');
          }}>保存配置</Button>
          <Button isDisabled={busy} variant="secondary" onPress={() => {
            void command<Record<string, unknown>>('/account').then((data) => { if (data) setFeedback('账户信息：' + JSON.stringify(data)); });
          }}>检查账户</Button>
        </div>
        <h4 className="border-t pt-3 font-semibold">更新工作流版本</h4>
        <TextField value={graph} onChange={setGraph} isDisabled={busy}>
          <Label>完整工作流 API JSON</Label><TextArea placeholder="留空从 RunningHub 读取" />
        </TextField>
        <div className="flex flex-wrap gap-2">
          <Button isDisabled={busy} variant="secondary" onPress={() => { void saveWorkflowVersion(); }}>检查并保存工作流版本</Button>
          {profile.dispatchPausedReason && <Button isDisabled={busy} variant="secondary" onPress={() => {
            if (window.confirm('请先修复账户余额、凭据或工作流配置。恢复后，已启动的批次可能继续付费生成。是否恢复？')) {
              void run('/dispatch/resume', { confirmPaid: true });
            }
          }}>恢复账户派发</Button>}
        </div>
      </>}
      {notice}
    </WorkModal>}

    {dialog === 'template' && <WorkModal title="配置人物模板" busy={busy} close={close} footer={
      <Button isDisabled={busy || !humanId || clipIds.some((id) => !id) || new Set(clipIds).size !== 6}
        onPress={() => { void command('/templates', { humanId, name: templateName, assetIds: clipIds }).then((saved) => { if (saved) close(); }); }}>
        保存六段模板
      </Button>
    }>
      <p>先在 <a className="underline" href={pathFor(root, '/v2/pages/digital-humans')}>数字人素材库</a> 挂载六段不同视频，再按实际顺序选择。</p>
      {[humans.error, human.error].filter(Boolean).map((error, index) => <p role="alert" key={index}>{error}</p>)}
      {humans.loading && <p role="status">正在读取人物…</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        <Choice label="人物" value={humanId} onChange={(id) => { setHumanId(id); setClipIds(Array(6).fill('')); }}
          options={humanRows.map((item) => ({ id: item.digitalHumanId, name: item.name }))} disabled={busy} />
        <Choice label="模板" value={templateName} onChange={setTemplateName}
          options={['A', 'B', 'C'].map((id) => ({ id, name: id }))} disabled={busy} />
        {clipIds.map((id, index) => <Choice key={index} label={'第 ' + (index + 1) + ' 段'} value={id} disabled={busy || !humanId}
          options={videoRows.map((video) => ({ id: video.assetId, name: video.filename }))}
          onChange={(value) => setClipIds((old) => old.map((entry, i) => i === index ? value : entry))} />)}
      </div>
      {humanId && !human.loading && videoRows.length < 6 && <p className="rh-empty">该人物的就绪参考视频不足六段。</p>}
      {notice}
    </WorkModal>}

    {dialog === 'plan' && preview && <WorkModal title="确认生成计划" busy={busy} close={close} footer={<>
      <Button variant="secondary" isDisabled={busy} onPress={close}>返回修改</Button>
      <Button isDisabled={disabled || !profile?.workflowVersionId} onPress={() => { void saveBatch(); }}>保存批次</Button>
    </>}>
      <p className="font-semibold">服务端计划：{preview.total} 段成片 / {preview.groups} 组</p>
      <p>保存后可在批次记录中主动启动。{single ? '本次使用模板的第 1 段参考视频。' : '每个服装图和人物模板组合包含六段。'}</p>
      {!profile?.workflowVersionId && <p className="rh-empty">请先在工作流配置中检查并保存版本。</p>}
      <details><summary>查看成片归档路径</summary><ul className="max-h-52 overflow-auto">{preview.paths.map((path) => <li className="break-all" key={path}>{path}</li>)}</ul></details>
      {notice}
    </WorkModal>}

    {dialog === 'pay' && startTarget && <WorkModal title={resuming ? '恢复批次' : '启动批次'} busy={busy} close={close} footer={<>
      <Button variant="secondary" isDisabled={busy} onPress={close}>取消</Button>
      <Button isDisabled={disabled || (startingLive && !paid)} onPress={() => { void confirmStart(); }}>
        {resuming ? '确认恢复' : '确认启动'}
      </Button>
    </>}>
      <p className="break-all">批次 {startTarget.id} · {startTarget.total} 段成片</p>
      {startingLive && <div className="rounded-lg bg-warning-soft p-3">
        <Check label="我了解实际生成会产生 RunningHub 费用。" selected={paid} onChange={setPaid} disabled={busy} />
      </div>}
      {notice}
    </WorkModal>}

    <Drawer.Root isOpen={Boolean(batchId)} onOpenChange={(open) => { if (!open && !busy) { setBatchId(''); setJobId(''); } }}>
      <Drawer.Backdrop>
        <Drawer.Content placement="right" className="w-full max-w-[44rem]">
          <Drawer.Dialog>
            <Drawer.Header><Drawer.Heading>批次详情</Drawer.Heading>{!busy && <Drawer.CloseTrigger aria-label="关闭批次详情" />}</Drawer.Header>
            <Drawer.Body className="space-y-3">
              {(batch.error || jobs.error) && <p role="alert">{batch.error || jobs.error}</p>}
              {batch.loading && !selectedBatch && <p role="status">正在读取批次…</p>}
              {notice}
              {selectedBatch && <>
                <p className="break-all font-mono text-xs">{selectedBatch.id}</p>
                <p>{statuses[selectedBatch.status] ?? selectedBatch.status} · 共 {selectedBatch.total} 个任务
                  {selectedBatch.mode === 'dry-run' ? ' · 历史演练批次' : ''}</p>
                <p>{Object.entries(selectedBatch.counts).map(([key, count]) => (statuses[key] ?? key) + ' ' + count).join(' / ')}</p>
                {selectedBatch.error && <p role="status">{selectedBatch.error}。暂停只停止新提交，已受理任务继续处理。</p>}
                <p>已知费用：{Object.keys(selectedBatch.costs).length
                  ? Object.entries(selectedBatch.costs).map(([key, amount]) => key + ' ' + amount).join(' / ')
                  : '未知'}；未知费用任务 {selectedBatch.unknownCostTasks}</p>
                <details><summary>分组完成情况</summary>{selectedBatch.groups.map((group) => <p className="break-all" key={group.path}>{group.path} · {group.succeeded}/{group.total} 已完成</p>)}</details>
              </>}
              <ul className="space-y-2">{jobs.data?.items.map((job) => <li className="rounded-lg border p-3" key={job.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="min-w-0 flex-1 break-all">{job.identity.originalFilename} · {job.identity.humanName} · {job.identity.templateName}{job.identity.clip} · {statuses[job.status] ?? job.status}</p>
                  <Button size="sm" variant="secondary" onPress={() => setJobId(job.id)}>查看任务 {job.identity.templateName}{job.identity.clip}</Button>
                </div>
              </li>)}</ul>
              {(jobOffset > 0 || (jobs.data?.items.length ?? 0) >= 100) && <div className="rh-pager">
                <Button variant="secondary" isDisabled={!jobOffset || jobs.loading} onPress={() => setJobOffset((n) => Math.max(0, n - 100))}>上一页任务</Button>
                <Button variant="secondary" isDisabled={(jobs.data?.items.length ?? 0) < 100 || jobs.loading} onPress={() => setJobOffset((n) => n + 100)}>下一页任务</Button>
              </div>}
              {jobId && <JobDetail key={jobId} jobId={jobId} root={root} revision={revision} canWrite={canWrite && !busy} admin={admin && !busy} run={run} />}
            </Drawer.Body>
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>
    </Drawer.Root>
  </div>;
}

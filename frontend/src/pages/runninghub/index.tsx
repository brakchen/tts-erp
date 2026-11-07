import { useEffect, useRef, useState } from 'react';
import { Button, Chip, Drawer, Label, TextArea, TextField, buttonVariants } from '@heroui/react';
import PageIntro from '../../shared/PageIntro';
import { requestJson } from '../../shared/api/client';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import { decodeAssetList, type Asset } from '../media-assets/contracts';
import BatchRecords from './BatchRecords';
import type { Artifact, Batch, Config, Job, Template } from './contracts';
import { canTerminateBatch, statuses, templateLabel } from './contracts';
import { Check, Choice, Field, SelectTile, WorkModal } from './controls';
import { errorText, useRead } from './requests';
import TemplateEditor from './TemplateEditor';
import './runninghub.css';

function ClipPreview({ jobId, root, first }: { jobId: string; root: string; first: boolean }) {
  const [revision, setRevision] = useState(0);
  const [failed, setFailed] = useState(false);
  const artifacts = useRead<{ items: Artifact[] }>(root, '/v2/runninghub/jobs/' + jobId + '/artifacts', revision);
  const final = artifacts.data?.items.find((item) => item.kind === 'final');
  const title = first ? '首段成片' : '成片预览';
  return <section aria-label={title} className="space-y-2">
    <h3 className="erp-section-heading">{title}</h3>
    {artifacts.loading && <p role="status">正在读取成片…</p>}
    {(artifacts.error || failed || (artifacts.data && !final)) && <div className="rh-empty space-y-2">
      <p role="status">{artifacts.error || '成片暂时无法预览。'}</p>
      <Button size="sm" variant="secondary" onPress={() => { setFailed(false); setRevision((n) => n + 1); }}>重新读取成片</Button>
    </div>}
    {final && !failed && <video aria-label={title} className="mx-auto max-h-72 max-w-full" controls playsInline
      preload="metadata" src={final.url} onError={() => setFailed(true)} />}
    {final && <a className="underline" href={final.url} target="_blank" rel="noreferrer">下载{title === '首段成片' ? title : '成片'}</a>}
  </section>;
}

function BatchArchive({ batch, root }: { batch: Batch; root: string }) {
  const [requested, setRequested] = useState(false);
  const finished = batch.status === 'SUCCEEDED';
  const stopped = batch.status === 'TERMINATED' || batch.status === 'TERMINATING';
  return <section aria-labelledby="rh-archive-title" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
    <div className="min-w-0 flex-1 space-y-1">
      <h3 id="rh-archive-title" className="erp-section-heading">批次成片 ZIP</h3>
      <p role="status">{finished
        ? requested ? '已请求服务器打包，可在浏览器下载列表查看进度。' : batch.total + ' 段成片 · 服务器从 MinIO 取文件并打包。'
        : stopped ? '可在任务详情下载已完成的单段成片。整批完成后才能下载 ZIP。'
          : '全部 ' + batch.total + ' 段完成后可下载 ZIP。'}</p>
    </div>
    {finished ? <a className={buttonVariants({ variant: 'primary' })} target="_blank" rel="noopener"
      href={pathFor(root, '/v2/runninghub/batches/' + batch.id + '/export')} onClick={() => setRequested(true)}>打包下载 ZIP</a>
      : <Button variant="secondary" isDisabled>打包下载 ZIP</Button>}
  </section>;
}

function JobDetail({ jobId, root, revision, canWrite, admin, stopping, run }: {
  jobId: string; root: string; revision: number; canWrite: boolean; admin: boolean;
  stopping: boolean;
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
      {canWrite && ['DOWNLOAD_FAILED', 'FINALIZE_FAILED', 'TRACKING_INTERRUPTED'].includes(job.status) && <Button onPress={() => { void run('/jobs/' + job.id + '/recover'); }}>{stopping && job.status === 'TRACKING_INTERRUPTED' ? '继续核对终止' : '恢复原任务'}</Button>}
      {canWrite && !stopping && ['FAILED', 'PREPARATION_FAILED'].includes(job.status) && <Button onPress={() => { if (window.confirm('此操作将为明确失败的任务安排新的 Attempt。真实模式可能再次计费，是否继续？')) void run('/jobs/' + job.id + '/retry'); }}>明确失败后重新生成</Button>}
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
        <p>配额：{attempt.reserved ? '保留' : '已释放'}</p><p>{attempt.error === 'CANCELLATION_UNCONFIRMED' ? '云端终止结果待确认。' : attempt.error}</p>
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
type Plan = { total: number; fullTotal: number; groups: number; paths: string[] };

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
  const templates = useRead<{ items: Template[] }>(root, '/v2/runninghub/templates', revision);
  const config = useRead<Config>(root, '/v2/runninghub/config', revision);
  const [batchOffset, setBatchOffset] = useState(0);
  const batches = useRead<{ items: Batch[] }>(root, '/v2/runninghub/batches?limit=30&offset=' + batchOffset, revision, true);
  const [selected, setSelected] = useState<Selection[]>([]);
  const [style, setStyle] = useState('');
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [single, setSingle] = useState(false);
  const [preview, setPreview] = useState<Plan>();
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [startTarget, setStartTarget] = useState<Batch>();
  const [paid, setPaid] = useState(false);
  const [batchId, setBatchId] = useState('');
  const [archiveView, setArchiveView] = useState(false);
  const [detailAction, setDetailAction] = useState<'terminate' | 'continue'>();
  const terminateTrigger = useRef<HTMLButtonElement>(null);
  const continueTrigger = useRef<HTMLButtonElement>(null);
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

  function openBatch(id: string, archive = false) {
    setFeedback('');
    setBatchId(id);
    setArchiveView(archive);
    setDetailAction(undefined);
    setJobId('');
    setJobOffset(0);
  }

  function backToDetails() {
    const trigger = detailAction === 'terminate' ? terminateTrigger : continueTrigger;
    setDetailAction(undefined);
    requestAnimationFrame(() => trigger.current?.focus());
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
  let decodeError = '';
  try {
    if (assets.data) imageRows = decodeAssetList(assets.data).items;
  } catch { decodeError = '素材列表格式错误，请刷新重试。'; }

  const disabled = busy || !canWrite;
  const profile = config.data;
  const templateRows = templates.data?.items ?? [];
  const selectedTemplateRows = templateRows.filter((template) => selectedTemplates.includes(template.id));
  const batchRows = batches.data?.items ?? [];
  const selectionHint = single && (selected.length !== 1 || selectedTemplates.length !== 1)
    ? '仅跑首段时，请选择一张图和一个人物模板。'
    : !selected.length || !selectedTemplates.length
      ? '请选择服装图和人物模板。'
      : body.images.some((item) => !item.style || !item.skuId)
        ? '请填写款式编号和 SKU。'
        : '';
  const displayFullCount = selected.length * selectedTemplateRows.reduce((total, template) => total + template.clips.length, 0);
  const displayPlanCount = single
    ? Number(selected.length === 1 && selectedTemplateRows.length === 1)
    : displayFullCount;
  const stripCount = selectedTemplateRows.length === 1 ? (single ? 1 : selectedTemplateRows[0].clips.length) : 0;
  const trialHint = single && displayPlanCount > 0
    ? displayFullCount > 1
      ? '本次只跑首段。看完效果后，可在原批次继续生成剩余 ' + (displayFullCount - 1) + ' 段。'
      : '此模板只有 1 段，首段完成即整批完成。'
    : '';
  const run = async (path: string, value?: object) => { await command(path, value); };
  const close = () => setDialog(undefined);
  const notice = feedback && <p role="status" className="break-words">{feedback}</p>;
  const selectedBatch = batch.data;
  const firstClipReady = selectedBatch?.status === 'FIRST_CLIP_READY';
  const remaining = selectedBatch?.counts.DEFERRED ?? 0;
  const completed = selectedBatch?.counts.SUCCEEDED ?? 0;
  const impact = selectedBatch?.terminationImpact;
  const stopping = selectedBatch?.status === 'TERMINATING' || selectedBatch?.status === 'TERMINATED';
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
        <section className="min-w-0" aria-labelledby="rh-templates-title">
          <div className="rh-panel-heading">
            <h3 id="rh-templates-title"><span className="rh-step mr-2">02</span>人物模板</h3>
            {canWrite && <Button size="sm" isDisabled={disabled}
              onPress={() => { setFeedback(''); setDialog('template'); }}>
              <span aria-hidden="true">＋</span> 新建模板
            </Button>}
          </div>
          {templateRows.length ? <div className="rh-template-grid">{templateRows.map((template) => (
            <SelectTile key={template.id} label={templateLabel(template) + ' · ' + template.clips.length + ' 段'} disabled={disabled}
              selected={selectedTemplates.includes(template.id)}
              onChange={(checked) => setSelectedTemplates((ids) => checked ? [...ids, template.id] : ids.filter((id) => id !== template.id))}>
              <span className="rh-tile-name rh-template-name" title={template.humanName + ' · 模板 ' + template.name}>
                <span className="rh-template-mark mr-2" aria-hidden="true">{template.name}</span>
                {template.humanName} · 模板 {template.name}
              </span>
              <span className="block px-1 text-xs text-muted">{template.clips.length} 段 · {template.version ? 'v' + template.version : template.id.slice(0, 8)}</span>
            </SelectTile>
          ))}</div> : <p className="rh-empty">{templates.loading ? '正在读取模板…' : templates.error ? '模板读取失败。' : '暂无人物模板。可使用数字人素材库中已挂载的参考视频新建模板。'}</p>}
        </section>
        <div className="border-t pt-3"><Check label="仅跑首段" selected={single} onChange={setSingle} disabled={disabled} /></div>
      </section>

      <section className="rh-panel rh-plan" aria-labelledby="rh-plan-title">
        <p className="rh-step mb-2 font-mono tracking-widest">BATCH PREVIEW</p>
        <h3 id="rh-plan-title">生成计划</h3>
        <div className="rh-plan-total" role="status" aria-label="计划成片数">
          <strong>{preview?.total ?? displayPlanCount}</strong><span>段成片</span>
        </div>
        <p className="text-sm">{selected.length} 张图 · {selectedTemplates.length} 个人物模板</p>
        {stripCount > 0 && <div className="rh-strip font-mono" aria-hidden="true">
          {Array.from({ length: Math.min(stripCount, 12) }, (_, index) =>
            <span key={index}>{String(index + 1).padStart(2, '0')}</span>)}
          {stripCount > 12 && <span>+{stripCount - 12}</span>}
        </div>}
        <div className="rh-specs">{specs.map((spec) => <span key={spec}>{spec}</span>)}</div>
        <p className="mt-3 break-all text-xs">{profile
          ? profile.instanceType + ' · 并发 ' + profile.concurrency + ' · ' + (profile.workflowVersionId ? '已校验版本' : '待检查工作流')
          : config.loading ? '正在读取工作流…' : '工作流配置暂不可用'}</p>
        <div className="rh-plan-action">
          {canWrite && <Button className="rh-plan-button" isDisabled={disabled || Boolean(selectionHint)} onPress={() => { void previewPlan(); }}>预览计划 <span aria-hidden="true">→</span></Button>}
          <p className="mt-2 text-xs">{canWrite ? selectionHint || trialHint || '预览后保存批次，启动时确认付费。' : '由有操作权限的人员创建并启动批次。'}</p>
        </div>
      </section>
    </div>

    <BatchRecords batches={batchRows} loading={batches.loading} error={batches.error}
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

    {dialog === 'template' && <TemplateEditor root={root} busy={busy} feedback={feedback} close={close}
      save={async (draft) => {
        const saved = await command<Template>('/templates', draft);
        if (saved) {
          setSelectedTemplates([saved.id]);
          setFeedback('已保存 ' + templateLabel(saved) + ' · ' + saved.clips.length + ' 段');
          close();
        }
      }} />}

    {dialog === 'plan' && preview && <WorkModal title="确认生成计划" busy={busy} close={close} footer={<>
      <Button variant="secondary" isDisabled={busy} onPress={close}>返回修改</Button>
      <Button isDisabled={disabled || !profile?.workflowVersionId} onPress={() => { void saveBatch(); }}>保存批次</Button>
    </>}>
      <p className="font-semibold">服务端计划：{preview.total} 段成片 / {preview.groups} 组</p>
      <dl className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
        <div><dt>本次生成</dt><dd className="font-semibold">{preview.total} 段</dd></div>
        <div><dt>完整批次</dt><dd className="font-semibold">{preview.fullTotal} 段</dd></div>
        <div><dt>服装图片</dt><dd className="break-all">{selected.map(({ asset }) => asset.filename).join('、')}</dd></div>
        <div><dt>人物模板</dt><dd className="break-all">{selectedTemplateRows.map(templateLabel).join('、')}</dd></div>
      </dl>
      <p>保存后可在批次记录中主动启动。</p>
      <p className="rh-empty">{single && preview.fullTotal > 1
        ? '首段完成后，可在原批次继续生成剩余 ' + (preview.fullTotal - preview.total) + ' 段。'
        : '成片段数以模板实际选入的参考视频为准。'}</p>
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
      {(startTarget.counts.DEFERRED ?? 0) > 0 && <p>本次只生成首段，剩余片段等待确认后续跑。</p>}
      {startingLive && <div className="rounded-lg bg-warning-soft p-3">
        <Check label="我了解实际生成会产生 RunningHub 费用。" selected={paid} onChange={setPaid} disabled={busy} />
      </div>}
      {notice}
    </WorkModal>}

    <Drawer.Backdrop isOpen={Boolean(batchId)} isDismissable={!busy} isKeyboardDismissDisabled={busy}
      onOpenChange={(open) => { if (!open && !busy) { setBatchId(''); setJobId(''); setDetailAction(undefined); } }}>
      <Drawer.Content placement="right" className="w-full max-w-[44rem]">
        <Drawer.Dialog>
          <Drawer.Header><Drawer.Heading>{archiveView ? '归档预览' : firstClipReady ? '查看首段' : '批次详情'}</Drawer.Heading>{!busy && <Drawer.CloseTrigger aria-label="关闭批次详情" />}</Drawer.Header>
          <Drawer.Body className="space-y-3">
            {(batch.error || jobs.error) && <p role="alert">{batch.error || jobs.error}</p>}
            {batch.loading && !selectedBatch && <p role="status">正在读取批次…</p>}
            {notice}
            {selectedBatch && <>
              <p className="break-all font-mono text-xs">{selectedBatch.id}</p>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p>{statuses[selectedBatch.status] ?? selectedBatch.status} · 共 {selectedBatch.total} 个任务
                  {selectedBatch.mode === 'dry-run' ? ' · 历史演练批次' : ''}</p>
                {canWrite && canTerminateBatch(selectedBatch) && !detailAction &&
                  <Button ref={terminateTrigger} size="sm" variant="danger" isDisabled={busy}
                    onPress={() => { setFeedback(''); setDetailAction('terminate'); }}>终止批次</Button>}
              </div>
              {canWrite && canTerminateBatch(selectedBatch) && detailAction === 'terminate' &&
                <section aria-labelledby="rh-terminate-title" className="space-y-2 rounded-lg border p-3">
                  <h3 id="rh-terminate-title" className="erp-section-heading">确认终止批次？</h3>
                  {impact && <dl className="grid gap-2 sm:grid-cols-3">
                    <div><dt>已完成并保留</dt><dd>{impact.completed} 段</dd></div>
                    <div><dt>未提交，将停止派发</dt><dd>{impact.unsubmitted} 段</dd></div>
                    <div><dt>云端在途，将请求取消</dt><dd>{impact.cloudInFlight} 段</dd></div>
                  </dl>}
                  {!!impact?.unconfirmed && <p>{impact.unconfirmed} 段提交结果待确认，将继续核对。</p>}
                  <p>未提交的任务将停止，云端排队和运行中的任务将请求取消。已完成的成片和已记录的费用将保留，终止后无法恢复派发。</p>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" autoFocus isDisabled={busy} onPress={backToDetails}>返回</Button>
                    <Button size="sm" variant="danger" isDisabled={busy} onPress={() => {
                      void command<Batch>('/batches/' + selectedBatch.id + '/terminate', { confirmTerminate: true })
                        .then((saved) => { if (saved) setDetailAction(undefined); });
                    }}>确认终止</Button>
                  </div>
                </section>}
              {selectedBatch.status === 'TERMINATING' && <p role="status">
                已停止新提交，正在处理已提交的任务。跟踪中断时可在单任务详情继续核对，提交结果不明时由管理员核对。
              </p>}
              {selectedBatch.status === 'TERMINATED' && <p className="rh-empty">批次已终止。已完成的片段和费用记录保留。</p>}
              <dl className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
                <div><dt>成片进度</dt><dd>{completed} / {selectedBatch.total}</dd></div>
                <div><dt>创建时配置</dt><dd>{selectedBatch.config
                  ? selectedBatch.config.instanceType + ' · 并发 ' + selectedBatch.config.concurrency + ' · 工作流 ' + (selectedBatch.config.workflowVersionId?.slice(0, 8) ?? '—')
                  : '—'}</dd></div>
                {!!selectedBatch.templates?.length && <div className="sm:col-span-2"><dt>模板版本</dt>
                  <dd className="break-all">{selectedBatch.templates.map(templateLabel).join('、')}</dd></div>}
              </dl>
              {!archiveView && selectedBatch.previewJobId && <ClipPreview key={selectedBatch.previewJobId}
                jobId={selectedBatch.previewJobId} root={root} first={firstClipReady} />}
              <p>{Object.entries(selectedBatch.counts).map(([key, count]) => (statuses[key] ?? key) + ' ' + count).join(' / ')}</p>
              {selectedBatch.error && <p role="status">{selectedBatch.error}。暂停只停止新提交，已受理任务继续处理。</p>}
              <p>已知费用：{Object.keys(selectedBatch.costs).length
                ? Object.entries(selectedBatch.costs).map(([key, amount]) => key + ' ' + amount).join(' / ')
                : '未知'}；未知费用任务 {selectedBatch.unknownCostTasks}</p>
              <details><summary>分组完成情况</summary>{selectedBatch.groups.map((group) => <p className="break-all" key={group.path}>{group.path} · {group.succeeded}/{group.total} 已完成</p>)}</details>
            </>}
            <ul className="space-y-2">{jobs.data?.items.map((job) => <li className="rounded-lg border p-3" key={job.id}>
              <div className="flex flex-wrap items-center gap-2">
                <p className="min-w-0 flex-1 break-all">第 {String(job.identity.clip).padStart(2, '0')} 段 · {job.referenceFilename ?? job.identity.originalFilename} · {job.identity.humanName} · {job.identity.templateName} · {statuses[job.status] ?? job.status}</p>
                {job.status !== 'DEFERRED' && <Button size="sm" variant="secondary"
                  onPress={() => setJobId(job.id)}>查看任务 {job.identity.templateName}{job.identity.clip}</Button>}
              </div>
            </li>)}</ul>
            {(jobOffset > 0 || (jobs.data?.items.length ?? 0) >= 100) && <div className="rh-pager">
              <Button variant="secondary" isDisabled={!jobOffset || jobs.loading} onPress={() => setJobOffset((n) => Math.max(0, n - 100))}>上一页任务</Button>
              <Button variant="secondary" isDisabled={(jobs.data?.items.length ?? 0) < 100 || jobs.loading} onPress={() => setJobOffset((n) => n + 100)}>下一页任务</Button>
            </div>}
            {jobId && <JobDetail key={jobId} jobId={jobId} root={root} revision={revision} canWrite={canWrite && !busy} admin={admin && !busy} stopping={stopping} run={run} />}
            {selectedBatch && <BatchArchive key={selectedBatch.id} batch={selectedBatch} root={root} />}
            {selectedBatch && firstClipReady && <section aria-label="首段续跑" className="space-y-3 rounded-lg bg-success-soft p-3">
              {detailAction === 'continue' ? <>
                <h3 className="erp-section-heading">继续生成剩余 {remaining} 段</h3>
                <p>沿用首段的服装、模板顺序和工作流。首段成片保留在原批次。</p>
                <p>已完成 {completed} / {selectedBatch.total} 段 · 本次安排 {remaining} 段</p>
                {selectedBatch.mode === 'live' && <Check label="我了解继续生成会产生 RunningHub 费用。"
                  selected={paid} onChange={setPaid} disabled={busy} />}
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" autoFocus isDisabled={busy} onPress={backToDetails}>返回详情</Button>
                  <Button isDisabled={disabled || (selectedBatch.mode === 'live' && !paid)} onPress={() => {
                    void command<Batch>('/batches/' + selectedBatch.id + '/continue', { confirmPaid: paid })
                      .then((saved) => { if (saved) { setDetailAction(undefined); setFeedback('已安排剩余片段继续生成。'); } });
                  }}>确认继续生成</Button>
                </div>
              </> : <>
                <p>首段满意后，可在同一个批次里完成后续片段。</p>
                {canWrite && !detailAction && <Button ref={continueTrigger} isDisabled={busy} onPress={() => {
                  setFeedback(''); setPaid(false); setDetailAction('continue');
                }}>继续生成剩余 {remaining} 段</Button>}
              </>}
            </section>}
          </Drawer.Body>
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  </div>;
}

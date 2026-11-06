import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Label, ListBox, Select, TextArea, TextField } from '@heroui/react';
import PageIntro from '../../shared/PageIntro';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';
import { decodeAssetList, decodeDigitalHumanDetail, decodeDigitalHumanList, type Asset } from '../media-assets/contracts';
import type { Artifact, Batch, Config, Job, Template } from './contracts';
import { statuses } from './contracts';

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

function Choice({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: { id: string; name: string }[]; onChange: (value: string) => void; disabled?: boolean;
}) {
  return <Select isDisabled={disabled} selectedKey={value || null} onSelectionChange={(key) => onChange(String(key ?? ''))}>
    <Label>{label}</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
    <Select.Popover><ListBox>{options.map((option) => <ListBox.Item key={option.id} id={option.id} textValue={option.name}>{option.name}</ListBox.Item>)}</ListBox></Select.Popover>
  </Select>;
}

function Check({ label, selected, onChange, disabled }: { label: string; selected: boolean; onChange: (checked: boolean) => void; disabled?: boolean }) {
  return <Checkbox isDisabled={disabled} isSelected={selected} onChange={onChange}><Checkbox.Content>
    <Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label className="break-all">{label}</Label></Checkbox.Content></Checkbox>;
}

function Field({ label, value, onChange, disabled }: { label: string; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return <TextField isDisabled={disabled} value={value} onChange={onChange}><Label>{label}</Label><Input /></TextField>;
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
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const assets = useRead<unknown>(root, '/v2/media/assets?kind=image&status=ready&limit=24&offset=' + offset + '&keyword=' + encodeURIComponent(search), revision);
  const humans = useRead<unknown>(root, '/v2/media/digital-humans?limit=200', revision);
  const templates = useRead<{ items: Template[] }>(root, '/v2/runninghub/templates', revision);
  const config = useRead<Config>(root, '/v2/runninghub/config', revision);
  const [batchOffset, setBatchOffset] = useState(0);
  const batches = useRead<{ items: Batch[] }>(root, '/v2/runninghub/batches?limit=30&offset=' + batchOffset, revision, true);
  const [humanId, setHumanId] = useState('');
  const human = useRead<unknown>(root, humanId ? '/v2/media/digital-humans/' + humanId : null, revision);
  const [templateName, setTemplateName] = useState('A');
  const [clipIds, setClipIds] = useState<string[]>(Array(6).fill(''));
  const [selected, setSelected] = useState<{ asset: Asset; skuId: string; style: string }[]>([]);
  const [style, setStyle] = useState('');
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [mode, setMode] = useState('dry-run');
  const [single, setSingle] = useState(true);
  const [preview, setPreview] = useState<{ total: number; groups: number; paths: string[] }>();
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [batchId, setBatchId] = useState('');
  const batch = useRead<Batch>(root, batchId ? '/v2/runninghub/batches/' + batchId : null, revision, true);
  const [jobOffset, setJobOffset] = useState(0);
  const jobs = useRead<{ items: Job[] }>(root, batchId ? '/v2/runninghub/batches/' + batchId + '/jobs?limit=100&offset=' + jobOffset : null, revision, true);
  const [jobId, setJobId] = useState('');
  const [credentialRef, setCredentialRef] = useState('env:RUNNINGHUB_API_KEY');
  const [instanceType, setInstanceType] = useState('plus');
  const [concurrency, setConcurrency] = useState('5');
  const [graph, setGraph] = useState('');
  const body = { images: selected.map((s) => ({ assetId: s.asset.assetId, skuId: s.skuId, style: s.style })), templateIds: selectedTemplates, single };
  useEffect(() => { setPreview(undefined); }, [selected, selectedTemplates, single]);

  async function command<T>(path: string, value: object = {}, method = 'POST'): Promise<T | undefined> {
    if (pending.current) return undefined;
    pending.current = true; setBusy(true); setFeedback('');
    try {
      const response = await requestJson<T>(root, '/v2/runninghub' + path, { method, body: JSON.stringify(value) });
      if (mounted.current) { setFeedback('操作已保存。'); setRevision((n) => n + 1); }
      return response.data;
    } catch (failure) { if (mounted.current) setFeedback(errorText(failure)); }
    finally { pending.current = false; if (mounted.current) setBusy(false); }
    return undefined;
  }
  const run = async (path: string, value?: object) => { await command(path, value); };
  let imageRows: Asset[] = [], humanRows: ReturnType<typeof decodeDigitalHumanList> = [], videoRows: Asset[] = [];
  let decodeError = '';
  try {
    if (assets.data) imageRows = decodeAssetList(assets.data).items;
    if (humans.data) humanRows = decodeDigitalHumanList(humans.data).filter((h) => h.status === 'active');
    if (human.data) videoRows = decodeDigitalHumanDetail(human.data).assets.filter((a) => a.kind === 'video' && a.status === 'ready');
  } catch { decodeError = '素材列表格式错误，请刷新重试。'; }
  const disabled = busy || !canWrite;

  return <div className="space-y-6">
    <PageIntro eyebrow="内容资产" title="换装视频" description="先预览计划，再主动启动。默认离线演练；真实任务按账户最多 5 个在途，成片经下载与规格校验后完成。">
      <Button variant="secondary" onPress={() => setRevision((n) => n + 1)}>刷新状态</Button>
    </PageIntro>
    {!canWrite && <p role="status">当前为只读权限。</p>}
    {[config.error, templates.error, assets.error, humans.error, batches.error, batch.error, jobs.error, human.error, decodeError].filter(Boolean).map((error, index) => <p role="alert" key={index}>{error}</p>)}
    {feedback && <p role="status">{feedback}</p>}
    <section className="rounded-xl border p-4 space-y-3"><h3 className="erp-section-heading">工作流与账户</h3>
      <p>{config.data ? `${config.data.workflowId} · ${config.data.instanceType} · 并发 ${config.data.concurrency} · ${config.data.workflowVersionId ? '已校验版本' : '待管理员检查工作流'}` : '正在读取配置…'}</p>
      <p>5 秒 · 24 fps · 576×1024 · 120 帧</p>
      {config.data?.dispatchPausedReason && <p role="status">账户新任务派发已暂停：{config.data.dispatchPausedReason}。已受理任务继续跟踪。</p>}
      {admin && <details><summary>管理配置</summary><div className="space-y-3 mt-3">
        <p>当前凭据引用：{config.data?.credentialRef}（{config.data?.credentialConfigured ? '已配置' : '未配置'}）</p>
        <Field label="凭据引用" value={credentialRef} onChange={setCredentialRef} />
        <Choice label="显存档位" value={instanceType} onChange={setInstanceType} options={[{ id: 'default', name: 'default / 24GB' }, { id: 'plus', name: 'plus / 48GB' }, { id: 'ultra', name: 'ultra / 84GB' }]} />
        <Choice label="云端并发上限" value={concurrency} onChange={setConcurrency} options={[1, 2, 3, 4, 5].map((n) => ({ id: String(n), name: String(n) }))} />
        <Button isDisabled={busy} onPress={() => { void command('/config', { credentialRef, workflowId: config.data?.workflowId ?? '2111762608670990337', instanceType, concurrency: Number(concurrency) }, 'PUT'); }}>保存配置</Button>
        <Button isDisabled={busy} variant="secondary" onPress={() => { void command<Record<string, unknown>>('/account').then((data) => { if (data) setFeedback('账户信息：' + JSON.stringify(data)); }); }}>检查账户</Button>
        <TextField value={graph} onChange={setGraph}><Label>离线导入完整 API 图（可留空，从官方读取）</Label><TextArea /></TextField>
        <Button isDisabled={busy} onPress={() => { let value; try { value = graph.trim() ? JSON.parse(graph) : null; } catch { setFeedback('工作流 JSON 无法解析。'); return; } void command('/workflow/check', { graph: value }); }}>检查并保存工作流版本</Button>
        {config.data?.dispatchPausedReason && <Button isDisabled={busy} onPress={() => { if (window.confirm('请先修复账户余额、凭据或工作流配置。恢复后，已启动的真实批次可继续付费生成。是否恢复？')) void command('/dispatch/resume', { confirmPaid: true }); }}>恢复账户派发</Button>}
      </div></details>}
    </section>
    {canWrite && <details className="rounded-xl border p-4"><summary>配置人物模板的六段视频</summary><div className="mt-3 space-y-3">
      <p>先在数字人素材库挂载六段不同的视频，再按实际顺序选择。保存后形成独立模板版本。</p>
      <Choice label="人物" value={humanId} onChange={(id) => { setHumanId(id); setClipIds(Array(6).fill('')); }} options={humanRows.map((h) => ({ id: h.digitalHumanId, name: h.name }))} />
      <Choice label="模板" value={templateName} onChange={setTemplateName} options={['A', 'B', 'C'].map((id) => ({ id, name: id }))} />
      <div className="grid gap-3 md:grid-cols-2">{clipIds.map((id, index) => <Choice key={index} label={`第 ${index + 1} 段`} value={id} disabled={busy || !humanId}
        options={videoRows.map((v) => ({ id: v.assetId, name: v.filename }))} onChange={(value) => setClipIds((old) => old.map((entry, i) => i === index ? value : entry))} />)}</div>
      <Button isDisabled={busy || clipIds.some((id) => !id)} onPress={() => { void command('/templates', { humanId, name: templateName, assetIds: clipIds }); }}>保存六段模板</Button>
    </div></details>}
    {canWrite && <section className="rounded-xl border p-4 space-y-3"><h3 className="erp-section-heading">创建计划</h3>
      <Field label="默认款式编号" value={style} onChange={setStyle} />
      <Field label="搜索服装图片" value={search} onChange={(value) => { setSearch(value); setOffset(0); }} />
      <div className="grid gap-3 md:grid-cols-3">{imageRows.map((asset) => <div className="min-w-0 rounded-lg border p-3" key={asset.assetId}>
        {asset.url && <img className="h-28 w-full object-contain" src={asset.url} alt={asset.filename} loading="lazy" />}
        <Check label={asset.filename} disabled={disabled} selected={selected.some((s) => s.asset.assetId === asset.assetId)} onChange={(checked) => setSelected((rows) => checked ? [...rows, { asset, skuId: asset.assetId, style }] : rows.filter((s) => s.asset.assetId !== asset.assetId))} />
      </div>)}</div>
      <div className="flex flex-wrap gap-2"><Button variant="secondary" isDisabled={!offset} onPress={() => setOffset((n) => Math.max(0, n - 24))}>上一页图片</Button><Button variant="secondary" isDisabled={imageRows.length < 24} onPress={() => setOffset((n) => n + 24)}>下一页图片</Button></div>
      {!assets.loading && !imageRows.length && <p>没有就绪图片。请先到资产管理上传服装图。</p>}
      {selected.map((entry, index) => <div className="grid gap-2 md:grid-cols-3" key={entry.asset.assetId}><p className="break-all">{entry.asset.filename}</p>
        <Field label={`款式 ${entry.asset.filename}`} value={entry.style} onChange={(value) => setSelected((rows) => rows.map((row, i) => i === index ? { ...row, style: value } : row))} />
        <Field label={`SKU ${entry.asset.filename}`} value={entry.skuId} onChange={(value) => setSelected((rows) => rows.map((row, i) => i === index ? { ...row, skuId: value } : row))} /></div>)}
      <fieldset className="space-y-2"><legend>人物 × 模板</legend>{templates.data?.items.map((template) => <div key={template.id}><Check label={`${template.humanName} / ${template.name} · ${template.id.slice(0, 8)}`} selected={selectedTemplates.includes(template.id)} disabled={disabled}
        onChange={(checked) => setSelectedTemplates((ids) => checked ? [...ids, template.id] : ids.filter((id) => id !== template.id))} /></div>)}</fieldset>
      <Check label="单任务联调（仅选择一张图、一个模板的第 1 段）" selected={single} onChange={setSingle} disabled={disabled} />
      <Choice label="生成模式" value={mode} onChange={setMode} options={[{ id: 'dry-run', name: '离线演练（不请求 RunningHub）' }, { id: 'live', name: '真实生成（启动时确认付费）' }]} />
      <p className="break-all">请求 ID：{requestId}</p>
      <div className="flex flex-wrap gap-2"><Button isDisabled={disabled} variant="secondary" onPress={() => { void command<typeof preview>('/plan', body).then((data) => { if (data) setPreview(data); }); }}>预览计划</Button>
        <Button isDisabled={disabled || !preview || !config.data?.workflowVersionId} onPress={() => { void command<Batch>('/batches', { ...body, requestId, mode }).then((saved) => { if (saved) { setBatchId(saved.id); setJobId(''); setJobOffset(0); } }); }}>保存批次</Button>
        <Button variant="tertiary" isDisabled={busy} onPress={() => setRequestId(crypto.randomUUID())}>新批次 ID</Button></div>
      {preview && <details open><summary>服务端计划：{preview.total} 个任务 / {preview.groups} 组</summary><ul className="max-h-52 overflow-auto">{preview.paths.map((path) => <li className="break-all" key={path}>{path}</li>)}</ul></details>}
    </section>}
    <section className="space-y-3"><h3 className="erp-section-heading">批次</h3>
      <Choice label="查看批次" value={batchId} onChange={(id) => { setBatchId(id); setJobId(''); setJobOffset(0); }} options={(batches.data?.items ?? []).map((b) => ({ id: b.id, name: `${b.id} · ${b.mode} · ${statuses[b.status] ?? b.status}` }))} />
      <div className="flex gap-2"><Button variant="secondary" isDisabled={!batchOffset} onPress={() => setBatchOffset((n) => Math.max(0, n - 30))}>上一页批次</Button><Button variant="secondary" isDisabled={(batches.data?.items.length ?? 0) < 30} onPress={() => setBatchOffset((n) => n + 30)}>下一页批次</Button></div>
      {batch.data && <><p>{statuses[batch.data.status] ?? batch.data.status} · 共 {batch.data.total} 个任务</p>
        <p>{Object.entries(batch.data.counts).map(([key, count]) => `${statuses[key] ?? key} ${count}`).join(' / ')}</p>
        {batch.data.error && <p role="status">{batch.data.error}。暂停只停止新提交，已受理任务继续处理。</p>}
        <p>已知费用：{Object.keys(batch.data.costs).length ? Object.entries(batch.data.costs).map(([key, amount]) => `${key} ${amount}`).join(' / ') : '未知'}；未知费用任务 {batch.data.unknownCostTasks}</p>
        {canWrite && batch.data.status !== 'SUCCEEDED' && <div className="flex flex-wrap gap-2"><Button isDisabled={busy || batch.data.status === 'ACTIVE'} onPress={() => {
          const current = batch.data!;
          if (current.mode === 'live' && !window.confirm(`启动真实批次 ${current.id}，将调用 RunningHub 付费生成。是否继续？`)) return;
          void command('/batches/' + current.id + '/start', { confirmPaid: current.mode === 'live' });
        }}>启动 / 恢复批次</Button><Button variant="secondary" isDisabled={busy || batch.data.status !== 'ACTIVE'} onPress={() => { void command('/batches/' + batchId + '/pause'); }}>暂停新提交</Button></div>}
        {batch.data.status === 'SUCCEEDED' && <a className="underline" href={pathFor(root, '/v2/runninghub/batches/' + batchId + '/export')}>按 SKU 导出全部成片 ZIP</a>}
        <details><summary>分组完成情况</summary>{batch.data.groups.map((group) => <p className="break-all" key={group.path}>{group.path} · {group.succeeded}/{group.total} 已完成</p>)}</details>
      </>}
      <ul className="space-y-2">{jobs.data?.items.map((job) => <li className="rounded-lg border p-3" key={job.id}><div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 break-all flex-1">{job.identity.originalFilename} · {job.identity.humanName} · {job.identity.templateName}{job.identity.clip} · {statuses[job.status] ?? job.status}</p>
        <Button variant="secondary" onPress={() => setJobId(job.id)}>查看任务 {job.identity.templateName}{job.identity.clip}</Button></div></li>)}</ul>
      {batchId && <div className="flex gap-2"><Button variant="secondary" isDisabled={!jobOffset} onPress={() => setJobOffset((n) => Math.max(0, n - 100))}>上一页任务</Button><Button variant="secondary" isDisabled={(jobs.data?.items.length ?? 0) < 100} onPress={() => setJobOffset((n) => n + 100)}>下一页任务</Button></div>}
    </section>
    {jobId && <JobDetail key={jobId} jobId={jobId} root={root} revision={revision} canWrite={canWrite && !busy} admin={admin && !busy} run={run} />}
  </div>;
}

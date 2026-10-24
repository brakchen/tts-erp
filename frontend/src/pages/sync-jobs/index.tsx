import { useEffect, useRef, useState } from 'react';
import { Button, Label, ListBox, Modal, Select, Table } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import type { PageProps } from '../../shared/contracts';
import { decodeAcknowledgement, decodeJobs, type Attempt, type Job, type JobsData } from './contracts';

type Recovery = { attempt: Attempt; phase: 'waiting' | 'checking' | 'checked' | 'failed' };
const listPath = '/v2/sync/jobs';
const displayTime = (value: string | null) => value === null ? '—' : new Date(value).toLocaleString('zh-CN', { hour12: false });

export default function SyncJobs({ bootstrap }: PageProps) {
  const canRead = bootstrap.roleCapabilities?.readwrite === true || bootstrap.roleCapabilities?.admin === true;
  const canAdmin = bootstrap.roleCapabilities?.admin === true;
  const [data, setData] = useState<JobsData>();
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [confirmation, setConfirmation] = useState<Attempt>();
  const [pending, setPending] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const [denied, setDenied] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [notice, setNotice] = useState('');
  const mounted = useRef(true);
  const busy = useRef(false);
  const reading = useRef(false);
  const gate = useRef<Recovery | undefined>(undefined);
  const notBefore = useRef(0);
  const stopDelay = useRef<(() => void) | undefined>(undefined);
  const verification = useRef<AbortController | undefined>(undefined);
  const authorized = useRef({ canRead, canAdmin });
  authorized.current = { canRead, canAdmin };

  function wait(seconds: number) {
    if (!(seconds > 0)) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + seconds * 1000);
    stopDelay.current?.(); setCooldown(true);
    stopDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, notBefore.current - performance.now());
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; stopDelay.current?.(); verification.current?.abort(); }; }, []);
  useEffect(() => {
    if (!canRead || performance.now() < notBefore.current) return;
    const controller = new AbortController(); let current = true;
    reading.current = true; setLoading(true); setReadError('');
    requestJson<unknown>(bootstrap.rootPath, listPath, { signal: controller.signal }).then(({ data: value }) => {
      const decoded = decodeJobs(value);
      if (current) setData(decoded);
    }).catch((error: unknown) => {
      if (!current) return;
      if (error instanceof ApiError) wait(error.retryAfter ?? 0);
      setData(undefined); setReadError(error instanceof ApiError ? `任务列表不可用（HTTP ${error.status}）` : '任务列表不可用，请明确刷新重试');
    }).finally(() => { if (current) { reading.current = false; setLoading(false); } });
    return () => { current = false; controller.abort(); reading.current = false; };
  }, [bootstrap.rootPath, canRead, refresh]);

  const blocked = !canRead || pending || loading || cooldown || denied || recovery !== undefined;
  function maySubmit(attempt: Attempt) {
    return authorized.current.canRead && (attempt.kind !== 'enabled' || authorized.current.canAdmin)
      && !busy.current && !reading.current && !denied && performance.now() >= notBefore.current && !gate.current;
  }
  async function submit(attempt: Attempt) {
    if (!maySubmit(attempt)) return;
    busy.current = true; setPending(true); setFailure(undefined); setNotice('');
    try {
      const body = attempt.kind === 'enabled' ? { enabled: attempt.enabled } : attempt.shop_id === null ? {} : { shop_id: attempt.shop_id };
      const { data: value } = await requestJson<unknown>(bootstrap.rootPath, `/v2/admin/sync-jobs/${encodeURIComponent(attempt.job_name)}/${attempt.kind}`, {
        method: attempt.kind === 'enabled' ? 'PATCH' : 'POST', body: JSON.stringify(body),
      });
      decodeAcknowledgement(value, attempt);
      if (!mounted.current) return;
      if (attempt.kind === 'enabled') {
        setData((previous) => previous && { ...previous, jobs: previous.jobs.map((j) => j.job_name === attempt.job_name ? { ...j, enabled: attempt.enabled } : j) });
        setNotice('周期调度变更已确认');
      } else setNotice('已提交后台，尚未确认完成；请稍后明确刷新查看运行状态。');
    } catch (error: unknown) {
      if (!mounted.current) return;
      const result = commandFailure(error, { enabled: '周期调度', shop_id: '执行店铺' });
      setFailure(result); wait(result.retryAfter);
      if (error instanceof ApiError && [401, 403].includes(error.status)) setDenied(true);
      if (result.uncertain || (error instanceof ApiError && [404, 409].includes(error.status))) {
        const next: Recovery = { attempt, phase: 'waiting' }; gate.current = next; setRecovery(next);
      }
    } finally { busy.current = false; if (mounted.current) setPending(false); }
  }
  function trigger(job: Job) {
    const attempt: Attempt = { kind: 'trigger', job_name: job.job_name, shop_id: job.is_tiktok ? selection[job.job_name] || null : null };
    if (!maySubmit(attempt)) return;
    if (job.is_tiktok && attempt.shop_id === null) setConfirmation(attempt);
    else void submit(attempt);
  }
  async function verify() {
    const previous = gate.current;
    if (!previous || previous.phase === 'checking' || reading.current || busy.current || !canRead || performance.now() < notBefore.current) return;
    const next: Recovery = { ...previous, phase: 'checking' }; gate.current = next; setRecovery(next);
    const controller = new AbortController(); verification.current = controller;
    try {
      const { data: value } = await requestJson<unknown>(bootstrap.rootPath, listPath, { signal: controller.signal });
      const decoded = decodeJobs(value);
      const attempt = previous.attempt;
      const job = decoded.jobs.find((j) => j.job_name === attempt.job_name);
      if (!job || (attempt.kind === 'trigger' && attempt.shop_id !== null
        && (!job.is_tiktok || !decoded.tiktok_shops.some((s) => s.shop_id === attempt.shop_id)))) throw new Error('核对未完成');
      if (mounted.current) { setData(decoded); const checked: Recovery = { ...previous, phase: 'checked' }; gate.current = checked; setRecovery(checked); }
    } catch (error: unknown) {
      if (!mounted.current || controller.signal.aborted) return;
      if (error instanceof ApiError) wait(error.retryAfter ?? 0);
      const failed: Recovery = { ...previous, phase: 'failed' }; gate.current = failed; setRecovery(failed);
    } finally { if (verification.current === controller) verification.current = undefined; }
  }
  function retry() {
    const previous = gate.current;
    if (previous?.phase !== 'checked' || performance.now() < notBefore.current || denied) return;
    gate.current = undefined; setRecovery(undefined);
    void submit(previous.attempt);
  }

  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">任务清单</h2>
      <Button variant="secondary" isDisabled={!canRead || loading || pending || cooldown || recovery?.phase === 'checking'} onPress={() => { if (!reading.current && !busy.current && performance.now() >= notBefore.current) setRefresh((n) => n + 1); }}>刷新任务</Button></div>
    <p>立即执行权限：readwrite / admin；启停周期调度需要 admin。停用周期调度不影响立即执行，权限以服务端判定为准。</p>
    {!canRead && <p role="alert">需要 readwrite 或 admin 才能读取任务管理数据。</p>}
    {loading && <p role="status">加载任务中…</p>}{readError && <p role="alert">{readError}</p>}
    {pending && <p role="status">提交中，请勿重复操作…</p>}{notice && <p role="status">{notice}</p>}
    {failure && <div role="alert"><p>{failure.message}</p>{Object.values(failure.fieldErrors).map((text) => <p key={text}>{text}</p>)}</div>}
    {cooldown && <p role="status">服务端指定冷却中，读取和提交均暂停；到期不会自动重试。</p>}
    {recovery && <section aria-label="命令核对" className="space-y-2">
      <p className="break-all">原操作：{recovery.attempt.job_name} · {recovery.attempt.kind === 'enabled' ? (recovery.attempt.enabled ? '启用' : '停用') : `执行 / ${recovery.attempt.shop_id ?? '全部店铺或系统级'}`}</p>
      <Button isDisabled={cooldown || loading || recovery.phase === 'checking' || !canRead} onPress={() => { void verify(); }}>查询核对</Button>
      {recovery.phase === 'checking' && <p role="status">核对中…</p>}
      {recovery.phase === 'failed' && <p role="status">核对未完成，请明确重试查询。</p>}
      {recovery.phase === 'checked' && <><p role="status">已读取当前公开任务状态；不能证明原命令是否生效。重新执行可能重复上游同步，请明确决定。</p><Button isDisabled={cooldown || denied || !canRead || (recovery.attempt.kind === 'enabled' && !canAdmin)} onPress={retry}>核对后明确重试原操作</Button></>}
    </section>}
    {canRead && data && <><p>服务端时间：{displayTime(data.server_time)}。健康级别与落后周期均沿用服务端结果；列表保持服务端顺序。</p>
      <Table><Table.ScrollContainer role="region" aria-label="任务表格"><Table.Content aria-label="定时任务" className="min-w-[75rem]"><Table.Header>
        <Table.Column isRowHeader>任务</Table.Column><Table.Column>周期</Table.Column><Table.Column>范围</Table.Column><Table.Column>状态</Table.Column><Table.Column>上次运行</Table.Column><Table.Column>周期调度</Table.Column><Table.Column>立即执行</Table.Column>
      </Table.Header><Table.Body renderEmptyState={() => '暂无注册任务'}>{data.jobs.map((job) => <Table.Row key={job.job_name} id={job.job_name}>
        <Table.Cell><div className="w-52 break-all"><p className="font-semibold">{job.job_name}</p><p>{job.module_path} · {job.entrypoint}</p></div></Table.Cell>
        <Table.Cell>{job.interval_seconds} 秒</Table.Cell><Table.Cell><p>{job.is_tiktok ? 'TikTok 店铺' : '系统级'}</p><p>{job.scope_type}</p></Table.Cell>
        <Table.Cell><p>{job.last_status.severity}</p><p>{job.last_status.last_status ?? '从未运行'}</p><p>落后周期：{job.last_status.cycles_late ?? '—'}</p><p>间隔秒数：{job.last_status.lag_seconds ?? '—'}</p>{job.last_status.last_error !== null && <p className="w-48 break-words">错误：{job.last_status.last_error}</p>}</Table.Cell>
        <Table.Cell><p>开始：{displayTime(job.last_status.last_run_at)}</p><p>结束：{displayTime(job.last_status.last_finished_at)}</p><p>下次预计：{displayTime(job.last_status.next_expected_at)}</p></Table.Cell>
        <Table.Cell focusMode="child"><p>{job.enabled ? '已启用' : '已停用'}</p><Button size="sm" isDisabled={blocked || !canAdmin} aria-label={`${job.enabled ? '停用' : '启用'} ${job.job_name} 周期调度`} onPress={() => { void submit({ kind: 'enabled', job_name: job.job_name, enabled: !job.enabled }); }}>{job.enabled ? '停用' : '启用'}</Button></Table.Cell>
        <Table.Cell focusMode="child"><div className="w-64 space-y-2">{job.is_tiktok && <Select aria-label={`执行店铺 ${job.job_name}`} value={selection[job.job_name] || 'all'} isDisabled={blocked} onChange={(key) => {
          if (typeof key === 'string') { setSelection((s) => ({ ...s, [job.job_name]: key === 'all' ? '' : key })); setFailure((f) => f && Object.keys(f.fieldErrors).length ? { ...f, fieldErrors: {} } : f); }
        }}><Label>执行店铺</Label><Select.Trigger aria-label={`执行店铺 ${job.job_name}`}><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="all" textValue="全部授权店铺">全部授权店铺</ListBox.Item>{data.tiktok_shops.map((s) => <ListBox.Item key={s.shop_id} id={s.shop_id} textValue={`${s.account_name ?? '未命名'} · ${s.shop_id}`}>{s.account_name ?? '未命名'} · {s.shop_id}{s.status === null ? '' : ` · ${s.status}`}</ListBox.Item>)}</ListBox></Select.Popover></Select>}
        <Button size="sm" isDisabled={blocked} aria-label={`执行 ${job.job_name}`} onPress={() => trigger(job)}>执行</Button></div></Table.Cell>
      </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table></>}
    <Modal.Backdrop isOpen={confirmation !== undefined} onOpenChange={(open) => { if (!open) setConfirmation(undefined); }}><Modal.Container><Modal.Dialog>
      <Modal.Header><Modal.Heading>确认全店铺执行</Modal.Heading></Modal.Header><Modal.Body><p className="break-all">将立即执行 {confirmation?.job_name} 的全部授权店铺，可能触发多店铺上游同步，确定继续吗？</p></Modal.Body>
      <Modal.Footer><Button variant="secondary" onPress={() => setConfirmation(undefined)}>取消</Button><Button isDisabled={blocked} onPress={() => { const attempt = confirmation; setConfirmation(undefined); if (attempt) void submit(attempt); }}>确认全店铺执行</Button></Modal.Footer>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}

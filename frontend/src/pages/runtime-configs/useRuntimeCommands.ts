import { useEffect, useRef, useState } from 'react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { acknowledge, decodeDetail, decodeList, decodeSecret, type Detail } from './contracts';
import { parseRuntime } from './json';

export const runtimePath = '/v2/config/runtime';
export interface Attempt { kind: 'create' | 'draft' | 'publish' | 'rollback' | 'retire' | 'restore' | 'secret-save' | 'secret-retire' | 'secret-restore'; key: string; target?: number }
const labels = { configKey: '配置键', displayName: '配置名称', draftPayload: '初始草稿', jsonSchema: 'Schema', payload: '草稿配置', expectedDraftVersion: '草稿版本', comment: '发布说明', value: '凭证明文' };
interface Recovery { attempt: Attempt; phase: 'unverified' | 'checking' | 'checked' | 'failed'; summary?: string }

/** Page-local singleflight and uncertainty gate. Neither timers nor reads replay commands. */
export function useRuntimeCommands(rootPath: string, canWrite: boolean) {
  const [pending, setPending] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [notice, setNotice] = useState('');
  const flight = useRef(false);
  const checkFlight = useRef(false);
  const gate = useRef<Recovery | undefined>(undefined);
  const deadline = useRef(0);
  const timer = useRef<(() => void) | undefined>(undefined);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; timer.current?.(); }; }, []);
  function delay(error: unknown) {
    if (!(error instanceof ApiError) || !error.retryAfter) return;
    deadline.current = Math.max(deadline.current, performance.now() + error.retryAfter * 1000);
    timer.current?.(); setBlocked(true);
    timer.current = scheduleReadDelay(() => { if (alive.current) setBlocked(false); }, deadline.current - performance.now());
  }
  const canRead = () => performance.now() >= deadline.current;
  async function read<T>(path: string, init: RequestInit = {}, parseResponse?: (text: string) => T) {
    if (!canRead()) throw new Error('读取冷却中');
    try { return await requestJson<T>(rootPath, runtimePath + path, init, parseResponse); }
    catch (error) { if (alive.current) delay(error); throw error; }
  }
  const canStart = () => canWrite && !flight.current && !gate.current && canRead();
  async function run(attempt: Attempt, path: string, method: string, body: string | undefined, success: string): Promise<Detail | true | undefined> {
    if (!canStart()) return;
    flight.current = true; setPending(true); setFailure(undefined); setNotice('');
    try {
      const { data } = await requestJson<unknown>(rootPath, runtimePath + path, { method, body }, parseRuntime);
      let result: Detail | true = true;
      if (attempt.kind === 'create' || attempt.kind === 'draft') result = decodeDetail(data, attempt.key);
      else acknowledge(data, attempt.kind, attempt.key, attempt.target);
      if (!alive.current) return;
      setNotice(success); return result;
    } catch (error) {
      if (!alive.current) return;
      delay(error); const result = commandFailure(error, labels); setFailure(result);
      if (result.uncertain) { const next: Recovery = { attempt, phase: 'unverified' }; gate.current = next; setRecovery(next); }
    } finally { flight.current = false; if (alive.current) setPending(false); }
  }
  async function verify() {
    const current = gate.current;
    if (!current || checkFlight.current || !canRead()) return;
    checkFlight.current = true;
    gate.current = { ...current, phase: 'checking' }; setRecovery(gate.current);
    try {
      let summary: string;
      if (current.attempt.kind.startsWith('secret-')) {
        const { data } = await read<unknown>('/secrets?includeRetired=true', {}, parseRuntime);
        const row = decodeList(data, decodeSecret).find((s) => s.name === current.attempt.key);
        summary = `元数据不能证明原凭证是否写入，也不能回读明文。当前 ${current.attempt.key}：${row ? `${row.retiredAt ? '已归档' : '有效'} · 指纹 ${row.fingerprint} · 更新 ${row.updatedAt}` : '未找到'}。`;
      } else {
        const { data } = await read<unknown>(`/items/${encodeURIComponent(current.attempt.key)}`, {}, parseRuntime);
        const row = decodeDetail(data, current.attempt.key);
        summary = `当前 ${row.configKey}：草稿版本 ${row.draftVersion} · 已发布 ${row.publishedVersion === null ? '无' : `v${row.publishedVersion}`} · ${row.retiredAt ? '已归档' : '有效'}；这些当前事实不能证明原命令结果，未覆盖你的编辑。`;
      }
      if (alive.current) { gate.current = { ...current, phase: 'checked', summary }; setRecovery(gate.current); }
    } catch (error) {
      if (alive.current) {
        delay(error);
        const createAbsent = current.attempt.kind === 'create' && error instanceof ApiError && error.status === 404 && error.detail === 'runtime config not found';
        gate.current = createAbsent
          ? { ...current, phase: 'checked', summary: `当前未找到 ${current.attempt.key}；不能证明原创建是否提交，也不能证明已回滚。创建输入仍保留；重新操作前须明确决定。` }
          : { ...current, phase: 'failed', summary: '核对失败，操作仍被阻止；请稍后明确重试查询。' };
        setRecovery(gate.current);
      }
    } finally { checkFlight.current = false; }
  }
  function allowManual() {
    if (gate.current?.phase !== 'checked' || !canRead()) return;
    gate.current = undefined; setRecovery(undefined); setFailure(undefined); setNotice('已允许手动操作；不会自动重放。请核对版本后重新选择操作。');
  }
  function edited() { setFailure((old) => old && Object.keys(old.fieldErrors).length ? { ...old, fieldErrors: {} } : old); }
  function invalid(message: string) { setFailure({ message, fieldErrors: {}, uncertain: false, retryAfter: 0 }); }
  return { pending, blocked, failure, recovery, notice, read, run, verify, allowManual, edited, invalid, canRead,
    disabled: !canWrite || pending || blocked || recovery !== undefined };
}


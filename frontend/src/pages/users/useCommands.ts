import { useEffect, useRef, useState } from 'react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { commandResult, labels, type CommandKind } from './contracts';
import { withinRequestLifetime } from './requestLifetime';

export interface Command { path: string; method: string; body?: unknown; kind: CommandKind }
type Recovery = 'none' | 'unverified' | 'checking' | 'failed' | 'checked' | 'allowed';
/** Page-local singleflight and explicit recovery. GET is evidence, never proof of a write or password. */
export function useCommands(root: string, canManage: boolean) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [phase, setPhase] = useState<Recovery>('none');
  const [cooldown, setCooldown] = useState(false);
  const [notice, setNotice] = useState('');
  const lock = useRef(false), recovery = useRef<Recovery>('none'), notBefore = useRef(0);
  const alive = useRef(true), delay = useRef<(() => void) | undefined>(undefined);
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => { alive.current = true; return () => { alive.current = false; delay.current?.(); for (const c of controllers.current) c.abort(); }; }, []);
  function wait(seconds: number) {
    if (!(seconds > 0)) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + seconds * 1000);
    setCooldown(true); delay.current?.();
    delay.current = scheduleReadDelay(() => { if (alive.current) setCooldown(false); }, notBefore.current - performance.now());
  }
  const readyRead = () => performance.now() >= notBefore.current;
  async function bounded<T>(operation: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal): Promise<T> {
    const controller = new AbortController(); controllers.current.add(controller);
    try { return await withinRequestLifetime(controller, operation, parent); }
    finally { controllers.current.delete(controller); }
  }
  async function read<T>(path: string, decode: (data: unknown) => T, parent?: AbortSignal): Promise<T> {
    if (!readyRead()) throw new Error('读取冷却中');
    try {
      const result = await bounded((signal) => requestJson<unknown>(root, path, { signal }, parseIdentityJson), parent);
      return decode(result.data);
    } catch (error) { if (alive.current && error instanceof ApiError) wait(error.retryAfter ?? 0); throw error; }
  }
  const canSubmit = () => canManage && !lock.current && readyRead() && ['none', 'allowed'].includes(recovery.current);
  async function submit(command: Command): Promise<boolean> {
    if (!canSubmit()) return false;
    lock.current = true; setPending(true); setFailure(undefined); setNotice('');
    recovery.current = 'none'; setPhase('none');
    try {
      const { data } = await bounded((signal) => requestJson<unknown>(root, command.path, {
        method: command.method, signal, body: command.body === undefined ? undefined : JSON.stringify(command.body),
      }, parseIdentityJson));
      commandResult(data, command.kind);
      if (alive.current) { setNotice('操作已确认'); return true; }
      return false;
    } catch (error) {
      if (alive.current) {
        const result = commandFailure(error, labels); setFailure(result); wait(result.retryAfter);
        if (result.uncertain) { recovery.current = 'unverified'; setPhase('unverified'); }
      }
      return false;
    } finally { lock.current = false; if (alive.current) setPending(false); }
  }
  async function verify(load: (signal: AbortSignal) => Promise<unknown>) {
    if (!readyRead() || lock.current || !['unverified', 'failed', 'checked', 'allowed'].includes(recovery.current)) return;
    recovery.current = 'checking'; setPhase('checking');
    try { await bounded(load); if (alive.current) { recovery.current = 'checked'; setPhase('checked'); } }
    catch { if (alive.current) { recovery.current = 'failed'; setPhase('failed'); } }
  }
  function allowRetry() { if (recovery.current === 'checked' && readyRead()) { recovery.current = 'allowed'; setPhase('allowed'); } }
  function edit() { setFailure((f) => f && Object.keys(f.fieldErrors).length ? { ...f, fieldErrors: {} } : f); }
  function reset() { if (recovery.current !== 'none' || lock.current || !readyRead()) return false; setFailure(undefined); setNotice(''); return true; }
  return { pending, failure, phase, cooldown, notice, read, submit, verify, allowRetry, edit, reset,
    blocked: !canManage || pending || cooldown || !['none', 'allowed'].includes(phase), readyRead, canSubmit };
}
export type Commands = ReturnType<typeof useCommands>;
export function readError(error: unknown): string { return error instanceof ApiError ? `读取失败（HTTP ${error.status}）` : '读取数据不可用，请手动重试'; }

import type { DiagnosticValue, TaskDiagnostics } from './contracts';
import { Facts } from './TaskFacts';

/** Presentation of the producer's already-redacted, bounded JSON values; no client scrubbing or links. */
function OutputValue({ value }: { value: DiagnosticValue }) {
  if (value === null) return <span>null</span>;
  if (typeof value !== 'object') return <span className="whitespace-pre-wrap break-all">{String(value)}</span>;
  if (Array.isArray(value)) return value.length ? <ol className="list-inside list-decimal space-y-2">{value.map((item, index) => <li key={index}><OutputValue value={item} /></li>)}</ol> : <span>空数组 []</span>;
  const entries = Object.entries(value);
  return entries.length ? <dl className="space-y-2">{entries.map(([key, item]) => <div key={key} className="min-w-0 border-l border-default pl-3"><dt className="whitespace-pre-wrap break-all font-medium">{key}</dt><dd className="min-w-0"><OutputValue value={item} /></dd></div>)}</dl> : <span>空对象</span>;
}
export default function DiagnosticFacts({ data }: { data: TaskDiagnostics }) {
  return <section aria-label="脱敏诊断节选" className="min-w-0 space-y-4">
    <h3 className="text-lg font-semibold">脱敏诊断节选</h3>
    <p>Prompt 为服务端脱敏节选（最多 2000 字符），不是完整原始 Prompt。Artemis 输出由服务端限深、限项、限长并脱敏；存储的审计事实不因展示而改写。</p>
    <Facts items={[[ '诊断任务 ID', data.taskId ], ['诊断 rowVersion', data.rowVersion]]} />
    {!data.attempts.length && <p>尚无执行诊断记录。</p>}
    {data.attempts.map((attempt) => <article key={String(attempt.sequenceNo)} aria-label={`执行 ${attempt.sequenceNo} 脱敏诊断`} className="min-w-0 space-y-3 rounded-lg border border-default p-3">
      <h4 className="font-semibold">第 {attempt.sequenceNo} 次 · {attempt.kindLabel} · {attempt.status}</h4>
      <Facts items={[[ 'Artemis ID', attempt.artemisSessionId], ['开始', attempt.startedAt], ['完成', attempt.finishedAt], ['执行错误', attempt.error]]} />
      <section aria-label={`执行 ${attempt.sequenceNo} Prompt 节选`} className="space-y-1"><h5 className="font-medium">Prompt 脱敏节选</h5>{attempt.promptSnapshot === null ? <p>无 Prompt 节选。</p> : attempt.promptSnapshot === '' ? <p>空 Prompt 节选。</p> : <p className="whitespace-pre-wrap break-all">{attempt.promptSnapshot}</p>}</section>
      <section aria-label={`执行 ${attempt.sequenceNo} Artemis 输出`} className="min-w-0 space-y-1"><h5 className="font-medium">Artemis 脱敏输出</h5>{attempt.artemisOutput === null ? <p>无 Artemis 输出。</p> : <OutputValue value={attempt.artemisOutput} />}</section>
    </article>)}
  </section>;
}

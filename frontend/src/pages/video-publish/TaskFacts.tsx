import { Button } from '@heroui/react';
import type { Attempt, Detail, Task, Target } from './contracts';

export const shown = (value: string | number | boolean | null | undefined) => value == null ? '—' : String(value);
export function Facts({ items }: { items: [string, string | number | boolean | null | undefined][] }) {
  return <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[max-content_minmax(0,1fr)]">{items.map(([label, value]) => <div key={label} className="contents"><dt className="font-medium">{label}</dt><dd className="min-w-0 whitespace-pre-wrap break-all">{shown(value)}</dd></div>)}</dl>;
}
export function TargetFacts({ target }: { target: Target }) { return <Facts items={[
  ['App', target.appName], ['包名', target.appPackage], ['设备（掩码）', target.deviceSerialMasked], ['相册', target.album],
]} />; }
export function Identity({ value, label, copy }: { value: string | null; label: string; copy: (value: string) => void }) {
  return value === null ? <span>尚未创建</span> : <div className="space-y-1"><code className="block max-w-80 break-all" title={value}>{value}</code><Button size="sm" variant="secondary" aria-label={`复制${label}`} onPress={() => copy(value)}>复制{label}</Button></div>;
}
export function AttemptFacts({ attempt, label, copy }: { attempt: Attempt; label: string; copy: (value: string) => void }) {
  return <section aria-label={label} className="space-y-2 rounded-lg border border-default p-3">
    <h4 className="font-medium">第 {attempt.sequenceNo} 次执行 · {attempt.kindLabel}</h4>
    <Facts items={[
      ['执行类型', attempt.kind], ['执行状态', attempt.status], ['步骤数', attempt.stepsCount], ['失败分类', attempt.retryClassification],
      ['服务端安全重试判定', attempt.retrySafe], ['执行错误', attempt.error], ['开始', attempt.startedAt], ['完成', attempt.finishedAt],
    ]} />
    <Identity value={attempt.artemisSessionId} label={`${label} Artemis ID`} copy={copy} />
  </section>;
}
export function TaskSummary({ task }: { task: Task }) {
  return <Facts items={[
    ['任务 ID', task.taskId], ['clientRequestId', task.clientRequestId], ['rowVersion', task.rowVersion],
    ['文件名', task.filename], ['对象文件名', task.objectFilename], ['文件大小（字节）', task.sizeBytes],
    ['业务状态', task.statusLabel], ['状态代码', task.status], ['当前阶段', task.stageLabel], ['阶段代码', task.stage],
    ['运行阶段', task.operationalStage], ['队列位置（服务端）', task.queuePosition], ['入队时间', task.queuedAt],
    ['阶段开始', task.stageStartedAt], ['运行阶段开始', task.operationalStageStartedAt], ['下次清理重试', task.cleanupNextAttemptAt],
    ['执行总数', task.attemptCount], ['发布执行数', task.publishAttemptCount], ['核验执行数', task.verifyAttemptCount],
    ['最近错误码', task.lastErrorCode], ['最近错误', task.lastErrorMessage], ['创建者', task.createdBy],
    ['创建时间', task.createdAt], ['更新时间', task.updatedAt], ['任务开始', task.startedAt],
    ['可重试清理资源', task.cleanupRetryableResources.length ? task.cleanupRetryableResources.join('、') : '无'],
  ]} />;
}
export function AvailableActions({ task }: { task: Task }) {
  return <div className="space-y-1"><p className="font-medium">服务端可用操作</p>{task.allowedActions.length ? <ul className="list-inside list-disc">{task.allowedActions.map((action) => <li key={action}>{action}</li>)}</ul> : <p>无</p>}</div>;
}
export function DetailFacts({ task, copy }: { task: Detail; copy: (value: string) => void }) {
  return <div className="space-y-5">
    <section aria-label="发布内容" className="space-y-2"><h3 className="text-lg font-semibold">发布内容</h3><TaskSummary task={task} /><Facts items={[
      ['内容类型', task.contentType], ['完整文案', task.caption], ['参数锁定时间', task.paramsLockedAt],
    ]} /><Identity value={task.latestArtemisSessionId} label="最近 Artemis ID" copy={copy} /></section>
    <section aria-label="目标与进度" className="space-y-2"><h3 className="text-lg font-semibold">目标与进度</h3><TargetFacts target={task.target} />
      <ol className="list-inside list-decimal"><li>创建：{task.createdAt}</li><li>入队：{shown(task.queuedAt)}</li><li>开始：{shown(task.startedAt)}</li><li aria-current="step">服务端当前阶段：{task.stageLabel} · {task.operationalStage}</li><li>最后更新：{task.updatedAt}</li></ol>
    </section>
    <section aria-label="对象信息" className="space-y-2"><h3 className="text-lg font-semibold">对象信息</h3><Facts items={[
      ['Bucket', task.object.bucket], ['对象 Key', task.object.key], ['对象 ETag', task.object.etag], ['删除时间', task.object.deletedAt],
    ]} /><p>原视频保留，不参与设备 / spool 清理。</p></section>
    <section aria-label="人工确认审计" className="space-y-2"><h3 className="text-lg font-semibold">人工确认审计</h3>{task.confirmation ? <Facts items={[
      ['确认人', task.confirmation.confirmedBy], ['确认时间', task.confirmation.confirmedAt], ['来源状态', task.confirmation.fromStatus],
    ]} /> : <p>{task.status === 'succeeded' ? '历史自动成功：没有人工确认记录。' : '暂无人工确认记录。'}</p>}</section>
    <section aria-label="执行历史" className="space-y-3"><h3 className="text-lg font-semibold">执行历史（服务端顺序）</h3>
      {task.attempts.length ? task.attempts.map((attempt) => <AttemptFacts key={String(attempt.sequenceNo)} attempt={attempt} label={`执行 ${attempt.sequenceNo}`} copy={copy} />) : <p>尚未创建执行记录。</p>}
    </section>
    {task.relatedPublishAttempt && <AttemptFacts attempt={task.relatedPublishAttempt} label="关联发布执行" copy={copy} />}
    <section aria-label="资源清理" className="space-y-2"><h3 className="text-lg font-semibold">资源清理</h3><Facts items={[
      ['设备清理状态', task.cleanup.device.status], ['设备清理错误', task.cleanup.device.error], ['spool 清理状态', task.cleanup.spool.status], ['spool 清理错误', task.cleanup.spool.error],
    ]} /></section>
    <AvailableActions task={task} />
  </div>;
}

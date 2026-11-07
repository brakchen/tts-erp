export interface Config {
  credentialRef: string; credentialConfigured: boolean; workflowId: string; workflowVersionId: string | null;
  instanceType: string; concurrency: number;
  dispatchPausedReason?: string | null;
}
export interface Template { id: string; name: string; humanId: string; humanName: string; clips: { assetId: string; filename: string }[] }
export interface Batch {
  id: string; mode: string; status: string; error: string | null; total: number; counts: Record<string, number>;
  groups: { path: string; total: number; succeeded: number }[]; costs: Record<string, string>; unknownCostTasks: number;
}
export interface Attempt {
  id: string; number: number; taskId: string | null; cloudStatus: string | null; reserved: boolean;
  error: string | null; usage: Record<string, unknown> | null; receipt: unknown;
}
export interface Job {
  id: string; batchId: string; status: string; error: string | null; seed: number;
  identity: { skuId: string; originalFilename: string; humanName: string; templateName: string; clip: number; archivePath: string };
  attempts?: Attempt[];
}
export interface Artifact { id: string; kind: string; url: string; spec: Record<string, unknown> }
export const statuses: Record<string, string> = {
  PLANNED: '待启动', ACTIVE: '派发中', PAUSED: '已暂停', PREPARING: '准备素材', SUBMITTING: '提交中',
  QUEUED: '云端排队', RUNNING: '云端生成', DOWNLOADING: '下载中', FINALIZING: '成片处理中',
  SUCCEEDED: '已完成', FAILED: '生成失败', SUBMISSION_UNKNOWN: '待管理员核对', TRACKING_INTERRUPTED: '跟踪中断',
  DOWNLOAD_FAILED: '下载失败', FINALIZE_FAILED: '成片处理失败', PREPARATION_FAILED: '素材准备失败',
  TERMINATING: '终止中', TERMINATED: '已终止',
};
export const canTerminateBatch = (batch: Batch) => ['PLANNED', 'ACTIVE', 'PAUSED'].includes(batch.status);

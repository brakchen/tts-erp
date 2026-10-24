// Video publish wire fields are camelCase. State, eligibility, ordering and actions belong to the API.
export type Count = number | string;
export interface Target { appName: string; appPackage: string; deviceSerialMasked: string; album: string }
export interface Attempt {
  sequenceNo: Count; kind: string; kindLabel: string; artemisSessionId: string; status: string;
  stepsCount: Count | null; retryClassification: string | null; retrySafe: boolean | null;
  error: string | null; startedAt: string | null; finishedAt: string | null;
}
export interface Task {
  taskId: string; clientRequestId: string; rowVersion: Count; filename: string; objectFilename: string; sizeBytes: Count;
  caption?: string; captionPreview: string; status: string; statusLabel: string; stage: string; stageLabel: string;
  queuedAt: string | null; queuePosition: Count | null; operationalStage: string; stageStartedAt: string | null;
  operationalStageStartedAt: string | null; cleanupNextAttemptAt: string | null; latestArtemisSessionId: string | null;
  attemptCount: Count; publishAttemptCount: Count; verifyAttemptCount: Count; lastErrorCode: string | null;
  lastErrorMessage: string | null; createdAt: string; updatedAt: string; createdBy: string; startedAt: string | null;
  allowedActions: string[]; cleanupRetryableResources: string[]; currentAttempt?: Attempt | null; relatedPublishAttempt?: Attempt;
  deviceSerialMasked?: string;
}
export interface Detail extends Task {
  contentType: string; caption: string; paramsLockedAt: string | null; target: Target;
  confirmation: { confirmedBy: string; confirmedAt: string; fromStatus: string } | null;
  object: { bucket: string; key: string; etag: string | null; deletedAt: string | null };
  attempts: Attempt[]; cleanup: { device: { status: string; error: string | null }; spool: { status: string; error: string | null } };
}
interface PollState { running: boolean; cleaning: boolean; queued: boolean }
export interface Current { tasks: Task[]; pollState: PollState; suggestedPollSeconds: number; serverTime: string }
export interface TaskList { items: Task[]; pollState: PollState; nextCursor: string | null; totalApprox: Count; serverTime: string }
export interface Config {
  acceptedContentTypes: string[]; acceptedExtensions: string[]; maxVideoBytes: Count; maxCaptionCharacters: Count; uploadUrlTtlSeconds: Count;
  target: Target; worker: { status: string; lastHeartbeatAt: string | null }; device: { status: string; message: string };
  artemis: { profile: string; verificationLevel: string }; workerTiming: { pollSeconds: number; leaseSeconds: number; heartbeatSeconds: number };
  canWrite: boolean; writeBlockReason: string | null; serverTime: string;
}
function invalid(): never { throw new Error('视频发布响应格式错误'); }
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function text(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function nullableText(value: unknown): string | null { return value === null ? null : text(value); }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid(); }
function count(value: unknown): Count { return (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) || (typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value)) ? value : invalid(); }
function positive(value: unknown): number { return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : invalid(); }
function array<T>(value: unknown, decode: (item: unknown) => T): T[] { return Array.isArray(value) ? value.map(decode) : invalid(); }
function uuid(value: unknown): string { const result = text(value); return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result) ? result : invalid(); }
function target(value: unknown): Target { const row = object(value); return { appName: text(row.appName), appPackage: text(row.appPackage), deviceSerialMasked: text(row.deviceSerialMasked), album: text(row.album) }; }
function poll(value: unknown): PollState { const row = object(value); return { running: bool(row.running), cleaning: bool(row.cleaning), queued: bool(row.queued) }; }
function attempt(value: unknown): Attempt {
  const row = object(value);
  return { sequenceNo: count(row.sequenceNo), kind: text(row.kind), kindLabel: text(row.kindLabel), artemisSessionId: uuid(row.artemisSessionId), status: text(row.status),
    stepsCount: row.stepsCount === null ? null : count(row.stepsCount), retryClassification: nullableText(row.retryClassification), retrySafe: row.retrySafe === null ? null : bool(row.retrySafe),
    error: nullableText(row.error), startedAt: nullableText(row.startedAt), finishedAt: nullableText(row.finishedAt) };
}
export function decodeTask(value: unknown): Task {
  const row = object(value);
  return { taskId: uuid(row.taskId), clientRequestId: uuid(row.clientRequestId), rowVersion: count(row.rowVersion), filename: text(row.filename), objectFilename: text(row.objectFilename), sizeBytes: count(row.sizeBytes),
    ...(row.caption !== undefined ? { caption: text(row.caption) } : {}), captionPreview: text(row.captionPreview), status: text(row.status), statusLabel: text(row.statusLabel), stage: text(row.stage), stageLabel: text(row.stageLabel),
    queuedAt: nullableText(row.queuedAt), queuePosition: row.queuePosition === null ? null : count(row.queuePosition), operationalStage: text(row.operationalStage), stageStartedAt: nullableText(row.stageStartedAt),
    operationalStageStartedAt: nullableText(row.operationalStageStartedAt), cleanupNextAttemptAt: nullableText(row.cleanupNextAttemptAt), latestArtemisSessionId: row.latestArtemisSessionId === null ? null : uuid(row.latestArtemisSessionId),
    attemptCount: count(row.attemptCount), publishAttemptCount: count(row.publishAttemptCount), verifyAttemptCount: count(row.verifyAttemptCount), lastErrorCode: nullableText(row.lastErrorCode), lastErrorMessage: nullableText(row.lastErrorMessage),
    createdAt: text(row.createdAt), updatedAt: text(row.updatedAt), createdBy: text(row.createdBy), startedAt: nullableText(row.startedAt),
    allowedActions: array(row.allowedActions, text), cleanupRetryableResources: array(row.cleanupRetryableResources, text),
    ...(row.currentAttempt !== undefined ? { currentAttempt: row.currentAttempt === null ? null : attempt(row.currentAttempt) } : {}),
    ...(row.relatedPublishAttempt !== undefined ? { relatedPublishAttempt: attempt(row.relatedPublishAttempt) } : {}),
    ...(row.deviceSerialMasked !== undefined ? { deviceSerialMasked: text(row.deviceSerialMasked) } : {}) };
}
export function decodeDetail(value: unknown): Detail {
  const row = object(value); const obj = object(row.object); const cleanup = object(row.cleanup);
  const resource = (v: unknown) => { const r = object(v); return { status: text(r.status), error: nullableText(r.error) }; };
  const confirmation = row.confirmation === null ? null : object(row.confirmation);
  return { ...decodeTask(value), contentType: text(row.contentType), caption: text(row.caption), paramsLockedAt: nullableText(row.paramsLockedAt), target: target(row.target),
    confirmation: confirmation && { confirmedBy: text(confirmation.confirmedBy), confirmedAt: text(confirmation.confirmedAt), fromStatus: text(confirmation.fromStatus) },
    object: { bucket: text(obj.bucket), key: text(obj.key), etag: nullableText(obj.etag), deletedAt: nullableText(obj.deletedAt) },
    attempts: array(row.attempts, attempt), cleanup: { device: resource(cleanup.device), spool: resource(cleanup.spool) } };
}
export type DiagnosticValue = string | number | boolean | null | DiagnosticValue[] | { [key: string]: DiagnosticValue };
export interface DiagnosticAttempt extends Attempt { promptSnapshot: string | null; artemisOutput: { [key: string]: DiagnosticValue } | null }
export interface TaskDiagnostics { taskId: string; rowVersion: Count; attempts: DiagnosticAttempt[] }
function diagnosticValue(value: unknown): DiagnosticValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : invalid();
  if (Array.isArray(value)) return value.map(diagnosticValue);
  return Object.fromEntries(Object.entries(object(value)).map(([key, item]) => [key, diagnosticValue(item)]));
}
/** Select only the server-redacted diagnostic projection, never cache the complete task body here. */
export function decodeTaskDiagnostics(value: unknown, expectedTaskId: string): TaskDiagnostics {
  const row = object(value); const taskId = uuid(row.taskId);
  if (taskId !== expectedTaskId) return invalid();
  return { taskId, rowVersion: count(row.rowVersion), attempts: array(row.attempts, (value) => {
    const item = object(value); const promptSnapshot = nullableText(item.promptSnapshot);
    if (promptSnapshot !== null && Array.from(promptSnapshot).length > 2000) return invalid();
    const artemisOutput = item.artemisOutput === null ? null : Object.fromEntries(Object.entries(object(item.artemisOutput)).map(([key, child]) => [key, diagnosticValue(child)]));
    return { ...attempt(item), promptSnapshot, artemisOutput };
  }) };
}
export function decodeCurrent(value: unknown): Current { const row = object(value); return { tasks: array(row.tasks, decodeTask), pollState: poll(row.pollState), suggestedPollSeconds: positive(row.suggestedPollSeconds), serverTime: text(row.serverTime) }; }
export function decodeList(value: unknown): TaskList { const row = object(value); return { items: array(row.items, decodeTask), pollState: poll(row.pollState), nextCursor: nullableText(row.nextCursor), totalApprox: count(row.totalApprox), serverTime: text(row.serverTime) }; }
export function decodeConfig(value: unknown): Config {
  const row = object(value); const worker = object(row.worker); const device = object(row.device); const artemis = object(row.artemis); const timing = object(row.workerTiming);
  return { acceptedContentTypes: array(row.acceptedContentTypes, text), acceptedExtensions: array(row.acceptedExtensions, text), maxVideoBytes: count(row.maxVideoBytes), maxCaptionCharacters: count(row.maxCaptionCharacters), uploadUrlTtlSeconds: count(row.uploadUrlTtlSeconds),
    target: target(row.target), worker: { status: text(worker.status), lastHeartbeatAt: nullableText(worker.lastHeartbeatAt) }, device: { status: text(device.status), message: text(device.message) },
    artemis: { profile: text(artemis.profile), verificationLevel: text(artemis.verificationLevel) }, workerTiming: { pollSeconds: positive(timing.pollSeconds), leaseSeconds: positive(timing.leaseSeconds), heartbeatSeconds: positive(timing.heartbeatSeconds) },
    canWrite: bool(row.canWrite), writeBlockReason: nullableText(row.writeBlockReason), serverTime: text(row.serverTime) };
}

export interface UploadTicket { task: Task; idempotentReplay: boolean; upload: { url: string; expiresAt: string | null } | null }
export function decodeTicket(value: unknown): UploadTicket {
  const row = object(value); const upload = object(row.upload); const headers = object(upload.headers);
  const task = decodeTask(row); const replay = row.idempotentReplay === undefined ? false : bool(row.idempotentReplay);
  const url = text(upload.url);
  if (replay && task.stage !== 'awaiting_upload' && url === '') return { task, idempotentReplay: replay, upload: null };
  const parsed = new URL(url);
  if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password || upload.method !== 'PUT'
      || Object.keys(headers).length !== 1 || headers['Content-Type'] !== 'video/mp4') return invalid();
  return { task, idempotentReplay: replay, upload: { url, expiresAt: nullableText(upload.expiresAt) } };
}
export interface Device { serial: string; serialMasked: string; model: string; product: string; state: string; isBusy: boolean; isEmulator: boolean }
export function decodeDevices(value: unknown): Device[] {
  const row = object(value); return array(row.devices, (v) => { const d = object(v); return { serial: text(d.serial), serialMasked: text(d.serialMasked), model: text(d.model), product: text(d.product), state: text(d.state), isBusy: bool(d.isBusy), isEmulator: bool(d.isEmulator) }; });
}

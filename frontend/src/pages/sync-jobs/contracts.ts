export interface JobStatus {
  job_name: string; interval_seconds: number | null; last_run_at: string | null;
  last_finished_at: string | null; last_status: string | null; last_error: string | null;
  next_expected_at: string | null; lag_seconds: number | null; cycles_late: number | null; severity: string;
}
export interface Job {
  job_name: string; module_path: string; entrypoint: string; interval_seconds: number;
  is_tiktok: boolean; enabled: boolean; scope_type: string; last_status: JobStatus;
}
export interface JobsData {
  server_time: string; jobs: Job[];
  tiktok_shops: { shop_pk: number; shop_id: string; account_name: string | null; status: string | null }[];
}
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string';
const nullableText = (v: unknown) => v === null || text(v);
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || number(v);
const time = (v: unknown) => text(v) && Number.isFinite(Date.parse(v));
const nullableTime = (v: unknown) => v === null || time(v);
const invalid = () => new Error('任务列表不可用，请明确刷新重试');
export function decodeJobs(value: unknown): JobsData {
  if (!record(value) || !time(value.server_time) || !Array.isArray(value.jobs) || !Array.isArray(value.tiktok_shops)) throw invalid();
  const names = new Set<string>();
  for (const j of value.jobs) {
    if (!record(j) || !text(j.job_name) || !j.job_name || names.has(j.job_name)
      || !text(j.module_path) || !text(j.entrypoint) || !number(j.interval_seconds) || !Number.isInteger(j.interval_seconds) || j.interval_seconds <= 0
      || typeof j.is_tiktok !== 'boolean' || typeof j.enabled !== 'boolean' || !text(j.scope_type) || !record(j.last_status)) throw invalid();
    names.add(j.job_name);
    const s = j.last_status;
    if (s.job_name !== j.job_name || !nullableNumber(s.interval_seconds) || !nullableTime(s.last_run_at)
      || !nullableTime(s.last_finished_at) || !nullableText(s.last_status) || !nullableText(s.last_error)
      || !nullableTime(s.next_expected_at) || !nullableNumber(s.lag_seconds) || !nullableNumber(s.cycles_late)
      || !text(s.severity) || !['ok', 'warn', 'crit', 'unknown'].includes(s.severity)) throw invalid();
  }
  const shops = new Set<string>();
  for (const s of value.tiktok_shops) {
    if (!record(s) || !number(s.shop_pk) || !Number.isSafeInteger(s.shop_pk) || !text(s.shop_id) || !s.shop_id
      || shops.has(s.shop_id) || !nullableText(s.account_name) || !nullableText(s.status)) throw invalid();
    shops.add(s.shop_id);
  }
  // SAFETY: every required wire field and nested record was checked above; extra fields are ignored.
  return value as unknown as JobsData;
}
export type Attempt = { job_name: string; kind: 'enabled'; enabled: boolean } | { job_name: string; kind: 'trigger'; shop_id: string | null };
export function decodeAcknowledgement(value: unknown, attempt: Attempt): void {
  if (!record(value) || value.job_name !== attempt.job_name) throw invalid();
  if (attempt.kind === 'enabled') {
    if (value.enabled !== attempt.enabled || !time(value.updated_at)) throw invalid();
  } else if (value.accepted !== true || value.shop_id !== attempt.shop_id || !time(value.accepted_at) || !text(value.message)) throw invalid();
}

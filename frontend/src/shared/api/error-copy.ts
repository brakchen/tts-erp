import { ApiError } from './client';
import { isRequestCancelledError, isRequestTimeoutError } from './request-lifetime';

export type ErrorCopySurface = 'default' | 'login';

/** Single-table business copy. Page-local maps should read from here (P1). */
export const BUSINESS_ERROR_COPY: Record<string, string> = {
  ASSET_IN_USE: '此素材仍被引用。请先解除全部引用，再从素材库删除。',
  ASSET_NOT_READY: '此资产还没有完成入库，暂时无法添加。',
  PORTRAIT_NOT_READY: '形象照片尚未就绪，请重新选择照片。',
  PACK_REQUIRES_READY_VIDEOS: '素材包只接收已入库的视频。请重新选择。',
  FX_RATE_UNAVAILABLE: '汇率数据缺失，无法计算结果',
  PRICE_FX_UNAVAILABLE: '价格币种缺少本次快照可用的汇率，无法计算结果',
  SHOP_REGION_REQUIRED: 'TikTok 店铺地区未配置，无法开始同步。请先在 TTS-ERP 店铺账号中填写国家/地区代码（如 VN）。',
  UPLOAD_NOT_FOUND: '未找到上传对象，请核对后明确重试上传',
  SIZE_MISMATCH: '文件大小校验未通过，请重新传输。',
  UPLOAD_SIZE_MISMATCH: '上传对象大小与原文件不一致，请选择原文件后重新上传',
  UPLOAD_MIME_MISSING: '上传对象缺少视频类型，请重新上传原 MP4',
  UPLOAD_MIME_MISMATCH: '上传对象类型与 MP4 声明不一致，请重新选择原文件',
  UPLOAD_ETAG_MISSING: '上传对象缺少 ETag，未入队；请稍后核对后明确重试确认',
  VIDEO_TOO_LARGE: '视频超过服务端上限，请选择较小的 MP4',
  INVALID_VIDEO_TYPE: '视频类型必须为 video/mp4 且扩展名为 .mp4',
  CAPTION_REQUIRED: '发布文案不能为空',
  CAPTION_TOO_LONG: '发布文案超过服务端上限',
  IDEMPOTENCY_PAYLOAD_MISMATCH: '原幂等键的请求内容不同，请先核对原任务，不要重复新建',
  TASK_VERSION_CONFLICT: '任务版本已变化，请查询当前事实后重新决定',
  ROW_VERSION_CONFLICT: '任务版本已变化，请查询当前事实后重新决定',
  TASK_ALREADY_SUBMITTED: '任务已提交给发布端，不能取消或修改参数',
  TASK_SUBMITTING: '任务正在提交，请查询当前事实后再决定',
  TASK_BUSY: '任务正在执行，请查询当前事实后再决定',
  CLEANUP_REQUIRED: '设备或本地暂存清理失败，请先核对并重试清理',
  UPLOAD_REPLACEMENT_REQUIRED: '原对象不可用，请核对服务端提供的重新上传操作',
  PROMPT_INVALID: '正文 Prompt 含不支持的字符',
  PROMPT_TOO_LONG: '正文 Prompt 超过服务端长度上限',
  DEVICE_SERIAL_INVALID: '设备序列号格式无效',
  DEVICE_NOT_CONFIGURED: '发布设备未配置',
  APP_PACKAGE_INVALID: 'App 包名格式无效',
  PUBLISH_WORKER_UNAVAILABLE: '发布 Worker 不可用，请稍后核对后手动重试',
  OBJECT_STORE_UNAVAILABLE: '对象存储不可用，原操作结果需核对后再决定',
  runtime_schema_invalid: '配置内容不符合校验规则，请展开高级信息核对字段类型、必填项和取值范围。',
  runtime_business_invalid: '配置未通过业务规则校验，请核对业务字段和允许范围。',
  runtime_secret_unavailable: '引用的凭证不存在、已归档或引用格式无效。请在凭证管理中保存或恢复凭证，再检查 secret:// 名称。',
  runtime_source_rollout_invalid: '此历史版本带有已退役的灰度数据，无法作为恢复来源。请选择其他历史版本。',
  runtime_number_invalid: '配置包含无法存储的数字，请修正溢出或非有限数值后重新检查。',
};

/** Known English transport details that already have page-local Chinese copy. */
const KNOWN_DETAIL_COPY: Record<string, string> = {
  'candidate matches current published configuration': '当前发布内容已与本次内容一致，无需重复发布，请刷新核对。',
  'runtime config is retired; restore it first': '配置已停用，请先返回概览重新启用。',
};

const VERSION_CONFLICT = /^((draft|published) version conflict; reload before (saving|publishing|rolling back))$/;

const TECHNICAL_DETAIL = [
  /auth store unavailable/i,
  /\bOperationalError\b/,
  /too many login attempts/i,
  /rate limit exceeded/i,
  /requires page:/i,
  /requires readwrite/i,
  /Traceback \(most recent call last\)/i,
  /request id/i,
  /\bx-request-id\b/i,
];

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function hasCjk(text: string): boolean {
  return /[\u3400-\u9fff]/.test(text);
}

export function isTechnicalDetail(text: string): boolean {
  return TECHNICAL_DETAIL.some((pattern) => pattern.test(text));
}

/** PRD 5.3: single-line Chinese, <=80 chars, not internal/technical. */
export function isUserFacingDetail(detail: unknown): detail is string {
  if (typeof detail !== 'string' || !detail || detail.includes('\n') || detail.includes('\r')) return false;
  if (detail.length > 80 || !hasCjk(detail) || isTechnicalDetail(detail)) return false;
  return true;
}

export function isNetworkError(error: unknown): boolean {
  if (error instanceof ApiError || isRequestTimeoutError(error) || isRequestCancelledError(error)) return false;
  if (error instanceof Error && (error.name === 'AbortError' || error.name === 'TimeoutError')) return false;
  if (error instanceof TypeError || (error instanceof Error && error.name === 'TypeError')) return true;
  return error instanceof Error && /failed to fetch|networkerror|load failed|network request failed|fetch failed/i.test(error.message);
}

function httpCopy(status: number, retryAfter: number, surface: ErrorCopySurface): string {
  if (surface === 'login') {
    if (status === 401) return '用户名或密码错误';
    if (status === 429) return retryAfter > 0 ? `登录过于频繁，请 ${retryAfter} 秒后再试` : '登录过于频繁，请稍后再试';
    if (status >= 500) return '登录服务暂不可用，请稍后重试';
  }
  if (status === 400) return '请求未通过检查，请核对后重试';
  if (status === 401) return '登录已失效，请重新登录';
  if (status === 403) return '没有权限执行此操作';
  if (status === 404) return '要找的内容不存在，请刷新后再试';
  if (status === 409) return '数据已变化，请刷新后确认再提交';
  if (status === 422) return '请检查标红的字段';
  if (status === 429) return retryAfter > 0 ? `操作太频繁，请在 ${retryAfter} 秒后重试` : '操作太频繁，请稍后再试';
  if (status === 502 || status === 504) return '服务暂时无响应，请稍后重试';
  if (status === 503) return '服务暂不可用，请稍后重试';
  return '服务出错了，请稍后重试';
}

export function resolveBusinessCode(error: ApiError): string | undefined {
  if (error.code && Object.hasOwn(BUSINESS_ERROR_COPY, error.code)) return error.code;
  if (record(error.detail) && typeof error.detail.code === 'string' && Object.hasOwn(BUSINESS_ERROR_COPY, error.detail.code)) {
    return error.detail.code;
  }
  if (typeof error.detail === 'string' && Object.hasOwn(BUSINESS_ERROR_COPY, error.detail)) return error.detail;
  return undefined;
}

export function errorCopy(error: unknown, surface: ErrorCopySurface = 'default'): string {
  if (isRequestTimeoutError(error)) return surface === 'login' ? '请求超时，请稍后重试' : '等待超时，请稍后重试';
  if (isNetworkError(error)) return '网络已断开，请检查网络后重试';
  if (!(error instanceof ApiError)) return surface === 'login' ? '登录服务暂不可用，请稍后重试' : '服务出错了，请稍后重试';
  const code = resolveBusinessCode(error);
  if (code) return BUSINESS_ERROR_COPY[code];
  if (typeof error.detail === 'string' && Object.hasOwn(KNOWN_DETAIL_COPY, error.detail)) return KNOWN_DETAIL_COPY[error.detail];
  if (typeof error.detail === 'string' && VERSION_CONFLICT.test(error.detail)) {
    return '版本已变化，你的输入已保留。请查看最新内容后重新检查。';
  }
  if (isUserFacingDetail(error.detail)) return error.detail;
  return httpCopy(error.status, error.retryAfter ?? 0, surface);
}

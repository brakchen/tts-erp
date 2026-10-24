import { ApiError } from './client';

export interface CommandFailure {
  message: string;
  fieldErrors: Record<string, string>;
  uncertain: boolean;
  retryAfter: number;
}
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const safeTypes: Record<string, string> = {
  missing: '请填写此项', string_too_short: '内容过短', string_too_long: '内容过长',
  int_parsing: '请输入整数', greater_than_equal: '数值低于允许范围',
  date_from_datetime_parsing: '请输入有效日期', value_error: '格式不符合要求',
};

/** Transport detail is never a display string. Select only known fields and static local messages. */
export function commandFailure(error: unknown, labels: Record<string, string>): CommandFailure {
  const fieldErrors: Record<string, string> = {};
  const status = error instanceof ApiError ? error.status : undefined;
  const retryAfter = error instanceof ApiError ? (error.retryAfter ?? 0) : 0;
  const detail = error instanceof ApiError ? error.detail : undefined;
  const put = (field: string, text: string) => { if (Object.hasOwn(labels, field)) fieldErrors[field] = `${labels[field]}：${text}`; };
  if (status === 422 && Array.isArray(detail)) {
    for (const item of detail) {
      if (!record(item) || !Array.isArray(item.loc) || item.loc.length !== 2 || item.loc[0] !== 'body' || typeof item.loc[1] !== 'string') continue;
      const text = typeof item.type === 'string' && Object.hasOwn(safeTypes, item.type) ? safeTypes[item.type] : '请检查此项';
      put(item.loc[1], text);
    }
  }
  if (status === 422 && detail === 'app_key and app_secret must be provided together') {
    put('app_key', '必须与 App Secret 成对填写'); put('app_secret', '必须与 App Key 成对填写');
  } else if (status === 422 && detail === 'service_id is required with app_key/app_secret') {
    put('service_id', '配置 App 凭证时必须填写');
  } else if (status === 422 && detail === 'app_secret length is invalid') {
    put('app_secret', '长度不符合要求');
  }
  const known = status !== undefined && [400, 401, 403, 404, 409, 422, 429].includes(status);
  const descriptions: Record<number, string> = {
    400: '请求未通过检查，请核对输入', 401: '登录已失效，请重新登录',
    403: '权限不足或安全检查未通过', 404: '记录不存在，请刷新核对',
    409: '数据冲突，请查询核对后再提交', 422: '校验未通过，请检查输入',
    429: '请求过于频繁，请稍后手动重试',
  };
  return { message: status !== undefined && known ? `${descriptions[status]}（HTTP ${status}）` : '提交结果未确认，请先查询核对；不要重复提交', fieldErrors, uncertain: !known, retryAfter };
}

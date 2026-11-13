import { ApiError } from './client';
import { errorCopy, type ErrorCopySurface } from './error-copy';

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
export function commandFailure(error: unknown, labels: Record<string, string>, surface: ErrorCopySurface = 'default'): CommandFailure {
  const fieldErrors: Record<string, string> = {};
  const status = error instanceof ApiError ? error.status : undefined;
  const retryAfter = error instanceof ApiError ? (error.retryAfter ?? 0) : 0;
  const detail = error instanceof ApiError ? error.detail : undefined;
  const missingShopApp = status === 409 && detail === "configure this shop's App Key and App Secret first";
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
  if (missingShopApp) {
    put('app_key', '此 Service ID 尚未配置，请填写');
    put('app_secret', '请与 App Key 成对填写');
  }
  return {
    message: missingShopApp
      ? '当前店铺的此 Service ID 尚未配置，请填写 App Key 和 App Secret 后保存'
      : errorCopy(error, surface),
    fieldErrors,
    uncertain: false,
    retryAfter,
  };
}

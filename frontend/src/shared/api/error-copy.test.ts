import { describe, expect, it } from 'vitest';
import { ApiError } from './client';
import { commandFailure } from './command-errors';
import {
  BUSINESS_ERROR_COPY, errorCopy, isTechnicalDetail, isUserFacingDetail, resolveBusinessCode,
} from './error-copy';
import { RequestCancelledError, RequestTimeoutError } from './request-lifetime';

const labels = { shop_id: '店铺 ID', service_id: 'Service ID', app_key: 'App Key', app_secret: 'App Secret', username: '用户名', password: '密码' };

describe('error-copy / commandFailure mapping', () => {
  it.each([
    [400, '请求未通过检查，请核对后重试'],
    [403, '没有权限执行此操作'],
    [404, '要找的内容不存在，请刷新后再试'],
    [409, '数据已变化，请刷新后确认再提交'],
    [422, '请检查标红的字段'],
    [500, '服务出错了，请稍后重试'],
    [501, '服务出错了，请稍后重试'],
    [503, '服务暂不可用，请稍后重试'],
    [502, '服务暂时无响应，请稍后重试'],
    [504, '服务暂时无响应，请稍后重试'],
  ])('maps HTTP %s without a status suffix', (status, message) => {
    const result = commandFailure(new ApiError('HTTP ' + status, status, undefined, undefined, 'TEST_SECRET'), labels);
    expect(result.uncertain).toBe(false);
    expect(result.message).toBe(message);
    expect(result.message).not.toMatch(/HTTP/);
    expect(JSON.stringify(result)).not.toContain('TEST_SECRET');
  });

  it('maps 429 with and without Retry-After', () => {
    const withWait = commandFailure(new ApiError('HTTP 429', 429, 12), labels);
    expect(withWait).toMatchObject({ uncertain: false, retryAfter: 12, message: '操作太频繁，请在 12 秒后重试' });
    const without = commandFailure(new ApiError('HTTP 429', 429), labels);
    expect(without).toMatchObject({ uncertain: false, retryAfter: 0, message: '操作太频繁，请稍后再试' });
  });

  it('prefers a known business code over HTTP defaults and detail text', () => {
    const error = new ApiError('HTTP 409', 409, undefined, undefined, '此素材仍被引用的原文不应优先', 'ASSET_IN_USE');
    expect(resolveBusinessCode(error)).toBe('ASSET_IN_USE');
    const result = commandFailure(error, labels);
    expect(result.uncertain).toBe(false);
    expect(result.message).toBe(BUSINESS_ERROR_COPY.ASSET_IN_USE);
    expect(result.message).not.toContain('HTTP');
  });

  it('reads known non-standard codes from detail.code', () => {
    const error = new ApiError('HTTP 422', 422, undefined, undefined, { code: 'runtime_schema_invalid', message: 'TEST_SECRET' });
    const result = commandFailure(error, labels);
    expect(result.message).toBe(BUSINESS_ERROR_COPY.runtime_schema_invalid);
    expect(JSON.stringify(result)).not.toContain('TEST_SECRET');
  });

  it('maps a known code stored as a detail string', () => {
    const result = commandFailure(new ApiError('HTTP 409', 409, undefined, undefined, 'UPLOAD_NOT_FOUND'), labels);
    expect(result.message).toBe(BUSINESS_ERROR_COPY.UPLOAD_NOT_FOUND);
  });

  it('discards technical detail and uses the HTTP default', () => {
    expect(isTechnicalDetail('auth store unavailable: OperationalError')).toBe(true);
    expect(isTechnicalDetail('too many login attempts')).toBe(true);
    expect(isTechnicalDetail('requires page:users')).toBe(true);
    expect(isUserFacingDetail('auth store unavailable: OperationalError')).toBe(false);
    const result = commandFailure(new ApiError('HTTP 503', 503, undefined, undefined, 'auth store unavailable: OperationalError'), labels);
    expect(result.message).toBe('服务暂不可用，请稍后重试');
    expect(result.message).not.toContain('OperationalError');
    expect(result.message).not.toContain('HTTP');
  });

  it('uses a short single-line Chinese detail in place of the default', () => {
    const detail = '用户名或密码错误';
    expect(isUserFacingDetail(detail)).toBe(true);
    const result = commandFailure(new ApiError('HTTP 401', 401, undefined, undefined, detail), labels);
    expect(result.message).toBe(detail);
    expect(result.uncertain).toBe(false);
  });

  it('does not use a long, multiline, or English detail as display copy', () => {
    expect(isUserFacingDetail('短句\n第二行')).toBe(false);
    expect(isUserFacingDetail('这是一段超过八十个字的说明，'.repeat(6))).toBe(false);
    expect(isUserFacingDetail('configure this shop first')).toBe(false);
    const result = commandFailure(new ApiError('HTTP 500', 500, undefined, undefined, 'internal failure: TEST_SECRET'), labels);
    expect(result.message).toBe('服务出错了，请稍后重试');
    expect(JSON.stringify(result)).not.toContain('TEST_SECRET');
  });

  it('distinguishes timeout, network, and other non-HTTP failures; all are certain', () => {
    const timeout = commandFailure(new RequestTimeoutError(), labels);
    expect(timeout).toMatchObject({ uncertain: false, message: '等待超时，请稍后重试' });
    const network = commandFailure(new TypeError('Failed to fetch'), labels);
    expect(network).toMatchObject({ uncertain: false, message: '网络已断开，请检查网络后重试' });
    const cancelled = commandFailure(new RequestCancelledError(), labels);
    expect(cancelled.uncertain).toBe(false);
    expect(cancelled.message).toBe('服务出错了，请稍后重试');
    expect(errorCopy(new RequestTimeoutError(), 'login')).toBe('请求超时，请稍后重试');
    expect(errorCopy(new TypeError('Failed to fetch'), 'login')).toBe('网络已断开，请检查网络后重试');
    expect(errorCopy(new ApiError('HTTP 503', 503, undefined, undefined, 'auth store unavailable: OperationalError'), 'login')).toBe('登录服务暂不可用，请稍后重试');
    expect(errorCopy(new ApiError('HTTP 429', 429, 8, undefined, 'too many login attempts'), 'login')).toBe('登录过于频繁，请 8 秒后再试');
    expect(errorCopy(new ApiError('HTTP 429', 429, 0, undefined, 'too many login attempts'), 'login')).toBe('登录过于频繁，请稍后再试');
    expect(errorCopy(new ApiError('HTTP 401', 401), 'login')).toBe('用户名或密码错误');
    expect(errorCopy(new Error('malformed'), 'login')).toBe('登录服务暂不可用，请稍后重试');
  });

  it('keeps shop App credential English details off the banner and on known fields only', () => {
    const together = commandFailure(new ApiError('HTTP 422', 422, undefined, undefined, 'app_key and app_secret must be provided together'), labels);
    expect(together.message).toBe('请检查标红的字段');
    expect(together.message).not.toContain('app_key');
    expect(together.fieldErrors.app_key).toBeTruthy();
    const missing = commandFailure(new ApiError('HTTP 409', 409, undefined, undefined, "configure this shop's App Key and App Secret first"), labels);
    expect(missing.message).toBe('当前店铺的此 Service ID 尚未配置，请填写 App Key 和 App Secret 后保存');
    expect(missing.uncertain).toBe(false);
  });
});

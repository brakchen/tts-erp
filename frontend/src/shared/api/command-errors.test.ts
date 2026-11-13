import { describe, expect, it } from 'vitest';
import { ApiError } from './client';
const modulePath = './command-errors';
const labels = { shop_id: '店铺 ID', service_id: 'Service ID', app_key: 'App Key', app_secret: 'App Secret', label_zh: '中文标签' };

describe('command error boundary shared by shops and enum', () => {
  it('selects only known field paths/types, never echoes raw message/input/context or prototype paths', async () => {
    const { commandFailure } = await import(modulePath);
    const detail = [
      { loc: ['body', 'shop_id'], type: 'value_error', msg: 'TEST_SECRET in message', input: 'TEST_SECRET', ctx: { error: 'TEST_SECRET' } },
      { loc: ['body', 'app_secret'], type: 'string_too_long', msg: 'TEST_SECRET' },
      { loc: ['body', 'constructor'], type: 'missing', msg: 'TEST_SECRET' },
      { loc: ['body', 'unknown'], type: 'missing' },
    ];
    const result = commandFailure(new ApiError('HTTP 422', 422, undefined, undefined, detail), labels);
    expect(result.uncertain).toBe(false);
    expect(result.message).toBe('请检查标红的字段');
    expect(result.message).not.toContain('HTTP');
    expect(Object.keys(result.fieldErrors)).toEqual(['shop_id', 'app_secret']);
    expect(result.fieldErrors.shop_id).toContain('店铺 ID'); expect(result.fieldErrors.app_secret).toContain('App Secret');
    expect(JSON.stringify(result)).not.toContain('TEST_SECRET'); expect(JSON.stringify(result)).not.toContain('constructor');
  });
  it.each([new TypeError('TEST_SECRET network'), new Error('TEST_SECRET malformed success'), new ApiError('TEST_SECRET upstream', 500, undefined, undefined, { input: 'TEST_SECRET' })])('marks identifiable failures as certain without exposing exception data', async (error) => {
    const { commandFailure } = await import(modulePath); const result = commandFailure(error, labels);
    expect(result.uncertain).toBe(false);
    expect(result.message).not.toContain('结果未确认');
    expect(result.message).not.toContain('HTTP');
    expect(JSON.stringify(result)).not.toContain('TEST_SECRET');
    if (error instanceof TypeError) expect(result.message).toBe('网络已断开，请检查网络后重试');
    else expect(result.message).toBe('服务出错了，请稍后重试');
  });
  it.each([401, 403, 404, 409, 422, 429, 500, 502, 503, 504])('keeps HTTP %s as a determined failure without a status suffix', async (status) => {
    const { commandFailure } = await import(modulePath); const result = commandFailure(new ApiError('TEST_SECRET', status, 600, undefined, { message: 'TEST_SECRET' }), labels);
    expect(result.uncertain).toBe(false); expect(result.message).not.toContain(String(status)); expect(result.message).not.toContain('HTTP'); expect(JSON.stringify(result)).not.toContain('TEST_SECRET'); expect(result.retryAfter).toBe(600);
  });
  it('maps the exact safe App pair contract, not arbitrary detail strings', async () => {
    const { commandFailure } = await import(modulePath);
    const result = commandFailure(new ApiError('HTTP 422', 422, undefined, undefined, 'app_key and app_secret must be provided together'), labels);
    expect(result.fieldErrors.app_key).toBeTruthy(); expect(result.fieldErrors.app_secret).toBeTruthy();
    expect(result.message).not.toContain('app_key and app_secret');
  });
});

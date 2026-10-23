import { describe, expect, it } from 'vitest';
import { pathFor } from './paths';

describe('pathFor', () => {
  it('keeps direct local paths and queries', () => {
    expect(pathFor('', '/v2/pages/dashboard?shop_pk=7')).toBe('/v2/pages/dashboard?shop_pk=7');
  });
  it('adds a deployment prefix exactly once', () => {
    expect(pathFor('/tts/', '/v2/commerce/channel-accounts')).toBe('/tts/v2/commerce/channel-accounts');
    expect(pathFor('/tts', '/tts/v2/pages/dashboard?next=%2F')).toBe('/tts/v2/pages/dashboard?next=%2F');
  });
  it('does not confuse a prefix with a different path segment', () => {
    expect(pathFor('/tts', '/tts-other')).toBe('/tts/tts-other');
  });
  it.each(['https://evil.test/', '//evil.test/', '/\\evil.test/', '/v2/../admin', '/v2/%2e%2e/admin'])('refuses unsafe path %s', (path) => {
    expect(() => pathFor('/tts', path)).toThrow();
  });
});

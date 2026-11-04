import { describe, expect, it } from 'vitest';
import {
  decodeAsset,
  decodeAssetList,
  decodeDigitalHuman,
  decodeDigitalHumanDetail,
  decodeUploadTicket,
  displayBytes,
} from './contracts';

function asset(overrides: Record<string, unknown> = {}) {
  return {
    asset_id: 12,
    kind: 'image',
    filename: 'shot.jpg',
    content_type: 'image/jpeg',
    size_bytes: 2048,
    description: null,
    tags: [],
    status: 'ready',
    failure_reason: null,
    uploaded_at: '2026-11-03T10:00:00Z',
    uploaded_by_prefix: 'ttserp_',
    created_at: '2026-11-03T09:00:00Z',
    url: 'http://127.0.0.1:9000/bucket/shot.jpg?sig=1',
    url_expires_at: null,
    ...overrides,
  };
}

describe('media asset wire contracts', () => {
  it('decodes one asset', () => {
    const row = decodeAsset(asset());
    expect(row.assetId).toBe('12');
    expect(row.kind).toBe('image');
    expect(row.sizeBytes).toBe(2048);
    expect(row.tags).toEqual([]);
    expect(row.url).toContain('sig=1');
  });

  it('rejects a non-object payload', () => {
    expect(() => decodeAsset('nope')).toThrow('媒体资产响应格式错误');
    expect(() => decodeAsset(null)).toThrow('媒体资产响应格式错误');
  });

  it('rejects a non-numeric identity', () => {
    expect(() => decodeAsset(asset({ asset_id: 'abc' }))).toThrow('媒体资产响应格式错误');
  });

  it('decodes a page with its total', () => {
    const page = decodeAssetList({ items: [asset()], total: 1, limit: 100, offset: 0 });
    expect(page.items).toHaveLength(1);
    expect(page.total).toBe(1);
  });

  it('rejects a page whose items are not an array', () => {
    expect(() => decodeAssetList({ items: 'nope', total: 0, limit: 10, offset: 0 })).toThrow(
      '媒体资产响应格式错误',
    );
  });

  it('decodes an upload ticket and keeps the required headers', () => {
    const ticket = decodeUploadTicket({
      asset_id: 5,
      object_key: 'assets/2026-11-03/5-a.jpg',
      upload_url: 'https://minio.example/bucket/assets/2026-11-03/5-a.jpg?sig=1',
      upload_expires_at: '2026-11-03T10:15:00Z',
      required_headers: { 'Content-Type': 'image/jpeg' },
    });
    expect(ticket.assetId).toBe('5');
    expect(ticket.requiredHeaders['Content-Type']).toBe('image/jpeg');
  });

  it('rejects an upload ticket with a non-http url', () => {
    expect(() =>
      decodeUploadTicket({
        asset_id: 5,
        object_key: 'k',
        upload_url: 'file:///etc/passwd',
        upload_expires_at: '2026-11-03T10:15:00Z',
        required_headers: {},
      }),
    ).toThrow('媒体资产响应格式错误');
  });

  it('decodes a digital human and its asset ids', () => {
    const human = decodeDigitalHuman({
      digital_human_id: 3,
      name: '数字人 A',
      description: null,
      status: 'active',
      created_at: '2026-11-03T09:00:00Z',
      asset_ids: [1, 2],
    });
    expect(human.digitalHumanId).toBe('3');
    expect(human.assetIds).toEqual(['1', '2']);
  });

  it('decodes a digital human detail with its assets', () => {
    const detail = decodeDigitalHumanDetail({
      digital_human_id: 3,
      name: '数字人 A',
      description: '说明',
      status: 'active',
      created_at: '2026-11-03T09:00:00Z',
      asset_ids: [12],
      assets: [asset()],
    });
    expect(detail.assets).toHaveLength(1);
    expect(detail.assets[0].assetId).toBe('12');
  });
});

describe('display-only byte formatting', () => {
  it('formats bytes below one KiB verbatim', () => {
    expect(displayBytes(512)).toBe('512 B');
  });

  it('formats MiB and GiB ranges', () => {
    expect(displayBytes(20 * 1024 * 1024)).toBe('20 MiB');
    expect(displayBytes(1024 * 1024 * 1024)).toBe('1.0 GiB');
  });

  it('returns a placeholder for unusable input', () => {
    expect(displayBytes('abc')).toBe('—');
  });
});

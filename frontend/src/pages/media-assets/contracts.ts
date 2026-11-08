// Media library wire fields are camelCase. State, size caps and eligibility
// belong to the API; the browser only renders what the server returned.
export type Count = number | string;

export interface Asset {
  assetId: string;
  kind: string;
  filename: string;
  contentType: string;
  sizeBytes: Count;
  description: string | null;
  tags: string[];
  status: string;
  failureReason: string | null;
  uploadedAt: string | null;
  uploadedByPrefix: string | null;
  createdAt: string;
  url: string | null;
  urlExpiresAt: string | null;
  usedBy: AssetReference[] | null;
  usedByPacks?: PackReference[];
  usageCount: Count | null;
}

export interface AssetReference {
  id: string;
  name: string;
  status: string;
}

export interface PackReference {
  packId: string;
  name: string;
}

export interface MediaPack extends PackReference {
  videoCount: Count;
}

export interface FeedPage<T> {
  items: T[];
  total: Count;
  nextCursor: string | null;
}

export interface AssetList extends FeedPage<Asset> {
  limit: Count;
  offset: Count;
}

export interface UploadTicket {
  assetId: string;
  objectKey: string;
  uploadUrl: string;
  uploadExpiresAt: string;
  requiredHeaders: Record<string, string>;
}

export interface DigitalHuman {
  digitalHumanId: string;
  name: string;
  description: string | null;
  status: string;
  createdAt: string;
  assetIds: string[];
  portraitUrl?: string | null;
}

export interface DigitalHumanDetail extends DigitalHuman {
  assets: Asset[];
}

function invalid(): never {
  throw new Error('媒体资产响应格式错误');
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  return typeof value === 'string' ? value : invalid();
}
function nullableText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value);
}
function count(value: unknown): Count {
  return (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0)
    || (typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value))
    ? value
    : invalid();
}
function identity(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return String(value);
  if (typeof value === 'string' && /^[1-9]\d*$/.test(value)) return value;
  return invalid();
}
function tags(value: unknown): string[] {
  if (!Array.isArray(value)) return invalid();
  return value.map((item) => text(item));
}

function decodeReferences(value: unknown): AssetReference[] | null {
  if (value === null || value === undefined) return null;
  if (!Array.isArray(value)) return invalid();
  return value.map((entry) => {
    const row = object(entry);
    return { id: identity(row.digital_human_id), name: text(row.name), status: text(row.status) };
  });
}

function decodePackReference(value: unknown): PackReference {
  const row = object(value);
  return { packId: identity(row.packId), name: text(row.name) };
}

export function decodeMediaPack(value: unknown): MediaPack {
  return { ...decodePackReference(value), videoCount: count(object(value).videoCount) };
}

export function decodeMediaPackFeed(value: unknown): FeedPage<MediaPack> {
  const row = object(value);
  if (!Array.isArray(row.items)) return invalid();
  return { items: row.items.map(decodeMediaPack), total: count(row.total), nextCursor: nullableText(row.nextCursor) };
}

export function decodeAsset(value: unknown): Asset {
  const row = object(value);
  if (row.usedByPacks !== undefined && !Array.isArray(row.usedByPacks)) return invalid();
  return {
    assetId: identity(row.asset_id),
    kind: text(row.kind),
    filename: text(row.filename),
    contentType: text(row.content_type),
    sizeBytes: count(row.size_bytes),
    description: nullableText(row.description),
    tags: tags(row.tags),
    status: text(row.status),
    failureReason: nullableText(row.failure_reason),
    uploadedAt: nullableText(row.uploaded_at),
    uploadedByPrefix: nullableText(row.uploaded_by_prefix),
    createdAt: text(row.created_at),
    url: nullableText(row.url),
    urlExpiresAt: nullableText(row.url_expires_at),
    usedBy: decodeReferences(row.used_by),
    ...(row.usedByPacks !== undefined && { usedByPacks: row.usedByPacks.map(decodePackReference) }),
    usageCount: row.usageCount === null || row.usageCount === undefined ? null : count(row.usageCount),
  };
}

export function decodeTags(value: unknown): string[] {
  return tags(value);
}

export function decodeAssetList(value: unknown): AssetList {
  const row = object(value);
  if (!Array.isArray(row.items)) return invalid();
  return {
    items: row.items.map(decodeAsset),
    total: count(row.total),
    limit: count(row.limit),
    offset: count(row.offset),
    nextCursor: nullableText(row.nextCursor),
  };
}

export function decodeUploadConfirmation(value: unknown, expectedAssetId: string): Count {
  const row = object(value);
  if (identity(row.asset_id) !== expectedAssetId || row.status !== 'ready') return invalid();
  return count(row.size_bytes);
}

export function decodeUploadTicket(value: unknown): UploadTicket {
  const row = object(value);
  const headers = object(row.required_headers);
  const parsed = new URL(text(row.upload_url));
  if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    return invalid();
  }
  return {
    assetId: identity(row.asset_id),
    objectKey: text(row.object_key),
    uploadUrl: text(row.upload_url),
    uploadExpiresAt: text(row.upload_expires_at),
    requiredHeaders: Object.fromEntries(
      Object.entries(headers).map(([key, item]) => [key, text(item)]),
    ),
  };
}

export function decodeDigitalHuman(value: unknown): DigitalHuman {
  const row = object(value);
  if (!Array.isArray(row.asset_ids)) return invalid();
  return {
    digitalHumanId: identity(row.digital_human_id),
    name: text(row.name),
    description: nullableText(row.description),
    status: text(row.status),
    createdAt: text(row.created_at),
    assetIds: row.asset_ids.map(identity),
    portraitUrl: nullableText(row.portraitUrl),
  };
}

export function decodeDigitalHumanDetail(value: unknown): DigitalHumanDetail {
  const row = object(value);
  if (!Array.isArray(row.assets)) return invalid();
  return { ...decodeDigitalHuman(value), assets: row.assets.map(decodeAsset) };
}

export function decodeDigitalHumanList(value: unknown): DigitalHuman[] {
  if (!Array.isArray(value)) return invalid();
  return value.map(decodeDigitalHuman);
}

export function decodeDigitalHumanFeed(value: unknown): FeedPage<DigitalHuman> {
  const row = object(value);
  if (!Array.isArray(row.items)) return invalid();
  return { items: row.items.map(decodeDigitalHuman), total: count(row.total), nextCursor: nullableText(row.nextCursor) };
}

/** Display-only byte formatting. It never feeds a business decision. */
export function displayBytes(value: Count): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KiB', 'MiB', 'GiB'];
  let scaled = bytes / 1024;
  let index = 0;
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[index]}`;
}

export const assetKinds = [
  { value: '', label: '全部类型' },
  { value: 'image', label: '图片' },
  { value: 'video', label: '视频' },
] as const;

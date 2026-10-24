import { isoDate } from './contracts';
export interface Query { shop: string; spus: string; window: string; activity: string; anchor: string; limit: string; offset: string; urlError: string }
const names = ['shop_pk', 'spu_ids', 'window_days', 'activity', 'anchor_date', 'limit', 'offset'];
export function readQuery(search: string): Query {
  const p = new URLSearchParams(search);
  const invalid = [...p.keys()].some((k) => !names.includes(k)) || names.some((k) => k !== 'spu_ids' && p.getAll(k).length > 1)
    || ['shop_pk', 'spu_ids', 'anchor_date'].some((k) => p.has(k) && p.get(k) === '');
  return { shop: p.get('shop_pk') ?? '', spus: p.getAll('spu_ids').join(','), window: p.get('window_days') ?? '7', activity: p.get('activity') ?? 'recent', anchor: p.get('anchor_date') ?? '', limit: p.get('limit') ?? '100', offset: p.get('offset') ?? '0', urlError: invalid ? '不支持的查询参数或重复条件，请重置筛选后重试。' : '' };
}
export function queryError(q: Query, bound?: { shop: string; date: string }): string {
  if (q.urlError) return q.urlError;
  if (q.shop && !/^[1-9]\d*$/.test(q.shop)) return '店铺必须是正整数内部编号。';
  if (!['3', '7'].includes(q.window)) return '比较窗口无效，仅支持 7 / 3 天。';
  if (!['recent', 'all'].includes(q.activity)) return '活动范围无效，请选择 recent 或 all。';
  if (q.spus) {
    const ids = q.spus.split(',').map((id) => id.trim());
    if (ids.length > 100 || ids.some((id) => !/^[1-9]\d*$/.test(id))) return 'SPU 输入无效：最多 100 个正整数内部编号，逗号分隔；未查询整店。';
  }
  if (q.anchor) {
    try { isoDate(q.anchor); } catch { return '截至日无效，请输入店铺当地 ISO 日期。'; }
    if (bound?.shop === q.shop && q.anchor > bound.date) return `日期超过最新可用日期 ${bound.date}，请修正后重试。`;
  }
  if (!/^[1-9]\d*$/.test(q.limit) || Number(q.limit) > 500) return '每页条数应为 1–500。';
  if (!/^\d+$/.test(q.offset) || !Number.isSafeInteger(Number(q.offset))) return '分页 offset 参数无效。';
  return '';
}
export function queryParams(q: Query): URLSearchParams {
  const p = new URLSearchParams({ window_days: q.window, activity: q.activity });
  if (q.shop) p.set('shop_pk', q.shop);
  if (q.anchor) p.set('anchor_date', q.anchor);
  if (q.spus) q.spus.split(',').forEach((id) => p.append('spu_ids', id.trim()));
  p.set('limit', q.limit); p.set('offset', q.offset); return p;
}

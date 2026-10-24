import { Button, Input } from '@heroui/react';
import type { Options } from './contracts';
import { readQuery, type Query } from './query';
export default function Filters({ draft, setDraft, options, disabled, onApply, onReset }: {
  draft: Query; setDraft: (query: Query) => void; options?: Options; disabled: boolean; onApply: () => void; onReset: () => void;
}) {
  const advertisers = options?.advertisers.filter((item) => !draft.seller_id || item.seller_id === draft.seller_id) ?? [];
  const selectClass = 'w-full rounded-lg border p-2 bg-surface text-foreground';
  return <form aria-label="广告筛选" className="space-y-4" onSubmit={(event) => { event.preventDefault(); onApply(); }}>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <label className="min-w-0">店铺（外部 Seller ID）<select className={selectClass} aria-label="店铺（外部 Seller ID）" value={draft.seller_id} onChange={(event) => {
        const seller_id = event.target.value;
        const keep = options?.advertisers.some((a) => (!seller_id || a.seller_id === seller_id) && a.advertiser_id === draft.advertiser_id);
        setDraft({ ...draft, seller_id, advertiser_id: keep ? draft.advertiser_id : '' });
      }}><option value="">全部店铺</option>{draft.seller_id && !options?.sellers.some((s) => s.seller_id === draft.seller_id) && <option value={draft.seller_id}>{draft.seller_id}</option>}{options?.sellers.map((s) => <option key={s.seller_id} value={s.seller_id}>{s.shop_name ? `${s.shop_name} · ` : ''}{s.seller_id} ({s.row_count})</option>)}</select></label>
      <label className="min-w-0">广告账户<select className={selectClass} aria-label="广告账户" value={draft.advertiser_id} onChange={(event) => setDraft({ ...draft, advertiser_id: event.target.value })}><option value="">全部账户</option>{draft.advertiser_id && !advertisers.some((a) => a.advertiser_id === draft.advertiser_id) && <option value={draft.advertiser_id}>{draft.advertiser_id}</option>}{advertisers.map((a) => <option key={`${a.seller_id}:${a.advertiser_id}`} value={a.advertiser_id}>{a.advertiser_id}{!draft.seller_id ? ` · ${a.seller_id}` : ''} ({a.row_count})</option>)}</select></label>
      <label className="min-w-0">数据接口<select className={selectClass} aria-label="数据接口" value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}><option value="">全部接口</option>{draft.endpoint && !options?.endpoints.some((e) => e.endpoint === draft.endpoint) && <option value={draft.endpoint}>{draft.endpoint}</option>}{options?.endpoints.map((e) => <option key={e.endpoint} value={e.endpoint}>{e.endpoint} ({e.row_count})</option>)}</select></label>
      <label className="min-w-0">开始日期<Input aria-label="开始日期" type="date" value={draft.day_from} onChange={(event) => setDraft({ ...draft, day_from: event.target.value })} /></label>
      <label className="min-w-0">结束日期<Input aria-label="结束日期" type="date" value={draft.day_to} onChange={(event) => setDraft({ ...draft, day_to: event.target.value })} /></label>
      <label className="min-w-0">计划 / 商品 ID 搜索<Input aria-label="计划 / 商品 ID 搜索" maxLength={200} value={draft.q} onChange={(event) => setDraft({ ...draft, q: event.target.value })} /></label>
      <label className="min-w-0">每页行数<select className={selectClass} aria-label="每页行数" value={draft.limit} onChange={(event) => setDraft({ ...draft, limit: Number(event.target.value) })}>{[25,50,100,200].map((limit) => <option key={limit} value={limit}>{limit}</option>)}</select></label>
    </div>
    <div className="flex flex-wrap gap-2"><Button type="submit" isDisabled={disabled}>应用筛选</Button><Button type="button" variant="secondary" isDisabled={disabled} onPress={() => { setDraft({ ...readQuery(''), sort: draft.sort, order: draft.order }); onReset(); }}>重置筛选</Button></div>
  </form>;
}

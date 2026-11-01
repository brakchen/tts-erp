import { Button, Description, Input, Label, TextField } from '@heroui/react';
import type { Options } from './contracts';
import { readQuery, type Query } from './query';
import './filters.css';

export default function Filters({ draft, setDraft, options, disabled, validation, onApply, onReset, onRefresh }: {
  draft: Query; setDraft: (query: Query) => void; options?: Options; disabled: boolean; validation: string; onApply: () => void; onReset: () => void; onRefresh?: () => void;
}) {
  const advertisers = options?.advertisers.filter((item) => !draft.seller_id || item.seller_id === draft.seller_id) ?? [];
  return <form aria-label="广告筛选" className="erp-panel ad-filters" noValidate onSubmit={(event) => { event.preventDefault(); if (!disabled) onApply(); }}>
    <header className="ad-filters-heading">
      <h2 className="erp-section-heading">筛选广告</h2>
      <p aria-label="数据日期范围" className="ad-filters-range">数据日期范围：{options ? options.min_day && options.max_day ? `${options.min_day} — ${options.max_day}` : '暂无广告日数据' : '—'}</p>
    </header>
    <fieldset disabled={disabled} className="ad-filters-fields">
      <legend className="sr-only">广告筛选条件</legend>
      <div className="ad-filters-grid">
      <label className="ad-filter-field"><span>店铺 <span className="ad-filter-note">Seller ID</span></span><select className="erp-select" aria-label="店铺（外部 Seller ID）" value={draft.seller_id} onChange={(event) => {
        const seller_id = event.target.value;
        const keep = options?.advertisers.some((a) => (!seller_id || a.seller_id === seller_id) && a.advertiser_id === draft.advertiser_id);
        setDraft({ ...draft, seller_id, advertiser_id: keep ? draft.advertiser_id : '' });
      }}><option value="">全部店铺</option>{draft.seller_id && !options?.sellers.some((s) => s.seller_id === draft.seller_id) && <option value={draft.seller_id}>{draft.seller_id}</option>}{options?.sellers.map((s) => <option key={s.seller_id} value={s.seller_id}>{s.shop_name ? `${s.shop_name} · ` : ''}{s.seller_id} ({s.row_count})</option>)}</select></label>
      <label className="ad-filter-field"><span>广告账户</span><select className="erp-select" aria-label="广告账户" value={draft.advertiser_id} onChange={(event) => setDraft({ ...draft, advertiser_id: event.target.value })}><option value="">全部账户</option>{draft.advertiser_id && !advertisers.some((a) => a.advertiser_id === draft.advertiser_id) && <option value={draft.advertiser_id}>{draft.advertiser_id}</option>}{advertisers.map((a) => <option key={`${a.seller_id}:${a.advertiser_id}`} value={a.advertiser_id}>{a.advertiser_id}{!draft.seller_id ? ` · ${a.seller_id}` : ''} ({a.row_count})</option>)}</select></label>
      <label className="ad-filter-field"><span>数据接口</span><select className="erp-select" aria-label="数据接口" value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}><option value="">全部接口</option>{draft.endpoint && !options?.endpoints.some((e) => e.endpoint === draft.endpoint) && <option value={draft.endpoint}>{draft.endpoint}</option>}{options?.endpoints.map((e) => <option key={e.endpoint} value={e.endpoint}>{e.endpoint} ({e.row_count})</option>)}</select></label>
      <TextField className="ad-filter-field" isDisabled={disabled}>
        <Label>开始日期</Label>
        <Input fullWidth className="min-w-0 h-11" type="date" value={draft.day_from} aria-invalid={!!validation} aria-describedby={validation ? 'ad-filter-date-error' : undefined} onChange={(event) => setDraft({ ...draft, day_from: event.target.value })} />
      </TextField>
      <TextField className="ad-filter-field" isDisabled={disabled}>
        <Label>结束日期</Label>
        <Input fullWidth className="min-w-0 h-11" type="date" value={draft.day_to} aria-invalid={!!validation} aria-describedby={validation ? 'ad-filter-date-error' : undefined} onChange={(event) => setDraft({ ...draft, day_to: event.target.value })} />
      </TextField>
      <TextField className="ad-filter-field" isDisabled={disabled}>
        <Label>计划 / 商品 ID 搜索</Label>
        <Input fullWidth className="min-w-0 h-11" type="search" maxLength={200} placeholder="输入计划 ID 或商品 ID" value={draft.q} onChange={(event) => setDraft({ ...draft, q: event.target.value })} />
        <Description>支持 ID 部分匹配，按 Enter 应用筛选</Description>
      </TextField>
      </div>
      {validation && <p id="ad-filter-date-error" className="text-danger text-sm" role="alert">{validation}</p>}
      <footer className="ad-filters-footer">
        <label className="ad-filter-page-size"><span>每页行数</span><select className="erp-select" aria-label="每页行数" value={draft.limit} onChange={(event) => setDraft({ ...draft, limit: Number(event.target.value) })}>{[25, 50, 100, 200].map((limit) => <option key={limit} value={limit}>{limit} 条 / 页</option>)}</select></label>
        <div className="ad-filters-actions">
          <Button type="button" variant="secondary" isDisabled={disabled} onPress={() => { if (disabled) return; setDraft({ ...readQuery(''), sort: draft.sort, order: draft.order }); onReset(); }}>重置筛选</Button>
          <Button type="submit" isDisabled={disabled}>应用筛选</Button>
          {onRefresh && <Button type="button" variant="secondary" isDisabled={disabled} onPress={() => { if (!disabled) onRefresh(); }}>刷新数据</Button>}
        </div>
      </footer>
    </fieldset>
  </form>;
}

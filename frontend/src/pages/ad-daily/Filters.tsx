import { Button, Description, Input, Label, ListBox, Select, TextField } from '@heroui/react';
import type { Options } from './contracts';
import { readQuery, type Query } from './query';
import './filters.css';

export default function Filters({ draft, setDraft, options, disabled, validation, onApply, onReset, onRefresh }: {
  draft: Query; setDraft: (query: Query) => void; options?: Options; disabled: boolean; validation: string; onApply: () => void; onReset: () => void; onRefresh?: () => void;
}) {
  const advertisers = options?.advertisers.filter((item) => !draft.seller_id || item.seller_id === draft.seller_id) ?? [];
  const advertiserChoice = advertisers.find((item) => item.advertiser_id === draft.advertiser_id);
  const advertiserKey = draft.advertiser_id ? advertiserChoice ? JSON.stringify([advertiserChoice.seller_id, advertiserChoice.advertiser_id]) : `unknown:${draft.advertiser_id}` : 'all';
  return <form aria-label="广告筛选" className="erp-panel ad-filters" noValidate onSubmit={(event) => { event.preventDefault(); if (!disabled) onApply(); }}>
    <header className="ad-filters-heading">
      <h2 className="erp-section-heading">筛选广告</h2>
      <p aria-label="数据日期范围" className="ad-filters-range">数据日期范围：{options ? options.min_day && options.max_day ? `${options.min_day} — ${options.max_day}` : '暂无广告日数据' : '—'}</p>
    </header>
    <fieldset disabled={disabled} className="ad-filters-fields">
      <legend className="sr-only">广告筛选条件</legend>
      <div className="ad-filters-grid">
      <Select fullWidth className="ad-filter-field" isDisabled={disabled} aria-label="店铺（外部 Seller ID）" value={`choice:${draft.seller_id}`} onChange={(key) => { if (typeof key === 'string') { const seller_id = key.slice(7); const keep = options?.advertisers.some((a) => (!seller_id || a.seller_id === seller_id) && a.advertiser_id === draft.advertiser_id); setDraft({ ...draft, seller_id, advertiser_id: keep ? draft.advertiser_id : '' }); } } }><Label>店铺 Seller ID</Label><Select.Trigger className="h-11 w-full"><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="choice:" textValue="全部店铺">全部店铺</ListBox.Item>{draft.seller_id && !options?.sellers.some((s) => s.seller_id === draft.seller_id) && <ListBox.Item key={draft.seller_id} id={`choice:${draft.seller_id}`} textValue={draft.seller_id}>{draft.seller_id}</ListBox.Item>}{options?.sellers.map((s) => <ListBox.Item key={s.seller_id} id={`choice:${s.seller_id}`} textValue={`${s.shop_name ? `${s.shop_name} · ` : ''}${s.seller_id} (${s.row_count})`}>{`${s.shop_name ? `${s.shop_name} · ` : ''}${s.seller_id} (${s.row_count})`}</ListBox.Item>)}</ListBox></Select.Popover></Select>
      <Select fullWidth className="ad-filter-field" isDisabled={disabled} aria-label="广告账户" value={advertiserKey} onChange={(key) => {
        if (key === 'all') setDraft({ ...draft, advertiser_id: '' });
        else if (typeof key === 'string') {
          const choice = advertisers.find((item) => JSON.stringify([item.seller_id, item.advertiser_id]) === key);
          if (choice) setDraft({ ...draft, advertiser_id: choice.advertiser_id });
        }
      }}><Label>广告账户</Label><Select.Trigger className="h-11 w-full"><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="all" textValue="全部账户">全部账户</ListBox.Item>{draft.advertiser_id && !advertiserChoice && <ListBox.Item id={`unknown:${draft.advertiser_id}`} textValue={draft.advertiser_id}>{draft.advertiser_id}</ListBox.Item>}{advertisers.map((a) => <ListBox.Item key={JSON.stringify([a.seller_id, a.advertiser_id])} id={JSON.stringify([a.seller_id, a.advertiser_id])} textValue={`${a.advertiser_id}${!draft.seller_id ? ` · ${a.seller_id}` : ''} (${a.row_count})`}>{`${a.advertiser_id}${!draft.seller_id ? ` · ${a.seller_id}` : ''} (${a.row_count})`}</ListBox.Item>)}</ListBox></Select.Popover></Select>
      <Select fullWidth className="ad-filter-field" isDisabled={disabled} aria-label="数据接口" value={`choice:${draft.endpoint}`} onChange={(key) => { if (typeof key === 'string') { setDraft({ ...draft, endpoint: key.slice(7) }); } } }><Label>数据接口</Label><Select.Trigger className="h-11 w-full"><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="choice:" textValue="全部接口">全部接口</ListBox.Item>{draft.endpoint && !options?.endpoints.some((e) => e.endpoint === draft.endpoint) && <ListBox.Item key={draft.endpoint} id={`choice:${draft.endpoint}`} textValue={draft.endpoint}>{draft.endpoint}</ListBox.Item>}{options?.endpoints.map((e) => <ListBox.Item key={e.endpoint} id={`choice:${e.endpoint}`} textValue={`${e.endpoint} (${e.row_count})`}>{`${e.endpoint} (${e.row_count})`}</ListBox.Item>)}</ListBox></Select.Popover></Select>
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
        <Select className="ad-filter-page-size" isDisabled={disabled} aria-label="每页行数" value={`choice:${draft.limit}`} onChange={(key) => { if (typeof key === 'string') { setDraft({ ...draft, limit: Number(key.slice(7)) }); } } }><Label>每页行数</Label><Select.Trigger className="h-11 w-full"><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{[25, 50, 100, 200].map((limit) => <ListBox.Item key={limit} id={`choice:${limit}`} textValue={`${limit} 条 / 页`}>{`${limit} 条 / 页`}</ListBox.Item>)}</ListBox></Select.Popover></Select>
        <div className="ad-filters-actions">
          <Button type="button" variant="secondary" isDisabled={disabled} onPress={() => { if (disabled) return; setDraft({ ...readQuery(''), sort: draft.sort, order: draft.order }); onReset(); }}>重置筛选</Button>
          <Button type="submit" isDisabled={disabled}>应用筛选</Button>
          {onRefresh && <Button type="button" variant="secondary" isDisabled={disabled} onPress={() => { if (!disabled) onRefresh(); }}>刷新数据</Button>}
        </div>
      </footer>
    </fieldset>
  </form>;
}

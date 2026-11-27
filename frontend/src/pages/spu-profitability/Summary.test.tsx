import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { displayPercent, displayValue, priceValue, Summary } from './Summary';

afterEach(cleanup);

it('rounds displayed decimals to two places without losing large integer digits', () => {
  expect(displayValue('90071992547409931.0001')).toBe('90,071,992,547,409,931.00');
  expect(displayValue('90071992547409931.995')).toBe('90,071,992,547,409,932.00');
  expect(displayValue('90071992547409931')).toBe('90,071,992,547,409,931');
  expect(displayValue('1.004')).toBe('1.00'); expect(displayValue('1.005')).toBe('1.01');
  expect(displayValue('-1.005')).toBe('-1.01'); expect(displayValue('9.999')).toBe('10.00');
  expect(displayValue(null)).toBe('—'); expect(displayValue('')).toBe('—');
  expect(displayValue('0.0000')).toBe('0.00'); expect(displayValue('-0.004')).toBe('0.00');
  expect(displayValue(12)).toBe('12'); expect(displayValue('NaN')).toBe('—');
  expect(priceValue('0.0000', true)).toBe('≈0.00'); expect(priceValue('37.005', true)).toBe('≈37.01');
  expect(priceValue('', false)).toBe('—'); expect(priceValue(null, true)).toBe('—');
  expect(displayPercent('0.073456')).toBe('7.35%'); expect(displayPercent('0.073')).toBe('7.30%');
  expect(displayPercent('0.00005')).toBe('0.01%'); expect(displayPercent('-0.00004')).toBe('0.00%');
  expect(displayPercent(null)).toBe('—'); expect(displayPercent('NaN')).toBe('—');
});

it('renders null, empty and absent current or projected values as dashes', () => {
  render(<Summary data={{ items: [], total: 0, totals: { net_profit: null, projected_net_revenue: '', projected_future_full_loss_qty: undefined }, meta: { projection: null } }} />);
  for (const id of ['sum-gmv', 'sum-sales', 'sum-purchase-cost', 'sum-net-profit', 'sum-net-revenue', 'sum-refund-rate', 'sum-projected-net-revenue', 'sum-projected-future-loss-qty']) {
    expect(document.getElementById(id)).toHaveTextContent(/^—$/);
  }
});

it('groups the original server values into sales, costs and profit without deriving missing totals', () => {
  render(<Summary data={{ items: [], total: 0, totals: { gmv: '1234.5678', effective_sales: '661.9793', spend: '270.2993', net_profit: '14.6395', projected_net_revenue: '412.5134', projected_net_profit: '-33.7859', roi_real: '1.71', roi_breakeven: '1.62', projected_roi_real: '1.46', projected_roi_breakeven: '1.67', ad_system_breakeven_roi: '2.86', ad_system_breakeven_roi_status: 'estimated_known_costs' }, meta: {} }} />);
  const sales = screen.getByLabelText('销售'); const costs = screen.getByLabelText('成本'); const profit = screen.getByLabelText('利润');
  expect(within(sales).getByText('1,234.57')).toBeInTheDocument();
  expect(within(sales).getByText('661.98')).toBeInTheDocument();
  expect(within(costs).getByText('270.30')).toBeInTheDocument();
  expect(costs.querySelector('#sum-purchase-cost')).toHaveTextContent('—');
  expect(within(costs).getByText('未提供汇总；采购价为单件口径')).toBeInTheDocument();
  expect(profit.querySelector('#sum-net-revenue')).toHaveTextContent('—');
  expect(within(profit).getByText('未提供汇总；净利润为服务端结果')).toBeInTheDocument();
  expect(within(profit).getByText('412.51')).toBeInTheDocument();
  expect(within(profit).getByText('-33.79')).toBeInTheDocument();
  expect(within(profit).getByRole('grid', { name: '当前与预计 ROI' })).toHaveTextContent('1.71');
  expect(within(profit).getByRole('grid', { name: '当前与预计 ROI' })).toHaveTextContent('1.46');
  expect(within(profit).getByRole('grid', { name: '当前与预计 ROI' })).toHaveTextContent('≈2.86');
  expect(within(sales).queryByText('预计净收入')).toBeNull();
  expect(within(costs).queryByText('预计净收入')).toBeNull();
  expect(within(costs).queryByText('预计新增全损')).toBeNull();
  expect(within(profit).getByText('预计新增全损（件）')).toBeInTheDocument();
});

it('separates the reporting dates from the server sample and keeps the sample inside profit', () => {
  const data = { items: [], total: 0, totals: {}, meta: { projection: { calculated_at: '2026-11-25T18:00:00Z', sample_start: '2026-10-20', sample_end: '2026-11-18', lookback_days: 30, maturity_lag_days: 7 } } };
  const view = render(<Summary data={data} scope="2026-11-19 ~ 2026-11-25" reportingRegion="VN" projectionControl={<button>TEST 样本切换</button>} />);
  const sample = screen.getByRole('region', { name: '历史预测依据' });
  expect(screen.getByLabelText('利润')).toContainElement(sample);
  expect(within(sample).getByText('按当前时间回看')).toBeInTheDocument();
  expect(within(sample).getByText('计算日 2026-11-26')).toBeInTheDocument();
  expect(within(sample).getByText('2026-10-20 ~ 2026-11-18')).toBeVisible();
  expect(within(sample).getByRole('button', { name: 'TEST 样本切换' })).toBeInTheDocument();
  expect(within(sample).queryByText(/SPU 范围/)).toBeNull();
  view.rerender(<Summary data={data} scope="2026-10-01 ~ 2026-10-31" reportingRegion="VN" />);
  expect(screen.getByText('2026-10-01 ~ 2026-10-31 · CNY')).toBeInTheDocument();
  expect(within(sample).getByText('2026-10-20 ~ 2026-11-18')).toBeVisible();
});

it('renders full-scope purchase cost and its loss subset directly from the server', () => {
  const totals = { cogs_total: '2060.0000', strict_full_loss_cost: '200.0000', net_revenue: '3000.0000', spend: '100.0000', net_profit: '840.0000' };
  const view = render(<Summary data={{ items: [{ spu_pk: '1', spu_id: 'TEST_PAGE', title: null, status: null, main_image_url: null, cogs_total: '20.0000' }], total: 10, totals, meta: {} }} />);
  const costs = screen.getByLabelText('成本');
  expect(costs.querySelector('#sum-purchase-cost')).toHaveTextContent('2,060.00');
  expect(within(costs).getByText('其中：全损成本')).toBeInTheDocument();
  expect(costs.querySelector('#sum-full-loss-cost')).toHaveTextContent('200.00');
  expect(document.getElementById('sum-net-revenue')).toHaveTextContent('3,000.00');
  expect(document.getElementById('sum-net-profit')).toHaveTextContent('840.00');
  expect(within(costs).queryByText(/未提供汇总/)).toBeNull();
  view.rerender(<Summary data={{ items: [], total: 0, totals: { cogs_total: '0.0000', strict_full_loss_cost: '0.0000', net_revenue: '0.0000' }, meta: {} }} />);
  expect(costs.querySelector('#sum-purchase-cost')).toHaveTextContent('0.00');
  expect(document.getElementById('sum-net-revenue')).toHaveTextContent('0.00');
});

it('shows the server default-cost warning when purchase cost includes estimates', () => {
  render(<Summary data={{ items: [], total: 1, totals: { cogs_total: '40.0000' }, meta: { warnings: ['default_unit_cost_used'], presentation: { default_cost_alert_message: 'TEST 缺价按后端默认单价估算' } } }} />);
  expect(within(screen.getByLabelText('成本')).getByText('采购成本含估算：TEST 缺价按后端默认单价估算')).toBeVisible();
});

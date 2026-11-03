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
  expect(profit.querySelector('#sum-net-revenue')).toHaveTextContent('—');
  expect(within(profit).getByText('412.51')).toBeInTheDocument();
  expect(within(profit).getByText('-33.79')).toBeInTheDocument();
  expect(within(profit).getByRole('table')).toHaveTextContent('1.71');
  expect(within(profit).getByRole('table')).toHaveTextContent('1.46');
  expect(within(profit).getByRole('table')).toHaveTextContent('≈2.86');
  expect(within(sales).queryByText('预计净收入')).toBeNull();
  expect(within(costs).queryByText('预计净收入')).toBeNull();
});

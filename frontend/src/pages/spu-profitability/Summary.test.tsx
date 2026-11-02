import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { displayValue, priceValue, Summary } from './Summary';

afterEach(cleanup);

it('formats source decimals without rounding long identities or inventing missing zeroes', () => {
  expect(displayValue('90071992547409931.0001')).toBe('90,071,992,547,409,931.0001');
  expect(displayValue(null)).toBe('—'); expect(displayValue('')).toBe('—');
  expect(displayValue('0.0000')).toBe('0.0000'); expect(displayValue('NaN')).toBe('—');
  expect(priceValue('0.0000', true)).toBe('≈0.0000'); expect(priceValue('', false)).toBe('—');
});

it('groups the original server values into sales, costs and profit without deriving missing totals', () => {
  render(<Summary data={{ items: [], total: 0, totals: { gmv: '1234.5678', effective_sales: '661.9793', spend: '270.2993', net_profit: '14.6395', projected_net_revenue: '412.5134', projected_net_profit: '-33.7859', roi_real: '1.71', roi_breakeven: '1.62', projected_roi_real: '1.46', projected_roi_breakeven: '1.67', ad_system_breakeven_roi: '2.86', ad_system_breakeven_roi_status: 'estimated_known_costs' }, meta: {} }} />);
  const sales = screen.getByLabelText('销售'); const costs = screen.getByLabelText('成本'); const profit = screen.getByLabelText('利润');
  expect(within(sales).getByText('1,234.5678')).toBeInTheDocument();
  expect(within(sales).getByText('661.9793')).toBeInTheDocument();
  expect(within(costs).getByText('270.2993')).toBeInTheDocument();
  expect(costs.querySelector('#sum-purchase-cost')).toHaveTextContent('—');
  expect(profit.querySelector('#sum-net-revenue')).toHaveTextContent('—');
  expect(within(profit).getByText('412.5134')).toBeInTheDocument();
  expect(within(profit).getByText('-33.7859')).toBeInTheDocument();
  expect(within(profit).getByRole('table')).toHaveTextContent('1.71');
  expect(within(profit).getByRole('table')).toHaveTextContent('1.46');
  expect(within(profit).getByRole('table')).toHaveTextContent('≈2.86');
  expect(within(sales).queryByText('预计净收入')).toBeNull();
  expect(within(costs).queryByText('预计净收入')).toBeNull();
});

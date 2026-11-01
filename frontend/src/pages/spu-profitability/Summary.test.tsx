import { expect, it } from 'vitest';
import { displayValue, priceValue } from './Summary';

it('formats source decimals without rounding long identities or inventing missing zeroes', () => {
  expect(displayValue('90071992547409931.0001')).toBe('90,071,992,547,409,931.0001');
  expect(displayValue(null)).toBe('—'); expect(displayValue('')).toBe('—');
  expect(displayValue('0.0000')).toBe('0.0000'); expect(displayValue('NaN')).toBe('—');
  expect(priceValue('0.0000', true)).toBe('≈0.0000'); expect(priceValue('', false)).toBe('—');
});

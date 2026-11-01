import { describe, expect, it } from 'vitest';
import { initialQuery, queryParams, preferenceKey, reportingDate, parseIds, percentToRatio } from './query';

describe('profitability page scope', () => {
  it('keeps explicit drill dates and exact business identities ahead of saved preferences', () => {
    const q = initialQuery('spu-roi', '?shop_pk=7&spu_ids=90071992547409931&w_start=2026-09-24&w_end=2026-09-30', { shopPk: '8', datesTouched: true, wStart: '2026-01-01', wEnd: '2026-02-01' });
    expect(q).toMatchObject({ shopPk: '7', spuIds: ['90071992547409931'], wStart: '2026-09-24', wEnd: '2026-09-30', datesTouched: true });
    expect(queryParams(q, false).get('spu_ids')).toBe('90071992547409931');
  });
  it('preserves invalid URL dates for server validation instead of widening the window', () => {
    const q = initialQuery('spu-roi', '?shop_pk=7&w_start=invalid&w_end=2026-09-30');
    expect(queryParams(q, false).get('w_start')).toBe('invalid');
  });
  it('keeps focused and exact selection separate and each profile preferences independent', () => {
    const q = initialQuery('focused-spus', '?shop_pk=7');
    expect(q.includeAll).toBe(true); expect(q.wStart).toBe('');
    expect(queryParams(q, true).get('scope')).toBe('focused');
    expect(queryParams(q, true).has('spu_ids')).toBe(false);
    expect(preferenceKey('focused-spus')).not.toBe(preferenceKey('spu-roi'));
  });
  it('uses the shop calendar rather than the browser calendar', () => {
    expect(reportingDate(new Date('2026-10-31T18:00:00Z'), 'VN')).toBe('2026-11-01');
    expect(reportingDate(new Date(), 'US')).toBe(null);
  });
  it('deduplicates pasted identifiers and shifts fee percentage text without binary rounding', () => {
    expect(parseIds(' A，B A\n90071992547409931 ')).toEqual(['A', 'B', '90071992547409931']);
    expect(percentToRatio('11.56')).toBe('0.1156');
    expect(percentToRatio('0.0001')).toBe('0.000001');
    expect(percentToRatio('.5')).toBe('0.005');
    expect(percentToRatio('1e1')).toBe('0.1');
    expect(percentToRatio('+1')).toBe('0.01');
    expect(percentToRatio('')).toBe(null);
  });
});

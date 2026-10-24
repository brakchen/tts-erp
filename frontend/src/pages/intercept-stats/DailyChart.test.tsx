import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type uPlot from 'uplot';
import DailyChart from './DailyChart';

const probe = vi.hoisted(() => ({ options: undefined as uPlot.Options | undefined, data: undefined as uPlot.AlignedData | undefined }));
vi.mock('uplot-react', () => ({ default: (props: { options: uPlot.Options; data: uPlot.AlignedData }) => { probe.options = props.options; probe.data = props.data; return null; } }));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); probe.options = undefined; probe.data = undefined; });

it('uses the library UTC calendar as well as unchanged backend counts/UTC x values', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 480 } as DOMRect);
  render(<DailyChart rows={[{ date: '2026-10-24', count: 41 }, { date: '2026-10-25', count: 0 }]} />);
  expect(probe.data).toEqual([[Date.parse('2026-10-24T00:00:00Z') / 1000, Date.parse('2026-10-25T00:00:00Z') / 1000], [41, 0]]);
  expect(probe.options?.axes?.[0].incrs).toEqual(expect.arrayContaining([86400]));
  expect(probe.options?.tzDate).toBeTypeOf('function');
  const calendar = probe.options!.tzDate!(Date.parse('2026-10-24T00:00:00Z') / 1000);
  expect([calendar.getFullYear(), calendar.getMonth(), calendar.getDate(), calendar.getHours()]).toEqual([2026, 9, 24, 0]);
});
it('measures its container, resizes without changing axis/series identity, and releases the observer', () => {
  let width = 480; let resize!: ResizeObserverCallback; const disconnect = vi.fn();
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ width } as DOMRect));
  vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback; } observe() {} disconnect = disconnect; });
  const view = render(<DailyChart rows={[{ date: '2026-10-24', count: 41 }]} />);
  const first = probe.options!; expect(first.width).toBe(480);
  width = 320; act(() => resize([], {} as ResizeObserver));
  expect(probe.options!.width).toBe(320); expect(probe.options!.axes).toBe(first.axes); expect(probe.options!.series).toBe(first.series);
  view.unmount(); expect(disconnect).toHaveBeenCalledTimes(1);
});

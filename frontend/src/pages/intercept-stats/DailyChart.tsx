import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import UplotReact from 'uplot-react';
import 'uplot/dist/uPlot.min.css';

export interface DailyCount { date: string; count: number }

/** Presentation adapter only: uPlot owns drawing and its upstream wrapper owns lifecycle. */
export default function DailyChart({ rows }: { rows: DailyCount[] }) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const update = () => setWidth(Math.max(0, Math.floor(element.getBoundingClientRect().width)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const data = useMemo<uPlot.AlignedData>(() => [rows.map((row) => Date.parse(`${row.date}T00:00:00Z`) / 1000), rows.map((row) => row.count)], [rows]);
  const base = useMemo(() => {
    const bars = uPlot.paths.bars;
    if (!bars) throw new Error('图表库不支持条形路径');
    return {
      height: 244,
      tzDate: (timestamp: number) => uPlot.tzDate(new Date(timestamp * 1000), 'UTC'),
      legend: { show: true },
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      axes: [{ incrs: [86400, 172800, 604800, 1209600, 2592000], values: (_plot: uPlot, splits: number[]) => splits.map((value) => new Date(value * 1000).toISOString().slice(5, 10)) }, { size: 48 }],
      series: [{ label: '日期（UTC）' }, { label: '请求数', fill: '#2563eb', paths: bars({ size: [0.55, 48] }), points: { show: false } }],
    };
  }, []);
  const options = useMemo<uPlot.Options>(() => ({ ...base, width }), [base, width]);
  return <div ref={container} className="min-w-0 w-full" aria-hidden="true">
    {width > 0 && <UplotReact options={options} data={data} />}
  </div>;
}

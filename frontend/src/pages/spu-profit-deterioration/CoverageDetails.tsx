import type { Coverage } from './contracts';
export default function CoverageDetails({ coverage }: { coverage: Coverage }) {
  return <details className="rounded-xl border p-4"><summary>数据覆盖依据与限制</summary><div className="space-y-4 pt-3">
    <p>覆盖状态：{coverage.status}；这是观测依据，不是上游完整采集认证。</p><p>依据方法：<span>{coverage.basis}</span></p>
    <h3 className="font-semibold">必要来源</h3>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Object.entries(coverage.sources).map(([name, source]) => <section className="min-w-0 rounded-lg border p-3" key={name} aria-label={`来源 ${name}`}>
      <h4>{name}</h4><dl className="space-y-2 break-all">
        <div><dt>状态 / 作业编号</dt><dd>{source.status} / {source.runId}</dd></div>
        <div><dt>采集启动时间</dt><dd>{source.startedAt}</dd></div><div><dt>采集完成时间</dt><dd>{source.finishedAt}</dd></div>
        <div><dt>推断经营截止日（不是已记录的 query-through）</dt><dd>{source.operationalCutoff}</dd></div><div><dt>已报告失败行数</dt><dd>{source.reportedFailures}</dd></div>
      </dl></section>)}</div>
    <h3 className="font-semibold">广告观测范围</h3><p className="break-all">接口：{coverage.advertising.endpoint}</p>
    <p>{coverage.advertising.startDate} 至 {coverage.advertising.endDate}</p>
    <p>已知缺失日期：{coverage.advertising.missingDates.length ? coverage.advertising.missingDates.join('、') : '无已知缺失（不代表完整采集）'}</p>
    <ul className="space-y-3">{coverage.advertising.observedScopes.map((scope, index) => <li key={index} className="break-all"><strong>{scope.advertiserId} / {scope.campaignId}</strong><p>观察日期：{scope.days.join('、') || '未提供'}</p></li>)}</ul>
    <h3 className="font-semibold">限制说明</h3><ul>{coverage.limitations.map((note, index) => <li key={index}>{note}</li>)}</ul>
  </div></details>;
}

import OffsetPagination from '../../shared/OffsetPagination';
import { useState } from 'react';
import { Button, Chip, ProgressBar, Table } from '@heroui/react';
import { canDispatchBatch, statuses, type Batch } from './contracts';

const filters = [
  { id: 'all', label: '全部' },
  { id: 'active', label: '进行中' },
  { id: 'done', label: '已完成' },
] as const;

export default function BatchRecords({ batches, loading, error, canWrite, busy, offset, onPage, onDetail, onStart, onPause }: {
  batches: Batch[];
  loading: boolean;
  error: string;
  canWrite: boolean;
  busy: boolean;
  offset: number;
  onPage: (offset: number) => void;
  onDetail: (id: string, archive?: boolean) => void;
  onStart: (batch: Batch) => void;
  onPause: (batch: Batch) => void;
}) {
  const [filter, setFilter] = useState<(typeof filters)[number]['id']>('all');
  const visible = batches.filter((batch) => filter === 'all' || (filter === 'done'
    ? batch.status === 'SUCCEEDED'
    : ['ACTIVE', 'PAUSED', 'TERMINATING'].includes(batch.status)));
  return <section className="rh-panel" aria-labelledby="rh-batches-title">
    <div className="rh-panel-heading">
      <h3 id="rh-batches-title">批次记录</h3>
      <div className="rh-filters" role="group" aria-label="筛选当前页批次">
        {filters.map(({ id, label }) => <Button key={id} size="sm" variant={filter === id ? 'secondary' : 'ghost'}
          aria-pressed={filter === id} onPress={() => setFilter(id)}>{label}</Button>)}
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    <Table variant="secondary" className="erp-table erp-compact-table">
      <Table.ScrollContainer className="erp-table-scroll" role="region" aria-label="批次记录表格滚动区域" tabIndex={0}>
        <Table.Content aria-label="批次记录" className="erp-table-content min-w-[31rem]">
          <Table.Header className="erp-table-head">
            <Table.Column className="erp-table-column" isRowHeader>批次</Table.Column>
            <Table.Column className="erp-table-column">状态</Table.Column>
            <Table.Column className="erp-table-column">成片进度</Table.Column>
            <Table.Column className="erp-table-column">操作</Table.Column>
          </Table.Header>
          <Table.Body className="erp-table-body" renderEmptyState={() => (
            <div className="erp-table-empty">{loading ? '正在读取批次…' : error ? '批次读取失败' : '当前页暂无批次'}</div>
          )}>
            {visible.map((batch) => {
              const done = batch.counts.SUCCEEDED ?? 0;
              const completed = batch.status === 'SUCCEEDED';
              const trial = batch.status === 'FIRST_CLIP_READY';
              const firstGroup = batch.groups[0]?.path;
              return <Table.Row className="erp-table-row" key={batch.id} id={batch.id}>
                <Table.Cell className="erp-table-cell">
                  <strong className="rh-batch-label" title={firstGroup}>{firstGroup?.split('/').at(-1) || '换装批次'}</strong>
                  <span className="erp-table-meta break-all">{batch.id}</span>
                </Table.Cell>
                <Table.Cell className="erp-table-cell">
                  <Chip size="sm" color={completed || trial ? 'success' : ['PAUSED', 'TERMINATING'].includes(batch.status) ? 'warning' : 'default'}>
                    {statuses[batch.status] ?? batch.status}
                  </Chip>
                </Table.Cell>
                <Table.Cell className="erp-table-cell">
                  <div className="rh-progress">
                    <ProgressBar aria-label={'成片进度 ' + batch.id} maxValue={Math.max(1, batch.total)} value={done}>
                      <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
                    </ProgressBar>
                    <span>{done} / {batch.total}</span>
                  </div>
                </Table.Cell>
                <Table.Cell className="erp-table-cell" focusMode="child">
                  <div className="flex flex-wrap gap-1">
                    <Button size="sm" variant="ghost" aria-label={(trial ? '查看首段 ' : '批次详情 ') + batch.id}
                      onPress={() => onDetail(batch.id)}>{trial ? '查看首段' : '详情'}</Button>
                    {completed && <Button size="sm" variant="ghost" onPress={() => onDetail(batch.id, true)}>归档</Button>}
                    {canWrite && canDispatchBatch(batch) && (batch.status === 'ACTIVE'
                      ? <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => onPause(batch)}>暂停</Button>
                      : <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => onStart(batch)}>
                        {batch.status === 'PAUSED' ? '恢复' : '启动'}
                      </Button>)}
                  </div>
                </Table.Cell>
              </Table.Row>;
            })}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
    {(offset > 0 || batches.length === 30) &&
      <OffsetPagination label="批次分页" offset={offset} pageSize={30} hasNext={batches.length >= 30} disabled={loading} onPage={onPage} />}
  </section>;
}

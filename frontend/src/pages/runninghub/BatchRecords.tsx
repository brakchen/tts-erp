import { useState } from 'react';
import { Button, Chip, ProgressBar, Table, buttonVariants } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import { statuses, type Batch } from './contracts';

const filters = [
  { id: 'all', label: '全部' },
  { id: 'active', label: '进行中' },
  { id: 'done', label: '已完成' },
] as const;

export default function BatchRecords({ batches, loading, error, root, canWrite, busy, offset, onPage, onDetail, onStart, onPause }: {
  batches: Batch[];
  loading: boolean;
  error: string;
  root: string;
  canWrite: boolean;
  busy: boolean;
  offset: number;
  onPage: (offset: number) => void;
  onDetail: (id: string) => void;
  onStart: (batch: Batch) => void;
  onPause: (batch: Batch) => void;
}) {
  const [filter, setFilter] = useState<(typeof filters)[number]['id']>('all');
  const visible = batches.filter((batch) => filter === 'all' || (filter === 'done'
    ? batch.status === 'SUCCEEDED'
    : batch.status === 'ACTIVE' || batch.status === 'PAUSED'));
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
              const firstGroup = batch.groups[0]?.path;
              return <Table.Row className="erp-table-row" key={batch.id} id={batch.id}>
                <Table.Cell className="erp-table-cell">
                  <strong className="rh-batch-label" title={firstGroup}>{firstGroup?.split('/').at(-1) || '换装批次'}</strong>
                  <span className="erp-table-meta break-all">{batch.id}</span>
                </Table.Cell>
                <Table.Cell className="erp-table-cell">
                  <Chip size="sm" color={completed ? 'success' : batch.status === 'PAUSED' ? 'warning' : 'default'}>
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
                    <Button size="sm" variant="ghost" aria-label={'批次详情 ' + batch.id} onPress={() => onDetail(batch.id)}>详情</Button>
                    {completed && <a className={buttonVariants({ size: 'sm', variant: 'ghost' })}
                      href={pathFor(root, '/v2/runninghub/batches/' + batch.id + '/export')}>归档 ZIP</a>}
                    {canWrite && !completed && (batch.status === 'ACTIVE'
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
    {(offset > 0 || batches.length === 30) && <div className="rh-pager">
      <Button size="sm" variant="secondary" isDisabled={!offset || loading} onPress={() => onPage(Math.max(0, offset - 30))}>上一页批次</Button>
      <Button size="sm" variant="secondary" isDisabled={batches.length < 30 || loading} onPress={() => onPage(offset + 30)}>下一页批次</Button>
    </div>}
  </section>;
}

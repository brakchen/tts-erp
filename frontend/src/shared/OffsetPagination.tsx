import { Pagination } from '@heroui/react';
import type { ReactNode } from 'react';

function pageSequence(current: number, pages: number): (number | '…')[] {
  const selected = [...new Set([1, pages, current - 2, current - 1, current, current + 1, current + 2].filter((page) => page >= 1 && page <= pages))].sort((left, right) => left - right);
  const result: (number | '…')[] = [];
  selected.forEach((page, index) => {
    if (index && page - selected[index - 1] > 1) result.push('…');
    result.push(page);
  });
  return result;
}
interface Props {
  label: string;
  offset: number;
  pageSize: number;
  total?: number | string | null;
  hasNext?: boolean;
  disabled?: boolean;
  onPage: (offset: number) => void;
  summaryAddon?: ReactNode;
}

export default function OffsetPagination({ label, offset, pageSize, total, hasNext, disabled = false, onPage, summaryAddon }: Props) {
  const current = Math.floor(offset / pageSize) + 1;
  const totalRows = total == null ? undefined : BigInt(total);
  const limit = BigInt(pageSize);
  const pageCount = totalRows === undefined ? undefined : ((totalRows + limit - 1n) / limit || 1n);
  const lastSafePage = BigInt(Math.floor(Number.MAX_SAFE_INTEGER / pageSize) + 1);
  const visiblePages = pageCount !== undefined && pageCount <= lastSafePage && BigInt(current) <= pageCount
    ? pageSequence(current, Number(pageCount))
    : [current];
  const nextOffset = offset + pageSize;
  const canGoNext = Number.isSafeInteger(nextOffset) &&
    (hasNext ?? (totalRows !== undefined && BigInt(nextOffset) < totalRows));

  return <Pagination aria-label={label} size="sm">
    <Pagination.Summary className="flex-wrap">
      {summaryAddon}
      <span>第 {current} 页{pageCount === undefined ? '' : ' · 共 ' + pageCount.toString() + ' 页'}</span>
    </Pagination.Summary>
    <Pagination.Content aria-label="页码导航" className="max-w-full flex-wrap">
      <Pagination.Item>
        <Pagination.Previous aria-label="上一页" isDisabled={disabled || offset === 0} onPress={() => onPage(Math.max(0, offset - pageSize))}>
          <Pagination.PreviousIcon />上一页
        </Pagination.Previous>
      </Pagination.Item>
      {visiblePages.map((page, index) => page === '…'
        ? <Pagination.Item key={'gap-' + index}><Pagination.Ellipsis /></Pagination.Item>
        : <Pagination.Item key={page}>
          <Pagination.Link aria-label={'第 ' + page + ' 页'} isActive={page === current} isDisabled={disabled}
            onPress={() => { if (page !== current) onPage((page - 1) * pageSize); }}>{page}</Pagination.Link>
        </Pagination.Item>)}
      <Pagination.Item>
        <Pagination.Next aria-label="下一页" isDisabled={disabled || !canGoNext} onPress={() => onPage(nextOffset)}>
          下一页<Pagination.NextIcon />
        </Pagination.Next>
      </Pagination.Item>
    </Pagination.Content>
  </Pagination>;
}

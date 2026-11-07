import { useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import type { Asset, DigitalHuman, FeedPage } from './contracts';
import { mediaPageSize, useMediaRead } from './media-ui';

export function useCursorFeed<T extends Asset | DigitalHuman>(
  root: string, path: string | null, decode: (value: unknown) => FeedPage<T>, revision: number | string = 0,
) {
  const key = JSON.stringify([root, path, revision]);
  const [requested, setRequested] = useState({ key, cursor: '' });
  const cursor = requested.key === key ? requested.cursor : '';
  const [page, setPage] = useState<{ key: string; cursor: string; data: FeedPage<T> }>();
  const request = path && path + (path.includes('?') ? '&' : '?') + new URLSearchParams({ cursor, limit: String(mediaPageSize) });
  const read = useMediaRead(root, request, decode, revision);
  useEffect(() => {
    const data = read.data;
    if (!data) return;
    setPage((previous) => {
      if (previous?.key === key && previous.cursor === cursor) return previous;
      const id = (row: T) => 'assetId' in row ? row.assetId : row.digitalHumanId;
      const rows = cursor && previous?.key === key ? previous.data.items : [];
      const seen = new Set(rows.map(id));
      return { key, cursor, data: { ...data, items: [...rows, ...data.items.filter((row) => !seen.has(id(row)))] } };
    });
  }, [cursor, key, read.data]);

  const current = page?.key === key ? page : undefined;
  const next = current?.data.nextCursor;
  return {
    data: current?.data,
    loading: Boolean(path && !current && !read.error),
    extending: Boolean(current && current.cursor !== cursor && !read.error),
    error: read.error,
    retry: read.retry,
    hasMore: Boolean(next && next !== current?.cursor),
    loadMore: () => {
      if (next && next !== cursor && current?.cursor === cursor) setRequested({ key, cursor: next });
    },
  };
}

export function CursorTail({ hasMore, extending, error, loadMore, retry, paused = false }: {
  hasMore: boolean; extending: boolean; error?: string;
  loadMore: () => void; retry: () => void; paused?: boolean;
}) {
  const tail = useRef<HTMLDivElement>(null);
  const load = useRef(loadMore);
  useEffect(() => { load.current = loadMore; }, [loadMore]);
  useEffect(() => {
    const element = tail.current;
    if (!element || paused || !hasMore || extending || error || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) load.current();
    }, { root: element.closest('[data-feed-scroll]'), rootMargin: '120px 0px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [paused, hasMore, extending, error]);
  if (!hasMore) return null;
  return <div ref={tail} className="media-feed-tail">
    {error ? <><p role="alert">{error}</p><Button variant="ghost" isDisabled={paused} onPress={retry}>重试加载</Button></>
      : <Button variant="ghost" isDisabled={paused} isPending={extending} onPress={loadMore}>加载更多</Button>}
  </div>;
}

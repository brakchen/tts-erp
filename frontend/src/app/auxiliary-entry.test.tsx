import { act, screen } from '@testing-library/react';
import type { Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { pathFor } from '../shared/api/paths';

const observed = vi.hoisted(() => ({
  root: undefined as Root | undefined,
  unauthorized: undefined as (() => void) | undefined,
  sessionRenders: vi.fn(),
  pageLoads: vi.fn(),
  handlerSets: vi.fn(),
  failResult: undefined as (() => void) | undefined,
}));

vi.mock('react-dom/client', async (original) => {
  const actual = await original<typeof import('react-dom/client')>();
  return { ...actual, createRoot(container: Parameters<typeof actual.createRoot>[0]) {
    observed.root = actual.createRoot(container);
    return observed.root;
  } };
});
vi.mock('../shared/api/paths', async (original) => {
  const actual = await original<typeof import('../shared/api/paths')>();
  return { ...actual, pathFor: vi.fn(actual.pathFor) };
});
vi.mock('../shared/api/client', async (original) => {
  const actual = await original<typeof import('../shared/api/client')>();
  return { ...actual, setUnauthorizedHandler(handler: () => void) {
    observed.handlerSets();
    observed.unauthorized = handler;
    return () => { observed.unauthorized = undefined; };
  } };
});
vi.mock('./SessionActions', () => ({ default: () => {
  observed.sessionRenders();
  return <p>TEST_AUTHENTICATED_SESSION_WIDGET</p>;
} }));
vi.mock('./page-loader', () => ({ pageLoader: (id: string) => {
  observed.pageLoads(id);
  return async () => ({ default: () => <p>TEST_RESULT_CONTENT</p> });
} }));

vi.mock('../pages/message', async () => {
  const { useState } = await import('react');
  return { default: () => {
    const [failed, setFailed] = useState(false);
    observed.failResult = () => setFailed(true);
    if (failed) throw new Error('TEST_result_view_failed');
    return <p>TEST_RESULT_CONTENT</p>;
  } };
});

afterEach(async () => {
  await act(async () => { observed.root?.unmount(); });
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('keeps a completed public result on view failure without session probes, replay, or callback refresh', async () => {
  const bootstrap = {
    rootPath: '/tts', pageId: 'message', releaseId: 'TEST_public_release',
    navigation: [], roleCapabilities: { readwrite: false, admin: false },
    pageData: { kind: 'message', title: 'TEST_RESULT', message: 'TEST_COMPLETED', tone: 'success', action: 'shops', sections: [{ title: 'TEST_SHOP', facts: [{ label: '店铺 ID', value: 'TEST_9007199254740993' }] }] },
  };
  document.body.innerHTML = '<div id="frontend-root"></div><script id="frontend-bootstrap" type="application/json"></script>';
  document.getElementById('frontend-bootstrap')!.textContent = JSON.stringify(bootstrap);
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  await act(async () => { await import('../main'); });
  expect(await screen.findByText('TEST_RESULT_CONTENT')).toBeVisible();
  expect(document.title).toBe('TEST_RESULT · tts-erp');
  expect(screen.getByRole('heading', { level: 1, name: 'TEST_RESULT' })).toBeVisible();
  expect(screen.getAllByRole('banner')).toHaveLength(1);
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  expect(observed.pageLoads).not.toHaveBeenCalled();
  expect(observed.sessionRenders).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  expect(observed.handlerSets).toHaveBeenCalledTimes(1);
  expect(observed.unauthorized).toBeTypeOf('function');
  vi.mocked(pathFor).mockClear();
  observed.unauthorized?.();
  observed.unauthorized?.();
  expect(pathFor).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  await act(async () => { observed.failResult?.(); });
  expect(await screen.findByText('TEST_COMPLETED')).toBeVisible();
  expect(screen.getByText('TEST_9007199254740993')).toBeVisible();
  expect(screen.getByRole('link', { name: '前往店铺注册台' })).toHaveAttribute('href', '/tts/v2/pages/shops');
  expect(screen.queryByRole('button', { name: '刷新页面' })).not.toBeInTheDocument();
  expect(screen.queryByText(/请刷新页面/)).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

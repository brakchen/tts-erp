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
  return async () => ({ default: () => <p>TEST_LOGIN_CONTENT</p> });
} }));

afterEach(async () => {
  await act(async () => { observed.root?.unmount(); });
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('mounts the anonymous entry in the one shell without a session probe or 401 redirect loop', async () => {
  const bootstrap = {
    rootPath: '/tts', pageId: 'login', releaseId: 'TEST_public_release',
    navigation: [], roleCapabilities: { readwrite: false, admin: false },
    loginNextPath: '/tts/v2/pages/dashboard?source=TEST_public_entry',
  };
  document.body.innerHTML = '<div id="frontend-root"></div><script id="frontend-bootstrap" type="application/json"></script>';
  document.getElementById('frontend-bootstrap')!.textContent = JSON.stringify(bootstrap);
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  await act(async () => { await import('../main'); });
  expect(await screen.findByText('TEST_LOGIN_CONTENT')).toBeVisible();
  expect(document.title).toBe('登录 · tts-erp');
  expect(screen.getByRole('heading', { level: 1, name: '登录' })).toBeVisible();
  expect(screen.getAllByRole('banner')).toHaveLength(1);
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  expect(observed.pageLoads).toHaveBeenCalledTimes(1);
  expect(observed.pageLoads).toHaveBeenCalledWith('login');
  expect(observed.sessionRenders).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  expect(observed.handlerSets).toHaveBeenCalledTimes(1);
  expect(observed.unauthorized).toBeTypeOf('function');
  vi.mocked(pathFor).mockClear();
  observed.unauthorized?.();
  observed.unauthorized?.();
  expect(pathFor).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { FrontendBootstrap, MessagePageData } from '../../shared/contracts';
import MessagePage from './index';

const data: MessagePageData = { kind: 'message', title: 'TEST_授权结果', message: 'TEST_已完成', tone: 'success',
  action: 'shops', sections: [{ title: 'TEST_店铺', facts: [{ label: '店铺 ID', value: 'TEST_9007199254740993' }] }] };
const bootstrap: FrontendBootstrap = { rootPath: '/tts', pageId: 'message', releaseId: 'TEST_release',
  navigation: [], roleCapabilities: { readwrite: false, admin: false }, pageData: data };
afterEach(() => vi.unstubAllGlobals());

it('renders completed facts and preserves long IDs without probing auth or replaying callbacks', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const view = render(<MessagePage bootstrap={bootstrap} />);
  expect(screen.getByText('TEST_已完成')).toBeVisible();
  expect(screen.getByText('TEST_9007199254740993')).toBeVisible();
  expect(screen.getByRole('link', { name: '前往店铺注册台' })).toHaveAttribute('href', '/tts/v2/pages/shops');
  view.rerender(<MessagePage bootstrap={bootstrap} />);
  expect(fetch).not.toHaveBeenCalled();
});

it('renders supplied text as text and uses a validated authorization URL only as a link', () => {
  const hostile = '<img src=x onerror=TEST_XSS>';
  const value: FrontendBootstrap = { ...bootstrap, pageData: { ...data, message: hostile,
    authorization: { url: 'https://services.tiktokshop.com/open/authorize?state=TEST_state', expiresAt: 'TEST_expiry' } } };
  render(<MessagePage bootstrap={value} />);
  expect(screen.getByText(hostile)).toBeVisible();
  expect(document.querySelector('img')).toBeNull();
  const link = screen.getByRole('link', { name: '打开授权页面' });
  expect(link).toHaveAttribute('href', 'https://services.tiktokshop.com/open/authorize?state=TEST_state');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
});

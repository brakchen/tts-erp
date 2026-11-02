import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { FrontendBootstrap } from '../../shared/contracts';
import OAuthOnboardPage from './index';

afterEach(() => vi.unstubAllGlobals());
it('explains readonly access and links to the shop-bound owner without generating a state', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const bootstrap: FrontendBootstrap = { rootPath: '/tts', pageId: 'oauth-onboard', releaseId: 'TEST_release',
    navigation: [], roleCapabilities: { readwrite: false, admin: false }, pageData: { kind: 'oauth-onboard' } };
  render(<OAuthOnboardPage bootstrap={bootstrap} />);
  expect(screen.getByText(/只读账号可查看说明/)).toBeVisible();
  expect(screen.getByRole('link', { name: '前往店铺注册台' })).toHaveAttribute('href', '/tts/v2/pages/shops');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

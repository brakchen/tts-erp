import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SessionActions from './SessionActions';

afterEach(() => vi.unstubAllGlobals());

describe('SessionActions', () => {
  it('preserves the existing session label and explicit CSRF-protected logout', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: true, displayName: 'TEST 运营' })))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher);
    const signedOut = vi.fn();
    render(<SessionActions rootPath="/tts" onSignedOut={signedOut} />);
    await screen.findByText('TEST 运营');
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }));
    await waitFor(() => expect(signedOut).toHaveBeenCalledTimes(1));
    expect(fetcher).toHaveBeenLastCalledWith('/tts/v2/auth/logout', expect.objectContaining({ method: 'POST', credentials: 'same-origin' }));
    expect(new Headers(fetcher.mock.calls[1][1].headers).get('X-Requested-With')).toBe('tts-erp');
  });
  it('does not treat auth off or header-key mode as an expired browser session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"authenticated":false}')));
    const signedOut = vi.fn();
    render(<SessionActions rootPath="" onSignedOut={signedOut} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: '退出登录' })).not.toBeInTheDocument();
    expect(signedOut).not.toHaveBeenCalled();
  });
  it('does not invent a successful logout or automatically replay an uncertain result', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"authenticated":true,"username":"TEST_user"}')).mockRejectedValueOnce(new TypeError('network'));
    vi.stubGlobal('fetch', fetcher);
    const signedOut = vi.fn();
    render(<SessionActions rootPath="" onSignedOut={signedOut} />);
    await userEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await screen.findByText('退出结果未确认，请重试或重新登录');
    expect(signedOut).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

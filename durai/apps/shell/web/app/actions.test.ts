import { expect, test, vi } from 'vitest';
import { signOut } from './actions';

const { api, clearCookie, redirect } = vi.hoisted(() => ({
  api: vi.fn(),
  clearCookie: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock('next/headers', () => ({ cookies: async () => ({ delete: clearCookie }) }));
vi.mock('next/navigation', () => ({ redirect }));
vi.mock('./lib', () => ({ api, sessionCookie: 'durai_session' }));

test('signOut sends a POST and clears the session cookie', async () => {
  // Sample: Sign in with cookie S-1. Submit sign-out. Check the API POST and cookie deletion.
  api.mockResolvedValue(new Response(null, { status: 204 }));
  await signOut();
  expect(api).toHaveBeenCalledWith('/session/sign-out', { method: 'POST' });
  expect(clearCookie).toHaveBeenCalledWith('durai_session');
  expect(redirect).toHaveBeenCalledWith('/');
});

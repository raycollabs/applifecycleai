'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { api, sessionCookie } from './lib';

export async function signIn(_: string, form: FormData) {
  // Sample: Post trader A with its secret. Set the session cookie and redirect to /positions.
  const result = await api('/session/sign-in', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trader: form.get('trader'), secret: form.get('secret') }),
  });
  if (!result.ok) return 'Sign-in failed. Check your details and try again.';
  const header = result.headers.getSetCookie().find(value => value.startsWith(sessionCookie + '='));
  const token = header?.match(/^durai_session=([A-Za-z0-9_-]{43});/i)?.[1];
  if (!token) return 'The session could not be created.';
  (await cookies()).set(sessionCookie, token, { path: '/', sameSite: 'lax', httpOnly: true,
    secure: process.env.DURAI_SHELL_COOKIE_SECURE !== '0', maxAge: 28800 });
  redirect('/positions');
}

export async function signOut() {
  // Sample: Submit the sign-out form with session S-1. Clear that cookie and redirect to /.
  const result = await api('/session/sign-out', { method: 'POST' });
  if (!result.ok && result.status !== 401) throw new Error('Sign-out is unavailable');
  (await cookies()).delete(sessionCookie);
  redirect('/');
}

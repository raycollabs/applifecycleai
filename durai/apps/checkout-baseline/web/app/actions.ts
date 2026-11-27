'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { api, sessionCookie } from './lib';

export async function signIn(_: string, form: FormData) {
  // Sample: Post merchant M-1 with its secret. Set session cookie and redirect to /inventory.
  const result = await api('/session/sign-in', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trader: form.get('trader'), secret: form.get('secret') }),
  });
  if (!result.ok) return 'Sign-in failed. Check your merchant credentials.';
  const header = result.headers.getSetCookie().find(v => v.startsWith(sessionCookie + '='));
  const token = header?.match(/^durai_session=([^;]+)/i)?.[1];
  if (!token) return 'The session could not be created.';
  (await cookies()).set(sessionCookie, token, {
    path: '/',
    sameSite: 'lax',
    httpOnly: true,
    secure: process.env.DURAI_SHELL_COOKIE_SECURE !== '0',
    maxAge: 28800,
  });
  redirect('/inventory');
}

export async function signOut() {
  // Sample: Submit POST sign-out form. Clear cookie and redirect to /.
  const result = await api('/session/sign-out', { method: 'POST' });
  if (!result.ok && result.status !== 401) throw new Error('Sign-out is unavailable');
  (await cookies()).delete(sessionCookie);
  redirect('/');
}

export async function createCheckoutIntent(_: string, form: FormData) {
  // Sample: Submit POST /api/intents with Idempotency-Key. Return API error text on 4xx or redirect to /payments.
  const key = String(form.get('idempotency_key') || '');
  const amount = Number(form.get('amount_cents') || 0);
  const sku = String(form.get('sku') || '') || null;
  const quantity = Number(form.get('quantity') || 0);
  const res = await api('/api/intents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify({ amount_cents: amount, currency: 'USD', sku, quantity }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { error?: { message?: string } } | null;
    return body?.error?.message || `Checkout failed (${res.status})`;
  }
  redirect('/payments');
}

export async function captureIntent(form: FormData) {
  // Sample: Submit POST /api/intents/{id}/capture and redirect to /payments.
  const id = String(form.get('intent_id') || '');
  const res = await api(`/api/intents/${encodeURIComponent(id)}/capture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error('Capture failed');
  redirect('/payments');
}

export async function refundIntent(form: FormData) {
  // Sample: Submit POST /api/intents/{id}/refund with minor-unit amount and redirect to /payments.
  const id = String(form.get('intent_id') || '');
  const amount = Number(form.get('amount_cents') || 0);
  const res = await api(`/api/intents/${encodeURIComponent(id)}/refund`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ amount_cents: amount }),
  });
  if (!res.ok) throw new Error('Refund failed');
  redirect('/payments');
}

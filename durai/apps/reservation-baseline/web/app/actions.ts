'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { api, sessionCookie } from './lib';

export async function signIn(_: string, form: FormData) {
  // Sample: Post organizer ORG-LIVE with its secret. Set session cookie and redirect to /events.
  const result = await api('/session/sign-in', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trader: form.get('trader'), secret: form.get('secret') }),
  });
  if (!result.ok) return 'Sign-in failed. Check your organizer credentials.';
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
  redirect('/events');
}

export async function signOut() {
  // Sample: Submit POST sign-out form. Clear cookie and redirect to /.
  const result = await api('/session/sign-out', { method: 'POST' });
  if (!result.ok && result.status !== 401) throw new Error('Sign-out is unavailable');
  (await cookies()).delete(sessionCookie);
  redirect('/');
}

export async function placeSeatHold(_: string, form: FormData) {
  // Sample: Submit POST /api/holds with Idempotency-Key. Return inline API error message on 4xx or redirect to /reservations.
  const key = String(form.get('idempotency_key') || '');
  const combined = String(form.get('event_section') || '');
  const [parsedEvent, parsedSection] = combined.includes('::') ? combined.split('::', 2) : ['', ''];
  const eventId = String(form.get('event_id') || parsedEvent || '');
  const section = String(form.get('section') || parsedSection || '');
  const seatCount = Number(form.get('seat_count') || 0);
  const customerRef = String(form.get('customer_ref') || 'GUEST');
  const res = await api('/api/holds', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify({
      event_id: eventId,
      section,
      seat_count: seatCount,
      customer_ref: customerRef,
      ttl_seconds: 600,
      currency: 'USD',
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { error?: { message?: string } } | null;
    return body?.error?.message || `Seat hold failed (${res.status})`;
  }
  redirect('/reservations');
}

export async function confirmHoldAction(form: FormData) {
  // Sample: Submit POST /api/holds/{id}/confirm with settlement amount and redirect to /reservations.
  const id = String(form.get('hold_id') || '');
  const paymentRaw = form.get('payment_cents');
  const payload = paymentRaw ? { payment_cents: Number(paymentRaw) } : {};
  const res = await api(`/api/holds/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error('Confirm hold failed');
  redirect('/reservations');
}

export async function cancelHoldAction(form: FormData) {
  // Sample: Submit POST /api/holds/{id}/cancel and redirect to /reservations.
  const id = String(form.get('hold_id') || '');
  const res = await api(`/api/holds/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error('Cancel hold failed');
  redirect('/reservations');
}

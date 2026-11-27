import 'server-only';
import { cookies } from 'next/headers';

export const sessionCookie = 'durai_session';
export const screens = {
  events: 'Events & Seats',
  hold: 'Hold Seat',
  reservations: 'Reservations',
  audit: 'Audit Log',
} as const;

export async function api(path: string, options: RequestInit = {}) {
  // Sample: Read /session with cookie S-1. Confirm the API receives S-1.
  const token = (await cookies()).get(sessionCookie)?.value;
  const origin = process.env.DURAI_SHELL_API_ORIGIN;
  if (!origin) throw new Error('Shell API origin is unavailable');
  const headers = new Headers(options.headers);
  if (token) headers.set('Cookie', sessionCookie + '=' + token);
  return fetch(new URL(path, origin), {
    ...options,
    headers,
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(5000),
  });
}

export async function organizer(): Promise<string | null> {
  // Sample: Read an expired session. Return null. Read active organizer session ORG-LIVE. Return ORG-LIVE.
  const result = await api('/session');
  if (result.status === 401) return null;
  if (!result.ok) throw new Error('Session is unavailable');
  return (await result.json() as { trader: string }).trader;
}

export function formatMinorUsd(cents: number): string {
  // Sample: Format 12000 minor units into $120.00.
  return '$' + (cents / 100).toFixed(2);
}

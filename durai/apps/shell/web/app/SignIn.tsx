'use client';

import { useActionState } from 'react';
import { signIn } from './actions';

export function SignIn() {
  // Sample: Submit a bad secret for trader A. Check that the error is visible.
  const [error, action, pending] = useActionState(signIn, '');
  return <form action={action}>
    <label>Trader<input name="trader" autoComplete="username" required /></label>
    <label>Secret<input name="secret" type="password" autoComplete="current-password" required /></label>
    <button type="submit" disabled={pending}>{pending ? 'Signing in' : 'Sign in'}</button>
    {error && <p role="alert">{error}</p>}
  </form>;
}

'use client';

import { useActionState } from 'react';
import { signIn } from './actions';

export function SignIn() {
  // Sample: Render accessible merchant sign-in form posting to signIn server action.
  const [error, action, pending] = useActionState(signIn, '');
  return (
    <form action={action} className="chk-form">
      <label htmlFor="trader">Merchant ID</label>
      <input id="trader" name="trader" required placeholder="M-ACME" />
      <label htmlFor="secret">Secret</label>
      <input id="secret" name="secret" type="password" required />
      {error ? <p role="alert" className="chk-error">{error}</p> : null}
      <button type="submit" disabled={pending} className="chk-btn-primary">
        {pending ? 'Signing in...' : 'Sign in'}
      </button>
    </form>
  );
}

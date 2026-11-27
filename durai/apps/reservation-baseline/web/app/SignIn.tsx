'use client';

import { useActionState } from 'react';
import { signIn } from './actions';

export function SignIn() {
  // Sample: Render accessible organizer sign-in form posting to signIn server action.
  const [error, action, pending] = useActionState(signIn, '');
  return (
    <form action={action} className="res-form">
      <label htmlFor="trader">Organizer ID</label>
      <input id="trader" name="trader" required placeholder="ORG-LIVE" />
      <label htmlFor="secret">Secret</label>
      <input id="secret" name="secret" type="password" required />
      {error ? <p role="alert" className="res-error">{error}</p> : null}
      <button type="submit" disabled={pending} className="res-btn-primary">
        {pending ? 'Signing in...' : 'Sign in'}
      </button>
    </form>
  );
}

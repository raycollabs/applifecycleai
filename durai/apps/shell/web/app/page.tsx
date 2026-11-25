import { redirect } from 'next/navigation';
import { SignIn } from './SignIn';
import { trader } from './lib';

export default async function Home() {
  // Sample: Open / without a session. Check the sign-in form.
  if (await trader()) redirect('/positions');
  return <section data-shell-state="signed-out"><h1>Sign in</h1><SignIn /></section>;
}

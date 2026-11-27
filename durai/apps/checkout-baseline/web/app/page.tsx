import { SignIn } from './SignIn';
import { merchant } from './lib';
import { redirect } from 'next/navigation';

export default async function HomePage() {
  // Sample: Signed-in merchant redirects to /inventory. Unauthenticated visitor sees POST sign-in form.
  const name = await merchant();
  if (name) redirect('/inventory');
  return (
    <main className="chk-main">
      <section className="chk-card chk-auth">
        <h1>Durai Checkout &amp; Ledger</h1>
        <p>Sign in with your merchant identifier to manage inventory holds, idempotent payments, and double-entry postings.</p>
        <SignIn />
      </section>
    </main>
  );
}

import { SignIn } from './SignIn';
import { organizer } from './lib';
import { redirect } from 'next/navigation';

export default async function HomePage() {
  // Sample: Signed-in organizer redirects to /events. Unauthenticated visitor sees POST sign-in form.
  const name = await organizer();
  if (name) redirect('/events');
  return (
    <main className="res-main">
      <section className="res-card res-auth">
        <h1>Durai Seat &amp; Capacity Reservations</h1>
        <p>Sign in with your organizer identifier to manage event capacity, idempotent seat holds, confirmations, and audit records.</p>
        <SignIn />
      </section>
    </main>
  );
}

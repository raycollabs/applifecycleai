import Link from 'next/link';
import { signOut } from './actions';
import { screens } from './lib';

export function Menu({ name, active }: { name: string; active?: string }) {
  // Sample: Render organizer ORG-LIVE header with Events, Hold Seat, Reservations, Audit links and POST Sign out.
  return (
    <header className="res-header">
      <div className="res-brand">Durai Seat Reservations</div>
      <nav aria-label="Reservations" className="res-nav">
        {Object.entries(screens).map(([path, title]) => (
          <Link key={path} href={'/' + path} className={active === path ? 'res-link active' : 'res-link'}>
            {title}
          </Link>
        ))}
      </nav>
      <div className="res-actor">
        <span data-shell-trader={name} className="res-badge">{name}</span>
        <form action={signOut}>
          <button type="submit" className="res-btn-ghost">Sign out</button>
        </form>
      </div>
    </header>
  );
}

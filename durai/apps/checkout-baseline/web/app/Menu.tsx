import Link from 'next/link';
import { signOut } from './actions';
import { screens } from './lib';

export function Menu({ name, active }: { name: string; active?: string }) {
  // Sample: Render merchant M-1 header with Inventory, Checkout, Payments, Ledger links and POST Sign out.
  return (
    <header className="chk-header">
      <div className="chk-brand">Durai Checkout &amp; Ledger</div>
      <nav aria-label="Checkout" className="chk-nav">
        {Object.entries(screens).map(([path, title]) => (
          <Link key={path} href={'/' + path} className={active === path ? 'chk-link active' : 'chk-link'}>
            {title}
          </Link>
        ))}
      </nav>
      <div className="chk-actor">
        <span data-shell-trader={name} className="chk-badge">{name}</span>
        <form action={signOut}>
          <button type="submit" className="chk-btn-ghost">Sign out</button>
        </form>
      </div>
    </header>
  );
}

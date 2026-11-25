import Link from 'next/link';
import { signOut } from './actions';
import { screens } from './lib';

export function Menu({ name }: { name: string }) {
  // Sample: Render trader A. Follow Positions and submit Sign out from the form.
  return <header>
    <nav aria-label="Trading">
      {Object.entries(screens).map(([path, title]) => <Link key={path} href={'/' + path}>{title}</Link>)}
    </nav>
    <span data-shell-trader={name}>{name}</span>
    <form action={signOut}><button type="submit">Sign out</button></form>
  </header>;
}

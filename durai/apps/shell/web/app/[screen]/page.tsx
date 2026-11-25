import { notFound } from 'next/navigation';
import { Menu } from '../Menu';
import { api, screens, trader } from '../lib';

type Screen = keyof typeof screens;
type Receipt = { receipt_id: number; label: string };

export default async function ScreenPage({ params }: { params: Promise<{ screen: string }> }) {
  // Sample: Open Cash as trader A. With no records, check the empty state.
  const { screen } = await params;
  if (!Object.hasOwn(screens, screen)) notFound();
  const name = await trader();
  if (!name) return <section data-shell-state="signed-out"><h1>Signed out</h1><a href="/">Sign in</a></section>;
  const result = await api('/api/receipts');
  if (!result.ok) throw new Error('Screen data is unavailable');
  const { items } = await result.json() as {items: Receipt[]};
  return <>
    <Menu name={name} />
    <h1>{screens[screen as Screen]}</h1>
    {items.length === 0 ? <p data-shell-state="empty">No records yet.</p> :
      <ul data-shell-state="ok">{items.map(item => <li key={item.receipt_id}>{item.label}</li>)}</ul>}
  </>;
}

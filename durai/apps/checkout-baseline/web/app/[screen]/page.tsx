import { notFound } from 'next/navigation';
import { CheckoutForm } from '../CheckoutForm';
import { Menu } from '../Menu';
import { captureIntent, refundIntent } from '../actions';
import { api, formatMinorUsd, merchant, screens } from '../lib';

type InventoryRow = { sku: string; total: number; reserved: number; available: number };
type IntentRow = { intent_id: string; amount_cents: number; captured_cents: number; refunded_cents: number; status: string; sku?: string };
type LedgerRow = { journal_id: string; tx_id: string; reason: string; debit_total_cents: number; credit_total_cents: number };

export default async function ScreenPage({ params }: { params: Promise<{ screen: string }> }) {
  // Sample: Render Inventory, Checkout, Payments, or Double-Entry Ledger with explicit empty and ok states.
  const { screen } = await params;
  if (!Object.hasOwn(screens, screen)) notFound();
  const name = await merchant();
  if (!name) return <section data-shell-state="signed-out"><h1>Signed out</h1><a href="/">Sign in</a></section>;

  if (screen === 'checkout') {
    const invRes = await api('/api/inventory');
    const invData = invRes.ok ? (await invRes.json() as { items?: InventoryRow[] }) : { items: [] };
    return (
      <main className="chk-main">
        <Menu name={name} active={screen} />
        <section className="chk-card" data-shell-state="ok">
          <h1>Create Checkout Intent</h1>
          <CheckoutForm skus={invData.items || []} />
        </section>
      </main>
    );
  }

  const endpoint = screen === 'inventory' ? '/api/inventory' : screen === 'payments' ? '/api/intents' : '/api/ledger';
  const res = await api(endpoint);
  if (!res.ok) throw new Error('Screen data is unavailable');
  const data = await res.json();
  const items = (data.items || []) as Array<InventoryRow & IntentRow & LedgerRow>;

  return (
    <main className="chk-main">
      <Menu name={name} active={screen} />
      <section className="chk-card">
        <h1>{screens[screen as keyof typeof screens]}</h1>
        {items.length === 0 ? (
          <p data-shell-state="empty">No records yet.</p>
        ) : (
          <table className="chk-table" data-shell-state="ok">
            <thead>
              {screen === 'inventory' ? <tr><th>SKU</th><th>Total</th><th>Reserved</th><th>Available</th></tr> : null}
              {screen === 'payments' ? <tr><th>Intent</th><th>Amount</th><th>Captured</th><th>Refunded</th><th>Status</th><th>Actions</th></tr> : null}
              {screen === 'ledger' ? <tr><th>Journal</th><th>Tx</th><th>Reason</th><th>Debits</th><th>Credits</th></tr> : null}
            </thead>
            <tbody>
              {items.map((row, idx) => (
                <tr key={idx}>
                  {screen === 'inventory' ? <><td>{row.sku}</td><td>{row.total}</td><td>{row.reserved}</td><td>{row.available}</td></> : null}
                  {screen === 'payments' ? <><td>{row.intent_id}</td><td>{formatMinorUsd(row.amount_cents)}</td><td>{formatMinorUsd(row.captured_cents)}</td><td>{formatMinorUsd(row.refunded_cents)}</td><td><span className="chk-pill">{row.status}</span></td><td>{row.captured_cents < row.amount_cents ? <form action={captureIntent} style={{display:'inline'}}><input type="hidden" name="intent_id" value={row.intent_id} /><button type="submit" className="chk-btn-ghost">Capture</button></form> : null} {row.captured_cents > row.refunded_cents ? <form action={refundIntent} style={{display:'inline'}}><input type="hidden" name="intent_id" value={row.intent_id} /><input type="hidden" name="amount_cents" value={String(Math.min(1000, row.captured_cents - row.refunded_cents))} /><button type="submit" className="chk-btn-ghost">Refund $10</button></form> : null}</td></> : null}
                  {screen === 'ledger' ? <><td>{row.journal_id}</td><td>{row.tx_id}</td><td>{row.reason}</td><td>{formatMinorUsd(row.debit_total_cents)}</td><td>{formatMinorUsd(row.credit_total_cents)}</td></> : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

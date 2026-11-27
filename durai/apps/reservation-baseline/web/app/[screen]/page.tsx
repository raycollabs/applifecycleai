import { notFound } from 'next/navigation';
import { HoldForm } from '../HoldForm';
import { Menu } from '../Menu';
import { cancelHoldAction, confirmHoldAction } from '../actions';
import { api, formatMinorUsd, organizer, screens } from '../lib';

type EventSeatRow = {
  event_id: string;
  section: string;
  total_seats: number;
  held_seats: number;
  confirmed_seats: number;
  available_seats: number;
  unit_price_cents: number;
};

type HoldRow = {
  hold_id: string;
  event_id: string;
  section: string;
  customer_ref: string;
  seat_count: number;
  amount_cents: number;
  settled_cents: number;
  status: string;
  expires_at: string;
};

type AuditRow = {
  audit_id: string;
  hold_id: string;
  action: string;
  event_id: string;
  section: string;
  seat_count: number;
  amount_cents: number;
  created_at: string;
};

export default async function ScreenPage({ params }: { params: Promise<{ screen: string }> }) {
  // Sample: Render Events & Seats, Hold Seat, Reservations, or Audit Log with explicit empty and ok states.
  const { screen } = await params;
  if (!Object.hasOwn(screens, screen)) notFound();
  const name = await organizer();
  if (!name) return <section data-shell-state="signed-out"><h1>Signed out</h1><a href="/">Sign in</a></section>;

  if (screen === 'hold') {
    const evRes = await api('/api/events');
    if (!evRes.ok) throw new Error('Event capacity is unavailable');
    const evData = await evRes.json() as { items?: EventSeatRow[] };
    const events = evData.items || [];
    return (
      <main className="res-main">
        <Menu name={name} active={screen} />
        <section className="res-card" data-shell-state={events.length === 0 ? 'empty' : 'ok'}>
          <h1>Place Idempotent Seat Hold</h1>
          <HoldForm events={events} />
        </section>
      </main>
    );
  }

  const endpoint = screen === 'events' ? '/api/events' : screen === 'reservations' ? '/api/holds' : '/api/audit';
  const res = await api(endpoint);
  if (!res.ok) throw new Error('Screen data is unavailable');
  const data = await res.json();
  const items = (data.items || []) as Array<EventSeatRow & HoldRow & AuditRow>;

  const totalSeats = screen === 'events' ? items.reduce((acc, r) => acc + r.total_seats, 0) : 0;
  const heldSeats = screen === 'events' ? items.reduce((acc, r) => acc + r.held_seats, 0) : 0;
  const confirmedSeats = screen === 'events' ? items.reduce((acc, r) => acc + r.confirmed_seats, 0) : 0;
  const availableSeats = screen === 'events' ? items.reduce((acc, r) => acc + r.available_seats, 0) : 0;
  const activeHolds = screen === 'reservations' ? items.filter(r => r.status === 'HELD').length : 0;
  const confirmedHolds = screen === 'reservations' ? items.filter(r => r.status === 'CONFIRMED').length : 0;
  const settledTotal = screen === 'reservations' ? items.reduce((acc, r) => acc + r.settled_cents, 0) : 0;

  return (
    <main className="res-main">
      <Menu name={name} active={screen} />
      {screen === 'events' ? (
        <section className="res-kpis" aria-label="Capacity Summary">
          <div className="res-kpi"><span className="res-kpi-label">Total Seats</span><strong>{totalSeats}</strong></div>
          <div className="res-kpi"><span className="res-kpi-label">Held Seats</span><strong>{heldSeats}</strong></div>
          <div className="res-kpi"><span className="res-kpi-label">Confirmed Seats</span><strong>{confirmedSeats}</strong></div>
          <div className="res-kpi"><span className="res-kpi-label">Available Pool</span><strong>{availableSeats}</strong></div>
        </section>
      ) : null}
      {screen === 'reservations' ? (
        <section className="res-kpis" aria-label="Reservations Summary">
          <div className="res-kpi"><span className="res-kpi-label">Active Holds</span><strong>{activeHolds}</strong></div>
          <div className="res-kpi"><span className="res-kpi-label">Confirmed Holds</span><strong>{confirmedHolds}</strong></div>
          <div className="res-kpi"><span className="res-kpi-label">Settled Volume</span><strong>{formatMinorUsd(settledTotal)}</strong></div>
        </section>
      ) : null}
      <section className="res-card">
        <h1>{screens[screen as keyof typeof screens]}</h1>
        {items.length === 0 ? (
          <p data-shell-state="empty">No records yet.</p>
        ) : (
          <table className="res-table" data-shell-state="ok">
            <thead>
              {screen === 'events' ? <tr><th>Event</th><th>Section</th><th>Total</th><th>Held</th><th>Confirmed</th><th>Available</th><th>Unit Price</th></tr> : null}
              {screen === 'reservations' ? <tr><th>Hold</th><th>Event / Section</th><th>Customer</th><th>Seats</th><th>Amount</th><th>Settled</th><th>Status</th><th>Actions</th></tr> : null}
              {screen === 'audit' ? <tr><th>Audit</th><th>Hold</th><th>Action</th><th>Event / Section</th><th>Seats</th><th>Amount</th></tr> : null}
            </thead>
            <tbody>
              {items.map((row, idx) => (
                <tr key={idx}>
                  {screen === 'events' ? <><td>{row.event_id}</td><td>{row.section}</td><td>{row.total_seats}</td><td>{row.held_seats}</td><td>{row.confirmed_seats}</td><td>{row.available_seats}</td><td>{formatMinorUsd(row.unit_price_cents)}</td></> : null}
                  {screen === 'reservations' ? (
                    <>
                      <td>{row.hold_id}</td>
                      <td>{row.event_id} / {row.section}</td>
                      <td>{row.customer_ref}</td>
                      <td>{row.seat_count}</td>
                      <td>{formatMinorUsd(row.amount_cents)}</td>
                      <td>{formatMinorUsd(row.settled_cents)}</td>
                      <td><span className={`res-pill res-pill-${row.status.toLowerCase()}`}>{row.status}</span></td>
                      <td>
                        {row.status === 'HELD' ? (
                          <form action={confirmHoldAction} style={{ display: 'inline' }}>
                            <input type="hidden" name="hold_id" value={row.hold_id} />
                            <input type="hidden" name="payment_cents" value={String(row.amount_cents)} />
                            <button type="submit" className="res-btn-ghost">Confirm &amp; Settle</button>
                          </form>
                        ) : null}
                        {row.status === 'HELD' || row.status === 'CONFIRMED' ? (
                          <form action={cancelHoldAction} style={{ display: 'inline', marginLeft: '6px' }}>
                            <input type="hidden" name="hold_id" value={row.hold_id} />
                            <button type="submit" className="res-btn-ghost">Cancel &amp; Release</button>
                          </form>
                        ) : null}
                      </td>
                    </>
                  ) : null}
                  {screen === 'audit' ? <><td>{row.audit_id}</td><td>{row.hold_id}</td><td><span className="res-pill">{row.action}</span></td><td>{row.event_id} / {row.section}</td><td>{row.seat_count}</td><td>{formatMinorUsd(row.amount_cents)}</td></> : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

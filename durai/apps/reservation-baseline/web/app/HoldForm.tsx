'use client';

import { useActionState } from 'react';
import { placeSeatHold } from './actions';

type EventSectionOption = {
  event_id: string;
  section: string;
  available_seats: number;
  unit_price_cents: number;
};

export function HoldForm({ events }: { events: EventSectionOption[] }) {
  // Sample: Render seat hold form with event/section selector and inline error banner from placeSeatHold.
  const [error, action, pending] = useActionState(placeSeatHold, '');
  const defaultValue = events[0] ? `${events[0].event_id}::${events[0].section}` : '';
  return (
    <form action={action} className="res-form">
      <label htmlFor="idempotency_key">Idempotency Key</label>
      <input id="idempotency_key" name="idempotency_key" required placeholder="HOLD-1001" />
      <label htmlFor="event_section">Event &amp; Section</label>
      <select id="event_section" name="event_section" defaultValue={defaultValue} required>
        {events.length === 0 ? <option value="">No event sections available</option> : null}
        {events.map(ev => (
          <option key={`${ev.event_id}::${ev.section}`} value={`${ev.event_id}::${ev.section}`}>
            {ev.event_id} / {ev.section} ({ev.available_seats} available @ ${(ev.unit_price_cents / 100).toFixed(2)})
          </option>
        ))}
      </select>
      <label htmlFor="seat_count">Seats</label>
      <input id="seat_count" name="seat_count" type="number" min="1" required defaultValue="2" />
      <label htmlFor="customer_ref">Customer Reference</label>
      <input id="customer_ref" name="customer_ref" required placeholder="CUST-1001" />
      {error ? <p role="alert" className="res-error">{error}</p> : null}
      <button type="submit" disabled={pending || events.length === 0} className="res-btn-primary">
        {pending ? 'Placing hold...' : 'Place 10-Min Hold'}
      </button>
    </form>
  );
}

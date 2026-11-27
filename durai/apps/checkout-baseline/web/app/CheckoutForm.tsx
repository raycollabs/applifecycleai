'use client';

import { useActionState } from 'react';
import { createCheckoutIntent } from './actions';

type SkuOption = { sku: string; available: number };

export function CheckoutForm({ skus }: { skus: SkuOption[] }) {
  // Sample: Render checkout form with SKU selector and inline error banner from createCheckoutIntent.
  const [error, action, pending] = useActionState(createCheckoutIntent, '');
  return (
    <form action={action} className="chk-form">
      <label htmlFor="idempotency_key">Idempotency Key</label>
      <input id="idempotency_key" name="idempotency_key" required placeholder="ORD-1002" />
      <label htmlFor="amount_cents">Amount (USD cents)</label>
      <input id="amount_cents" name="amount_cents" type="number" min="1" required defaultValue="5000" />
      <label htmlFor="sku">SKU (optional)</label>
      <select id="sku" name="sku" defaultValue={skus[0]?.sku || ''}>
        <option value="">None (no stock hold)</option>
        {skus.map(item => (
          <option key={item.sku} value={item.sku}>
            {item.sku} ({item.available} available)
          </option>
        ))}
      </select>
      <label htmlFor="quantity">Quantity</label>
      <input id="quantity" name="quantity" type="number" min="0" defaultValue={skus.length ? '1' : '0'} />
      {error ? <p role="alert" className="chk-error">{error}</p> : null}
      <button type="submit" disabled={pending} className="chk-btn-primary">
        {pending ? 'Submitting...' : 'Authorize & Reserve'}
      </button>
    </form>
  );
}

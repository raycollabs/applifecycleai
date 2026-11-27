CREATE SCHEMA IF NOT EXISTS checkout;

CREATE TABLE IF NOT EXISTS checkout.inventory (
    merchant text NOT NULL,
    sku text NOT NULL,
    total_units integer NOT NULL CHECK (total_units >= 0),
    PRIMARY KEY (merchant, sku)
);

CREATE TABLE IF NOT EXISTS checkout.reservation (
    reservation_id text PRIMARY KEY,
    merchant text NOT NULL,
    sku text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    order_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('HELD', 'CONFIRMED', 'RELEASED', 'EXPIRED'))
);

CREATE TABLE IF NOT EXISTS checkout.payment_intent (
    intent_id text PRIMARY KEY,
    merchant text NOT NULL,
    amount_cents bigint NOT NULL CHECK (amount_cents > 0),
    currency text NOT NULL,
    captured_cents bigint NOT NULL DEFAULT 0 CHECK (captured_cents >= 0),
    refunded_cents bigint NOT NULL DEFAULT 0 CHECK (refunded_cents >= 0),
    status text NOT NULL,
    sku text,
    quantity integer NOT NULL DEFAULT 0,
    reservation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (captured_cents <= amount_cents),
    CHECK (refunded_cents <= captured_cents)
);

CREATE TABLE IF NOT EXISTS checkout.idempotency (
    merchant text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    intent_id text NOT NULL REFERENCES checkout.payment_intent(intent_id),
    PRIMARY KEY (merchant, idempotency_key)
);

CREATE TABLE IF NOT EXISTS checkout.journal_entry (
    journal_id text PRIMARY KEY,
    tx_id text NOT NULL,
    merchant text NOT NULL,
    reason text NOT NULL,
    debit_total_cents bigint NOT NULL,
    credit_total_cents bigint NOT NULL,
    postings jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (debit_total_cents = credit_total_cents)
);

CREATE TABLE IF NOT EXISTS checkout.session (
    token_digest bytea PRIMARY KEY,
    merchant text NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE SCHEMA IF NOT EXISTS reservation;

CREATE TABLE IF NOT EXISTS reservation.event_seat (
    organizer text NOT NULL,
    event_id text NOT NULL,
    section text NOT NULL,
    total_seats integer NOT NULL CHECK (total_seats >= 0),
    confirmed_seats integer NOT NULL DEFAULT 0 CHECK (confirmed_seats >= 0),
    unit_price_cents bigint NOT NULL DEFAULT 0 CHECK (unit_price_cents >= 0),
    PRIMARY KEY (organizer, event_id, section),
    CHECK (confirmed_seats <= total_seats)
);

CREATE TABLE IF NOT EXISTS reservation.seat_hold (
    hold_id text PRIMARY KEY,
    organizer text NOT NULL,
    event_id text NOT NULL,
    section text NOT NULL,
    customer_ref text NOT NULL,
    seat_count integer NOT NULL CHECK (seat_count > 0),
    amount_cents bigint NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
    settled_cents bigint NOT NULL DEFAULT 0 CHECK (settled_cents >= 0),
    currency text NOT NULL DEFAULT 'USD',
    status text NOT NULL CHECK (status IN ('HELD', 'CONFIRMED', 'CANCELLED', 'EXPIRED')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (settled_cents <= amount_cents),
    FOREIGN KEY (organizer, event_id, section) REFERENCES reservation.event_seat (organizer, event_id, section)
);

CREATE TABLE IF NOT EXISTS reservation.idempotency (
    organizer text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    hold_id text NOT NULL REFERENCES reservation.seat_hold(hold_id),
    PRIMARY KEY (organizer, idempotency_key)
);

CREATE TABLE IF NOT EXISTS reservation.audit_log (
    audit_id text PRIMARY KEY,
    hold_id text NOT NULL,
    organizer text NOT NULL,
    action text NOT NULL CHECK (action IN ('HOLD_PLACED', 'HOLD_CONFIRMED', 'HOLD_CANCELLED', 'HOLD_EXPIRED')),
    event_id text NOT NULL,
    section text NOT NULL,
    seat_count integer NOT NULL CHECK (seat_count >= 0),
    amount_cents bigint NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reservation.session (
    token_digest bytea PRIMARY KEY,
    organizer text NOT NULL,
    expires_at timestamptz NOT NULL
);

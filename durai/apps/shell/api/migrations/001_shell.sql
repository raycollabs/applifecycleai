CREATE SCHEMA IF NOT EXISTS shell;

CREATE TABLE shell.actor (
    actor_id text PRIMARY KEY,
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE shell.session (
    token_digest bytea PRIMARY KEY,
    actor_id text NOT NULL REFERENCES shell.actor,
    expires_at timestamptz NOT NULL
);

CREATE TABLE shell.receipt (
    receipt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id text NOT NULL REFERENCES shell.actor,
    label text NOT NULL
);

CREATE TABLE shell.idempotency (
    actor_id text NOT NULL REFERENCES shell.actor,
    operation text NOT NULL,
    request_key text NOT NULL,
    request_digest text NOT NULL,
    response jsonb NOT NULL,
    PRIMARY KEY (actor_id, operation, request_key)
);

CREATE TABLE shell.audit (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id text NOT NULL REFERENCES shell.actor,
    action text NOT NULL,
    receipt_id bigint REFERENCES shell.receipt,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shell.outbox (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_id bigint NOT NULL UNIQUE REFERENCES shell.receipt,
    event_type text NOT NULL,
    published_at timestamptz
);

CREATE INDEX receipt_actor_page ON shell.receipt(actor_id, receipt_id);
CREATE INDEX outbox_pending ON shell.outbox(event_id) WHERE published_at IS NULL;

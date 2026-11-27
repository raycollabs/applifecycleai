BEGIN;
REVOKE ALL ON DATABASE reporting FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA reporting_private;
CREATE SCHEMA oil;
CREATE SCHEMA gas;
CREATE ROLE report_oil LOGIN;
CREATE ROLE report_gas LOGIN;
CREATE ROLE report_unassigned LOGIN;
CREATE ROLE report_publisher LOGIN;
GRANT CONNECT ON DATABASE reporting TO report_oil, report_gas, report_unassigned, report_publisher;

CREATE TABLE reporting_private.runs (
    application text NOT NULL CHECK (application <> ''),
    business_date date NOT NULL,
    book text NOT NULL CHECK (book <> ''),
    revision integer NOT NULL CHECK (revision > 0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
    status text NOT NULL CHECK (status IN ('COMPLETE', 'INCOMPLETE', 'EMPTY')),
    event_cutoff date NOT NULL,
    known_at timestamptz NOT NULL,
    calculation_version text NOT NULL,
    issues jsonb NOT NULL,
    mark_references jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    row_count integer NOT NULL CHECK ((status = 'COMPLETE' AND row_count > 0) OR (status <> 'COMPLETE' AND row_count = 0)),
    PRIMARY KEY (application, business_date, book, revision)
);

CREATE INDEX runs_accepted_by_book_date ON reporting_private.runs
    (application, book, business_date, revision DESC) WHERE status = 'COMPLETE';

CREATE TABLE reporting_private.rows (
    application text NOT NULL,
    business_date date NOT NULL,
    book text NOT NULL,
    revision integer NOT NULL,
    contract text NOT NULL CHECK (contract <> ''),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    opening_units numeric NOT NULL,
    closing_units numeric NOT NULL,
    opening_pnl numeric NOT NULL,
    fill_pnl numeric NOT NULL,
    fees numeric NOT NULL,
    net_pnl numeric NOT NULL,
    settlement_cash numeric,
    PRIMARY KEY (application, book, business_date, revision, contract, currency),
    FOREIGN KEY (application, business_date, book, revision)
        REFERENCES reporting_private.runs (application, business_date, book, revision)
);

CREATE VIEW reporting_private.current_close AS
SELECT r.business_date, r.book, r.contract, r.currency, r.revision,
       r.opening_units, r.closing_units, r.opening_pnl, r.fill_pnl, r.fees,
       r.net_pnl, r.settlement_cash, a.status, a.event_cutoff, a.known_at,
       a.content_sha256, a.application
FROM reporting_private.rows AS r
JOIN (
    SELECT DISTINCT ON (application, book, business_date) *
    FROM reporting_private.runs
    WHERE status = 'COMPLETE'
    ORDER BY application, book, business_date, revision DESC
) AS a USING (application, business_date, book, revision);

CREATE VIEW oil.energy_futures_close WITH (security_barrier = true) AS
SELECT business_date, book, contract, currency, revision, opening_units,
       closing_units, opening_pnl, fill_pnl, fees, net_pnl, settlement_cash,
       status, event_cutoff, known_at, content_sha256
FROM reporting_private.current_close WHERE application = 'energy-futures' AND book = 'SYN-OIL';

CREATE VIEW gas.energy_futures_close WITH (security_barrier = true) AS
SELECT business_date, book, contract, currency, revision, opening_units,
       closing_units, opening_pnl, fill_pnl, fees, net_pnl, settlement_cash,
       status, event_cutoff, known_at, content_sha256
FROM reporting_private.current_close WHERE application = 'energy-futures' AND book = 'SYN-GAS';

GRANT USAGE ON SCHEMA oil TO report_oil;
GRANT USAGE ON SCHEMA gas TO report_gas;
GRANT SELECT ON oil.energy_futures_close TO report_oil;
GRANT SELECT ON gas.energy_futures_close TO report_gas;
GRANT USAGE ON SCHEMA reporting_private TO report_publisher;
GRANT INSERT, SELECT ON reporting_private.runs TO report_publisher;
GRANT INSERT ON reporting_private.rows TO report_publisher;
ALTER ROLE report_oil SET statement_timeout = '10s';
ALTER ROLE report_gas SET statement_timeout = '10s';
COMMIT;

CREATE DATABASE IF NOT EXISTS reporting_internal;
CREATE DATABASE IF NOT EXISTS oil;
CREATE DATABASE IF NOT EXISTS gas;

CREATE TABLE IF NOT EXISTS reporting_internal.close_revisions
(
    application String,
    business_date Date32,
    book String,
    revision UInt32,
    phase Enum8('staged' = 1, 'published' = 2, 'recorded' = 3),
    status LowCardinality(String),
    content_sha256 FixedString(64),
    operation_id UUID,
    envelope String,
    report_rows Array(Tuple(
        contract String,
        currency String,
        opening_units Decimal(38, 12),
        closing_units Decimal(38, 12),
        opening_pnl Decimal(38, 12),
        fill_pnl Decimal(38, 12),
        fees Decimal(38, 12),
        net_pnl Decimal(38, 12),
        settlement_cash Nullable(Decimal(38, 12))
    )),
    event_cutoff Date32,
    known_at String,
    recorded_at DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
ORDER BY (book, application, business_date, revision, phase);

CREATE USER IF NOT EXISTS reporting_view_owner IDENTIFIED WITH no_password HOST NONE;
GRANT SELECT ON reporting_internal.close_revisions TO reporting_view_owner;

CREATE VIEW IF NOT EXISTS reporting_internal.energy_futures_close
DEFINER = reporting_view_owner SQL SECURITY DEFINER
AS
SELECT
    application,
    business_date,
    book,
    revision,
    tupleElement(report_row, 'contract') AS contract,
    tupleElement(report_row, 'currency') AS currency,
    tupleElement(snapshot, 2) AS status,
    tupleElement(snapshot, 3) AS event_cutoff,
    tupleElement(snapshot, 4) AS known_at,
    tupleElement(report_row, 'opening_units') AS opening_units,
    tupleElement(report_row, 'closing_units') AS closing_units,
    tupleElement(report_row, 'opening_pnl') AS opening_pnl,
    tupleElement(report_row, 'fill_pnl') AS fill_pnl,
    tupleElement(report_row, 'fees') AS fees,
    tupleElement(report_row, 'net_pnl') AS net_pnl,
    tupleElement(report_row, 'settlement_cash') AS settlement_cash
FROM
(
    SELECT
        application,
        business_date,
        book,
        max(src.revision) AS revision,
        argMax(tuple(report_rows, status, event_cutoff, known_at), src.revision) AS snapshot
    FROM reporting_internal.close_revisions AS src
    WHERE phase = 'published'
    GROUP BY application, business_date, book
)
ARRAY JOIN tupleElement(snapshot, 1) AS report_row;

GRANT SELECT ON reporting_internal.energy_futures_close TO reporting_view_owner;

CREATE VIEW IF NOT EXISTS oil.energy_futures_close
DEFINER = reporting_view_owner SQL SECURITY DEFINER
AS SELECT * FROM reporting_internal.energy_futures_close WHERE application = 'energy-futures' AND book = 'SYN-OIL';

CREATE VIEW IF NOT EXISTS gas.energy_futures_close
DEFINER = reporting_view_owner SQL SECURITY DEFINER
AS SELECT * FROM reporting_internal.energy_futures_close WHERE application = 'energy-futures' AND book = 'SYN-GAS';

CREATE ROLE IF NOT EXISTS reporting_publisher;
CREATE ROLE IF NOT EXISTS reporting_oil;
CREATE ROLE IF NOT EXISTS reporting_gas;

GRANT SELECT, INSERT ON reporting_internal.close_revisions TO reporting_publisher;
GRANT SELECT ON oil.energy_futures_close TO reporting_oil;
GRANT SELECT ON gas.energy_futures_close TO reporting_gas;

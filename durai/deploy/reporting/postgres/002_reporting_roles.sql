\getenv publisher_password REPORTING_PUBLISHER_PASSWORD
\getenv oil_password REPORTING_OIL_PASSWORD
\getenv gas_password REPORTING_GAS_PASSWORD
\getenv unassigned_password REPORTING_UNASSIGNED_PASSWORD
ALTER ROLE report_publisher PASSWORD :'publisher_password';
ALTER ROLE report_oil PASSWORD :'oil_password';
ALTER ROLE report_gas PASSWORD :'gas_password';
ALTER ROLE report_unassigned PASSWORD :'unassigned_password';

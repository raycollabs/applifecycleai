# Energy futures synthetic data

Use WTI crude oil and Henry Hub natural gas futures. Use two delivery months
and two synthetic trading desks. Treat prices, traders, and transactions as
test data.

## Prepare inputs

Run the exporter from the Durai folder. Select a new output directory.

```powershell
python databricks/datalab/prepare_energy_futures.py ./private/energy-futures-input
```

The exporter writes contracts, traders, bookings, price events, settlements,
and replay examples. Read the receipt for the seed digest and file digests.
The booking fields match the current trade-book request. For a price
correction, pass the earlier message key in the request field `corrects`.

## Connect DataLab

The installed Marketplace share exposes an approach document and screenshots.
The published notebook describes the workflow. Get the runnable DataLab app,
job, package, or API from the provider. Check its version, model settings,
and access terms. Use its relational mode with an isolated output schema.

Keep the test expectations in the receipt and replay cases. Check output
keys, price ticks, lots, correction links, and settlement cash before loading
the data into a disposable trade-book workspace.

## Request from chat

Register `chat_mcp.py` as a stdio MCP server in the chat client. Use the
workspace profile, warehouse ID, destination catalog, and trade demo project
as server arguments. Give the server access to the local Databricks CLI
profile and Docker engine.

```powershell
python databricks/datalab/chat_mcp.py --profile DEFAULT --warehouse WAREHOUSE_ID --catalog workspace --project durai-app-deepseek-r3
```

Send this request in chat.

> Prepare the energy-futures-v1 synthetic dataset in Databricks and populate the trading demo. Show me the Delta table and the number of new bookings, price events, and settlements.

The server publishes a versioned Delta table, verifies the rows, and passes
the data to the trade book. Use the same request again to verify replay.
This reference path uses Databricks SQL. Connect the DataLab runtime when
its runnable component is available.

To check the historical WTI spot benchmark, send this request:

> Check the installed FRED WTI spot reference. Show me its date range, latest price, and whether the data is stale.

The read-only tool reports dates, counts, the latest available price, and
age in calendar days. It marks a price older than seven days as stale.
Use contract prices for futures settlement.

## Close one futures book

Run the close command with the book, business date, prior business date,
and cutoff with a time zone. Read the exit code and the receipt.

```powershell
python tools/run_trading_close.py --project durai-app-deepseek-r3 --book SYN-OIL --business-date 2026-11-03 --prior-business-date 2026-11-02 --known-at 2026-11-25T23:59:00Z --receipt-directory ./private/close
```

The command reads one PostgreSQL snapshot. It uses USD futures trades and
contract marks from the settlement table. It reports fees and settlement
cash in separate fields. An incomplete close returns exit code 2. The
receipt lists the missing inputs. Run the command with the same cutoff
and inputs to get the same receipt.

# Generated synthetic exchange-position application

This application is a small ontology-bound reference environment. It books signed
lots, removes signed correction lots, and shows trade and net positions in shares.
It uses SQLite in memory to evaluate the bounded command history. It has no broker
integration, authentication, durable financial ledger, or production deployment.

Run `python -m unittest -v test_business.py` for the development examples.
Run `python server.py`, then open http://127.0.0.1:54430 for the trade UI.
Only run reviewed source on the host. Durai evaluates unreviewed candidates in
Docker with no network and no credentials. The UI binds loopback only and resets
its synthetic command history when the process stops. At most 100 commands are
accepted. A positive correction removes lots from the current position.

`implementation-brief.json` records domain semantics, pack pins, and design scope.
`manifest.json` seals every generated file. `evaluation.json` records independent
Docker results and the workflow run identity. Passing tests leave the candidate
waiting for human release review; this package does not authorize activation.

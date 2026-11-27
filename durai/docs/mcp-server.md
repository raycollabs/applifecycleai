# Durai governance MCP server

The server lets any MCP client ask Durai whether a tool call may go out, open a reviewer gate, read a gate state, and record an evidence receipt. It never approves a gate. A human reviewer approves in Slack or in the console.

## Tools

| Tool | Does | Writes |
| --- | --- | --- |
| `durai_check_call` | Checks one planned call against the egress list and the operator boundary. Returns `allow`, `deny` or `gate` with a reason. | Opens a gate when the host is unlisted, inside the boundary, and a gate store exists. |
| `durai_open_gate` | Opens a reviewer gate for one entry such as `example.com:443`. | A gate record. |
| `durai_gate_state` | Returns `WAITING`, `APPROVED`, `CANCELLED` or `NOT_OPEN`. | Nothing. |
| `durai_egress_check` | Says whether an entry is listed and inside the boundary. | Nothing. |
| `durai_receipt` | Records a result as a receipt. The receipt keeps digests of the arguments and the result, never the raw values. | An in-memory receipt. |

There is no approve tool. A `durai_runtime` approval is ignored by the gate. Only a `durai_reviewer` approval counts.

Resource (read only): `durai://egress/starter`. It returns the `sandbox.egress` record in force with its digest.

## Answers

| Decision | Reason | Meaning |
| --- | --- | --- |
| `allow` | `no-egress`, `egress-listed`, `gate-approved` | The call may go. |
| `gate` | `gate-waiting` | A reviewer must approve. Retry after approval. The answer has `workflow` and `subjectDigest`. |
| `deny` | `egress-not-listed`, `egress-ceiling-exceeded`, `egress-ceiling-error`, `egress-host-invalid`, `gate-cancelled`, `workflow-required`, `governance-error` | The call must not go. |

Any internal error gives `deny` with `governance-error`. The answer never holds exception text.

## Install

The server runs on the standard library plus the optional MCP SDK.

```
pip install "mcp==1.30.0"
```

The pin is also in `requirements-mcp.in`. Without the SDK the tool logic still works in Python. Only the stdio server needs it.

## Configuration (environment variables)

| Variable | Effect |
| --- | --- |
| `DURAI_EGRESS_TIER` | Tier of the starter record to use: `restricted` or `balanced` (default). |
| `DURAI_EGRESS_BOUNDARY` | Comma list of entries the operator allows at most, such as `api.deepinfra.com:443`. Turns on the prover check. |
| `DURAI_PROVER_CLI` | Command for `openshell-prover`. |
| `DURAI_GOVERNANCE_DB` | Database name for gates, for example `durai_test`. Without it no gate opens and an unlisted host is denied. |

## Client setup

### Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  durai-governance:
    command: python
    args: ["-m", "durai.mcp_server"]
    env:
      PYTHONPATH: "/path/to/durai"
      DURAI_EGRESS_TIER: "balanced"
```

### Claude Code

Run once in the project folder:

```
claude mcp add durai-governance --env PYTHONPATH=/path/to/durai -- python -m durai.mcp_server
```

Or write `.mcp.json`:

```json
{
  "mcpServers": {
    "durai-governance": {
      "command": "python",
      "args": ["-m", "durai.mcp_server"],
      "env": {"PYTHONPATH": "/path/to/durai"}
    }
  }
}
```

An MCP server only answers when the agent calls it. To make an agent call `durai_check_call` before each outgoing request, add a rule to the agent instructions. For hard enforcement use the Hermes plugin (Phase 4 of the plan) or the sandbox.

## Tests

- `tests/test_contract_governance.py` runs the shared port contract on `LocalGovernance` with fake gates and a fake prover.
- `tests/test_mcp_server.py` runs the tool logic with no SDK. With the SDK installed it also starts the server as a child process and calls it over stdio.
- No test uses a database, a network or a model.

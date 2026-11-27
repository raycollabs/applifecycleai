# Guide: Durai governance in Claude Code

Claude Code uses the MCP server. The server answers when the agent calls it. It does not block other tools.

## Install

1. Install Durai in a Python environment. Install the MCP SDK: `pip install "mcp==1.30.0"`.
2. Add the server. Run once in the project folder:

```
claude mcp add durai-governance --env PYTHONPATH=/path/to/durai -- python -m durai.mcp_server
```

Or write `.mcp.json` in the project folder:

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

## Configuration

Same variables as the Hermes guide. Set them in the `env` block.

| Variable | Effect |
| --- | --- |
| `DURAI_EGRESS_TIER` | `restricted` or `balanced` (default). |
| `DURAI_EGRESS_BOUNDARY` | The most the operator allows. Turns on the prover check. |
| `DURAI_PROVER_CLI` | Command for `openshell-prover`. |
| `DURAI_GOVERNANCE_DB` | Database name for gates. |

## Scripted check (no model call)

Run in the `durai` folder:

```
python -c "from durai.mcp_server import GovernanceTools, port_from_environment; t = GovernanceTools(port_from_environment()); print(t.call('durai_check_call', {'tool_name': 'fetch', 'arguments': {'url': 'https://example.com/x'}}))"
```

Expected: `decision` `deny` and reason `egress-not-listed`. In Claude Code, run `/mcp`. The server `durai-governance` must show five tools.

## What is enforced

Nothing is forced. The tools give answers: `durai_check_call`, `durai_open_gate`, `durai_gate_state`, `durai_egress_check`, `durai_receipt`. There is no approve tool.

To make the agent ask first, add a rule to `CLAUDE.md`: "Before any tool call that sends data to a host, call `durai_check_call`. Do not continue on `deny`. On `gate`, stop and tell the user."

## What is not enforced

- The agent can skip the check. A rule in `CLAUDE.md` is a request, not a lock.
- Claude Code tools such as Bash and WebFetch are not blocked by this server.
- A Claude Code `PreToolUse` hook that calls the same check can enforce. This guide does not include that hook. It is not built.

## How a reviewer approves

A human reviewer approves in Slack (see `docs/slack.md`) or in the console. Only a `durai_reviewer` approval counts. The agent reads the result with `durai_gate_state` and retries.

## Limits

- Receipts stay in memory in the server process. Use the Hermes plugin with `DURAI_RECEIPT_FILE` for a file.
- Without `DURAI_GOVERNANCE_DB` no gate opens. An unlisted host is denied.

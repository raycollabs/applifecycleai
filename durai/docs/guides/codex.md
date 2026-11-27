# Guide: Durai governance in Codex

Codex uses the MCP server. The server answers when the agent calls it. It does not block other tools.

## Install

1. Install Durai in a Python environment. Install the MCP SDK: `pip install "mcp==1.30.0"`.
2. Add the server to the Codex config file (`~/.codex/config.toml`):

```toml
[mcp_servers.durai-governance]
command = "python"
args = ["-m", "durai.mcp_server"]

[mcp_servers.durai-governance.env]
PYTHONPATH = "/path/to/durai"
DURAI_EGRESS_TIER = "balanced"
```

UNVERIFIED: the Codex config table names above come from general knowledge of Codex, and were not checked against the current Codex documentation for this guide. Check `mcp_servers` in the Codex docs before you rely on this block. The server itself is the same as in the Hermes and Claude Code guides.

## Configuration

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

Expected: `decision` `deny` and reason `egress-not-listed`.

## What is enforced

Nothing is forced. The tools give answers. Add a rule to the agent instructions (`AGENTS.md`): "Before any tool call that sends data to a host, call `durai_check_call`. Do not continue on `deny`. On `gate`, stop and tell the user."

## What is not enforced

- The agent can skip the check.
- Codex shell and network tools are not blocked by this server.
- Use the Codex sandbox settings, or the Durai sandbox, for hard limits.

## How a reviewer approves

A human reviewer approves in Slack (see `docs/slack.md`) or in the console. Only a `durai_reviewer` approval counts. The agent reads the result with `durai_gate_state` and retries. There is no approve tool.

## Limits

- Receipts stay in memory in the server process.
- Without `DURAI_GOVERNANCE_DB` no gate opens. An unlisted host is denied.

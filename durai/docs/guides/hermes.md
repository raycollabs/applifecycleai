# Guide: Durai governance in Hermes Agent

Two parts work together. The plugin enforces. The MCP server lets the agent ask.

| Part | Does | Enforces? |
| --- | --- | --- |
| Plugin `durai_governance` | Checks every tool call before it runs. Blocks denied calls. Writes digest-only receipts. | Yes, if the Hermes hook blocks (see "Verified and not verified"). |
| MCP server `durai-governance` | Lets the agent check a call, open a gate and read a gate state. | No. The agent must call it. |

Use the plugin for enforcement. Add the MCP server when the agent should open gates itself.

## Verified and not verified

Read from the Hermes source at commit `443ad89371a5da9843b2cae8b7f452aee594eba1` (see `report_hermes_plugin_spike_2026-11-26.md`). Hermes was not run for this guide.

| Item | State |
| --- | --- |
| `pre_tool_call` can block with `{"action": "block", "message": ...}` | Verified in source |
| A hook that times out blocks the call (30 s default) | Verified in source |
| A hook cannot wait for a human | Verified in source |
| Plugin folders load from `~/.hermes/plugins/` | Verified in source |
| The hook registration call is `ctx.register_hook(name, callback)` | UNVERIFIED. The plugin and its tests use this name. Check it against `hermes_cli/plugins.py`. |
| The `plugin.yaml` field names (`capabilities`, `hooks`, `env_vars`) | UNVERIFIED. Run `hermes plugins validate`. |
| The entry point group `hermes_agent.plugins` for pip installs | UNVERIFIED. Use the folder install. |
| A `None` return from `pre_tool_call` lets the call run | UNVERIFIED. Likely, and not checked. |
| A veto works with `plugins.isolation: host` | UNVERIFIED. See the manual check below. |

## Install (folder, main route)

1. Install Durai in the same Python environment as Hermes. The plugin imports `durai`.
2. Copy the folder `integrations/hermes/durai_governance` to `~/.hermes/plugins/durai_governance`.
3. Run `hermes plugins` and enable `durai_governance`.
4. Set the variables below in the Hermes environment.

Pip install (UNVERIFIED group name): `pip install durai-hermes-governance`. The wheel holds the same folder and declares the entry point `hermes_agent.plugins`. If Hermes does not find the plugin, use the folder install.

## Configuration

| Variable | Effect |
| --- | --- |
| `DURAI_GOVERNANCE` | `off` turns the plugin off. Calls pass and no receipt is written. Default is on. |
| `DURAI_EGRESS_TIER` | `restricted` or `balanced` (default). |
| `DURAI_EGRESS_BOUNDARY` | Comma list of the most the operator allows, such as `api.deepinfra.com:443`. Turns on the prover check. |
| `DURAI_PROVER_CLI` | Command for `openshell-prover`. |
| `DURAI_GOVERNANCE_DB` | Database name for gates. Without it, an unlisted host is denied and no gate opens. |
| `DURAI_RECEIPT_FILE` | Path of a JSON lines file for receipts. Without it receipts stay in memory only. |

MCP server config (optional), in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  durai-governance:
    command: python
    args: ["-m", "durai.mcp_server"]
    env:
      PYTHONPATH: "/path/to/durai"
```

## Scripted check (no Hermes, no model call)

Run in the `durai` folder. It calls the hook the way Hermes does.

```
python -c "import sys; sys.path.insert(0, 'integrations/hermes'); import durai_governance as p; print(p.pre_tool_call(tool_name='fetch', args={'url': 'https://example.com/x'}, session_id='S-1', tool_call_id='C-1'))"
```

Expected: a dict with `action` `block` and a message that names `egress-not-listed`. With `https://api.deepinfra.com/v1/x` the answer is `None`.

The test suite does the same with a fake ctx: `python -m unittest tests.test_hermes_plugin`.

## What is enforced

- A call to a host that is not on the egress list is blocked.
- A host outside the operator boundary is blocked before a gate opens.
- A call that needs a reviewer is blocked with the gate id. The agent retries after approval.
- Any error, a missing `durai` package or a check that takes longer than 5 seconds blocks the call.
- The plugin never returns `approve` or `modify`.

## What is not enforced

- Only URLs and host fields in the call arguments are checked. A tool that reaches the network some other way is not seen.
- A tool that runs code can still reach the network from inside that code. Use the sandbox for that.
- `DURAI_GOVERNANCE=off` turns everything off. Protect the Hermes environment.
- A Hermes setting that auto-approves does not affect a `block`. A `block` stays absolute (source, not tested).

## How a reviewer approves

The agent cannot approve. A reviewer approves the gate in Slack (see `docs/slack.md`) or in the console. Only a `durai_reviewer` approval counts. An approval from the runtime role leaves the gate waiting. After approval the agent retries the call and the plugin allows it.

## Manual check: veto under host isolation (UNVERIFIED, not run)

Do this once before you rely on the plugin with `plugins.isolation: host`.

1. In `~/.hermes/config.yaml` set `plugins.isolation: host`.
2. Start Hermes in a scratch profile with `DURAI_GOVERNANCE_DB` unset.
3. Ask the agent to run a tool call to `https://example.com/x`.
4. Expected: the call is blocked and the tool result starts with `Durai governance:`.
5. If the call runs, the veto does not cross the host boundary. Stop and use `in_process` isolation, or do not rely on the plugin.
6. Set `DURAI_GOVERNANCE=off` and repeat. The call must run. This shows the plugin made the difference.

## Limits

- Hermes core moves fast. Pin a Hermes version and run the scripted check after each update.
- The plugin keeps receipts in memory unless `DURAI_RECEIPT_FILE` is set.
- Catalog listing in Hermes needs a Nous maintainer to merge a pull request. This plugin is distributed standalone.

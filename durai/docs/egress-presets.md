# Egress presets, gate and audit export

Status: implemented 2026-11-26 (plan Part E and Part F). Plan: <plans>\plan_durai_compare_enhancer_and_sandbox_2026-11-26.md.

## 1. The egress record

A pack can hold one record with id `sandbox.egress` (layer DESIGN, type KNOWLEDGE). The body has `contentKind: "SANDBOX_EGRESS"`, a `revision`, `presets` and `tiers`.

```json
{"contentKind": "SANDBOX_EGRESS", "revision": 1,
 "tiers": {"restricted": [], "balanced": ["model-host"]},
 "presets": {"model-host": {"host": "api.deepinfra.com", "port": 443}}}
```

- A preset is a host, a port, and optional `access` (read-only, read-write, full), `protocol` (rest, tcp), `enforcement` (enforce, audit) and `binaries` (absolute paths).
- Tiers: `restricted`, `balanced`, `open`. A tier lists preset names. A name that has no preset is refused.
- `durai/egress_presets.py` checks the body, builds the record, reads it from an effective view, lists the allow-list of a tier and makes a new revision.
- A view without the record gives the starter body: tier restricted allows nothing, tier balanced allows only the model host. This is the strictest allowed baseline.
- The pack format is not changed. The record uses an allowed layer and type. The signature covers the record digest like every other record.
- `durai/egress_policy.py` turns a tier into the OpenShell policy. It refuses localhost, loopback, link-local, the metadata address, bare names and wildcards with fewer than three labels.

## 2. Gate

`durai/egress_gate.py` sends a new egress rule to the existing gate in `workflow_gates.py`.

1. An agent proposes a rule. `propose` opens a gate. The subject is the digest of the rule. The question names binaries, host, port, access and protocol.
2. Only an approval written by `durai_reviewer` counts. A `durai_runtime` approval is ignored. The deadline ends the gate as CANCELLED.
3. `apply` returns a new egress body with revision +1 for approved rules only. A waiting, rejected or cancelled rule changes nothing.
4. The result is a body, not a signed pack. A publisher signs the new revision. The agent cannot sign.

## 3. Audit export

`durai/ocsf_mapper.py` maps a `durai_governance.audit` row and a `durai_coordination.event` row to an OCSF 1.3 API Activity event (class 6003). It also reads OpenShell OCSF JSONL and returns a reference: the source, the digest of the bytes, the event count, the denied count and a count by class. The reference holds no log body. Keep the log in the evidence store.

## 4. Escape suite

`durai/sandbox_escape.py` and `tools/run_escape_suite.py <docker|openshell>` run seven scripted probes: writes outside scope, secret names, blocked network targets, fork bomb, memory hog, output flood and host files. After each probe the suite checks that the sandbox still answers. The receipt is `receipts/sandbox-escape-<adapter>.json`.

| Probe | Docker adapter | OpenShell adapter |
| --- | --- | --- |
| write_scope | blocked | blocked |
| secret_names | none | none |
| network_blocked (5 targets) | blocked | blocked |
| fork_bomb | stopped at 31 children (limit 32) | 300 children, limit is 2048 |
| memory_hog (512 MiB) | killed, exit 137 | killed, exit 137 |
| output_flood (10 MiB) | FAILED, kept 64 KB | FAILED, kept 64 KB |
| host_files | none readable | none readable |

The OpenShell process cap is `sandbox_pids_limit` in the Docker driver table of the gateway TOML. The default is 2048. Set a lower value to match the Docker adapter.
Scope of the receipts: a local container or OpenShell run on one machine. They are not an independent kernel-escape qualification. Model-driven escape attempts are not part of this suite.

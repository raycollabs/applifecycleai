# Durai workflow container (Phase A)

A workflow is an ordered list of steps. Each step runs one strategy and can have an approval gate before or after it. The strategies are SINGLE, SUBAGENTS and TEAM (fixed, decidedBy workflow-definition) and AUTO (chosen at step start, see "AUTO strategy"). An unknown value is refused. A fixed SUBAGENTS or TEAM step whose executor is not registered ends FAILED with no fallback; AUTO falls back to SINGLE.

## Definition format durai-workflow/v1

```json
{"format": "durai-workflow/v1", "id": "W-1",
 "steps": [
  {"id": "S-1", "goal": {"spec": {"...": "task spec"}}, "strategy": "SINGLE"},
  {"id": "S-2", "goal": "Book 2 lots", "strategy": "SINGLE",
   "gate": {"when": "before", "question": "Release S-2?", "deadlineSeconds": 3600}}]}
```

- `id` and step ids use letters, digits, dot, dash, underscore (1 to 160 characters). The workflow id is also the context store plan id.
- `gate.when` is `before`, `after`, or `none`. A gate needs `question` and `deadlineSeconds` from 1 to 604800.
- `durai.workflow_definition.validate(definition)` returns the normalized definition. `digest(definition)` returns one stable SHA-256 for equal content (defaults are filled in before hashing).

## Running

`durai.workflow_runner.build_workflow(definition, providers, database)` returns a LangGraph builder. `start_workflow(builder, workflow_id, database)` runs it and `resume_workflow(builder, workflow_id, database)` continues it from the PostgreSQL checkpoint (thread id = workflow id), also in a new process with a rebuilt builder. Each step resolves its executor with `providers.resolve('workflow-step', strategy)`. An executor is a callable `(step, context) -> {'state': SUCCEEDED|FAILED|CANCELLED|LOST, 'detail': ...}`. An exception or a result without a terminal state becomes FAILED. A step that is not SUCCEEDED stops the workflow, and the workflow takes that state. `DevLoopExecutor` wraps the development loop: loop outcome PASSED gives SUCCEEDED, any other outcome gives FAILED.

## Gates

At a gate the runner proposes a subject (a PROPOSED decision record in plan = workflow id) and opens a gate record (a progress record with the question and the deadline), then pauses with a LangGraph interrupt. The subject digest is the digest of the subject record.

The gate state is computed from the context store:

- APPROVED: an approval record whose author is `durai_reviewer`, whose `subjectDigest` equals the gate subject, whose decision is APPROVE, and that was written before the deadline.
- CANCELLED: the deadline passed without approval, or the reviewer wrote REJECT. A timeout never approves.
- WAITING: otherwise.

The database refuses an approval from any other login (PermissionError), and the gate check also requires the author to be `durai_reviewer`. A poller or an operator calls `resume_workflow` to re-check; a gate still WAITING pauses again, and a passed deadline cancels the step.

## Sweeping overdue gates

`sweep_gates(builder_for, database, now=None, clock=time.time)` in `durai.workflow_runner` finds every workflow whose latest event is GATE_OPENED and whose gate is no longer WAITING (deadline passed, or an approval or rejection exists), and resumes it. It returns the resumed workflow ids. A resumed workflow moves to CANCELLED or continues after APPROVED, so a second sweep resumes nothing. `builder_for(workflow_id)` returns the builder for that workflow or None to skip it. Run it from a scheduler, for example:

```
python -c "from durai.workflow_runner import sweep_gates; print(sweep_gates(my_builders.get, 'durai_local'))"
```

A cancelled after-gate keeps the step state (for example SUCCEEDED) and ends the workflow as CANCELLED; a cancelled before-gate ends the step as CANCELLED.

## AUTO strategy (Phase B)

A step with `"strategy": "AUTO"` is decided when the step starts, by the decision point `decision.execution-strategy` (`durai/strategy_select.py`). A fixed strategy skips the decision.

- Inputs from the step goal: `goal.spec.writePaths` (and optional `roles`), `goal.files` (path to text, used to find imports between write paths), `goal.budget.modelCalls` (remaining calls).
- Features: `moduleShape` ONE, COUPLED (a write path imports another write path), or LOOSE; `roleSplit`; `budgetLow` (remaining calls below 2 x `singleRunMedianModelCalls`, default 8); `riskClass` MONEY or STANDARD and `writePathCount` (recorded only).
- Rules in order: budget-low gives SINGLE; role-split gives TEAM; loose-modules gives SUBAGENTS; default gives SINGLE.
- Rules come from the pinned `decision.execution-strategy` PROCEDURE of a generic pack v15 binding (`design/engineering-philosophy-v15.json`). A v14 or older binding uses the local `DEFAULT_RULES`, which equal the v15 rules.
- Events: DECISION_RECORDED (point, choice, reasons, ruleDigest, featuresDigest, features, stepId), then STEP_STARTED with `strategy` (the strategy that runs) and `decidedBy`. When no SUBAGENTS or TEAM executor is registered, the runner records STRATEGY_FALLBACK (stepId, wanted, used SINGLE) and runs SINGLE.
- A resume re-reads the recorded events of the step and does not record a second decision or fallback.
- Install of the v15 pack into `durai_local` needs planner review and user approval; tests use `durai_test` only.

## Running a workflow

`tools/workflow.py` runs a workflow from the command line with the default providers (`durai/workflow_registry.py`, `default_providers(database, settings)`). The registry holds workflow-step SINGLE (`GoalDevLoop` over the development loop), SUBAGENTS (`SubagentsExecutor` with `DevLoopChild`), and TEAM (`TeamExecutor`, dev-loop members and a reviewer), plus the step acceptance `sandbox_acceptance`. Run it from `durai/` with `DURAI_SETTINGS` set:

```
python tools/workflow.py --database durai_local --accept-spend start --definition W-1.json
python tools/workflow.py --database durai_local --accept-spend resume --definition W-1.json
python tools/workflow.py --database durai_local --accept-spend sweep --definitions ./definitions
```

- Global options come before the subcommand: `--database` (default `durai_local`), `--accept-spend` (required, because the default model factory calls the configured paid coding model; without it the tool stops before any model call), `--scratch` (folder for candidates and workspaces), `--max-parallel` (subagents at once, default 2), `--lease-seconds` (TEAM lease length, default 900).
- `start` validates the definition, runs it, and prints one JSON line: `workflowId`, `status`, `steps`, `waitingAtGate`. A gate pauses the run (`waitingAtGate` true); approve it as described above, then run `resume` with the same definition file. The definition file must have the same content, because the gate subject carries its digest.
- `sweep` takes a folder with one `<workflowId>.json` per workflow and resumes every workflow whose gate is no longer waiting (see "Sweeping overdue gates"). It prints `{"resumed": [...]}`.
- Step goal for a SINGLE step: `{"spec": <task spec>, "files": {path: text}, "suite": {path: text}}`. `files` must have the manifest digest of `spec.sourceRevision` and `suite` that of `spec.protectedSuite`. The pins (source, contract, suite, image, runtime) are recomputed at run time, so a stale spec ends the step FAILED.
- Step acceptance (SUBAGENTS and TEAM): `goal.acceptance = {"spec": ..., "suite": {...}}`, or `goal.spec` and `goal.suite` when `acceptance` is absent. The merged or shared workspace goes through `SandboxEvaluator` (Docker image, no network, protected suite, comment policy against `goal.files`). The host Python never runs the candidate. The result is `{passed, detail, evidence}`; `evidence` is the verdict digest.
- Exit code is 0 after a run, 1 with a printed `{"error": ...}` for a refused definition or a missing file.

## Events (run id = workflow id)

WORKFLOW_STARTED, STEP_STARTED, STEP_FINISHED (state, detail, outcome), GATE_OPENED (subjectDigest, question, deadline), GATE_APPROVED, GATE_CANCELLED, WORKFLOW_FINISHED, plus DECISION_RECORDED and STRATEGY_FALLBACK for AUTO steps. Read them with `durai.execution_events.replay(workflow_id)`.

## Approving a gate as the reviewer

Read the subject digest from the GATE_OPENED event of the workflow, then run with the reviewer login configured in the local settings:

```
python -c "from durai.workflow_gates import approve; approve('W-1', '<subjectDigest>', 'Approved for release of S-2.')"
```

Use `decision='REJECT'` to cancel the gate. Add `database='durai_test'` for the test database. Then call `resume_workflow` to continue the workflow.

## Dashboard

A read-only web dashboard shows workflows, gates, and benchmark results. It listens on 127.0.0.1 only, answers GET only (any other method gets 405), and reads with the `durai_runtime` login in READ ONLY transactions. It cannot approve a gate. Responses never contain keys named like apiKey, password, token, or secret.

Start it (prints the URL, never credentials):

```
python tools/dashboard.py --port 54430
```

Add `--database durai_test` for the test database. Open `http://127.0.0.1:54430/` in a browser. The page sets a session cookie; the JSON endpoints return 401 without it. The page polls every 10 seconds. Tabs and endpoints:

- Workflows (`/api/workflows`): workflow ids with a WORKFLOW_STARTED event, newest 100. Shows state (RUNNING, WAITING_GATE, or the WORKFLOW_FINISHED state), each step with its state (PENDING, RUNNING, or its terminal state), start and finish times.
- Pending gates (`/api/gates`): the latest GATE_OPENED of a workflow with no later GATE_APPROVED, GATE_CANCELLED, or WORKFLOW_FINISHED. Shows workflow, step, subject digest, question, deadline, waiting seconds, and the exact reviewer command from "Approving a gate as the reviewer" to run in a terminal.
- Runs (`/api/runs`): the newest 200 imported dev-loop runs with label, case, model profile, outcome, pass attempt, calls, and wall seconds.
- Scorecard (`/api/scorecard`): rows of `durai_bench.model_scorecard`.
- Experiments (`/api/experiments`): per experiment the `compare()` counts: pairs, both pass, treatment only, baseline only, both fail.
- Decisions (`/api/decisions`): DECISION_RECORDED events of `decision.execution-strategy` and STRATEGY_FALLBACK events, newest 200, with choice, wanted strategy, rule, rule digest, and features.

## SUBAGENTS strategy

An AUTO step whose write paths form 2 or more loosely coupled modules runs the SUBAGENTS executor (`durai/strategy_subagents.py`, registered as `workflow-step` `SUBAGENTS`). Register it with `register_subagents(providers, SubagentsExecutor(child, acceptance=...))`. Without that registration the runner still records STRATEGY_FALLBACK and runs SINGLE.

- Split: write paths are grouped by module (`journal.py` and `test_journal.py` form one module). Each subtask gets its own `writePaths` and `editScope` subset. Subtasks are ordered by first write path (`sub-1`, `sub-2`, ...). Overlapping scopes, or fewer than 2 modules, end the step FAILED with `SPLIT_REFUSED` before any child runs. `goal.subtasks` may map a module (for example `/workspace/journal`) to spec fields that differ per subtask (taskId, deliverables, protectedSuite, sourceRevision).
- Run: each subtask runs on its own workspace through the child executor (constructor argument `child`; `DevLoopChild` wraps the SINGLE `DevLoopExecutor` and restores the retained package). The workspace factory defaults to a folder copy of the base files (`goal.files`). `max_parallel` (default 2) limits the thread pool. `timeout` and `clock` bound the wait; a timeout or an exception gives LOST. The step budget `goal.budget.modelCalls` is divided by the subtask count; each child sees its share in `context['budget']`.
- Verify: only a SUCCEEDED child with a package (files plus digest) is eligible. Any other state blocks the merge and the step ends FAILED (or CANCELLED when every blocker is CANCELLED).
- Merge: the orchestrator applies packages in the fixed order on a merge workspace. A path outside the subtask scope, a path already changed by an earlier package, or a base digest that differs gives MERGE_CONFLICT. Then nothing is merged and the step is FAILED. There is no automatic conflict resolution.
- Acceptance: the `acceptance(workspace, step, context)` callable runs once on the merged workspace. Only a passing result gives SUCCEEDED. There is no default acceptance; the default refuses.
- Events (run id = workflow id, each with stepId): SUBAGENT_STARTED (subagentId, module, writePaths), SUBAGENT_FINISHED (subagentId, state, packageDigest, usage, budget), MERGE_APPLIED (subagentId, packageDigest, paths), MERGE_CONFLICT (subagentId, path, reason), ACCEPTANCE_FINISHED (passed, mergedDigest).
- Dashboard: the Agents tab (`/api/agents`, read-only, same scrub and CSP) lists one row per subagent with state, module, package digest, budget, usage, merge state, and acceptance result.

## TEAM strategy

An AUTO step whose task lists `roles` (rule role-split) runs the TEAM executor (`durai/strategy_team.py`, registered as `workflow-step` `TEAM`). Register it with `register_team(providers, TeamExecutor(members, acceptance=..., leases=FileLeases(database)))`. `members` is one executor for every member, or a dict by role or member id. Without the registration the runner records STRATEGY_FALLBACK and runs SINGLE.

- Roles: `goal.spec.roles` is a list of role names or dicts `{role, memberId, writePaths}` (or a dict by role). Up to 3 members. Templates: `implementer` (production files), `test-writer` (test files), `reviewer` (no write leases, starts after the writers). Without `writePaths`, test files go to the test writer and the other write paths to the implementer. Unknown roles, more than 3 members, overlapping paths, or a reviewer with paths end the step FAILED with `TEAM_REFUSED`.
- Leases: table `durai_coordination.file_lease` (migration `core_0018.sql`), class `durai/file_leases.py` `FileLeases(database, role, clock)`. One HELD row per (worktree, path). `claim`, `renew`, `release`, `transfer`, and `check` are transactional and compare the generation. An expired lease is LOST (recorded on the next `check`, `renew`, `release`, `transfer`, or `claim`) and its holder must claim again. `transfer` releases the old row and creates generation + 1 in one transaction. Rows are never deleted. A reviewer role cannot hold a lease.
- Edit guard: `EditGuardMiddleware(..., leases=(FileLeases, worktree, owner))` refuses `write_file` and `edit_file` without a live lease of the owner (LEASE_REQUIRED or LEASE_LOST). Without `leases` the guard is unchanged. Reads are free.
- Run: the worktree id is `<workflowId>/<stepId>`. Every writer claims its leases before any member starts; a claim conflict ends the step FAILED with `LEASE_CONFLICT` and no member runs. Members share one workspace through a leased wrapper (a write needs a live lease; reads are free). A member sees `context['workspace']`, `context['leases']` (`paths()`, `renew()`, `transfer(path, member)`), `context['role']`, `context['prompt']`, and its share of `goal.budget.modelCalls`. Writers run in parallel; the reviewer runs only when every writer SUCCEEDED, otherwise it is CANCELLED. Each member ends in SUCCEEDED, FAILED, CANCELLED, or LOST (exception). A member releases its leases when it ends.
- Acceptance: the `acceptance(workspace, step, context)` callable runs once on the shared workspace after every member SUCCEEDED. Only a passing result gives SUCCEEDED.
- Events (run id = workflow id, each with stepId and memberId): TEAM_MEMBER_STARTED, TEAM_MEMBER_FINISHED (state, usage, budget), LEASE_CLAIMED, LEASE_RELEASED, LEASE_TRANSFERRED (fromMemberId), LEASE_LOST, ACCEPTANCE_FINISHED (memberId orchestrator).
- Dashboard: the Agents tab lists team members (module column shows the role, merge shows SHARED) and a second table of file leases from `/api/leases` (read-only, same scrub and CSP).
- Install: `core_0018.sql` is applied to durai_local only after review, with `python -c "from durai.db import prepare; prepare('durai_local')"` (login postgres, from `durai/` with `DURAI_SETTINGS` set).
- Dev-loop members (Phase G): `DevLoopExecutor(..., external=ExternalRuns(), leases=FileLeases)` passes the shared workspace and `(FileLeases, worktree, memberId)` of a team member through the loop to `DeepAgentsWorker(workspace=, leases=)`. The worker gives the leases to the edit guard and writes the changed write paths of a passing candidate into the shared workspace (a write needs a live lease). A loop run marked external whose workspace is missing ends BLOCKED with `LEASE_REQUIRED`. Member specs come from `goal.members[memberId]` (fields merged into the spec; `roles` is removed) and member suites from `goal.suites[memberId]`. The reviewer is a deterministic read-only member (`ReviewerMember`): it fails when the shared workspace holds no change.

## Hardening (Phase H)

- Lease heartbeat: `FileLeases.heartbeat(lease, every, stop, seconds, on_lost)` is a daemon thread that renews the lease every `every` seconds (TEAM uses `leaseSeconds / 3`). `TeamRun` starts one per member lease and stops it on release, transfer, and member end. A refused renew sets a flag for the member; the leased workspace then refuses every further write with `LEASE_LOST`.
- Durable team resume: with `TeamExecutor(..., scratch=folder)` (the default registry sets `<scratch>/teams`) the shared workspace is a disk folder `<scratch>/teams/<workflowId>/<stepId>/team` (`durai/workspace_disk.py`). TEAM_WORKSPACE_CREATED records the path and the base digest. After a process restart call `recover_workflow(builder, workflowId, database)` (or run the step again). The step then re-opens the folder (TEAM_WORKSPACE_REOPENED), skips members whose last TEAM_MEMBER_FINISHED is SUCCEEDED, checks the leases of the others (TEAM_RESUMED lists LIVE, LOST, or NONE per path; expired leases are marked LOST), and the members claim again with a new generation. A missing folder, a missing marker, a missing base file, or another base digest ends the step FAILED with `WORKSPACE_LOST` and a WORKSPACE_LOST event. The folder is removed when the step ends. A member that was in the middle of a dev-loop run restarts that run from the beginning of the member step.
- Git workspace: `GitWorktreeWorkspace(repo, base_commit, name, scratch, base)` (`durai/workspace_git.py`) adds a detached worktree with `git -c safe.directory=* worktree add --detach`, checks that the commit holds the step base files (`BASE_MISMATCH` otherwise), and removes it on `close()` with `worktree remove --force`. `goal.workspace` is `"folder"` (default) or `"git"` (or `{kind: git, repo, baseCommit}`); SUBAGENTS children and the merge workspace use it. Register the defaults with `default_providers(settings={'git': {'repo':..., 'baseCommit':..., 'scratch':...}})`. Any other value gives `WORKSPACE_REFUSED`.
- Model-backed reviewer: `ReviewerMember(model, policies)` sends the reviewer role prompt, the blueprint rules (coding-worker policies), the step objective, and the unified diff of the shared workspace against the base to `model.invoke`. It binds no tools and holds no lease. The reply must be strict JSON `{"verdict": "APPROVE" | "CHANGES", "findings": [...]}`; anything else fails closed. CHANGES fails the step and the step detail lists the findings. `default_providers(..., reviewer_model=model)` turns it on; without a model the deterministic reviewer stays.
- Parallel SUBAGENTS: tests run two real dev loops at the same time through `default_providers` (`maxParallel` 2). The scripted model factory must choose its script by task id (from the prompt), not by attempt number.

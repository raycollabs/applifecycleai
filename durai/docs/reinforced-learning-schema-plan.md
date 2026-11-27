# Durai Internal Reinforced Learning (RL) Schema & Closed-Loop Architecture

## 1. Objective & Design Invariants

Every Durai run (`S-REV` → `DESIGN` → `PLAN` → `IMPLEMENTATION` → `TESTS` → `DEPLOYED` → `P-REV`) produces telemetry, failures, smell counts, and reviewer findings. Today those signals sit in per-run JSON receipts and `durai_bench` / `durai_improvement` tables without a closed feedback pipeline back into prompts, guidance rules, and model routing.

The **Internal Reinforced Learning (RL) Store** closes that loop with five non-negotiable invariants:
1. **Verified Reward Signal Only:** Per `bench_0012` (`build_score_guard`), rewards may only be computed from **executed checks** (pytest/vitest/Playwright/`check_sign_in`), never unverified claims.
2. **Human Gate Before Promotion:** Agents may cluster failures, draft candidate `DO NOT` rules, and open tickets, **never** self-approve prompt or pack changes into production (`PROPOSED → APPROVED` requires a human reviewer).
3. **Privacy-Preserving Ticket Telemetry:** Local runs record full workspace context in `durai_local`. Public/customer installations submit **anonymized, schema-only tickets** (error codes, stop reasons, pack digests, sanitized AST smell kinds, cost/token deltas—never customer source code, prompts, or secrets).
4. **Rollback-Safe Publishing via PackRegistry:** Approved improvements compile into versioned, Ed25519-signed `durai.lessons` / `durai.starter` pack updates (`durai-ontology-pack/v2`), propagating via `PackFeedClient` and `EffectiveView`.
5. **PostgreSQL First:** Core RL state, tickets, rewards, and routing stats live in PostgreSQL 17 (`durai_improvement` / `durai_rl`); export to Superset read replica or ClickHouse/Delta (`AnalyticsSinkPort`) only when query concurrency requires it.

---

## 2. Database Schema Extension (`migrations/improvement/rl_0001.sql.proposed`)

Builds alongside `durai_bench`, `durai_prompt`, and `durai_improvement.finding`:

| Table | Purpose | Key Columns & Guards |
|---|---|---|
| `durai_improvement.execution_episode` | One immutable RL episode per component or composite task run | `episode_id`, `run_id`, `sample_id`, `component_category`, `shell_profile`, `model_id`, `provider`, `effective_view_digest`, `stop_reason`, `calls_used`, `input_tokens`, `output_tokens`, `list_cost_usd`, `enterprise_cost_usd`, `passed_checks`, `failed_checks`, `smell_penalty`, `reward_score` (`CHECK (passed_checks + failed_checks > 0 OR reward_score <= 0)`) |
| `durai_improvement.episode_signal` | Step-level friction signals inside an episode | `signal_id`, `episode_id`, `step_index`, `signal_kind` (`EDIT_MISMATCH`, `PLAN_GATE_REFUSAL`, `GET_CHANGES_COOKIE`, `STALL_NUDGE`, `CHECK_APP_FAIL`, `SMELL_HIGH`), `tool_name`, `rule_id`, `detail_digest` |
| `durai_improvement.ticket` | Improvement tickets submitted locally (`durai_local`) or via public intake API | `ticket_id`, `source_mode` (`LOCAL_DURAI`, `PUBLIC_SHELL`), `client_instance_hash`, `pack_id`, `pack_version`, `shell_version`, `category` (`TOOL_FRICTION`, `WEB_RULE_GAP`, `SQL_BLUEPRINT_GAP`, `UI_UX_DEFECT`, `PROMPT_AMBIGUITY`, `ROUTING`), `severity`, `fingerprint` (`UNIQUE` dedup), `summary`, `sanitized_payload` (jsonb), `occurrence_count`, `status` (`OPEN`, `TRIAGED`, `CANDIDATE_LINKED`, `FIXED_PENDING_EVAL`, `RELEASED`, `WONT_FIX`) |
| `durai_improvement.policy_candidate` | Proposed fix derived from a ticket cluster | `candidate_id`, `cluster_fingerprint`, `target_kind` (`LESSON_RULE`, `GUIDANCE_TOPIC`, `WEB_STATIC_RULE`, `SHELL_TEMPLATE`, `MODEL_ROUTE`), `target_ref`, `proposed_patch`, `status` (`DRAFT`, `PROPOSED`, `APPROVED`, `REJECTED`, `SUPERSEDED`), `proposed_by` (agent ok), `approved_by` (human only via DB trigger/allowlist), `approved_at` |
| `durai_improvement.policy_evaluation` | Shadow replay & blind A/B benchmark proof before release | `eval_id`, `candidate_id`, `baseline_episode_id`, `treatment_episode_id`, `delta_pass_rate`, `delta_calls`, `delta_tool_errors`, `delta_list_cost_usd`, `decision` (`PASS_READY`, `REGRESSION`, `INCONCLUSIVE`) |
| `durai_improvement.model_route_stats` | Rolling contextual bandit / empirical routing table | `(component_category, shell_profile, model_id, provider, pack_version)`, `episodes_count`, `finish_rate`, `avg_calls`, `avg_list_cost_usd`, `mean_reward`, `updated_at` |

### Reward Function Definition
To avoid gaming (e.g., skipping checks or bloating files), compute `reward_score` deterministically from executed metrics:

```text
reward_score =
  + 1.00 * (stop_reason == 'FINISHED')
  + 0.50 * (passed_checks / max(passed_checks + failed_checks, 1))
  - 0.05 * tool_error_count
  - 0.15 * stall_nudges
  - 0.10 * high_smells - 0.02 * low_smells
  - 0.25 * log1p(list_cost_usd * 10)
```

If `passed_checks + failed_checks == 0`, reward is capped `<= 0` (enforced by database `CHECK`).

---

## 3. How Each Execution Drives Continuous Improvement (7-Step Pipeline)

### Step A — During Execution: Emit Structured Signals
- `durai/build_driver.py` and `durai/build_tools.py` emit fine-grained events into `episode_signal` whenever a guard fires (`Plan gate refusal`, `edit_file` mismatch, `STOP_STALL` nudge, `check_web_rules` violation, `DEPLOYED` `check_app` rejection, `smell_scorer` HIGH finding).

### Step B — End of Run: Record Episode & Submit Ticket (Local + Public)
1. **Compute Episode Metrics:** Aggregate `build_driver` trace + `smell_scorer` + `check_app` + `model_pricing` into `durai_improvement.execution_episode`.
2. **Auto-File Ticket on Friction:** If `stop_reason != 'FINISHED'` OR `tool_errors >= 2` OR `web_rules` triggered OR `smell_high >= 1` OR user flags a post-deploy UI/UX issue:
   - Compute deterministic `fingerprint = sha256(category + rule_id + component_category + shell_version)`.
   - **Local Mode (`durai_local`):** Insert/increment `occurrence_count` directly in `durai_improvement.ticket`.
   - **Public Site Mode (Planned Open-Core Intake):** Serialize through `PrivacySanitizer` (strips workspace file contents, paths, secrets, and user overlays; keeps only `fingerprint`, `pack_id`, `pack_version`, `category`, `signal_kinds`, and synthetic repro tags) and queue via local outbox (`EventPort`) to `POST https://tickets.durai.io/v1/intake`.

### Step C — Periodic Triage Worker (Local or Hosted)
- Reads `status = 'OPEN'` tickets ordered by `occurrence_count DESC, severity DESC`.
- Runs `durai/lesson_proposals.py` (`cluster_failures`) to map recurring fingerprints into concrete `policy_candidate` drafts:
  - Example 1 (`GET_CHANGES_COOKIE` / `PREFETCH_CHANGED_COOKIE` seen 3+ times) → draft a `GUIDANCE_TOPIC` rule + `build_web_rules.py` static check + Server Action example.
  - Example 2 (`edit_file` mismatch on Next.js Server Action imports) → draft a `LESSON_RULE` reminding builders to read `app/actions.ts` exports or inspect full symbol signatures first.
  - Example 3 (`409 Conflict` crashing Next.js ErrorBoundary with React #441) → draft a `UI_UX` rule requiring `useActionState` inline alerts for `4xx` domain errors instead of unhandled Server Action `throw`.

### Step D — Automated Shadow Replay & Proof Gate
- Before a human even reviews the candidate, run a verification pass (`policy_evaluation`):
  1. Replay failing trace inputs (`tools/replay_trace.py`) against the updated static check / rule to confirm the new rule catches the historical defect.
  2. (When model spend is approved) Run `tools/run_blind_build.py` on the affected sample component with the candidate `EffectiveView` and compare `delta_calls`, `delta_tool_errors`, `delta_pass_rate`, and `delta_list_cost_usd`.

### Step E — Human Review & Approval
- Present queued `PROPOSED` candidates + `policy_evaluation` diffs in the Durai Governance Dashboard (or CLI review tool).
- Human operator marks `APPROVED` (DB trigger rejects agent identities).

### Step F — Compile, Sign & Distribute via Pack Feed
- `tools/build_pack.py` compiles `APPROVED` lessons and updated guidance/blueprints into a new signed pack version (e.g., `durai.starter` `1.0.1` or `durai.lessons` feed entry).
- Pull clients (`PackFeedClient.sync()`) install idempotently (`PostgresRegistryStore`), invalidate `EffectiveView`, and immediately feed the improved ontology/context into subsequent DeepSeek runs!

### Step G — Contextual Model Routing Feedback
- `model_route_stats` refreshes on every `execution_episode` commit.
- `durai/model_pricing.py` / router queries `(component_category, shell_profile)` to select the cheapest model achieving `finish_rate >= threshold` (e.g., routing standard CRUD/React shell screens to `DeepSeek-V4.1-Flash` once ontology lift passes 95%, while surfacing low-finish categories as top-priority RL tickets).

---

## 4. Phased Implementation Roadmap (Concrete Code Tasks)

| Phase | Deliverables | Verification |
|---|---|---|
| **RL-1: Schema & Store Adapter** | Finalize `migrations/improvement/rl_0001.sql.proposed` (review & apply to `durai_local` only with explicit approval). Add `durai/rl_store.py` (`RLStore` protocol + `InMemoryRLStore` + `PostgresRLStore`). | Contract tests on `InMemoryRLStore` & disposable `postgres:17-alpine` verifying reward guard and idempotent ticket fingerprinting. |
| **RL-2: Episode & Ticket Collector** | Add `durai/ticket_collector.py` with `PrivacySanitizer` (strips paths/secrets/code) and wire post-run hook in `tools/run_blind_build.py` + `tools/product_review.py` (`P-REV`). | Unit tests verifying private paths/tokens are stripped in `PUBLIC_SHELL` mode and duplicate failures increment `occurrence_count` instead of creating duplicate rows. |
| **RL-3: Triage & Evaluation CLI** | Add `tools/triage_tickets.py` bridging `durai_improvement.ticket` → `lesson_proposals.draft_proposal()` → `policy_candidate` + shadow replay verification (`policy_evaluation`). | End-to-end local test: simulate 3 failing `STOP_STALL` / React #441 episodes → ticket filed → candidate drafted → human approval enforced (agent approval raises error). |
| **RL-4: Pack Feed & Routing Loop** | Extend `tools/build_pack.py` to include `APPROVED` lessons in `durai.starter` / `durai.lessons` feed; add `durai/model_router.py` reading `model_route_stats`. | Test that installing updated signed pack invalidates `EffectiveView` and surfaces the new `DO NOT` rule in the next worker prompt. |
| **RL-5: Public Site Intake Service** | Small stateless FastAPI endpoint `POST /v1/tickets` + signed org/instance token rate limiting + `GET /v1/feed` hosted pack feed + Superset dashboard over PG read replica (`durai_improvement`). | Integration test checking unauthenticated / oversized / raw-code payloads are rejected at public edge while sanitized metrics persist cleanly. |

---

## 5. Orchestrator + X Subagents Execution Plan

To implement RL-1 through RL-5 safely in parallel without merge conflicts or unapproved shared edits, use **1 Orchestrator** and **4 isolated worktree subagents** (`S1`–`S4`):

### Topology & File Ownership

| Role | Worktree / Branch | Owns (Exclusive Files) | Depends On |
|---|---|---|---|
| **Orchestrator (Lead)** | Integrated branch (`durai-rl-integrated`) | Shared hooks (`durai/build_driver.py`, `tools/run_blind_build.py`, `tools/build_pack.py`), `design/open-core-boundary-v1.json`, `durai_local` approval/migration, final integration & scans | Merges `S1`..`S4` |
| **Subagent S1 (`durai-rl-store`)** | `durai-rl-s1-store` | `migrations/improvement/rl_0001.sql.proposed`, `durai/rl_store.py`, `durai/adapters/rl_store/postgres.py`, `tests/test_rl_store.py` | Contract defined in Section 2 |
| **Subagent S2 (`durai-rl-collector`)** | `durai-rl-s2-collector` | `durai/privacy_sanitizer.py`, `durai/ticket_collector.py`, `tests/test_ticket_collector.py` | Pure JSON receipt input (`RLStore` interface) |
| **Subagent S3 (`durai-rl-triage`)** | `durai-rl-s3-triage` | `tools/triage_tickets.py`, `durai/policy_eval.py`, `tests/test_triage_tickets.py` | `lesson_proposals.py`, `replay_trace.py`, `smell_scorer.py` |
| **Subagent S4 (`durai-rl-delivery`)** | `durai-rl-s4-delivery` | `durai/intake_api.py`, `durai/model_router.py`, `migrations/improvement/rl_superset_views.sql.proposed`, `tests/test_intake_and_router.py` | `RLStore` & `PackRegistry` interfaces |

*(Optional scaling: if `S3` clusters 5+ independent ticket categories—e.g., React UI rules, SQL blueprints, Python tool errors—the Orchestrator can spawn `1` evaluation subagent per candidate patch against isolated `build_run_node` containers.)*

---

### Execution Waves

#### Wave 0 — Setup & Interface Freeze (Orchestrator, ~5 min)
1. Tag/freeze the typed Python protocol `RLStore` (`record_episode`, `append_signal`, `upsert_ticket`, `create_candidate`, `approve_candidate`, `record_evaluation`, `get_route_stats`) and `migrations/improvement/rl_0001.sql.proposed`.
2. Spin up 4 isolated git worktrees from `HEAD`:
   - `git worktree add -b durai-rl-s1-store ...`
   - `git worktree add -b durai-rl-s2-collector ...`
   - `git worktree add -b durai-rl-s3-triage ...`
   - `git worktree add -b durai-rl-s4-delivery ...`
3. Dispatch `S1`, `S2`, `S3`, and `S4` in parallel.

#### Wave 1 — Parallel Build & Unit Verification (`S1`–`S4` concurrently, zero model spend)
- **Subagent S1:**
  1. Validate `rl_0001.sql.proposed` against a disposable `postgres:17-alpine` container (`com.durai.owner=ports-test`).
  2. Implement `InMemoryRLStore` + `PostgresRLStore` (using `ON CONFLICT (fingerprint) DO UPDATE SET occurrence_count = occurrence_count + 1`).
  3. Pass contract tests verifying: reward guard refuses unverified episodes (`passed+failed=0`), DB check refuses agent self-approval (`approved_by == proposed_by`), restart persistence.
- **Subagent S2:**
  1. Implement `compute_reward(receipt, smells)` and `extract_signals(trace_jsonl, web_rules, smells)`.
  2. Implement `PrivacySanitizer` stripping source code, paths, user overlays, and secrets before `PUBLIC_SHELL` serialization.
  3. Test duplicate error fingerprinting and secret-leak rejection.
- **Subagent S3:**
  1. Implement `tools/triage_tickets.py` querying `OPEN` tickets, grouping clusters, and writing `policy_candidate` rows via `lesson_proposals.py`.
  2. Implement `durai/policy_eval.py` running `replay_trace.py` + `build_web_rules` + `smell_scorer` checks against historical failure clusters to populate `policy_evaluation`.
  3. Verify `approve_candidate` refuses non-human identities.
- **Subagent S4:**
  1. Implement `durai/intake_api.py` (stateless FastAPI `POST /v1/tickets` with signed instance token check and strict JSON schema rejecting raw code blocks).
  2. Implement `durai/model_router.py` ranking `(model_id, provider)` by `mean_reward` and `avg_list_cost_usd` from `model_route_stats`, with fallback to `durai/model_pricing.py`.
  3. Add read-only SQL views in `rl_superset_views.sql.proposed` targeting a PostgreSQL read replica for Superset dashboards (ClickHouse escalation only when needed).

#### Wave 2 — Orchestrator Integration & Local Gate (Orchestrator)
1. Merge `S1` → `S2` → `S3` → `S4` cleanly into `durai-rl-integrated` (resolve cross-module imports).
2. Wire shared hooks in Orchestrator-owned files:
   - Post-run episode + ticket submission hook in `tools/run_blind_build.py` and `tools/product_review.py` (`P-REV`).
   - Include `APPROVED` RL lesson candidates inside `tools/build_pack.py` so `PackFeedClient.sync()` ships them to `EffectiveView`.
   - Classify all new files in `design/open-core-boundary-v1.json`.
3. **Verification Gate:** Run full unittest suite (`S1`–`S4` + A1/A2/A3 regression tests), `tools/boundary_scan.py ..` (`0` unclassified), `tools/secret_scan.py --public-only ..` (`0` findings), and verify `docker ps -a --filter label=com.durai.owner=ports-test` is empty.
4. **Database Approval Gate:** Ask user for explicit approval before applying `rl_0001.sql.proposed` to `durai_local`.

#### Wave 3 — Controlled Reinforcement Proof (Orchestrator + Eval Runners, user spend approval required)
1. Run `tools/run_blind_build.py` (DeepSeek-V4.1-Flash-1017 on `design/samples/checkout-v1.json` + `trading-v1.json`) to populate live `execution_episode`, `ticket`, and `model_route_stats` rows in `durai_local`.
2. Run `tools/triage_tickets.py`, human-approve top candidate rule(s), rebuild signed pack via `tools/build_pack.py`, sync via `PackFeedClient`, and re-run to verify positive `policy_evaluation` lift (`delta_calls`, `delta_tool_errors`, `delta_list_cost_usd`).


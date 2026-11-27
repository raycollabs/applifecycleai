# Three-Sample Benchmark Comparison: Baseline vs. Bare Control vs. Durai Agent

**Date:** 2026-11-26  
**Workspace:** `D:\plans\durai-checkout-sample-20261125\durai`  
**Database Authority:** `durai_local` (PostgreSQL 17 on `127.0.0.1:54410`, schema `durai_improvement` active)  
**Primary Execution Default:** `reflection` (`Beam-1.0-501B-A23B`, committed `7d7765c`) with automated failover to `relace` / DeepSeek routes

---

## 1. Executive Summary Across All Three Samples

| Metric | Sample 1: Trade-Pilot (`S-TRADE-*`) | Sample 2: Checkout & Ledger (`S-CHK-*`) | Sample 3: Seat Reservations (`S-RES-*`) |
|---|---|---|---|
| **Baseline Status** | Complete (Round 3 reference on `:54370`) | **Complete & Live** on `http://127.0.0.1:54390/` (`7/7` tests) | **Complete & Live** on `http://127.0.0.1:54391/` (`10/10` tests) |
| **Sample Catalog Components** | `6` (`BOOK`, `ACCESS`, `PRICES`, `PNL`, `CASH`, `SCREENS`) | `6` (`INVENTORY`, `INTENT`, `FUNDS`, `LEDGER`, `ACCESS`, `SCREENS`) | `6` (`SEATS`, `HOLD`, `CONFIRM`, `CANCEL`, `ACCESS`, `SCREENS`) |
| **Bare Control (`deepseek-flash`)** | `0/3` finished (`117` calls, `STOP_COST`, `16/0` tests, `$10.00` ent / `$0.64` list) | `0/3` finished (`94` calls, `STOP_COST`, `6/1` tests, `$6.02` ent / `$0.39` list) | `0/3` finished (`116` calls, `STOP_COST`, `7/6` tests, `$6.08` ent / `$0.39` list) |
| **Durai Agent (`DeepSeek-V4-Flash` / Failover / `Reflection`)** | **`6/6 FINISHED`** (`R3`: `1,132` calls, `244 Py + 45 TS` tests, `$115.73` ent / `$10.14` list) | **`5/6 FINISHED`** (`chk-durai-v3`: `181` calls, `$12.63` ent / `$0.78` list; UI reached `82/0` tests in `chk-reflection-s6`) | **`6/6 FINISHED + DEPLOYED`** (`res-durai-v3` + `s6`: `53/0` tests, `$19.10` ent / `$1.14` list incl. `s6`) |
| **Durai vs. Bare Structural Delta** | Gates, manifest & multi-pass completion | `5` backend components `FINISHED` on pass 1 (`66/0` tests) vs `0` finished in bare (`6/1`) | All `6` components `FINISHED` & `DEPLOYED` (`53/0` tests) vs `0` finished in bare (`7/6` failing) |

> **Provider Note:** Direct `deepseek` (`api.deepseek.com`) hit `402 Insufficient Balance` mid-trial (`chk-durai-v2`, `res-durai-v2`), and `deepinfra-deepseek` experienced transient load/rate-limit errors (`providerErrors: 6` in `res-durai-v3`), automatically failing over to `relace` (`deepseek-ai/DeepSeek-V4-Flash-0731`) cleanly after commit `afaf27a`. New primary provider `reflection` (`Beam-1.0-501B-A23B`) was integrated (`7d7765c`) and exercised on `res-reflection-s1` and `chk-reflection-s6` (which also exercised `->relace` failover after `3` provider errors).

---

## 2. Detailed Per-Component Results: Sample 2 (Checkout & Ledger)

### Bare Control (`chk-bare-v1`, `deepseek-flash`, `--bare --cost-cap 6.0`)

| Component | Stop Reason | Calls | Last Run Tests | Accepted Stages | Enterprise / List Cost |
|---|---|---|---|---|---|
| `S-CHK-INVENTORY` | `STOP_BUDGET` | `40` | `1 passed / 0 failed` | `[]` | `$3.88` / `$0.248` |
| `S-CHK-INTENT` | `STOP_BUDGET` | `40` | `6 passed / 1 failed` | `[]` | `$1.69` / `$0.113` |
| `S-CHK-FUNDS` | `STOP_COST` | `14` | `6 passed / 1 failed` | `[]` | `$0.46` / `$0.030` |
| **Total (`3` attempted)** | **`0/6 FINISHED`** | **`94`** | **`6 passed / 1 failed`** | **`0`** | **`$6.02` / `$0.392`** |

### Durai Agent (`chk-durai-v3`, `relace` `DeepSeek-V4-Flash-0731`, `--cost-cap 30.0`)

| Component | Stop Reason | Calls | Last Run Tests | Accepted Stages | Enterprise / List Cost |
|---|---|---|---|---|---|
| `S-CHK-INVENTORY` | **`FINISHED`** | `26` | `15 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$1.01` / `$0.061` |
| `S-CHK-INTENT` | **`FINISHED`** | `27` | `24 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$1.10` / `$0.073` |
| `S-CHK-FUNDS` | **`FINISHED`** | `19` | `44 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$0.76` / `$0.047` |
| `S-CHK-LEDGER` | **`FINISHED`** | `27` | `59 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$3.80` / `$0.233` |
| `S-CHK-ACCESS` | **`FINISHED`** | `41` | `66 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$2.18` / `$0.147` |
| `S-CHK-SCREENS` (`v3` pass) | `STOP_IDLE` | `41` | `81 passed / 0 failed` | `DESIGN, PLAN` | `$3.79` / `$0.214` |
| **Total (`chk-durai-v3`)** | **`5/6 FINISHED`** | **`181`** | **`81 passed / 0 failed`** | — | **`$12.63` / `$0.776`** |
| `S-CHK-SCREENS` (`chk-reflection-s6`, `reflection->relace`) | `STOP_IDLE` | `20` | **`82 passed / 0 failed`** | `DESIGN, PLAN` | `$3.03` / `$0.169` |

*Root cause on `S-CHK-SCREENS` `check_app` (`422`):** `chk-reflection-s6` fixed the `/api` prefix in `app/lib.ts` and passed all `82/82` tests, then hit `SCREEN_LOST_SESSION` on `/inventory` because `app/[screen]/page.tsx` renders an error block without `<Menu name={name} />` (omitting `TR-BEN` from visible DOM) when `merchant()` returns empty under sandbox `SHELL_INIT` (`shell.actor` seeded without an `actor_merchant` binding).*

---

## 3. Detailed Per-Component Results: Sample 3 (Seat Reservations)

### Bare Control (`res-bare-v1`, `deepseek-flash`, `--bare --cost-cap 6.0`)

| Component | Stop Reason | Calls | Last Run Tests | Accepted Stages | Enterprise / List Cost |
|---|---|---|---|---|---|
| `S-RES-SEATS` | `STOP_BUDGET` | `40` | `7 passed / 5 failed` | `[]` | `$2.29` / `$0.141` |
| `S-RES-HOLD` | `STOP_BUDGET` | `40` | `7 passed / 6 failed` | `[]` | `$1.40` / `$0.096` |
| `S-RES-CONFIRM` | `STOP_COST` | `36` | `7 passed / 6 failed` | `[]` | `$2.38` / `$0.156` |
| **Total (`3` attempted)** | **`0/6 FINISHED`** | **`116`** | **`7 passed / 6 failed`** | **`0`** | **`$6.08` / `$0.393`** |

### Durai Agent (`res-durai-v3`, `deepinfra-deepseek -> relace` + `res-durai-v3-s6` `relace`)

| Component | Stop Reason | Calls | Last Run Tests | Accepted Stages | Enterprise / List Cost |
|---|---|---|---|---|---|
| `S-RES-SEATS` | **`FINISHED`** | `50` | `14 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$3.37` / `$0.200` |
| `S-RES-HOLD` | **`FINISHED`** | `28` | `21 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$2.96` / `$0.172` |
| `S-RES-CONFIRM` | **`FINISHED`** | `31` | `30 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$3.25` / `$0.210` |
| `S-RES-CANCEL` | **`FINISHED`** | `14` | `35 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$1.98` / `$0.109` |
| `S-RES-ACCESS` | **`FINISHED`** | `17` | `46 passed / 0 failed` | `DESIGN, PLAN, IMPLEMENTATION, TESTS` | `$0.75` / `$0.049` |
| `S-RES-SCREENS` (`v3` pre-fix) | `STOP_IDLE` | `30` | `51 passed / 0 failed` | `DESIGN, PLAN` | `$3.23` / `$0.180` |
| `S-RES-SCREENS` (`s6` post-fix) | **`FINISHED`** | `28` | **`53 passed / 0 failed`** | `DESIGN, PLAN, IMPLEMENTATION, TESTS, DEPLOYED` | `$3.56` / `$0.220` |
| **Total (`6/6 Completed`)** | **`6/6 FINISHED`** | **`198`** | **`53 passed / 0 failed`** | **All 5 stages incl. `DEPLOYED`** | **`$19.10` / `$1.140`** |

> Additional Reflection single-component run `res-reflection-s1` (`S-RES-SEATS`, `reflection -> relace`) recorded `20` calls, `1 passed / 1 failed`, `STOP_IDLE`, `$2.30` enterprise / `$0.114` list after failing over mid-run on `3` Reflection provider errors.

---

## 4. Closed-Loop RL Improvements Driven by These Runs (`durai_local`)

1. **Provider Failover Thinking Fix (`afaf27a`):** Fixed HTTP `400` (`reasoning_content in the thinking mode`) on mid-trace cross-provider failover (`live_model(..., thinking=False)` on fallback). Proven live when `res-durai-v3` failed over cleanly from `deepinfra-deepseek` to `relace` across all 5 backend components.
2. **Dynamic Shell Domain Route Discovery (`585db06`):** `durai/build_web_rules.py#check_sign_in` previously hardcoded Trading paths (`'/positions', '/book', '/prices', '/cash'`), failing non-Trading UIs inside `check_app`. Switching `mode == 'shell'` to `paths=None` (post-sign-in redirect + discovered `<nav>` links) immediately unblocked `res-durai-v3-s6` (`S-RES-SCREENS`) to **`FINISHED` + `DEPLOYED`**.
3. **Vitest Failure Assertion Surfacing (`1110eab`):** `durai/build_run_node.py#run_node_tests` previously hid failing vitest assertion messages behind `--reporter=json`. Extracting `testResults[*].assertionResults` immediately enabled `chk-reflection-s6` to resolve its failing `app/actions.test.ts` assertion and reach `82/82` passing tests.
4. **Reflection Primary Provider Migration (`7d7765c`):** Added `reflection` (`Beam-1.0-501B-A23B`) to `durai/model_pricing.py`, `durai/model_router.py`, and set `--provider` default in `tools/run_blind_build.py` to `reflection`.

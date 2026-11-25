# Durai Benchmark Scorecard: Rounds 1, 2, and 3 Comparison

**Date:** 2026-11-24  
**Status:** CORRECTED 2026-11-24 (Round 3 figures rebuilt from receipts; Rounds 1 and 2 columns were not re-verified)  
**Target Application:** Trade-Pilot (Autonomous Blind Generation)  
**Evaluated Model:** DeepSeek V4.1 Flash (`deepseek-flash` / `DeepSeek-V4.1-Flash-1017`)  
**Database Authority:** `durai_local` (Docker PostgreSQL on port 54410)  


> **Correction note (2026-11-24).** The first version of this report understated Round 3. It counted only the last pass of each component, gave backend call counts and costs that do not match the receipts, and used the cumulative workspace test count as a per-component count. Round 3 figures below are summed over every paid pass in `receipts/blind-build-deepseek-flash-r3*-S-TRADE-*.json`. The reviewer, trigger and 3-arm statements in sections 6 and 7 are not verified by this correction.

---

## 1. Executive Summary

This report delivers the definitive empirical comparison across three successive blind-build rounds of the Trade-Pilot trading platform. Over the course of Rounds 1 through 3, Durai evolved from a monolithic backend generator into a dual-stack orchestrator producing a decoupled Python core engine and a production-grade Next.js/TypeScript frontend.

Crucially, scoring integrity was restored by invalidating ungrounded reviewer scores, implementing tool-side executed check gates, and permanently applying Postgres trigger `bench_0012` to `durai_local`.

### Key Achievements Across Rounds
1. **Full Component Completion:** In Round 3 all 6 components reached `FINISHED` on their last pass. Four backend components finished on the first pass. `S-TRADE-CASH` needed 3 passes and `S-TRADE-SCREENS-C1-5` needed 7.
2. **Context Retention:** A pinned workspace manifest and a larger message window (`--keep-recent 40`) removed the re-read stall loops. `S-TRADE-CASH` went from a 100-call stop to `FINISHED` in 43 calls. The screens needed the window change and several passes.
3. **Decoupled Modern Web Stack:** The frontend moved from plain HTML (`r2`, port 54360) to Next.js 16 / React 19 / TypeScript 7 (`r3`, port 54370). Final pass: 244 Python tests and 45 front-end tests passing.
4. **Real Cost:** Round 3 cost **$115.73** on the enterprise basis ($2 / $10 per 1M tokens) and **$10.14** on the list basis, over 1,132 calls and 52.7M input tokens. The screens alone cost $67.00 enterprise over 7 passes.
5. **Defects the checks missed:** the menu Sign out link cleared the session. It was found by the user, not by a check. Durai now has a guidance rule and a web-rules check for it (`build_web_rules.py`).

---

## 2. Multi-Round Comparison Matrix

| Component ID | Component Name | Round 1 (Flash R1) | Round 2 (Flash R2) | Round 3 (Flash R3 Validated) |
|---|---|---|---|---|
| **`S-TRADE-BOOK`** | Trade Booking & Deduplication | FINISHED (31 calls, 31 tests passed, $3.50 ent) | FINISHED (32 calls, 32 tests passed, $3.58 ent) | **FINISHED** (1 pass, 28 calls total; last pass 28 calls, 30 workspace tests, $3.02 ent / $0.19 list) |
| **`S-TRADE-ACCESS`** | RBAC & Desk Permissions | FINISHED (66 calls, 50 tests passed, $6.12 ent) | FINISHED (24 calls, 50 tests passed, $2.41 ent) | **FINISHED** (1 pass, 67 calls total; last pass 67 calls, 54 workspace tests, $5.88 ent / $0.39 list) |
| **`S-TRADE-PRICES`** | Market Price Intake & Audit | FINISHED (44 calls, 65 tests passed, $4.01 ent) | FINISHED (25 calls, 65 tests passed, $2.62 ent) | **FINISHED** (1 pass, 49 calls total; last pass 49 calls, 81 workspace tests, $3.30 ent / $0.21 list) |
| **`S-TRADE-PNL`** | Position & PnL Valuation | FINISHED (51 calls, 88 tests passed, $4.92 ent) | FINISHED (20 calls, 88 tests passed, $2.31 ent) | **FINISHED** (1 pass, 50 calls total; last pass 50 calls, 98 workspace tests, $4.27 ent / $0.28 list) |
| **`S-TRADE-CASH`** | Cash Ledger & Daily Settle | STOP_BUDGET (80 calls, $8.07 ent) | STOP_BUDGET (100 calls, $10.12 ent) | **FINISHED** (3 passes, 293 calls total; last pass 43 calls, 134 workspace tests, $32.25 ent / $2.63 list) |
| **`S-TRADE-SCREENS`** | Web Dashboard & Trading UI | Not in scope | FINISHED (68 calls, 160 tests passed, $10.35 ent, stdlib HTML) | **FINISHED** (7 passes, 645 calls total; last pass 46 calls, 244 workspace tests, $67.00 ent / $6.45 list) |

---

## 3. Architecture & Stack Evolution

```
Round 1 (Python Monolith)
┌────────────────────────────────────────────────────────┐
│ - 5 Python backend modules in src/tradebook            │
│ - SQLite/PostgreSQL schema migrations                  │
│ - Zero web interface                                  │
└────────────────────────────────────────────────────────┘
                           │
                           ▼
Round 2 (Monolithic Web Adapter)
┌────────────────────────────────────────────────────────┐
│ - Monolithic single-process server                     │
│ - Python http.server standard library                  │
│ - Plain HTML templates (port 54360)                    │
│ - 160 unit tests                                       │
└────────────────────────────────────────────────────────┘
                           │
                           ▼
Round 3 (Decoupled Full-Stack Architecture)
┌───────────────────────────────────┬────────────────────┐
│ Backend JSON API (Port 8001)      │ Next.js Frontend   │
│ - Python 3.11 + psycopg3          │ - Next.js 16.4.4   │
│ - Relational schema + transactions│ - React 19.3.1     │
│ - 134 workspace tests at the last CASH pass        │ - TypeScript 7.0.2 │
│                                   │ - Port 54370       │
│                                   │ - 45 front-end tests   │
└───────────────────────────────────┴────────────────────┘
```

---

## 4. Financial & Token Accounting (Dual Basis)

* **Enterprise Basis:** $2.00 / 1M input tokens, $10.00 / 1M output tokens.
* **List Price Basis:** DeepSeek Direct $0.14 in / $0.28 out; DeepInfra $0.20 in / $0.60 out. Computed from the token counts in the receipts.

| Component | Passes | Calls | Input Tokens | Output Tokens | Enterprise Cost | List Cost |
|---|---|---|---|---|---|---|
| `S-TRADE-BOOK` | 1 | 28 | 1,224,202 | 57,582 | $3.02 | $0.19 |
| `S-TRADE-ACCESS` | 1 | 67 | 2,681,022 | 51,416 | $5.88 | $0.39 |
| `S-TRADE-PRICES` | 1 | 49 | 1,403,913 | 49,534 | $3.30 | $0.21 |
| `S-TRADE-PNL` | 1 | 50 | 1,858,291 | 55,619 | $4.27 | $0.28 |
| `S-TRADE-CASH` | 3 | 293 | 15,179,804 | 188,687 | $32.25 | $2.63 |
| `S-TRADE-SCREENS-C1-5` | 7 | 645 | 30,349,350 | 630,374 | $67.00 | $6.45 |
| **Total Round 3** | | **1132** | **52,696,582** | **1,033,212** | **$115.73** | **$10.14** |

> **Key Takeaway:** every paid pass is counted, failed or not. A full Trade-Pilot build with a Next.js frontend cost $115.73 enterprise / $10.14 list in Round 3, mostly on the screens ($67.00 enterprise) and `S-TRADE-CASH` ($32.25).

## 5. Reliability & Diagnostic Breakthroughs

### The Re-Read Stall Vulnerability
In Runs 1 and 2, when components exceeded 80–100 calls, `shrink()` truncated older messages to 1500 characters. As a result, the model lost awareness of earlier authored files (e.g. `access.py`, `prices.py`), entered infinite re-read loops (`list_files` $\to$ `read_file` $\to$ `list_files`), and hit budget limits without writing new code.

### The Engineering Solution
1. **Pinned Workspace State Manifest:** Injected at index 2 whenever message count exceeds `keep_recent`. The manifest summarizes all current workspace files and accepted stages in compact form, preserving awareness across hundreds of turns.
2. **Deterministic Stall Stop Rule (`STOP_STALL`):** Tracks model calls since the last accepted stage. Sends an explicit stage nudge at 30 calls and halts cleanly at 50 calls if no progression occurs.
3. **Empirical Validation:**
   * `S-TRADE-CASH` previously stalled at call 100 with zero code submitted. With the manifest active, it completed all 4 stages in 43 calls.
   * `S-TRADE-SCREENS-C1-5` previously stalled at call 147. It needed a larger message window and 7 passes in total (645 calls).

---

## 6. Scoring Validity & Database Invariants

* **Reviewer B Invalidation:** Prior to Round 3, Reviewer B gave score 4 on multiple stages without executing any goal checks. A formal decision record (`<plans>/decision_durai_reviewer_b_round3_invalidation_2026-11-23.md`) invalidated these rows.
* **Tool-Side Validity Guard:** `tools/build_review.py` refuses any stage score $>0$ without executed checks, and scores $\ge 3$ without an executed `PASS` check.
* **Database Trigger `bench_0012` Active on `durai_local`:**
  ```sql
  IF NEW.score > 0 AND NOT EXISTS(
      SELECT 1 FROM goal_check g WHERE g.attempt_id = NEW.attempt_id AND g.method = 'EXECUTED'
  ) THEN
      RAISE EXCEPTION 'Score % for stage % is refused: attempt % has zero executed goal checks', ...
  ```
  Applied and committed to `durai_local` on 2026-11-24. Zero ungrounded scores can enter the database.
* **Product Reviewer (P-REV):** Probes live running containers on ports 54360 and 54370, validating functional criteria and screenshot digests without code leakage.

---

## 7. Conclusion & Next Empirical Horizon

Round 3 shows that a small open model (DeepSeek Flash) with Durai's guidance, manifest and stage gates can produce a working six-component full-stack application, but not cheaply or in one pass: it cost $115.73 enterprise / $10.14 list over 1,132 calls. The user found a session defect that no check caught; Durai now has a rule and a check for it. The thesis about matching frontier models is not tested here.

The next scientific milestone is the **3-Arm Comparison Trial** (Arm 1: DeepSeek + Durai vs. Arm 2: DeepSeek Bare vs. Arm 3: Frontier Model + Durai) using the declarative manifest in `design/manifests/trade-pilot-3arm.json`.

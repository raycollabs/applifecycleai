# Reflection vs. DeepSeek vs. Bare Control Comparison

Generated automatically from `receipts/blind-build-*.json`.

| Arm / Run | Components Attempted | FINISHED | Calls | Enterprise Cost | List Cost | Last-Run Tests (Passed/Failed) | Providers Used |
|---|---|---|---|---|---|---|---|
| Checkout — Bare (`deepseek-flash`) | 3/6 | **0/3** | 94 | $6.02 | $0.392 | 1/0 | deepseek |
| Checkout — DeepSeek (`relace` `DeepSeek-V4-Flash-0731`) | 6/6 | **5/6** | 181 | $12.63 | $0.776 | 81/0 | relace |
| Checkout — Reflection (`chk-reflection-v1`) | 6/6 | **3/6** | 325 | $21.75 | $2.113 | 42/0 | reflection |
| Reservations — Bare (`deepseek-flash`) | 3/6 | **0/3** | 116 | $6.08 | $0.393 | 7/5 | deepseek |
| Reservations — DeepSeek (`deepinfra->relace`, `v3`) | 6/6 | **5/6** | 170 | $15.54 | $0.920 | 14/0 | deepinfra-deepseek->relace |
| Reservations — DeepSeek Screens Fix (`res-durai-v3-s6`) | 1/1 | **1/1** | 28 | $3.56 | $0.220 | 53/0 | relace |
| Reservations — Reflection (`res-reflection-v1`) | 6/6 | **1/6** | 345 | $19.98 | $1.948 | 12/0 | reflection |

## Checkout — Bare (`deepseek-flash`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-CHK-FUNDS` | `STOP_COST` | 14 | 6/1 | [] | `deepseek` | $0.46 | $0.030 |
| `S-CHK-INTENT` | `STOP_BUDGET` | 40 | 6/1 | [] | `deepseek` | $1.69 | $0.113 |
| `S-CHK-INVENTORY` | `STOP_BUDGET` | 40 | 1/0 | [] | `deepseek` | $3.88 | $0.248 |

## Checkout — DeepSeek (`relace` `DeepSeek-V4-Flash-0731`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-CHK-ACCESS` | `FINISHED` | 41 | 66/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `relace` | $2.18 | $0.147 |
| `S-CHK-FUNDS` | `FINISHED` | 19 | 44/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `relace` | $0.76 | $0.047 |
| `S-CHK-INTENT` | `FINISHED` | 27 | 24/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `relace` | $1.10 | $0.073 |
| `S-CHK-INVENTORY` | `FINISHED` | 26 | 15/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `relace` | $1.01 | $0.061 |
| `S-CHK-LEDGER` | `FINISHED` | 27 | 59/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `relace` | $3.80 | $0.233 |
| `S-CHK-SCREENS` | `STOP_IDLE` | 41 | 81/0 | DESIGN, PLAN | `relace` | $3.79 | $0.214 |

## Checkout — Reflection (`chk-reflection-v1`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-CHK-ACCESS` | `STOP_BUDGET` | 60 | 42/0 | DESIGN, PLAN | `reflection` | $4.93 | $0.482 |
| `S-CHK-FUNDS` | `FINISHED` | 47 | 29/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $2.56 | $0.246 |
| `S-CHK-INTENT` | `FINISHED` | 40 | 21/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $2.42 | $0.232 |
| `S-CHK-INVENTORY` | `FINISHED` | 58 | 13/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $3.19 | $0.307 |
| `S-CHK-LEDGER` | `STOP_BUDGET` | 60 | 37/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $4.16 | $0.402 |
| `S-CHK-SCREENS` | `STOP_BUDGET` | 60 | 42/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $4.50 | $0.443 |

## Reservations — Bare (`deepseek-flash`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-RES-CONFIRM` | `STOP_COST` | 36 | 7/6 | [] | `deepseek` | $2.38 | $0.156 |
| `S-RES-HOLD` | `STOP_BUDGET` | 40 | 7/6 | [] | `deepseek` | $1.40 | $0.096 |
| `S-RES-SEATS` | `STOP_BUDGET` | 40 | 7/5 | [] | `deepseek` | $2.29 | $0.141 |

## Reservations — DeepSeek (`deepinfra->relace`, `v3`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-RES-ACCESS` | `FINISHED` | 17 | 46/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `deepinfra-deepseek->relace` | $0.75 | $0.049 |
| `S-RES-CANCEL` | `FINISHED` | 14 | 35/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `deepinfra-deepseek->relace` | $1.98 | $0.109 |
| `S-RES-CONFIRM` | `FINISHED` | 31 | 30/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `deepinfra-deepseek->relace` | $3.25 | $0.210 |
| `S-RES-HOLD` | `FINISHED` | 28 | 21/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `deepinfra-deepseek->relace` | $2.96 | $0.172 |
| `S-RES-SCREENS` | `STOP_IDLE` | 30 | 51/0 | DESIGN, PLAN | `deepinfra-deepseek->relace` | $3.23 | $0.180 |
| `S-RES-SEATS` | `FINISHED` | 50 | 14/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `deepinfra-deepseek->relace` | $3.37 | $0.200 |

## Reservations — DeepSeek Screens Fix (`res-durai-v3-s6`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-RES-SCREENS` | `FINISHED` | 28 | 53/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS, DEPLOYED | `relace` | $3.56 | $0.220 |

## Reservations — Reflection (`res-reflection-v1`)

| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |
|---|---|---|---|---|---|---|---|
| `S-RES-ACCESS` | `STOP_BUDGET` | 60 | 42/0 | DESIGN, PLAN | `reflection` | $4.06 | $0.398 |
| `S-RES-CANCEL` | `STOP_BUDGET` | 60 | 37/0 | DESIGN, PLAN | `reflection` | $3.54 | $0.346 |
| `S-RES-CONFIRM` | `FINISHED` | 45 | 29/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $2.47 | $0.240 |
| `S-RES-HOLD` | `STOP_BUDGET` | 60 | 22/0 | DESIGN, PLAN | `reflection` | $3.22 | $0.313 |
| `S-RES-SCREENS` | `STOP_BUDGET` | 60 | 45/0 | DESIGN, PLAN, IMPLEMENTATION, TESTS | `reflection` | $3.84 | $0.377 |
| `S-RES-SEATS` | `STOP_BUDGET` | 60 | 12/0 | DESIGN, PLAN | `reflection` | $2.84 | $0.273 |

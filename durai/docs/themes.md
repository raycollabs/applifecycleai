# UI themes in the prompt enhancer

Status: implemented 2026-11-26. Plan: <plans>\plan_durai_ui_themes_and_slack_2026-11-26.md (Part T).

A request to build an app, screen or dashboard gets four UI theme options. The enhancer adds them as context. It never rewrites the request.

## Themes

| Id | Use for |
| --- | --- |
| `enterprise-admin` | Admin consoles, roles, audit, settings. This is the default. |
| `transaction-heavy` | Trades, orders, payments, blotters, ledgers. |
| `report-heavy` | Reports, charts, KPIs, exports. |
| `app-lifecycle-manager` | Pipelines, releases, environments, approval gates, rollback. |

Each theme has design tokens (color, type, spacing, density, radius), layout rules, components and acceptance checks. The tokens are written as CSS custom properties.

## How it works

1. `design/prompt/themes/catalog.json` holds the data. It carries a SHA-256 digest. After an edit run `python tools/stamp_theme_catalog.py`.
2. `durai/theme_intent.py` needs a build word (build, create, design, ...) and an app word (app, screen, dashboard, ...). A code question stays quiet.
3. The default theme comes from the request words. `theme: report-heavy` in the request chooses a theme.
4. `Enhancer.enhance` appends the theme block after its normal text. A compare request keeps its own path and gets no theme block.
5. `tools/enhance_hook.py` runs the compare check first, then the theme check. It fails open.
6. `DURAI_ENHANCE=0` turns all of it off.

To add a theme, add a record to the catalog and stamp it. No code change is needed. The catalog is an OPEN file in the open-core boundary.

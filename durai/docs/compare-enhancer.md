# Compare-request enhancer

Status: implemented 2026-11-26. Plan: <plans>\plan_durai_compare_enhancer_and_sandbox_2026-11-26.md (Part A).

A compare request asks Durai to compare itself with a GitHub repository, a framework or a tool. The enhancer adds an answer format to such a request. The answer then has the same shape every time.

## What it does

1. `durai/compare_intent.py` reads the request text. It needs a compare word and a target word.
   - Compare words: compare, vs, versus, alternative to, replace ... with, adapt ... from, what we lack, how does ... differ.
   - Target words: a github.com URL, repo, framework, library, tool, platform, SDK, open source, product, runtime.
2. When both are present, it adds ten answer sections, the verdict words, the cost scale and the table columns.
3. The request stays the first lines, unchanged.
4. The template is data: `design/prompt/compare/template.json`. It has a digest. A changed file is refused. After an edit, run `python tools/stamp_compare_template.py`.

## Where it runs

- `Enhancer.enhance` (`prompt_enhancer.py`) calls it first when no kinds list is given. Build requests follow the old path and give the same text as before.
- `tools/enhance_hook.py` is a Claude Code UserPromptSubmit hook. It reads the prompt from stdin. It prints `additionalContext` for a compare request. It prints nothing for other requests. It exits 0 on every error. It makes no network call and no model call.

## Controls

- Set `DURAI_ENHANCE=0` to turn the enhancer off.
- The hook shows one line: "Durai compare format applied".

## Open-core line

The detector, template and hook are OPEN. Other files under `design/prompt/` stay PAID_PACK.

## Sample

Request: "Compare Durai with github.com/acme/agentbox. What do we lack?"
Result: the request, then "Targets named: github.com/acme/agentbox", then sections 1 to 10, the verdict words ADOPT, ADAPT, KEEP, SKIP and REPLACE, and the cost scale S, M and L.
Request: "Let a trader book a trade." Result: no compare text. The old classifier gives kind idempotent-command.

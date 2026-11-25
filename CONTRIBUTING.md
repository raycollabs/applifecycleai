# Contributing to Durai

Status: draft. Decision D3 of the plan chose a Developer Certificate of Origin (DCO).

## Sign every commit
Add a line to each commit message:

    Signed-off-by: Your Name <you@example.org>

Use `git commit -s`. The line means you agree to the Developer Certificate of Origin 1.1 (https://developercertificate.org).

## Small commits
One module change is one commit. A small commit is easy to review and easy to revert.

## Code comments
Write only sample walkthrough comments in ASD-STE100 form. Use concrete inputs, actions and results.

    # Sample: Start with 200 shares. Remove 1 lot of 100 shares. Check 100 shares.

Do not write comments that explain why code exists.

## Tests
Write one test function for each goal. Run the tests before you open a pull request.

## Keys and data
Never commit a key, a token or a private path. Run `python tools/secret_scan.py --public-only` before you push.
Never add a hidden goal check to the open tree.

## Packs
You can write your own packs in the open pack format. A pack that is not signed by raycollabs installs as COMMUNITY.

# Slack chat client

`durai/ports/chat.py` is the chat port. `durai/adapters/chat/slack.py` is the Slack adapter. `durai/chat_bridge.py` maps a chat message to an enhanced draft and a button click to a reviewer approval. Standard library only. No network call runs unless you pass an HTTP function.

## What it does

1. Slack sends a slash command (`/durai build a dashboard`), a message event or a button click to your HTTPS endpoint.
2. `ChatBridge.handle(headers, body)` checks the signature first. A bad signature or a timestamp older than 5 minutes raises `InvalidSignature`. Answer 401.
3. A command or message becomes an enhanced draft. The bridge opens a gate (one hour) and posts the draft in the thread with an Approve button.
4. A click records a `durai_reviewer` approval only when the Slack user ID is on the operator allow-list. Any other click is ignored and logged. An agent never approves.
5. One Slack thread maps to one workflow id.

## Create the Slack app

1. Open https://api.slack.com/apps and choose Create New App, From scratch.
2. OAuth & Permissions, Bot Token Scopes: `chat:write` and `commands`. Add `channels:history` only if you want message events.
3. Slash Commands: create `/durai`. Set the Request URL to `https://<your host>/slack/command`.
4. Interactivity & Shortcuts: turn it on. Set the Request URL to `https://<your host>/slack/interact`.
5. Event Subscriptions (optional): turn it on. Set the Request URL to `https://<your host>/slack/events`. Slack sends a `url_verification` request. The bridge returns the challenge. Subscribe to `message.channels`.
6. Install the app to the workspace. Copy the Bot User OAuth Token (starts with `xoxb-`) and the Signing Secret.

## Where the secrets live

Put them in `private/providers/slack.json` (git-ignored, the shared provider folder, see `durai/provider_config.py`):

```json
{"id": "slack", "botToken": "xoxb-...", "signingSecret": "...", "reviewers": ["U0123ABCD"]}
```

`reviewers` is the allow-list of Slack user IDs that map to `durai_reviewer`. The token and secret never appear in code, logs or receipts. `ChatBridge.log` redacts token and signature patterns.

## Wire it in a server

```python
# Sample: Build the adapter from slack.json. Pass the raw request headers and body to bridge.handle. Return 401 on InvalidSignature.
```

The HTTP function takes `(url, headers, body_bytes)` and returns the parsed JSON reply.

## Tests

`tests/test_chat.py` runs the shared `ChatContract` on the Slack adapter and a memory adapter. It checks signature, replay window, thread mapping, drafts, reviewer-only approval and secret redaction. No live Slack call runs.

## What a live test needs from you

- A Slack app with the scopes above, the bot token and the signing secret.
- A public HTTPS URL for the three request URLs (a tunnel is fine for a trial).
- The Slack user IDs of your reviewers.
- The workspace and channel where the bot is invited.

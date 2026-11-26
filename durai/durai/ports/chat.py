"""Sample: Verify a chat request. Parse it to one event. Post a reply in the same thread. An adapter for Slack, Teams or Discord keeps these four calls."""
from typing import Protocol


class ChatPort(Protocol):
    def verify(self, headers: dict, body: bytes) -> None:
        # Sample: Headers and body signed with the right secret return None. A changed body or a six minute old timestamp raises InvalidSignature.
        ...

    def parse(self, headers: dict, body: bytes) -> dict:
        # Sample: A slash command "/durai build a dashboard" from user U1 in channel C1 returns {"kind": "command", "user": "U1", "channel": "C1", "thread": "C1:", "text": "build a dashboard"}. A button click returns kind "action" with the value.
        ...

    def post(self, channel: str, text: str, thread: str | None = None, blocks: list | None = None) -> str:
        # Sample: Post "Gate open" to channel C1 in thread C1:100.1. Return the message id 100.2.
        ...

    def thread_key(self, channel: str, thread: str | None) -> str:
        # Sample: Channel C1 and thread 100.1 return C1:100.1. Two replies in one thread return the same key.
        ...

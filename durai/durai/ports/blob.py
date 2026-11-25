"""Sample: Put 3 bytes at a/b.bin with key B-1. Get the same 3 bytes. The digest equals the SHA-256 of the bytes."""
from typing import Protocol


class BlobPort(Protocol):
    def put(self, path: str, data: bytes, key: str) -> str:
        # Sample: Put b"abc" at a/b.bin. Return its SHA-256 hex. Put it again with the same key. Return the same digest.
        ...

    def get(self, path: str) -> bytes:
        # Sample: Get a/b.bin. Return b"abc". Get x/none.bin. Raise NotFound.
        ...

    def presign(self, path: str, seconds: int) -> str:
        # Sample: Presign a/b.bin for 60 seconds. Return a link text. Presign x/none.bin. Raise NotFound.
        ...

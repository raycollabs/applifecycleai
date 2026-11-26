"""Sample: Create a sandbox with key K-1. Run python -c "print(200)". Check exit 0 and output 200. Destroy it twice. Check no error."""
from typing import Protocol, Sequence


class SandboxPort(Protocol):
    def create(self, spec: dict, key: str) -> str:
        # Sample: Create with spec {"seconds": 20} and key K-1. Return sbx-1. Create again with key K-1. Return sbx-1 and make nothing new.
        ...

    def exec(self, sandbox_id: str, argv: Sequence[str], stdin: bytes = b'', deadline: float | None = None) -> dict:
        # Sample: Run ["python", "-c", "print(200)"]. Return state SUCCEEDED, exitCode 0 and output b"200". A slow process returns state FAILED and reason deadline.
        ...

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        # Sample: Write program.py with 20 bytes. A path that leaves the workspace, such as ../x or /etc/x, raises InvalidPath.
        ...

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        # Sample: Read program.py after a write of 20 bytes. Return those 20 bytes. Read missing.py. Raise NotFound.
        ...

    def apply_policy(self, sandbox_id: str, policy: dict, key: str) -> None:
        # Sample: Apply {"network": "none"}. Return. Apply an egress allow-list on an adapter that cannot enforce it. Raise Unsupported.
        ...

    def events(self, sandbox_id: str) -> list[dict]:
        # Sample: After create and one exec, return CREATED, EXEC_STARTED, EXEC_ENDED with sequence 1, 2, 3 and one digest each.
        ...

    def destroy(self, sandbox_id: str, key: str) -> None:
        # Sample: Destroy sbx-1 with key K-2. Destroy it again. Return both times. Exec on sbx-1 raises NotFound.
        ...

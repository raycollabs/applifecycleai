"""Sample: Start worker W-1 with child C-1. Cancel W-1. Check that W-1 and C-1 both stop."""
import os
import signal
import subprocess
import threading
import time
from .common import Conflict

LIMIT = 65536


def alive(pid):
    # Sample: Check a running PID 4120. Return True. Check it after a kill. Return False.
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class OwnedProcess:
    def __init__(self, argv, cwd=None, env=None):
        # Sample: Start python -c "print(200)" in a new process group. Read its output in a thread.
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, **options)
        self.output = bytearray()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        # Sample: Read 70000 bytes of output. Keep the last 65536 bytes.
        while chunk := self.process.stdout.read(4096):
            self.output.extend(chunk)
            del self.output[:max(0, len(self.output) - LIMIT)]

    def wait(self, seconds):
        # Sample: Wait 120 seconds for exit code 0. Return 0. Kill the tree after the deadline.
        deadline = time.monotonic() + seconds
        while self.process.poll() is None:
            if time.monotonic() > deadline:
                self.cancel()
                raise Conflict('Owned process exceeded its deadline')
            time.sleep(.05)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        return self.process.returncode

    def cancel(self):
        # Sample: Cancel W-1 with child C-1. Kill the complete tree. Return the W-1 exit code.
        if self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.process.pid)], capture_output=True, timeout=30)
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        code = self.process.wait(timeout=30)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        return code

    def text(self):
        # Sample: Read output b"200\n". Return the text 200.
        return bytes(self.output).decode('utf-8', errors='replace')

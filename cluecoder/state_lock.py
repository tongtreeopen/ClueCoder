"""Reentrant state-file mutex, released by the OS when a process exits."""

import errno
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class StateLock:
    def __init__(self, path: Callable[[], Path]):
        self.path = path
        self._thread = threading.RLock()
        self._depth = 0
        self._file = None

    def __enter__(self):
        self._thread.acquire()
        try:
            if not self._depth:
                path = self.path()
                if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
                    raise ValueError("State lock escapes directory")
                self._file = path.open("a+b")
                if not path.stat().st_size:
                    self._file.write(b"\0")
                    self._file.flush()
                deadline = time.monotonic() + 10
                while True:
                    self._file.seek(0)
                    try:
                        if os.name == "nt":
                            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Persistent state is busy") from None
                        time.sleep(0.01)
            self._depth += 1
            return self
        except BaseException:
            if self._file is not None and not self._depth:
                self._file.close()
                self._file = None
            self._thread.release()
            raise

    def __exit__(self, *exc):
        self._depth -= 1
        try:
            if not self._depth:
                self._file.close()
                self._file = None
        finally:
            self._thread.release()

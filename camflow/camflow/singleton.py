"""Single-instance lock so only one CamFlow runs at a time.

Without this, a copy left running in a terminal plus a copy launched as the
app both listen for the hotkey and both paste — making every dictation appear
twice. Holds an exclusive lock on ~/.camflow/camflow.lock for the process's
lifetime (released automatically when the process exits).
"""

from __future__ import annotations

import fcntl
from pathlib import Path

LOCK_PATH = Path.home() / ".camflow" / "camflow.lock"

# Module-level reference keeps the file open (and the lock held) for the
# lifetime of the process. Do not let this be garbage-collected.
_lock_handle = None


def acquire() -> bool:
    """Return True if we got the lock, False if another instance holds it."""
    global _lock_handle
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        _lock_handle.close()
        _lock_handle = None
        return False

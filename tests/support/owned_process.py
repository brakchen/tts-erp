"""Own one Linux test subprocess group from launch through bounded cleanup.

The launch owner creates the session itself; callers cannot supply a process,
process-group id, shell, or pre-exec callback. External output files remain
caller-owned. Only PIPE streams created by Popen are closed here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from typing import IO

_TERMINATE_GRACE_SECONDS = 0.5
_REAP_TIMEOUT_SECONDS = 5
_POLL_INTERVAL_SECONDS = 0.02


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@contextmanager
def owned_process(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str],
    stdout: int | IO[str] | None,
    stderr: int | IO[str] | None,
) -> Iterator[subprocess.Popen[str]]:
    """Launch in a new session and stop only that group on every exit.

    Communicate/wait deadlines belong to the caller. Cleanup does not infer
    descendant liveness from the leader or its pipes: npm may have exited while
    a worker ignores TERM and has already closed stdout/stderr.
    """
    if sys.platform != "linux":
        raise RuntimeError("Owned test subprocesses require Linux")
    process = subprocess.Popen(
        args, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
        text=True, start_new_session=True,
    )
    try:
        yield process
    finally:
        try:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
            while _group_exists(process.pid):
                process.poll()  # Reap the leader; the group remains the liveness authority.
                if not _group_exists(process.pid):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
            process.wait(timeout=_REAP_TIMEOUT_SECONDS)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

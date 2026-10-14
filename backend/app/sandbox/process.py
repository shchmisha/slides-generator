"""Running a renderer script as its own process group, with a clean environment."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# What the scripts need from our environment (fonts, browsers, locale); nothing else passes.
PASSED_ENV = (
    "PATH",
    "LANG",
    "CHROMIUM_PATH",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PRODUCER_HEADLESS_SHELL_PATH",
    "FONTCONFIG_PATH",
)


@contextmanager
def private_home() -> Iterator[Path]:
    """A throwaway HOME/TMPDIR for Chromium's profile, caches and sockets. Short path on
    purpose: a Unix socket path must fit in 108 bytes."""
    short = "/tmp" if os.access("/tmp", os.W_OK) else None  # noqa: S108 - private mkdtemp
    home = Path(tempfile.mkdtemp(prefix="r-", dir=short))
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def clean_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    env |= {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(home),
    }
    return env | (extra or {})


def run_group(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str]:
    """Run in a new process group and kill the whole group on timeout or any interruption:
    a script starts Node, which starts Chromium, and none of them may outlive the job."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    finally:
        # Also after a normal exit: a Chromium helper left behind must not keep running.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    return proc.returncode, stderr

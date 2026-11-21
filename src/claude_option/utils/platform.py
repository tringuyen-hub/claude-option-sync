"""OS differences in one place: private files, the single-instance lock, background processes.

POSIX (Linux, macOS): chmod 0600/0700, fcntl.flock.
Windows: an ACL that grants only the current user (icacls), msvcrt.locking.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
log = logging.getLogger(__name__)


def python_executable(windowless: bool = False) -> str:
    """The venv's interpreter; on Windows optionally pythonw.exe (no console window)."""
    exe = Path(sys.executable)
    if IS_WINDOWS and windowless:
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _windows_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain else user


def make_private(path: Path, *, is_dir: bool | None = None) -> None:
    """Restrict a file or directory to the current user. Best effort on Windows."""
    path = Path(path)
    if not path.exists():
        return
    if is_dir is None:
        is_dir = path.is_dir()
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if is_dir else 0o600)
        return
    grant = f"{_windows_user()}:(OI)(CI)F" if is_dir else f"{_windows_user()}:F"
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("could not restrict permissions on %s: %s", path, exc)


def is_private(path: Path) -> bool:
    """POSIX: no group/other bits. Windows: no inherited ACL entries (icacls output has no '(I)')."""
    path = Path(path)
    if not IS_WINDOWS:
        return (path.stat().st_mode & 0o077) == 0
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return "(I)" not in out and _windows_user().split("\\")[-1].lower() in out.lower()


class LockHeld(RuntimeError):
    pass


def lock_file(path: Path):
    """Open `path` and take an exclusive, non-blocking lock on it; the open handle holds the lock."""
    fh = open(path, "a+")
    try:
        if IS_WINDOWS:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise LockHeld(str(path)) from None
    return fh


def spawn_background(args: list[str], cwd: Path, log_path: Path) -> int:
    """Start a detached process that outlives this one; output goes to `log_path`. Returns its pid."""
    out = open(log_path, "ab")
    kwargs: dict = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": out, "stderr": out}
    if IS_WINDOWS:
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    out.close()
    return proc.pid


def terminate(pid: int) -> None:
    """Last-resort stop when the broker does not answer its shutdown request."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


def pid_alive(pid: int) -> bool:
    if IS_WINDOWS:   # os.kill(pid, 0) would terminate the process on Windows
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

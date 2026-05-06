"""
Centralized ffmpeg process registry.

Problem solved
──────────────
Previously, when the pipeline raised mid-render, lingering ffmpeg children
held .ts files open and Python's tempdir cleanup failed with WinError 32.
The cancel button also could not reliably kill in-flight processes because
each call had its own local Popen handle.

Solution
────────
Every ffmpeg invocation goes through this registry. The registry keeps
weak references to live Popen objects.  On exit (graceful or via
cancel/exception) we walk the registry and terminate everything.

Usage
─────
    from .process_manager import ProcessManager

    pm = ProcessManager(cancel_event)
    pm.run(["ffmpeg", ...], context="segment 5", timeout_s=30)
    # raises CancelledError if cancel_event is set, RuntimeError on non-zero,
    # TimeoutError on hang.

    # On shutdown / between pipeline phases:
    pm.kill_all()
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Optional


class CancelledError(Exception):
    """Raised when the user requested cancellation."""
    pass


@dataclass
class ProcessHandle:
    proc:    subprocess.Popen
    context: str
    started: float = field(default_factory=time.monotonic)


class ProcessManager:
    """
    Owns every ffmpeg/ffprobe Popen and guarantees cleanup.

    Thread-safe: registry mutated under a lock; kill_all() uses iteration
    over a snapshot to avoid mutation during cleanup.
    """

    # Class-level registry so atexit can reach every instance
    _global_lock = threading.Lock()
    _global_registry: weakref.WeakSet["ProcessManager"] = weakref.WeakSet()

    def __init__(self, cancel: Optional[threading.Event] = None) -> None:
        self.cancel = cancel or threading.Event()
        self._lock = threading.Lock()
        self._procs: list[ProcessHandle] = []
        with ProcessManager._global_lock:
            ProcessManager._global_registry.add(self)

    # ── Core run loop ───────────────────────────────────────────────────────

    def run(
        self,
        cmd: list[str],
        *,
        context: str = "ffmpeg",
        timeout_s: float = 600,
        capture_output: bool = True,
    ) -> tuple[int, bytes, bytes]:
        """
        Run cmd as a managed subprocess with stderr drained in a background
        thread (avoids the 64 KB pipe-buffer deadlock).

        Returns (exit_code, stdout_bytes, stderr_bytes).
        Raises CancelledError if cancel set; TimeoutError on deadline; otherwise
        returns the exit code (caller checks if needed).
        """
        # Inject -loglevel error -nostats to keep stderr small
        if cmd and os.path.basename(cmd[0]).lower().startswith("ffmpeg"):
            cmd = [cmd[0], "-loglevel", "error", "-nostats"] + cmd[1:]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL if not capture_output else subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        handle = ProcessHandle(proc=proc, context=context)
        with self._lock:
            self._procs.append(handle)

        # Background drain to prevent deadlock
        stderr_chunks: list[bytes] = []
        stdout_chunks: list[bytes] = []

        def _drain_stderr() -> None:
            try:
                while True:
                    chunk = proc.stderr.read(8192)
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
            except Exception:
                pass

        def _drain_stdout() -> None:
            if not capture_output:
                return
            try:
                while True:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    stdout_chunks.append(chunk)
            except Exception:
                pass

        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_out = threading.Thread(target=_drain_stdout, daemon=True)
        t_err.start()
        t_out.start()

        try:
            t0 = time.monotonic()
            poll_interval = 0.15
            while True:
                ret = proc.poll()
                if ret is not None:
                    t_err.join(timeout=2)
                    t_out.join(timeout=2)
                    return (
                        ret,
                        b"".join(stdout_chunks),
                        b"".join(stderr_chunks),
                    )

                if self.cancel.is_set():
                    self._kill_one(handle)
                    raise CancelledError(f"Cancelled while running: {context}")

                if time.monotonic() - t0 > timeout_s:
                    self._kill_one(handle)
                    raise TimeoutError(
                        f"{context}: deadline {timeout_s:.0f}s excedido"
                    )

                time.sleep(poll_interval)

        finally:
            with self._lock:
                if handle in self._procs:
                    self._procs.remove(handle)

    def run_checked(
        self,
        cmd: list[str],
        *,
        context: str = "ffmpeg",
        timeout_s: float = 600,
    ) -> bytes:
        """run + raise on non-zero exit. Returns stderr (useful for parsing)."""
        ret, _stdout, stderr = self.run(
            cmd, context=context, timeout_s=timeout_s, capture_output=False
        )
        if ret != 0:
            tail = stderr.decode("utf-8", errors="replace")[-700:]
            raise RuntimeError(f"{context}: ffmpeg exit {ret}\n{tail}")
        return stderr

    # ── Cleanup ─────────────────────────────────────────────────────────────

    def _kill_one(self, handle: ProcessHandle) -> None:
        try:
            if handle.proc.poll() is None:
                handle.proc.kill()
                handle.proc.wait(timeout=5)
        except Exception:
            pass

    def kill_all(self) -> int:
        """Kill every live process. Returns count killed."""
        with self._lock:
            snapshot = list(self._procs)
        killed = 0
        for h in snapshot:
            if h.proc.poll() is None:
                self._kill_one(h)
                killed += 1
        with self._lock:
            self._procs.clear()
        return killed

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise CancelledError("cancelled")

    def __enter__(self) -> "ProcessManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.kill_all()


# ── Global atexit hook: catches Ctrl+C, app crashes, etc. ────────────────────

@atexit.register
def _shutdown_all() -> None:
    """Kill any ffmpeg children we forgot about."""
    with ProcessManager._global_lock:
        managers = list(ProcessManager._global_registry)
    for pm in managers:
        try:
            pm.kill_all()
        except Exception:
            pass

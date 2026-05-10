"""
Centralized ffmpeg process registry.

Problem solved
--------------
When the pipeline raises mid-render, lingering ffmpeg children can keep temp
files open and break cleanup on Windows. The cancel button also needs one
place that knows about every running subprocess.

Solution
--------
Every managed ffmpeg/ffprobe invocation goes through this registry. Each
ProcessManager tracks its live Popen handles and kills them on cancellation,
timeout, exception, normal context exit, or process shutdown.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Optional


class CancelledError(Exception):
    """Raised when the user requested cancellation."""


@dataclass
class ProcessHandle:
    proc: subprocess.Popen
    context: str
    started: float = field(default_factory=time.monotonic)


class ProcessManager:
    """
    Owns ffmpeg/ffprobe subprocesses and guarantees cleanup.

    The registry is thread-safe: process handles are mutated under a lock and
    kill_all() works on a snapshot to avoid mutation during cleanup.
    """

    _global_lock = threading.Lock()
    _global_registry: weakref.WeakSet["ProcessManager"] = weakref.WeakSet()

    def __init__(self, cancel: Optional[threading.Event] = None) -> None:
        self.cancel = cancel or threading.Event()
        self._lock = threading.Lock()
        self._procs: list[ProcessHandle] = []
        with ProcessManager._global_lock:
            ProcessManager._global_registry.add(self)

    def run(
        self,
        cmd: list[str],
        *,
        context: str = "ffmpeg",
        timeout_s: float = 600,
        capture_output: bool = True,
    ) -> tuple[int, bytes, bytes]:
        """
        Run cmd as a managed subprocess.

        stderr is drained in a background thread to avoid pipe-buffer deadlocks.
        Returns (exit_code, stdout_bytes, stderr_bytes). Raises CancelledError
        if the cancel event is set, and TimeoutError if the deadline is reached.
        """
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

        stderr_chunks: list[bytes] = []
        stdout_chunks: list[bytes] = []

        def _drain_stderr() -> None:
            try:
                while proc.stderr:
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
                while proc.stdout:
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
            started = time.monotonic()
            while True:
                ret = proc.poll()
                if ret is not None:
                    t_err.join(timeout=2)
                    t_out.join(timeout=2)
                    return ret, b"".join(stdout_chunks), b"".join(stderr_chunks)

                if self.cancel.is_set():
                    self._kill_one(handle)
                    raise CancelledError(f"Cancelled while running: {context}")

                if time.monotonic() - started > timeout_s:
                    self._kill_one(handle)
                    raise TimeoutError(f"{context}: deadline {timeout_s:.0f}s excedido")

                time.sleep(0.15)

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
        """Run a managed command and raise RuntimeError on non-zero exit."""
        ret, _stdout, stderr = self.run(
            cmd,
            context=context,
            timeout_s=timeout_s,
            capture_output=False,
        )
        if ret != 0:
            tail = stderr.decode("utf-8", errors="replace")[-700:]
            raise RuntimeError(f"{context}: ffmpeg exit {ret}\n{tail}")
        return stderr

    def _kill_one(self, handle: ProcessHandle) -> None:
        try:
            if handle.proc.poll() is None:
                handle.proc.kill()
                handle.proc.wait(timeout=5)
        except Exception:
            pass

    def kill_all(self) -> int:
        """Kill every live process. Returns the number of processes killed."""
        with self._lock:
            snapshot = list(self._procs)
        killed = 0
        for handle in snapshot:
            if handle.proc.poll() is None:
                self._kill_one(handle)
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


@atexit.register
def _shutdown_all() -> None:
    """Kill any managed ffmpeg children still alive on interpreter shutdown."""
    with ProcessManager._global_lock:
        managers = list(ProcessManager._global_registry)
    for manager in managers:
        try:
            manager.kill_all()
        except Exception:
            pass

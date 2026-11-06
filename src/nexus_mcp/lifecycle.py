"""Server lifecycle: stop when the session is done, and always exit the process.

The stdio server stops for one of these reasons:

- The client closes stdin (the normal end of a session). FastMCP returns from `run()`.
- The client sends SIGTERM, SIGINT or SIGHUP.
- The parent process is gone, so the server is an orphan (`NEXUS_EXIT_ON_PARENT_EXIT`).
- No tool call came for `NEXUS_IDLE_TIMEOUT` seconds (default 3 hours, 0 turns it off).

Each reason goes through `finish()`. It waits for a running index, saves the graph,
flushes the logs and then ends the process with `os._exit`. A normal interpreter exit can
hang: threads that read stdin or index files are joined at exit (measured: after SIGTERM
the server logged "shutdown complete" and was still alive 20 s later). A watchdog ends the
process if the cleanup itself hangs, and a second signal ends it at once.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

EXIT_CLEAN = 0
EXIT_FORCED = 1

# The parent at import time, read as early as possible: a parent that exits during the
# slow imports that follow must still count as a change.
_START_PARENT_PID = os.getppid()

# Time that the watchdog adds to the cleanup timeout before it ends the process.
_WATCHDOG_GRACE_S = 5.0


class ActivityTracker:
    """Record when the last tool call ended and how many calls run now.

    The lock exists because tool calls run on several threads, and the monitor thread
    reads both values together: a call that runs is never idle time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._in_flight = 0

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1
            self._last = time.monotonic()

    def end(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._last = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since the last call ended, or 0.0 while a call runs."""
        with self._lock:
            if self._in_flight:
                return 0.0
            return time.monotonic() - self._last


activity = ActivityTracker()


def _index_running() -> bool:
    """True while an index or a background reindex holds the pipeline lock."""
    from nexus_mcp import core_api

    return core_api._pipeline_lock.locked()


class LifecycleMonitor(threading.Thread):
    """Daemon thread that asks for a shutdown when the server is idle or an orphan."""

    def __init__(
        self,
        *,
        idle_timeout_s: float,
        check_interval_s: float,
        exit_on_parent_exit: bool,
        tracker: Optional[ActivityTracker] = None,
        busy: Callable[[], bool] = _index_running,
        on_trigger: Optional[Callable[[str], None]] = None,
        parent_pid: Optional[int] = None,
    ) -> None:
        super().__init__(name="nexus-lifecycle", daemon=True)
        self.idle_timeout_s = idle_timeout_s
        self.check_interval_s = max(0.01, check_interval_s)
        self.exit_on_parent_exit = exit_on_parent_exit
        self._tracker = tracker or activity
        self._busy = busy
        self._on_trigger = on_trigger or request_shutdown
        self._parent_pid = _START_PARENT_PID if parent_pid is None else parent_pid
        self._stop_event = threading.Event()
        self.reason: Optional[str] = None

    def check(self) -> Optional[str]:
        """Return the reason to stop now, or None."""
        # PID 1 as the first parent means a container init or launchd started the
        # server: a change from it says nothing about the client.
        if self.exit_on_parent_exit and self._parent_pid > 1:
            if os.getppid() != self._parent_pid:
                return f"parent process {self._parent_pid} exited"
        if self.idle_timeout_s > 0:
            idle = self._tracker.idle_seconds()
            if idle >= self.idle_timeout_s and not self._busy():
                return f"no tool call for {idle:.0f}s (NEXUS_IDLE_TIMEOUT={self.idle_timeout_s:g})"
        return None

    def run(self) -> None:
        while not self._stop_event.wait(self.check_interval_s):
            try:
                reason = self.check()
            except Exception as e:  # noqa: BLE001 - the monitor must never die silently
                logger.warning("Lifecycle check failed: %s", e)
                continue
            if reason:
                self.reason = reason
                self._on_trigger(reason)
                return

    def stop(self) -> None:
        self._stop_event.set()


# --- Shutdown coordination ----------------------------------------------------------

_lock = threading.Lock()
_requested = threading.Event()
_cleanup_started = threading.Event()
_watchdog_cancel: Optional[threading.Event] = None
_watchdog: Optional[threading.Thread] = None
_signal_count = 0
_shutdown_timeout_s = 10.0


def _hard_exit(code: int) -> None:
    """End the process now. Tests replace this function."""
    os._exit(code)


def _flush_output() -> None:
    """Flush the log handlers and the standard streams. Never raises."""
    for handler in list(logging.root.handlers):
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass


def _arm_watchdog() -> None:
    """Start the thread that ends the process if the shutdown hangs. Runs once."""
    global _watchdog, _watchdog_cancel
    with _lock:
        if _watchdog is not None:
            return
        deadline = _shutdown_timeout_s + _WATCHDOG_GRACE_S
        cancel = _watchdog_cancel = threading.Event()

        def _run() -> None:
            if not cancel.wait(deadline):
                # No log call here: a full stderr pipe is one way the shutdown hangs.
                _hard_exit(EXIT_FORCED)

        _watchdog = threading.Thread(target=_run, name="nexus-exit-watchdog", daemon=True)
        _watchdog.start()


def request_shutdown(reason: str) -> None:
    """Ask the server to stop. Safe to call from any thread, more than once.

    The main thread does the cleanup: a SIGTERM to this process unwinds `server.run()`
    into `finish()`. The watchdog ends the process if that does not happen in time.
    """
    if _requested.is_set():
        return
    _requested.set()
    logger.info("Nexus-MCP is stopping: %s", reason)
    _arm_watchdog()
    if os.name == "posix":
        os.kill(os.getpid(), signal.SIGTERM)
    else:  # pragma: no cover - Windows has no SIGTERM handler; SIGINT reaches main
        import _thread

        _thread.interrupt_main(signal.SIGINT)


def _on_signal(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
    """First signal: unwind `server.run()`. A second signal: end the process now."""
    global _signal_count
    _signal_count += 1
    if _signal_count > 1:
        logger.warning("Second signal %s: exiting now.", signum)
        _flush_output()
        _hard_exit(EXIT_FORCED)
        return
    _requested.set()
    _arm_watchdog()
    if _cleanup_started.is_set():
        return  # finish() runs already; let it complete
    logger.info("Received signal %s, shutting down...", signum)
    raise SystemExit(EXIT_CLEAN)


def install_signal_handlers(shutdown_timeout_s: float) -> None:
    """Route SIGTERM, SIGINT and SIGHUP to the shutdown. Call from the main thread."""
    global _shutdown_timeout_s
    _shutdown_timeout_s = max(0.0, shutdown_timeout_s)
    signals = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)  # the terminal that started the server closed
    for sig in signals:
        signal.signal(sig, _on_signal)


def _cleanup(timeout_s: float) -> None:
    from nexus_mcp.state import get_state

    get_state().shutdown(wait_for_index_s=timeout_s)


def finish(exit_code: int = EXIT_CLEAN) -> None:
    """Clean up, then end the process. Runs once; it does not return.

    The cleanup runs on its own thread with a deadline, so a hang in it (a lock, a
    native library) cannot keep the process alive.
    """
    _cleanup_started.set()
    _arm_watchdog()
    timeout_s = _shutdown_timeout_s
    worker = threading.Thread(
        target=_cleanup_safely, args=(timeout_s,), name="nexus-shutdown", daemon=True
    )
    worker.start()
    # The cleanup waits up to timeout_s for an index; the graph save needs a little more.
    worker.join(timeout_s + 2.0)
    if worker.is_alive():
        logger.warning("Shutdown cleanup did not finish in %.0fs; exiting anyway.", timeout_s)
        exit_code = EXIT_FORCED
    _flush_output()
    _cancel_watchdog()
    _hard_exit(exit_code)


def _cleanup_safely(timeout_s: float) -> None:
    try:
        _cleanup(timeout_s)
    except Exception as e:  # noqa: BLE001 - an error must not stop the exit
        logger.warning("Shutdown cleanup failed: %s", e)


def start_monitor(settings) -> LifecycleMonitor:
    """Start the idle and orphan monitor from the settings and return it."""
    monitor = LifecycleMonitor(
        idle_timeout_s=settings.idle_timeout_s,
        check_interval_s=settings.lifecycle_check_interval_s,
        exit_on_parent_exit=settings.exit_on_parent_exit,
    )
    monitor.start()
    return monitor


def _cancel_watchdog() -> None:
    global _watchdog, _watchdog_cancel
    with _lock:
        if _watchdog_cancel is not None:
            _watchdog_cancel.set()
        _watchdog = None
        _watchdog_cancel = None


def reset() -> None:
    """Reset the module state (for tests)."""
    global _signal_count, _shutdown_timeout_s, activity
    _cancel_watchdog()
    _requested.clear()
    _cleanup_started.clear()
    _signal_count = 0
    _shutdown_timeout_s = 10.0
    activity = ActivityTracker()

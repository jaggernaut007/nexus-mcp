"""Tests for the server lifecycle: idle timeout, orphan exit and a guaranteed exit."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from nexus_mcp import core_api, lifecycle
from nexus_mcp.config import Settings
from nexus_mcp.state import get_state
from tests.conftest import _call_tool

# --- ActivityTracker ----------------------------------------------------------------


def test_activity_tracker_idle_grows_without_calls():
    tracker = lifecycle.ActivityTracker()
    time.sleep(0.05)
    assert tracker.idle_seconds() >= 0.05


def test_activity_tracker_call_in_flight_is_never_idle():
    tracker = lifecycle.ActivityTracker()
    tracker.begin()
    time.sleep(0.05)
    assert tracker.idle_seconds() == 0.0
    tracker.end()
    assert 0.0 <= tracker.idle_seconds() < 0.05


def test_activity_tracker_end_without_begin_does_not_go_negative():
    tracker = lifecycle.ActivityTracker()
    tracker.end()
    tracker.begin()
    assert tracker.idle_seconds() == 0.0


# --- LifecycleMonitor.check ---------------------------------------------------------


def _monitor(**kw):
    defaults = dict(
        idle_timeout_s=0.0, check_interval_s=0.01, exit_on_parent_exit=False,
        tracker=lifecycle.ActivityTracker(), busy=lambda: False, on_trigger=lambda r: None,
    )
    defaults.update(kw)
    return lifecycle.LifecycleMonitor(**defaults)


def test_check_idle_timeout_reached_returns_reason():
    tracker = lifecycle.ActivityTracker()
    time.sleep(0.02)
    reason = _monitor(idle_timeout_s=0.01, tracker=tracker).check()
    assert reason and "no tool call" in reason


def test_check_idle_timeout_zero_is_off():
    tracker = lifecycle.ActivityTracker()
    time.sleep(0.02)
    assert _monitor(idle_timeout_s=0.0, tracker=tracker).check() is None


def test_check_idle_while_index_runs_waits():
    tracker = lifecycle.ActivityTracker()
    time.sleep(0.02)
    assert _monitor(idle_timeout_s=0.01, tracker=tracker, busy=lambda: True).check() is None


def test_check_parent_changed_returns_reason():
    monitor = _monitor(exit_on_parent_exit=True, parent_pid=os.getppid() + 100_000)
    assert "parent process" in monitor.check()


def test_check_parent_same_returns_none():
    assert _monitor(exit_on_parent_exit=True).check() is None


def test_check_first_parent_pid_1_is_ignored():
    """A server that init or launchd started has PID 1 as parent from the start."""
    assert _monitor(exit_on_parent_exit=True, parent_pid=1).check() is None


def test_monitor_run_triggers_once_and_stops():
    reasons = []
    tracker = lifecycle.ActivityTracker()
    monitor = _monitor(idle_timeout_s=0.01, tracker=tracker, on_trigger=reasons.append)
    monitor.start()
    monitor.join(timeout=2)
    assert not monitor.is_alive()
    assert len(reasons) == 1 and monitor.reason == reasons[0]


def test_monitor_stop_ends_thread_without_trigger():
    reasons = []
    monitor = _monitor(idle_timeout_s=3600, on_trigger=reasons.append)
    monitor.start()
    monitor.stop()
    monitor.join(timeout=2)
    assert not monitor.is_alive() and reasons == []


def test_monitor_survives_a_failing_check(monkeypatch):
    reasons = []
    calls = {"n": 0}
    monitor = _monitor(on_trigger=reasons.append)

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return "second check"

    monkeypatch.setattr(monitor, "check", flaky)
    monitor.start()
    monitor.join(timeout=2)
    assert reasons == ["second check"]


def test_start_monitor_reads_settings(monkeypatch):
    monkeypatch.setenv("NEXUS_IDLE_TIMEOUT", "0")
    monkeypatch.setenv("NEXUS_LIFECYCLE_CHECK_INTERVAL", "0.01")
    monitor = lifecycle.start_monitor(Settings())
    try:
        assert monitor.is_alive() and monitor.idle_timeout_s == 0.0
    finally:
        monitor.stop()
        monitor.join(timeout=2)


# --- Settings -----------------------------------------------------------------------


def test_settings_lifecycle_defaults():
    s = Settings()
    assert s.idle_timeout_s == 10800.0
    assert s.exit_on_parent_exit is True
    assert s.shutdown_timeout_s == 10.0


def test_settings_lifecycle_from_env(monkeypatch):
    monkeypatch.setenv("NEXUS_IDLE_TIMEOUT", "60")
    monkeypatch.setenv("NEXUS_EXIT_ON_PARENT_EXIT", "false")
    monkeypatch.setenv("NEXUS_SHUTDOWN_TIMEOUT", "3")
    s = Settings()
    assert (s.idle_timeout_s, s.exit_on_parent_exit, s.shutdown_timeout_s) == (60.0, False, 3.0)


# --- Tool calls count as activity ---------------------------------------------------


def test_tool_call_resets_idle_time():
    from nexus_mcp.server import create_server

    mcp = create_server()
    time.sleep(0.05)
    assert lifecycle.activity.idle_seconds() >= 0.05
    asyncio.run(_call_tool(mcp, "status"))
    assert lifecycle.activity.idle_seconds() < 0.05


# --- finish() and signals -----------------------------------------------------------


def test_finish_cleans_up_then_exits_clean(monkeypatch):
    done = []
    monkeypatch.setattr(lifecycle, "_cleanup", lambda timeout: done.append(timeout))
    lifecycle.finish(lifecycle.EXIT_CLEAN)
    assert done == [10.0]
    assert lifecycle.exits == [lifecycle.EXIT_CLEAN]


def test_finish_cleanup_that_hangs_still_exits(monkeypatch):
    monkeypatch.setattr(lifecycle, "_shutdown_timeout_s", 0.05)
    release = threading.Event()
    monkeypatch.setattr(lifecycle, "_cleanup", lambda timeout: release.wait(10))
    start = time.monotonic()
    lifecycle.finish(lifecycle.EXIT_CLEAN)
    release.set()
    assert time.monotonic() - start < 5
    assert lifecycle.exits == [lifecycle.EXIT_FORCED]


def test_finish_cleanup_that_raises_still_exits(monkeypatch):
    def boom(timeout):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(lifecycle, "_cleanup", boom)
    lifecycle.finish(lifecycle.EXIT_CLEAN)
    assert lifecycle.exits == [lifecycle.EXIT_CLEAN]


def test_watchdog_exits_when_nothing_finishes(monkeypatch):
    monkeypatch.setattr(lifecycle, "_shutdown_timeout_s", 0.0)
    monkeypatch.setattr(lifecycle, "_WATCHDOG_GRACE_S", 0.05)
    lifecycle._arm_watchdog()
    deadline = time.monotonic() + 3
    while not lifecycle.exits and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lifecycle.exits == [lifecycle.EXIT_FORCED]


def test_first_signal_raises_system_exit_clean():
    with pytest.raises(SystemExit) as exc:
        lifecycle._on_signal(signal.SIGTERM, None)
    assert exc.value.code == lifecycle.EXIT_CLEAN


def test_second_signal_exits_at_once():
    with pytest.raises(SystemExit):
        lifecycle._on_signal(signal.SIGTERM, None)
    lifecycle._on_signal(signal.SIGINT, None)
    assert lifecycle.exits == [lifecycle.EXIT_FORCED]


def test_signal_during_cleanup_does_not_interrupt_it():
    lifecycle._signal_count = 0
    lifecycle._cleanup_started.set()
    lifecycle._on_signal(signal.SIGTERM, None)  # must not raise into finish()
    assert lifecycle.exits == []


def test_request_shutdown_is_sent_once(monkeypatch):
    sent = []
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: sent.append(sig))
    lifecycle.request_shutdown("test")
    lifecycle.request_shutdown("test again")
    assert sent == [signal.SIGTERM]


# --- state.shutdown waits for a running index --------------------------------------


def test_shutdown_skips_graph_save_while_index_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path))
    from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

    state = get_state()
    state.settings = Settings()
    state.graph_engine = RustworkxCodeGraph()
    state.codebase_path = tmp_path
    assert core_api._pipeline_lock.acquire(blocking=False)
    try:
        state.shutdown(wait_for_index_s=0.05)
    finally:
        core_api._pipeline_lock.release()
    assert not (tmp_path / "graph.db").exists()


def test_shutdown_saves_graph_after_index_ends(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path))
    from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

    state = get_state()
    state.settings = Settings()
    state.graph_engine = RustworkxCodeGraph()
    state.codebase_path = tmp_path
    assert core_api._pipeline_lock.acquire(blocking=False)
    threading.Timer(0.1, core_api._pipeline_lock.release).start()
    state.shutdown(wait_for_index_s=5)
    assert (tmp_path / "graph.db").exists()
    assert not core_api._pipeline_lock.locked()


# --- The real process ---------------------------------------------------------------

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "test", "version": "0"}}}


def _server_env(tmp_path, **extra):
    env = {**os.environ, "NEXUS_STORAGE_DIR": str(tmp_path), "NEXUS_WARM_START": "false",
           "NEXUS_AUTO_RESTORE": "false", "NEXUS_LIFECYCLE_CHECK_INTERVAL": "0.2"}
    env.update(extra)
    return env


def _start_server(tmp_path, **extra):
    proc = subprocess.Popen(
        [sys.executable, "-m", "nexus_mcp.server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_server_env(tmp_path, **extra),
    )
    proc.stdin.write((json.dumps(INIT) + "\n").encode())
    proc.stdin.flush()
    assert b'"result"' in proc.stdout.readline()
    return proc


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_server_process_exits_on_sigterm(tmp_path):
    """Before this fix the process logged 'shutdown complete' and then hung."""
    proc = _start_server(tmp_path)
    try:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


def test_server_process_exits_on_stdin_close(tmp_path):
    proc = _start_server(tmp_path)
    try:
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_server_process_exits_after_idle_timeout(tmp_path):
    proc = _start_server(tmp_path, NEXUS_IDLE_TIMEOUT="0.5")
    try:
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


@pytest.mark.skipif(os.name != "posix", reason="POSIX reparenting")
def test_server_process_exits_when_parent_dies(tmp_path):
    """The client dies but stdin stays open (another process holds the pipe)."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (json.dumps(INIT) + "\n").encode())
    # The launcher exits only after the server answered, so the server saw it as parent.
    launcher = (
        "import subprocess, sys\n"
        f"p = subprocess.Popen([sys.executable, '-m', 'nexus_mcp.server'], stdin={read_fd},"
        " stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)\n"
        "assert b'result' in p.stdout.readline()\n"
        "print(p.pid, flush=True)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", launcher], env=_server_env(tmp_path), pass_fds=[read_fd],
        capture_output=True, text=True, timeout=10,
    )
    os.close(read_fd)
    pid = int(out.stdout.strip())
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the orphaned server is still running")
    finally:
        os.close(write_fd)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

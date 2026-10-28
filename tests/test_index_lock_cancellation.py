"""A cancelled index() call must keep `_pipeline_lock` tied to the indexing work."""

import asyncio
import threading

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp.config import reset_settings


class _Result:
    def to_dict(self):
        return {"total_files": 0}


class _BlockingPipeline:
    """Stands in for IndexingPipeline (the slow boundary). `index` waits for `release`."""

    vector_engine = bm25_engine = graph_engine = None

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def index(self, path, cb):
        self.started.set()
        self.release.wait(10)
        return _Result()

    incremental_index = index


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_AUTO_WATCH", "false")
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / ".nexus"))
    reset_settings()
    fake = _BlockingPipeline()
    core_api._pipeline = fake
    yield fake
    fake.release.set()


def test_index_cancelled_while_waiting_for_lock_does_not_leak_it(tmp_path, pipeline):
    pipeline.release.set()  # the index itself returns at once

    async def scenario():
        core_api._pipeline_lock.acquire()  # another index holds the lock
        task = asyncio.create_task(core_api.index(str(tmp_path)))
        await asyncio.sleep(0.1)
        task.cancel()
        core_api._pipeline_lock.release()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert core_api._pipeline_lock.acquire(timeout=5), "index() leaked _pipeline_lock"
    core_api._pipeline_lock.release()


def test_index_cancelled_mid_index_keeps_lock_until_work_ends(tmp_path, pipeline):
    async def scenario():
        task = asyncio.create_task(core_api.index(str(tmp_path)))
        await asyncio.to_thread(pipeline.started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The index thread still runs, so the lock must still be held.
        held = not core_api._pipeline_lock.acquire(blocking=False)
        if not held:
            core_api._pipeline_lock.release()
        pipeline.release.set()
        return held

    assert asyncio.run(scenario()), "the lock was freed while the index thread still runs"
    assert core_api._pipeline_lock.acquire(timeout=5)
    core_api._pipeline_lock.release()

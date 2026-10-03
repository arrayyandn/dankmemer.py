import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from store_helpers import database

from dankmemer import (
    CoordinationConfig,
    CoordinationMode,
    DankMemer,
    DisabledPolling,
    EventConfig,
    EventDelivery,
    LotteryResult,
    PollingConfig,
    PollingResource,
)


def worker(
    backend: str, location: str, schema: str, directory: Path, name: str, mode: str
) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(root)
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(root / "tests" / "shared_event_worker.py"),
            backend,
            location,
            schema,
            str(directory),
            name,
            mode,
        ],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


async def wait_files(
    paths: Sequence[Path], processes: Sequence[subprocess.Popen[str]]
) -> None:
    async with asyncio.timeout(10):
        while not all(path.exists() for path in paths):
            for process in processes:
                if process.poll() is not None and process.returncode != 0:
                    output, errors = process.communicate()
                    pytest.fail(f"shared worker failed: {output}\n{errors}")
            await asyncio.sleep(0.01)


async def stop_workers(
    directory: Path, processes: Sequence[subprocess.Popen[str]]
) -> None:
    (directory / "stop").touch()
    for process in processes:
        try:
            output, errors = await asyncio.to_thread(process.communicate, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, errors = await asyncio.to_thread(process.communicate, timeout=5)
            pytest.fail(f"shared worker did not stop: {output}\n{errors}")
        assert process.returncode == 0, output + errors


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mode", ["fanout", "compete"])
async def test_separate_processes_share_polling_and_subscription_ownership(
    tmp_path: Path, backend: str, mode: str
) -> None:
    async with database(backend, tmp_path) as (store, location, schema):
        processes = [
            worker(backend, location, schema, tmp_path, name, mode)
            for name in ("left", "right")
        ]
        try:
            await wait_files(
                [tmp_path / "left.ready", tmp_path / "right.ready"], processes
            )
            (tmp_path / "go").touch()
            if mode == "fanout":
                await wait_files(
                    [tmp_path / "left.callback", tmp_path / "right.callback"], processes
                )
            else:
                async with asyncio.timeout(10):
                    while not tuple(tmp_path.glob("*.callback")):
                        await asyncio.sleep(0.01)
            async with asyncio.timeout(10):
                while await store.count_pending_callbacks():
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.3)
            callbacks = tuple(tmp_path.glob("*.callback"))
            assert len(callbacks) == (2 if mode == "fanout" else 1)
            assert all(path.read_text() == "100" for path in callbacks)
            attempts = [
                line
                for path in tmp_path.glob("*.requests")
                for line in path.read_text().splitlines()
            ]
            assert attempts == ["lottery"]
            assert await store.count_pending_callbacks() == 0
        finally:
            await stop_workers(tmp_path, processes)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_crashed_process_loses_lease_and_pending_callback_replays(
    tmp_path: Path, backend: str
) -> None:
    async with database(backend, tmp_path) as (store, location, schema):
        process = worker(backend, location, schema, tmp_path, "crashed", "crash")
        try:
            await wait_files([tmp_path / "crashed.ready"], [process])
            (tmp_path / "go").touch()
            await wait_files([tmp_path / "crashed.callback"], [process])
            output, errors = await asyncio.to_thread(process.communicate, timeout=5)
            assert process.returncode == 0, output + errors
            assert await store.count_pending_callbacks() == 1
            disabled = DisabledPolling()
            client = DankMemer(
                "test-token",
                event_store=store,
                events=EventConfig(delivery=EventDelivery.DURABLE, emit_initial=True),
                coordination=CoordinationConfig(
                    mode=CoordinationMode.SHARED,
                    application_id="test-application",
                    lease_seconds=2.0,
                    heartbeat_seconds=0.2,
                    check_seconds=0.05,
                ),
                polling=PollingConfig(
                    global_boosts=disabled,
                    lottery=disabled,
                    merchant_trades=disabled,
                    blogs=disabled,
                    changelogs=disabled,
                ),
                silent=True,
            )
            seen: asyncio.Queue[LotteryResult] = asyncio.Queue()

            @client.event(subscription_id="consumer.crashed")
            async def on_lottery_result(result: LotteryResult) -> None:
                await seen.put(result)

            async with client:
                result = await asyncio.wait_for(seen.get(), 5)
                assert result.winnings == 100
                async with asyncio.timeout(5):
                    while await store.count_pending_callbacks():
                        await asyncio.sleep(0.01)
                assert not client.active_polling_resources
            checkpoint = await store.read_checkpoint(PollingResource.LOTTERY)
            assert checkpoint is not None and checkpoint.version == 1
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.communicate, timeout=5)

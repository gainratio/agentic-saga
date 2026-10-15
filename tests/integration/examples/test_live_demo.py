from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import pytest
import pytest_asyncio
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from agentic_saga.contracts.runtime import SagaStatus
from agentic_saga.contracts.trace import RunTrace
from examples.ecommerce.domain import ScenarioName
from examples.ecommerce.live import follow_run

pytestmark = pytest.mark.temporal

_DEADLINE_SECONDS = 120.0


@pytest_asyncio.fixture
async def environment() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as started:
        yield started


@pytest.mark.asyncio
async def test_follow_run_publishes_unfinished_steps_before_the_final_outcome(
    environment: WorkflowEnvironment,
) -> None:
    # Given
    published: list[RunTrace] = []

    # When
    run = await follow_run(
        environment.client,
        ScenarioName.BUSINESS_FAILURE,
        published.append,
        step_delay_seconds=0.2,
        poll_seconds=0.02,
    )

    # Then
    sizes = [len(trace.events) for trace in published]
    unfinished = [trace for trace in published if trace.finished_at is None]
    assert run.state.status is SagaStatus.COMPENSATED_VERIFIED
    assert published[-1].outcome is SagaStatus.COMPENSATED_VERIFIED
    assert published[-1].finished_at is not None
    assert len(unfinished) >= 3
    assert {trace.outcome for trace in unfinished} >= {SagaStatus.RUNNING, SagaStatus.COMPENSATING}
    assert sizes == sorted(sizes)


def _read_line(process: subprocess.Popen[str], deadline: float) -> str:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=max(0.0, deadline - time.monotonic())):
            raise AssertionError("demo command produced no output before the deadline")
    return process.stdout.readline()


def _wait_for(process: subprocess.Popen[str], marker: str, seen: list[str]) -> str:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    while time.monotonic() < deadline:
        line = _read_line(process, deadline)
        if not line:
            raise AssertionError(f"demo command exited early:\n{''.join(seen)}")
        seen.append(line)
        if marker in line:
            return line
    raise AssertionError(f"missing {marker!r}:\n{''.join(seen)}")


@contextmanager
def _demo_command(address: str) -> Iterator[subprocess.Popen[str]]:
    command = [sys.executable, "-m", "examples.ecommerce.live", "--no-open"]
    arguments = ["--temporal-address", address, "--step-delay", "0"]
    process = subprocess.Popen(
        [*command, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - loopback test URL
        return bytes(response.read())


@pytest.mark.asyncio
async def test_demo_command_serves_the_live_run_in_the_recorder(
    environment: WorkflowEnvironment,
) -> None:
    # Given
    address = environment.client.service_client.config.target_host
    seen: list[str] = []

    with _demo_command(address) as process:
        # When
        url = _wait_for(process, "Recorder:", seen).split("Recorder:", 1)[1].split()[0]
        _wait_for(process, "Live run finished", seen)
        index = json.loads(_get(f"{url}/traces/index.json"))
        trace_ref = index["runs"][0]["trace_ref"]
        trace = RunTrace.model_validate_json(_get(f"{url}/traces/{trace_ref}"))
        page = _get(f"{url}/").decode()
        process.send_signal(signal.SIGINT)
        code = process.wait(timeout=10)

    # Then
    output = "".join(seen)
    assert "no API key" in output
    assert index["live"] is True
    assert trace.outcome is SagaStatus.COMPENSATED_VERIFIED
    assert "<div id=" in page
    assert code == 0

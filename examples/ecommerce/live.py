"""Run one checkout saga for real and watch each step in your browser as it happens.

`uv run poe demo` starts a throwaway local Temporal server, runs the saga with a scripted agent
and simulated providers (no API key, no model calls), and serves the Flight Recorder page. The
page polls the run's record every second, so forward steps, the failure, and each undo appear
while the Temporal workflow is still running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from agentic_saga.contracts.trace import RunTrace, TraceEvent
from agentic_saga.demo import RecorderServer, materialize_recorder_site, serve_recorder
from agentic_saga.temporal import connect_local_client, project_run_trace
from agentic_saga.temporal.contracts import WorkflowState
from agentic_saga.temporal.workflow import AgenticSagaWorkflow
from examples.ecommerce.demo import (
    DEFINITION_VERSION,
    EcommerceRun,
    new_saga_id,
    run_with_client,
)
from examples.ecommerce.domain import ScenarioName

type TracePublisher = Callable[[RunTrace], None]

_MAX_STEP_DELAY: Final[float] = 10.0
_MAX_PORT: Final[int] = 65535
_STORIES: Final[dict[ScenarioName, str]] = {
    ScenarioName.BUSINESS_FAILURE: "delivery fails after payment, so earlier steps are undone",
    ScenarioName.HAPPY_PATH: "every step works",
    ScenarioName.LOST_RESPONSE: "the payment reply is lost and is reconciled, not repeated",
    ScenarioName.COMPENSATION_FAILURE: "the refund cannot be proven, so a person must decide",
}


class LiveSite:
    """A recorder site whose single run is replaced, file by file, as the saga advances."""

    def __init__(self, workspace: Path, scenario: ScenarioName) -> None:
        self.root = workspace / "site"
        self._workspace = workspace
        self._scenario = scenario
        self._version = 0

    def publish(self, trace: RunTrace) -> None:
        """Write the newest trace under a fresh name, then point the index at it."""
        self._version += 1
        stage = self._workspace / f"stage-{self._version}"
        site = self._materialize(stage, trace)
        if not self.root.exists():
            os.replace(site, self.root)
            site = self.root
        self._swap_trace(site)
        shutil.rmtree(stage)

    def _materialize(self, stage: Path, trace: RunTrace) -> Path:
        stage.mkdir()
        site = stage / "site"
        materialize_recorder_site(
            site,
            {self._scenario.value: trace},
            presentation="ecommerce",
            default_run_id=self._scenario.value,
            recording_mode="scripted",
        )
        return site

    def _swap_trace(self, source: Path) -> None:
        traces = self.root / "traces"
        name = self._trace_name(self._version)
        os.replace(source / "traces" / f"{self._scenario.value}.json", traces / name)
        index = json.loads((source / "traces" / "index.json").read_bytes())
        index["runs"][0]["trace_ref"] = name
        index["live"] = True
        staged_index = traces / "index.json.next"
        staged_index.write_text(f"{json.dumps(index, indent=2)}\n", encoding="utf-8")
        os.replace(staged_index, traces / "index.json")
        # Keep the previous file one more round for a browser that is mid-download.
        (traces / self._trace_name(self._version - 2)).unlink(missing_ok=True)

    def _trace_name(self, version: int) -> str:
        return f"{self._scenario.value}-{version:04d}.json"


async def follow_run(
    client: Client,
    scenario: ScenarioName,
    publish: TracePublisher,
    *,
    step_delay_seconds: float = 0.0,
    poll_seconds: float = 0.25,
) -> EcommerceRun:
    """Run one scenario and publish its trace each time the workflow records new events."""
    saga_id = new_saga_id(scenario)
    task = asyncio.create_task(
        run_with_client(client, scenario, saga_id=saga_id, step_delay_seconds=step_delay_seconds)
    )
    handle = client.get_workflow_handle_for(AgenticSagaWorkflow.run, saga_id)
    seen = 0
    while not task.done():
        seen = await _publish_new_events(handle, publish, seen)
        await asyncio.wait({task}, timeout=poll_seconds)
    run = task.result()
    publish(run.trace)
    return run


async def _publish_new_events(
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
    publish: TracePublisher,
    seen: int,
) -> int:
    state = await _current_state(handle)
    if state is None or len(state.events) <= seen:
        return seen
    publish(project_run_trace(state, definition_version=DEFINITION_VERSION))
    return len(state.events)


async def _current_state(
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
) -> WorkflowState | None:
    try:
        return await handle.query(AgenticSagaWorkflow.state)
    except RPCError as error:
        # The workflow (or its namespace) does not exist yet. The run task reports real failures.
        if error.status is RPCStatusCode.NOT_FOUND:
            return None
        raise


class _LiveView:
    """Publishes traces to the site, echoes new steps, and opens the page on the first one."""

    def __init__(self, stack: ExitStack, site: LiveSite, arguments: argparse.Namespace) -> None:
        self._stack = stack
        self._site = site
        self._arguments = arguments
        self._printed = 0
        self.server: RecorderServer | None = None

    def publish(self, trace: RunTrace) -> None:
        self._site.publish(trace)
        for event in trace.events[self._printed :]:
            _say(f"  {_describe(event)}")
        self._printed = len(trace.events)
        if self.server is None:
            self._start_server()

    def _start_server(self) -> None:
        static = self._stack.enter_context(
            resources.as_file(resources.files("agentic_saga.demo").joinpath("static"))
        )
        server = serve_recorder(self._site.root, port=self._arguments.port, static_directory=static)
        self.server = self._stack.enter_context(server)
        _say(f"Recorder: {self.server.url}  (this page updates every second)")
        if self._arguments.open_browser:
            _open_browser(self.server.url)


def _describe(event: TraceEvent) -> str:
    tool = "" if event.tool_name is None else f" {event.tool_name}"
    return f"{event.saga_seq:02d} {event.event_type}{tool} -> {event.after_status.value}"


def _open_browser(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except webbrowser.Error:
        opened = False
    if not opened:
        _say(f"Open {url} in your browser to watch the run.")


@asynccontextmanager
async def _temporal(address: str | None) -> AsyncIterator[Client]:
    if address is not None:
        _say(f"Temporal: using the server at {address}")
        yield await connect_local_client(address)
        return
    _say("Temporal: starting a throwaway local server (the first run downloads it once)…")
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter
    ) as environment:
        yield environment.client


async def _run_live(arguments: argparse.Namespace, view: _LiveView) -> EcommerceRun:
    async with _temporal(arguments.temporal_address) as client:
        return await follow_run(
            client,
            ScenarioName(arguments.scenario),
            view.publish,
            step_delay_seconds=arguments.step_delay,
        )


def _intro(arguments: argparse.Namespace) -> None:
    scenario = ScenarioName(arguments.scenario)
    _say(f"Agentic Saga live demo: {scenario.value} ({_STORIES[scenario]}).")
    _say("Mode: scripted agent, simulated shop services, no API key, no model calls.")


def _serve_until_stopped(view: _LiveView, run: EcommerceRun) -> None:
    _say(f"Live run finished: {run.state.status.value}. Press Ctrl+C to stop the page.")
    if view.server is not None:
        view.server.wait()


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    _intro(arguments)
    with ExitStack() as stack:
        workspace = Path(stack.enter_context(TemporaryDirectory(prefix="agentic-saga-live-")))
        workspace = workspace.resolve(strict=True)
        view = _LiveView(stack, LiveSite(workspace, ScenarioName(arguments.scenario)), arguments)
        try:
            _serve_until_stopped(view, asyncio.run(_run_live(arguments, view)))
        except KeyboardInterrupt:
            return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv run poe demo",
        description="Run one checkout saga on Temporal and watch it live in your browser.",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default=ScenarioName.BUSINESS_FAILURE.value,
        choices=tuple(item.value for item in ScenarioName),
    )
    parser.add_argument("--step-delay", type=_step_delay, default=1.5, metavar="SECONDS")
    parser.add_argument("--temporal-address", default=None, metavar="HOST:PORT")
    parser.add_argument("--port", type=_port, default=0)
    parser.add_argument("--no-open", action="store_false", dest="open_browser")
    return parser


def _step_delay(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("step delay must be a number of seconds") from error
    if not 0 <= seconds <= _MAX_STEP_DELAY:
        raise argparse.ArgumentTypeError("step delay must be between 0 and 10 seconds")
    return seconds


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= _MAX_PORT:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _say(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())

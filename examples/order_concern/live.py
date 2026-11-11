"""Run a customer order-concern saga for real and watch each step in your browser.

`uv run poe demo` evaluates the customer's prompt first (politely declining prompts that are not
order processing issues with zero workflows started), and for valid order concerns starts a
throwaway local Temporal server, streams the 4-layer compensating saga live, and opens the Flight
Recorder page.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
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
from examples.order_concern.demo import (
    DEFINITION_VERSION,
    ConcernRunOptions,
    DeclinedOrderConcernRun,
    OrderConcernRun,
    default_prompt_for,
    new_concern_saga_id,
    run_concern_with_client,
)
from examples.order_concern.domain import POLITE_NON_ORDER_REPLY, ConcernScenario
from examples.order_concern.triage import is_order_processing_concern

type TracePublisher = Callable[[RunTrace], None]

_MAX_STEP_DELAY: Final[float] = 10.0
_MAX_PORT: Final[int] = 65535
_SKIPPABLE_QUERY_ERRORS: Final[frozenset[RPCStatusCode]] = frozenset(
    {RPCStatusCode.NOT_FOUND, RPCStatusCode.CANCELLED, RPCStatusCode.DEADLINE_EXCEEDED}
)
_STORIES: Final[dict[ConcernScenario, str]] = {
    ConcernScenario.ALREADY_IN_TRANSIT: (
        "original parcel is scanned onto the truck after courier booking, so earlier steps undo"
    ),
    ConcernScenario.HAPPY_PATH: (
        "original parcel is confirmed stopped at the depot, so the express replacement ships"
    ),
    ConcernScenario.LOST_RESPONSE: (
        "the courtesy-credit reply is lost and reconciled instead of crediting twice"
    ),
    ConcernScenario.COMPENSATION_FAILURE: (
        "the credit reversal reply is lost, so the workflow pauses for a support lead"
    ),
    ConcernScenario.NOT_AN_ORDER: (
        "prompt is not an order processing issue, so intake politely declines before Temporal"
    ),
}


class LiveSite:
    """A recorder site whose single run is replaced, file by file, as the saga advances."""

    def __init__(self, workspace: Path, scenario: ConcernScenario) -> None:
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
            presentation="order-concern",
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
        (traces / self._trace_name(self._version - 2)).unlink(missing_ok=True)

    def _trace_name(self, version: int) -> str:
        return f"{self._scenario.value}-{version:04d}.json"


async def follow_concern_run(
    client: Client,
    scenario: ConcernScenario,
    publish: TracePublisher,
    *,
    options: ConcernRunOptions | None = None,
    poll_seconds: float = 0.25,
) -> OrderConcernRun:
    """Run one order-concern scenario and publish its trace as the workflow records events."""
    resolved = options or ConcernRunOptions()
    saga_id = resolved.saga_id or new_concern_saga_id(scenario)
    run_options = ConcernRunOptions(
        prompt=resolved.prompt,
        task_queue=resolved.task_queue,
        saga_id=saga_id,
        step_delay_seconds=resolved.step_delay_seconds,
    )
    task = asyncio.create_task(run_concern_with_client(client, scenario, run_options))
    handle = client.get_workflow_handle_for(AgenticSagaWorkflow.run, saga_id)
    await _poll_until_done(task, handle, publish, poll_seconds)
    outcome = task.result()
    if isinstance(outcome, DeclinedOrderConcernRun):
        raise RuntimeError(outcome.reply)
    publish(outcome.trace)
    return outcome


async def _poll_until_done(
    task: asyncio.Task[object],
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
    publish: TracePublisher,
    poll_seconds: float,
) -> None:
    seen = 0
    while not task.done():
        seen = await _publish_new_events(handle, publish, seen)
        await asyncio.wait({task}, timeout=poll_seconds)


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
        if error.status in _SKIPPABLE_QUERY_ERRORS:
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


async def _run_live(arguments: argparse.Namespace, prompt: str, view: _LiveView) -> OrderConcernRun:
    async with _temporal(arguments.temporal_address) as client:
        options = ConcernRunOptions(prompt=prompt, step_delay_seconds=arguments.step_delay)
        return await follow_concern_run(
            client,
            ConcernScenario(arguments.scenario),
            view.publish,
            options=options,
        )


def _selected_prompt(arguments: argparse.Namespace) -> str:
    if arguments.prompt is not None:
        return str(arguments.prompt).strip()
    return default_prompt_for(ConcernScenario(arguments.scenario))


def _decline_non_order(prompt: str) -> int:
    _say("Agentic Saga live demo: customer concern intake.")
    _say(f'Customer prompt: "{prompt}"')
    _say(f'Agent reply: "{POLITE_NON_ORDER_REPLY}"')
    _say("Outcome: polite_decline (not an order processing issue; 0 workflows started, 0 tools).")
    return 0


def _intro(scenario: ConcernScenario, prompt: str) -> None:
    _say(f"Agentic Saga live demo: {scenario.value} ({_STORIES[scenario]}).")
    _say(f'Customer prompt: "{prompt}"')
    _say(
        "Layers: 1) Intake accepted order concern -> 2) Constrained step engine -> "
        "3) Temporal workflow state -> 4) Paired deterministic tools."
    )


def _serve_until_stopped(view: _LiveView, run: OrderConcernRun, exit_when_done: bool) -> None:
    _say(f"Live run finished: {run.state.status.value}. Press Ctrl+C to stop the page.")
    if view.server is not None and not exit_when_done:
        view.server.wait()


def quiet_simulated_failures() -> None:
    """The lost-reply scenarios fail an activity on purpose; don't print those as tracebacks."""
    logging.getLogger("temporalio.activity").setLevel(logging.ERROR)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    prompt = _selected_prompt(arguments)
    if not is_order_processing_concern(prompt):
        return _decline_non_order(prompt)
    if not _port_is_free(arguments.port):
        _say(f"Port {arguments.port} is already in use. Pick another with --port, or omit it.")
        return 2
    return _execute_live_demo(arguments, prompt)


def _execute_live_demo(arguments: argparse.Namespace, prompt: str) -> int:
    scenario = ConcernScenario(arguments.scenario)
    quiet_simulated_failures()
    _intro(scenario, prompt)
    with ExitStack() as stack:
        workspace = Path(stack.enter_context(TemporaryDirectory(prefix="agentic-saga-concern-")))
        view = _LiveView(stack, LiveSite(workspace.resolve(strict=True), scenario), arguments)
        try:
            run = asyncio.run(_run_live(arguments, prompt, view))
            _serve_until_stopped(view, run, bool(arguments.exit_when_done))
        except KeyboardInterrupt:
            return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv run poe demo",
        description="Triage a customer concern, run an order saga on Temporal, and watch it live.",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default=ConcernScenario.ALREADY_IN_TRANSIT.value,
        choices=tuple(item.value for item in ConcernScenario),
    )
    parser.add_argument("--prompt", default=None, help="Custom user prompt for intake triage.")
    parser.add_argument("--step-delay", type=_step_delay, default=1.5, metavar="SECONDS")
    parser.add_argument("--temporal-address", default=None, metavar="HOST:PORT")
    parser.add_argument("--port", type=_port, default=0)
    parser.add_argument("--no-open", action="store_false", dest="open_browser")
    parser.add_argument("--exit-when-done", action="store_true")
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


def _port_is_free(port: int) -> bool:
    if port == 0:
        return True
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _say(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())

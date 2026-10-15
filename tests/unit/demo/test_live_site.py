from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from agentic_saga.contracts.trace import RunTrace
from examples.ecommerce import live
from examples.ecommerce.domain import ScenarioName
from examples.ecommerce.live import LiveSite

_TRACES = Path("examples/ecommerce/flight-recorder/traces")


def _full_trace() -> RunTrace:
    source = _TRACES / "business-failure.json"
    return RunTrace.model_validate_json(source.read_bytes(), strict=True)


def _partial_trace(events: int) -> RunTrace:
    full = _full_trace()
    selected = full.events[:events]
    return full.model_copy(
        update={
            "events": selected,
            "finished_at": None,
            "outcome": selected[-1].after_status,
            "proofs": (),
        }
    )


def _index(site: LiveSite) -> dict[str, object]:
    return dict(json.loads((site.root / "traces" / "index.json").read_bytes()))


def _entry(site: LiveSite) -> dict[str, str]:
    runs = _index(site)["runs"]
    assert isinstance(runs, list)
    return dict(runs[0])


def test_first_publish_creates_a_servable_live_site(tmp_path: Path) -> None:
    # Given
    site = LiveSite(tmp_path, ScenarioName.BUSINESS_FAILURE)

    # When
    site.publish(_partial_trace(2))

    # Then
    entry = _entry(site)
    payload = (site.root / "traces" / entry["trace_ref"]).read_bytes()
    assert (site.root / "index.html").is_file()
    assert _index(site)["live"] is True
    assert _index(site)["default_run_id"] == "business-failure"
    assert entry["id"] == "business-failure"
    assert entry["trace_sha256"] == sha256(payload).hexdigest()
    assert len(RunTrace.model_validate_json(payload).events) == 2


def test_each_publish_points_the_index_at_a_new_file_and_prunes_old_ones(
    tmp_path: Path,
) -> None:
    # Given
    site = LiveSite(tmp_path, ScenarioName.BUSINESS_FAILURE)
    site.publish(_partial_trace(2))
    first_ref = _entry(site)["trace_ref"]
    site.publish(_partial_trace(5))
    second_ref = _entry(site)["trace_ref"]

    # When
    site.publish(_full_trace())

    # Then
    final = _entry(site)
    traces = sorted(path.name for path in (site.root / "traces").glob("business-failure*"))
    assert len({first_ref, second_ref, final["trace_ref"]}) == 3
    assert traces == sorted([second_ref, final["trace_ref"]])
    payload = (site.root / "traces" / final["trace_ref"]).read_bytes()
    assert RunTrace.model_validate_json(payload).finished_at is not None
    assert final["trace_sha256"] == sha256(payload).hexdigest()
    assert not any(path.name.startswith("stage-") for path in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_follow_run_raises_when_the_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    async def failing_run(*_: object, **__: object) -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("worker could not start")

    async def missing_state(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(live, "run_with_client", failing_run)
    monkeypatch.setattr(live, "_current_state", missing_state)
    client = MagicMock()

    # When / Then
    with pytest.raises(RuntimeError, match="worker could not start"):
        await live.follow_run(client, ScenarioName.HAPPY_PATH, lambda _: None, poll_seconds=0.001)


def test_parser_defaults_to_the_failure_scenario_with_visible_pacing() -> None:
    # When
    arguments = live.build_parser().parse_args([])

    # Then
    assert arguments.scenario == "business-failure"
    assert arguments.step_delay == 1.5
    assert arguments.temporal_address is None
    assert arguments.open_browser is True


@pytest.mark.parametrize("value", ["-1", "11", "soon"])
def test_parser_rejects_unusable_step_delays(value: str) -> None:
    with pytest.raises(SystemExit):
        live.build_parser().parse_args(["--step-delay", value])


def test_demo_task_runs_the_live_browser_demo() -> None:
    # Given
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    # When
    task = project["tool"]["poe"]["tasks"]["demo"]

    # Then
    assert task == "python -m examples.ecommerce.live"


class _Handle:
    def __init__(self, error: RPCError) -> None:
        self._error = error

    async def query(self, *_: object) -> object:
        raise self._error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (RPCStatusCode.NOT_FOUND, "workflow not found"),
        (RPCStatusCode.CANCELLED, "Timeout expired"),
        (RPCStatusCode.DEADLINE_EXCEEDED, "query deadline exceeded"),
    ],
)
async def test_state_query_skips_a_poll_that_has_no_state_yet(
    status: RPCStatusCode, message: str
) -> None:
    # Given: the workflow is not started yet, or a busy server timed the query out
    handle = _Handle(RPCError(message, status, b""))

    # When
    state = await live._current_state(cast(Any, handle))

    # Then
    assert state is None


@pytest.mark.asyncio
async def test_state_query_raises_any_other_temporal_error() -> None:
    # Given
    handle = _Handle(RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b""))

    # When / Then
    with pytest.raises(RPCError, match="server unavailable"):
        await live._current_state(cast(Any, handle))


def test_demo_hides_tracebacks_for_failures_the_scenarios_simulate() -> None:
    # Given
    logger = logging.getLogger("temporalio.activity")
    before = logger.level

    # When
    live.quiet_simulated_failures()

    # Then
    try:
        assert logger.getEffectiveLevel() == logging.ERROR
    finally:
        logger.setLevel(before)

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic_ai.models.test import TestModel

from agentic_saga.contracts.trace import RunTrace
from agentic_saga.demo.assets import materialize_recorder_site
from agentic_saga.demo.reference import order_concern_summary
from examples.order_concern import live
from examples.order_concern.domain import POLITE_NON_ORDER_REPLY, ConcernScenario
from examples.order_concern.triage import (
    build_llm_concern_triage,
    classify_order_concern_locally,
    is_order_processing_concern,
)

_TRACES = Path("examples/ecommerce/flight-recorder/traces")


def _sample_trace(name: str = "business-failure") -> RunTrace:
    return RunTrace.model_validate_json((_TRACES / f"{name}.json").read_bytes(), strict=True)


def test_order_concern_detector_distinguishes_order_issues_from_chitchat() -> None:
    assert is_order_processing_concern("I paid $79 for order_8841 and it never arrived.")
    assert is_order_processing_concern("Order #4412 has not been delivered yet.")
    assert not is_order_processing_concern("What is the weather in Seattle this weekend?")
    assert not is_order_processing_concern("Write a poem about autumn leaves.")


@pytest.mark.asyncio
async def test_local_classifier_politely_declines_non_order_prompts() -> None:
    declined = await classify_order_concern_locally("Who won the 1998 World Cup?")
    accepted = await classify_order_concern_locally("I paid for my package and never received it.")

    assert declined.in_scope is False
    assert declined.message == POLITE_NON_ORDER_REPLY
    assert accepted.in_scope is True


def test_live_main_politely_declines_non_order_prompt_without_starting_temporal(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporal_called = MagicMock()
    monkeypatch.setattr(live, "_execute_live_demo", temporal_called)

    code = live.main(["--prompt", "What is the weather in Seattle this weekend?"])
    output = capsys.readouterr().out

    assert code == 0
    assert POLITE_NON_ORDER_REPLY in output
    assert "polite_decline" in output
    temporal_called.assert_not_called()


def test_not_an_order_scenario_exits_cleanly_before_starting_temporal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = live.main(["not-an-order"])
    output = capsys.readouterr().out

    assert code == 0
    assert "not an order processing issue" in output


def test_order_concern_live_site_publishes_order_concern_presentation(tmp_path: Path) -> None:
    site = live.LiveSite(tmp_path, ConcernScenario.ALREADY_IN_TRANSIT)
    site.publish(_sample_trace("business-failure"))

    index = json.loads((site.root / "traces" / "index.json").read_bytes())
    entry = index["runs"][0]
    payload = (site.root / "traces" / entry["trace_ref"]).read_bytes()

    assert index["live"] is True
    assert entry["id"] == "already-in-transit"
    assert entry["presentation"] == "order-concern"
    assert entry["name"] == "Original parcel scanned onto truck"
    assert entry["trace_sha256"] == sha256(payload).hexdigest()


def test_materialize_recorder_site_supports_all_order_concern_summaries(tmp_path: Path) -> None:
    traces = {
        "already-in-transit": _sample_trace("business-failure"),
        "happy-path": _sample_trace("happy-path"),
        "compensation-failure": _sample_trace("compensation-failure"),
    }
    destination = tmp_path / "order-concern-site"
    materialize_recorder_site(
        destination,
        traces,
        presentation="order-concern",
        default_run_id="already-in-transit",
        recording_mode="scripted",
    )

    index = json.loads((destination / "traces" / "index.json").read_bytes())
    by_id = {entry["id"]: entry for entry in index["runs"]}
    assert by_id["already-in-transit"]["name"] == "Original parcel scanned onto truck"
    assert "undone" in by_id["already-in-transit"]["summary"]
    assert "verified" in by_id["happy-path"]["summary"]
    assert "support-lead" in by_id["compensation-failure"]["summary"]

    incomplete = _sample_trace("happy-path").model_copy(
        update={"outcome": _sample_trace("happy-path").events[0].after_status}
    )
    assert "ends before" in order_concern_summary(incomplete)


def test_build_llm_concern_triage_wires_pydantic_ai_classifier() -> None:
    triage = build_llm_concern_triage(TestModel(), MagicMock())
    assert callable(triage.classifier)


def test_order_concern_parser_defaults_to_already_in_transit() -> None:
    args = live.build_parser().parse_args([])
    assert args.scenario == "already-in-transit"
    assert args.prompt is None
    assert args.step_delay == 1.5

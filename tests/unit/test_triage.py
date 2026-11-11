from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentic_saga import (
    ClassifiedConcernTriage,
    ConcernClassification,
    DeclinedConcern,
    SagaGoal,
    SagaWorkflowInput,
    StartedConcernSaga,
    WorkflowTool,
    build_pydantic_ai_concern_classifier,
    start_concern_saga,
)
from agentic_saga import triage as triage_module


def _workflow_input(prompt: str) -> SagaWorkflowInput:
    return SagaWorkflowInput(
        saga_id="saga_orderconcern00001",
        goal=SagaGoal(
            goal_id="resolve_order_concern",
            text="Resolve stuck order concern",
            context={"prompt": prompt[:80]},
        ),
        tools=(WorkflowTool(name="verify_original_stopped", kind="read", proof_for_success=True),),
        max_agent_turns=4,
        max_tool_calls=4,
    )


@pytest.mark.asyncio
async def test_declined_prompt_replies_politely_and_starts_zero_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_mock = AsyncMock()
    monkeypatch.setattr(triage_module, "start_saga", start_mock)

    async def decline(_prompt: str) -> ConcernClassification:
        return ConcernClassification(
            in_scope=False,
            message="That doesn't look like an order processing issue.",
        )

    triage = ClassifiedConcernTriage(classifier=decline, workflow_builder=_workflow_input)
    outcome = await start_concern_saga(
        MagicMock(),
        triage,
        "What is the weather in Seattle?",
        task_queue="order-concern",
    )

    assert outcome == DeclinedConcern(reply="That doesn't look like an order processing issue.")
    start_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_prompt_starts_temporal_saga_with_validated_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_handle = MagicMock()
    start_mock = AsyncMock(return_value=fake_handle)
    monkeypatch.setattr(triage_module, "start_saga", start_mock)

    async def accept(prompt: str) -> ConcernClassification:
        return ConcernClassification(
            in_scope=True,
            message=f"Accepted order concern: {prompt[:40]}",
        )

    triage = ClassifiedConcernTriage(classifier=accept, workflow_builder=_workflow_input)
    outcome = await start_concern_saga(
        MagicMock(),
        triage,
        "I paid $79 for order_8841 and have not received it.",
        task_queue="order-concern",
    )

    assert isinstance(outcome, StartedConcernSaga)
    assert outcome.handle is fake_handle
    assert outcome.concern_summary.startswith("Accepted order concern:")
    start_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_prompt", ["", "   ", "x" * 2001])
async def test_start_concern_saga_rejects_empty_or_unbounded_prompts(bad_prompt: str) -> None:
    triage = MagicMock()
    with pytest.raises(ValidationError):
        await start_concern_saga(MagicMock(), triage, bad_prompt, task_queue="order-concern")


@pytest.mark.asyncio
async def test_pydantic_ai_concern_classifier_parses_structured_verdict() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert messages
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=tool.name,
                    args={
                        "in_scope": False,
                        "message": "Not an order processing issue — no changes started.",
                    },
                )
            ]
        )

    classifier = build_pydantic_ai_concern_classifier(
        FunctionModel(respond),
        instructions="Classify customer order concerns.",
    )
    verdict = await classifier("Tell me a joke about penguins")

    assert verdict == ConcernClassification(
        in_scope=False,
        message="Not an order processing issue — no changes started.",
    )
    assert TextPart("ok").content == "ok"


def test_pydantic_ai_concern_classifier_requires_agent_extra_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)
    with pytest.raises(RuntimeError, match=r"agentic-saga\[agent\]"):
        build_pydantic_ai_concern_classifier("test", instructions="Classify")

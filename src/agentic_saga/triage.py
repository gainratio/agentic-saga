"""Typed user-concern triage boundary in front of deterministic Temporal sagas."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Annotated, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from temporalio.client import Client

from agentic_saga.temporal.client import SagaHandle, start_saga
from agentic_saga.temporal.contracts import SagaWorkflowInput

type BoundedPrompt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
type BoundedReply = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class _TriageModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ConcernClassification(_TriageModel):
    """Structured LLM or rule verdict deciding whether a user prompt is in domain scope."""

    in_scope: bool
    message: BoundedReply


type ConcernClassifier = Callable[[str], Awaitable[ConcernClassification]]


class ConcernAccepted(_TriageModel):
    """A relevant user concern mapped to a validated deterministic saga input."""

    kind: Literal["accepted"] = "accepted"
    concern_summary: BoundedReply
    workflow_input: SagaWorkflowInput


class ConcernDeclined(_TriageModel):
    """A polite refusal when a user prompt is outside the saga's domain."""

    kind: Literal["declined"] = "declined"
    reply: BoundedReply


type ConcernDecision = Annotated[
    ConcernAccepted | ConcernDeclined,
    Field(discriminator="kind"),
]


class _PromptEnvelope(_TriageModel):
    prompt: BoundedPrompt


class ConcernTriageDriver(Protocol):
    """Translates a free-form user prompt into a saga input or a polite decline."""

    async def evaluate(self, prompt: str) -> ConcernDecision: ...


class _RunResult(Protocol):
    output: ConcernClassification


class _ClassificationAgent(Protocol):
    async def run(self, user_prompt: str) -> _RunResult: ...


class _AgentFactory(Protocol):
    def __call__(
        self,
        model: object,
        *,
        output_type: type[ConcernClassification],
        instructions: str,
    ) -> _ClassificationAgent: ...


@dataclass(frozen=True)
class ClassifiedConcernTriage:
    """Pairs an LLM or local classifier with a deterministic SagaWorkflowInput builder."""

    classifier: ConcernClassifier
    workflow_builder: Callable[[str], SagaWorkflowInput]

    async def evaluate(self, prompt: str) -> ConcernDecision:
        verdict = await self.classifier(prompt)
        if not verdict.in_scope:
            return ConcernDeclined(reply=verdict.message)
        return ConcernAccepted(
            concern_summary=verdict.message,
            workflow_input=self.workflow_builder(prompt),
        )


def build_pydantic_ai_concern_classifier(
    model: object,
    *,
    instructions: str,
) -> ConcernClassifier:
    """Build a structured Pydantic AI concern classifier (`agentic-saga[agent]`)."""
    factory = _load_agent_factory()
    agent = factory(model, output_type=ConcernClassification, instructions=instructions)

    async def _classify(prompt: str) -> ConcernClassification:
        result = await agent.run(prompt)
        return ConcernClassification.model_validate(result.output)

    return _classify


def _load_agent_factory() -> _AgentFactory:
    try:
        module = import_module("pydantic_ai")
    except ModuleNotFoundError:
        raise RuntimeError("install agentic-saga[agent] to use LLM concern triage") from None
    return cast(_AgentFactory, module.Agent)


@dataclass(frozen=True)
class StartedConcernSaga:
    """Returned when a relevant concern started a durable Temporal saga."""

    concern_summary: str
    handle: SagaHandle
    kind: Literal["started"] = "started"


@dataclass(frozen=True)
class DeclinedConcern:
    """Returned when an out-of-scope prompt was politely declined with zero side effects."""

    reply: str
    kind: Literal["declined"] = "declined"


type ConcernStartOutcome = StartedConcernSaga | DeclinedConcern


async def start_concern_saga(
    client: Client,
    triage: ConcernTriageDriver,
    prompt: str,
    *,
    task_queue: str,
) -> ConcernStartOutcome:
    """Triage a user prompt and start a Temporal saga only when the concern is in scope."""
    envelope = _PromptEnvelope(prompt=prompt)
    decision = await triage.evaluate(envelope.prompt)
    if isinstance(decision, ConcernDeclined):
        return DeclinedConcern(reply=decision.reply)
    handle = await start_saga(client, decision.workflow_input, task_queue=task_queue)
    return StartedConcernSaga(concern_summary=decision.concern_summary, handle=handle)


__all__ = [
    "ClassifiedConcernTriage",
    "ConcernAccepted",
    "ConcernClassification",
    "ConcernClassifier",
    "ConcernDecision",
    "ConcernDeclined",
    "ConcernStartOutcome",
    "ConcernTriageDriver",
    "DeclinedConcern",
    "StartedConcernSaga",
    "build_pydantic_ai_concern_classifier",
    "start_concern_saga",
]

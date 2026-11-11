"""4-layer order-concern orchestration: Triage -> Decision Engine -> Temporal -> Tools."""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter
from temporalio import activity
from temporalio.client import Client, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agentic_saga.agents.choice import ProposalCandidate
from agentic_saga.agents.proposals import FinishIntent, ToolCallIntent
from agentic_saga.contracts.actions import AgentProposal, Finish, ToolCall
from agentic_saga.contracts.common import JsonObject, thaw_json_object
from agentic_saga.contracts.runtime import (
    SagaGoal,
    SagaObservation,
    SagaStatus,
    ToolDescriptor,
)
from agentic_saga.contracts.tools import EffectToolDefinition, ToolRegistry
from agentic_saga.contracts.trace import RunTrace
from agentic_saga.manifest import SagaContext, load_saga_context
from agentic_saga.temporal import project_run_trace
from agentic_saga.temporal.activities import TemporalActivities
from agentic_saga.temporal.contracts import (
    HumanCompensationResolution,
    HumanResolutionVerificationRequest,
    HumanResolutionVerificationResult,
    SagaWorkflowInput,
    WorkflowState,
    WorkflowTool,
)
from agentic_saga.temporal.worker import build_worker
from agentic_saga.temporal.workflow import HUMAN_RESOLUTION_ACTIVITY, AgenticSagaWorkflow
from agentic_saga.triage import DeclinedConcern, start_concern_saga
from examples.order_concern.domain import (
    DEFAULT_CONCERN_PROMPT,
    DEFAULT_NON_ORDER_PROMPT,
    BookExpressCourier,
    ConcernScenario,
    IssueShippingCredit,
    ProviderCount,
    ReserveReplacement,
    ResolutionState,
    VerifyOriginalStopped,
)
from examples.order_concern.provider import OrderResolutionProvider, build_registry
from examples.order_concern.triage import ConcernContext, build_local_concern_triage

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
EXPECTED_FORWARD = (
    "reserve_replacement",
    "issue_shipping_credit",
    "book_express_courier",
    "verify_original_stopped",
)
_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "reserve_replacement": (),
    "issue_shipping_credit": ("reserve_replacement",),
    "book_express_courier": ("issue_shipping_credit",),
    "verify_original_stopped": ("book_express_courier",),
}
_SUPPORT_AUTH_SECRET = secrets.token_bytes(32)
_UNAUTHORIZED = "authz_untrusted_actor_0000001"
DEFINITION_VERSION = "order-concern-temporal-v1"
_MANIFEST_PATH = Path(__file__).with_name("saga.yaml")
_INVARIANT_CHECKS = ("no_external_effects", "obligations_reversed", "resolution_verified")
_HUMAN_WAIT_SECONDS = 60.0


@dataclass(frozen=True)
class OrderConcernRun:
    scenario: ConcernScenario
    prompt: str
    concern_summary: str
    state: WorkflowState
    trace: RunTrace
    counts: tuple[ProviderCount, ...]
    compensation_order: tuple[str, ...]
    provider_events: tuple[str, ...]
    proposals: tuple[str, ...]
    provider_state: ResolutionState
    human_pause_status: SagaStatus | None = None
    stale_update_rejected: bool = False
    unauthorized_update_rejected: bool = False


@dataclass(frozen=True)
class DeclinedOrderConcernRun:
    scenario: ConcernScenario
    prompt: str
    reply: str
    provider_events: tuple[str, ...] = ()


type OrderConcernOutcome = OrderConcernRun | DeclinedOrderConcernRun


@dataclass(frozen=True)
class _Setup:
    client: Client
    scenario: ConcernScenario
    prompt: str
    saga_id: str
    registry: ToolRegistry
    context: SagaContext
    agent: ResolutionAgent
    provider: OrderResolutionProvider
    queue: str


@dataclass(frozen=True)
class _Finished:
    summary: str
    state: WorkflowState
    pause: SagaStatus | None = None
    stale_rejected: bool = False
    unauthorized_rejected: bool = False


@activity.defn(name=HUMAN_RESOLUTION_ACTIVITY)
async def verify_support_lead_resolution(
    request: HumanResolutionVerificationRequest,
) -> HumanResolutionVerificationResult:
    expected = _support_authorization(
        request.saga_id, request.operation_id, request.based_on_event_seq
    )
    if not hmac.compare_digest(request.authorization_reference, expected):
        return HumanResolutionVerificationResult.rejected("authorization_denied")
    return HumanResolutionVerificationResult.accepted(
        trusted_actor="support_lead",
        authorization_id=request.authorization_reference,
    )


class ResolutionAgent:
    """Constrained step proposer matching Jev / Pydantic AI's exact AgentDriver contract."""

    def __init__(self, plan: Sequence[str] = EXPECTED_FORWARD) -> None:
        self._plan = tuple(plan)
        self.proposals: list[str] = []

    async def next_action(
        self, observation: SagaObservation, available_tools: Sequence[ToolDescriptor]
    ) -> AgentProposal:
        names = frozenset(item.name for item in available_tools)
        tool = next((name for name in self._plan if name in names), None)
        if tool is None:
            self.proposals.append("finish")
            return _finish_proposal(observation)
        self.proposals.append(tool)
        return _tool_proposal(tool, observation)


async def build_order_concern_candidates(
    observation: SagaObservation, available_tools: Sequence[ToolDescriptor]
) -> Sequence[ProposalCandidate]:
    """Candidate builder for Jev (`build_openrouter_decisions_driver` / `build_jev_driver`)."""
    candidates = [_tool_candidate(tool, observation) for tool in available_tools]
    if observation.finish_allowed:
        candidates.append(_finish_candidate())
    return tuple(candidates)


def _tool_candidate(tool: ToolDescriptor, observation: SagaObservation) -> ProposalCandidate:
    cid = f"choice_{sha256(tool.name.encode()).hexdigest()[:12]}"
    return ProposalCandidate(
        candidate_id=cid,
        criteria=f"Select {tool.name} when it advances the customer's order resolution.",
        proposal=ToolCallIntent(
            kind="tool_call",
            tool_name=tool.name,
            arguments=_arguments(tool.name, observation.goal),
            rationale=f"Select eligible step {tool.name}.",
        ),
    )


def _finish_candidate() -> ProposalCandidate:
    return ProposalCandidate(
        candidate_id="choice_finishsaga01",
        criteria="Finish only when replacement, courtesy credit, and stopped parcel are verified.",
        proposal=FinishIntent(
            kind="finish",
            rationale="Express replacement, courtesy credit, and stopped original parcel verified.",
            target_status="succeeded_verified",
        ),
    )


def default_prompt_for(scenario: ConcernScenario) -> str:
    if scenario is ConcernScenario.NOT_AN_ORDER:
        return DEFAULT_NON_ORDER_PROMPT
    return DEFAULT_CONCERN_PROMPT


@dataclass(frozen=True)
class ConcernRunOptions:
    prompt: str | None = None
    task_queue: str | None = None
    saga_id: str | None = None
    step_delay_seconds: float = 0.0


async def run_concern_scenario(
    scenario: ConcernScenario | str,
    *,
    prompt: str | None = None,
) -> OrderConcernOutcome:
    selected = ConcernScenario(scenario)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as environment:
        options = ConcernRunOptions(prompt=prompt, saga_id=new_concern_saga_id(selected))
        return await run_concern_with_client(environment.client, selected, options)


async def run_concern_with_client(
    client: Client,
    scenario: ConcernScenario | str,
    options: ConcernRunOptions | None = None,
) -> OrderConcernOutcome:
    selected = ConcernScenario(scenario)
    resolved = options or ConcernRunOptions()
    setup = _build_setup(client, selected, resolved)
    return await _execute_concern(setup)


def _build_setup(
    client: Client,
    scenario: ConcernScenario,
    options: ConcernRunOptions,
) -> _Setup:
    prompt = options.prompt if options.prompt is not None else default_prompt_for(scenario)
    saga_id = options.saga_id or new_concern_saga_id(scenario)
    provider = OrderResolutionProvider(scenario, step_delay_seconds=options.step_delay_seconds)
    registry = build_registry(provider)
    context = load_saga_context(
        _MANIFEST_PATH, registry=registry, policy_checks=(), invariant_checks=_INVARIANT_CHECKS
    )
    queue = options.task_queue or f"order-concern-{saga_id}"
    return _Setup(
        client, scenario, prompt, saga_id, registry, context, ResolutionAgent(), provider, queue
    )


async def _execute_concern(setup: _Setup) -> OrderConcernOutcome:
    triage = build_local_concern_triage(
        lambda text: build_workflow_input(setup.registry, setup.context, setup.saga_id, text)
    )
    async with _build_worker(setup):
        started = await start_concern_saga(
            setup.client, triage, setup.prompt, task_queue=setup.queue
        )
        if isinstance(started, DeclinedConcern):
            return DeclinedOrderConcernRun(
                setup.scenario, setup.prompt, started.reply, tuple(setup.provider.events)
            )
        finished = await _await_saga(setup, started.concern_summary, started.handle)
        return _to_run(setup, finished)


async def _await_saga(
    setup: _Setup,
    summary: str,
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
) -> _Finished:
    if setup.scenario is not ConcernScenario.COMPENSATION_FAILURE:
        return _Finished(summary=summary, state=await handle.result())
    pause_seconds = setup.provider.step_delay_seconds * 2
    state, pause, stale, unauthorized = await _resolve_human_pause(handle, pause_seconds)
    return _Finished(summary, state, pause, stale, unauthorized)


def _build_worker(setup: _Setup) -> Worker:
    activities = TemporalActivities(setup.agent, setup.registry, setup.context.budget)
    return build_worker(
        setup.client,
        task_queue=setup.queue,
        activities=activities,
        human_resolution_activity=verify_support_lead_resolution,
    )


async def _resolve_human_pause(
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
    pause_seconds: float,
) -> tuple[WorkflowState, SagaStatus, bool, bool]:
    state = await _wait_for_human(handle)
    await asyncio.sleep(pause_seconds)
    unresolved = next(item for item in state.compensations if item.state == "unresolved")
    op_id = unresolved.compensation_identity.operation_id
    stale = await _rejected(handle, _resolution(state, op_id, -1))
    unauth = await _rejected(handle, _resolution(state, op_id, 0, _UNAUTHORIZED))
    await handle.execute_update(
        AgenticSagaWorkflow.resolve_compensation, _resolution(state, op_id, 0)
    )
    return await handle.result(), state.status, stale, unauth


async def _rejected(
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
    resolution: HumanCompensationResolution,
) -> bool:
    try:
        await handle.execute_update(AgenticSagaWorkflow.resolve_compensation, resolution)
    except WorkflowUpdateFailedError:
        return True
    return False


def _resolution(
    state: WorkflowState,
    operation_id: str,
    delta: int,
    authorization: str | None = None,
) -> HumanCompensationResolution:
    seq = len(state.events) + delta
    ref = authorization or _support_authorization(state.saga_id, operation_id, seq)
    return HumanCompensationResolution(
        operation_id=operation_id,
        based_on_event_seq=seq,
        authorization_reference=ref,
        receipt={"support_lead_confirmation": "credit_reversal_verified"},
    )


async def _wait_for_human(
    handle: WorkflowHandle[AgenticSagaWorkflow, WorkflowState],
) -> WorkflowState:
    async with asyncio.timeout(_HUMAN_WAIT_SECONDS):
        while True:
            state = await handle.query(AgenticSagaWorkflow.state)
            if state.status is SagaStatus.HUMAN_REQUIRED:
                return state
            await asyncio.sleep(0.05)


def build_workflow_input(
    registry: ToolRegistry,
    context: SagaContext,
    saga_id: str,
    customer_concern: str,
) -> SagaWorkflowInput:
    return SagaWorkflowInput(
        saga_id=saga_id,
        goal=_goal(context, customer_concern),
        tools=tuple(_workflow_tool(registry, name) for name in EXPECTED_FORWARD),
        max_agent_turns=context.budget.turn_limit,
        max_tool_calls=context.budget.tool_call_limit,
    )


def _goal(context: SagaContext, customer_concern: str) -> SagaGoal:
    state = ResolutionState()
    public = ConcernContext(
        order_id=state.order_id,
        customer_id=state.customer_id,
        sku=state.sku,
        quantity=state.replacement_quantity,
        credit_minor=state.courtesy_credit_minor,
        currency=state.currency,
        customer_concern=customer_concern[:240],
    )
    return SagaGoal(
        goal_id="resolve_order_concern",
        text=context.manifest.objective,
        context=_JSON.validate_python(public.model_dump(mode="json")),
    )


def _workflow_tool(registry: ToolRegistry, name: str) -> WorkflowTool:
    definition = registry.definition(name)
    if isinstance(definition, EffectToolDefinition):
        return WorkflowTool(
            name=name,
            kind="effect",
            compensation_tool=definition.compensate_with,
            prerequisites=_PREREQUISITES[name],
        )
    return WorkflowTool(
        name=name,
        kind="read",
        proof_for_success=name == "verify_original_stopped",
        prerequisites=_PREREQUISITES[name],
    )


def _finish_proposal(observation: SagaObservation) -> Finish:
    return Finish(
        proposal_id=f"proposal_{observation.saga_seq:08d}",
        based_on_saga_seq=observation.saga_seq,
        rationale="Express replacement, courtesy credit, and stopped original parcel are verified.",
        target_status="succeeded_verified",
    )


def _tool_proposal(tool: str, observation: SagaObservation) -> ToolCall:
    return ToolCall(
        proposal_id=f"proposal_{observation.saga_seq:08d}",
        tool_name=tool,
        arguments=_arguments(tool, observation.goal),
        based_on_saga_seq=observation.saga_seq,
        rationale="Select the next eligible order-concern resolution capability.",
    )


def _arguments(tool: str, goal: SagaGoal) -> JsonObject:
    ctx = ConcernContext.model_validate(thaw_json_object(goal.context))
    commands = {
        "reserve_replacement": ReserveReplacement(
            order_id=ctx.order_id, sku=ctx.sku, quantity=ctx.quantity
        ),
        "issue_shipping_credit": IssueShippingCredit(
            order_id=ctx.order_id,
            customer_id=ctx.customer_id,
            credit_minor=ctx.credit_minor,
            currency=ctx.currency,
        ),
        "book_express_courier": BookExpressCourier(order_id=ctx.order_id),
        "verify_original_stopped": VerifyOriginalStopped(order_id=ctx.order_id),
    }
    return _JSON.validate_python(commands[tool].model_dump(mode="json"))


def _support_authorization(saga_id: str, operation_id: str, event_seq: int) -> str:
    material = f"{saga_id}\0{operation_id}\0{event_seq}".encode()
    digest = hmac.new(_SUPPORT_AUTH_SECRET, material, sha256).hexdigest()
    return f"authz_{digest}"


def _to_run(setup: _Setup, finished: _Finished) -> OrderConcernRun:
    provider = setup.provider
    return OrderConcernRun(
        scenario=setup.scenario,
        prompt=setup.prompt,
        concern_summary=finished.summary,
        state=finished.state,
        trace=project_run_trace(finished.state, definition_version=DEFINITION_VERSION),
        counts=provider.counts(),
        compensation_order=provider.compensation_order(),
        provider_events=tuple(provider.events),
        proposals=tuple(setup.agent.proposals),
        provider_state=provider.state,
        human_pause_status=finished.pause,
        stale_update_rejected=finished.stale_rejected,
        unauthorized_update_rejected=finished.unauthorized_rejected,
    )


def new_concern_saga_id(scenario: ConcernScenario) -> str:
    material = f"{scenario.value}:{asyncio.get_running_loop().time()}"
    return f"saga_{sha256(material.encode()).hexdigest()}"


__all__ = [
    "DEFINITION_VERSION",
    "EXPECTED_FORWARD",
    "ConcernRunOptions",
    "DeclinedOrderConcernRun",
    "OrderConcernOutcome",
    "OrderConcernRun",
    "ResolutionAgent",
    "build_order_concern_candidates",
    "build_workflow_input",
    "default_prompt_for",
    "new_concern_saga_id",
    "run_concern_scenario",
    "run_concern_with_client",
]

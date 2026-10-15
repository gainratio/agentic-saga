"""Book a trip with agentic-saga, watch the final check fail, and see every booking undone.

The steps: book_flight -> book_hotel -> confirm_trip. Each booking is paired with its undo
(cancel_flight, cancel_hotel). confirm_trip always reports "not verified", so the saga cannot
prove the goal and undoes the finished bookings newest-first: cancel_hotel, then cancel_flight.

Nothing here needs an API key or a running server. The "travel API" is an in-memory fake, the
"agent" is a fixed script, and Temporal's test server is downloaded and started for you.

Run it from the repository root:

    uv run python -m examples.quickstart.trip
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from agentic_saga import SagaGoal, SagaWorkflowInput, WorkflowTool, start_saga
from agentic_saga.contracts.actions import AgentProposal, Finish, ToolCall
from agentic_saga.contracts.common import Reversibility
from agentic_saga.contracts.outcomes import (
    EffectConfirmed,
    EffectOutcome,
    ReconcileUnsupported,
    ReconciliationOutcome,
)
from agentic_saga.contracts.runtime import ExecutionBudget, SagaObservation, ToolDescriptor
from agentic_saga.contracts.tools import (
    EffectContext,
    EffectToolDefinition,
    ReadToolDefinition,
    ReconcileContext,
    ToolCapabilities,
    ToolRegistry,
)
from agentic_saga.temporal import (
    HumanResolutionVerificationRequest,
    HumanResolutionVerificationResult,
    TemporalActivities,
    build_worker,
)
from agentic_saga.temporal.workflow import HUMAN_RESOLUTION_ACTIVITY

TASK_QUEUE = "quickstart-trip"
PLAN = ("book_flight", "book_hotel", "confirm_trip")


# 1. Typed commands. Tool inputs must be strict, frozen, and reject unknown fields.
class Trip(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    trip_id: str


class TripCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    verified: bool


# 2. A fake travel API: a stand-in for the real airline and hotel APIs. It only records calls.
@dataclass(frozen=True)
class FakeBookingApi:
    name: str
    calls: list[str]

    async def execute(self, command: Trip, context: EffectContext) -> EffectOutcome:
        self.calls.append(self.name)
        return EffectConfirmed(receipt={"receipt_ref": f"fake://{self.name}/{command.trip_id}"})

    async def reconcile(self, command: Trip, context: ReconcileContext) -> ReconciliationOutcome:
        # Asked only when a response is lost ("did that booking happen?"). The fake never
        # loses one; a real adapter would look the booking up.
        return ReconcileUnsupported(reason="the in-memory fake cannot look bookings up")


@dataclass(frozen=True)
class FakeTripCheck:
    calls: list[str]

    async def read(self, command: Trip) -> TripCheck:
        self.calls.append("confirm_trip")
        return TripCheck(verified=False)  # The airline repriced the seat: the trip fails.


# 3. Register each forward step with its undo. Undo steps are effects the agent never sees.
def booking(name: str, undo: str | None, calls: list[str]) -> EffectToolDefinition[Trip]:
    return EffectToolDefinition(
        name=name,
        definition_version="trip-v1",
        command_schema_version="trip-v1",
        input_model=Trip,
        adapter=FakeBookingApi(name, calls),
        capabilities=ToolCapabilities(
            reconciliation_supported=False,
            cancellation_supported=False,
            fencing_supported=False,
            reversibility=Reversibility.SEMANTIC if undo else Reversibility.IRREVERSIBLE,
            partial_effects_possible=False,
        ),
        compensate_with=undo,
        description=f"Fake travel API call: {name}.",
    )


def build_registry(forward: list[str], undo: list[str]) -> ToolRegistry:
    return ToolRegistry(
        (
            booking("book_flight", "cancel_flight", forward),
            booking("book_hotel", "cancel_hotel", forward),
            booking("cancel_flight", None, undo),
            booking("cancel_hotel", None, undo),
            ReadToolDefinition("confirm_trip", Trip, TripCheck, FakeTripCheck(forward)),
        )
    )


# 4. What the workflow may let the agent call, in which order, and what proves success.
#    The undo pairing is declared again here; it must match the registry above.
WORKFLOW_TOOLS = (
    WorkflowTool(name="book_flight", kind="effect", compensation_tool="cancel_flight"),
    WorkflowTool(
        name="book_hotel",
        kind="effect",
        compensation_tool="cancel_hotel",
        prerequisites=("book_flight",),
    ),
    WorkflowTool(
        name="confirm_trip", kind="read", proof_for_success=True, prerequisites=("book_hotel",)
    ),
)


# 5. A scripted agent. A real AgentDriver would ask a model; this one follows PLAN.
class ScriptedAgent:
    async def next_action(
        self, observation: SagaObservation, available_tools: Sequence[ToolDescriptor]
    ) -> AgentProposal:
        step = first_offered(available_tools)
        proposal_id = f"proposal_{observation.saga_seq:08d}"
        if step is None:  # Nothing left to do: claim success (the workflow checks the proof).
            return Finish(
                proposal_id=proposal_id,
                based_on_saga_seq=observation.saga_seq,
                rationale="Every step is done.",
                target_status="succeeded_verified",
            )
        return ToolCall(
            proposal_id=proposal_id,
            tool_name=step,
            arguments=observation.goal.context,
            based_on_saga_seq=observation.saga_seq,
            rationale=f"Next step in the plan: {step}.",
        )


def first_offered(available_tools: Sequence[ToolDescriptor]) -> str | None:
    offered = {tool.name for tool in available_tools}
    return next((name for name in PLAN if name in offered), None)


# build_worker requires a hook that checks a human's sign-off on an undo that failed.
# No undo fails in this example, so it is never called; it refuses if it ever is.
@activity.defn(name=HUMAN_RESOLUTION_ACTIVITY)
async def no_human_operator(
    request: HumanResolutionVerificationRequest,
) -> HumanResolutionVerificationResult:
    return HumanResolutionVerificationResult.rejected("no_operator_in_quickstart")


# 6. The goal and limits for one run. The agent sees the goal; the workflow enforces limits.
BUDGET = ExecutionBudget(
    turn_limit=5, tool_call_limit=3, elapsed_ms_limit=60_000, token_limit=1_000
)


def trip_saga() -> SagaWorkflowInput:
    return SagaWorkflowInput(
        saga_id=f"saga_{uuid.uuid4().hex}",
        goal=SagaGoal(
            goal_id="book_trip",
            text="Book a flight and a hotel for trip 42, then confirm the trip.",
            context={"trip_id": "trip_42"},
        ),
        tools=WORKFLOW_TOOLS,
        max_agent_turns=BUDGET.turn_limit,
        max_tool_calls=BUDGET.tool_call_limit,
    )


@dataclass(frozen=True)
class TripResult:
    forward: tuple[str, ...]
    undo: tuple[str, ...]
    status: str


# 7. Start Temporal's test server, run one saga on a worker, and report what happened.
async def run_trip() -> TripResult:
    forward: list[str] = []
    undo: list[str] = []
    activities = TemporalActivities(ScriptedAgent(), build_registry(forward, undo), BUDGET)
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        build_worker(
            env.client,
            task_queue=TASK_QUEUE,
            activities=activities,
            human_resolution_activity=no_human_operator,
        ),
    ):
        handle = await start_saga(env.client, trip_saga(), task_queue=TASK_QUEUE)
        state = await handle.result()
    return TripResult(tuple(forward), tuple(undo), state.status.value)


def main() -> None:
    result = asyncio.run(run_trip())
    print("Forward steps:", " -> ".join(result.forward))
    print("Undo steps:   ", " -> ".join(result.undo))
    print("Outcome:      ", result.status)


if __name__ == "__main__":
    main()

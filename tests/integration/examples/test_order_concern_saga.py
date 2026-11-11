from __future__ import annotations

from pathlib import Path

import pytest

from agentic_saga.contracts.runtime import (
    ExecutionBudget,
    SagaObservation,
    SagaStatus,
    ToolDescriptor,
)
from agentic_saga.manifest import load_saga_context
from examples.order_concern.demo import (
    DeclinedOrderConcernRun,
    OrderConcernRun,
    build_order_concern_candidates,
    build_workflow_input,
    run_concern_scenario,
)
from examples.order_concern.domain import POLITE_NON_ORDER_REPLY, ConcernScenario
from examples.order_concern.provider import OrderResolutionProvider, build_registry


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_already_in_transit_undoes_all_three_steps_newest_first() -> None:
    outcome = await run_concern_scenario(ConcernScenario.ALREADY_IN_TRANSIT)

    assert isinstance(outcome, OrderConcernRun)
    assert outcome.state.status is SagaStatus.COMPENSATED_VERIFIED
    assert outcome.compensation_order == (
        "cancel_express_courier",
        "reverse_shipping_credit",
        "release_replacement",
    )
    assert outcome.provider_state.replacement_reserved is False
    assert outcome.provider_state.credit_status == "reversed"
    assert outcome.provider_state.express_courier == "cancelled"


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_happy_path_verifies_stopped_parcel_and_finishes_cleanly() -> None:
    outcome = await run_concern_scenario(ConcernScenario.HAPPY_PATH)

    assert isinstance(outcome, OrderConcernRun)
    assert outcome.state.status is SagaStatus.SUCCEEDED_VERIFIED
    assert outcome.compensation_order == ()
    assert outcome.provider_state.replacement_reserved is True
    assert outcome.provider_state.credit_status == "issued"
    assert outcome.provider_state.express_courier == "booked"


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_lost_response_reconciles_courtesy_credit_without_double_crediting() -> None:
    outcome = await run_concern_scenario(ConcernScenario.LOST_RESPONSE)

    assert isinstance(outcome, OrderConcernRun)
    assert outcome.state.status is SagaStatus.SUCCEEDED_VERIFIED
    credit_counts = next(item for item in outcome.counts if item.tool == "issue_shipping_credit")
    assert credit_counts.effects == 1
    assert credit_counts.reconciliations == 1


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_compensation_failure_pauses_for_support_lead_and_rejects_bad_updates() -> None:
    outcome = await run_concern_scenario(ConcernScenario.COMPENSATION_FAILURE)

    assert isinstance(outcome, OrderConcernRun)
    assert outcome.human_pause_status is SagaStatus.HUMAN_REQUIRED
    assert outcome.stale_update_rejected is True
    assert outcome.unauthorized_update_rejected is True
    assert outcome.state.status is SagaStatus.COMPENSATED_VERIFIED


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_non_order_prompt_is_politely_declined_with_zero_provider_effects() -> None:
    outcome = await run_concern_scenario(
        ConcernScenario.ALREADY_IN_TRANSIT,
        prompt="Can you recommend a sci-fi movie for tonight?",
    )

    assert isinstance(outcome, DeclinedOrderConcernRun)
    assert outcome.reply == POLITE_NON_ORDER_REPLY
    assert outcome.provider_events == ()


@pytest.mark.asyncio
async def test_jev_candidate_builder_offers_current_forward_and_finish_choices() -> None:
    registry = build_registry(OrderResolutionProvider(ConcernScenario.HAPPY_PATH))
    context = load_saga_context(
        Path("examples/order_concern/saga.yaml"),
        registry=registry,
        policy_checks=(),
        invariant_checks=("no_external_effects", "obligations_reversed", "resolution_verified"),
    )
    workflow_input = build_workflow_input(
        registry,
        context,
        "saga_orderconcerntest01",
        "I paid $79 for order_8841 and it never arrived.",
    )
    obs = SagaObservation(
        saga_id="saga_orderconcerntest01",
        saga_seq=1,
        state=SagaStatus.RUNNING,
        goal=workflow_input.goal,
        last_action=None,
        projection={"status": "running"},
        remaining_budget=ExecutionBudget(
            turn_limit=4, tool_call_limit=4, token_limit=4096, elapsed_ms_limit=30000
        ),
        finish_allowed=True,
    )
    descriptor = ToolDescriptor.from_definition(registry.definition("reserve_replacement"))
    candidates = await build_order_concern_candidates(obs, (descriptor,))
    assert len(candidates) == 2

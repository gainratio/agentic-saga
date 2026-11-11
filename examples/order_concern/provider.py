"""Deterministic paired tools for resolving a paid-not-delivered order concern."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from pydantic import BaseModel, TypeAdapter
from temporalio.exceptions import ApplicationError

from agentic_saga.contracts.common import JsonObject, Reversibility
from agentic_saga.contracts.outcomes import (
    EffectConfirmed,
    EffectOutcome,
    NoEffectConfirmed,
    ReconcileEffectConfirmed,
    ReconcileNoEffectConfirmed,
    ReconcileUnsupported,
    ReconciliationOutcome,
)
from agentic_saga.contracts.tools import (
    EffectContext,
    EffectToolDefinition,
    ReadToolDefinition,
    ReconcileContext,
    ToolCapabilities,
    ToolRegistry,
)
from examples.order_concern.domain import (
    BookExpressCourier,
    CancelExpressCourier,
    ConcernScenario,
    IssueShippingCredit,
    OriginalShipmentCheck,
    ProviderCount,
    ReleaseReplacement,
    ReserveReplacement,
    ResolutionState,
    ReverseShippingCredit,
    VerifyOriginalStopped,
)

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_COMPENSATIONS = frozenset(
    {"cancel_express_courier", "reverse_shipping_credit", "release_replacement"}
)


@dataclass
class _Count:
    executes: int = 0
    effects: int = 0
    reconciliations: int = 0


class OrderResolutionProvider:
    """Idempotent simulated inventory, billing-credit, and courier services."""

    def __init__(self, scenario: ConcernScenario, *, step_delay_seconds: float = 0.0) -> None:
        self.scenario = scenario
        self.step_delay_seconds = step_delay_seconds
        self.state = ResolutionState()
        self.events: list[str] = []
        self._counts: dict[str, _Count] = {}
        self._receipts: dict[str, JsonObject] = {}

    async def execute(self, tool: str, command: BaseModel, context: EffectContext) -> EffectOutcome:
        await self._pause()
        self._record(tool, "execute")
        prior = self._receipts.get(context.operation_id)
        if prior is not None:
            return self._outcome_for(tool, prior)
        if not _allowed(tool, command, self.state):
            return NoEffectConfirmed(reason="authoritative rule rejected command")
        receipt = _receipt(tool, context.operation_id)
        self._commit(tool, context.operation_id, receipt)
        return self._outcome_for(tool, receipt)

    async def read(self, command: VerifyOriginalStopped) -> OriginalShipmentCheck:
        await self._pause()
        self._record("verify_original_stopped", "read")
        valid_order = command.order_id == self.state.order_id
        stopped = self.scenario not in {
            ConcernScenario.ALREADY_IN_TRANSIT,
            ConcernScenario.COMPENSATION_FAILURE,
        }
        ready = self.state.express_courier == "booked" and stopped
        return OriginalShipmentCheck(verified=valid_order and ready)

    async def reconcile(
        self, tool: str, command: BaseModel, context: ReconcileContext
    ) -> ReconciliationOutcome:
        await self._pause()
        self._count(tool).reconciliations += 1
        self.events.append(f"reconcile:{tool}")
        if _uncertain_reverse(self.scenario, tool):
            return ReconcileUnsupported(reason="credit reversal confirmation is unavailable")
        receipt = self._receipts.get(context.operation_id)
        if receipt is None or not _matches_committed(tool, command, self.state):
            return ReconcileNoEffectConfirmed(reason="provider has no matching effect")
        return ReconcileEffectConfirmed(receipt=receipt)

    def counts(self) -> tuple[ProviderCount, ...]:
        return tuple(
            ProviderCount(name, item.executes, item.effects, item.reconciliations)
            for name, item in sorted(self._counts.items())
        )

    def compensation_order(self) -> tuple[str, ...]:
        return tuple(
            event.removeprefix("effect:")
            for event in self.events
            if event.removeprefix("effect:") in _COMPENSATIONS
        )

    async def _pause(self) -> None:
        if self.step_delay_seconds > 0:
            await asyncio.sleep(self.step_delay_seconds)

    def _outcome_for(self, tool: str, receipt: JsonObject) -> EffectOutcome:
        if _lost_reply(self.scenario, tool):
            raise ApplicationError("provider response was lost", type="ResponseLost")
        return EffectConfirmed(receipt=receipt)

    def _commit(self, tool: str, operation_id: str, receipt: JsonObject) -> None:
        self.state = _MUTATIONS[tool](self.state)
        self._receipts[operation_id] = receipt
        self._count(tool).effects += 1
        self.events.append(f"effect:{tool}")

    def _record(self, tool: str, prefix: str) -> None:
        self._count(tool).executes += 1
        self.events.append(f"{prefix}:{tool}")

    def _count(self, tool: str) -> _Count:
        return self._counts.setdefault(tool, _Count())


@dataclass(frozen=True)
class _EffectAdapter[CommandT: BaseModel]:
    provider: OrderResolutionProvider
    tool: str

    async def execute(self, command: CommandT, context: EffectContext) -> EffectOutcome:
        return await self.provider.execute(self.tool, command, context)

    async def reconcile(
        self, command: CommandT, context: ReconcileContext
    ) -> ReconciliationOutcome:
        return await self.provider.reconcile(self.tool, command, context)


@dataclass(frozen=True)
class _ReadAdapter:
    provider: OrderResolutionProvider

    async def read(self, command: VerifyOriginalStopped) -> OriginalShipmentCheck:
        return await self.provider.read(command)


@dataclass(frozen=True)
class _Spec:
    name: str
    model: type[BaseModel]
    compensation: str | None
    description: str


def build_registry(provider: OrderResolutionProvider) -> ToolRegistry:
    effects = tuple(_effect_def(provider, spec) for spec in _SPECS)
    check = ReadToolDefinition(
        "verify_original_stopped",
        VerifyOriginalStopped,
        OriginalShipmentCheck,
        _ReadAdapter(provider),
        "Verify the stuck original parcel was stopped before finalizing express replacement.",
    )
    return ToolRegistry((*effects, check))


def _effect_def(provider: OrderResolutionProvider, spec: _Spec) -> EffectToolDefinition[BaseModel]:
    reversibility = Reversibility.SEMANTIC if spec.compensation else Reversibility.IRREVERSIBLE
    capabilities = ToolCapabilities(
        idempotency_retention_seconds=86_400,
        reconciliation_supported=True,
        cancellation_supported=False,
        fencing_supported=True,
        reversibility=reversibility,
        partial_effects_possible=False,
    )
    return EffectToolDefinition(
        name=spec.name,
        definition_version="temporal-order-concern-v1",
        command_schema_version="order-concern-command-v1",
        input_model=spec.model,
        adapter=_EffectAdapter(provider, spec.name),
        capabilities=capabilities,
        compensate_with=spec.compensation,
        description=spec.description,
    )


def _receipt(tool: str, operation_id: str) -> JsonObject:
    digest = sha256(f"{tool}\0{operation_id}".encode()).hexdigest()
    return _JSON.validate_python({"receipt_ref": f"opaque://order-concern/{digest}/v1"})


def _lost_reply(scenario: ConcernScenario, tool: str) -> bool:
    return (scenario, tool) in {
        (ConcernScenario.LOST_RESPONSE, "issue_shipping_credit"),
        (ConcernScenario.COMPENSATION_FAILURE, "reverse_shipping_credit"),
    }


def _uncertain_reverse(scenario: ConcernScenario, tool: str) -> bool:
    return scenario is ConcernScenario.COMPENSATION_FAILURE and tool == "reverse_shipping_credit"


def _allowed(tool: str, command: BaseModel, state: ResolutionState) -> bool:
    checker = _ALLOW_RULES.get(tool)
    return checker(command, state) if checker is not None else False


def _matches_committed(tool: str, command: BaseModel, state: ResolutionState) -> bool:
    checker = _COMMIT_RULES.get(tool)
    return checker(command, state) if checker is not None else False


def _same_replacement(command: BaseModel, state: ResolutionState) -> bool:
    return isinstance(command, (ReserveReplacement, ReleaseReplacement)) and (
        command.order_id == state.order_id
        and command.sku == state.sku
        and command.quantity == state.replacement_quantity
    )


def _same_credit(command: BaseModel, state: ResolutionState) -> bool:
    return isinstance(command, (IssueShippingCredit, ReverseShippingCredit)) and (
        command.order_id == state.order_id
        and command.customer_id == state.customer_id
        and command.credit_minor == state.courtesy_credit_minor
        and command.currency == state.currency
    )


def _same_courier(command: BaseModel, state: ResolutionState) -> bool:
    return (
        isinstance(command, (BookExpressCourier, CancelExpressCourier))
        and command.order_id == state.order_id
    )


_ALLOW_RULES: dict[str, Callable[[BaseModel, ResolutionState], bool]] = {
    "reserve_replacement": lambda cmd, st: (
        _same_replacement(cmd, st) and not st.replacement_reserved
    ),
    "release_replacement": lambda cmd, st: _same_replacement(cmd, st) and st.replacement_reserved,
    "issue_shipping_credit": lambda cmd, st: _same_credit(cmd, st) and st.credit_status == "none",
    "reverse_shipping_credit": lambda cmd, st: (
        _same_credit(cmd, st) and st.credit_status == "issued"
    ),
    "book_express_courier": lambda cmd, st: _same_courier(cmd, st) and st.express_courier == "none",
    "cancel_express_courier": lambda cmd, st: (
        _same_courier(cmd, st) and st.express_courier == "booked"
    ),
}

_COMMIT_RULES: dict[str, Callable[[BaseModel, ResolutionState], bool]] = {
    "reserve_replacement": lambda cmd, st: _same_replacement(cmd, st) and st.replacement_reserved,
    "release_replacement": lambda cmd, st: (
        _same_replacement(cmd, st) and not st.replacement_reserved
    ),
    "issue_shipping_credit": lambda cmd, st: _same_credit(cmd, st) and st.credit_status == "issued",
    "reverse_shipping_credit": lambda cmd, st: (
        _same_credit(cmd, st) and st.credit_status == "reversed"
    ),
    "book_express_courier": lambda cmd, st: (
        _same_courier(cmd, st) and st.express_courier == "booked"
    ),
    "cancel_express_courier": lambda cmd, st: (
        _same_courier(cmd, st) and st.express_courier == "cancelled"
    ),
}

_MUTATIONS: dict[str, Callable[[ResolutionState], ResolutionState]] = {
    "reserve_replacement": lambda st: st.model_copy(update={"replacement_reserved": True}),
    "release_replacement": lambda st: st.model_copy(update={"replacement_reserved": False}),
    "issue_shipping_credit": lambda st: st.model_copy(update={"credit_status": "issued"}),
    "reverse_shipping_credit": lambda st: st.model_copy(update={"credit_status": "reversed"}),
    "book_express_courier": lambda st: st.model_copy(update={"express_courier": "booked"}),
    "cancel_express_courier": lambda st: st.model_copy(update={"express_courier": "cancelled"}),
}

_SPECS = (
    _Spec(
        "reserve_replacement",
        ReserveReplacement,
        "release_replacement",
        "Reserve a replacement unit while investigating the stuck order.",
    ),
    _Spec(
        "release_replacement",
        ReleaseReplacement,
        None,
        "Release the held replacement unit back to stock.",
    ),
    _Spec(
        "issue_shipping_credit",
        IssueShippingCredit,
        "reverse_shipping_credit",
        "Issue a courtesy shipping credit to the customer's account.",
    ),
    _Spec(
        "reverse_shipping_credit",
        ReverseShippingCredit,
        None,
        "Reverse the courtesy shipping credit when replacement is cancelled.",
    ),
    _Spec(
        "book_express_courier",
        BookExpressCourier,
        "cancel_express_courier",
        "Book an overnight courier for the replacement parcel.",
    ),
    _Spec(
        "cancel_express_courier",
        CancelExpressCourier,
        None,
        "Cancel the overnight courier booking.",
    ),
)

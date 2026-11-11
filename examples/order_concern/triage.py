"""Layer 1: Translate a free-form user prompt into an order saga or a polite refusal."""

from __future__ import annotations

import re
from collections.abc import Callable

from agentic_saga.temporal.contracts import SagaWorkflowInput
from agentic_saga.triage import (
    ClassifiedConcernTriage,
    ConcernClassification,
    build_pydantic_ai_concern_classifier,
)
from examples.order_concern.domain import POLITE_NON_ORDER_REPLY, StrictModel

_MIN_TERM_MATCHES = 2
_ORDER_TERMS = re.compile(
    r"\b(order(?:_[a-z0-9]+)?|paid|charg(?:e|ed)|deliver(?:y|ed)?|ship(?:ment|ped|ping)?|"
    r"parcel|package|arrived?|receiv(?:e|ed)|refund|replace(?:ment)?|tracking)\b",
    re.IGNORECASE,
)
_ORDER_ID = re.compile(r"\b(order_[a-z0-9]{1,32}|#[0-9]{3,12})\b", re.IGNORECASE)
ORDER_CONCERN_INSTRUCTIONS = (
    "Classify whether the customer message is an order processing concern (such as paying for an "
    "order that has not arrived, a stuck shipment, or requesting a replacement or refund). "
    "When in_scope is false, set message to a polite reply explaining that this is not an order "
    "processing issue and no order changes were started. When in_scope is true, set message to a "
    "concise one-sentence summary of the order concern."
)


class ConcernContext(StrictModel):
    order_id: str
    customer_id: str
    sku: str
    quantity: int
    credit_minor: int
    currency: str
    customer_concern: str


def is_order_processing_concern(prompt: str) -> bool:
    """Return True only when a user message describes an order, payment, or delivery concern."""
    matches = {match.group(0).lower() for match in _ORDER_TERMS.finditer(prompt)}
    return len(matches) >= _MIN_TERM_MATCHES or (
        _ORDER_ID.search(prompt) is not None and bool(matches)
    )


async def classify_order_concern_locally(prompt: str) -> ConcernClassification:
    """Zero-key offline classifier following the same contract as an LLM triage model."""
    if not is_order_processing_concern(prompt):
        return ConcernClassification(in_scope=False, message=POLITE_NON_ORDER_REPLY)
    summary = f"Paid-not-delivered order concern accepted: {prompt[:160]}"
    return ConcernClassification(in_scope=True, message=summary)


def build_local_concern_triage(
    workflow_builder: Callable[[str], SagaWorkflowInput],
) -> ClassifiedConcernTriage:
    """Build zero-key local concern triage sharing the exact LLM triage interface."""
    return ClassifiedConcernTriage(
        classifier=classify_order_concern_locally,
        workflow_builder=workflow_builder,
    )


def build_llm_concern_triage(
    model: object,
    workflow_builder: Callable[[str], SagaWorkflowInput],
) -> ClassifiedConcernTriage:
    """Build Pydantic AI LLM concern triage (`agentic-saga[agent]`) in front of Temporal."""
    classifier = build_pydantic_ai_concern_classifier(
        model, instructions=ORDER_CONCERN_INSTRUCTIONS
    )
    return ClassifiedConcernTriage(classifier=classifier, workflow_builder=workflow_builder)

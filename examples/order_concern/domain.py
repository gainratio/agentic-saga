"""Typed domain commands and scenarios for the paid-not-delivered order concern."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ConcernScenario(StrEnum):
    ALREADY_IN_TRANSIT = "already-in-transit"
    HAPPY_PATH = "happy-path"
    LOST_RESPONSE = "lost-response"
    COMPENSATION_FAILURE = "compensation-failure"
    NOT_AN_ORDER = "not-an-order"


DEFAULT_CONCERN_PROMPT = (
    "I paid $79 four days ago for order_8841 and still haven't received it — "
    "please rush a replacement or refund me."
)
DEFAULT_NON_ORDER_PROMPT = "What is the weather in Seattle this weekend?"
POLITE_NON_ORDER_REPLY = (
    "That doesn't look like an order processing issue. I can help when an order you paid for "
    "hasn't arrived or needs a replacement or refund — no order changes were started."
)


class ReserveReplacement(StrictModel):
    order_id: str
    sku: str
    quantity: int = Field(strict=True, gt=0)


class ReleaseReplacement(StrictModel):
    order_id: str
    sku: str
    quantity: int = Field(strict=True, gt=0)


class IssueShippingCredit(StrictModel):
    order_id: str
    customer_id: str
    credit_minor: int = Field(strict=True, gt=0)
    currency: str


class ReverseShippingCredit(StrictModel):
    order_id: str
    customer_id: str
    credit_minor: int = Field(strict=True, gt=0)
    currency: str


class BookExpressCourier(StrictModel):
    order_id: str


class CancelExpressCourier(StrictModel):
    order_id: str


class VerifyOriginalStopped(StrictModel):
    order_id: str


class OriginalShipmentCheck(StrictModel):
    verified: bool


class ResolutionState(StrictModel):
    order_id: str = "order_8841"
    customer_id: str = "customer_demo_001"
    sku: str = "sku_travel_pack"
    replacement_quantity: int = Field(default=1, strict=True, gt=0)
    courtesy_credit_minor: int = Field(default=1500, strict=True, gt=0)
    currency: str = "USD"
    replacement_reserved: bool = False
    credit_status: Literal["none", "issued", "reversed"] = "none"
    express_courier: Literal["none", "booked", "cancelled"] = "none"


@dataclass(frozen=True)
class ProviderCount:
    tool: str
    executes: int
    effects: int
    reconciliations: int

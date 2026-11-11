# Customer Order-Concern Compensating Saga

This example shows why all four layers of **Agentic Saga** work together on a real support problem:
a customer writes in saying *"I paid $79 four days ago for `order_8841` and still haven't received it — please rush a replacement or refund me."*

## The 4-Layer Architecture

1. **Agent / LLM Concern Intake (`start_concern_saga`, `ClassifiedConcernTriage`)** — Reads the free-form user prompt. If the prompt is **not** an order processing issue (for example, *"What is the weather in Seattle this weekend?"*), it politely replies that it is not an order processing issue and exits with **zero** Temporal workflows started and **zero** tool calls.
2. **Constrained Step Decision Engine (`Jev` / `PydanticAI` / `ResolutionAgent`)** — Inside the `agentic_saga.decide` Temporal Activity, sees only the currently eligible forward capabilities (`reserve_replacement` -> `issue_shipping_credit` -> `book_express_courier` -> `verify_original_stopped`) and proposes one step at a time.
3. **Durable Workflow State (`Temporal` `AgenticSagaWorkflow`)** — Enforces prerequisites and budgets, reconciles lost replies before retrying, verifies proof, runs newest-first compensating transactions (`cancel_express_courier` -> `reverse_shipping_credit` -> `release_replacement`) when `verify_original_stopped` discovers the original parcel was just scanned onto a delivery truck, and pauses in `HUMAN_REQUIRED` when a compensation reply is uncertain.
4. **Deterministic Paired Tools (`ToolRegistry`)** — Validates strict Pydantic inputs/outputs, derives deterministic idempotency keys, reconciles ambiguous calls, and pairs each forward effect with its exact compensation tool.

## Run It

```bash
# Default: paid-not-delivered concern where the original parcel is already on the truck -> newest-first undo
uv run poe demo

# Non-order prompt -> polite refusal, zero Temporal workflows started, zero tool calls
uv run poe demo not-an-order
uv run poe demo --prompt "What is the weather in Seattle this weekend?"

# Other saga outcomes
uv run poe demo happy-path
uv run poe demo lost-response
uv run poe demo compensation-failure
```

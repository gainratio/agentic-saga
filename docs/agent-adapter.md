# Agent decision adapters

## TL;DR

The default decision engine is **Jev through OpenRouter**. It uses the same `OPENROUTER_API_KEY`
as the Pydantic AI planner, so one key covers both. Calling Jev directly from TypeSafe
(`TYPESAFE_API_KEY`) is the alternative. Whichever you pick, the agent only chooses one step that
is allowed right now. The Temporal workflow runs that step and owns retries, lost-response checks,
undo (compensation), final proof, and the hand-off to a person.

The model never receives compensation or human-escalation controls. It sees `finish_saga` only
after the workflow has fresh proof for `succeeded_verified`.

```text
bounded public observation + eligible business schemas
                         |
                         v
   Jev via OpenRouter (default) / Jev direct / Pydantic AI
                         |
                         v
             typed, sequence-bound proposal
                         |
                         v
      deterministic Temporal workflow -> Activity -> provider
                         |
                         v
       reconciled evidence, compensation, or verified finish
```

This split is deliberate. A model-quality evaluation measures whether the model selected the right
advertised tool with the right public arguments. Temporal integration tests separately prove
transaction correctness. A successful tool call is not evidence that a payment or rollback was
correctly executed.

## Where Jev fits

Three pieces, three jobs:

- **The saga** is the job itself: a list of steps, where every step that changes something has an
  undo step. A checkout is reserve stock, then charge the card, then create the order. The undo
  steps are cancel the order, refund the card, release the stock.
- **Temporal** is the record keeper. It runs the saga as a durable state machine: every step and
  its result is written to history, so a crash picks up where it stopped instead of charging twice.
- **Jev** is the decision engine. On each turn your app builds a short list of complete, allowed
  next steps (for example "charge the card for order 123"), and Jev picks one of them and says how
  sure it is. It cannot invent a step or change its arguments.

Jev does not decide how to undo. If the job cannot be proven done, the workflow undoes the finished
steps newest-first on its own, and asks a person only when an undo cannot be proven.

Jev runs inside the `agentic_saga.decide` Temporal Activity, never inside the workflow code.
Temporal records the Activity's answer in history. When the workflow replays after a crash or a
deploy, it reads that recorded answer instead of calling Jev again, so replay stays deterministic
even though a model made the choice.

## Default: Jev through OpenRouter

Install the extra and set one key:

```bash
uv sync --extra jev-openrouter --group dev
cp .env.example .env
# Add your OPENROUTER_API_KEY to the ignored .env file, then export it in your shell.
```

This builds the Jev driver for the ecommerce example's checkout and hands it to the Temporal
Activities. Run it from the repository root. Building the driver makes no network call; Jev is only
called when a Temporal Worker runs the `agentic_saga.decide` Activity.

```python
from pathlib import Path

from agentic_saga.agents import (
    OpenRouterDecisionsSettings,
    ProposalCandidate,
    ToolCallIntent,
    build_openrouter_decisions_driver,
)
from agentic_saga.contracts.runtime import ExecutionBudget
from agentic_saga.manifest import load_saga_context
from agentic_saga.temporal import TemporalActivities
from examples.ecommerce.domain import ScenarioName
from examples.ecommerce.provider import EcommerceProvider, build_registry

registry = build_registry(EcommerceProvider(ScenarioName.HAPPY_PATH))
context = load_saga_context(
    Path("examples/ecommerce/saga.yaml"),
    registry=registry,
    invariant_checks=("no_external_effects", "obligations_reversed", "order_verified"),
)


async def candidates(observation, eligible_descriptors):
    """Build every complete next step Jev may pick. Jev picks one; it never writes arguments."""
    eligible = {descriptor.name for descriptor in eligible_descriptors}
    if "verify_order" not in eligible:
        return ()
    return (
        ProposalCandidate(
            candidate_id="choice_00000001",
            criteria="Verify the authoritative final order state.",
            minimum_confidence=0.9,
            proposal=ToolCallIntent(
                tool_name="verify_order",
                arguments={"order_id": "order_demo_001"},
                rationale="Fresh success proof is required.",
            ),
        ),
    )


# Reads OPENROUTER_API_KEY. Pinned to typesafe/jev-1.13; zero data retention is required.
driver = build_openrouter_decisions_driver(
    context, candidates, OpenRouterDecisionsSettings.from_environment()
)
activities = TemporalActivities(
    driver,
    registry,
    ExecutionBudget(turn_limit=6, tool_call_limit=4, elapsed_ms_limit=180_000, token_limit=6_000),
)
# Pass `activities` to agentic_saga.temporal.build_worker(...) to serve the workflow.
```

The route pins `typesafe/jev-1.13`, accepts only the response model `typesafe/jev-1.13-20260917`,
and calls only `https://openrouter.ai/api/alpha/decisions`; it never falls back to chat
completions. Every request asks OpenRouter for zero data retention and no data collection.

## Alternative: Jev direct from TypeSafe

Use this when you have a TypeSafe account and want no OpenRouter hop. Swap the builder and the key;
the candidate function stays the same.

```bash
uv sync --extra jev --group dev
export TYPESAFE_API_KEY=...   # your own key
```

```python
from agentic_saga.agents import JevSettings, build_jev_driver

driver = build_jev_driver(context, candidates, JevSettings.from_environment())
```

The direct route pins `jev-1.13.0` on `https://api.typesafe.ai` (override the version with
`TYPESAFE_DEFAULT_MODEL`; moving aliases such as `jev-latest` are rejected).

## What both Jev routes guarantee

Both routes reject unknown or duplicate choices, malformed probabilities, non-finite confidence,
low-confidence selections, private payloads, and stale proposals. A single legal candidate is
selected locally without a paid request. Provider SDK retries are zero because Temporal owns retry
policy. Both classify provider failures the same way (429 is rate limited, other 4xx is rejected,
5xx is a server error, timeouts are transport failures).

Install only the chosen transport and run its offline tests:

```bash
uv sync --extra jev-openrouter --group dev
# or
uv sync --extra jev --group dev

uv run pytest tests/unit/agents/test_choice.py tests/unit/agents/test_jev.py \
  tests/unit/agents/test_openrouter_decisions.py -q
```

## Install and prove it offline

```bash
uv sync --extra agent --group dev
uv run pytest tests/unit/agents tests/unit/temporal tests/integration/temporal -q
uv run python -m examples.ecommerce.eval
```

The last command strictly validates all 24 transaction fixtures and makes no model or network call.
The Pydantic AI/OpenRouter dependencies are optional; importing core Agentic Saga does not load
them.

## Pydantic AI planner with OpenRouter

Use this when you want a model to fill in tool arguments itself instead of choosing from a list
your app builds. It shares the same `OPENROUTER_API_KEY`.

```python
from agentic_saga.agents import OpenRouterSettings, build_openrouter_driver

settings = OpenRouterSettings.from_environment()
driver = build_openrouter_driver(context, settings)
proposal = await driver.next_action(observation, eligible_descriptors)
```

`context` is the validated, public `SagaContext`. `observation` is the bounded view produced by the
Temporal decision Activity. `eligible_descriptors` contains only capabilities whose prerequisites
and call budgets currently pass.

The native provider-visible surface is:

- each currently eligible business tool;
- `finish_saga(target_status="succeeded_verified", rationale=...)` only when fresh terminal proof
  exists.

There is no native `begin_compensation` or `escalate_to_human` tool. An ordinary failed proof
triggers workflow-owned compensation. An unknown effect is reconciled before any later choice.
Unresolved reconciliation or compensation moves the workflow to `HUMAN_REQUIRED`; a validated
Workflow Update resumes it only after a verification Activity accepts public authorization.

Pydantic AI receives deferred schemas, never registered Python business callables. A native
`charge_payment(...)` response therefore cannot charge anything. The adapter accepts exactly one
deferred call, supplies host-owned proposal identity and current sequence, and returns a typed
proposal to Temporal for deterministic revalidation.

The adapter constructs a bare Pydantic AI `Agent` with no built-in tools, filesystem, subagents,
planning, or memory; the only capability is a required tool call. Temperature is zero. One validation correction is allowed,
with at most two provider requests per durable agent turn. Provider SDK retries are zero because
Temporal owns retry policy.

The default model is pinned to `openai/gpt-oss-120b`. A moving OpenRouter route such as
`openrouter/auto`, `openrouter/free`, or a `latest` alias is rejected.

## Opt-in live model evaluation

The live evaluator calls the real Pydantic AI/OpenRouter native-tool path with the same bounded
observation shape used by the Temporal decision Activity. It does not run business Activities and
does not claim transaction correctness.

```bash
cp .env.example .env
chmod 600 .env
# Add your OPENROUTER_API_KEY to the ignored .env file.

RUN_LIVE_MODEL_EVALS=1 uv run python -m examples.ecommerce.eval \
  --live --suite smoke --samples 1 --output .artifacts/eval
```

The smoke suite makes one bounded decision request. `release` runs the three canonical corpus
checkpoints where a model has a legal decision. `extended` runs all 18 model-relevant checkpoints;
the six workflow-owned escalation fixtures are intentionally excluded.

The report records the configured model/provider, selected native tool, correctness, latency, and
token/cost data when the public adapter exposes it. The current adapter does not expose provider
usage, so those fields remain `null` and the CLI says `usage=unavailable`. Provider failures are
reported separately and never improve or reduce model-quality denominators. Artifacts contain no
credentials or expected arguments.

Live evaluation requires both `RUN_LIVE_MODEL_EVALS=1` and `OPENROUTER_API_KEY`. Ordinary tests do
not load `.env` and do not make network calls.

## Safety boundary

The agent may:

- select one advertised business tool and provide schema-valid public arguments;
- propose `succeeded_verified` only when the workflow advertises verified finish.

The Temporal workflow alone may:

- accept or reject stale and ineligible proposals;
- assign stable provider idempotency identity;
- execute and retry Activities;
- reconcile an unknown effect before continuing;
- record compensation obligations and unwind them in reverse order;
- enter `HUMAN_REQUIRED` and verify an authorization-bound resolution;
- assign final workflow status.

## Honest limits

- Model evaluation demonstrates decision behavior on a fixed public corpus, not universal model
  reliability.
- Temporal provides durable execution, not external exactly-once semantics. Providers still need
  durable idempotency and authoritative reconciliation.
- Compensation is a business action, not database rollback. It can fail and require a verified
  operator decision.
- Model/provider identity is configured identity unless the provider response exposes a separately
  authenticated returned identity.

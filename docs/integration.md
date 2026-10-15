# Use Agentic Saga in your own app

TL;DR: you register each step your job can take, pair every step that changes something with the
step that undoes it, plug in something that picks the next step (a script or an AI model), and
run it on Temporal. The library checks every choice, runs the step, proves the end result, and
undoes finished steps newest-first when the job cannot be proven done.

This guide walks through the smallest complete program,
[`examples/quickstart/trip.py`](../examples/quickstart/trip.py), then covers what changes when you
move from that example to real services. The README shows the short version.

## 1. Install

The library is not on PyPI yet. Install it from GitHub:

```bash
uv add "agentic-saga @ git+https://github.com/gainratio/agentic-saga"
# or
pip install "git+https://github.com/gainratio/agentic-saga"
```

It needs Python 3.12 or 3.13 on Linux or macOS. It brings `temporalio` and `pydantic` with it.

## 2. The smallest complete program

`trip.py` books a flight, then a hotel, then checks the trip. The check fails on purpose, so the
saga cancels the hotel and then the flight. Run it from a clone of this repository:

```bash
uv run python -m examples.quickstart.trip
```

```text
Forward steps: book_flight -> book_hotel -> confirm_trip
Undo steps:    cancel_hotel -> cancel_flight
Outcome:       compensated_verified
```

Temporal's Rust core may also print a `WARN ... Temporal Server 1.16.0 or newer is required` line
first. It comes from the throwaway test server and is harmless. The file also runs unchanged in
your own project after the install line above (`python trip.py`).

The file has seven numbered blocks. Here is what each one is for:

| Block | What you write | Why |
| --- | --- | --- |
| 1. Typed commands | Pydantic models for each step's input, strict and frozen | Bad arguments are rejected before anything runs |
| 2. Adapters | `execute` and `reconcile` for steps that change things, `read` for checks | This is where your real API calls go |
| 3. Registry | One `EffectToolDefinition` per changing step, with `compensate_with` naming its undo | The library knows how to undo each step |
| 4. Workflow tools | `WorkflowTool` entries: order (`prerequisites`), undo pairing, which read proves success | The workflow only offers steps whose prerequisites are done |
| 5. Agent | An `AgentDriver` with `next_action(observation, available_tools)` | Picks one of the offered steps, or says it is finished |
| 6. Goal and limits | `SagaGoal`, `ExecutionBudget`, `SagaWorkflowInput` | Caps turns and tool calls; the agent cannot exceed them |
| 7. Run | `TemporalActivities`, `build_worker`, `start_saga` | Starts a worker and the workflow, and waits for the result |

A few rules the example follows that are easy to miss:

- **Undo steps are registered but never offered to the agent.** The workflow decides when to undo
  and in what order.
- **The undo pairing appears twice:** `compensate_with` on the registry entry and
  `compensation_tool` on the `WorkflowTool`. They must match, or the step fails with
  `tool_contract_mismatch`.
- **An undo step receives the forward step's arguments merged with its result,** filtered to the
  undo step's input fields. Give the forward and undo steps compatible input models.
- **A proof step must return `verified: true` to count as success.** Anything else means the job
  is not proven, which starts the undo.
- **`build_worker` needs a human sign-off activity** even if no undo in your app can fail. The
  example registers one that always refuses. See [Operations](operations.md) for a real one.

## 3. Run Temporal for real

The example starts Temporal's test server inside the process with
`WorkflowEnvironment.start_time_skipping()`. That is test infrastructure, not an embedded
production mode. For manual development, install the [Temporal CLI](https://docs.temporal.io/cli)
and run:

```bash
temporal server start-dev
```

That starts a disposable local Temporal Service on `localhost:7233` with its Web UI on
`localhost:8233`. Connect to it and serve your workflow:

```python
from agentic_saga.temporal import (
    TemporalActivities,
    build_worker,
    connect_local_client,
    start_saga,
)

client = await connect_local_client("localhost:7233", namespace="default")
activities = TemporalActivities(agent, tool_registry, execution_budget)

async with build_worker(
    client,
    task_queue="checkout",
    activities=activities,
    human_resolution_activity=verify_human_resolution,
):
    handle = await start_saga(client, saga_input, task_queue="checkout")
    final_state = await handle.result()
```

Use the same task queue for the worker and the `start_saga` call. `connect_local_client` refuses
anything but a loopback address because it turns TLS off. For Temporal Cloud, use
`TemporalCloudConfig` with `connect_cloud_client`:

```python
import os

from pydantic import SecretStr

from agentic_saga.temporal import TemporalCloudConfig, connect_cloud_client

cloud = TemporalCloudConfig(
    target_host="your-namespace.tmprl.cloud:7233",
    namespace="your-namespace.your-account",
    api_key=SecretStr(os.environ["TEMPORAL_API_KEY"]),
)
client = await connect_cloud_client(cloud)
```

| Use | Temporal mode |
| --- | --- |
| Automated tests | `WorkflowEnvironment.start_time_skipping()`; isolated test server and virtual time |
| Local manual development | `temporal server start-dev`; disposable service and Web UI |
| Production | Temporal Cloud or an operated, production-ready self-hosted Temporal Service |

Step inputs and results are stored in Temporal's history. If private data goes in, configure the
client and workers with the same encrypting Payload Codec backed by your key management service,
and limit who can read the namespace. See [SECURITY.md](../SECURITY.md).

## 4. Connect real services

The fake adapters in `trip.py` always succeed. Real ones need three things:

- **A stable request ID.** Temporal runs activities at least once. Pass the step's
  `context.operation_id` to your provider as its idempotency key, so a repeated call is
  recognized instead of charging twice.
- **A real `reconcile`.** When a reply is lost, the workflow asks the adapter what happened before
  doing anything else. Look the operation up by its ID and return what you found:
  `ReconcileEffectConfirmed`, `ReconcileNoEffectConfirmed`, `ReconcilePending`,
  `ReconcileConflict`, or `ReconcileUnsupported` when you cannot tell.
- **The reference you need to undo it.** Return it in the step's result so the undo step receives
  it.

The ecommerce example implements all three; read
[`examples/ecommerce/provider.py`](../examples/ecommerce/provider.py).

## 5. Pick who chooses the next step

The agent is the planner, not the transaction engine. It sees the goal, the current facts, and only
the steps allowed right now, and proposes one. The workflow checks that proposal and controls
execution. You can swap the scripted agent for:

- Jev through OpenRouter (the default model route),
- Jev called directly from TypeSafe,
- any model through Pydantic AI and OpenRouter.

All three are in the [agent adapter guide](agent-adapter.md). To try a model-backed agent:

```bash
cp .env.example .env
chmod 600 .env
# Edit .env and set OPENROUTER_API_KEY to your own key.
uv sync --extra agent --group dev
```

`.env` stays local and `.env.example` holds no secrets. The library never reads `.env` itself;
your application decides whether to load it. Never pass the key through Temporal payloads, and set
an OpenRouter spend limit before live use.

## 6. See the full worked example

The ecommerce example in [`examples/ecommerce`](../examples/ecommerce/README.md) covers every
outcome. This one command runs all of them on Temporal's test server:

```bash
uv run pytest -q -m temporal --force-enable-socket tests/bdd/steps/test_ecommerce_saga.py
```

What it checks:

- The order completes: reserve inventory -> charge payment -> schedule fulfillment -> verify order.
- The delivery is refused after payment, so the undo runs newest-first:
  cancel fulfillment -> refund payment -> release inventory.
- A payment goes through but its reply is lost. The activity reconciles the same operation before
  the saga continues, so nobody is charged twice.
- A refund cannot be proven. The workflow stops at `human_required`;
  stale and unauthorized Updates are rejected, and an authorized Update lets the undo finish.

The readable behavior is in `tests/bdd/features/ecommerce_saga.feature`. To watch any of these
scenarios live in your browser, run `uv run poe demo <scenario>`
(`happy-path`, `business-failure`, `lost-response`, or `compensation-failure`).

## Next

- [Architecture](ARCHITECTURE.md): the flow, building blocks, and source map.
- [Temporal safety contract](temporal-safety-contract.md): the exact rules the workflow enforces.
- [Operations](operations.md): running workers, retries, and human pauses.
- [Context manifest](context-manifest.md): describing the job to a model in `saga.yaml`.

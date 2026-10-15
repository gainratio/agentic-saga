# Agentic Saga

A Python library that lets an AI agent run multi-step jobs like a checkout, and undoes finished steps if one fails.

**Try it:** `git clone https://github.com/gainratio/agentic-saga && cd agentic-saga && uv run poe demo` opens a live run in your browser. No API key.

A checkout touches several outside services: reserve the stock, charge the card, book the
delivery. If the delivery company says no after the card was charged, someone has to refund the
card and release the stock, in that order, exactly once. Doing that by hand in code is fiddly.
Letting an AI agent do it is worse: you do not want a language model deciding when to refund or
whether a lost response means "paid" or "not paid".

Agentic Saga splits the job. The agent only picks the next step from a short list of steps that
are allowed right now. Ordinary Python rules, run by [Temporal](https://temporal.io) (an
open-source service that records every step, so a crash does not lose work), check that choice,
run the step, and undo the finished steps newest-first if the job cannot be proven done. If even
the undo cannot be proven, it stops and waits for a person.

**Technical docs:** [Architecture](docs/ARCHITECTURE.md) · [Use it in your app](docs/integration.md) · [Getting started for developers](docs/GETTING_STARTED.md) · [Agent adapters](docs/agent-adapter.md) · [Safety rules](docs/temporal-safety-contract.md) · [Operations](docs/operations.md)

![The live demo in the browser: a checkout reserves stock, charges the card and books delivery; the final check fails; the page then shows the delivery cancelled, the card refunded and the stock released, ending in "Safely undone"](docs/assets/live-demo.gif)

## Try it

You need [uv](https://docs.astral.sh/uv/) and an internet connection for the first run. uv
installs a matching Python and the dependencies if you don't have them. Nothing else to install, and no
account or API key.

```bash
git clone https://github.com/gainratio/agentic-saga.git
cd agentic-saga
uv run poe demo
```

The command starts a throwaway Temporal server on your machine (the first run downloads it, about
a minute), runs one checkout saga on it, and opens the page above. Each step takes about a second
and a half so you can follow it:

1. The agent reserves the stock, charges the card, and books the delivery. Each box turns green.
2. The final check fails: the delivery company refused the order.
3. The workflow undoes the finished steps newest-first: cancel the delivery, refund the card,
   release the stock.
4. The page shows the result, **Safely undone**, with each undo confirmed.

The "agent" in this demo is a fixed script and the shop services are simulations, so no AI model
is called. The page says so in its dark banner. Your terminal shows the same run (this is a real
run; the port numbers change each time):

```text
Agentic Saga live demo: business-failure (delivery fails after payment, so earlier steps are undone).
Mode: scripted agent, simulated shop services, no API key, no model calls.
Temporal: starting a throwaway local server (the first run downloads it once)…
Temporal CLI 1.9.1 (Server 1.32.0, UI 2.54.1)

Temporal Server:      localhost:62953
Temporal Persistence: in-memory
Temporal Metrics:     http://localhost:62955/metrics
  01 saga_started -> running
  02 agent_decision_recorded reserve_inventory -> running
Recorder: http://127.0.0.1:62969  (this page updates every second)
  03 effect_outcome_recorded reserve_inventory -> running
  04 agent_decision_recorded charge_payment -> running
  05 effect_outcome_recorded charge_payment -> running
  06 agent_decision_recorded schedule_fulfillment -> running
  07 effect_outcome_recorded schedule_fulfillment -> running
  08 agent_decision_recorded verify_order -> running
  09 invariant_evaluated verify_order -> running
  10 compensation_started -> compensating
  11 compensation_outcome_recorded cancel_fulfillment -> compensating
  12 compensation_outcome_recorded refund_payment -> compensating
  13 compensation_outcome_recorded release_inventory -> compensating
  14 compensation_completed -> compensating
  15 terminal_assigned -> compensated_verified
Live run finished: compensated_verified. Press Ctrl+C to stop the page.
```

The page stays open after the run so you can click **Watch from start** to replay it, or open any
step for its details. Press Ctrl+C to stop. Three other runs show the other cases:

```bash
uv run poe demo happy-path            # every step works
uv run poe demo lost-response         # the payment reply is lost; it is checked, not repeated
uv run poe demo compensation-failure  # the refund can't be proven, so a person must sign off
```

In the last two, the terminal also prints a Python traceback ending in
`BusinessToolAdapterFailure`. That is the simulated lost reply, and it is expected. In
`compensation-failure` the demo plays the person and signs off for them, so the undo can finish.

Add `--step-delay 0` to run at full speed, or `--temporal-address localhost:7233` to use a
Temporal server you already started with `temporal server start-dev`. You can see that server's
own view of the run in its web UI at `localhost:8233`.

## Use it in your own code

Install it from GitHub (it is not on PyPI yet):

```bash
uv add "agentic-saga @ git+https://github.com/gainratio/agentic-saga"
```

The smallest complete program is [`examples/quickstart/trip.py`](examples/quickstart/trip.py). It
books a flight and a hotel, then a final check fails, and the saga cancels both. Here are its key
parts. First, register each step that changes something, and name the step that undoes it:

```python
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
```

`booking(...)` wraps your API call in an `EffectToolDefinition`; `confirm_trip` is a read-only
check. Then tell the workflow the order of the steps and which check proves the job is done:

```python
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
```

Finally, start a worker and the saga, and wait for the result. Here the agent is a script; swap in
a model-backed one from the [agent adapter guide](docs/agent-adapter.md) without changing anything
else:

```python
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
```

Run it with `uv run python -m examples.quickstart.trip` from this repository, or copy the file
into your own project and run `python trip.py`:

```text
Forward steps: book_flight -> book_hotel -> confirm_trip
Undo steps:    cancel_hotel -> cancel_flight
Outcome:       compensated_verified
```

The code above is cut straight from that file, and a test runs the file, so this section cannot
drift from what works. The [integration guide](docs/integration.md) explains each part, the rules
that are easy to miss, how to connect a real Temporal server or Temporal Cloud, and what a real
service adapter must do (a stable request ID, and a way to look up a lost reply).

## How it works

Your code registers each step as a typed Python function, and pairs every step that changes
something with the step that undoes it (charge with refund, reserve with release). On each turn the
agent sees the goal, the current facts, and only the steps allowed right now, and proposes one.
A Temporal Workflow checks the proposal against those rules and limits, runs the step, and records
the result. If a reply is lost, it asks the provider what happened before doing anything else. The
job only counts as done after a fresh check proves it; otherwise every finished step is undone in
reverse order, and a person is asked only when the undo itself cannot be proven.

### Who decides the next step

The default decision engine for real use is Jev, a model that only picks the next step. Your app
lists the steps allowed right now, and Jev chooses one and says how sure it is. It never writes
arguments and never decides how to undo; the workflow does that. Jev runs inside the
`agentic_saga.decide` Temporal Activity, so its answer is recorded and a replay reads the recorded
answer instead of asking again. It needs one key, `OPENROUTER_API_KEY`:

```python
from agentic_saga.agents import OpenRouterDecisionsSettings, build_openrouter_decisions_driver

# context: your SagaContext; candidates: async function returning the allowed ProposalCandidates
driver = build_openrouter_decisions_driver(
    context, candidates, OpenRouterDecisionsSettings.from_environment()
)
```

The alternatives are Jev called directly from TypeSafe (`build_jev_driver` with
`TYPESAFE_API_KEY`), any model through [Pydantic AI](https://ai.pydantic.dev) and OpenRouter, or a
scripted driver like the one in the demo. All four are in the
[agent adapter guide](docs/agent-adapter.md), with a full runnable example.

### The browser page

The browser page is the Flight Recorder, a small React app bundled with the library. It reads the
run's record and draws it; it never runs anything itself. `uv run poe demo` serves it while the run
is still going. `uv run agentic-saga demo --open` serves four recorded runs without Temporal.

## What it does not do

- **It is not released.** Version 0.1.0 is not on PyPI and has no tagged release. Install it from
  GitHub.
- **It cannot make an outside service safe to retry.** Your payment or stock service must accept a
  stable request ID so a repeated call is recognized. The example services are simulations.
- **It needs a Temporal server.** The demo and examples start a throwaway one for you. Real use
  needs your own (`temporal server start-dev` locally) or Temporal Cloud.
- **It does not judge the AI model.** Tests never call a paid model. How well a live model picks
  steps is not tested here.
- **Anything you put in a step's inputs is stored in Temporal.** Use Temporal's payload encryption
  for private data. With an AI agent on, the goal, the current facts, and the allowed steps are sent
  to OpenRouter with your key.
- **Recorded runs are hash-checked, not signed.** Someone who can edit a trace file can rewrite it.
- Python 3.12 or 3.13 on Linux or macOS only.

## When to use something else

| If you need | Use |
| --- | --- |
| Every change lives in one database | A database transaction |
| A fixed sequence of steps that rarely changes | Temporal on its own |
| An agent that only reads or drafts, and nothing it does is hard to undo | An agent framework calling tools directly |
| A few routes you can write out by hand | Plain `if/else` code |
| An agent picking steps that change real things, with undo and proof built in | Agentic Saga |

## Develop

```bash
uv run poe gate
```

This runs the Python checks CI runs: formatting, lint, strict typing, complexity, offline tests,
Temporal tests, and at least 90% branch coverage on the core. The browser app in
`web/flight-recorder/` has its own check. [Getting started for developers](docs/GETTING_STARTED.md)
covers prerequisites, timings, a map of the code, and your first change. Then read
[CONTRIBUTING.md](CONTRIBUTING.md).

## More detail

- [Use it in your app](docs/integration.md): the minimal example explained, real Temporal and
  Temporal Cloud, real service adapters, and picking an agent.
- [Getting started for developers](docs/GETTING_STARTED.md): from a fresh clone to a green local
  build and your first change.
- [Architecture](docs/ARCHITECTURE.md): the flow, the building blocks, the source map, security,
  and what the tests prove.
- [Explore the interactive architecture map](docs/architecture/index.html).
- [Temporal safety contract](docs/temporal-safety-contract.md): the exact rules the Workflow
  enforces.
- [Operations](docs/operations.md): running Workers, retries, human pauses, and known limits.
- [Agent adapters](docs/agent-adapter.md): Jev through OpenRouter (the default), Jev direct,
  Pydantic AI, and the scripted driver.
- [Context manifest](docs/context-manifest.md): describing a job to the agent in `saga.yaml`.
- [Flight Recorder](docs/flight-recorder.md): the browser page, live and recorded.
- [ADR 0001](docs/adr/0001-temporal-runtime.md): why Temporal is the only durability engine.
- [Quality review, 2026-09-19](docs/NORTHSTAR-REVIEW.md): an internal review and its findings.
- [Ecommerce example](examples/ecommerce/README.md): the full checkout with all four outcomes.
- [PROVENANCE.md](PROVENANCE.md): how a release candidate is built and checked.
- [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md), and
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT — see [LICENSE](LICENSE). To cite this project, use [CITATION.cff](CITATION.cff).

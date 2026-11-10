# CLAUDE.md — Agentic Saga (`agentic-saga`)

Canonical agent instructions for `gainratio/agentic-saga`.

## Status & Portfolio Pointers

- **Package version:** `0.1.0` (`src/agentic_saga/_version.py`, Python `>=3.12`, MIT)
- **Portfolio hub:** `https://open-source.gainratio.com/agentic-saga/`
- **Portfolio engineering standard §8 (WASM):** Not applicable — Python library on Temporal Python SDK plus React Flight Recorder viewer (`web/flight-recorder/`); zero WASM modules.
- **Portfolio engineering standard §3.1–§3.4:**
  - **§3.1 (Trust gate):** Covered by `pytest-bdd` Temporal scenarios (`tests/bdd/`) and unit tests (`tests/`) — idempotency keys, lost-response reconciliation before retry/undo, reverse LIFO compensation, verified human resolution via authenticated Temporal Updates, and bounded `saga.yaml` manifest parsing.
  - **§3.2 (Probabilistic input -> canonical data):** Enforced architecturally — models (`Jev`, `OpenRouterDecisions`, `PydanticAIDriver`) run inside `agentic_saga.decide` Activity and can only propose one currently eligible forward step; the deterministic Temporal Workflow validates eligibility, budgets, prerequisites, proof reads, and reverse compensation.
  - **§3.3–§3.4:** N/A (no public composite ranking scores or PDF exports).

## Stack & Layout

- **Python toolchain:** `uv` (`uv.lock`), Hatchling, Ruff (`lint-check` + `format-check`, max line length 100), `mypy --strict`, `xenon` (`A/A/A`), `pytest` + `pytest-bdd` + `pytest-socket` (`>=90%` branch coverage), Poe the Poet (`poe gate`), Dagger CI (`.dagger/`).
- **Frontend viewer:** `web/flight-recorder/` (TypeScript + React + Vitest static Flight Recorder assets synced into `src/agentic_saga/demo/static`).
- **Source layout:**
  - `src/agentic_saga/` — Temporal Workflow, Activities, tool registry, trace projection, CLI (`agentic-saga`), and optional agent drivers (`agents/`).
  - `examples/quickstart/trip.py` — minimal runnable example tested verbatim against `README.md`.
  - `examples/ecommerce/` — 4-scenario live Flight Recorder demo (`uv run poe demo`).
  - `scripts/` — release artifact builder, coverage checks, and release-candidate verifier.

## Core Invariants

1. **The agent plans; the Temporal Workflow owns state, reconciliation, and compensation.** Never allow a model adapter to trigger compensation, construct arbitrary tool arguments outside schema validation, or declare terminal state without `proof_for_success` verification.
2. **Reconcile lost responses before retrying or compensating.** Uncertain Activity outcomes query provider reconciliation using the stable operation ID before deciding next action.
3. **README & docs contracts are executable.** `tests/test_readme_contract.py` pins `README.md` section order, verbatim code blocks from `examples/quickstart/trip.py`, link targets, and bans internal/hype buzzwords.

## Quality Gates (Non-Negotiable)

Run `uv run poe gate` before every commit:

- **Scar (hardcoded `compensation_verified` claim):** Early workflows emitted `compensation_verified` (`obligations_reversed=True`) without evaluating an invariant. Replaced with `compensation_completed` backed by each undo step's confirmed outcome (`CHANGELOG.md` `0.1.0`).
- **Scar (overstated `proof_for_success` default):** `proof_for_success` defaults to `False`; docs and code must state that fresh proof reads apply specifically to steps declaring `proof_for_success=True`.
- **Scar (100-column line width & strict typing on scripts):** Both `src/` and `scripts/` run under `ruff format --check`, `mypy --strict`, and `xenon A/A/A`.

- **Scar (Python 3.14 venv trap):** `scripts/build_runtime_wheelhouse.sh` requires Python `3.12` or `3.13`; running `uv run poe gate` with a default Python `3.14` `.venv` fails `tests/test_release_contract.py`. Always pin `--python 3.13` (`uv run --python 3.13 poe gate`) locally when Python 3.14 is installed on the host.

## Commands

```sh
uv run --python 3.13 poe gate              # full Python gate: lint, format, mypy, xenon A, unit + Temporal BDD + release-script cov
uv run --python 3.13 poe demo              # launch local Temporal + live Flight Recorder demo (no API key)
uv run --python 3.13 poe release-candidate # build and verify clean wheel + sdist release artifacts
```

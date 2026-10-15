# Getting started for developers

TL;DR: by the end you will have a working checkout, the full Python check passing, the demo running
in your browser, and one small tested change made. It takes about 10 minutes. Every command here
was run from a fresh clone on an Apple silicon Mac on 2026-10-15; the times are from that run.

## 1. Prerequisites

| Tool | Version | Check it | Needed for |
| --- | --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | 0.8 or newer (tested 0.8.5) | `uv --version` | Everything |
| Python | 3.12 or 3.13 (CI uses 3.12.12 and 3.13.14) | `python3 --version` | Everything. uv installs one if missing |
| git | any recent | `git --version` | Everything |
| Node.js | 24 or newer (CI uses 24.6; tested 26.11) | `node --version` | Only web UI work |
| pnpm | exactly 11.5.0, run through `npx` | `npx --yes pnpm@11.5.0 --version` | Only web UI work |

macOS or Linux only. You do not need Docker, a Temporal server, or an API key: the tests download
Temporal's test server on first use and start it for you. We call pnpm as `npx --yes pnpm@11.5.0`
everywhere, which pins the version and works even when a global pnpm shim is broken. If another
Python virtualenv is active in your shell, run `deactivate` first so uv uses the project's `.venv`.

## 2. Clone, install, run the full check

```bash
git clone https://github.com/gainratio/agentic-saga.git
cd agentic-saga
uv sync --group dev        # 1 s here; longer the first time uv downloads packages
uv run poe gate            # 1 min 7 s to 1 min 17 s
```

`uv run poe gate` runs, in order: ruff lint and format checks, `mypy --strict`, Xenon complexity
(grade A everywhere), the offline tests, the Temporal tests, and two branch-coverage checks (90%
minimum on the library and on the release scripts). It stops at the first failure.

Success ends like this (about 950 offline tests, 39 Temporal tests, 65 release-script tests):

```text
Poe => python -m scripts.release_runner --check-python-coverage .coverage.json
core branch coverage: 90.655%
...
============================== 65 passed in 0.87s ==============================
Poe => python -m scripts.release_runner --check-release-coverage .coverage-release-scripts.json
release-script branch coverage: 90.909%
```

Some useful single steps, all defined under `[tool.poe.tasks]` in `pyproject.toml`:

| Command | What it does |
| --- | --- |
| `uv run poe test` | Offline tests only, with the 90% coverage floor |
| `uv run poe test-temporal` | Tests that start the local Temporal test server |
| `uv run poe typecheck` | `mypy --strict` on `src`, `tests`, `examples` |
| `uv run ruff format .` | Reformat Python files (do this before `poe gate`) |

## 3. See it run

The smallest example books a trip, fails the final check, and undoes the bookings newest-first:

```bash
uv run python -m examples.quickstart.trip    # 1 s
```

```text
Forward steps: book_flight -> book_hotel -> confirm_trip
Undo steps:    cancel_hotel -> cancel_flight
Outcome:       compensated_verified
```

It also prints a harmless `WARN ... Temporal Server 1.16.0 or newer` line. Read
[examples/quickstart/trip.py](../examples/quickstart/trip.py) top to bottom: it is the whole API in
one file.

The browser demo runs the checkout saga on a throwaway Temporal server and shows each step live:

```bash
uv run poe demo                 # opens your browser; add --no-open to skip that
uv run poe demo happy-path      # other scenarios: lost-response, compensation-failure
```

It printed `Live run finished: compensated_verified. Press Ctrl+C to stop the page.` after 11
seconds. Press Ctrl+C to stop it. The full example is described in
[examples/ecommerce/README.md](../examples/ecommerce/README.md).

## 4. Map of the code

| Path | What it owns |
| --- | --- |
| `src/agentic_saga/contracts/` | Typed, size-limited values that cross every boundary: tool calls, outcomes, budgets, traces, redaction |
| `src/agentic_saga/temporal/` | The Workflow (checks each proposal, enforces limits, undoes newest-first), the Activities that call the agent and your tools, client and Worker helpers, the journal |
| `src/agentic_saga/agents/` | Decision adapters: Pydantic AI through OpenRouter, Jev, OpenRouter Decisions |
| `src/agentic_saga/manifest.py` | Loads and checks `saga.yaml` |
| `src/agentic_saga/demo/` | The local replay server and the built web UI it serves (`static/`, generated, see section 6) |
| `src/agentic_saga/cli/` | The `agentic-saga demo` command |
| `examples/quickstart/` | `trip.py`: the minimal end-to-end example |
| `examples/ecommerce/` | The full checkout example: simulated providers, scripted agent, `live.py` (the `poe demo` command), evaluation |
| `web/flight-recorder/` | The React + Vite replay UI. Source in `src/`, browser tests in `e2e/` |
| `tests/unit/` | Fast tests, grouped like `src/agentic_saga/` |
| `tests/integration/` | Cross-module tests: CLI, manifest, examples, the Workflow on Temporal |
| `tests/bdd/` | The four checkout scenarios in plain language (`features/ecommerce_saga.feature`) |
| `tests/contract/` | `ToolAdapterContract`: a reusable test class a durable tool adapter must pass |
| `tests/live_model/` | Optional paid model evaluations, skipped unless you opt in |
| `tests/test_*.py` | Repository-wide checks: README and docs wording, package contents, public API, release files |
| `scripts/` | Release build, release verification, coverage checks, mutation testing |
| `docs/` | Architecture, guides, and decision records ([ARCHITECTURE.md](ARCHITECTURE.md) is the overview) |

## 5. Your first change, test first

The task: let the trip example finish successfully when the trip confirmation passes. Today
`run_trip()` always fails the check.

1. Branch: `git switch -c feat/trip-confirmed-path`.
2. Write the failing test. Add this to `tests/integration/examples/test_quickstart_example.py`:

   ```python
   @pytest.mark.asyncio
   @pytest.mark.temporal
   async def test_should_finish_verified_and_undo_nothing_when_trip_confirmation_passes() -> None:
       result = await run_trip(confirmed=True)

       assert result.forward == ("book_flight", "book_hotel", "confirm_trip")
       assert result.undo == ()
       assert result.status == "succeeded_verified"
   ```

3. Run just that test and watch it fail for the right reason:

   ```bash
   uv run pytest -q -m temporal --force-enable-socket \
     tests/integration/examples/test_quickstart_example.py -k confirmation_passes
   ```

   ```text
   E       TypeError: run_trip() got an unexpected keyword argument 'confirmed'
   1 failed, 2 deselected in 0.49s
   ```

4. Make it pass in `examples/quickstart/trip.py`: add a `confirmed: bool = False` field to
   `FakeTripCheck` and return `TripCheck(verified=self.confirmed)`; pass `confirmed` through
   `build_registry(...)`; and give `run_trip` a keyword-only `confirmed: bool = False`. Rerun the
   same command: `3 passed` once you drop the `-k` filter.
5. Format, then run the full check:

   ```bash
   uv run ruff format .
   uv run poe gate
   ```

Other changes follow the same loop. Workflow behavior gets a test in `tests/unit/temporal/` or
`tests/integration/temporal/`, or a new scenario in `tests/bdd/features/ecommerce_saga.feature`.
If you change the README, `tests/test_readme_contract.py` checks its section order, example output,
and links, so update it in the same change.

## 6. Changing the web UI

The replay UI lives in `web/flight-recorder/`. The Python package serves a built copy of it from
`src/agentic_saga/demo/static/`, which is committed. CI fails if the two drift apart.

```bash
cd web/flight-recorder
npx --yes pnpm@11.5.0 install --frozen-lockfile     # 1 s with a warm pnpm store
npx --yes pnpm@11.5.0 exec playwright install chromium   # once per machine
npx --yes pnpm@11.5.0 gate                          # 24 s
```

`pnpm gate` runs Biome lint, TypeScript, Vitest with coverage (204 tests), the asset-sync script's
own tests, the Vite build, `assets:check`, and Playwright browser tests (27, desktop and mobile
Chromium). Success ends with `27 passed`.

The edit loop:

1. Edit files under `web/flight-recorder/src/`.
2. Run `npx --yes pnpm@11.5.0 gate`. If you changed the UI, it fails at `assets:check` with
   `package assets are out of sync`. That is expected.
3. Run `npx --yes pnpm@11.5.0 assets:sync`. It copies `dist/` into
   `src/agentic_saga/demo/static/`.
4. Run `npx --yes pnpm@11.5.0 gate` again, then `uv run poe gate` from the repo root.
5. Commit the source change and the updated `src/agentic_saga/demo/static/` together.

More on the UI: [flight-recorder.md](flight-recorder.md).

## 7. Test markers

Markers are declared in `pyproject.toml`. pytest runs with `--strict-markers` and sockets blocked.

| Marker | Meaning | Run by |
| --- | --- | --- |
| (none) | Offline unit and integration tests | `uv run poe test` (part of `poe gate`) |
| `offline` | Declared for explicit labeling; no test uses it yet | Same as unmarked |
| `bdd` | The plain-language checkout scenarios | `poe test` or `poe test-temporal`, depending on their other markers |
| `temporal` | Starts the local Temporal test server | `uv run poe test-temporal` (part of `poe gate`) |
| `network` | Needs real network access | `uv run poe test-network` only. Never in `poe gate` |
| `live_model` | Paid model calls | Never by default. Needs `RUN_LIVE_MODEL_EVALS=1` and a key; see [agent-adapter.md](agent-adapter.md) |

To run one Temporal test file, copy the flags from `poe test-temporal`:
`uv run pytest -q -m temporal --force-enable-socket <file>`.

## 8. Pull requests

- One change per PR, from a short-lived branch off `main`. Branch names use a type prefix:
  `feat/`, `fix/`, `docs/`, `test/`, `ci/`, `chore/`.
- Commits and PR titles use the same types with an optional scope, for example
  `fix(security): catch stateless GitHub tokens`. PRs are usually squash-merged, so the PR title
  becomes the commit on `main`.
- Put the failing test, the fix, and any doc update in the same PR. Never commit `.env`, keys,
  `dist/`, coverage files, or generated traces.
- Run `uv run poe gate` (and `pnpm gate` if you touched `web/`) before pushing.

What CI runs: the `Dagger` workflow (`.github/workflows/dagger.yml`, code in `.dagger/`) runs on
every PR and push to `main`. It builds the wheel and source archive while running `pnpm gate` on
Node 24 with Chromium. Then, on Python 3.12 and on 3.13, it runs `uv run poe gate`, installs and
tests the built wheel and source archive (`poe release-candidate`), and measures the release. A
separate `Dagger security audit` workflow audits Python and npm dependencies every Monday.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the short version of these rules and
[SECURITY.md](../SECURITY.md) to report a vulnerability privately.

## Troubleshooting

- **`pnpm` fails with `Cannot find module .../corepack/pnpm/...`.** Your global shim is broken.
  Use `npx --yes pnpm@11.5.0 ...` as shown above.
- **The first Temporal test or `poe demo` is slow.** The first run downloads Temporal's test
  server (tests) or dev server (`poe demo`) once. Later runs start in about a second.

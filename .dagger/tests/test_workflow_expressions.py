"""Event text never reaches dagger-for-github's bash script as a `${{ }}` expression.

dagger-for-github pastes every `with:` input except `module` into a bash script. An
expression there is rendered before bash runs, so any value an event author can shape
becomes shell text. gainratio/ci's fleet rule `dagger-args-expression` rejects it. Values
arrive through `env:` instead, and the args only test or quote the variable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
DAGGER_ACTION = "dagger/dagger-for-github@"
#: Expression roots whose values an event author, dispatcher, or fork controls.
ATTACKER_EXPRESSIONS = ("${{ inputs.", "${{ github.event.", "${{ github.head_ref")
#: The action passes `module` as INPUT_MODULE; every other input is pasted into bash.
ENV_ONLY_INPUTS = frozenset({"module"})
COMMIT_SHA = "a" * 40
RUN_REPOSITORY = "gainratio/agentic-saga"

Step = dict[str, object]


def _steps(name: str) -> list[Step]:
    workflow = cast(dict[str, object], yaml.safe_load((WORKFLOWS / name).read_text()))
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    return [step for job in jobs.values() for step in cast(list[Step], job["steps"])]


def _dagger_step(name: str) -> Step:
    steps = [step for step in _steps(name) if str(step.get("uses", "")).startswith(DAGGER_ACTION)]
    assert len(steps) == 1
    return steps[0]


def _script_inputs() -> list[tuple[str, str, str]]:
    return [
        (path.name, key, str(value))
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for step in _steps(path.name)
        if str(step.get("uses", "")).startswith(DAGGER_ACTION)
        for key, value in cast(Step, step.get("with", {})).items()
        if key not in ENV_ONLY_INPUTS
    ]


def _expand_like_the_action(args: str, cwd: Path) -> list[str]:
    """Render `${{ github.sha }}`/`${{ github.repository }}` as Actions does, then expand."""
    bash = shutil.which("bash")
    assert bash is not None
    rendered = args.replace("${{ github.sha }}", COMMIT_SHA).replace(
        "${{ github.repository }}", RUN_REPOSITORY
    )
    result = subprocess.run(  # noqa: S603 - fixed bash, test-owned argv
        [bash, "-c", f"printf '%s\\0' {rendered}"],
        env={"PATH": "/usr/bin:/bin"},
        cwd=cwd,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().split("\0")[:-1]


def test_should_paste_no_attacker_controlled_expression_into_any_dagger_input() -> None:
    # Given every input dagger-for-github pastes into its bash script.
    inputs = _script_inputs()

    # When each one is searched for an event-controlled expression.
    pasted = [
        (name, key, text)
        for name, key, text in inputs
        if any(expression in text for expression in ATTACKER_EXPRESSIONS)
    ]

    # Then none is found.
    assert inputs
    assert pasted == []


@pytest.mark.parametrize(
    ("name", "verb"), (("dagger.yml", "ci"), ("dagger-security.yml", "security"))
)
def test_should_pass_dagger_only_the_fixed_public_arguments(
    name: str, verb: str, tmp_path: Path
) -> None:
    # Given the real args of the ingress workflow.
    args = str(cast(Step, _dagger_step(name)["with"])["args"])

    # When bash expands them.
    argv = _expand_like_the_action(args, tmp_path)

    # Then Dagger receives the verb, commit, and repository, and no auth argument at all.
    assert argv == [verb, f"--commit-sha={COMMIT_SHA}", f"--repository={RUN_REPOSITORY}"]

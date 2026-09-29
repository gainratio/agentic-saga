"""Keep the decision-engine docs runnable: every documented import must exist.

The default Jev-through-OpenRouter example is executed as written (it builds the
driver and the Temporal Activities, and makes no network call), so a renamed
symbol or changed signature breaks this test instead of silently rotting the docs.
"""

from __future__ import annotations

import ast
import re
import runpy
import textwrap
from importlib import import_module
from pathlib import Path

import pytest

from agentic_saga.agents import ChoiceAgentDriver
from agentic_saga.temporal import TemporalActivities

ROOT = Path(__file__).parents[1]
DOCS = ("README.md", "docs/agent-adapter.md")
_DEFAULT_BUILDER = "build_openrouter_decisions_driver("
_OWNED_PACKAGES = ("agentic_saga", "examples")


def _python_blocks(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    fenced = re.findall(r"^( *)```python\n(.*?)^\1```", text, flags=re.DOTALL | re.MULTILINE)
    return [textwrap.dedent(body) for _, body in fenced]


def _documented_imports() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in DOCS:
        for block in _python_blocks(path):
            found.extend(_owned_imports(path, block))
    return found


def _owned_imports(path: str, block: str) -> list[tuple[str, str, str]]:
    nodes = (node for node in ast.walk(ast.parse(block)) if isinstance(node, ast.ImportFrom))
    owned = (node for node in nodes if (node.module or "").startswith(_OWNED_PACKAGES))
    return [(path, node.module or "", alias.name) for node in owned for alias in node.names]


def _default_example() -> str:
    blocks = _python_blocks("docs/agent-adapter.md")
    return next(block for block in blocks if _DEFAULT_BUILDER in block)


def test_should_document_real_symbols_when_docs_import_from_this_project() -> None:
    # Given
    imports = _documented_imports()
    # When
    missing = [item for item in imports if not hasattr(import_module(item[1]), item[2])]
    # Then
    assert imports
    assert missing == []


def test_should_build_default_jev_driver_when_the_documented_example_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-secret")
    script = tmp_path / "default_jev_example.py"
    script.write_text(_default_example(), encoding="utf-8")
    # When
    namespace = runpy.run_path(str(script), run_name="default_jev_example")
    # Then
    driver = namespace["driver"]
    assert isinstance(driver, ChoiceAgentDriver)
    assert driver.provider_id == "openrouter-decisions"
    assert driver.model_route == ("typesafe/jev-1.13",)
    assert isinstance(namespace["activities"], TemporalActivities)


def test_should_name_jev_via_openrouter_as_default_when_readme_explains_engines() -> None:
    # Given
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    # Then
    assert "build_openrouter_decisions_driver" in readme
    assert "OPENROUTER_API_KEY" in readme
    assert "agentic_saga.decide" in readme
    assert "build_jev_driver" in readme

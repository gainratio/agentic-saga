from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from examples.quickstart.trip import run_trip

ROOT = Path(__file__).parents[3]


@pytest.mark.asyncio
@pytest.mark.temporal
async def test_should_undo_bookings_newest_first_when_trip_confirmation_fails() -> None:
    # Given the quickstart trip saga, whose final confirmation always fails
    # When it runs to completion on the Temporal test server
    result = await run_trip()

    # Then every forward step ran in order, and the undo steps ran newest-first
    assert result.forward == ("book_flight", "book_hotel", "confirm_trip")
    assert result.undo == ("cancel_hotel", "cancel_flight")
    assert result.status == "compensated_verified"


@pytest.mark.temporal
def test_should_print_the_saga_story_when_run_as_a_module() -> None:
    # Given the documented command line
    command = (sys.executable, "-m", "examples.quickstart.trip")

    # When a newcomer runs it from the repository root
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False
    )

    # Then it exits cleanly and prints the forward steps, undo steps, and outcome
    assert completed.returncode == 0, completed.stderr
    assert "Forward steps: book_flight -> book_hotel -> confirm_trip" in completed.stdout
    assert "Undo steps:    cancel_hotel -> cancel_flight" in completed.stdout
    assert "Outcome:       compensated_verified" in completed.stdout

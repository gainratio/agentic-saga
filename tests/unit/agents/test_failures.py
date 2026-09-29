from __future__ import annotations

import pytest

from agentic_saga.agents import pydanticai
from agentic_saga.agents.failures import AgentFailureCategory, classify_http_status


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (429, AgentFailureCategory.RATE_LIMIT_EXHAUSTED),
        (400, AgentFailureCategory.REQUEST_REJECTED),
        (401, AgentFailureCategory.REQUEST_REJECTED),
        (499, AgentFailureCategory.REQUEST_REJECTED),
        (500, AgentFailureCategory.SERVER_ERROR_EXHAUSTED),
        (599, AgentFailureCategory.SERVER_ERROR_EXHAUSTED),
    ],
)
def test_should_classify_error_status_when_provider_rejects(
    status: int, category: AgentFailureCategory
) -> None:
    # When
    result = classify_http_status(status)
    # Then
    assert result is category


@pytest.mark.parametrize("status", [None, 100, 200, 301, 399, 600])
def test_should_not_classify_when_status_is_not_an_http_error(status: int | None) -> None:
    # When
    result = classify_http_status(status)
    # Then
    assert result is None


@pytest.mark.parametrize(("status", "expected"), [(200, True), (299, True), (300, False)])
def test_should_treat_provider_success_range_as_invalid_response_when_given(
    status: int, expected: bool
) -> None:
    # When
    result = classify_http_status(status, invalid_response=range(200, 300))
    # Then
    assert (result is AgentFailureCategory.INVALID_RESPONSE) is expected


def test_should_keep_one_category_type_when_imported_from_pydanticai() -> None:
    # Then
    assert pydanticai.AgentFailureCategory is AgentFailureCategory

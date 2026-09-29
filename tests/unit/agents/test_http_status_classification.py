"""Characterize how every decision adapter classifies provider HTTP failures.

The three adapters read the status from different exception attributes, but must
agree on what 429, 4xx, 5xx, and timeouts mean. These tables pin the behavior so
the shared classifier cannot drift one provider away from the others.
"""

from __future__ import annotations

import httpx
import httpx2
import pytest
from openrouter.errors import OpenRouterDefaultError
from typesafe_sdk import TypeSafeAPITimeoutError
from typesafe_sdk._core.errors import api_error

from agentic_saga.agents import jev, openrouter_decisions, pydanticai
from agentic_saga.agents.pydanticai import AgentFailureCategory

_RATE = AgentFailureCategory.RATE_LIMIT_EXHAUSTED
_REJECTED = AgentFailureCategory.REQUEST_REJECTED
_SERVER = AgentFailureCategory.SERVER_ERROR_EXHAUSTED
_TRANSPORT = AgentFailureCategory.TRANSPORT_EXHAUSTED
_INVALID = AgentFailureCategory.INVALID_RESPONSE
_INTERNAL = AgentFailureCategory.INTERNAL

# Statuses every provider must classify the same way.
_SHARED_STATUSES = (
    (429, _RATE),
    (400, _REJECTED),
    (401, _REJECTED),
    (403, _REJECTED),
    (404, _REJECTED),
    (499, _REJECTED),
    (500, _SERVER),
    (502, _SERVER),
    (503, _SERVER),
    (599, _SERVER),
    (600, _INTERNAL),
    (100, _INTERNAL),
)
# Provider-specific meaning of a non-error status carried on an exception.
_JEV_NON_ERRORS = ((200, _INVALID), (204, _INVALID), (301, _INTERNAL), (399, _INTERNAL))
_OPENROUTER_NON_ERRORS = ((200, _INVALID), (204, _INVALID), (301, _INVALID), (399, _INVALID))
_PYDANTIC_NON_ERRORS = ((200, _INTERNAL), (204, _INTERNAL), (301, _INTERNAL), (399, _INTERNAL))
_TIMEOUTS = (
    TimeoutError("private timeout"),
    ConnectionError("private connection"),
)


class _StatusError(Exception):
    """TypeSafe SDK shape: the status lives on ``status``."""

    def __init__(self, status: object) -> None:
        self.status = status
        super().__init__("private provider body")


class _StatusCodeError(Exception):
    """OpenRouter / Pydantic AI shape: the status lives on ``status_code``."""

    def __init__(self, status_code: object) -> None:
        self.status_code = status_code
        super().__init__("private provider body")


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _ResponseError(Exception):
    """Pydantic AI shape: the status lives on ``response.status_code``."""

    def __init__(self, status_code: int) -> None:
        self.response = _Response(status_code)
        super().__init__("private provider body")


def _openrouter(error: Exception) -> AgentFailureCategory:
    transport = openrouter_decisions._load_dependencies().transport_errors
    return openrouter_decisions._failure_category(error, transport)


@pytest.mark.parametrize(("status", "category"), _SHARED_STATUSES + _JEV_NON_ERRORS)
def test_should_classify_typesafe_status_when_jev_sdk_raises(
    status: int, category: AgentFailureCategory
) -> None:
    # Given
    error = _StatusError(status)
    # When
    result = jev._failure_category(error)
    # Then
    assert result is category


@pytest.mark.parametrize(("status", "category"), _SHARED_STATUSES + _OPENROUTER_NON_ERRORS)
def test_should_classify_openrouter_status_when_decisions_sdk_raises(
    status: int, category: AgentFailureCategory
) -> None:
    # Given
    error = _StatusCodeError(status)
    # When
    result = _openrouter(error)
    # Then
    assert result is category


@pytest.mark.parametrize(("status", "category"), _SHARED_STATUSES + _PYDANTIC_NON_ERRORS)
def test_should_classify_pydantic_status_when_model_call_raises(
    status: int, category: AgentFailureCategory
) -> None:
    # Given
    direct = _StatusCodeError(status)
    nested = _ResponseError(status)
    # When
    results = (pydanticai._failure_category(direct), pydanticai._failure_category(nested))
    # Then
    assert results == (category, category)


@pytest.mark.parametrize(("status", "category"), _SHARED_STATUSES[:-2])
def test_should_classify_real_sdk_errors_identically_when_any_provider_rejects(
    status: int, category: AgentFailureCategory
) -> None:
    # Given
    typesafe = api_error(status, None, httpx2.Headers())
    openrouter = OpenRouterDefaultError("private", httpx.Response(status))
    # When
    results = (
        jev._failure_category(typesafe),
        _openrouter(openrouter),
        pydanticai._failure_category(openrouter),
    )
    # Then
    assert results == (category, category, category)


@pytest.mark.parametrize("error", _TIMEOUTS)
def test_should_classify_builtin_timeouts_as_transport_when_sdk_neutral_provider_raises(
    error: Exception,
) -> None:
    # When
    results = (jev._failure_category(error), pydanticai._failure_category(error))
    # Then
    assert results == (_TRANSPORT, _TRANSPORT)


def test_should_classify_typesafe_timeout_as_transport_when_jev_times_out() -> None:
    # Given
    error = TypeSafeAPITimeoutError(1.0)
    # When
    result = jev._failure_category(error)
    # Then
    assert result is _TRANSPORT


@pytest.mark.parametrize("error", (httpx.ReadTimeout("private"), httpx.ConnectError("private")))
def test_should_classify_httpx_timeouts_as_transport_when_openrouter_raises(
    error: Exception,
) -> None:
    # When
    result = _openrouter(error)
    # Then
    assert result is _TRANSPORT


@pytest.mark.parametrize("status", ("429", 429.0, True, None))
def test_should_ignore_non_integer_status_when_any_provider_raises(status: object) -> None:
    # Given
    errors = (_StatusError(status), _StatusCodeError(status))
    # When
    results = (
        jev._failure_category(errors[0]),
        _openrouter(errors[1]),
        pydanticai._failure_category(errors[1]),
    )
    # Then
    assert results == (_INTERNAL, _INTERNAL, _INTERNAL)

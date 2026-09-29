"""Failure categories and HTTP status classification shared by every decision adapter.

Each adapter still reads the status from its own SDK's exception shape; only the
meaning of the number lives here, so 429, 4xx, and 5xx cannot drift between providers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

_TOO_MANY_REQUESTS: Final = 429
_CLIENT_ERRORS: Final = range(400, 500)
_SERVER_ERRORS: Final = range(500, 600)
_NO_STATUSES: Final = range(0)


class AgentFailureCategory(StrEnum):
    """Closed, secret-free reason for an adapter planning failure."""

    RATE_LIMIT_EXHAUSTED = "rate_limit_exhausted"
    REQUEST_REJECTED = "request_rejected"
    SERVER_ERROR_EXHAUSTED = "server_error_exhausted"
    TRANSPORT_EXHAUSTED = "transport_exhausted"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL = "internal"


def classify_http_status(
    status: int | None, *, invalid_response: range = _NO_STATUSES
) -> AgentFailureCategory | None:
    """Return the category for an HTTP status, or None when it is not an HTTP failure.

    ``invalid_response`` names the non-error statuses a provider SDK only raises for
    when it received a response it could not parse.
    """

    if status is None:
        return None
    if status in invalid_response:
        return AgentFailureCategory.INVALID_RESPONSE
    return _error_category(status)


def _error_category(status: int) -> AgentFailureCategory | None:
    if status == _TOO_MANY_REQUESTS:
        return AgentFailureCategory.RATE_LIMIT_EXHAUSTED
    if status in _CLIENT_ERRORS:
        return AgentFailureCategory.REQUEST_REJECTED
    if status in _SERVER_ERRORS:
        return AgentFailureCategory.SERVER_ERROR_EXHAUSTED
    return None


__all__ = ["AgentFailureCategory", "classify_http_status"]

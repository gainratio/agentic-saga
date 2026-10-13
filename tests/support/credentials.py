"""Synthetic credential builders assembled at runtime so no token literal is committed."""

from __future__ import annotations

import base64
import json

_APP_ID = "1234567"
_SIGNATURE_BYTES = bytes(range(256)) * 2


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def stateless_installation_token() -> str:
    """Return a fake GitHub App token in the stateless ``ghs_<APPID>_<JWT>`` shape.

    The JWT segments are base64url, so the token carries ``.``, ``-`` and ``_``
    and is longer than 500 characters, like the format GitHub rolled out in 2026.
    """
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"app": int(_APP_ID), "sub": "installation|1"}).encode())
    jwt = ".".join((header, payload, _b64url(_SIGNATURE_BYTES)))
    return "".join(("ghs", "_", _APP_ID, "_", jwt))

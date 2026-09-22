# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Small shared utilities."""

import math
from typing import Any

import jwt  # PyJWT


def decode_id_token(id_token: str) -> dict:
    """
    Decode a JWT without verifying its signature (safe here because Auth0
    already validated it).
    """
    if not id_token:
        return None
    payload = jwt.decode(id_token.encode("utf-8"), options={"verify_signature": False}) or {}
    return payload


def finite(value: Any, fallback: float = 0.0) -> float:
    """Coerce to a finite float, falling back on NaN/inf/garbage."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback

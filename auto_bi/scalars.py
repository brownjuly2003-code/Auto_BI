"""Canonical scalar types for DecisionOps contracts (DECISIONOPS_ARCHITECTURE §7.1).

Money, ratios and tolerances travel as decimal strings; time as RFC 3339 UTC; durations as
ISO 8601; content addresses as ``sha256:<hex>``. The reason is digest stability: a binary
float, a local-time stamp or a `0.1 == 0.1000` ambiguity would make the same contract hash
differently on another machine or another serializer, and a hash that depends on the
machine cannot gate a release.

These are plain ``str`` fields with validators, so a contract round-trips through YAML and
JSON unchanged — the canonical form is what was written, not what a decimal library chose
to print.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator

# Plain decimal literal: optional sign, no exponent, no padding, no thousands separators.
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
# ISO 8601 duration; at least one component, no fractional parts (§7.1 keeps them exact).
_DURATION_RE = re.compile(
    r"^P(?!$)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?(?:T(?!$)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?$"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _decimal_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a decimal string, not a binary number")
    if not _DECIMAL_RE.match(value):
        raise ValueError(
            "must be a plain decimal string: optional '-', digits, optional '.', no exponent"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation:  # pragma: no cover - regex already rejects these
        raise ValueError("must be a decimal string") from None
    if value.startswith("-") and parsed == 0:
        raise ValueError("negative zero is not a canonical decimal value")
    return value


def _duration(value: object) -> str:
    if not isinstance(value, str) or not _DURATION_RE.match(value):
        raise ValueError("must be an ISO 8601 duration such as PT6H or P13W")
    return value


def _utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise ValueError("must be an RFC 3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("must be an RFC 3339 UTC timestamp ending in Z") from None
    return value


def _sha256_digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError("must be a content address of the form sha256:<64 lowercase hex>")
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise ValueError("must be a lowercase slug: [a-z0-9] then [a-z0-9._-]")
    return value


DecimalString = Annotated[str, BeforeValidator(_decimal_string)]
Duration = Annotated[str, BeforeValidator(_duration)]
Timestamp = Annotated[str, BeforeValidator(_utc_timestamp)]
Sha256Digest = Annotated[str, BeforeValidator(_sha256_digest)]
Slug = Annotated[str, BeforeValidator(_slug)]

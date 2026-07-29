"""SafeError and central secret redaction (plan_sol step 3 / audit P0-3).

Public responses, durable Store rows, SSE, and trace events must never carry raw
provider bodies, DSN credentials, Authorization headers, cookies, or API keys.
Operators correlate events via ``correlation_id`` to internal logs that keep a
redacted ``internal_detail`` plus optional provider status/class.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

# --- Stable public error codes -------------------------------------------------

CODE_INTERNAL = "internal.error"
CODE_BI_HTTP = "bi.http_error"
CODE_BI_AUTH = "bi.auth_failed"
CODE_BI_HEALTH = "bi.healthcheck_failed"
CODE_BI_CONFIG = "bi.config_error"
CODE_DWH = "dwh.error"
CODE_STORE = "store.error"
CODE_LLM = "llm.error"
CODE_SQL = "sql.validation_failed"
CODE_VALIDATION = "validation.error"

# Generic user-facing copy (no hostnames, SQL, tokens, or provider bodies).
PUBLIC_MESSAGES: dict[str, str] = {
    CODE_INTERNAL: "Internal error",
    CODE_BI_HTTP: "BI request failed",
    CODE_BI_AUTH: "BI authentication failed",
    CODE_BI_HEALTH: "BI service is unavailable",
    CODE_BI_CONFIG: "BI configuration error",
    CODE_DWH: "Data warehouse is unavailable",
    CODE_STORE: "Session store is unavailable",
    CODE_LLM: "LLM request failed",
    CODE_SQL: "SQL validation failed",
    CODE_VALIDATION: "Request validation failed",
}

# --- Redaction -----------------------------------------------------------------

_REDACTED = "***"

# scheme://user:password@host
_URI_CREDS_RE = re.compile(
    r"(?i)((?:https?|postgres(?:ql)?|mysql|clickhouse|mongodb|redis|amqp|ftp|s3)"
    r"://)([^/\s:@\"']+):([^@\s\"']+)@"
)
# Authorization: Bearer/Basic … or bare Bearer/Basic tokens
_AUTH_RE = re.compile(r"(?i)(\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+)([^\s,;\"']+)")
# password=…, api_key: …, access_token=…, etc.
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key|x-api-key|"
    r"password|passwd|pwd|client_secret|private_key|csrf[_-]?token"
    r")\s*([:=]\s*)([^\s,;\"']+)"
)
# Cookie / Set-Cookie header values
_COOKIE_RE = re.compile(r"(?i)(\b(?:set-)?cookie\s*[:=]\s*)([^\n]+)")
# JWT-shaped tokens
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
# Long hex/base64-looking secrets (API keys, session ids) after common prefixes
_LONG_TOKEN_RE = re.compile(
    r"(?i)(\b(?:token|secret|key|session|csrf)\b\s*[:=]\s*)([A-Za-z0-9_\-+/=]{16,})"
)


def new_correlation_id() -> str:
    """Short opaque id safe to show users and store in durable rows."""
    return uuid.uuid4().hex[:16]


def redact_secrets(text: str) -> str:
    """Strip credentials and secret-shaped substrings from free text.

    Safe to call on already-redacted input. Empty/None-like values pass through.
    """
    if not text:
        return text
    out = str(text)
    out = _URI_CREDS_RE.sub(rf"\1{_REDACTED}:{_REDACTED}@", out)
    out = _AUTH_RE.sub(rf"\1{_REDACTED}", out)
    out = _KEY_VALUE_SECRET_RE.sub(rf"\1\2{_REDACTED}", out)
    out = _COOKIE_RE.sub(rf"\1{_REDACTED}", out)
    out = _JWT_RE.sub(_REDACTED, out)
    out = _LONG_TOKEN_RE.sub(rf"\1{_REDACTED}", out)
    return out


class SafeError(Exception):
    """Boundary error with a stable public face and a redacted internal detail.

    ``str(safe_error)`` is the public message only — never the internal detail —
    so accidental ``str(exc)`` on HTTP/SSE paths stays safe.
    """

    def __init__(
        self,
        code: str,
        public_message: str | None = None,
        *,
        retryable: bool = False,
        correlation_id: str | None = None,
        internal_detail: str = "",
        provider_status: int | None = None,
        provider_class: str | None = None,
    ) -> None:
        self.code = code
        self.public_message = public_message or PUBLIC_MESSAGES.get(
            code, PUBLIC_MESSAGES[CODE_INTERNAL]
        )
        self.retryable = retryable
        self.correlation_id = correlation_id or new_correlation_id()
        self.internal_detail = redact_secrets(internal_detail) if internal_detail else ""
        self.provider_status = provider_status
        self.provider_class = provider_class
        super().__init__(self.public_message)

    def for_public(self) -> dict[str, Any]:
        """JSON-serialisable payload for HTTP / SSE clients."""
        return {
            "code": self.code,
            "message": self.public_message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }

    def for_store(self) -> str:
        """Durable Store / trace string: code + public message + correlation id."""
        return f"[{self.code}] {self.public_message} (ref={self.correlation_id})"

    def for_sse(self) -> str:
        """SSE ``error`` event text (public only)."""
        return f"{self.public_message} (ref={self.correlation_id})"

    def log_fields(self) -> dict[str, Any]:
        """Structured fields for operator logs (already redacted)."""
        return {
            "error_code": self.code,
            "correlation_id": self.correlation_id,
            "retryable": self.retryable,
            "provider_status": self.provider_status,
            "provider_class": self.provider_class,
            "internal_detail": self.internal_detail,
            "public_message": self.public_message,
        }


def _bi_code_for_status(status: int | None) -> str:
    if status in (401, 403):
        return CODE_BI_AUTH
    return CODE_BI_HTTP


def to_safe_error(
    exc: BaseException,
    *,
    default_code: str = CODE_INTERNAL,
    public_message: str | None = None,
    retryable: bool = False,
) -> SafeError:
    """Normalize any exception into a SafeError without leaking secrets."""
    if isinstance(exc, SafeError):
        return exc

    # Lazy imports keep errors.py free of adapter/llm import cycles at module load.
    from auto_bi.adapters.datalens.client import DataLensAPIError
    from auto_bi.adapters.superset.client import SupersetAPIError
    from auto_bi.agent.propose import SpecValidationError
    from auto_bi.agent.sql_guard import SQLGuardError
    from auto_bi.llm.base import LLMError

    raw = redact_secrets(str(exc))
    provider_class = type(exc).__name__
    provider_status: int | None = getattr(exc, "status_code", None)
    if not isinstance(provider_status, int):
        provider_status = None

    if isinstance(exc, SupersetAPIError | DataLensAPIError):
        code = _bi_code_for_status(provider_status)
        msg = public_message or PUBLIC_MESSAGES[code]
        if provider_status is not None and code == CODE_BI_HTTP:
            msg = f"{PUBLIC_MESSAGES[CODE_BI_HTTP]} (HTTP {provider_status})"
        return SafeError(
            code,
            msg,
            retryable=retryable or (provider_status is not None and provider_status >= 500),
            internal_detail=raw,
            provider_status=provider_status,
            provider_class=provider_class,
        )

    if isinstance(exc, LLMError):
        return SafeError(
            CODE_LLM,
            public_message or PUBLIC_MESSAGES[CODE_LLM],
            retryable=retryable,
            internal_detail=raw,
            provider_class=provider_class,
        )

    if isinstance(exc, SQLGuardError):
        # SQL guard messages are engineer-facing and must still be redacted (DSN in cause).
        return SafeError(
            CODE_SQL,
            public_message or PUBLIC_MESSAGES[CODE_SQL],
            retryable=False,
            internal_detail=raw,
            provider_class=provider_class,
        )

    if isinstance(exc, SpecValidationError):
        return SafeError(
            CODE_VALIDATION,
            public_message or PUBLIC_MESSAGES[CODE_VALIDATION],
            retryable=False,
            internal_detail=raw,
            provider_class=provider_class,
        )

    # Pipeline healthcheck: RuntimeError("superset healthcheck failed: …")
    lowered = raw.lower()
    if "healthcheck failed" in lowered or ("healthcheck" in lowered and "failed" in lowered):
        return SafeError(
            CODE_BI_HEALTH,
            public_message or PUBLIC_MESSAGES[CODE_BI_HEALTH],
            retryable=True,
            internal_detail=raw,
            provider_class=provider_class,
        )

    code = default_code
    return SafeError(
        code,
        public_message or PUBLIC_MESSAGES.get(code, PUBLIC_MESSAGES[CODE_INTERNAL]),
        retryable=retryable,
        internal_detail=raw,
        provider_status=provider_status,
        provider_class=provider_class,
    )


def public_error_text(exc: BaseException, *, default_code: str = CODE_INTERNAL) -> str:
    """Single-line public text for SSE / legacy string fields."""
    return to_safe_error(exc, default_code=default_code).for_sse()


def store_error_text(exc: BaseException, *, default_code: str = CODE_INTERNAL) -> str:
    """Single-line durable text for Store builds.error / trace.detail."""
    return to_safe_error(exc, default_code=default_code).for_store()


class SecretRedactFilter(logging.Filter):
    """Logging filter: redact secrets in message, args, and exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True

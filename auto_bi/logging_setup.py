"""Structured logging for `auto_bi serve` (O-3).

`--log-level`/`--log-format` (cli.py::_serve) are the only place that should touch the
root logger — no other module calls `logging.basicConfig` or configures handlers.

Plan_sol step 3: every handler gets a SecretRedactFilter so DSN/tokens never land
in structured or text logs even if a caller passes raw exception text.
"""

from __future__ import annotations

import json
import logging
import sys

from auto_bi.errors import SecretRedactFilter, redact_secrets


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — the shape log aggregators (ELK/Loki/CloudWatch) expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        if record.exc_info:
            payload["exc_info"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_format: str = "text") -> None:
    """Reset the root logger to one stdout handler at `level`, formatted as `log_format`
    ('text' for a human reading a local console, 'json' for a prod log aggregator).
    Idempotent — safe to call once at process start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SecretRedactFilter())
    handler.setFormatter(
        _JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Also attach at root so third-party handlers/tests that log without our
    # configure still get redaction when the filter is present on the logger.
    if not any(isinstance(f, SecretRedactFilter) for f in root.filters):
        root.addFilter(SecretRedactFilter())

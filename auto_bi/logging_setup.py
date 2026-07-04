"""Structured logging for `auto_bi serve` (O-3).

`--log-level`/`--log-format` (cli.py::_serve) are the only place that should touch the
root logger — no other module calls `logging.basicConfig` or configures handlers.
"""

from __future__ import annotations

import json
import logging
import sys


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — the shape log aggregators (ELK/Loki/CloudWatch) expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_format: str = "text") -> None:
    """Reset the root logger to one stdout handler at `level`, formatted as `log_format`
    ('text' for a human reading a local console, 'json' for a prod log aggregator).
    Idempotent — safe to call once at process start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

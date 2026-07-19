"""Prometheus text-exposition metrics (audit D-3).

Rendered by hand rather than through `prometheus_client`: the whole surface is a handful
of counters read from the store plus three in-process gauges, and the exposition format is
a few lines of text. A dependency would also bring its own default collectors and a global
registry — neither of which we want in a library that also runs as a CLI.

Two sources, deliberately separated:
- `LiveMetrics` — in-process, resets on restart (Prometheus handles counter resets): build
  slots in use, and the DWH round-trips this process has made. Nothing else knows these.
- `Store.metrics_snapshot()` — durable counters that survive a restart (builds, sessions,
  LLM ledger). Read fresh on every scrape.
"""

from __future__ import annotations

import threading
from typing import Any

from auto_bi.llm.budget import ModelPrices, cost_usd


class LiveMetrics:
    """In-process counters an API server updates as it works. Thread-safe.

    Instantiated per app (not a module global) so tests and a second server in the same
    interpreter never share state.
    """

    def __init__(self, build_slots_total: int = 0) -> None:
        self._lock = threading.Lock()
        self.build_slots_total = build_slots_total
        self._builds_in_flight = 0
        self._dwh_queries = 0
        self._dwh_seconds = 0.0

    def build_started(self) -> None:
        with self._lock:
            self._builds_in_flight += 1

    def build_finished(self) -> None:
        with self._lock:
            # never go negative: a release path that runs twice would otherwise report a
            # nonsense gauge for the rest of the process's life
            self._builds_in_flight = max(0, self._builds_in_flight - 1)

    def dwh_query(self, seconds: float) -> None:
        with self._lock:
            self._dwh_queries += 1
            self._dwh_seconds += seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "builds_in_flight": self._builds_in_flight,
                "build_slots_total": self.build_slots_total,
                "dwh_queries": self._dwh_queries,
                "dwh_seconds": self._dwh_seconds,
            }


def _escape(value: str) -> str:
    """Escape a label VALUE per the exposition format (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, value: float, labels: dict[str, str] | None = None) -> str:
    if labels:
        rendered = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sorted(labels.items()))
        name = f"{name}{{{rendered}}}"
    # integers render without a decimal tail; floats keep enough precision for USD cents
    text = str(int(value)) if float(value).is_integer() else f"{value:.6f}"
    return f"{name} {text}"


def render(snapshot: dict[str, Any], live: dict[str, Any], prices: ModelPrices) -> str:
    """Store snapshot + in-process counters -> exposition text (ends with a newline)."""
    out: list[str] = []

    def metric(name: str, kind: str, help_text: str, samples: list[str]) -> None:
        # a metric with no samples is emitted with its HELP/TYPE anyway: a scraper then sees
        # "known metric, currently zero series" instead of "metric disappeared"
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        out.extend(samples)

    metric(
        "auto_bi_builds_total",
        "counter",
        "Dashboard builds recorded in the store, by final status.",
        [
            _line("auto_bi_builds_total", r["n"], {"status": r["status"]})
            for r in snapshot["builds_by_status"]
        ],
    )
    metric(
        "auto_bi_sessions_total",
        "gauge",
        "Sessions in the store, by current status.",
        [
            _line("auto_bi_sessions_total", r["n"], {"status": r["status"]})
            for r in snapshot["sessions_by_status"]
        ],
    )
    metric(
        "auto_bi_builds_in_flight",
        "gauge",
        "Builds currently running in this process.",
        [_line("auto_bi_builds_in_flight", live["builds_in_flight"])],
    )
    metric(
        "auto_bi_build_slots_total",
        "gauge",
        "Concurrent build slots configured (AUTO_BI_MAX_CONCURRENT_BUILDS).",
        [_line("auto_bi_build_slots_total", live["build_slots_total"])],
    )
    metric(
        "auto_bi_dwh_queries_total",
        "counter",
        "Read-only DWH queries issued by this process.",
        [_line("auto_bi_dwh_queries_total", live["dwh_queries"])],
    )
    metric(
        "auto_bi_dwh_query_seconds_total",
        "counter",
        "Cumulative wall time spent in DWH queries by this process.",
        [_line("auto_bi_dwh_query_seconds_total", live["dwh_seconds"])],
    )
    metric(
        "auto_bi_llm_calls_total",
        "counter",
        "LLM provider round-trips recorded in the store, by status.",
        [
            _line("auto_bi_llm_calls_total", r["n"], {"status": r["status"]})
            for r in snapshot["llm_by_status"]
        ],
    )
    metric(
        "auto_bi_llm_tokens_total",
        "counter",
        "LLM tokens by model and direction; only providers that report usage contribute.",
        [
            _line(
                "auto_bi_llm_tokens_total", r[f"{d}_tokens"], {"model": r["model"], "direction": d}
            )
            for r in snapshot["llm_by_model"]
            for d in ("input", "output")
        ],
    )
    metric(
        "auto_bi_llm_seconds_total",
        "counter",
        "Cumulative LLM latency by model.",
        [
            _line("auto_bi_llm_seconds_total", r["latency_ms"] / 1000.0, {"model": r["model"]})
            for r in snapshot["llm_by_model"]
        ],
    )
    metric(
        "auto_bi_llm_cost_usd_total",
        "counter",
        "LLM spend at the configured price table; an unlisted model prices at 0.",
        [_line("auto_bi_llm_cost_usd_total", cost_usd(snapshot["llm_by_model"], prices))],
    )
    metric(
        "auto_bi_store_rows",
        "gauge",
        "Rows per store table — makes the effect of the retention sweep observable.",
        [
            _line("auto_bi_store_rows", n, {"table": table})
            for table, n in sorted(snapshot["table_rows"].items())
        ],
    )
    return "\n".join(out) + "\n"

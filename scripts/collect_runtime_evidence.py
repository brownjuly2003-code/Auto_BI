"""Collect CI runtime samples without claiming a production SLO.

The latency command measures a child process repeatedly and records descriptive
nearest-rank p50/p95 values. The container-snapshot command records the already
healthy Auto_BI image container's startup time and current cgroup memory usage.
Neither command serializes child argv, environment, stdout, or stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE_CLASS = "ci_sample_not_production_slo"
PERCENTILE_METHOD = "nearest_rank"

_MEMORY_UNITS_TO_MIB = {
    "B": 1 / (1024 * 1024),
    "KiB": 1 / 1024,
    "MiB": 1.0,
    "GiB": 1024.0,
    "TiB": 1024.0 * 1024.0,
}
_MEMORY_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>B|KiB|MiB|GiB|TiB)$")
_PROC_RSS_RE = re.compile(r"^VmRSS:\s+(?P<kib>\d+)\s+kB$", re.MULTILINE)


class EvidenceError(RuntimeError):
    """A safe-to-report runtime-evidence collection failure."""


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile (one-based, rounded up)."""
    if not values:
        raise ValueError("percentile values must not be empty")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be greater than 0 and at most 100")
    ordered = sorted(float(value) for value in values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[max(1, min(rank, len(ordered))) - 1]


def parse_docker_mem_usage_mib(raw: str) -> float:
    """Parse the used-memory side of one Docker ``MemUsage`` line into MiB."""
    if not raw or "\n" in raw or "\r" in raw:
        raise ValueError("Docker memory usage must be exactly one non-empty line")
    used = raw.split("/", maxsplit=1)[0].strip()
    match = _MEMORY_RE.fullmatch(used)
    if match is None:
        raise ValueError("Docker memory usage has an unsupported format")
    return float(match.group("value")) * _MEMORY_UNITS_TO_MIB[match.group("unit")]


def parse_proc_status_rss_mib(raw: str) -> float:
    """Parse Linux ``/proc/<pid>/status`` VmRSS (reported in KiB) into MiB."""
    matches = _PROC_RSS_RE.findall(raw)
    if len(matches) != 1:
        raise ValueError("process status must contain exactly one VmRSS value")
    return int(matches[0]) / 1024


def _safe_metadata() -> dict[str, object]:
    return {
        "evidence_class": EVIDENCE_CLASS,
        "git_sha": os.environ.get("GITHUB_SHA", "local"),
        "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
        "runner_os": os.environ.get("RUNNER_OS", platform.system()),
        "recorded_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _docker_stdout(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except OSError:
        raise EvidenceError("Docker command could not be started") from None
    if result.returncode != 0:
        raise EvidenceError("Docker command failed") from None
    return result.stdout.strip()


def collect_container_snapshot(
    *,
    container: str,
    started_epoch_ms: int,
    max_cold_start_ms: int,
    max_memory_mib: float,
    now_epoch_ms: int | None = None,
) -> dict[str, object]:
    """Collect one healthy named app-container snapshot and enforce soft budgets."""
    if not container:
        raise ValueError("container must not be empty")
    if started_epoch_ms < 0:
        raise ValueError("started_epoch_ms must not be negative")
    if max_cold_start_ms <= 0 or max_memory_mib <= 0:
        raise ValueError("container budgets must be positive")

    health = _docker_stdout(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            container,
        ]
    )
    if health != "healthy":
        raise EvidenceError("Auto_BI image container is not healthy")

    observed_epoch_ms = int(time.time() * 1000) if now_epoch_ms is None else int(now_epoch_ms)
    cold_start_ms = observed_epoch_ms - started_epoch_ms
    if cold_start_ms < 0:
        raise EvidenceError("container start timestamp is in the future")
    if cold_start_ms > max_cold_start_ms:
        raise EvidenceError("Auto_BI image cold-start sample exceeded its soft budget")

    memory_raw = _docker_stdout(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}",
            container,
        ]
    )
    memory_usage_mib = parse_docker_mem_usage_mib(memory_raw)
    process_status = _docker_stdout(
        [
            "docker",
            "exec",
            container,
            "cat",
            "/proc/1/status",
        ]
    )
    process_rss_mib = parse_proc_status_rss_mib(process_status)
    if process_rss_mib > max_memory_mib:
        raise EvidenceError("Auto_BI image memory sample exceeded its soft budget")

    payload = _safe_metadata()
    payload.update(
        {
            "kind": "container_snapshot",
            "container": container,
            "health": health,
            "cold_start_ms": cold_start_ms,
            "memory_usage_mib": round(memory_usage_mib, 3),
            "process_rss_mib": round(process_rss_mib, 3),
            "max_cold_start_ms": max_cold_start_ms,
            "max_memory_mib": max_memory_mib,
            "within_cold_start_budget": True,
            "within_memory_budget": True,
        }
    )
    return payload


def _run_measured_child(argv: list[str], timeout_seconds: float) -> float:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise EvidenceError("measured command timed out") from None
    except OSError:
        raise EvidenceError("measured command could not be started") from None
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.returncode != 0:
        raise EvidenceError("measured command failed") from None
    return max(elapsed_ms, 0.0)


def collect_latency_samples(
    *,
    label: str,
    warmups: int,
    samples: int,
    timeout_seconds: float,
    command: list[str],
) -> dict[str, object]:
    """Run a child command repeatedly and return descriptive latency evidence."""
    if not label:
        raise ValueError("label must not be empty")
    if warmups < 0:
        raise ValueError("warmups must not be negative")
    if samples <= 0:
        raise ValueError("samples must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not command:
        raise ValueError("measured command must not be empty")

    for _ in range(warmups):
        _run_measured_child(command, timeout_seconds)

    samples_ms = [round(_run_measured_child(command, timeout_seconds), 3) for _ in range(samples)]
    payload = _safe_metadata()
    payload.update(
        {
            "kind": "latency",
            "label": label,
            "warmups": warmups,
            "sample_count": samples,
            "percentile_method": PERCENTILE_METHOD,
            "samples_ms": samples_ms,
            "p50_ms": nearest_rank_percentile(samples_ms, 50),
            "p95_ms": nearest_rank_percentile(samples_ms, 95),
        }
    )
    return payload


def _write_payload(output: Path, payload: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    latency = subparsers.add_parser("latency", help="Collect child-process latency samples")
    latency.add_argument("--label", required=True)
    latency.add_argument("--warmups", type=_nonnegative_int, default=0)
    latency.add_argument("--samples", type=_positive_int, required=True)
    latency.add_argument("--timeout-seconds", type=_positive_float, required=True)
    latency.add_argument("--output", type=Path, required=True)
    latency.add_argument("command", nargs=argparse.REMAINDER)

    container = subparsers.add_parser(
        "container-snapshot",
        help="Collect one healthy Auto_BI image container snapshot",
    )
    container.add_argument("--container", required=True)
    container.add_argument("--started-epoch-ms", type=_positive_int, required=True)
    container.add_argument("--max-cold-start-ms", type=_positive_int, required=True)
    container.add_argument("--max-memory-mib", type=_positive_float, required=True)
    container.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "latency":
            command = list(args.command)
            if command and command[0] == "--":
                command = command[1:]
            payload = collect_latency_samples(
                label=args.label,
                warmups=args.warmups,
                samples=args.samples,
                timeout_seconds=args.timeout_seconds,
                command=command,
            )
            output = args.output
        else:
            payload = collect_container_snapshot(
                container=args.container,
                started_epoch_ms=args.started_epoch_ms,
                max_cold_start_ms=args.max_cold_start_ms,
                max_memory_mib=args.max_memory_mib,
            )
            output = args.output
        _write_payload(output, payload)
    except (EvidenceError, ValueError, OSError) as exc:
        print(f"runtime evidence failed: {exc}", file=sys.stderr)
        return 1
    print(f"runtime evidence written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

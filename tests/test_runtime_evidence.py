"""Tests for scripts/collect_runtime_evidence.py (future public helpers + CLI)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "collect_runtime_evidence.py"


def _load_module() -> ModuleType:
    assert SCRIPT.is_file(), f"runtime evidence script missing: {SCRIPT}"
    spec = importlib.util.spec_from_file_location(
        "collect_runtime_evidence",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load_module()


# ---------------------------------------------------------------------------
# nearest_rank_percentile
# ---------------------------------------------------------------------------


def test_nearest_rank_percentile_sorted_range(mod: ModuleType) -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert mod.nearest_rank_percentile(values, 50) == 4.0
    assert mod.nearest_rank_percentile(values, 95) == 7.0


def test_nearest_rank_percentile_unsorted(mod: ModuleType) -> None:
    values = [7.0, 1.0, 5.0, 3.0, 2.0, 6.0, 4.0]
    assert mod.nearest_rank_percentile(values, 50) == 4.0
    assert mod.nearest_rank_percentile(values, 95) == 7.0


def test_nearest_rank_percentile_singleton(mod: ModuleType) -> None:
    assert mod.nearest_rank_percentile([42.0], 50) == 42.0
    assert mod.nearest_rank_percentile([42.0], 95) == 42.0


def test_nearest_rank_percentile_empty_raises(mod: ModuleType) -> None:
    with pytest.raises(ValueError):
        mod.nearest_rank_percentile([], 50)


# ---------------------------------------------------------------------------
# parse_docker_mem_usage_mib
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("45.5MiB / 7.5GiB", 45.5),
        ("1.5GiB / 7.5GiB", 1.5 * 1024),
        ("512KiB / 1GiB", 512 / 1024),
        ("100B / 1GiB", 100 / (1024 * 1024)),
    ],
)
def test_parse_docker_mem_usage_mib_units(mod: ModuleType, raw: str, expected: float) -> None:
    assert mod.parse_docker_mem_usage_mib(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "garbage",
        "45.5MiB / 1GiB\nextra",
        "not-a-size",
    ],
)
def test_parse_docker_mem_usage_mib_rejects(mod: ModuleType, raw: str) -> None:
    with pytest.raises(ValueError):
        mod.parse_docker_mem_usage_mib(raw)


def test_parse_proc_status_rss_mib(mod: ModuleType) -> None:
    status = "Name:\tauto_bi\nVmSize:\t100000 kB\nVmRSS:\t40960 kB\nThreads:\t4\n"
    assert mod.parse_proc_status_rss_mib(status) == pytest.approx(40.0)
    with pytest.raises(ValueError):
        mod.parse_proc_status_rss_mib("Name:\tauto_bi\n")


# ---------------------------------------------------------------------------
# latency CLI
# ---------------------------------------------------------------------------


ENV_CANARY = "AUTO_BI_RUNTIME_EVIDENCE_TEST_CANARY"
ENV_VALUE_CANARY = "env-value-canary-unit-test-xyz"
ARGV_CANARY = "argv-canary-unit-test-xyz"
STDOUT_CANARY = "stdout-canary-unit-test-xyz"


def _child_code() -> str:
    # Child validates env + argv canaries, then prints a stdout canary.
    return (
        "import os,sys;"
        f"assert os.environ.get({ENV_CANARY!r})=={ENV_VALUE_CANARY!r};"
        f"assert {ARGV_CANARY!r} in sys.argv;"
        f"print({STDOUT_CANARY!r})"
    )


def test_latency_cli_success(tmp_path: Path) -> None:
    out = tmp_path / "latency.json"
    env = os.environ.copy()
    env[ENV_CANARY] = ENV_VALUE_CANARY

    cmd = [
        sys.executable,
        str(SCRIPT),
        "latency",
        "--label",
        "unit",
        "--warmups",
        "0",
        "--samples",
        "3",
        "--timeout-seconds",
        "5",
        "--output",
        str(out),
        "--",
        sys.executable,
        "-c",
        _child_code(),
        ARGV_CANARY,
    ]
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert out.is_file()

    payload = json.loads(out.read_text(encoding="utf-8"))
    raw_text = out.read_text(encoding="utf-8")

    assert payload["evidence_class"] == "ci_sample_not_production_slo"
    assert payload["kind"] == "latency"
    assert payload["label"] == "unit"
    assert payload["warmups"] == 0
    assert payload["sample_count"] == 3
    assert payload["percentile_method"] == "nearest_rank"

    samples_ms = payload["samples_ms"]
    assert isinstance(samples_ms, list)
    assert len(samples_ms) == 3
    assert all(isinstance(s, int | float) and s >= 0 for s in samples_ms)

    p50_ms = payload["p50_ms"]
    p95_ms = payload["p95_ms"]
    assert p50_ms == pytest.approx(
        _load_module().nearest_rank_percentile([float(s) for s in samples_ms], 50)
    )
    assert p95_ms == pytest.approx(
        _load_module().nearest_rank_percentile([float(s) for s in samples_ms], 95)
    )
    assert p50_ms <= p95_ms

    # Serialized JSON must not leak command/argv/env/stdout/stderr canaries or keys.
    for forbidden_key in ("command", "argv", "env", "stdout", "stderr"):
        assert forbidden_key not in payload
    assert ENV_CANARY not in raw_text
    assert ENV_VALUE_CANARY not in raw_text
    assert ARGV_CANARY not in raw_text
    assert STDOUT_CANARY not in raw_text


def test_latency_cli_child_exit_nonzero(tmp_path: Path) -> None:
    out = tmp_path / "latency_fail.json"
    env = os.environ.copy()
    env[ENV_CANARY] = ENV_VALUE_CANARY

    cmd = [
        sys.executable,
        str(SCRIPT),
        "latency",
        "--label",
        "unit",
        "--warmups",
        "0",
        "--samples",
        "3",
        "--timeout-seconds",
        "5",
        "--output",
        str(out),
        "--",
        sys.executable,
        "-c",
        "import sys; sys.exit(7)",
    ]
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not out.exists()


def test_latency_cli_timeout(tmp_path: Path) -> None:
    out = tmp_path / "latency_timeout.json"
    env = os.environ.copy()
    env[ENV_CANARY] = ENV_VALUE_CANARY

    cmd = [
        sys.executable,
        str(SCRIPT),
        "latency",
        "--label",
        "unit",
        "--warmups",
        "0",
        "--samples",
        "3",
        "--timeout-seconds",
        "0.05",
        "--output",
        str(out),
        "--",
        sys.executable,
        "-c",
        "import time; time.sleep(10)",
    ]
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not out.exists()


# ---------------------------------------------------------------------------
# collect_container_snapshot (mocked subprocess)
# ---------------------------------------------------------------------------


class _Completed:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_collect_container_snapshot_success(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        shell: bool = False,
        **kwargs: Any,
    ) -> _Completed:
        calls.append(
            {
                "args": list(args),
                "shell": shell,
                "capture_output": capture_output,
                "text": text,
                "check": check,
            }
        )
        joined = " ".join(args)
        if "inspect" in args:
            return _Completed(stdout="healthy\n")
        if "stats" in args:
            return _Completed(stdout="45.5MiB / 7.5GiB\n")
        if "exec" in args:
            return _Completed(stdout="Name:\tauto_bi\nVmRSS:\t40960 kB\n")
        raise AssertionError(f"unexpected docker call: {joined}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    payload = mod.collect_container_snapshot(
        container="smoke",
        started_epoch_ms=100_000,
        max_cold_start_ms=90_000,
        max_memory_mib=1024.0,
        now_epoch_ms=101_000,
    )

    assert payload["evidence_class"] == "ci_sample_not_production_slo"
    assert payload["kind"] == "container_snapshot"
    assert payload["container"] == "smoke"
    assert payload["health"] == "healthy"
    assert payload["cold_start_ms"] == 1000
    assert payload["memory_usage_mib"] == pytest.approx(45.5)
    assert payload["process_rss_mib"] == pytest.approx(40.0)
    assert payload["within_cold_start_budget"] is True
    assert payload["within_memory_budget"] is True

    assert len(calls) == 3
    for call in calls:
        assert call["shell"] is False
        assert isinstance(call["args"], list)
        assert all(isinstance(a, str) for a in call["args"])

    assert calls[0]["args"] == [
        "docker",
        "inspect",
        "--format",
        "{{.State.Health.Status}}",
        "smoke",
    ]
    assert calls[1]["args"] == [
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{.MemUsage}}",
        "smoke",
    ]
    assert calls[2]["args"] == [
        "docker",
        "exec",
        "smoke",
        "cat",
        "/proc/1/status",
    ]


def test_collect_container_snapshot_unhealthy_aborts_before_stats(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        args: list[str],
        **kwargs: Any,
    ) -> _Completed:
        calls.append(list(args))
        if "inspect" in args:
            return _Completed(stdout="unhealthy\n")
        if "stats" in args:
            raise AssertionError("stats must not be called when unhealthy")
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        mod.collect_container_snapshot(
            container="smoke",
            started_epoch_ms=100_000,
            max_cold_start_ms=90_000,
            max_memory_mib=1024.0,
            now_epoch_ms=101_000,
        )

    assert any("inspect" in c for c in calls)
    assert not any("stats" in c for c in calls)


def test_collect_container_snapshot_over_cold_budget(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        if "inspect" in args:
            return _Completed(stdout="healthy\n")
        if "stats" in args:
            return _Completed(stdout="45.5MiB / 7.5GiB\n")
        if "exec" in args:
            return _Completed(stdout="Name:\tauto_bi\nVmRSS:\t20480 kB\n")
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        mod.collect_container_snapshot(
            container="smoke",
            started_epoch_ms=100_000,
            max_cold_start_ms=500,  # cold_start = 1000 > 500
            max_memory_mib=1024.0,
            now_epoch_ms=101_000,
        )


def test_collect_container_snapshot_over_memory_budget(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        if "inspect" in args:
            return _Completed(stdout="healthy\n")
        if "stats" in args:
            return _Completed(stdout="45.5MiB / 7.5GiB\n")
        if "exec" in args:
            return _Completed(stdout="Name:\tauto_bi\nVmRSS:\t20480 kB\n")
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        mod.collect_container_snapshot(
            container="smoke",
            started_epoch_ms=100_000,
            max_cold_start_ms=90_000,
            max_memory_mib=10.0,  # process RSS = 20 > 10
            now_epoch_ms=101_000,
        )

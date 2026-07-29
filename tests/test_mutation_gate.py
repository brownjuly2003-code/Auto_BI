"""plan_sol step 12: bounded mutation gate configuration and result policy."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_stats_check(tmp_path: Path, stats: dict[str, int]) -> subprocess.CompletedProcess[str]:
    stats_path = tmp_path / "mutmut-cicd-stats.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_mutation_stats.py"),
            str(stats_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _clean_stats() -> dict[str, int]:
    return {
        "killed": 11,
        "survived": 0,
        "total": 12,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 1,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }


def test_mutmut_scope_is_pinned_and_bounded() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mutmut = config["tool"]["mutmut"]
    assert mutmut["source_paths"] == ["auto_bi/"]
    assert mutmut["only_mutate"] == ["auto_bi/agent/sql_guard.py"]
    assert mutmut["pytest_add_cli_args_test_selection"] == [
        "tests/test_property_quality.py",
        "tests/test_x5_raw_sql.py",
        "tests/test_query_plan.py",
    ]
    assert mutmut["also_copy"] == ["semantic/"]


def test_slo_mutation_smoke_test_selection_matches_mutmut_config() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = config["tool"]["mutmut"]["pytest_add_cli_args_test_selection"]

    slo = (ROOT / "docs" / "operations" / "SLO.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## Mutation smoke\n(.*?)(?=^## |\Z)",
        slo,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    documented = re.findall(r"`(tests/test_[^`]+\.py)`", match.group(1))
    assert documented == expected


def test_slo_mutation_smoke_documents_timeout_as_effective_kill() -> None:
    slo = (ROOT / "docs" / "operations" / "SLO.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## Mutation smoke\n(.*?)(?=^## |\Z)",
        slo,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    section = match.group(1)

    reject_match = re.search(
        r"check_mutation_stats\.py`\s+rejects\s+(.+?)\s+mutants\.",
        section,
        flags=re.DOTALL,
    )
    assert reject_match is not None
    reject_clause = reject_match.group(1)
    assert re.search(r"\binterrupted\b", reject_clause, flags=re.IGNORECASE)
    assert not re.search(r"\btime(?:d)?[-\s]?outs?\b", reject_clause, flags=re.IGNORECASE)

    assert re.search(
        r"\btime(?:d)?[-\s]?outs?\s+mutants?\s+count(?:s)?\s+toward\s+" r"effective\s+kills?",
        section,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"`killed\s*\+\s*timeout`\s+must\s+equal\s+`total`",
        section,
        flags=re.IGNORECASE,
    )


def test_primary_quality_job_runs_pinned_mutation_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Mutation smoke (SQL guard)" in workflow
    assert "uv run --with mutmut==3.6.0 mutmut run" in workflow
    assert "uv run --with mutmut==3.6.0 mutmut export-cicd-stats" in workflow
    assert "scripts/check_mutation_stats.py mutants/mutmut-cicd-stats.json" in workflow


def test_mutation_stats_accept_killed_and_timeout_mutants(tmp_path: Path) -> None:
    result = _run_stats_check(tmp_path, _clean_stats())
    assert result.returncode == 0, result.stderr
    assert "12/12 effective kills" in result.stdout


@pytest.mark.parametrize(
    "field",
    [
        "survived",
        "no_tests",
        "skipped",
        "suspicious",
        "check_was_interrupted_by_user",
        "segfault",
    ],
)
def test_mutation_stats_reject_incomplete_or_surviving_results(tmp_path: Path, field: str) -> None:
    stats = _clean_stats()
    stats["killed"] -= 1
    stats[field] = 1
    result = _run_stats_check(tmp_path, stats)
    assert result.returncode == 1
    assert f"{field}=1" in result.stderr


def test_mutation_stats_reject_empty_run(tmp_path: Path) -> None:
    stats = _clean_stats()
    stats["killed"] = 0
    stats["timeout"] = 0
    stats["total"] = 0
    result = _run_stats_check(tmp_path, stats)
    assert result.returncode == 1
    assert "total=0" in result.stderr

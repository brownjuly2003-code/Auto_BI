"""Offline investigation application/CLI acceptance contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import auto_bi.cli as cli
from auto_bi.investigation.claims import InvestigationBrief
from auto_bi.verification.model import EXIT_ERROR

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_FIXTURE = FIXTURES / "weekly_margin.yaml"
RESULT_FIXTURE = FIXTURES / "revenue_results.yaml"
ARTIFACT_NAMES = {"brief.json", "brief.md", "plan.json", "results.json"}


def invoke(output: Path, plan: Path = PLAN_FIXTURE, *results: Path) -> int:
    result_paths = results or (RESULT_FIXTURE,)
    return cli.main(
        [
            "decision",
            "investigate",
            str(plan),
            "--results",
            *(str(path) for path in result_paths),
            "--output",
            str(output),
        ]
    )


def write_plan(tmp_path: Path, mutate: Any) -> Path:
    raw = yaml.safe_load(PLAN_FIXTURE.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_fixture_run_writes_only_reproducible_offline_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote_mutations: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> None:
        remote_mutations.append("called")
        raise AssertionError("offline investigation entered a remote mutation path")

    for name in ("_build", "_build_raw", "_build_auto", "_prune", "_environment_probe"):
        monkeypatch.setattr(cli, name, forbidden)

    first = tmp_path / "first"
    second = tmp_path / "second"
    assert invoke(first) == 0
    assert invoke(second) == 0

    assert {path.name for path in first.iterdir()} == ARTIFACT_NAMES
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    assert remote_mutations == []

    plan = json.loads((first / "plan.json").read_bytes())
    results = json.loads((first / "results.json").read_bytes())
    brief = json.loads((first / "brief.json").read_bytes())
    markdown = (first / "brief.md").read_text(encoding="utf-8")
    assert plan["kind"] == "InvestigationPlan"
    assert results["kind"] == "InvestigationResults"
    assert len(results["canonical_results"]) == 1
    assert len(results["outputs"]) == 4
    assert brief["kind"] == "InvestigationBrief"
    assert len(brief["claims"]) == 7
    assert "Trust: `exploratory`" in markdown
    assert "Human acceptance: `not measured`" in markdown
    assert capsys.readouterr().err == ""


def test_missing_metric_fails_closed_without_creating_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replacement = "metric://sales/profit@1"

    def repoint(raw: dict[str, Any]) -> None:
        raw["scope"]["metrics"] = [replacement]
        for step in raw["steps"]:
            step["metrics"] = [replacement]

    output = tmp_path / "out"
    assert invoke(output, write_plan(tmp_path, repoint)) == EXIT_ERROR
    assert not output.exists()
    assert "missing canonical result" in capsys.readouterr().err.lower()


def test_distinct_metric_paths_publish_distinct_reproducible_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    metrics = ["metric://sales/revenue@5", "metric://sales-revenue@5"]

    def two_metrics(raw: dict[str, Any]) -> None:
        raw["scope"]["metrics"] = metrics
        raw["steps"] = [{**raw["steps"][0], "metrics": metrics}]

    plan_path = write_plan(tmp_path, two_metrics)
    second_result = yaml.safe_load(RESULT_FIXTURE.read_text(encoding="utf-8"))
    second_result["metric_ref"] = metrics[1]
    second_result["result_id"] = "sales-revenue-flat"
    result_path = tmp_path / "second-result.yaml"
    result_path.write_text(yaml.safe_dump(second_result, sort_keys=False), encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert invoke(first, plan_path, RESULT_FIXTURE, result_path) == 0
    assert invoke(second, plan_path, result_path, RESULT_FIXTURE) == 0
    assert {path.name for path in first.iterdir()} == ARTIFACT_NAMES
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    brief = InvestigationBrief.model_validate_json((first / "brief.json").read_bytes())
    assert [item.output.metric_ref for item in brief.evidence] == metrics
    assert len({item.artifact_id for item in brief.evidence}) == 2
    assert capsys.readouterr().err == ""


def test_duplicate_metric_result_fails_closed_without_creating_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out"
    assert invoke(output, PLAN_FIXTURE, RESULT_FIXTURE, RESULT_FIXTURE) == EXIT_ERROR
    assert not output.exists()
    assert "duplicate canonical result" in capsys.readouterr().err.lower()


def test_budget_stop_writes_no_partial_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def constrain(raw: dict[str, Any]) -> None:
        raw["budget"]["max_input_rows"] = 1

    output = tmp_path / "out"
    assert invoke(output, write_plan(tmp_path, constrain)) == EXIT_ERROR
    assert not output.exists()
    assert "budget.input_rows_exceeded" in capsys.readouterr().err

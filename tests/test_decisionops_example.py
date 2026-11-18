"""Execute the example's documented commands and outcomes, including after a demo edit."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.cli import main
from auto_bi.github_action import exit_for

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples/decisionops-gate"


def documented_command(text: str, name: str) -> list[str]:
    match = re.search(rf"<!-- e2:{name} -->\s*```sh\n(.*?)\n```", text, re.DOTALL)
    assert match, f"missing executable example: {name}"
    argv = shlex.split(match.group(1).replace("\\\n", " "))
    assert argv[:3] == ["uv", "run", "auto_bi"]
    return argv[3:]


def documented_outcome(text: str, name: str) -> tuple[str, int, list[int], str]:
    match = re.search(
        rf"^\| `{name}` \| `(\w+)` \| (\d+) \| ([\d-]+) \| ([\d-]+) "
        rf"\| ([\d-]+) \| ([\d-]+) \| `(\w+|-)` \|$",
        text,
        re.MULTILINE,
    )
    assert match, f"missing documented outcome: {name}"
    verdict, code, *cells = match.groups()
    return verdict, int(code), [int(n) for n in cells[:-1] if n != "-"], cells[-1]


def run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out)


def assert_outcome(text: str, name: str, code: int, report: dict[str, Any]) -> None:
    verdict, expected_code, counts, step = documented_outcome(text, name)
    assert (report["verdict"], code) == (verdict, expected_code)
    if counts:
        statuses = Counter(check["status"] for check in report["checks"])
        assert [statuses[s] for s in ("block", "review", "advisory")] + [
            len(report["checks"])
        ] == counts
    if step != "-":
        assert ("success" if exit_for(code, "block") == 0 else "failure") == step


@pytest.fixture
def example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root = tmp_path / "examples/decisionops-gate"
    shutil.copytree(EXAMPLE, root)
    monkeypatch.chdir(tmp_path)
    return root, (root / "README.md").read_text(encoding="utf-8")


def test_example_contracts_validate_against_its_semantics(
    example: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    root, text = example
    code, report = run_json(documented_command(text, "validate"), capsys)
    assert_outcome(text, "validate", code, report)
    # The four provenance files are also a complete, valid contract set.
    code, report = run_json(["decision", "validate", str(root / "contracts"), "--json"], capsys)
    assert_outcome(text, "validate", code, report)


@pytest.mark.parametrize("starting_timezone", [None, "UTC"])
def test_example_scenarios_survive_the_demo_semantic_edit(
    example: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    starting_timezone: str | None,
) -> None:
    root, text = example
    metric = root / "semantic/metrics/gross_margin_pct.yaml"
    data = yaml.safe_load(metric.read_text(encoding="utf-8"))
    if starting_timezone is not None:
        data["time"]["timezone"] = starting_timezone
        metric.write_bytes(yaml.safe_dump(data, allow_unicode=True).encode("utf-8"))
    argv = documented_command(text, "unchanged")
    evidence_index = argv.index("--evidence") + 1
    argv[evidence_index] = str(tmp_path / "unchanged-evidence")
    code, unchanged = run_json(argv, capsys)
    assert_outcome(text, "unchanged", code, unchanged)

    candidate = tmp_path / "comment-candidate"
    shutil.copytree(root / "semantic", candidate)
    model = candidate / "model.yaml"
    model.write_bytes(model.read_bytes() + b"\n# Comment-only demonstration commit.\n")
    argv[argv.index("--candidate") + 1] = str(candidate)
    argv[evidence_index] = str(tmp_path / "comment-evidence")
    code, commented = run_json(argv, capsys)
    assert_outcome(text, "yaml-comment", code, commented)
    assert commented["candidate_digest"] == unchanged["candidate_digest"]
    assert any(c["summary_code"] == "semantic.unchanged" for c in commented["checks"])

    changed_metric = candidate / "metrics/gross_margin_pct.yaml"
    data["time"]["timezone"] = "UTC" if data["time"]["timezone"] != "UTC" else "Europe/Moscow"
    changed_metric.write_bytes(yaml.safe_dump(data, allow_unicode=True).encode("utf-8"))
    argv[evidence_index] = str(tmp_path / "changed-evidence")
    code, changed = run_json(argv, capsys)
    assert_outcome(text, "timezone-change", code, changed)
    assert changed["candidate_digest"] != unchanged["candidate_digest"]
    code, impact = run_json(
        ["decision", "evidence", argv[evidence_index], "--artifact", "check-impact.products"],
        capsys,
    )
    assert code == 0
    # The snapshot's margin tile is a ratio SQL metric the inventory does not type: the change
    # names the dashboard, and the two SQL tiles are named as read in part, not as computing it.
    assert [i["dashboards"] for i in impact["impacts"]] == [["dashboard://weekly_margin@7"]]
    assert [i["charts"] for i in impact["impacts"]] == [[]]
    assert {(g["code"], g["subject"]) for g in impact["chart_gaps"]} == {
        (code, f"dashboard://weekly_margin@7#{chart}")
        for code in ("chart.metric_unread", "chart.sql_partial")
        for chart in ("chart_2", "chart_3")
    }


def test_readme_quick_run_executes_every_documented_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO)
    text = (REPO / "README.md").read_text(encoding="utf-8").split("## Auto_BI v0.5.0")[0]
    names = re.findall(r"<!-- e2:([\w-]+) -->", text)
    assert names == ["validate", "unchanged", "changed", "evidence"]
    reports = {}
    for name in names:
        argv = documented_command(text, name)
        # Only the destination changes; all documented input paths and flags execute as written.
        argv = [
            str(tmp_path / Path(arg).name) if arg.startswith(".evidence/e2-pilot-") else arg
            for arg in argv
        ]
        code, report = run_json(argv, capsys)
        assert_outcome(text, name, code, report)
        reports[name] = report
    assert reports["evidence"]["manifest_digest"] == reports["changed"]["evidence_digest"]
    assert reports["evidence"]["problems"] == []

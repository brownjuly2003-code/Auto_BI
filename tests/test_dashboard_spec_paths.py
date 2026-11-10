"""Where `decision verify` reads the spec of a delivered dashboard, through the real gate.

Durable spec: docs/specs/dashboard-spec-paths.md. The file was named by the last segment of a
ref and its pin: `dashboard://sales/weekly_margin@7` and `dashboard://ops/weekly_margin@7` read
one `weekly_margin@7.yaml`, so the layout of another dashboard was compiled, sealed and read for
chart impact, and a dashboard nobody supplied a spec for passed as compiled. Every segment of a
ref but the last is now a directory of the path, and the last names the file with the pin.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.manifest import read_bundle, verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.ir.spec import DashboardSpec
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateReport,
)
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    dashboard_spec,
    product_contract,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_unreadable_run_inputs import ROWS, _deny, _deny_look, _refused

SALES = "dashboard://sales/weekly_margin@7"
OPS = "dashboard://ops/weekly_margin@7"
DEEP = "dashboard://sales/emea/weekly_margin@7"


def _case(tmp_path: Path, *refs: str) -> Case:
    """A run that passes when every spec it delivers compiles, delivering *refs*.

    The dashboards directory keeps the fixture's `weekly_margin@7.yaml`: the layout of
    `dashboard://weekly_margin@7`, which none of these refs names, and the file the last
    segment of each of them named.
    """
    case = Case(tmp_path).with_asset().with_results(ROWS)
    product = product_contract()
    product["delivery"]["dashboards"] = [{"ref": ref} for ref in refs]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    return case


def _write_spec(path: Path, spec: dict[str, Any]) -> str:
    """Write *spec* to *path* in the form its suffix names; the digest a compile gives it."""
    if path.suffix == ".json":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    else:
        _write_yaml(path, spec)
    return sha256_digest(
        DashboardSpec.model_validate(spec).model_dump(mode="json"), allow_float=True
    )


def _titled(title: str) -> dict[str, Any]:
    spec = dashboard_spec()
    spec["title"] = title
    return spec


def _margin_by_category() -> dict[str, Any]:
    """A layout that reads no column `_region_retyped` changes."""
    return {
        "title": "Маржа по категориям",
        "target_bi": "superset",
        "charts": [
            {
                "id": "margin_by_category",
                "title": "Маржа по категориям",
                "viz": "bar",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["category"],
                    "measures": [{"column": "gross_margin", "agg": "sum"}],
                },
            }
        ],
    }


def _region_retyped(case: Case) -> Case:
    model = semantic_model()
    next(column for column in model.tables[0].columns if column.name == "region").type = "UInt32"
    return case.with_candidate(model=model)


def _compiled(tmp_path: Path, report: GateReport) -> dict[str, str]:
    """The spec digest `bi.contract` compiled for each dashboard."""
    return _digests(_check_payload(tmp_path, report, "bi.contract"))


def _digests(payload: dict[str, Any]) -> dict[str, str]:
    return {item["dashboard"]: item["spec_digest"] for item in payload["compiled"]}


def _missing(tmp_path: Path, report: GateReport) -> list[str]:
    missing: list[str] = _check_payload(tmp_path, report, "bi.contract")["missing"]
    return missing


def _bi_payloads(tmp_path: Path, report: GateReport) -> list[str]:
    store = FilesystemArtifactStore(tmp_path / "evidence")
    bundle = read_bundle(store, report.run_id)
    return sorted(a.artifact_id for a in bundle.artifacts if a.artifact_id.startswith("bi-"))


def _tiles(tmp_path: Path, report: GateReport) -> list[str]:
    """Every tile a change of the run reaches, after checking that the region retype is one."""
    impact = _check_payload(tmp_path, report, "impact.products")
    assert "field.type_changed" in {item["code"] for item in impact["impacts"]}
    return [chart["ref"] for item in impact["impacts"] for chart in item["charts"]]


# --- the defect: the file of the last segment ---------------------------------------------


def test_a_dashboard_of_two_segments_does_not_read_the_file_of_its_last_segment(
    tmp_path: Path,
) -> None:
    report = _case(tmp_path, OPS).run()

    assert summary_of(report, "bi.contract") == "bi.spec_missing"
    assert status_of(report, "bi.contract") is CheckStatus.REVIEW
    assert _compiled(tmp_path, report) == {}
    assert _missing(tmp_path, report) == [OPS]
    assert _bi_payloads(tmp_path, report) == []
    asking = [
        c.check_id for c in report.checks if c.status in {CheckStatus.REVIEW, CheckStatus.BLOCK}
    ]
    assert asking == ["bi.contract"]
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_the_file_of_its_last_segment_does_not_drive_its_chart_impact(tmp_path: Path) -> None:
    report = _region_retyped(_case(tmp_path, OPS)).run()

    assert _tiles(tmp_path, report) == []
    gaps = _check_payload(tmp_path, report, "impact.products")["chart_gaps"]
    assert [(gap["code"], gap["subject"]) for gap in gaps] == [
        ("chart.dashboard_spec_missing", OPS)
    ]


def test_two_dashboards_of_one_last_segment_read_their_own_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case(tmp_path, SALES, OPS)
    sales = _write_spec(case.dashboards / "sales" / "weekly_margin@7.yaml", dashboard_spec())

    assert main(_verify_argv(case)) == 3

    out = capsys.readouterr().out
    assert "review   bi.contract" in out
    assert "verdict: needs_review" in out
    (run,) = (tmp_path / "evidence").iterdir()
    assert verify_bundle(FilesystemArtifactStore(tmp_path / "evidence"), run.name) == []
    payload = json.loads((run / "checks" / "check-bi.contract.json").read_text(encoding="utf-8"))
    assert _digests(payload) == {SALES: sales}
    assert payload["missing"] == [OPS]


# --- the file of the whole ref ------------------------------------------------------------


def test_each_dashboard_compiles_its_own_spec_and_reaches_its_own_tiles(tmp_path: Path) -> None:
    case = _region_retyped(_case(tmp_path, SALES, OPS))
    sales = _write_spec(case.dashboards / "sales" / "weekly_margin@7.yaml", dashboard_spec())
    ops = _write_spec(case.dashboards / "ops" / "weekly_margin@7.yaml", _margin_by_category())

    report = case.run()

    assert _compiled(tmp_path, report) == {SALES: sales, OPS: ops}
    assert _missing(tmp_path, report) == []
    payloads = _bi_payloads(tmp_path, report)
    assert any(name.startswith("bi-dashboard-sales-weekly-margin-7-") for name in payloads)
    assert any(name.startswith("bi-dashboard-ops-weekly-margin-7-") for name in payloads)
    assert _tiles(tmp_path, report) == [f"{SALES}#revenue_by_region"]
    assert _check_payload(tmp_path, report, "impact.products")["chart_gaps"] == []


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".json"])
def test_every_segment_but_the_last_is_a_directory(tmp_path: Path, suffix: str) -> None:
    case = _case(tmp_path, DEEP)
    path = case.dashboards / "sales" / "emea" / f"weekly_margin@7{suffix}"
    deep = _write_spec(path, _titled("EMEA"))

    report = case.run()

    assert _compiled(tmp_path, report) == {DEEP: deep}
    assert summary_of(report, "bi.contract") == "bi.compiled"
    assert report.verdict is Verdict.PASS_WITH_ADVISORY


def test_the_first_suffix_a_spec_is_there_under_is_read(tmp_path: Path) -> None:
    digests: dict[str, str] = {}
    for suffix in (".yaml", ".yml", ".json"):
        directory = tmp_path / "specs"
        digests[suffix] = _write_spec(directory / f"weekly_margin@7{suffix}", _titled(suffix))
    read = []
    for run, gone in enumerate(("", ".yaml", ".yml")):
        if gone:
            (tmp_path / "specs" / f"weekly_margin@7{gone}").unlink()
        case = _case(tmp_path / f"run-{run}", SALES)
        (case.dashboards / "sales").mkdir()
        for spec in (tmp_path / "specs").iterdir():
            (case.dashboards / "sales" / spec.name).write_bytes(spec.read_bytes())
        read.append(_compiled(tmp_path / f"run-{run}", case.run())[SALES])

    assert read == [digests[".yaml"], digests[".yml"], digests[".json"]]


def test_a_dashboard_of_two_segments_does_not_load_another_version(tmp_path: Path) -> None:
    """Neither the file of version 8 beside it nor the file of its last segment at version 7."""
    case = _case(tmp_path, SALES)
    _write_spec(case.dashboards / "sales" / "weekly_margin@8.yaml", dashboard_spec())

    report = case.run()

    assert _missing(tmp_path, report) == [SALES]
    assert summary_of(report, "bi.contract") == "bi.spec_missing"


def test_a_segment_that_names_a_file_is_a_spec_that_is_not_there(tmp_path: Path) -> None:
    case = _case(tmp_path, SALES)
    (case.dashboards / "sales").write_text("not a directory\n", encoding="utf-8")

    report = case.run()

    assert _missing(tmp_path, report) == [SALES]
    assert summary_of(report, "bi.contract") == "bi.spec_missing"


# --- a file that is there and is refused ends the run, as before --------------------------
#
# Each of them has a valid `.yml` beside it, which a refusal of the `.yaml` does not hand on to.


@pytest.mark.parametrize(
    ("refuse", "said"),
    [(_deny_look, "PermissionError"), (_deny, "PermissionError")],
    ids=["look", "read"],
)
def test_a_spec_of_its_path_that_is_refused_ends_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refuse: Callable[[pytest.MonkeyPatch, Path], None],
    said: str,
) -> None:
    case = _case(tmp_path, SALES)
    path = case.dashboards / "sales" / "weekly_margin@7.yaml"
    _write_spec(path, dashboard_spec())
    _write_spec(path.with_suffix(".yml"), dashboard_spec())
    refuse(monkeypatch, path)

    assert _refused(case) == f"cannot read {path}: {said}"


def test_a_spec_of_its_path_that_is_no_dashboard_spec_ends_the_run(tmp_path: Path) -> None:
    case = _case(tmp_path, SALES)
    path = case.dashboards / "sales" / "weekly_margin@7.yaml"
    _write_yaml(path, {"title": "без чартов"})
    _write_spec(path.with_suffix(".yml"), dashboard_spec())

    assert _refused(case).startswith(f"{path} is not a valid DashboardSpec: ")


def test_reading_the_file_of_the_whole_ref_moved_both_versions() -> None:
    assert int(VERIFIER_VERSION) >= 112
    assert int(CHECK_VERSION) >= 76

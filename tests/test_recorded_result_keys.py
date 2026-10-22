"""Recorded answers must name an asset in the supplied, unscoped repository."""

from pathlib import Path

import pytest
import yaml

from auto_bi.cli import main
from auto_bi.decision.use_cases import VerifyError
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    ASSET_ID,
    PRODUCT_ID,
    REVENUE_PRODUCT_ID,
    Case,
    _two_product_graph,
    _write_yaml,
    summary_of,
)


@pytest.mark.parametrize(
    "key", ["typo", "asset://missing@1", 12, None, "line\nbreak", ASSET_ID + " ", ASSET_ID + "@1"]
)
@pytest.mark.parametrize("bare", [True, False])
def test_unknown_result_key_refuses_before_probe_and_evidence(
    tmp_path: Path, key: object, bare: bool
) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"]])
    assert case.results is not None
    raw = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    value = raw[ASSET_ID]
    raw[key] = value["result"] if bare else value
    _write_yaml(case.results, raw)

    def unexpected_probe(_questions: object) -> None:
        pytest.fail("unknown result key reached the DWH probe")

    with pytest.raises(VerifyError, match="unknown verified asset") as caught:
        case.run(probe=unexpected_probe)
    assert repr(key) in str(caught.value)
    assert len(str(caught.value).splitlines()) == 1
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("with_assets", [True, False])
@pytest.mark.parametrize("filename", ["results.yaml", "results\u2028name.yaml"])
def test_unknown_result_key_alone_is_cli_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], with_assets: bool, filename: str
) -> None:
    case = Case(tmp_path).with_candidate().with_results([["Москва", "0.412"]])
    if with_assets:
        case.with_asset()
    assert case.results is not None
    raw = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    case.results = tmp_path / filename
    _write_yaml(case.results, {"typo": raw[ASSET_ID]})

    assert main(_verify_argv(case)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "unknown verified asset 'typo'" in captured.err
    assert not (tmp_path / "evidence").exists()


def test_malformed_value_keeps_priority_over_unknown_key(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"]])
    assert case.results is not None
    raw = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    _write_yaml(case.results, {"unknown": raw[ASSET_ID], ASSET_ID: {"rows": "invalid"}})
    with pytest.raises(VerifyError, match="holds an invalid recorded result"):
        case.run()
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("product_id", [PRODUCT_ID, REVENUE_PRODUCT_ID])
def test_known_result_is_allowed_outside_selected_product(tmp_path: Path, product_id: str) -> None:
    case = _two_product_graph(Case(tmp_path).with_candidate())
    case.with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run(product_id=product_id)
    expected = "result.equivalent" if product_id == PRODUCT_ID else "result.no_expectations"
    assert summary_of(report, "results.reconcile") == expected


@pytest.mark.parametrize("with_assets", [True, False])
@pytest.mark.parametrize("empty_file", [True, False])
def test_absent_answers_remain_a_gate_outcome(
    tmp_path: Path, with_assets: bool, empty_file: bool
) -> None:
    case = Case(tmp_path).with_candidate()
    if with_assets:
        case.with_asset()
    if empty_file:
        case.results = tmp_path / "results.yaml"
        _write_yaml(case.results, {})
    report = case.run()
    expected = "result.not_equivalent" if with_assets else "result.no_expectations"
    assert summary_of(report, "results.reconcile") == expected

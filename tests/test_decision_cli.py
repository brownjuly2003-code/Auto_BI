"""DecisionOps CLI surface and its exit-code contract (§12.1).

Exit codes are a contract with CI, so they are pinned here rather than left to whatever the
implementation happens to return: a gate whose "block" is indistinguishable from a crash
cannot be wired into a pipeline. Per §12.1 this test exists before the CLI does.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from auto_bi.cli import main
from auto_bi.verification.model import EXIT_ERROR, Verdict, exit_code_for
from tests.test_decision_validate import write_contracts
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    ENVIRONMENT_REF,
    PRODUCT_ID,
    Case,
    _formula_change,
)


def test_capture_cli_writes_a_result_file_consumable_by_verify(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimal import Decimal

    from auto_bi.verification.result_probe import QueryColumn, QueryResult

    case = Case(tmp_path).with_candidate().with_asset()
    output = tmp_path / "captured-results.yaml"

    def target(engine: str) -> tuple[Any, str, str]:
        assert engine == "clickhouse"

        def factory() -> Any:
            return lambda _sql: QueryResult(
                columns=(
                    QueryColumn("region", "String", nullable=False),
                    QueryColumn("gross_margin_pct", "Decimal(18, 6)", nullable=False),
                ),
                rows=(("Москва", Decimal("0.412000")),),
            )

        return factory, ENVIRONMENT_REF, "auto_bi_ro"

    monkeypatch.setattr("auto_bi.cli._result_capture_target", target)

    assert (
        main(
            [
                "decision",
                "capture",
                str(case.assets),
                "--candidate",
                str(case.candidate),
                "--asset",
                ASSET_ID,
                "--dwh",
                "clickhouse",
                "--output",
                str(output),
                "--captured-at",
                CREATED_AT,
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"asset    {ASSET_ID}" in captured.out
    assert f"wrote    {output}" in captured.out
    recorded = yaml.safe_load(output.read_text(encoding="utf-8"))[ASSET_ID]
    assert recorded["attestation"]["semantic_digest"] == case.candidate_digest
    assert recorded["attestation"]["environment_ref"] == ENVIRONMENT_REF


def test_exit_code_contract_is_stable() -> None:
    assert exit_code_for(Verdict.PASS) == 0
    assert exit_code_for(Verdict.PASS_WITH_ADVISORY) == 0
    assert exit_code_for(Verdict.NEEDS_REVIEW) == 3
    assert exit_code_for(Verdict.BLOCK) == 4
    assert EXIT_ERROR == 2
    assert {verdict.value for verdict in Verdict} == {
        "pass",
        "pass_with_advisory",
        "needs_review",
        "block",
    }


def test_valid_set_exits_zero_and_reports_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["decision", "validate", str(write_contracts(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "pass" in out
    assert "decision://sales/weekly_margin@1" in out


def test_unresolvable_ref_exits_block(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def repoint(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/net_margin_pct@1"]

    assert main(["decision", "validate", str(write_contracts(tmp_path, product=repoint))]) == 4
    out = capsys.readouterr().out
    assert "metric_ref.unknown" in out
    assert "block" in out


def test_lifecycle_question_exits_needs_review(tmp_path: Path) -> None:
    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = "draft"

    assert main(["decision", "validate", str(write_contracts(tmp_path, metric=relabel))]) == 3


def test_advisory_still_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = "deprecated"

    assert main(["decision", "validate", str(write_contracts(tmp_path, metric=relabel))]) == 0
    assert "metric.deprecated" in capsys.readouterr().out


def test_json_output_is_the_only_thing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["decision", "validate", str(write_contracts(tmp_path)), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["kind"] == "DecisionValidationReport"
    assert payload["verdict"] == "pass"
    assert payload["products"] == ["decision://sales/weekly_margin@1"]
    assert payload["issues"] == []


def test_json_output_carries_stable_issue_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def repoint(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/net_margin_pct@1"]

    main(["decision", "validate", str(write_contracts(tmp_path, product=repoint)), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [issue["code"] for issue in payload["issues"]] == ["metric_ref.unknown"]
    assert payload["issues"][0]["severity"] == "block"


def test_missing_path_exits_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["decision", "validate", str(tmp_path / "nope")]) == EXIT_ERROR
    assert "contract.unreadable" in capsys.readouterr().out


def test_directory_without_products_exits_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run that validated nothing must not look like a pass."""
    metric = yaml.safe_load(
        (Path(__file__).parent / "fixtures" / "decision" / "gross_margin_pct.yaml").read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "gross_margin_pct.yaml").write_text(
        yaml.safe_dump(metric, allow_unicode=True), encoding="utf-8"
    )
    assert main(["decision", "validate", str(tmp_path)]) == EXIT_ERROR
    assert "no decision product" in capsys.readouterr().out.lower()


def test_decision_command_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["decision"])
    assert exc.value.code == 2


# --- decision verify ----------------------------------------------------------


def _verify_argv(case: Case, *extra: str) -> list[str]:
    argv = [
        "decision",
        "verify",
        str(case.contracts),
        "--base",
        str(case.base),
        "--candidate",
        str(case.candidate),
        "--evidence",
        str(case.root / "evidence"),
        "--dashboards",
        str(case.dashboards),
        "--created-at",
        CREATED_AT,
    ]
    if case.assets is not None:
        argv += ["--assets", str(case.assets)]
    if case.results is not None:
        argv += ["--results", str(case.results)]
    return [*argv, *extra]


def test_environment_probe_wires_the_runtime_cost_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_bi import cli, config
    from auto_bi.introspect import clickhouse
    from auto_bi.verification import environment_probe, runtime_probe
    from auto_bi.verification.environment import (
        EnvironmentFacts,
        EnvironmentQuestions,
        RoleFacts,
    )

    settings = SimpleNamespace(
        ch_host="warehouse",
        ch_port=8123,
        ch_database="dm",
        ch_user="gate_ro",
        ch_password="secret",
    )

    def catalog_query(_sql: str) -> list[object]:
        return []

    def cost_query(_question: object) -> None:
        return None

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(clickhouse, "make_run_query", lambda configured: catalog_query)
    monkeypatch.setattr(
        runtime_probe, "make_clickhouse_runtime_probe", lambda configured: cost_query
    )

    def collect(
        run_query: Any,
        questions: EnvironmentQuestions,
        *,
        engine: str,
        target: str,
        run_cost: Any,
    ) -> EnvironmentFacts:
        assert run_query is catalog_query
        assert run_cost is cost_query
        assert engine == "clickhouse"
        assert target == "warehouse:8123/dm"
        return EnvironmentFacts(
            engine=engine,
            target=target,
            role=RoleFacts(name="gate_ro", read_only=True, evidence="readonly=1"),
        )

    monkeypatch.setattr(environment_probe, "collect", collect)
    probe = cli._environment_probe("clickhouse")

    assert probe is not None
    assert probe(EnvironmentQuestions()) is not None


def test_verify_exits_zero_on_a_reconciled_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    case.with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    assert main(_verify_argv(case)) == 0
    out = capsys.readouterr().out
    assert "verdict: pass_with_advisory" in out
    assert "status   completed" in out


def test_verify_exits_block_on_a_breaking_seed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    assert main(_verify_argv(case)) == 4
    out = capsys.readouterr().out
    assert "block    impact.products" in out
    assert "verdict: block" in out


def test_verify_exits_review_when_a_comparison_did_not_happen(tmp_path: Path) -> None:
    """No candidate result means no evidence — a human decides, exit 3 (§12.1)."""
    case = Case(tmp_path).with_candidate().with_asset()
    assert main(_verify_argv(case)) == 3


def test_verify_exits_review_when_the_number_has_no_stated_origin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A results file in the pre-attestation shape still reads, and still asks a human."""
    case = Case(tmp_path).with_candidate()
    case.with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]], attested=None)
    assert main(_verify_argv(case, "--environment", ENVIRONMENT_REF)) == 3
    out = capsys.readouterr().out
    assert "review   results.provenance" in out
    assert "verdict: needs_review" in out


def test_verify_exits_zero_when_the_number_is_traced_to_its_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    case.with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    assert main(_verify_argv(case, "--environment", ENVIRONMENT_REF)) == 0
    assert "pass     results.provenance" in capsys.readouterr().out


def test_verify_rejects_a_results_file_whose_attestation_does_not_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed input is a usage error, not a verdict about the release (§12.1)."""
    case = Case(tmp_path).with_candidate()
    case.with_asset().with_results([["Москва", "0.412"]])
    assert case.results is not None
    raw = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    raw[ASSET_ID]["attestation"]["origin"] = "guesswork"
    case.results.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert main(_verify_argv(case)) == EXIT_ERROR
    assert "invalid recorded result" in capsys.readouterr().err


def test_verify_usage_error_exits_two_and_writes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    (case.contracts / "weekly_margin.yaml").unlink()
    assert main(_verify_argv(case)) == EXIT_ERROR
    captured = capsys.readouterr()
    assert "no decision product" in captured.err
    assert captured.out == ""


def test_verify_json_output_is_the_only_thing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    code = main(_verify_argv(case, "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["kind"] == "DecisionGateReport"
    assert payload["product_ref"] == f"{PRODUCT_ID}@1"
    assert payload["operational_status"] == "completed"
    assert payload["verdict"] == "pass_with_advisory"
    assert payload["run_id"].startswith("run-")


def test_verify_writes_the_bundle_under_the_evidence_dir(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    main(_verify_argv(case, "--json"))
    runs = sorted((tmp_path / "evidence").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "manifest.json").is_file()
    assert (runs[0] / "checks" / "check-bi.contract.json").is_file()


# --- decision evidence --------------------------------------------------------


def _written_bundle(tmp_path: Path, *extra: str) -> Path:
    """Run the gate once and return the evidence root it wrote into."""
    case = Case(tmp_path).with_candidate()
    main(_verify_argv(case, "--json", *extra))
    return tmp_path / "evidence"


def _run_dir(evidence: Path) -> Path:
    return next(child for child in sorted(evidence.iterdir()) if child.is_dir())


def test_evidence_reads_back_the_bundle_the_gate_just_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()

    assert main(["decision", "evidence", str(evidence)]) == 0
    out = capsys.readouterr().out
    assert _run_dir(evidence).name in out
    assert f"{PRODUCT_ID}@1" in out
    assert "check-semantic.diff" in out  # the artifacts are listed, not just counted
    assert "verdict: pass" in out


@pytest.mark.parametrize(("opening", "closing"), [(b"[", b"]"), (b'{"nested":', b"}")])
def test_evidence_artifact_reports_manifest_beyond_json_parser_depth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], opening: bytes, closing: bytes
) -> None:
    run_dir = tmp_path / "run-deep-manifest"
    run_dir.mkdir()
    raw = opening * 3000 + b'"private-manifest-marker"' + closing * 3000
    manifest = run_dir / "manifest.json"
    manifest.write_bytes(raw)

    assert main(["decision", "evidence", str(run_dir), "--artifact", "check-any"]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "no readable manifest.json" in captured.err
    assert "private-manifest-marker" not in captured.err
    assert "Traceback" not in captured.err
    assert manifest.read_bytes() == raw
    assert list(run_dir.iterdir()) == [manifest]

    assert main(["decision", "evidence", str(run_dir), "--json"]) == 4
    captured = capsys.readouterr()
    assert captured.err == ""
    inspection = json.loads(captured.out)
    assert [problem["code"] for problem in inspection["problems"]] == ["evidence.manifest_invalid"]


def test_evidence_accepts_the_run_directory_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`<run-or-path>` per §12.1 — a run directory is as good an address as its root."""
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()

    assert main(["decision", "evidence", str(_run_dir(evidence))]) == 0
    assert _run_dir(evidence).name in capsys.readouterr().out


def test_evidence_will_not_guess_which_run_is_meant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    case = Case(tmp_path).with_candidate()
    main(_verify_argv(case, "--json", "--environment", "dwh://stage"))
    capsys.readouterr()
    runs = sorted(child.name for child in evidence.iterdir() if child.is_dir())
    assert len(runs) == 2

    assert main(["decision", "evidence", str(evidence)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert all(run in captured.err for run in runs)
    assert main(["decision", "evidence", str(evidence), "--run", runs[0]]) == 0


def test_evidence_on_a_directory_that_holds_no_bundle_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["decision", "evidence", str(tmp_path)]) == EXIT_ERROR
    assert "no evidence bundle" in capsys.readouterr().err


def test_evidence_exits_block_on_a_tampered_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    (_run_dir(evidence) / "checks" / "check-semantic.diff.json").write_bytes(b"{}")
    capsys.readouterr()

    assert main(["decision", "evidence", str(evidence)]) == 4
    out = capsys.readouterr().out
    assert "evidence.digest_mismatch" in out
    assert "verdict: block" in out


def test_evidence_json_output_is_the_only_thing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()

    code = main(["decision", "evidence", str(evidence), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["kind"] == "EvidenceInspection"
    assert payload["verdict"] == "pass"
    assert payload["problems"] == []
    assert payload["bundle"]["product_ref"] == f"{PRODUCT_ID}@1"
    assert payload["manifest_digest"].startswith("sha256:")


def test_evidence_prints_one_artifact_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()

    code = main(["decision", "evidence", str(evidence), "--artifact", "check-semantic.diff"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["changes"] == []


def test_evidence_artifact_honors_the_manifest_digest_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()
    main(["decision", "evidence", str(evidence), "--json"])
    digest = json.loads(capsys.readouterr().out)["manifest_digest"]

    code = main(
        [
            "decision",
            "evidence",
            str(evidence),
            "--artifact",
            "check-semantic.diff",
            "--expect-digest",
            digest,
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["changes"] == []
    assert captured.err == ""

    stale = "sha256:" + "0" * 64
    code = main(
        [
            "decision",
            "evidence",
            str(evidence),
            "--artifact",
            "check-semantic.diff",
            "--expect-digest",
            stale,
        ]
    )
    captured = capsys.readouterr()
    assert code == 4
    assert captured.out == ""
    assert "evidence.digest_unexpected" in captured.err


def test_evidence_refuses_to_print_a_payload_that_is_not_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout is for the payload; bytes that are not text get an address, not a mangling."""
    from tests.test_evidence_store import RUN, full_bundle

    builder = full_bundle(tmp_path)
    builder.add_blob(
        "screenshot",
        kind="raw_payload",
        payload=b"\x89PNG\r\n\x1a\n\xff\xfe",
        media_type="image/png",
    )
    builder.write()
    capsys.readouterr()

    code = main(["decision", "evidence", str(tmp_path / RUN), "--artifact", "screenshot"])
    captured = capsys.readouterr()

    assert code == EXIT_ERROR
    assert captured.out == ""
    assert "image/png" in captured.err
    assert "blobs/sha256/" in captured.err


def test_evidence_says_which_artifacts_exist_when_the_id_is_wrong(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()

    assert main(["decision", "evidence", str(evidence), "--artifact", "check-nope"]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "check-semantic.diff" in captured.err


def test_evidence_checks_the_digest_it_was_told_to_expect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()
    main(["decision", "evidence", str(evidence), "--json"])
    digest = json.loads(capsys.readouterr().out)["manifest_digest"]

    assert main(["decision", "evidence", str(evidence), "--expect-digest", digest]) == 0
    capsys.readouterr()

    stale = "sha256:" + "0" * 64
    assert main(["decision", "evidence", str(evidence), "--expect-digest", stale]) == 4
    assert "evidence.digest_unexpected" in capsys.readouterr().out

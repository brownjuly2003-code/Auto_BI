"""plan_sol step 12: bounded mutation gate configuration and result policy."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from scripts import check_mutation_stats

ROOT = Path(__file__).resolve().parents[1]
FUNCTION = "auto_bi.agent.sql_guard.x__grade_columns"
METHOD = "auto_bi.agent.sql_guard.x\u01c1_ReadThroughState\u01c1__init__"
OTHER_FUNCTION = "auto_bi.ir.validate.x_validate"
BASELINE_PROPOSAL_MARKER = "mutation gate: baseline proposal:"


def _run_stats_check(
    tmp_path: Path, *, in_process: bool = False
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(ROOT / "scripts" / "check_mutation_stats.py"),
        str(tmp_path / "mutmut-cicd-stats.json"),
        "--mutants-dir",
        str(tmp_path / "mutants"),
        "--baseline",
        str(tmp_path / "baseline.json"),
        "--pyproject",
        str(tmp_path / "pyproject.toml"),
    ]
    if in_process:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = check_mutation_stats.main(args[2:])
        return subprocess.CompletedProcess(args, returncode, stdout.getvalue(), stderr.getvalue())
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _stats(**counts: int) -> dict[str, int]:
    stats = {
        "killed": 0,
        "survived": 0,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    stats.update(counts)
    stats["total"] = sum(stats.values())
    return stats


def _write_source(path: Path, mutants: dict[str, int | None]) -> None:
    definitions: list[str] = []
    for mutant in mutants:
        name = mutant.rsplit(".", 1)[1]
        if "\u01c1" in name:
            class_name = name.split("\u01c1")[1]
            definitions.append(f"class {class_name}:\n    def {name}(self):\n        pass\n")
        else:
            definitions.append(f"def {name}():\n    pass\n")
    path.write_text("\n".join(definitions), encoding="utf-8", newline="\n")


def _write_run(
    tmp_path: Path,
    outcomes: dict[str, list[int | None]] | None = None,
    stats: dict[str, int] | None = None,
    baseline: dict[str, dict[str, int]] | None = None,
) -> None:
    if outcomes is None:
        outcomes = {FUNCTION: [1, 36]}
    if stats is None:
        stats = _stats(killed=1, timeout=1)
    mutants_dir = tmp_path / "mutants"
    mutants_dir.mkdir()
    by_module: dict[str, dict[str, int | None]] = {}
    for function, codes in outcomes.items():
        module = function.rsplit(".", 1)[0]
        results = by_module.setdefault(module, {})
        for number, code in enumerate(codes, 1):
            results[f"{function}__mutmut_{number}"] = code
    for module, results in by_module.items():
        meta_path = mutants_dir / (module.replace(".", "/") + ".py.meta")
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({"exit_code_by_key": results}), encoding="utf-8")
        _write_source(meta_path.with_suffix(""), results)
    targets = [module.replace(".", "/") + ".py" for module in by_module]
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mutmut]\nonly_mutate = " + json.dumps(targets) + "\n", encoding="utf-8"
    )
    (tmp_path / "mutmut-cicd-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    (tmp_path / "baseline.json").write_text(
        json.dumps({"measured": "synthetic test fixture", "functions": baseline or {}}),
        encoding="utf-8",
    )


def _assert_baseline_proposal(
    result: subprocess.CompletedProcess[str], functions: dict[str, dict[str, int]]
) -> str:
    assert result.stdout.count(BASELINE_PROPOSAL_MARKER) == 1
    _, marker, proposal_text = result.stdout.partition(BASELINE_PROPOSAL_MARKER + "\n")
    assert marker
    expected = {
        "measured": "TODO: fill in measurement date and environment",
        "functions": functions,
    }
    assert json.loads(proposal_text) == expected
    assert proposal_text == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    return proposal_text


def test_mutmut_scope_is_pinned_and_bounded() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mutmut = config["tool"]["mutmut"]
    assert mutmut["source_paths"] == ["auto_bi/"]
    assert mutmut["only_mutate"] == [
        "auto_bi/agent/sql_guard.py",
        "auto_bi/ir/validate.py",
        "auto_bi/agent/dataset_plan.py",
        "auto_bi/agent/cleanup.py",
    ]
    assert mutmut["pytest_add_cli_args"] == [
        "-W",
        "ignore:unclosed scandir iterator:ResourceWarning",
        "-W",
        "ignore:(rm_rf) error removing:pytest.PytestWarning",
    ]
    assert mutmut["pytest_add_cli_args_test_selection"] == [
        "tests/test_property_quality.py",
        "tests/test_ir_validate.py",
        "tests/test_x5_raw_sql.py",
        "tests/test_p1_6_governance.py",
        "tests/test_query_plan.py",
        "tests/test_dataset_plan.py",
        "tests/test_native_filters.py",
        "tests/test_pipeline.py",
        "tests/test_cli_prune.py",
        "tests/test_adapter_contract.py",
        "tests/test_adapter_factory.py",
        "tests/test_auth.py",
        "tests/test_autospec.py",
        "tests/test_bi_canonical_refusal.py",
        "tests/test_bi_compile_refusal.py",
        "tests/test_bi_contract_validation.py",
        "tests/test_build_attempt_reconciliation.py",
        "tests/test_capture_driver_failures.py",
        "tests/test_clickhouse_session_limits.py",
        "tests/test_datalens_chart.py",
        "tests/test_decision_chart_impact.py",
        "tests/test_decision_chart_impact_dashboard_scope.py",
        "tests/test_decision_chart_impact_dictionary.py",
        "tests/test_decision_chart_impact_join_get.py",
        "tests/test_environment_sql_guard.py",
        "tests/test_generated_sql_bytes.py",
        "tests/test_label_joins.py",
        "tests/test_lifecycle_close.py",
        "tests/test_machine.py",
        "tests/test_postgres_sessions.py",
        "tests/test_probe_completion.py",
        "tests/test_probe_safety.py",
        "tests/test_propose.py",
        "tests/test_result_capture.py",
        "tests/test_result_probe.py",
        "tests/test_result_provenance.py",
        "tests/test_result_reconcile_column_order.py",
        "tests/test_result_tolerance_contract.py",
        "tests/test_runtime_probe.py",
        "tests/test_runtime_setting_refusals.py",
        "tests/test_safe_error.py",
        "tests/test_sql_guard_clickhouse_array_join_paren.py",
        "tests/test_sql_guard_clickhouse_columns_reachability.py",
        "tests/test_sql_guard_clickhouse_cte_case.py",
        "tests/test_sql_guard_clickhouse_in_table.py",
        "tests/test_sql_guard_clickhouse_inline_alias.py",
        "tests/test_sql_guard_clickhouse_join_using_reachability.py",
        "tests/test_sql_guard_clickhouse_settings.py",
        "tests/test_sql_guard_clickhouse_star_reachability.py",
        "tests/test_sql_guard_clickhouse_table_cte_case.py",
        "tests/test_sql_guard_count_star.py",
        "tests/test_sql_guard_cte_visibility.py",
        "tests/test_sql_guard_having_qualify.py",
        "tests/test_sql_guard_mutation_alias_reads.py",
        "tests/test_sql_guard_mutation_clickhouse_aliases.py",
        "tests/test_sql_guard_mutation_cte_gates.py",
        "tests/test_sql_guard_parse_depth.py",
        "tests/test_sql_guard_postgres_cast_output.py",
        "tests/test_sql_guard_postgres_group_by.py",
        "tests/test_sql_guard_postgres_order_by.py",
        "tests/test_sql_guard_postgres_root_parenthesized.py",
        "tests/test_sql_guard_postgres_table_column_alias.py",
        "tests/test_sqlgen.py",
        "tests/test_sqlgen_compare.py",
        "tests/test_sqlgen_grain.py",
        "tests/test_sqlgen_histogram.py",
        "tests/test_sqlgen_lag.py",
        "tests/test_sqlgen_ratio.py",
        "tests/test_sqlgen_running_share.py",
        "tests/test_sqlgen_transforms.py",
        "tests/test_sqlgen_yoy.py",
        "tests/test_superset_adapter.py",
        "tests/test_verified_assets.py",
        "tests/test_x3_retention.py",
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
    assert f"The selection now contains {len(expected)} modules." in match.group(1)


def test_slo_mutation_smoke_production_targets_match_mutmut_config() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = config["tool"]["mutmut"]["only_mutate"]

    slo = (ROOT / "docs" / "operations" / "SLO.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## Mutation smoke\n(.*?)(?=^## |\Z)",
        slo,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    documented = re.findall(r"`(auto_bi/[^`]+\.py)`", match.group(1))
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
        r"`killed\s*\+\s*timeout\s*\+\s*survived\s*\+\s*no_tests`" r"\s+must\s+equal\s+`total`",
        section,
        flags=re.IGNORECASE,
    )
    normalized = " ".join(section.split())
    for sentence in (
        "2026-11-03",
        "3011 mutants: 2614 killed, 2 timeout, 356 survived, and 39 no_tests",
        "1553 seconds wall-clock",
        "2.08 mutations/second",
        "60-minute CI timeout",
        "3011 mutants: 2586 killed, 9 timeout, 377 survived, and 39 no_tests",
        "2265 seconds",
        "Python 3.13.17",
        "ignore:unclosed scandir iterator:ResourceWarning",
        "ignore:(rm_rf) error removing:pytest.PytestWarning",
        BASELINE_PROPOSAL_MARKER,
        "54% of run time",
        "3011 mutants: 2733 killed, 3 timeout, 260 survived, and 15 no_tests",
        "3011 mutants: 2830 killed, 3 timeout, 178 survived, and 0 no_tests",
        "2878 killed, 3 timeout, 130 survived, and 0 no_tests",
        "the baseline keeps the larger one",
        "scripts/mutation_baseline.json",
        "A function absent from the baseline is allowed zero weak mutants.",
        "The baseline must be lowered in the same change",
        "Later slices kill survivors and lower both the baseline and the timeout.",
    ):
        assert sentence in normalized


def test_primary_quality_job_runs_pinned_mutation_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Mutation smoke (bounded core)" in workflow
    assert "uv run --with mutmut==3.6.0 mutmut run" in workflow
    assert "--max-children 4" in workflow
    assert "uv run --with mutmut==3.6.0 mutmut export-cicd-stats" in workflow
    assert (
        "scripts/check_mutation_stats.py mutants/mutmut-cicd-stats.json "
        "--mutants-dir mutants --baseline scripts/mutation_baseline.json "
        "--pyproject pyproject.toml"
    ) in workflow
    assert re.search(r"Mutation smoke \(bounded core\)\n\s+timeout-minutes: 60\n", workflow)
    step = re.search(
        r"^      - name: Mutation smoke \(bounded core\)\n(.*?)(?=^      - |\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert step is not None
    assert re.search(
        r'^        env:\n(?:^          #[^\n]*\n)*^          PYTHONHASHSEED: "0"$',
        step.group(1),
        flags=re.MULTILINE,
    )


def test_mutation_stats_accept_killed_and_timeout_mutants(tmp_path: Path) -> None:
    _write_run(tmp_path)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "2/2 effective kills" in result.stdout
    assert BASELINE_PROPOSAL_MARKER not in result.stdout


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (None, "not_checked"),
        (2, "check_was_interrupted_by_user"),
        (34, "skipped"),
        (35, "suspicious"),
        (37, "caught_by_type_check"),
        (-11, "segfault"),
        (-9, "segfault"),
        (99, "suspicious"),
    ],
)
def test_mutation_stats_reject_other_statuses(
    tmp_path: Path, code: int | None, status: str
) -> None:
    _write_run(tmp_path, {FUNCTION: [code]}, _stats(**{status: 1}))
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert status in result.stderr
    assert FUNCTION in result.stderr


@pytest.mark.parametrize("code", [36, -24, 24, 152, 255])
def test_mutation_stats_accept_every_timeout_code(tmp_path: Path, code: int) -> None:
    _write_run(tmp_path, {FUNCTION: [code]}, _stats(timeout=1))
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "1/1 effective kills" in result.stdout


def test_mutation_ratchet_accepts_equal_baseline(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        {FUNCTION: [0, 0, 5, 33], METHOD: [0], OTHER_FUNCTION: [1, 3, 36]},
        _stats(killed=2, timeout=1, survived=3, no_tests=2),
        {FUNCTION: {"survived": 2, "no_tests": 2}, METHOD: {"survived": 1}},
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "3/8 effective kills" in result.stdout
    assert "must be lowered" not in result.stdout
    assert BASELINE_PROPOSAL_MARKER not in result.stdout


@pytest.mark.parametrize(("code", "kind"), [(0, "survived"), (5, "no_tests"), (33, "no_tests")])
@pytest.mark.parametrize("allowed", [0, 1, 3])
def test_mutation_ratchet_prints_complete_baseline_proposal(
    tmp_path: Path, code: int, kind: str, allowed: int
) -> None:
    baseline = {METHOD: {"survived": 1, "no_tests": 1}}
    if allowed:
        baseline[FUNCTION] = {kind: allowed}
    counts = {"killed": 1, "survived": 1, "no_tests": 1}
    counts[kind] += 2
    _write_run(
        tmp_path,
        {OTHER_FUNCTION: [1], METHOD: [0, 5], FUNCTION: [code, code]},
        _stats(**counts),
        baseline,
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == (1 if allowed < 2 else 0), result.stderr
    proposal_text = _assert_baseline_proposal(
        result, {METHOD: {"survived": 1, "no_tests": 1}, FUNCTION: {kind: 2}}
    )

    # The complete proposal is accepted unchanged as the next baseline.
    (tmp_path / "baseline.json").write_text(proposal_text, encoding="utf-8")
    rechecked = _run_stats_check(tmp_path)
    assert rechecked.returncode == 0, rechecked.stderr
    assert BASELINE_PROPOSAL_MARKER not in rechecked.stdout


def test_mutation_ratchet_proposes_empty_baseline_when_all_weak_counts_fall_to_zero(
    tmp_path: Path,
) -> None:
    _write_run(tmp_path, baseline={FUNCTION: {"survived": 1, "no_tests": 1}})
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    _assert_baseline_proposal(result, {})


@pytest.mark.parametrize(("code", "kind"), [(0, "survived"), (5, "no_tests"), (33, "no_tests")])
def test_mutation_ratchet_rejects_increase(tmp_path: Path, code: int, kind: str) -> None:
    _write_run(tmp_path, {FUNCTION: [code, code]}, _stats(**{kind: 2}), {FUNCTION: {kind: 1}})
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"{FUNCTION}: {kind}=2 exceeds baseline=1" in result.stderr


@pytest.mark.parametrize(("code", "kind"), [(0, "survived"), (5, "no_tests")])
def test_mutation_ratchet_rejects_unlisted_function(tmp_path: Path, code: int, kind: str) -> None:
    _write_run(tmp_path, {OTHER_FUNCTION: [code]}, _stats(**{kind: 1}), {FUNCTION: {kind: 2}})
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"{OTHER_FUNCTION}: {kind}=1 exceeds baseline=0" in result.stderr


def test_mutation_ratchet_does_not_trade_weak_kinds(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [5]}, _stats(no_tests=1), {FUNCTION: {"survived": 2}})
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"{FUNCTION}: no_tests=1 exceeds baseline=0" in result.stderr


def test_mutation_ratchet_reports_lower_counts_and_absent_functions(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        {FUNCTION: [0, 1]},
        _stats(killed=1, survived=1),
        {
            FUNCTION: {"survived": 2, "no_tests": 1},
            OTHER_FUNCTION: {"survived": 3},
        },
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"{FUNCTION}: survived=1 below baseline=2" in result.stdout
    assert f"{FUNCTION}: no_tests=0 below baseline=1" in result.stdout
    assert f"{OTHER_FUNCTION}: produced no mutants" in result.stdout
    assert f"{OTHER_FUNCTION}: survived=0 below baseline=3" in result.stdout
    assert "baseline must be lowered in the same change" in result.stdout
    _assert_baseline_proposal(result, {FUNCTION: {"survived": 1}})


def test_mutation_ratchet_prints_notices_alongside_errors(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        {FUNCTION: [0, 1], METHOD: [0]},
        _stats(killed=1, survived=2),
        {FUNCTION: {"survived": 2}, OTHER_FUNCTION: {"survived": 3}},
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"{METHOD}: survived=1 exceeds baseline=0" in result.stderr
    assert f"{FUNCTION}: survived=1 below baseline=2" in result.stdout
    assert f"{OTHER_FUNCTION}: produced no mutants" in result.stdout
    assert "below baseline" not in result.stderr
    assert "produced no mutants" not in result.stderr
    _assert_baseline_proposal(result, {FUNCTION: {"survived": 1}, METHOD: {"survived": 1}})


def test_mutation_inventory_rejects_lost_survivor_with_matching_stats(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1, 0]}, _stats(killed=1, survived=1))
    before = _run_stats_check(tmp_path)
    assert before.returncode == 1
    assert f"{FUNCTION}: survived=1 exceeds baseline=0" in before.stderr
    meta = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    meta.write_text(
        json.dumps({"exit_code_by_key": {f"{FUNCTION}__mutmut_1": 1}}), encoding="utf-8"
    )
    (tmp_path / "mutmut-cicd-stats.json").write_text(json.dumps(_stats(killed=1)), encoding="utf-8")
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"missing result: {FUNCTION}__mutmut_2" in result.stderr
    assert "within baseline" not in result.stdout


def test_mutation_inventory_rejects_results_without_definitions(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1, 1, 1]}, _stats(killed=3))
    _write_source(tmp_path / "mutants/auto_bi/agent/sql_guard.py", {f"{FUNCTION}__mutmut_1": 1})
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    for number in (2, 3):
        assert f"unexpected result: {FUNCTION}__mutmut_{number}" in result.stderr


def test_mutation_inventory_rejects_definitions_without_results(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1]}, _stats(killed=1))
    _write_source(
        tmp_path / "mutants/auto_bi/agent/sql_guard.py",
        {f"{FUNCTION}__mutmut_{number}": 1 for number in (1, 2, 3)},
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    for number in (2, 3):
        assert f"missing result: {FUNCTION}__mutmut_{number}" in result.stderr


@pytest.mark.parametrize("suffix", [".py", ".py.meta"])
def test_mutation_inventory_requires_every_target_file(tmp_path: Path, suffix: str) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: [1]}, _stats(killed=2))
    path = tmp_path / "mutants" / ("auto_bi/ir/validate" + suffix)
    path.unlink()
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert str(path) in result.stderr
    assert "Traceback" not in result.stderr
    if suffix == ".py.meta":
        assert f"missing result: {OTHER_FUNCTION}__mutmut_1" in result.stderr


@pytest.mark.parametrize("code", [0, 1])
def test_mutation_inventory_reads_target_meta_omitted_by_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: [code]}, _stats(killed=1 + code))
    visible = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter([visible]))
    result = _run_stats_check(tmp_path, in_process=True)
    assert result.returncode == (1 if code == 0 else 0), result.stderr
    if code == 0:
        assert f"{OTHER_FUNCTION}: survived=1 exceeds baseline=0" in result.stderr
        assert "within baseline" not in result.stdout
    else:
        assert "2/2 effective kills, within baseline" in result.stdout


def test_mutation_inventory_reads_target_meta_despite_walk_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: [0]}, _stats(killed=1))

    def broken_walk(self: Path, pattern: str):
        yield tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
        raise PermissionError("synthetic enumeration error")

    monkeypatch.setattr(Path, "rglob", broken_walk)
    result = _run_stats_check(tmp_path, in_process=True)
    assert result.returncode == 1
    assert f"{OTHER_FUNCTION}: survived=1 exceeds baseline=0" in result.stderr
    assert "synthetic enumeration error" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("suffix", [".py", ".py.meta"])
def test_mutation_inventory_rejects_target_resolving_outside_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    _write_run(tmp_path)
    escaped = tmp_path / "mutants" / ("auto_bi/agent/sql_guard" + suffix)
    original_resolve = Path.resolve

    def resolve(self: Path, *args, **kwargs) -> Path:
        if self == escaped:
            return tmp_path / "mutants-outside" / escaped.name
        return original_resolve(self, *args, **kwargs)

    # Model a symlink/junction without requiring Windows symlink privileges.
    monkeypatch.setattr(Path, "resolve", resolve)
    result = _run_stats_check(tmp_path, in_process=True)
    assert result.returncode == 1
    assert f"{escaped}: target path must stay inside mutants directory" in result.stderr
    assert "Traceback" not in result.stderr


def test_mutation_inventory_rejects_stray_meta(tmp_path: Path) -> None:
    _write_run(tmp_path)
    path = tmp_path / "mutants/stray.py.meta"
    path.write_text('{"exit_code_by_key": {}}', encoding="utf-8")
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"{path}: .meta does not belong to a target" in result.stderr


def test_mutation_inventory_rejects_results_in_wrong_target_meta(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: [1]}, _stats(killed=2))
    first = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    second = tmp_path / "mutants/auto_bi/ir/validate.py.meta"
    first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
    first.write_bytes(second_bytes)
    second.write_bytes(first_bytes)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    for path, expected, unexpected in (
        (first, FUNCTION, OTHER_FUNCTION),
        (second, OTHER_FUNCTION, FUNCTION),
    ):
        assert f"{path}: missing result: {expected}__mutmut_1" in result.stderr
        assert f"{path}: unexpected result: {unexpected}__mutmut_1" in result.stderr


def test_mutation_inventory_accepts_method_with_ascii_escaped_meta(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1], METHOD: [1]}, _stats(killed=2))
    meta = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    assert "\\u01c1" in meta.read_text(encoding="utf-8")
    assert "\u01c1" in meta.with_suffix("").read_text(encoding="utf-8")
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "2/2 effective kills" in result.stdout


def test_mutation_inventory_accepts_nested_definitions(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1]}, _stats(killed=1))
    source = tmp_path / "mutants/auto_bi/agent/sql_guard.py"
    definition = source.read_text(encoding="utf-8")
    source.write_text(
        "def wrapper():\n" + "\n".join("    " + line for line in definition.splitlines()) + "\n",
        encoding="utf-8",
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("remove_result", [False, True])
def test_mutation_inventory_checks_async_definitions(tmp_path: Path, remove_result: bool) -> None:
    _write_run(tmp_path, {FUNCTION: [1, 1]}, _stats(killed=2))
    source = tmp_path / "mutants/auto_bi/agent/sql_guard.py"
    name = FUNCTION.rsplit(".", 1)[1]
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            f"def {name}__mutmut_2", f"async def {name}__mutmut_2"
        ),
        encoding="utf-8",
    )
    if remove_result:
        source.with_suffix(".py.meta").write_text(
            json.dumps({"exit_code_by_key": {f"{FUNCTION}__mutmut_1": 1}}), encoding="utf-8"
        )
        (tmp_path / "mutmut-cicd-stats.json").write_text(
            json.dumps(_stats(killed=1)), encoding="utf-8"
        )
    result = _run_stats_check(tmp_path)
    assert result.returncode == (1 if remove_result else 0), result.stderr
    if remove_result:
        assert f"missing result: {FUNCTION}__mutmut_2" in result.stderr
        assert "within baseline" not in result.stdout
    else:
        assert "2/2 effective kills, within baseline" in result.stdout


@pytest.mark.parametrize("keep_result", [False, True])
def test_mutation_inventory_withholds_incomplete_baseline(
    tmp_path: Path, keep_result: bool
) -> None:
    _write_run(
        tmp_path,
        {FUNCTION: [1, 0, 5], METHOD: [0], OTHER_FUNCTION: [1]},
        _stats(killed=2, survived=2, no_tests=1),
        {
            FUNCTION: {"survived": 2, "no_tests": 2},
            METHOD: {"survived": 2},
            OTHER_FUNCTION: {"no_tests": 1},
        },
    )
    results = {f"{METHOD}__mutmut_1": 0}
    if keep_result:
        results[f"{FUNCTION}__mutmut_1"] = 1
    (tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta").write_text(
        json.dumps({"exit_code_by_key": results}), encoding="utf-8"
    )
    (tmp_path / "mutmut-cicd-stats.json").write_text(
        json.dumps(_stats(killed=1 + keep_result, survived=1)), encoding="utf-8"
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"missing result: {FUNCTION}__mutmut_2" in result.stderr
    assert f"missing result: {FUNCTION}__mutmut_3" in result.stderr
    assert BASELINE_PROPOSAL_MARKER not in result.stdout
    assert (
        result.stdout.count(
            "mutation gate: baseline proposal withheld because results are incomplete"
        )
        == 1
    )
    assert f"{FUNCTION}: produced no mutants" not in result.stdout
    assert f"{FUNCTION}: survived=0 below baseline" not in result.stdout
    assert f"{FUNCTION}: no_tests=0 below baseline" not in result.stdout
    assert f"{METHOD}: survived=1 below baseline=2" in result.stdout
    assert f"{OTHER_FUNCTION}: no_tests=0 below baseline=1" in result.stdout


def test_mutation_inventory_ignores_orig_and_registry_references(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1]}, _stats(killed=1))
    source = tmp_path / "mutants/auto_bi/agent/sql_guard.py"
    name = FUNCTION.rsplit(".", 1)[1]
    source.write_text(
        source.read_text(encoding="utf-8")
        + f"\ndef {name}__mutmut_orig():\n    pass\n"
        + f"\ndef {name}__mutmut_2_suffix():\n    pass\n"
        + f"\nmutants_{name}__mutmut['{name}__mutmut_99'] = {name}__mutmut_99\n",
        encoding="utf-8",
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "1/1 effective kills" in result.stdout


def test_mutation_inventory_rejects_empty_target_among_nonempty_targets(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: []}, _stats(killed=1))
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    source = tmp_path / "mutants/auto_bi/ir/validate.py"
    assert f"{source}: empty mutant inventory" in result.stderr


@pytest.mark.parametrize("contents", [b"def broken(:", b"\xff"])
def test_mutation_inventory_rejects_malformed_source(tmp_path: Path, contents: bytes) -> None:
    _write_run(tmp_path)
    source = tmp_path / "mutants/auto_bi/agent/sql_guard.py"
    source.write_bytes(contents)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert str(source) in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "contents",
    [
        b"[",
        b"\xff",
        b"[tool.mutmut]\n",
        b'[tool.mutmut]\nonly_mutate = "auto_bi/agent/sql_guard.py"\n',
        b"[tool.mutmut]\nonly_mutate = []\n",
        b"[tool.mutmut]\nonly_mutate = [1]\n",
        b'[tool.mutmut]\nonly_mutate = ["../outside.py"]\n',
        b'[tool.mutmut]\nonly_mutate = ["auto_bi"]\n',
        b'[tool.mutmut]\nonly_mutate = ["same.py", "same.py"]\n',
    ],
)
def test_mutation_inventory_rejects_malformed_pyproject(tmp_path: Path, contents: bytes) -> None:
    _write_run(tmp_path)
    (tmp_path / "pyproject.toml").write_bytes(contents)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert "pyproject.toml" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "target",
    [
        r"\outside.py",
        "C:outside.py",
        "C:/outside.py",
        "/outside.py",
        r"\\server\share\outside.py",
        r"..\outside.py",
    ],
)
def test_mutation_inventory_rejects_rooted_or_drive_relative_target(
    tmp_path: Path, target: str
) -> None:
    _write_run(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mutmut]\nonly_mutate = " + json.dumps([target]) + "\n", encoding="utf-8"
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"only_mutate target must be a relative .py file: {target}" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("target", ["pkg/*.py", "pkg/?.py", "pkg/[ab].py"])
def test_mutation_inventory_rejects_target_patterns(tmp_path: Path, target: str) -> None:
    _write_run(tmp_path, {"pkg.a.x_run": [1]}, _stats(killed=1))
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mutmut]\nonly_mutate = " + json.dumps([target]) + "\n", encoding="utf-8"
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"only_mutate patterns are not supported: {target}" in result.stderr
    assert "missing" not in result.stderr
    assert "does not belong to a target" not in result.stderr
    assert "Traceback" not in result.stderr


def test_mutation_inventory_rejects_duplicate_result_in_target_meta(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1]}, _stats(killed=1))
    meta = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    key = f'"{FUNCTION}__mutmut_1": 1'
    meta.write_text('{"exit_code_by_key": {' + key + ", " + key + "}}", encoding="utf-8")
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert f"duplicate JSON key: {FUNCTION}__mutmut_1" in result.stderr


@pytest.mark.parametrize("field", list(_stats()))
def test_mutation_stats_reject_disagreement(tmp_path: Path, field: str) -> None:
    stats = _stats(killed=1, timeout=1)
    stats[field] += 1
    _write_run(tmp_path, stats=stats)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert field in result.stderr
    assert "does not match" in result.stderr


def test_mutation_stats_reject_empty_run(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: []}, _stats())
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert "total=0" in result.stderr


@pytest.mark.parametrize(
    "missing", ["mutants", "baseline.json", "mutmut-cicd-stats.json", "meta", "pyproject.toml"]
)
def test_mutation_stats_reject_missing_inputs(tmp_path: Path, missing: str) -> None:
    _write_run(tmp_path)
    if missing == "mutants":
        (tmp_path / "mutants").rename(tmp_path / "unused")
    elif missing == "meta":
        next((tmp_path / "mutants").rglob("*.meta")).unlink()
    else:
        (tmp_path / missing).unlink()
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert missing in result.stderr


@pytest.mark.parametrize("filename", ["baseline.json", "mutmut-cicd-stats.json", "meta"])
@pytest.mark.parametrize("contents", [b"{", b"[]", b"{}", b"\xff"])
def test_mutation_stats_reject_malformed_inputs(
    tmp_path: Path, filename: str, contents: bytes
) -> None:
    _write_run(tmp_path)
    path = (
        next((tmp_path / "mutants").rglob("*.meta")) if filename == "meta" else tmp_path / filename
    )
    path.write_bytes(contents)
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert filename in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "counts",
    [
        {"survived": 0},
        {"survived": -1},
        {"survived": True},
        {"no_tests": 1.5},
        {"no_tests": "1"},
        {"killed": 1},
        {},
        [],
    ],
)
def test_mutation_stats_reject_malformed_baseline_counts(tmp_path: Path, counts: object) -> None:
    _write_run(tmp_path)
    (tmp_path / "baseline.json").write_text(
        json.dumps({"measured": "test", "functions": {FUNCTION: counts}}), encoding="utf-8"
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert "baseline" in result.stderr


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_mutation_stats_reject_malformed_stats_counts(tmp_path: Path, value: object) -> None:
    _write_run(tmp_path)
    stats = dict(_stats(killed=1, timeout=1), killed=value)
    (tmp_path / "mutmut-cicd-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert "killed must be a non-negative integer" in result.stderr


@pytest.mark.parametrize(
    "results",
    [
        [],
        {"not_a_mutant": 1},
        {f"{FUNCTION}__mutmut_1": True},
        {f"{FUNCTION}__mutmut_1": "1"},
        {f"{FUNCTION}__mutmut_1": 1.0},
    ],
)
def test_mutation_stats_reject_malformed_meta_results(tmp_path: Path, results: object) -> None:
    _write_run(tmp_path)
    next((tmp_path / "mutants").rglob("*.meta")).write_text(
        json.dumps({"exit_code_by_key": results}), encoding="utf-8"
    )
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert ".meta" in result.stderr


def test_mutation_stats_reject_duplicate_mutants_across_meta_files(tmp_path: Path) -> None:
    _write_run(tmp_path, {FUNCTION: [1], OTHER_FUNCTION: [1]}, _stats(killed=2))
    meta = tmp_path / "mutants/auto_bi/agent/sql_guard.py.meta"
    (tmp_path / "mutants/auto_bi/ir/validate.py.meta").write_bytes(meta.read_bytes())
    result = _run_stats_check(tmp_path)
    assert result.returncode == 1
    assert "duplicate mutant" in result.stderr


def test_committed_mutation_baseline_is_well_formed() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules = {
        path.removesuffix(".py").replace("/", ".")
        for path in config["tool"]["mutmut"]["only_mutate"]
    }
    baseline = json.loads((ROOT / "scripts" / "mutation_baseline.json").read_text(encoding="utf-8"))
    assert set(baseline) == {"measured", "functions"}
    assert isinstance(baseline["measured"], str) and baseline["measured"].strip()
    functions = baseline["functions"]
    assert isinstance(functions, dict) and functions
    assert list(functions) == sorted(functions)
    for function, counts in functions.items():
        module, name = function.rsplit(".", 1)
        assert module in modules
        assert name.startswith(("x_", "x\u01c1"))
        assert "__mutmut_" not in function
        assert isinstance(counts, dict) and counts
        assert set(counts) <= {"survived", "no_tests"}
        assert list(counts) == sorted(counts)
        assert all(type(value) is int and value > 0 for value in counts.values())

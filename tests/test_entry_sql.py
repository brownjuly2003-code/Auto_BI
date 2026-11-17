"""E1 parser refusals and SQL-free diagnostics on every verify entry point."""

from __future__ import annotations

import json
import logging
import sys

import pytest
import sqlglot

from auto_bi import cli, github_action
from auto_bi.agent import sql_guard
from auto_bi.decision import impact as chart_impact
from auto_bi.decision import use_cases
from auto_bi.evidence.manifest import verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.mcp.tools import ToolBox
from auto_bi.verification import environment_probe, release_gate
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    CREATED_AT,
    Case,
    _formula_change,
    _write_yaml,
    dashboard_spec,
    environment,
)
from tests.test_github_action import _environ
from tests.test_mcp_tools import _call, _refs

SECRET = "private-sql-marker"
BAD_SQL = [
    pytest.param("SELECT '" + SECRET, "TokenError", id="unclosed-quote"),
    pytest.param(
        "SELECT \"region\" ('North') FROM dm.sales_daily", "AttributeError", id="missing-in"
    ),
    pytest.param("SELECT FROM", "ParseError", id="parse-error"),
]


@pytest.mark.parametrize("sql, error_class", BAD_SQL)
@pytest.mark.parametrize(
    "entry", [sql_guard.guard_sql, sql_guard.extract_table_refs, sql_guard.extract_column_refs]
)
def test_real_parser_errors_are_safe_guard_refusals(sql, error_class, entry):
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        entry(sql)
    assert type(caught.value.__cause__).__name__ == error_class
    assert error_class in str(caught.value)
    assert "sqlglot.parse" in str(caught.value)
    assert sql not in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("error_class", [ValueError, IndexError, AttributeError])
def test_other_parser_failures_are_not_silently_ignored(monkeypatch, error_class):
    def fail(*args, **kwargs):
        raise error_class(SECRET)

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard.guard_sql("SELECT 1")
    assert str(caught.value) == (
        f"SQL does not parse: {error_class.__name__} at sqlglot.parse; position unavailable"
    )
    assert isinstance(caught.value.__cause__, error_class)


def test_our_own_post_parse_error_propagates(monkeypatch):
    def fail(root):
        raise AttributeError("our normalization bug")

    monkeypatch.setattr(sql_guard, "_normalize_postgres_root_subquery", fail)
    with pytest.raises(AttributeError, match="our normalization bug"):
        sql_guard.guard_sql("(SELECT 1)", dialect="postgres")


def raw_case(tmp_path, sql):
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    spec = dashboard_spec()
    spec["charts"][0]["viz"] = "table"
    spec["charts"][0]["query"] = {"table": "dm.sales_daily", "raw_sql": sql}
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)
    return case


def call_entry(case, entry, capsys, monkeypatch):
    if entry == "mcp":
        report, is_error = _call(
            ToolBox(evidence_dir=case.root / "evidence"),
            "verify",
            **_refs(case),
            created_at=CREATED_AT,
        )
        assert not is_error
        return report
    if entry == "cli":
        assert cli.main(_verify_argv(case, "--json")) == 4
        captured = capsys.readouterr()
        assert SECRET not in captured.err
        return json.loads(captured.out)
    reports = []

    def run(argv, env):
        with monkeypatch.context() as scoped:
            for key, value in env.items():
                scoped.setenv(key, value)
            code = cli.main(list(argv))
        captured = capsys.readouterr()
        reports.append(json.loads(captured.out))
        assert SECRET not in captured.err
        return code, captured.out.encode(), captured.err.encode()

    env = _environ(
        case.root,
        contracts=str(case.contracts),
        base=str(case.base),
        candidate=str(case.candidate),
        dashboards=str(case.dashboards),
        evidence=str(case.root / "evidence"),
        comment="false",
        **{"created-at": CREATED_AT},
    )
    assert github_action.main(env, runner=run) == 4
    return reports[0]


@pytest.mark.parametrize("entry", ["cli", "action", "mcp"])
@pytest.mark.parametrize("sql, error_class", BAD_SQL)
def test_bad_chart_is_a_blocking_finding_and_verified_bundle(
    tmp_path, capsys, monkeypatch, entry, sql, error_class
):
    case = raw_case(tmp_path, sql)
    report = call_entry(case, entry, capsys, monkeypatch)
    assert report["verdict"] == "block"
    check = next(c for c in report["checks"] if c["check_id"] == "bi.contract")
    assert check["summary_code"] == "bi.spec_invalid"
    root = case.root / "evidence" / report["run_id"]
    payload = json.loads((root / "checks/check-bi.contract.json").read_text(encoding="utf-8"))
    errors = payload["invalid"][0]["errors"]
    assert any(error_class in error and "sqlglot.parse" in error for error in errors)
    assert payload["compiled"] == []
    assert sql not in json.dumps(errors)
    assert SECRET not in json.dumps(errors)
    impact = json.loads((root / "checks/check-impact.products.json").read_text(encoding="utf-8"))
    assert "chart.opaque_sql" in {gap["code"] for gap in impact["chart_gaps"]}
    assert verify_bundle(FilesystemArtifactStore(root.parent), report["run_id"]) == []
    assert SECRET not in capsys.readouterr().err


@pytest.mark.parametrize("error_class", [ValueError, IndexError])
def test_injected_parser_error_still_writes_finding_and_bundle(
    tmp_path, capsys, monkeypatch, error_class
):
    sql = "SELECT '" + SECRET + "' FROM dm.sales_daily"
    case = raw_case(tmp_path, sql)
    original = sqlglot.parse

    def parse(text, *args, **kwargs):
        if text == sql:
            raise error_class(SECRET)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(sqlglot, "parse", parse)
    report = call_entry(case, "cli", capsys, monkeypatch)
    root = case.root / "evidence" / report["run_id"]
    payload = json.loads((root / "checks/check-bi.contract.json").read_text(encoding="utf-8"))
    assert error_class.__name__ in payload["invalid"][0]["errors"][0]
    assert SECRET not in json.dumps(payload)
    assert verify_bundle(FilesystemArtifactStore(root.parent), report["run_id"]) == []


@pytest.fixture(params=["lastResort", "configured"])
def noisy_sqlglot(capsys, request):
    log = logging.getLogger("sqlglot")
    state = log.handlers[:], log.propagate, log.disabled, log.level
    # Includes an already configured handler; a NullHandler alone cannot silence it.
    log.handlers = [logging.StreamHandler(sys.stderr)] if request.param == "configured" else []
    log.propagate = request.param == "configured"
    log.disabled = False
    log.setLevel(logging.WARNING)
    try:
        # Prove the fixture would leak real parser SQL without the verify boundary.
        sqlglot.parse(f"SHOW '{SECRET}'")
        assert SECRET in capsys.readouterr().err
        yield log
    finally:
        log.handlers, log.propagate, log.disabled, log.level = state


@pytest.mark.parametrize("entry", ["cli", "action", "mcp"])
def test_real_unsupported_sql_warning_is_silent_on_every_door(
    tmp_path, capsys, monkeypatch, noisy_sqlglot, entry
):
    case = raw_case(tmp_path, f"SHOW '{SECRET}\n::error::untrusted'")
    call_entry(case, entry, capsys, monkeypatch)
    assert SECRET not in capsys.readouterr().err
    assert not noisy_sqlglot.disabled


@pytest.mark.parametrize(
    "phase",
    ["load_contracts", "_snapshot", "_asset_repository", "_load_results", "_probe", "run_gate"],
)
def test_verify_silences_parser_even_before_gate_and_in_probe(
    tmp_path, monkeypatch, capsys, noisy_sqlglot, phase
):
    case = Case(tmp_path).with_candidate().with_asset()
    original = getattr(use_cases, phase)
    visits = []

    def warning(*args, **kwargs):
        visits.append(phase)
        sqlglot.parse(f"SHOW '{SECRET}'")
        return original(*args, **kwargs)

    monkeypatch.setattr(use_cases, phase, warning)
    case.run()
    assert visits
    assert SECRET not in capsys.readouterr().err
    assert not noisy_sqlglot.disabled


@pytest.mark.parametrize(
    "owner, name",
    [
        (chart_impact._ChartResolver, "_column_reads"),
        (chart_impact._ChartResolver, "_parsed"),
        (release_gate, "compile_metric_queries"),
        (release_gate, "validate_spec"),
        (release_gate, "_compile"),
        (environment_probe, "_named_columns"),
    ],
    ids=["impact-columns", "impact-tree", "reference-sql", "bi-guard", "bi-compile", "probe"],
)
def test_each_sql_path_inside_verify_keeps_parser_diagnostics_off_stderr(
    tmp_path, monkeypatch, capsys, noisy_sqlglot, owner, name
):
    case = raw_case(tmp_path, "SELECT region, revenue FROM dm.sales_daily").with_asset()
    original = getattr(owner, name)
    visits = []

    def warning(*args, **kwargs):
        visits.append(name)
        sqlglot.parse(f"SHOW '{SECRET}'")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, warning)

    def probe(questions):
        # Exercise the probe's separate parse_one call without a database connection.
        assert environment_probe._named_columns("SELECT region FROM dm.sales_daily", "clickhouse")
        return environment()

    case.run(probe=probe)
    assert visits, name
    assert SECRET not in capsys.readouterr().err
    assert not noisy_sqlglot.disabled


def test_logger_state_is_restored_when_verify_raises(monkeypatch, noisy_sqlglot):
    def fail(*args):
        raise RuntimeError("our bug")

    monkeypatch.setattr(use_cases, "load_contracts", fail)
    with pytest.raises(RuntimeError, match="our bug"):
        use_cases.verify(use_cases.VerifyRequest([], "base", "head", "unused", CREATED_AT))
    assert not noisy_sqlglot.disabled


def test_input_loader_parser_warning_cannot_escape_verify(monkeypatch, capsys, noisy_sqlglot):
    def fail(*args):
        sqlglot.parse(f"SHOW '{SECRET}'")
        raise RuntimeError("our bug")

    monkeypatch.setattr(use_cases, "load_contracts", fail)
    with pytest.raises(RuntimeError, match="our bug"):
        use_cases.verify(use_cases.VerifyRequest([], "base", "head", "unused", CREATED_AT))
    assert SECRET not in capsys.readouterr().err
    assert not noisy_sqlglot.disabled


def test_verify_preserves_an_already_disabled_logger(monkeypatch, noisy_sqlglot):
    noisy_sqlglot.disabled = True

    def fail(*args):
        raise RuntimeError("our bug")

    monkeypatch.setattr(use_cases, "load_contracts", fail)
    with pytest.raises(RuntimeError, match="our bug"):
        use_cases.verify(use_cases.VerifyRequest([], "base", "head", "unused", CREATED_AT))
    assert noisy_sqlglot.disabled


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize(
    "error_class",
    [sqlglot.errors.TokenError, AttributeError, ValueError, IndexError, RecursionError],
)
@pytest.mark.parametrize(
    "entry", [sql_guard.guard_sql, sql_guard.extract_table_refs, sql_guard.extract_column_refs]
)
def test_parser_failure_boundary_on_both_dialects(monkeypatch, dialect, error_class, entry):
    failure = error_class(SECRET)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        entry("SELECT 1", dialect=dialect)
    assert caught.value.__cause__ is failure
    assert error_class.__name__ in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("error_class", [KeyboardInterrupt, SystemExit])
def test_process_control_is_not_a_guard_finding(monkeypatch, error_class):
    def fail(*args, **kwargs):
        raise error_class

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(error_class):
        sql_guard.guard_sql("SELECT 1")


def test_parse_error_coordinates_are_kept_without_tokens():
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard.guard_sql("SELECT FROM")
    assert str(caught.value) == "SQL does not parse: ParseError at sqlglot.parse; line 1, column 11"


def test_postgres_secondary_tokenizer_is_a_narrow_guard_boundary(monkeypatch):
    failure = ValueError(SECRET)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sqlglot.Dialect.get_or_raise("postgres").__class__, "tokenize", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard._postgres_sort_token_info("SELECT 1", "postgres")
    assert caught.value.__cause__ is failure
    assert str(caught.value) == (
        "SQL does not parse: ValueError at sqlglot.tokenize; position unavailable"
    )

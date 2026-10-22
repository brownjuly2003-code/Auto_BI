"""A runtime guard refusal is an operational error at the CLI probe boundary.

Rejected SQL is supplied at the PlanQuestion seam: these tests do not claim that
the current typed compiler generates it from an ordinary metric contract.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from sqlglot.errors import TokenError

from auto_bi import cli
from auto_bi.agent.sql_guard import SQLGuardError
from auto_bi.decision import use_cases
from auto_bi.introspect import clickhouse, greenplum
from auto_bi.verification import runtime_probe
from auto_bi.verification.environment import EnvironmentQuestions
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import EXIT_ERROR
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case, budgeted_contract
from tests.test_environment_probe import questions
from tests.test_runtime_probe import FakeClickHouse, FakePostgres, question
from tests.test_unanswered_engine import (
    ENGINES,
    _answered_by,
    _catalog,
    _configure,
    _family,
    _settings,
)

REFUSAL = "error: cannot execute environment query: SQLGuardError\n"
REJECTED_SQL = [
    pytest.param("SELECT * FROM remote('private-input', dm, sales_daily)", id="remote-source"),
    pytest.param("SELECT 'private-input'\nFROM", id="parse-error"),
    pytest.param("SELECT 1; SELECT 'private-input'", id="multiple-statements"),
    pytest.param("DROP private_input_marker", id="command-fallback"),
]


def _runtime_clients(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeClickHouse, FakePostgres]:
    import clickhouse_connect
    import psycopg

    ch = FakeClickHouse()
    pg = FakePostgres()
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kwargs: ch)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: pg)
    return ch, pg


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("sql", REJECTED_SQL)
def test_cli_reports_real_runtime_guard_refusals_without_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    sql: str,
) -> None:
    # Pytest's root handlers would otherwise hide the ordinary CLI lastResort output.
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(logging.getLogger("sqlglot"), "handlers", [])
    _configure(monkeypatch, 8123)
    catalog = _catalog(engine)
    built = _answered_by(monkeypatch, engine, catalog)
    ch, pg = _runtime_clients(monkeypatch)
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(timeout_ms=2500))
    original = use_cases.questions_for
    asked: list[EnvironmentQuestions] = []

    def with_rejected_plan(product: Any, candidate: Any) -> EnvironmentQuestions:
        questions = original(product, candidate)
        assert len(questions.plans) == 1
        questions.plans[0].sql = sql
        questions.plans[0].dialect = _family(engine)
        asked.append(questions)
        return questions

    monkeypatch.setattr(use_cases, "questions_for", with_rejected_plan)

    assert cli.main([*_verify_argv(case), "--dwh", engine]) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == REFUSAL
    assert len(asked) == 1
    assert built == [_family(engine)]
    assert any(sql in query and query.startswith("EXPLAIN") for query in catalog.asked)
    assert ch.calls == []
    assert pg.transactions == 0
    assert pg.cursor_value.statements == []
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("propagate", [False, True])
def test_configured_parser_logging_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    engine: str,
    propagate: bool,
) -> None:
    _configure(monkeypatch, 8123)
    _answered_by(monkeypatch, engine, _catalog(engine))
    _runtime_clients(monkeypatch)
    parser = logging.getLogger("sqlglot")
    output = StringIO()
    configured = logging.StreamHandler(output)
    monkeypatch.setattr(parser, "handlers", [configured])
    monkeypatch.setattr(parser, "propagate", propagate)
    supplied = questions()
    supplied.plans = [question(timeout_ms=2500)]
    supplied.plans[0].sql = "DROP private_input_marker"
    supplied.plans[0].dialect = _family(engine)

    with caplog.at_level(logging.INFO, logger="sqlglot"):
        probe = cli._environment_probe(engine)
        assert probe is not None
        for _ in range(2):
            with pytest.raises(use_cases.VerifyError, match="SQLGuardError"):
                probe(supplied)

        assert parser.level == logging.INFO
        assert parser.propagate is propagate
        assert parser.handlers[0] is configured
        assert len(parser.handlers) == 2
        assert isinstance(parser.handlers[1], logging.NullHandler)

    assert output.getvalue().count("private_input_marker") == 2
    assert len(caplog.records) == (2 if propagate else 0)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, TokenError])
def test_unrelated_collector_errors_keep_their_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, error_type: type[Exception]
) -> None:
    _configure(monkeypatch, 8123)
    error = error_type("cannot execute environment query: SQLGuardError")

    def broken(_sql: str) -> list[dict[str, Any]]:
        raise error

    _answered_by(monkeypatch, engine, broken)
    case = Case(tmp_path).with_candidate()

    with pytest.raises(error_type) as caught:
        cli.main([*_verify_argv(case), "--dwh", engine])

    assert caught.value is error
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ENGINES)
def test_guard_handler_does_not_cover_the_connection_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    parser = logging.getLogger("sqlglot")
    monkeypatch.setattr(parser, "handlers", [])
    _configure(monkeypatch, 8123)
    error = SQLGuardError("unexpected factory failure")

    def broken(_settings: Any) -> Any:
        raise error

    module, name = (
        (clickhouse, "make_run_query")
        if _family(engine) == "clickhouse"
        else (greenplum, "make_run_query_pg")
    )
    monkeypatch.setattr(module, name, broken)
    case = Case(tmp_path).with_candidate()

    with pytest.raises(SQLGuardError) as caught:
        cli.main([*_verify_argv(case), "--dwh", engine])

    assert caught.value is error
    assert parser.handlers == []
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
def test_direct_collector_retains_the_real_guard_exception(
    monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    ch, pg = _runtime_clients(monkeypatch)
    supplied = questions()
    supplied.plans = [question(timeout_ms=2500)]
    supplied.plans[0].sql = "SELECT * FROM remote('private-input', dm, sales_daily)"
    supplied.plans[0].dialect = engine
    run_cost = (
        runtime_probe.make_clickhouse_runtime_probe(_settings(8123))
        if engine == "clickhouse"
        else runtime_probe.make_postgres_runtime_probe(_settings(8123))
    )

    with pytest.raises(SQLGuardError, match="forbidden table function"):
        collect(_catalog(engine), supplied, engine=engine, target="stand", run_cost=run_cost)

    assert ch.calls == []
    assert pg.transactions == 0

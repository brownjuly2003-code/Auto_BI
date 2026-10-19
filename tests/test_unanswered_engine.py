"""An engine `decision verify --dwh` was to ask and that did not answer ends the run in one line.

The probe of §8.2 step 7 connected when the command built it, before the use case was called,
so a run refused for its moment or for an input had opened a session first. What the driver
raised — a connection refused, a session lost — went up as it was: exit code 1 and a traceback
of 23 lines (psycopg) to 154 (clickhouse_connect, its own warning printed above). Building the
probe now connects to nothing; what the driver raises while the run asks is a usage error of
one line, exit code 2, with no bundle; and what a driver logs is kept off the stream a command
answers on (`docs/specs/unanswered-engine.md`).
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import clickhouse_connect
import psycopg
import pytest
from clickhouse_connect.driver.exceptions import (
    DatabaseError,
    OperationalError,
    StreamFailureError,
)

from auto_bi import cli, config
from auto_bi.cli import main
from auto_bi.config import Settings
from auto_bi.decision import use_cases
from auto_bi.decision.use_cases import VerifyError
from auto_bi.introspect import clickhouse, greenplum
from auto_bi.verification.environment import EnvironmentQuestions
from auto_bi.verification.model import EXIT_ERROR
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT, Case, budgeted_contract
from tests.test_environment_probe import FakeEngine, clickhouse_engine, postgres_engine
from tests.test_stated_run_facts import _capture_argv, _verify
from tests.test_unreadable_run_inputs import ROWS, _case

NL = chr(10)
TAB = chr(9)

#: Every engine `--dwh` takes.
ENGINES = ["clickhouse", "postgres", "greenplum", "greengage"]

RunQuery = Callable[[str], list[dict[str, Any]]]


def _family(engine: str) -> str:
    """The driver an engine is asked through: Greenplum and Greengage speak PostgreSQL's."""
    return "clickhouse" if engine == "clickhouse" else "postgres"


@pytest.fixture
def closed_port() -> int:
    """A port of the loopback address that was free a moment ago: a session is refused at once.

    Not one held bound and left without `listen`: macOS drops what is sent to such a port, and
    a session to it is refused only when the connection times out (psycopg: `could not receive
    data from server: Operation timed out`, after more than a minute).
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def _settings(port: int) -> Settings:
    """Both driver families configured to *port* of the loopback address, database `dm`."""
    return Settings(
        ch_host="127.0.0.1",
        ch_port=port,
        ch_user="gate_ro",
        ch_password="",
        ch_database="dm",
        gp_host="127.0.0.1",
        gp_port=port,
        gp_user="gate_ro",
        gp_password="",
        gp_database="dm",
    )


def _configure(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: _settings(port))


class _Sessions:
    """Counts the sessions the drivers are asked to open; each is still opened by its driver."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[str] = []
        get_client = clickhouse_connect.get_client
        connect = psycopg.connect

        def clickhouse_session(*args: Any, **kwargs: Any) -> Any:
            self.opened.append("clickhouse")
            return get_client(*args, **kwargs)

        def postgres_session(*args: Any, **kwargs: Any) -> Any:
            self.opened.append("postgres")
            return connect(*args, **kwargs)

        monkeypatch.setattr(clickhouse_connect, "get_client", clickhouse_session)
        monkeypatch.setattr(psycopg, "connect", postgres_session)


def _answered_by(monkeypatch: pytest.MonkeyPatch, engine: str, run_query: RunQuery) -> list[str]:
    """Have *run_query* answer the catalog of *engine*; the list holds each session built."""
    built: list[str] = []

    def session(_settings: Settings) -> RunQuery:
        built.append(_family(engine))
        return run_query

    if _family(engine) == "clickhouse":
        monkeypatch.setattr(clickhouse, "make_run_query", session)
    else:
        monkeypatch.setattr(greenplum, "make_run_query_pg", session)
    return built


def _catalog(engine: str) -> FakeEngine:
    return clickhouse_engine() if _family(engine) == "clickhouse" else postgres_engine()


def _one_line(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    """What a command printed: its standard output, and the one line of its error stream."""
    captured = capsys.readouterr()
    assert captured.err.count(NL) == 1 and captured.err.endswith(NL), captured.err
    line = captured.err[:-1]
    assert line.splitlines() == [line]
    return captured.out, line


class _FallsOver:
    """Answers as *inner* until it is asked a question holding *marker*; raises *error* then."""

    def __init__(self, inner: FakeEngine, marker: str, error: Exception) -> None:
        self.inner = inner
        self.marker = marker
        self.error = error
        self.fell = False

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        if self.marker in sql:
            self.fell = True
            raise self.error
        return self.inner(sql)


# --- building the probe ---------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_building_the_probe_opens_no_session(
    monkeypatch: pytest.MonkeyPatch, closed_port: int, engine: str
) -> None:
    sessions = _Sessions(monkeypatch)
    _configure(monkeypatch, closed_port)

    probe = cli._environment_probe(engine)

    assert probe is not None
    assert sessions.opened == []
    with pytest.raises(use_cases.EnvironmentProbeError):
        probe(EnvironmentQuestions())
    assert sessions.opened == [_family(engine)]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("refused", ["the moment", "the results file"])
def test_a_run_refused_before_its_questions_opens_no_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    engine: str,
    refused: str,
) -> None:
    sessions = _Sessions(monkeypatch)
    _configure(monkeypatch, closed_port)
    case = _case(tmp_path)
    argv = _verify_argv(case)
    if refused == "the moment":
        argv[argv.index("--created-at") + 1] = "yesterday"
    else:
        assert case.results is not None
        case.results.unlink()

    assert main([*argv, "--dwh", engine]) == EXIT_ERROR

    out, line = _one_line(capsys)
    assert out == ""
    assert not line.startswith("error: cannot ask")
    if refused == "the moment":
        assert "--created-at must be an RFC 3339 UTC timestamp ending in Z" in line
    assert sessions.opened == []
    assert not (case.root / "evidence").exists()


# --- an engine that does not answer ---------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_an_engine_that_is_not_reached_ends_the_run_in_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    engine: str,
) -> None:
    _configure(monkeypatch, closed_port)
    case = _case(tmp_path)

    assert main([*_verify_argv(case), "--dwh", engine]) == EXIT_ERROR

    out, line = _one_line(capsys)
    assert out == ""
    head = f"error: cannot ask {engine} at 127.0.0.1:{closed_port}/dm: OperationalError: "
    assert line.startswith(head) and line != head
    assert not (case.root / "evidence").exists()


MIDWAY = [
    pytest.param(
        "clickhouse",
        "FROM system.tables",
        DatabaseError(
            "Code: 159. DB::Exception: Timeout exceeded: elapsed 5.001 seconds, maximum: 5. "
            "(TIMEOUT_EXCEEDED) (version 24.8.14.39 (official build))" + NL
        ),
        "DatabaseError: Code: 159. DB::Exception: Timeout exceeded: elapsed 5.001 seconds, "
        "maximum: 5. (TIMEOUT_EXCEEDED) (version 24.8.14.39 (official build))",
        id="clickhouse, a question the engine failed",
    ),
    pytest.param(
        "clickhouse",
        "FROM system.columns",
        StreamFailureError("Stream ended unexpectedly (connection closed by server)"),
        "StreamFailureError: Stream ended unexpectedly (connection closed by server)",
        id="clickhouse, a result cut off midway",
    ),
    pytest.param(
        "clickhouse",
        "FROM system.columns",
        OperationalError(),
        "OperationalError",
        id="clickhouse, a session lost with nothing said",
    ),
    pytest.param(
        "postgres",
        "FROM pg_tables",
        psycopg.OperationalError(
            "server closed the connection unexpectedly" + NL + TAB + "This probably means the "
            "server terminated abnormally" + NL + TAB + "before or while processing the request."
        ),
        "OperationalError: server closed the connection unexpectedly This probably means the "
        "server terminated abnormally before or while processing the request.",
        id="postgres, a session lost",
    ),
    pytest.param(
        "greengage",
        "format_type(a.atttypid",
        psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
        "QueryCanceled: canceling statement due to statement timeout",
        id="greengage, a question cancelled",
    ),
    pytest.param(
        "greenplum",
        "FROM pg_attribute",
        psycopg.errors.DataCorrupted("invalid page in block 0 of relation base/16384/1249"),
        "DataCorrupted: invalid page in block 0 of relation base/16384/1249",
        id="greenplum, a catalog page the engine could not read",
    ),
]


@pytest.mark.parametrize("engine,marker,error,said", MIDWAY)
def test_an_engine_that_falls_over_midway_ends_the_run_in_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    engine: str,
    marker: str,
    error: Exception,
    said: str,
) -> None:
    _configure(monkeypatch, closed_port)
    falls = _FallsOver(_catalog(engine), marker, error)
    _answered_by(monkeypatch, engine, falls)
    case = _case(tmp_path)

    assert main([*_verify_argv(case), "--dwh", engine]) == EXIT_ERROR

    out, line = _one_line(capsys)
    assert out == ""
    assert line == f"error: cannot ask {engine} at 127.0.0.1:{closed_port}/dm: {said}"
    assert falls.fell and falls.inner.asked
    assert not (case.root / "evidence").exists()


def test_a_runtime_session_that_is_refused_ends_the_run_in_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
) -> None:
    """The catalog answered; the exact query a declared timeout is measured by was to run on a
    session of its own, and that session was refused."""
    sessions = _Sessions(monkeypatch)
    _configure(monkeypatch, closed_port)
    _answered_by(monkeypatch, "clickhouse", clickhouse_engine())
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(timeout_ms=3000))
    case.with_asset().with_results(ROWS)

    assert main([*_verify_argv(case), "--dwh", "clickhouse"]) == EXIT_ERROR

    out, line = _one_line(capsys)
    assert out == ""
    head = f"error: cannot ask clickhouse at 127.0.0.1:{closed_port}/dm: OperationalError: "
    assert line.startswith(head)
    assert sessions.opened == ["clickhouse"]
    assert not (case.root / "evidence").exists()


# --- an engine that answers -----------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_a_run_the_engine_answers_opens_one_session_and_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    engine: str,
) -> None:
    _configure(monkeypatch, closed_port)
    catalog = _catalog(engine)
    built = _answered_by(monkeypatch, engine, catalog)
    case = _case(tmp_path)

    assert main([*_verify_argv(case), "--dwh", engine]) != EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status   completed" in captured.out
    assert built == [_family(engine)]
    assert catalog.asked
    assert list((case.root / "evidence").iterdir())


REFUSALS = [
    pytest.param(
        "clickhouse",
        "FROM system.tables",
        DatabaseError("Code: 497. DB::Exception: gate_ro: Not enough privileges. (ACCESS_DENIED)"),
        id="clickhouse, access denied",
    ),
    pytest.param(
        "postgres",
        "FROM pg_tables",
        psycopg.errors.InsufficientPrivilege("permission denied for table pg_tables"),
        id="postgres, insufficient privilege",
    ),
]


@pytest.mark.parametrize("engine,marker,refusal", REFUSALS)
def test_what_the_engine_refused_is_a_fact_of_a_run_that_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    engine: str,
    marker: str,
    refusal: Exception,
) -> None:
    """The refusal is raised by the driver as any error of the driver is, and it is no error of
    the run: the engine said "not for you", which is what step 7 asks."""
    _configure(monkeypatch, closed_port)
    refuses = _FallsOver(_catalog(engine), marker, refusal)
    _answered_by(monkeypatch, engine, refuses)
    case = _case(tmp_path)

    assert main([*_verify_argv(case), "--dwh", engine]) != EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status   completed" in captured.out
    assert refuses.fell
    assert list((case.root / "evidence").iterdir())


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
def test_what_the_driver_did_not_raise_is_raised_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed_port: int, engine: str
) -> None:
    def broken(_sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("a fault of ours")

    _configure(monkeypatch, closed_port)
    _answered_by(monkeypatch, engine, broken)
    case = _case(tmp_path)

    with pytest.raises(RuntimeError, match="a fault of ours"):
        main([*_verify_argv(case), "--dwh", engine])
    assert not (case.root / "evidence").exists()


# --- the use case and its error -------------------------------------------------------------


def test_the_use_case_ends_a_run_whose_engine_did_not_answer_as_a_usage_error(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    error = OperationalError("connection refused" + NL + TAB + "by the stand")

    def probe(_questions: EnvironmentQuestions) -> Any:
        raise use_cases.EnvironmentProbeError("clickhouse", "stand:8123/dm", error)

    with pytest.raises(VerifyError) as caught:
        _verify(case, probe=probe)

    assert str(caught.value) == (
        "cannot ask clickhouse at stand:8123/dm: OperationalError: connection refused by the stand"
    )
    assert caught.value.__suppress_context__
    assert not (case.root / "evidence").exists()


SAID = [
    pytest.param(
        OperationalError("refused" + NL + TAB + "by the stand  at once" + chr(13) + NL),
        "OperationalError: refused by the stand at once",
        id="line breaks, a tab and blanks",
    ),
    pytest.param(
        psycopg.OperationalError("lost" + chr(0x2028) + "midway"),
        "OperationalError: lost midway",
        id="a line separator",
    ),
    pytest.param(StreamFailureError(), "StreamFailureError", id="nothing said"),
    pytest.param(DatabaseError("  " + NL), "DatabaseError", id="only blanks said"),
]


@pytest.mark.parametrize("error,said", SAID)
def test_what_the_driver_said_is_one_line_after_its_class(error: Exception, said: str) -> None:
    probe_error = use_cases.EnvironmentProbeError("postgres", "warehouse:5432/dm", error)

    assert str(probe_error) == f"postgres at warehouse:5432/dm: {said}"


# --- what a driver logs ---------------------------------------------------------------------


#: What builds a client of the engine: the probe of `decision verify`, the target of a capture.
BUILDERS = [
    pytest.param("_environment_probe", id="verify"),
    pytest.param("_result_capture_target", id="capture"),
]


@pytest.mark.parametrize("builder", BUILDERS)
def test_a_clickhouse_client_puts_one_null_handler_on_the_driver_log(
    monkeypatch: pytest.MonkeyPatch, closed_port: int, builder: str
) -> None:
    driver = logging.getLogger("clickhouse_connect")
    monkeypatch.setattr(driver, "handlers", [])
    _configure(monkeypatch, closed_port)

    getattr(cli, builder)("clickhouse")
    getattr(cli, builder)("clickhouse")

    assert [type(handler) for handler in driver.handlers] == [logging.NullHandler]


def test_logging_that_was_configured_still_gets_the_driver_records(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, closed_port: int
) -> None:
    driver = logging.getLogger("clickhouse_connect")
    monkeypatch.setattr(driver, "handlers", [])
    _configure(monkeypatch, closed_port)
    cli._environment_probe("clickhouse")

    with caplog.at_level(logging.WARNING):
        logging.getLogger("clickhouse_connect.driver.http_sync").warning("Unexpected Http Driver")

    assert [record.getMessage() for record in caplog.records] == ["Unexpected Http Driver"]


@pytest.mark.parametrize("engine", ["postgres", "greenplum", "greengage"])
def test_a_postgres_family_run_leaves_the_clickhouse_driver_log_alone(
    monkeypatch: pytest.MonkeyPatch, closed_port: int, engine: str
) -> None:
    driver = logging.getLogger("clickhouse_connect")
    monkeypatch.setattr(driver, "handlers", [])
    _configure(monkeypatch, closed_port)

    cli._environment_probe(engine)
    cli._result_capture_target(engine)

    assert driver.handlers == []


# --- the commands as processes --------------------------------------------------------------


def _process(argv: list[str], port: int) -> subprocess.CompletedProcess[str]:
    """The command as a process of its own, both driver families configured to *port*."""
    root = Path(cli.__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
    for family in ("CH", "GP"):
        env[f"AUTO_BI_{family}_HOST"] = "127.0.0.1"
        env[f"AUTO_BI_{family}_PORT"] = str(port)
        env[f"AUTO_BI_{family}_DATABASE"] = "dm"
    return subprocess.run(
        [sys.executable, "-m", "auto_bi.cli", *argv],
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=300,
    )


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
def test_a_verify_process_whose_engine_is_not_reached_answers_in_one_line(
    tmp_path: Path, closed_port: int, engine: str
) -> None:
    case = _case(tmp_path)

    ended = _process([*_verify_argv(case), "--dwh", engine], closed_port)

    assert (ended.returncode, ended.stdout) == (EXIT_ERROR, "")
    assert ended.stderr.count(NL) == 1 and ended.stderr.endswith(NL), ended.stderr
    head = f"error: cannot ask {engine} at 127.0.0.1:{closed_port}/dm: OperationalError: "
    assert ended.stderr.startswith(head)
    assert not (case.root / "evidence").exists()


def test_a_capture_process_whose_engine_is_not_reached_answers_in_one_line(
    tmp_path: Path, closed_port: int
) -> None:
    case = _case(tmp_path)

    ended = _process([*_capture_argv(case), "--captured-at", CREATED_AT], closed_port)

    assert (ended.returncode, ended.stdout) == (EXIT_ERROR, "")
    assert ended.stderr.count(NL) == 1 and ended.stderr.endswith(NL), ended.stderr
    head = f"error: cannot capture {ASSET_ID}: ClickHouse connection failed: "
    assert ended.stderr.startswith(head)
    assert not (case.root / "captured.yaml").exists()

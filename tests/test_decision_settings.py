"""Decision DWH commands refuse unreadable settings without exposing their values."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auto_bi import cli, config
from auto_bi.introspect import clickhouse, greenplum
from auto_bi.verification import result_probe, runtime_probe
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case
from tests.test_stated_run_facts import _capture_argv
from tests.test_unreadable_run_inputs import _case

ENGINES = ["clickhouse", "postgres", "greenplum", "greengage"]
SECRET = "private-settings-value-DO-NOT-PRINT"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use real Settings without the checkout's dotenv or the host's AUTO_BI variables."""
    for key in os.environ:
        if key.upper().startswith("AUTO_BI_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    cached_settings = config.get_settings
    cached_settings.cache_clear()
    yield
    cached_settings.cache_clear()


def _argv(case: Case, command: str, engine: str = "clickhouse") -> list[str]:
    if command == "verify":
        return _verify_argv(case, "--dwh", engine)
    return _capture_argv(case, "--dwh", engine)


def _no_factories(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append("factory")
        raise AssertionError("settings must be refused before building a DWH factory")

    for module, names in (
        (clickhouse, ["make_run_query"]),
        (greenplum, ["make_run_query_pg"]),
        (result_probe, ["make_clickhouse_result_query", "make_postgres_result_query"]),
        (runtime_probe, ["make_clickhouse_runtime_probe", "make_postgres_runtime_probe"]),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    return calls


def _refused(case: Case, command: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert cli.main(_argv(case, command)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot load settings: ")
    assert captured.err.endswith("\n") and len(captured.err.splitlines()) == 1
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err
    assert not (case.root / "evidence").exists()
    assert not (case.root / "captured.yaml").exists()
    return captured.err


@pytest.mark.parametrize("command", ["verify", "capture"])
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_bad_port_is_a_safe_usage_error_before_any_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    engine: str,
    source: str,
) -> None:
    case = _case(tmp_path)
    field = "ch_port" if engine == "clickhouse" else "gp_port"
    key = f"AUTO_BI_{field.upper()}"
    if source == "environment":
        monkeypatch.setenv(key, SECRET + "\nsecond line")
    else:
        (tmp_path / ".env").write_text(f"{key}={SECRET}\n", encoding="utf-8")
    calls = _no_factories(monkeypatch)

    assert cli.main(_argv(case, command, engine)) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: cannot load settings: ValidationError: {field} (int_parsing)\n"
    assert calls == []
    assert not (tmp_path / "evidence").exists()
    assert not (tmp_path / "captured.yaml").exists()


@pytest.mark.parametrize("command", ["verify", "capture"])
def test_multiple_validation_errors_are_bounded_and_do_not_print_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    for field in ("ch_port", "gp_port", "bi_connection_strict", "send_samples", "auth_enabled"):
        monkeypatch.setenv(f"AUTO_BI_{field.upper()}", SECRET)
    calls = _no_factories(monkeypatch)

    line = _refused(_case(tmp_path), command, capsys)

    assert line == (
        "error: cannot load settings: ValidationError: ch_port (int_parsing); "
        "gp_port (int_parsing); bi_connection_strict (bool_parsing); (+2 more)\n"
    )
    assert calls == []


@pytest.mark.parametrize("command", ["verify", "capture"])
@pytest.mark.parametrize("failure", ["permission", "encoding"])
def test_unreadable_dotenv_reports_only_the_exception_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    failure: str,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"\xff" if failure == "encoding" else b"AUTO_BI_CH_PORT=8123\n")
    if failure == "permission":
        original = builtins.open

        def denied(file: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, (str, Path)) and Path(file).resolve() == dotenv:
                raise PermissionError(13, SECRET, SECRET)
            return original(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", denied)
    calls = _no_factories(monkeypatch)

    line = _refused(_case(tmp_path), command, capsys)

    expected = "PermissionError" if failure == "permission" else "UnicodeDecodeError"
    assert line == f"error: cannot load settings: {expected}\n"
    assert calls == []


@pytest.mark.parametrize("command", ["verify", "capture"])
def test_validation_details_and_unknown_locations_are_not_disclosed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    error = ValidationError.from_exception_data(
        SECRET,
        [
            {
                "type": "value_error",
                "loc": (SECRET,),
                "input": SECRET,
                "ctx": {"error": ValueError(SECRET)},
            }
        ],
    )

    def invalid() -> config.Settings:
        raise error

    monkeypatch.setattr(config, "get_settings", invalid)
    line = _refused(_case(tmp_path), command, capsys)
    assert line == "error: cannot load settings: ValidationError: <settings> (value_error)\n"


def test_offline_verify_does_not_load_even_invalid_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTO_BI_CH_PORT", SECRET)

    def forbidden() -> config.Settings:
        raise AssertionError("offline verify must not read settings")

    monkeypatch.setattr(config, "get_settings", forbidden)
    case = _case(tmp_path)
    assert cli.main(_verify_argv(case)) == 0
    assert capsys.readouterr().err == ""
    assert list((tmp_path / "evidence").iterdir())


@pytest.mark.parametrize("command", ["verify", "capture"])
def test_unexpected_settings_failure_is_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    error = RuntimeError("implementation fault")

    def broken() -> config.Settings:
        raise error

    monkeypatch.setattr(config, "get_settings", broken)
    with pytest.raises(RuntimeError) as caught:
        cli.main(_argv(_case(tmp_path), command))
    assert caught.value is error


@pytest.mark.parametrize("command", ["verify", "capture"])
@pytest.mark.parametrize("engine", ENGINES)
def test_real_cli_process_exits_two_without_a_traceback_or_artifacts(
    tmp_path: Path, command: str, engine: str
) -> None:
    case = _case(tmp_path)
    field = "ch_port" if engine == "clickhouse" else "gp_port"
    env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]))
    env[f"AUTO_BI_{field.upper()}"] = SECRET
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    ended = subprocess.run(
        [sys.executable, "-m", "auto_bi.cli", *_argv(case, command, engine)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert (ended.returncode, ended.stdout) == (2, "")
    assert ended.stderr == f"error: cannot load settings: ValidationError: {field} (int_parsing)\n"
    assert not (tmp_path / "evidence").exists()
    assert not (tmp_path / "captured.yaml").exists()

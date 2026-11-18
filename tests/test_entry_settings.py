"""E1: every CLI settings consumer can exclude the checkout's dotenv."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from auto_bi import cli, config
from auto_bi.github_action import child_env
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case
from tests.test_stated_run_facts import _capture_argv

COMMANDS = [
    "build",
    "build-auto",
    "raw",
    "chat",
    "serve",
    "eval",
    "gaps",
    "prune",
    "introspect-ch",
    "introspect-gp",
    "inventory",
    "verify-clickhouse",
    "verify-postgres",
    "verify-greenplum",
    "verify-greengage",
    "capture-clickhouse",
    "capture-postgres",
    "capture-greenplum",
    "capture-greengage",
]
DWH_FIELDS = [
    "ch_host",
    "ch_port",
    "ch_user",
    "ch_password",
    "ch_database",
    "ch_host_from_bi",
    "ch_port_from_bi",
    "ch_host_from_datalens",
    "gp_host",
    "gp_port",
    "gp_user",
    "gp_password",
    "gp_database",
    "gp_schema",
]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in os.environ:
        if key.upper().startswith("AUTO_BI_"):
            monkeypatch.delenv(key)
    cached = config.get_settings
    cached.cache_clear()
    yield
    cached.cache_clear()


class SettingsObserved(BaseException):
    """End a CLI call before any DWH, BI, LLM or server can start."""


def argv_for(case, command):
    model = str(case.candidate / "model.yaml")
    if command.startswith("verify-"):
        return _verify_argv(case, "--dwh", command.removeprefix("verify-"))
    if command.startswith("capture-"):
        return _capture_argv(case, "--dwh", command.removeprefix("capture-"))
    if command.startswith("introspect-"):
        engine = "clickhouse" if command.endswith("-ch") else "greenplum"
        return ["introspect", "--engine", engine]
    if command == "inventory":
        return [
            "decision",
            "inventory",
            str(case.candidate),
            "--dashboard",
            "dashboard://sales/weekly_margin@7=1",
            "--output",
            str(case.root / "inventory"),
        ]
    if command == "raw":
        sql = case.root / "query.sql"
        sql.write_bytes(b"SELECT 1\n")
        args = ["raw", "--sql-file", str(sql)]
    elif command == "build-auto":
        args = ["build", "--auto", "dm.sales_daily"]
    elif command == "build":
        args = ["build", "revenue"]
    elif command == "eval":
        args = ["eval", "--suite", "golden", "--llm-mode", "live"]
    else:
        args = [command]
    return [*args, "--model-path", model]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("field", DWH_FIELDS)
def test_no_env_file_on_every_cli_consumer_and_connection_field(
    tmp_path, monkeypatch, command, field
):
    monkeypatch.chdir(tmp_path)
    default = config.Settings.model_fields[field].default
    poisoned = 6543 if isinstance(default, int) else "checkout-controlled"
    (tmp_path / ".env").write_bytes(f"AUTO_BI_{field.upper()}={poisoned}\n".encode())
    monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", "1")
    case = Case(tmp_path).with_candidate().with_asset()
    real_settings = config.get_settings

    def observe():
        settings = real_settings()
        assert getattr(settings, field) == default
        raise SettingsObserved

    monkeypatch.setattr(config, "get_settings", observe)
    monkeypatch.setattr("auto_bi.logging_setup.configure_logging", lambda *args: None)
    with pytest.raises(SettingsObserved):
        cli.main(argv_for(case, command))


@pytest.mark.parametrize("mode", [None, "0"])
def test_default_still_reads_dotenv_and_environment_wins(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_bytes(b"AUTO_BI_CH_HOST=local-file\nAUTO_BI_GP_HOST=file-pg\n")
    monkeypatch.setenv("AUTO_BI_GP_HOST", "workflow-pg")
    if mode is not None:
        monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", mode)
    settings = config.get_settings()
    assert settings.ch_host == "local-file"
    assert settings.gp_host == "workflow-pg"


def test_no_env_file_never_opens_dotenv_and_keeps_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Invalid UTF-8 would fail even if the implementation read and then discarded values.
    (tmp_path / ".env").write_bytes(b"\xff\xfe\x00")
    monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", "1")
    monkeypatch.setenv("AUTO_BI_CH_HOST", "workflow-ch")
    assert config.get_settings().ch_host == "workflow-ch"
    assert config.unknown_env_settings() == []


def test_no_env_source_is_configured_before_any_file_is_read(monkeypatch):
    from pydantic_settings import DotEnvSettingsSource

    seen = []

    def read(source):
        seen.append(source.env_file)
        assert source.env_file is None
        return {}

    monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", "1")
    monkeypatch.setenv("AUTO_BI_GP_HOST", "workflow-pg")
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_files", read)
    assert config.get_settings().gp_host == "workflow-pg"
    assert seen == [None]
    assert config.unknown_env_settings() == []


def test_settings_and_file_mode_are_cached_for_the_process(monkeypatch):
    monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", "1")
    monkeypatch.setenv("AUTO_BI_GP_HOST", "first-host")
    first = config.get_settings()
    monkeypatch.setenv("AUTO_BI_NO_ENV_FILE", "0")
    monkeypatch.setenv("AUTO_BI_GP_HOST", "second-host")
    assert config.get_settings() is first
    assert config.get_settings().gp_host == "first-host"


@pytest.mark.parametrize("supplied", [None, "0", "1"])
def test_action_forces_no_file_for_every_child(supplied):
    env = {} if supplied is None else {"AUTO_BI_NO_ENV_FILE": supplied}
    assert child_env(env)["AUTO_BI_NO_ENV_FILE"] == "1"


def test_cli_settings_consumer_inventory_is_complete():
    # A new settings-reading command needs a behavioural case above too.
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    consumers = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in {"get_settings", "_decision_settings"}
            for call in ast.walk(node)
        )
    }
    assert consumers == {
        "_build",
        "_build_raw",
        "_build_auto",
        "_chat",
        "_serve",
        "_eval",
        "_gaps",
        "_prune",
        "_introspect",
        "_decision_settings",
        "_decision_inventory",
        "_environment_probe",
        "_result_capture_target",
    }
    fields = {name for name in config.Settings.model_fields if name.startswith(("ch_", "gp_"))}
    assert fields == set(DWH_FIELDS)

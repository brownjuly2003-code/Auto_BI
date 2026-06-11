"""Scaffold smoke: packages import, settings load with defaults, CLI parses."""

import auto_bi
from auto_bi.cli import main
from auto_bi.config import Settings


def test_version() -> None:
    assert auto_bi.__version__


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.ch_port == 8123
    assert settings.gracekelly_model == "claude-sonnet-4-6"
    assert settings.send_samples is True


def test_cli_build_not_implemented_yet() -> None:
    assert main(["build", "test dashboard"]) == 1

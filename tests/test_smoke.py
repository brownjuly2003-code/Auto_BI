"""Scaffold smoke: packages import, settings load with defaults, CLI parses."""

import tomllib
from pathlib import Path

import auto_bi
from auto_bi.cli import main
from auto_bi.config import Settings


def test_version() -> None:
    assert auto_bi.__version__


def test_version_matches_pyproject() -> None:
    """Drift guard: auto_bi.__version__ must equal pyproject.toml [project].version.

    A `vX.Y.Z` tag publishes GHCR + PyPI off these two numbers (P1-7); if they disagree
    the release ships mismatched artifacts. Caught here on every PR, not only at tag time.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == auto_bi.__version__


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.ch_port == 8123
    assert settings.gracekelly_model == "claude-sonnet-5"
    assert settings.send_samples is True


def test_cli_build_requires_semantic_model(tmp_path) -> None:
    missing = str(tmp_path / "nope" / "model.yaml")
    assert main(["build", "test dashboard", "--model-path", missing]) == 2

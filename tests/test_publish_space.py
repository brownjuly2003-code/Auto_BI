"""publish_space.py safety envelope (audit C-3): a user-passed workdir is wiped only
when it is a verified clone of THE Space and --force-clean is explicit; a foreign
directory is never deleted. All tests abort before any network call."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "hf-demo" / "publish_space.py"
_spec = importlib.util.spec_from_file_location("publish_space", _SCRIPT)
publish_space = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_space)

TOKEN = "hf_" + "a" * 30


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)


def _git_dir(path: Path, remote: str | None) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


def test_foreign_directory_is_never_deleted(tmp_path, capsys) -> None:
    victim = tmp_path / "important"
    victim.mkdir()
    keep = victim / "keep.txt"
    keep.write_text("precious", encoding="utf-8")
    assert publish_space.main([str(victim)]) == 2
    assert keep.read_text(encoding="utf-8") == "precious"
    assert "refusing to touch" in capsys.readouterr().err


def test_foreign_git_repo_is_never_deleted(tmp_path, capsys) -> None:
    # even a git repo is refused unless its remotes point at THE Space
    victim = _git_dir(tmp_path / "other_project", "https://github.com/acme/other.git")
    assert publish_space.main([str(victim)]) == 2
    assert victim.exists()
    assert "refusing to touch" in capsys.readouterr().err


def test_existing_space_clone_requires_force_clean(tmp_path, capsys) -> None:
    clone = _git_dir(tmp_path / "space", f"https://huggingface.co/spaces/{publish_space.SPACE}")
    assert publish_space.main([str(clone)]) == 2
    assert clone.exists()
    assert "--force-clean" in capsys.readouterr().err


def test_is_space_clone_marker(tmp_path) -> None:
    assert not publish_space.is_space_clone(tmp_path / "missing")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not publish_space.is_space_clone(plain)
    no_remote = _git_dir(tmp_path / "no_remote", None)
    assert not publish_space.is_space_clone(no_remote)
    real = _git_dir(tmp_path / "real", f"https://huggingface.co/spaces/{publish_space.SPACE}")
    assert publish_space.is_space_clone(real)


def test_missing_token_fails_closed_unless_dry_run(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("HF_TOKEN")
    victim = tmp_path / "d"
    victim.mkdir()
    assert publish_space.main([str(victim)]) == 2
    assert "HF_TOKEN" in capsys.readouterr().err
    # dry-run needs no token but still refuses the foreign dir before any network call
    assert publish_space.main([str(victim), "--dry-run"]) == 2
    assert "refusing to touch" in capsys.readouterr().err

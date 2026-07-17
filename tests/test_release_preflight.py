"""Unit tests for scripts/release_preflight.py (P1-7 release-coherence gate).

`scripts/` is not an importable package, so the module is loaded from its path with
importlib and exercised against synthetic repo roots (a tmp pyproject.toml +
auto_bi/__init__.py + CHANGELOG.md) — no live build, no network, fast on every PR.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "release_preflight", REPO_ROOT / "scripts" / "release_preflight.py"
)
assert _SPEC is not None and _SPEC.loader is not None
rp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rp)


def _make_repo(
    tmp_path: Path,
    *,
    pyproject_version: str = "0.3.2",
    dunder_version: str = "0.3.2",
    changelog: str | None = None,
) -> Path:
    """Write a minimal repo root with the three files preflight reads."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "autobi-agent"\nversion = "{pyproject_version}"\n',
        encoding="utf-8",
    )
    pkg = tmp_path / "auto_bi"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        f'"""Auto_BI."""\n\n__version__ = "{dunder_version}"\n', encoding="utf-8"
    )
    if changelog is None:
        changelog = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "## [0.3.2] - 2026-07-17\n\n"
            "### Added\n\n- Something shipped.\n\n"
            "## [0.3.1] - 2026-07-10\n\n- Older release.\n"
        )
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


# --- version coherence -------------------------------------------------------


def test_matching_version_passes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, pyproject_version="0.3.2", dunder_version="0.3.2")
    # does not raise
    rp.check_version_coherence("0.3.2", repo)


def test_mismatched_pyproject_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, pyproject_version="0.3.1", dunder_version="0.3.2")
    with pytest.raises(rp.PreflightError) as exc:
        rp.check_version_coherence("0.3.2", repo)
    assert "pyproject.toml" in str(exc.value)


def test_mismatched_dunder_version_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, pyproject_version="0.3.2", dunder_version="0.3.1")
    with pytest.raises(rp.PreflightError) as exc:
        rp.check_version_coherence("0.3.2", repo)
    assert "__version__" in str(exc.value)


def test_tag_ahead_of_both_fails(tmp_path: Path) -> None:
    # both files agree on 0.3.1 but the tag says 0.3.2 (the exact P1-7 scenario).
    repo = _make_repo(tmp_path, pyproject_version="0.3.1", dunder_version="0.3.1")
    with pytest.raises(rp.PreflightError):
        rp.check_version_coherence("0.3.2", repo)


def test_read_helpers(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, pyproject_version="1.2.3", dunder_version="1.2.3")
    assert rp.read_pyproject_version(repo) == "1.2.3"
    assert rp.read_dunder_version(repo) == "1.2.3"


# --- changelog section -------------------------------------------------------


def test_populated_changelog_section_passes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)  # default changelog has a populated [0.3.2] section
    rp.check_changelog_section("0.3.2", repo)


def test_missing_changelog_section_fails(tmp_path: Path) -> None:
    changelog = "# Changelog\n\n## [Unreleased]\n\n## [0.3.1] - 2026-07-10\n\n- Older.\n"
    repo = _make_repo(tmp_path, changelog=changelog)
    with pytest.raises(rp.PreflightError):
        rp.check_changelog_section("0.3.2", repo)


def test_empty_changelog_section_fails(tmp_path: Path) -> None:
    # heading present but no content before the next `## [` heading.
    changelog = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.3.2] - 2026-07-17\n\n"
        "## [0.3.1] - 2026-07-10\n\n- Older.\n"
    )
    repo = _make_repo(tmp_path, changelog=changelog)
    with pytest.raises(rp.PreflightError) as exc:
        rp.check_changelog_section("0.3.2", repo)
    assert "empty" in str(exc.value)


def test_unreleased_only_does_not_satisfy_a_version(tmp_path: Path) -> None:
    changelog = "# Changelog\n\n## [Unreleased]\n\n- Work in progress.\n"
    repo = _make_repo(tmp_path, changelog=changelog)
    with pytest.raises(rp.PreflightError):
        rp.check_changelog_section("0.3.2", repo)


def test_extract_changelog_section_content(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    section = rp.extract_changelog_section("0.3.2", repo)
    assert any("Something shipped." in line for line in section)
    # stops at the next release heading — older notes are not bled in.
    assert not any("Older release." in line for line in section)


# --- built artifacts ---------------------------------------------------------


def test_built_artifacts_match(tmp_path: Path) -> None:
    (tmp_path / "autobi_agent-0.3.2.tar.gz").write_text("", encoding="utf-8")
    (tmp_path / "autobi_agent-0.3.2-py3-none-any.whl").write_text("", encoding="utf-8")
    rp.check_built_artifacts("0.3.2", tmp_path)


def test_built_artifacts_version_drift_fails(tmp_path: Path) -> None:
    (tmp_path / "autobi_agent-0.3.1.tar.gz").write_text("", encoding="utf-8")
    (tmp_path / "autobi_agent-0.3.1-py3-none-any.whl").write_text("", encoding="utf-8")
    with pytest.raises(rp.PreflightError):
        rp.check_built_artifacts("0.3.2", tmp_path)


def test_built_artifacts_missing_wheel_fails(tmp_path: Path) -> None:
    (tmp_path / "autobi_agent-0.3.2.tar.gz").write_text("", encoding="utf-8")
    with pytest.raises(rp.PreflightError):
        rp.check_built_artifacts("0.3.2", tmp_path)


# --- main() wiring -----------------------------------------------------------


def test_main_passes_on_coherent_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert rp.main(["0.3.2", "--repo-root", str(repo)]) == 0


def test_main_strips_leading_v(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert rp.main(["v0.3.2", "--repo-root", str(repo)]) == 0


def test_main_fails_on_mismatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, pyproject_version="0.3.1", dunder_version="0.3.1")
    assert rp.main(["0.3.2", "--repo-root", str(repo)]) == 1

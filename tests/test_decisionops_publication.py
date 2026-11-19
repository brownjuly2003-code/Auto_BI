"""Publication coherence beyond the generic release preflight (no tag or registry publish)."""

import re
import tomllib
from pathlib import Path

from auto_bi import __version__

REPO = Path(__file__).resolve().parents[1]


def _changelog_section(changelog: str, heading: str) -> str:
    assert changelog.count(f"\n{heading}\n") == 1, heading
    return changelog.split(f"\n{heading}\n", 1)[1].split("\n## [", 1)[0]


def test_current_version_is_one_across_package_lock_and_docs() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    root = next(package for package in lock["package"] if package["name"] == "autobi-agent")
    # The dated CHANGELOG section of a release version: test_docs_defaults (ADR 0009).
    assert project["version"] == root["version"] == __version__
    for path in ("README.md", "docs/README.md", "docs/DECISIONOPS_CURRENT_STATE.md"):
        assert f"`{__version__}`" in (REPO / path).read_text(encoding="utf-8"), path
    architecture = (REPO / "docs/DECISIONOPS_ARCHITECTURE.md").read_text(encoding="utf-8")
    package_row = re.search(r"^\| Package \|.*$", architecture, re.MULTILINE)
    assert package_row and f"`{__version__}`" in package_row.group(0)


def test_the_0_9_0_release_notes_name_e4_and_keep_the_evidence_versions() -> None:
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _changelog_section(changelog, "## [0.9.0] - 2026-11-19")
    for slice_ in ("E4a", "E4b-1", "E4b-2"):
        assert re.search(rf"^- \*\*{slice_}\b", release, re.MULTILINE), slice_
    # E4 changes the inventory reader only: the release names the versions it kept.
    for fact in ("`VERIFIER_VERSION=139`", "`CHECK_VERSION=86`", "Тег не ставится"):
        assert fact in release, fact


def test_the_0_8_0_release_notes_name_e3_and_keep_the_evidence_versions() -> None:
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _changelog_section(changelog, "## [0.8.0] - 2026-11-18")
    assert re.search(r"^- \*\*E3\b", release, re.MULTILINE)
    assert "examples/decisionops-gate" in release
    # E3 changes no verdict: the release names the versions it kept.
    for fact in ("`VERIFIER_VERSION=139`", "`CHECK_VERSION=86`", "Тег не ставится"):
        assert fact in release, fact


def test_the_0_7_0_release_notes_name_every_published_slice() -> None:
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _changelog_section(changelog, "## [0.7.0] - 2026-11-17")
    for slice_id in ("D1", "D2", "D3", "D4", "D5", "D6", "E1"):
        assert re.search(rf"^- \*\*{slice_id}\b", release, re.MULTILINE), slice_id
    assert "examples/decisionops-gate/" in release
    # E1's facts survive the release summary (r1: the shortened entry had dropped them).
    for fact in ("AUTO_BI_NO_ENV_FILE=1", "--no-env-file", "Verifier 138 → 139", "`v0.6.0`"):
        assert fact in release, fact

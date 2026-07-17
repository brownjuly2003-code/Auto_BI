"""Release preflight: version-coherence + changelog gate for a `vX.Y.Z` tag.

Run by `.github/workflows/release.yml` BEFORE any publish job so a tag whose version
disagrees with the code — or a build that produces a differently-versioned artifact —
fails the whole release instead of shipping a half-matched GHCR image + PyPI wheel
(audit P1-7). A stray tag `v0.3.2` while `pyproject.toml` / `auto_bi.__version__` still
say `0.3.1` is rejected here, before anything is published.

Usage::

    python scripts/release_preflight.py 0.3.2                 # coherence + changelog
    python scripts/release_preflight.py 0.3.2 --dist-dir dist # + built sdist/wheel match

The pure checks live in small functions (unit-tested in `tests/test_release_preflight.py`
against synthetic repo roots); `main()` only wires them to argv + an exit code. Kept to the
standard library (`tomllib`, `re`, `pathlib`) so it runs in the release job before the
project is installed and needs no third-party import.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `__version__ = "0.3.1"` in auto_bi/__init__.py — parsed textually (not imported) so the
# check works against a synthetic repo root in tests and needs no installed package.
_DUNDER_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)


class PreflightError(Exception):
    """A release-coherence check failed; the tag must not publish."""


def read_pyproject_version(repo_root: Path = REPO_ROOT) -> str:
    """Return `[project].version` from pyproject.toml (stdlib `tomllib`)."""
    path = repo_root / "pyproject.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    try:
        return str(data["project"]["version"])
    except (KeyError, TypeError) as exc:
        raise PreflightError(f"{path}: no [project].version") from exc


def read_dunder_version(repo_root: Path = REPO_ROOT) -> str:
    """Return `auto_bi.__version__` by parsing auto_bi/__init__.py."""
    path = repo_root / "auto_bi" / "__init__.py"
    match = _DUNDER_VERSION_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise PreflightError(f"{path}: no __version__ assignment")
    return match.group(1)


def check_version_coherence(version: str, repo_root: Path = REPO_ROOT) -> None:
    """Fail unless tag version == pyproject [project].version == auto_bi.__version__."""
    pyproject = read_pyproject_version(repo_root)
    dunder = read_dunder_version(repo_root)
    mismatches = []
    if pyproject != version:
        mismatches.append(f"pyproject.toml [project].version = {pyproject!r}")
    if dunder != version:
        mismatches.append(f"auto_bi.__version__ = {dunder!r}")
    if mismatches:
        raise PreflightError(
            f"tag version {version!r} does not match:\n      " + "\n      ".join(mismatches)
        )


def extract_changelog_section(version: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return the CHANGELOG.md lines under `## [<version>]` up to the next `## [` heading.

    Mirrors the awk extract in release.yml that builds the GitHub Release body: the section
    is the block between the version heading and the following top-level entry. Raises if the
    heading is absent.
    """
    path = repo_root / "CHANGELOG.md"
    heading = f"## [{version}]"
    section: list[str] = []
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ["):
            if found:
                break
            found = line.startswith(heading)
            continue
        if found:
            section.append(line)
    if not found:
        raise PreflightError(f"{path}: no section '## [{version}]' (only '## [Unreleased]'?)")
    return section


def check_changelog_section(version: str, repo_root: Path = REPO_ROOT) -> None:
    """Fail unless CHANGELOG.md has a non-empty `## [<version>]` section."""
    section = extract_changelog_section(version, repo_root)
    if not any(line.strip() for line in section):
        raise PreflightError(
            f"CHANGELOG.md section '## [{version}]' is empty — "
            "add release notes for this version before tagging"
        )


def _sdist_version(filename: str) -> str:
    # PEP 625 sdist: {normalized_name}-{version}.tar.gz (name has no '-').
    stem = filename[: -len(".tar.gz")]
    return stem.rsplit("-", 1)[-1]


def _wheel_version(filename: str) -> str:
    # PEP 427 wheel: {name}-{version}-{pytag}-{abitag}-{plat}.whl (name has no '-').
    parts = filename[: -len(".whl")].split("-")
    return parts[1] if len(parts) >= 2 else ""


def check_built_artifacts(version: str, dist_dir: Path) -> None:
    """Fail unless `dist_dir` holds exactly one sdist + one wheel, both at `version`.

    Belt-and-suspenders after `uv build`: the published bits must carry the single, coherent
    tagged version — no stray older artifact, no build-time version drift.
    """
    if not dist_dir.is_dir():
        raise PreflightError(f"{dist_dir}: not a directory (run `uv build` first)")
    sdists = sorted(p.name for p in dist_dir.glob("*.tar.gz"))
    wheels = sorted(p.name for p in dist_dir.glob("*.whl"))
    problems = []
    if len(sdists) != 1:
        problems.append(f"expected exactly 1 sdist (*.tar.gz), found {len(sdists)}: {sdists}")
    if len(wheels) != 1:
        problems.append(f"expected exactly 1 wheel (*.whl), found {len(wheels)}: {wheels}")
    for name in sdists:
        if _sdist_version(name) != version:
            problems.append(f"sdist {name!r} is not version {version!r}")
    for name in wheels:
        if _wheel_version(name) != version:
            problems.append(f"wheel {name!r} is not version {version!r}")
    if problems:
        raise PreflightError(
            "built artifacts do not match the tag:\n      " + "\n      ".join(problems)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_preflight",
        description="Fail a release unless the tag, code and changelog agree on one version.",
    )
    parser.add_argument(
        "version", help="Target release version, e.g. 0.3.2 (tag without leading v)"
    )
    parser.add_argument(
        "--dist-dir",
        default=None,
        help="If given, also verify the built sdist+wheel in this dir carry the version",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    version = args.version[1:] if args.version.startswith("v") else args.version
    repo_root = Path(args.repo_root)

    checks: list[tuple[str, Callable[[], None]]] = [
        (
            "version coherence (tag == pyproject == __version__)",
            lambda: check_version_coherence(version, repo_root),
        ),
        (
            "changelog section present and non-empty",
            lambda: check_changelog_section(version, repo_root),
        ),
    ]
    if args.dist_dir:
        checks.append(
            (
                "built artifacts match the tag",
                lambda: check_built_artifacts(version, Path(args.dist_dir)),
            )
        )

    failures = 0
    for label, check in checks:
        try:
            check()
        except PreflightError as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}", file=sys.stderr)
        else:
            print(f"ok    {label}")

    if failures:
        print(
            f"\nRelease preflight FAILED ({failures} check(s)) for version {version!r}.",
            file=sys.stderr,
        )
        return 1
    print(f"\nRelease preflight passed for version {version!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

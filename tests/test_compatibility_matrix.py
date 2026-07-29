"""plan_sol step 10: README compatibility claims must match release gates.

The offline suite cannot stand up Greenplum or DataLens, but it can:
- pin which surfaces are release-gated in CI vs operator/Mac-only;
- ensure Greenplum offline contracts (advisor + golden) stay importable;
- ensure DataLens adapter modules stay importable and are labelled honestly;
- refuse silent widening of public badges without a documented gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PYPROJECT = (REPO / "pyproject.toml").read_text(encoding="utf-8")
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")

# Support matrix: each public claim maps to the gate that proves it.
# status:
#   release_gated — default CI must fail if this regresses
#   offline_contract — unit/offline eval in CI (no live stand)
#   experimental — live path exists; not a default release gate (Mac/operator)
COMPATIBILITY_MATRIX: list[dict[str, str]] = [
    {
        "claim": "Python 3.12+",
        "status": "release_gated",
        "gate": "quality (3.12) + quality-py-latest (3.13) + windows-cli-smoke",
    },
    {
        "claim": "ClickHouse + Superset (v1)",
        "status": "release_gated",
        "gate": "integration job (compose stand + contract + E2E)",
    },
    {
        "claim": "Greenplum advisor + golden fixtures",
        "status": "offline_contract",
        "gate": "quality eval advisor/golden model_gp.yaml",
    },
    {
        "claim": "Greenplum live DWH integration",
        "status": "experimental",
        "gate": "operator Mac/stand; not default CI",
    },
    {
        "claim": "DataLens adapter (unit compile paths)",
        "status": "offline_contract",
        "gate": "tests/test_datalens_*.py (non-integration)",
    },
    {
        "claim": "DataLens live build contract",
        "status": "experimental",
        "gate": "tests/test_datalens_contract.py Mac-only integration",
    },
    {
        "claim": "Windows package/CLI",
        "status": "release_gated",
        "gate": "windows-cli-smoke job",
    },
    {
        "claim": "dependency locked / latest / lowest-direct",
        "status": "release_gated",
        "gate": "dependency-resolution matrix (plan_sol step 6)",
    },
]


def test_matrix_rows_have_required_fields() -> None:
    for row in COMPATIBILITY_MATRIX:
        assert row["claim"] and row["status"] and row["gate"]
        assert row["status"] in {"release_gated", "offline_contract", "experimental"}


def test_ci_has_python_latest_and_windows_jobs() -> None:
    assert "Lint & tests (Python latest)" in CI
    assert "Windows package/CLI smoke" in CI
    assert "uv python install 3.13" in CI
    assert "windows-latest" in CI
    # Primary quality job remains the required-check name (ruleset step 5).
    assert re.search(r"name:\s*Lint, format & tests \(offline\)", CI)


def test_ci_still_runs_greenplum_offline_eval() -> None:
    assert "semantic/model_gp.yaml" in CI
    assert "Eval — advisor suite (offline, Greenplum)" in CI
    assert "Eval — golden suite replay (offline, Greenplum fixtures)" in CI


def test_ci_integration_is_clickhouse_superset_not_datalens() -> None:
    # Live DataLens must not silently appear as a default release gate.
    assert "Integration (ClickHouse + Superset stand)" in CI
    assert "test_datalens_contract.py" not in CI
    assert "test_superset_contract.py" in CI


def test_readme_marks_datalens_and_greenplum_honestly() -> None:
    # Public badges stay broad; prose must state which path is experimental / Mac-only.
    assert "DataLens" in README
    assert "Greenplum" in README or "Greengage" in README
    lower = README.lower()
    assert "experimental" in lower or "mac-only" in lower or "mac only" in lower
    # Explicit release-gate language for v1.
    assert "ClickHouse" in README and "Superset" in README


def test_pyproject_requires_python_matches_badge() -> None:
    assert 'requires-python = ">=3.12"' in PYPROJECT
    assert "Programming Language :: Python :: 3.12" in PYPROJECT
    assert "Programming Language :: Python :: 3.13" in PYPROJECT


def test_release_image_python_is_declared_and_ci_verified() -> None:
    """Release Dockerfile Python minor must have a classifier and a CI uv install."""
    matches = re.findall(
        r"^FROM python:(\d+\.\d+)-slim@sha256:[0-9a-f]{64}\s*$",
        DOCKERFILE,
        flags=re.MULTILINE,
    )
    assert len(matches) == 1, (
        "root Dockerfile must declare exactly one release base line "
        "FROM python:X.Y-slim@sha256:<64 lowercase hex>; "
        f"got {matches!r}"
    )
    version = matches[0]
    classifier = f"Programming Language :: Python :: {version}"
    assert classifier in PYPROJECT, (
        f"release image Python {version} requires classifier {classifier!r} in "
        "pyproject.toml (classifier + CI verification)"
    )
    ci_versions = set(re.findall(r"uv python install (\d+\.\d+)", CI))
    assert version in ci_versions, (
        f"release image Python {version} requires CI verification via "
        f"'uv python install {version}'; verified set is {sorted(ci_versions)!r} "
        "(classifier + CI verification)"
    )


def test_greenplum_offline_modules_import() -> None:
    from auto_bi.introspect.greenplum import GreenplumIntrospector  # noqa: F401
    from auto_bi.semantic.model import SemanticModel

    model_path = REPO / "semantic" / "model_gp.yaml"
    assert model_path.is_file()
    model = SemanticModel.load(model_path)
    assert model.tables, "GP model must list tables for offline advisor/golden"


def test_datalens_adapter_importable_offline() -> None:
    from auto_bi.adapters.datalens.adapter import DataLensAdapter
    from auto_bi.ir.spec import TargetBI

    assert DataLensAdapter is not None
    assert TargetBI.DATALENS.value == "datalens"


def test_release_gated_claims_have_ci_anchors() -> None:
    """Every release_gated row must mention a string that exists in ci.yml."""
    anchors = {
        "Python 3.12+": "quality-py-latest",
        "ClickHouse + Superset (v1)": "test_superset_contract.py",
        "Windows package/CLI": "windows-cli-smoke",
        "dependency locked / latest / lowest-direct": "dependency-resolution",
    }
    for row in COMPATIBILITY_MATRIX:
        if row["status"] != "release_gated":
            continue
        anchor = anchors.get(row["claim"])
        assert anchor is not None, f"missing anchor map for {row['claim']}"
        assert anchor in CI, f"CI missing anchor {anchor!r} for claim {row['claim']}"


def _normalize_dist_name(name: str) -> str:
    """PEP 503-ish normalize: lowercase, collapse [-_.] to hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(req: str) -> str:
    """Extract distribution name from a PEP 508 requirement string."""
    return re.split(r"[<>=!~;(\[]", req, maxsplit=1)[0].strip()


def test_pyyaml_declared_floor_and_lowest_direct_gate() -> None:
    """Ratchet PyYAML floor in pyproject + uv.lock metadata; keep lowest-direct CI gate.

    Does not assert the resolved installed PyYAML package version — only the
    declared specifier floor, lock requires-dist metadata, and CI gate command.
    """
    with (REPO / "pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)

    deps = list(pyproject.get("project", {}).get("dependencies") or [])
    pyyaml_deps = [
        d for d in deps if _normalize_dist_name(_requirement_name(str(d))).startswith("pyyaml")
    ]
    assert pyyaml_deps == ["pyyaml>=6.0.1"], (
        "sole project dependency whose normalized name starts with 'pyyaml' "
        f"must be exactly 'pyyaml>=6.0.1'; got {pyyaml_deps!r}"
    )

    with (REPO / "uv.lock").open("rb") as fh:
        lock = tomllib.load(fh)

    packages = lock.get("package") or []
    if isinstance(packages, dict):
        packages = [packages]
    autobi = next(
        (p for p in packages if isinstance(p, dict) and p.get("name") == "autobi-agent"),
        None,
    )
    assert autobi is not None, "uv.lock must contain package name 'autobi-agent'"

    requires = (autobi.get("metadata") or {}).get("requires-dist") or []
    pyyaml_reqs = [
        r
        for r in requires
        if isinstance(r, dict) and _normalize_dist_name(str(r.get("name", ""))).startswith("pyyaml")
    ]
    assert len(pyyaml_reqs) == 1, (
        f"autobi-agent metadata.requires-dist must have exactly one pyyaml entry; "
        f"got {pyyaml_reqs!r}"
    )
    assert pyyaml_reqs[0].get("specifier") == ">=6.0.1", (
        "autobi-agent pyyaml requires-dist specifier must be exactly '>=6.0.1'; "
        f"got {pyyaml_reqs[0].get('specifier')!r}"
    )

    assert "uv sync --no-dev --resolution lowest-direct" in CI

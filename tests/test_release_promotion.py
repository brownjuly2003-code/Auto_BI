"""plan_sol step 6: release promotion invariants (scan before tags, lock in demo).

Offline structural checks — no docker, no network, no paid APIs. Catches regressions
in workflow ordering and demo/image pins that would re-open audit P1-3 / P1-4.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"
DEMO_DOCKERFILE = REPO / "deploy" / "hf-demo" / "Dockerfile"
SUPERSET_DOCKERFILE = REPO / "docker" / "superset" / "Dockerfile"
PUBLISH_SPACE = REPO / "deploy" / "hf-demo" / "publish_space.py"
DEMO_IMAGE_YML = REPO / ".github" / "workflows" / "demo-image.yml"
UV_LOCK = REPO / "uv.lock"

# Must stay aligned with uv.lock `clickhouse-connect` version and both Dockerfiles.
PINNED_CLICKHOUSE_CONNECT = "1.5.0"
SUPERSET_DIGEST = "sha256:15e110b8533d3cb6a0d529512ea71252b0ac62e3f72b1f7a5000f1361822ac26"


def _release_text() -> str:
    return RELEASE_YML.read_text(encoding="utf-8")


def test_release_jobs_and_finalize_gate() -> None:
    text = _release_text()
    for job in (
        "preflight:",
        "image-security:",
        "pypi:",
        "provenance:",
        "finalize:",
        "release-status:",
    ):
        assert re.search(
            rf"^  {re.escape(job.rstrip(':'))}:\s*$", text, re.M
        ), f"release.yml missing job {job}"
    # finalize waits for image scan + package publish + package provenance
    fin = re.search(r"^  finalize:.*?^(  [a-z]|\Z)", text, re.M | re.S)
    assert fin, "finalize job block not found"
    block = fin.group(0)
    assert "image-security" in block
    assert "pypi" in block
    assert "provenance" in block


def test_image_security_scans_before_push() -> None:
    text = _release_text()
    img = re.search(r"^  image-security:.*?^(  [a-z]|\Z)", text, re.M | re.S)
    assert img, "image-security job not found"
    block = img.group(0)
    scan_pos = block.find("Scan image (Trivy, before promotion)")
    push_pos = block.find("Push version tag only")
    assert scan_pos != -1, "Trivy step missing or renamed"
    assert push_pos != -1, "version push step missing or renamed"
    assert scan_pos < push_pos, "Trivy must run before version push"
    # no :latest inside image-security (promotion is finalize)
    assert ":latest" not in block or "not :latest" in block or "not latest" in block.lower()
    # load-only build present
    assert "load: true" in block
    assert "autobi-agent-image.spdx.json" in block


def test_finalize_promotes_latest_and_attaches_image_sbom() -> None:
    text = _release_text()
    fin = re.search(r"^  finalize:.*?^(  [a-z]|\Z)", text, re.M | re.S)
    assert fin
    block = fin.group(0)
    assert "Promote latest" in block or "latest" in block
    assert "autobi-agent-image.spdx.json" in block
    assert "autobi-agent.spdx.json" in block
    assert "action-gh-release" in block or "Create GitHub Release" in block


def test_image_security_does_not_create_github_release() -> None:
    text = _release_text()
    img = re.search(r"^  image-security:.*?^(  [a-z]|\Z)", text, re.M | re.S)
    assert img
    assert "action-gh-release" not in img.group(0)


def test_demo_dockerfile_uses_frozen_lock_and_pinned_driver() -> None:
    text = DEMO_DOCKERFILE.read_text(encoding="utf-8")
    assert "uv.lock" in text
    assert "uv sync --frozen" in text
    assert f"clickhouse-connect=={PINNED_CLICKHOUSE_CONNECT}" in text
    # range pins are the audit finding we closed
    assert "clickhouse-connect>=" not in text


def test_superset_dockerfile_digest_and_driver_pin() -> None:
    text = SUPERSET_DOCKERFILE.read_text(encoding="utf-8")
    assert SUPERSET_DIGEST in text
    assert f"clickhouse-connect=={PINNED_CLICKHOUSE_CONNECT}" in text
    assert "clickhouse-connect>=" not in text


def test_uv_lock_matches_pinned_clickhouse_connect() -> None:
    text = UV_LOCK.read_text(encoding="utf-8")
    # package stanza for clickhouse-connect
    m = re.search(
        r'name = "clickhouse-connect"\nversion = "([^"]+)"',
        text,
    )
    assert m, "clickhouse-connect package not found in uv.lock"
    assert m.group(1) == PINNED_CLICKHOUSE_CONNECT, (
        f"Dockerfile pin {PINNED_CLICKHOUSE_CONNECT} != uv.lock {m.group(1)}; " "bump both together"
    )


def test_publish_space_whitelist_includes_uv_lock() -> None:
    text = PUBLISH_SPACE.read_text(encoding="utf-8")
    assert '"uv.lock"' in text or "'uv.lock'" in text
    # still only tracked files
    assert "WHITELIST" in text


def test_demo_image_workflow_has_path_triggers() -> None:
    text = DEMO_IMAGE_YML.read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "pull_request" in text
    for path in (
        "deploy/hf-demo/**",
        "uv.lock",
        "pyproject.toml",
        ".github/workflows/demo-image.yml",
        ".github/workflows/release.yml",
    ):
        assert path in text, f"demo-image path trigger missing: {path}"


@pytest.mark.parametrize(
    "path",
    [
        RELEASE_YML,
        DEMO_DOCKERFILE,
        SUPERSET_DOCKERFILE,
        PUBLISH_SPACE,
        DEMO_IMAGE_YML,
        UV_LOCK,
    ],
)
def test_supply_chain_files_exist(path: Path) -> None:
    assert path.is_file(), path

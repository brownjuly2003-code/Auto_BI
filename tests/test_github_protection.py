"""plan_sol step 5: required check names stay aligned with workflow job names."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "apply_github_protection.py"


def _load_required_checks() -> list[str]:
    # Avoid importing via path hacks: the script is not a package module.
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"REQUIRED_CHECKS:\s*list\[str\]\s*=\s*\[(.*?)\]",
        text,
        flags=re.DOTALL,
    )
    assert match, "REQUIRED_CHECKS list not found in apply_github_protection.py"
    return re.findall(r'"([^"]+)"', match.group(1))


def _workflow_job_names(path: Path) -> set[str]:
    """Collect explicit `name:` under jobs, or the job id when name is omitted."""
    lines = path.read_text(encoding="utf-8").splitlines()
    names: set[str] = set()
    in_jobs = False
    current_job: str | None = None
    job_has_name = False
    job_indent = 0

    for line in lines:
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line[0].isspace() and not line.startswith("#"):
            # top-level key after jobs block ended
            break
        m_job = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m_job:
            if current_job is not None and not job_has_name:
                names.add(current_job)
            current_job = m_job.group(1)
            job_has_name = False
            job_indent = 2
            continue
        if current_job is None:
            continue
        m_name = re.match(r"^    name:\s*(.+?)\s*$", line)
        if m_name:
            raw = m_name.group(1).strip().strip("\"'")
            names.add(raw)
            job_has_name = True
            continue
        # nested keys deeper than job are ignored; leaving jobs handled above
        if line.startswith(" ") and not line.startswith(" " * (job_indent + 1)):
            pass

    if current_job is not None and not job_has_name:
        names.add(current_job)
    return names


@pytest.mark.parametrize(
    "workflow, expected_subset",
    [
        (
            "ci.yml",
            {
                "Lint, format & tests (offline)",
                "Lint & tests (Python latest)",
                "Windows package/CLI smoke",
                "Browser E2E (offline, no stand)",
                "Dependency audit (pip-audit)",
                "Dependency resolution (locked / latest / lowest-direct)",
                "Docker image build (drift check)",
                "Integration (ClickHouse + Superset stand)",
            },
        ),
        ("gitleaks.yml", {"gitleaks"}),
        ("codeql.yml", {"analyze (python)"}),
    ],
)
def test_workflow_exports_expected_job_names(workflow: str, expected_subset: set[str]) -> None:
    path = REPO / ".github" / "workflows" / workflow
    assert path.is_file()
    names = _workflow_job_names(path)
    missing = expected_subset - names
    assert not missing, f"{workflow}: missing job names {missing}; found {names}"


def test_required_checks_match_workflows() -> None:
    required = set(_load_required_checks())
    found: set[str] = set()
    for wf in ("ci.yml", "gitleaks.yml", "codeql.yml"):
        found |= _workflow_job_names(REPO / ".github" / "workflows" / wf)
    missing = required - found
    assert not missing, (
        f"REQUIRED_CHECKS not present as workflow job names: {missing}. "
        f"Workflow names: {sorted(found)}"
    )


def test_scaffolding_files_exist() -> None:
    assert (REPO / ".github" / "CODEOWNERS").is_file()
    assert (REPO / ".github" / "pull_request_template.md").is_file()
    assert SCRIPT.is_file()
    body = (REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    for heading in (
        "Security checklist",
        "Data / privacy",
        "Docs",
        "Release / deploy",
        "Test plan",
    ):
        assert heading in body


def test_codeowners_mentions_maintainer() -> None:
    text = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "@brownjuly2003-code" in text

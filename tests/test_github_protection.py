"""plan_sol step 5: required check names stay aligned with workflow job names."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "apply_github_protection.py"

# Safe CI fingerprints only — no broad commit/path history suppression.
_GITLEAKS_SAFE_FINGERPRINTS = frozenset(
    {
        "39bb6fae46711defa4e8f8f0c73708907e458f06:tests/test_prompt_data.py:generic-api-key:31",
        "3845cb9dc52ba3da5bde4b51513cb734febdf05d:tests/test_safe_error.py:generic-api-key:45",
        "d8056cde7a0f7b632dfd8a098a33317312e630e4:tests/test_deployment_profile.py:generic-api-key:152",
        "d8056cde7a0f7b632dfd8a098a33317312e630e4:tests/test_deployment_profile.py:generic-api-key:153",
    }
)


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


def test_gitleaks_ignore_is_exact_safe_fingerprints_only() -> None:
    """Pin .gitleaksignore to four safe fingerprints; block broad TOML suppression."""
    ignore_path = REPO / ".gitleaksignore"
    assert ignore_path.is_file(), "root .gitleaksignore must exist"

    active: set[str] = set()
    for raw in ignore_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        active.add(line)
    assert active == _GITLEAKS_SAFE_FINGERPRINTS, (
        f".gitleaksignore active lines must be exactly the four safe CI fingerprints; "
        f"got {sorted(active)}"
    )

    config_path = REPO / ".gitleaks.toml"
    assert config_path.is_file(), ".gitleaks.toml must exist for allowlist policy checks"
    with config_path.open("rb") as fh:
        config = tomllib.load(fh)

    disabled_rules = (config.get("extend") or {}).get("disabledRules") or []
    assert (
        "generic-api-key" not in disabled_rules
    ), "generic-api-key must not be disabled through extend.disabledRules"

    def _allowlist_entries(node: object) -> list[dict]:
        if isinstance(node, dict):
            return [node]
        if isinstance(node, list):
            return [e for e in node if isinstance(e, dict)]
        return []

    # Reject any commits entry in global allowlists (broad commit/path history suppression).
    for key in ("allowlist", "allowlists"):
        for entry in _allowlist_entries(config.get(key)):
            assert (
                "commits" not in entry
            ), "global gitleaks allowlist must not contain commits entries"
            rule_ids = entry.get("ruleIds") or entry.get("rules") or entry.get("ids") or []
            if isinstance(rule_ids, str):
                rule_ids = [rule_ids]
            assert (
                "generic-api-key" not in rule_ids
            ), "global allowlist must not disable generic-api-key by rule id"

    # Reject disabling generic-api-key via rules table.
    rules = config.get("rules") or []
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("id", "")) == "generic-api-key":
                assert (
                    rule.get("disabled") is not True
                ), "generic-api-key must not be disabled in .gitleaks.toml"

    workflow = (REPO / ".github" / "workflows" / "gitleaks.yml").read_text(encoding="utf-8")
    assert ".gitleaksignore" in workflow, (
        "gitleaks.yml must explicitly mention .gitleaksignore so policy does not "
        "falsely claim TOML is the only suppression mechanism"
    )
    lower = workflow.lower()
    assert "fingerprint" in lower or "exact" in lower, (
        "gitleaks.yml must mention exact/fingerprint suppression " "(not TOML-only allowlists)"
    )

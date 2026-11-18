"""Offline contract of the E2 GitHub workflow, including its summary script."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_decisionops_example import documented_command

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/decisionops-gate-demo.yml"
EXAMPLE = "examples/decisionops-gate"


@pytest.fixture
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_only_example_pull_requests_with_bounded_permissions_and_concurrency(workflow) -> None:
    assert workflow.get("on", workflow.get(True)) == {"pull_request": {"paths": [f"{EXAMPLE}/**"]}}
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in ("pull_request_target", "workflow_run", "secrets.", "dwh", "signing-key"):
        assert forbidden not in text
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert workflow["concurrency"] == {
        "group": "decisionops-gate-demo-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }
    assert list(workflow["jobs"]) == ["gate"]
    job = workflow["jobs"]["gate"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 15
    assert "if" not in job
    assert "permissions" not in job and "continue-on-error" not in job


def test_candidate_head_and_base_worktree_are_real_revision_inputs(workflow) -> None:
    steps = workflow["jobs"]["gate"]["steps"]
    checkout, base = steps[:2]
    assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    assert checkout["with"] == {
        "ref": "${{ github.event.pull_request.head.sha }}",
        "persist-credentials": False,
        "fetch-depth": 0,
    }
    assert base["env"] == {"BASE_SHA": "${{ github.event.pull_request.base.sha }}"}
    assert base["shell"] == "bash"
    assert base["run"].strip() == (
        'git worktree add --detach "$RUNNER_TEMP/decisionops-base" "$BASE_SHA"'
    )
    assert "if" not in checkout and "if" not in base


def test_pinned_action_runs_the_documented_example_with_block_policy(workflow) -> None:
    steps = workflow["jobs"]["gate"]["steps"]
    assert len(steps) == 5
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"])
        assert "${{" not in step.get("run", "")
        assert "continue-on-error" not in step
    gate = steps[2]
    assert gate["id"] == "gate"
    assert gate["uses"].startswith("brownjuly2003-code/Auto_BI@")
    # The placeholder of the writer is not a commit GitHub can fetch (r2 P1).
    assert not gate["uses"].endswith("@" + "0" * 40)
    assert "# E2: pinned to the head commit of the publication pull request" in WORKFLOW.read_text(
        encoding="utf-8"
    )
    expected_contracts = [
        f"{EXAMPLE}/contracts/weekly_margin.yaml",
        f"{EXAMPLE}/contracts/weekly_revenue.yaml",
        f"{EXAMPLE}/semantic/metrics",
    ]
    inputs = gate["with"]
    assert inputs["contracts"].splitlines() == expected_contracts
    assert {key: value for key, value in inputs.items() if key != "contracts"} == {
        "base": "${{ runner.temp }}/decisionops-base/" + EXAMPLE + "/semantic",
        "candidate": f"{EXAMPLE}/semantic",
        "dashboards": f"{EXAMPLE}/dashboards",
        "product": "decision://sales/weekly_margin",
        "environment": "github-demo",
        "fail-on": "block",
        "comment": "true",
    }
    assert "if" not in gate
    text = (REPO / EXAMPLE / "README.md").read_text(encoding="utf-8")
    local = documented_command(text, "unchanged")
    assert local[2 : 2 + len(expected_contracts)] == expected_contracts
    for key in ("candidate", "dashboards", "product", "environment"):
        assert local[local.index(f"--{key}") + 1] == inputs[key]


def test_bundle_upload_and_summary_run_after_block(workflow) -> None:
    job = workflow["jobs"]["gate"]
    upload, summary = job["steps"][-2:]
    assert upload["uses"] == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    assert upload["if"] == "${{ always() && steps.gate.outputs.verdict != '' }}"
    assert upload["with"] == {
        "name": "${{ env.ARTIFACT_NAME }}",
        "path": "${{ steps.gate.outputs.evidence }}",
        "if-no-files-found": "error",
        "retention-days": 14,
    }
    assert job["env"] == {
        "ARTIFACT_NAME": "decisionops-pr-${{ github.event.pull_request.number }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    }
    assert summary["if"] == "${{ always() }}"
    assert summary["env"] == {
        "GATE_VERDICT": "${{ steps.gate.outputs.verdict }}",
        "GATE_EXIT_CODE": "${{ steps.gate.outputs.exit-code }}",
        "GATE_OUTCOME": "${{ steps.gate.outcome }}",
        "EVIDENCE_DIGEST": "${{ steps.gate.outputs.evidence-digest }}",
        "FAIL_ON": "block",
    }
    text = (REPO / EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert f"на {upload['with']['retention-days']} дней" in text
    assert "`decisionops-pr-<номер PR>-<run id>-<attempt>`" in text
    assert "`fail-on: block`" in text


@pytest.mark.parametrize(
    ("verdict", "code", "outcome"),
    [("pass_with_advisory", "0", "success"), ("block", "4", "failure"), ("", "2", "failure")],
)
def test_summary_script_writes_distinct_facts(workflow, tmp_path, verdict, code, outcome) -> None:
    step = workflow["jobs"]["gate"]["steps"][-1]
    assert step["shell"] == "bash"
    lines = step["run"].splitlines()
    assert lines[0] == "python - <<'PY'" and lines[-1] == "PY"
    script = "\n".join(lines[1:-1])
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "GITHUB_STEP_SUMMARY": str(summary),
        "GATE_VERDICT": verdict,
        "GATE_EXIT_CODE": code,
        "GATE_OUTCOME": outcome,
        "EVIDENCE_DIGEST": "sha256:" + "a" * 64,
        "ARTIFACT_NAME": "decisionops-pr-17-123-2",
        "FAIL_ON": step["env"]["FAIL_ON"],
    }
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    text = summary.read_text(encoding="utf-8")
    assert f"| Verdict | `{verdict or 'no verdict'}` |" in text
    assert f"| Gate exit code | `{code}` |" in text
    assert f"| Gate step outcome (fail-on: block) | `{outcome}` |" in text
    assert f"| Evidence digest | `{env['EVIDENCE_DIGEST']}` |" in text
    assert f"| Artifact name | `{env['ARTIFACT_NAME']}` |" in text


def test_usage_links_to_the_demo_workflow() -> None:
    usage = (
        (REPO / "docs/specs/pull-request-comment.md")
        .read_text(encoding="utf-8")
        .split("## Usage\n", 1)[1]
    )
    assert "../../.github/workflows/decisionops-gate-demo.yml" in usage
    assert "examples/decisionops-gate/" in usage

"""The trusted snapshot template is manual, pinned and cannot write to the repository."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import mock_open

import pytest
import yaml

from auto_bi import github_action

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "examples/decisionops-gate/workflows/inventory-snapshot.yml"


@pytest.fixture
def workflow() -> dict[str, Any]:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_manual_default_branch_read_only_workflow(workflow: dict[str, Any]) -> None:
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert list(workflow["jobs"]) == ["snapshot"]
    job = workflow["jobs"]["snapshot"]
    assert job["if"] == (
        "${{ github.ref == format('refs/heads/{0}', github.event.repository.default_branch) }}"
    )
    assert job["environment"] == "superset-inventory"
    assert "permissions" not in job
    assert "env" not in job
    assert not any(key in workflow for key in ("env", "concurrency"))
    # Steps run in the workspace root, never inside the `inputs` checkout, whose files the
    # default branch's committers control; inline Python is isolated from cwd anyway (-I).
    assert "defaults" not in job and "defaults" not in workflow
    assert not any("working-directory" in step for step in job["steps"])
    steps = job["steps"]
    assert steps[0]["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "path": "inputs",
        "persist-credentials": False,
    }
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"])
        assert "continue-on-error" not in step
        assert "${{" not in step.get("run", "")
    text = TEMPLATE.read_text(encoding="utf-8")
    for forbidden in ("pull_request", "contents: write", "git push", "gh pr", "github.token"):
        assert forbidden not in text


def test_code_pin_and_secrets_boundary(workflow: dict[str, Any]) -> None:
    steps = workflow["jobs"]["snapshot"]["steps"]
    by_id = {step["id"]: step for step in steps if "id" in step}
    assert by_id["code"]["with"] == {
        "repository": "brownjuly2003-code/Auto_BI",
        "ref": "${{ steps.pin.outputs.sha }}",
        "path": "inventory-code",
        "persist-credentials": False,
    }
    assert steps.index(by_id["pin"]) < steps.index(by_id["code"]) < steps.index(by_id["inventory"])
    inventory = by_id["inventory"]
    assert inventory["env"] == {
        "AUTO_BI_NO_ENV_FILE": "1",
        "AUTO_BI_SUPERSET_URL": "${{ secrets.SUPERSET_URL }}",
        "AUTO_BI_SUPERSET_USER": "${{ secrets.SUPERSET_USER }}",
        "AUTO_BI_SUPERSET_PASSWORD": "${{ secrets.SUPERSET_PASSWORD }}",
        "UV_PROJECT_ENVIRONMENT": "${{ runner.temp }}/inventory-venv",
        "UV_PYTHON": "3.12",
    }
    for step in steps:
        if step is not inventory:
            assert "secrets." not in str(step)
            assert "AUTO_BI_SUPERSET_" not in str(step)
    assert by_id["uv"]["with"]["version"] == "${{ steps.uv-version.outputs.version }}"
    assert "inventory-code/.uv-version" in by_id["uv-version"]["run"]
    assert "--frozen" in by_id["install"]["run"]
    assert "--no-env-file" in by_id["install"]["run"]
    assert "inventory-code" in by_id["install"]["run"]
    upload = steps[-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert "if" not in upload
    assert upload["with"] == {
        "name": "superset-inventory-${{ github.run_id }}-${{ github.run_attempt }}",
        "path": "${{ runner.temp }}/inventory-snapshot",
        "if-no-files-found": "error",
        "retention-days": 14,
    }


def _python(step: dict[str, Any]) -> str:
    lines = step["run"].splitlines()
    assert lines[0] == "python -I - <<'PY'" and lines[-1] == "PY"
    return "\n".join(lines[1:-1])


@pytest.mark.parametrize(
    "pin", ["main", "v0.7.0", "a" * 39, "0" * 40, "REPLACE_WITH_REVIEWED_E3_COMMIT_SHA"]
)
def test_unconfigured_or_moving_pin_fails_before_checkout(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    pin: str,
) -> None:
    step = next(s for s in workflow["jobs"]["snapshot"]["steps"] if s.get("id") == "pin")
    monkeypatch.setenv("INVENTORY_SHA", pin)
    with pytest.raises(SystemExit) as error:
        exec(compile(_python(step), "workflow-pin", "exec"), {})
    assert error.value.code == 2


def test_full_sha_is_the_checkout_output(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = next(s for s in workflow["jobs"]["snapshot"]["steps"] if s.get("id") == "pin")
    sha = "abcdef0123" * 4
    monkeypatch.setenv("INVENTORY_SHA", sha)
    monkeypatch.setenv("GITHUB_OUTPUT", "pin-output")
    output = mock_open()
    monkeypatch.setattr("builtins.open", output)
    exec(compile(_python(step), "workflow-pin", "exec"), {})
    output.assert_called_once_with("pin-output", "a", encoding="utf-8", newline="\n")
    output().write.assert_called_once_with("sha=" + sha + "\n")


def test_the_template_pins_the_commit_the_demo_action_runs(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = next(s for s in workflow["jobs"]["snapshot"]["steps"] if s.get("id") == "pin")
    sha = step["env"]["INVENTORY_SHA"]
    demo = yaml.safe_load(
        (REPO / ".github/workflows/decisionops-gate-demo.yml").read_text(encoding="utf-8")
    )
    gate = next(s for s in demo["jobs"]["gate"]["steps"] if s.get("id") == "gate")
    # One published E3 commit: the head of its publication pull request (a parent in `main`).
    assert gate["uses"] == "brownjuly2003-code/Auto_BI@" + sha
    monkeypatch.setenv("INVENTORY_SHA", sha)
    monkeypatch.setenv("GITHUB_OUTPUT", "pin-output")
    output = mock_open()
    monkeypatch.setattr("builtins.open", output)
    exec(compile(_python(step), "workflow-pin", "exec"), {})
    output().write.assert_called_once_with("sha=" + sha + "\n")


def test_pr_target_is_refused_before_any_configuration_or_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("pull_request_target proceeded past its event guard")

    monkeypatch.setattr(github_action, "read_config", forbidden)
    assert (
        github_action.main(
            {"GITHUB_EVENT_NAME": "pull_request_target"}, runner=forbidden, transport=forbidden
        )
        == 2
    )


@pytest.mark.parametrize("code,expected", [(0, 0), (3, 0), (2, 2), (1, 1)])
def test_snapshot_step_runs_isolated_cli_and_accepts_only_snapshot_exits(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    expected: int,
) -> None:
    step = next(s for s in workflow["jobs"]["snapshot"]["steps"] if s.get("id") == "inventory")
    seen: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        assert kwargs == {"check": False}
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("GITHUB_WORKSPACE", "/workspace")
    monkeypatch.setenv("RUNNER_TEMP", "/runner")
    with pytest.raises(SystemExit) as error:
        exec(compile(_python(step), "workflow-inventory", "exec"), {})
    assert error.value.code == expected
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    assert seen == [
        [
            "uv",
            "run",
            "--no-env-file",
            "--frozen",
            "--no-sync",
            "--no-dev",
            "--project",
            str(workspace / "inventory-code"),
            "python",
            "-I",
            "-X",
            "utf8",
            "-m",
            "auto_bi.cli",
            "decision",
            "inventory",
            str(workspace / "inputs/semantic"),
            "--map",
            str(workspace / "inputs/superset-map.json"),
            "--database",
            "2",
            "--output",
            str(Path("/runner") / "inventory-snapshot"),
        ]
    ]


@pytest.mark.parametrize(
    "path", [REPO / "action.yml", REPO / ".github/workflows/decisionops-gate-demo.yml"]
)
def test_pr_entrypoints_have_no_superset_credentials_or_secret_expressions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "AUTO_BI_SUPERSET_" not in text
    assert "secrets." not in text
    assert "pull_request_target" not in text

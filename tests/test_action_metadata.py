"""`action.yml`, the composite action of ADR 0012 D3, against the module it runs.

Durable spec: docs/specs/pull-request-comment.md. The metadata and `auto_bi/github_action.py`
name the same inputs, defaults and outputs; an input reaches the module as an environment
variable and is never expanded into the script; the action it uses is pinned to the commit
the project's own CI pins.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from auto_bi.github_action import (
    DEFAULTS,
    FLAG_INPUTS,
    OUTPUTS,
    OWN_INPUTS,
    PYTHON_FLAGS,
    REQUIRED_INPUTS,
    env_name,
)

REPO = Path(__file__).resolve().parents[1]
ACTION: dict[str, Any] = yaml.safe_load((REPO / "action.yml").read_text(encoding="utf-8"))
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
INPUTS = [name for name, _ in FLAG_INPUTS] + list(OWN_INPUTS)


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = ACTION["runs"]["steps"]
    return steps


def _gate() -> dict[str, Any]:
    return next(step for step in _steps() if step.get("id") == "gate")


def test_the_action_is_composite_and_each_script_names_its_shell() -> None:
    assert ACTION["runs"]["using"] == "composite"
    assert [step["shell"] for step in _steps() if "run" in step] == ["bash"]


def test_no_expression_is_expanded_into_a_script() -> None:
    for step in _steps():
        assert "${{" not in step.get("run", "")


def test_the_action_it_uses_is_pinned_to_the_commit_ci_pins() -> None:
    used = [step["uses"] for step in _steps() if "uses" in step]
    assert used
    for uses in used:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), uses
        assert f"uses: {uses} " in CI


def test_each_input_reaches_the_module_under_its_variable() -> None:
    env = _gate()["env"]

    assert set(ACTION["inputs"]) == {*INPUTS, "python-version"}
    for name in INPUTS:
        assert env[env_name(name)] == f"${{{{ inputs.{name} }}}}"
    assert {key for key in env if key.startswith("DECISION_GATE_")} == set(map(env_name, INPUTS))
    assert env["UV_PYTHON"] == "${{ inputs.python-version }}"
    assert not [key for key in env if key.startswith("AUTO_BI_")]


def test_the_defaults_and_the_required_inputs_are_the_module_ones() -> None:
    inputs = ACTION["inputs"]

    for name, value in DEFAULTS.items():
        assert inputs[name]["default"] == value
    assert inputs["github-token"]["default"] == "${{ github.token }}"
    assert {name for name, spec in inputs.items() if spec["required"]} == {
        "contracts",
        *REQUIRED_INPUTS,
    }
    for name, spec in inputs.items():
        assert spec["description"].strip(), name
        assert spec["required"] or "default" in spec, name


def test_the_outputs_are_the_module_ones_in_its_order() -> None:
    outputs = ACTION["outputs"]

    assert list(outputs) == list(OUTPUTS)
    for name in OUTPUTS:
        assert outputs[name]["value"] == f"${{{{ steps.gate.outputs.{name} }}}}"


def test_the_gate_runs_the_module_from_the_lock_of_the_action() -> None:
    run = _gate()["run"]

    assert 'export UV_PROJECT_ENVIRONMENT="$RUNNER_TEMP/' in run
    # Isolated, as the module starts the gate: the checkout is the working directory.
    assert PYTHON_FLAGS == ("-I", "-X", "utf8")
    assert run.rstrip("\n").endswith(
        'uv run --frozen --no-dev --project "$GITHUB_ACTION_PATH" '
        "python -I -X utf8 -m auto_bi.github_action"
    )


def test_the_gate_step_is_given_the_inputs_and_nothing_else() -> None:
    assert set(_gate()["env"]) == {*map(env_name, INPUTS), "UV_PYTHON"}
    others = [step for step in _steps() if step.get("id") != "gate"]
    assert "inputs.github-token" not in yaml.safe_dump(others)

"""Policy refusals display supplied dimension names without terminal controls."""

from pathlib import Path

import pytest

from auto_bi.investigation.application import OfflineInvestigationError, build_offline_investigation
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from tests.test_investigation_cli import PLAN_FIXTURE, RESULT_FIXTURE, invoke


@pytest.mark.parametrize(
    ("dimension", "shown"),
    [
        ("missing category", "missing category"),
        ("категория", "категория"),
        ("category\nFORGED diagnostic", r"category\nFORGED diagnostic"),
        ("category\r\nFORGED diagnostic", r"category\r\nFORGED diagnostic"),
        ("category\rFORGED diagnostic", r"category\rFORGED diagnostic"),
        ("category\tvalue", r"category\tvalue"),
        ("category\x1b[2J", r"category\x1b[2J"),
        ("category\u2028value\u2029end", r"category\u2028value\u2029end"),
        ("category\u202evalue", r"category\u202evalue"),
    ],
)
def test_policy_refusal_is_one_visible_line_and_preserves_raw_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dimension: str, shown: str
) -> None:
    raw = InvestigationPlan.load(PLAN_FIXTURE).model_dump(mode="json")
    raw["scope"]["dimensions"] = [dimension]
    raw["steps"] = raw["steps"][:1]
    raw["steps"][0]["dimensions"] = [dimension]
    plan = InvestigationPlan.model_validate(raw)
    result = CanonicalMetricResult.load(RESULT_FIXTURE)
    prefix = (
        "policy stopped at health/metric://sales/revenue@5: "
        "scope.unsupported_dimension — dimensions are absent from canonical result: "
    )
    with pytest.raises(OfflineInvestigationError) as caught:
        build_offline_investigation(plan, [result])
    assert str(caught.value) == prefix + dimension

    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(plan.to_yaml(), encoding="utf-8")
    output = tmp_path / "missing" / "output"

    assert invoke(output, plan_path, RESULT_FIXTURE) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {prefix}{shown}\n"
    assert len(captured.err.splitlines()) == 1
    assert not output.parent.exists()

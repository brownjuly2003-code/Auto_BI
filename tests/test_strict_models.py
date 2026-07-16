"""Audit P1-5: unknown keys and typos must fail validation, not silently default."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_bi.api.schemas import AutoSessionRequest, ReplyRequest, StartSessionRequest
from auto_bi.ir.spec import ChartQuery, DashboardSpec
from auto_bi.semantic.model import SemanticModel


def test_chart_query_rejects_typo_limt() -> None:
    with pytest.raises(ValidationError) as ei:
        ChartQuery.model_validate(
            {
                "table": "dm.sales_daily",
                "measures": [{"column": "revenue", "agg": "sum"}],
                "limt": 1,  # typo — must not become limit=5000 silently
            }
        )
    assert "limt" in str(ei.value).lower() or "extra" in str(ei.value).lower()


def test_dashboard_spec_rejects_unknown_top_level() -> None:
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate(
            {
                "title": "x",
                "charts": [
                    {
                        "id": "c1",
                        "title": "t",
                        "viz": "big_number",
                        "query": {
                            "table": "dm.sales_daily",
                            "measures": [{"column": "revenue", "agg": "sum"}],
                        },
                    }
                ],
                "extra_key": True,
            }
        )


def test_auto_session_rejects_max_chart_typo() -> None:
    with pytest.raises(ValidationError):
        AutoSessionRequest.model_validate({"table": "dm.sales_daily", "max_chart": 2})


def test_auto_session_max_charts_bounds() -> None:
    assert AutoSessionRequest(table="dm.sales_daily", max_charts=1).max_charts == 1
    assert AutoSessionRequest(table="dm.sales_daily", max_charts=12).max_charts == 12
    with pytest.raises(ValidationError):
        AutoSessionRequest(table="dm.sales_daily", max_charts=0)
    with pytest.raises(ValidationError):
        AutoSessionRequest(table="dm.sales_daily", max_charts=13)


def test_semantic_model_rejects_tablse_typo() -> None:
    with pytest.raises(ValidationError):
        SemanticModel.model_validate({"tablse": [{"name": "dm.x", "columns": []}]})


def test_start_session_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        StartSessionRequest.model_validate({"request": "hi", "foo": 1})


def test_reply_requires_nonempty_text() -> None:
    with pytest.raises(ValidationError):
        ReplyRequest.model_validate({"text": ""})

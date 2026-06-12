"""Advisor narration (task 1.7): findings -> direct human verdicts in the dialogue.

Division of labour per D5/D9: the verdict CLASS and severity are decided by code
(worst finding per chart, never by the model); the LLM only puts the precomputed
facts into direct Russian — no euphemisms, no new conclusions. Advisory-only:
narration failures degrade to the mechanical titles, never block the flow.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMClient, LLMError
from auto_bi.llm.policy import reasoning_for

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {Severity.INFO: 0, Severity.WARN: 1, Severity.CRITICAL: 2}
_CLASS_ORDER = {
    VerdictClass.OK: 0,
    VerdictClass.SPEC_ADJUSTMENT: 1,
    VerdictClass.DM_CHANGE_REQUEST: 2,
}


class ChartVerdict(BaseModel):
    """One verdict per problematic chart, shown in PROPOSE_SPEC."""

    chart_id: str
    severity: Severity
    verdict_class: VerdictClass
    text: str  # LLM narrative (or mechanical fallback)
    suggestions: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)  # finding ids behind the verdict


class _Narrative(BaseModel):  # LLM output: text only, keyed by chart
    class Item(BaseModel):
        chart_id: str
        text: str

    verdicts: list[Item] = Field(default_factory=list)


NARRATE_PROMPT = """Ты — Feasibility Advisor BI-агента. Детерминированные правила уже проверили
дашборд против физики витрин (ключи сортировки, партиции, EXPLAIN) и вынесли вердикты.
Твоя задача — СФОРМУЛИРОВАТЬ их для пользователя. Решение уже принято кодом: ничего не
переоценивай, не смягчай и не добавляй новых выводов.

Дашборд (заголовки чартов): {charts}

Findings по чартам (факты и измеренное evidence):
{findings}

Для КАЖДОГО chart_id из findings верни 1-3 предложения по-русски: прямой вердикт без
эвфемизмов (что не так, почему — со ссылкой на цифры evidence) и что делать. Тон —
инженер инженеру: «фильтр по manager_id идёт мимо ключа сортировки (date, store_id) —
скан ~96% строк на каждое обновление».

Ответ — ТОЛЬКО JSON в блоке ```json```:
{schema}"""


def worst_verdicts(findings: list[Finding]) -> dict[str, ChartVerdict]:
    """Code-decided verdict per chart: worst class + worst severity, merged suggestions."""
    per_chart: dict[str, ChartVerdict] = {}
    for f in findings:
        current = per_chart.get(f.chart_id)
        if current is None:
            per_chart[f.chart_id] = ChartVerdict(
                chart_id=f.chart_id,
                severity=f.severity,
                verdict_class=f.verdict_class,
                text=f.title,
                suggestions=list(f.suggestions),
                rules=[f.rule],
            )
            continue
        if _SEVERITY_ORDER[f.severity] > _SEVERITY_ORDER[current.severity]:
            current.severity = f.severity
        if _CLASS_ORDER[f.verdict_class] > _CLASS_ORDER[current.verdict_class]:
            current.verdict_class = f.verdict_class
        current.text = f"{current.text}; {f.title}"  # mechanical fallback text
        current.suggestions.extend(s for s in f.suggestions if s not in current.suggestions)
        current.rules.append(f.rule)
    return per_chart


def narrate_findings(
    llm: LLMClient,
    spec: DashboardSpec,
    findings: list[Finding],
    *,
    session_id: str | None = None,
) -> list[ChartVerdict]:
    """Findings -> per-chart verdicts with LLM wording; [] when the spec is clean."""
    if not findings:
        return []
    verdicts = worst_verdicts(findings)

    charts = {c.id: c.title for c in spec.charts}
    fields = {"chart_id", "rule", "severity", "title", "evidence", "suggestions"}
    payload = [f.model_dump(mode="json", include=fields) for f in findings]
    prompt = NARRATE_PROMPT.format(
        charts=json.dumps(charts, ensure_ascii=False),
        findings=json.dumps(payload, ensure_ascii=False, indent=1),
        schema=json.dumps(_Narrative.model_json_schema(), ensure_ascii=False),
    )
    try:
        narrative = llm.complete(
            prompt,
            _Narrative,
            reasoning=reasoning_for("narrate_advisor"),
            session_id=session_id,
        )
    except LLMError:
        logger.warning("advisor narration failed; falling back to mechanical titles")
        return list(verdicts.values())

    for item in narrative.verdicts:
        if item.chart_id in verdicts and item.text.strip():
            verdicts[item.chart_id].text = item.text.strip()
    return list(verdicts.values())

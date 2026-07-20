"""D-2 §3: one EXPLAIN per distinct statement, shared by the advisor and the SQL guard.

The saving is only ever taken for a BYTE-IDENTICAL statement: the advisor judges the
effective query on the pre-normalization spec, the guard the normalized one, and those
diverge as soon as a label join or a control default is in play. The divergence tests
below are the ones that matter — sharing evidence across two different queries would hand
the advisor a measurement of a query the BI never runs.
"""

import pytest

from auto_bi.advisor.core import Advisor
from auto_bi.agent.pipeline import build_dashboard, compile_and_build, review_and_log
from auto_bi.agent.query_plan import PlanCache
from auto_bi.agent.sql_guard import LiveSQLValidator, SQLGuardError
from auto_bi.ir.spec import DashboardSpec
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC, FakeLLM
from tests.test_superset_adapter import FakeSuperset, make_adapter

SQL = 'SELECT "date" FROM "dm"."sales_daily"'


class RecordingRunQuery:
    """RunQuery seam that records every statement the code sends to the DWH."""

    def __init__(self, *, estimate_rows: list[dict] | None = None) -> None:
        self.statements: list[str] = []
        self._estimate_rows = (
            [{"rows": 1000, "marks": 2, "parts": 1}] if estimate_rows is None else estimate_rows
        )

    def __call__(self, sql: str) -> list[dict]:
        self.statements.append(sql)
        if sql.startswith("EXPLAIN ESTIMATE"):
            return list(self._estimate_rows)
        if sql.startswith("EXPLAIN"):
            return [{"explain": "Expression"}]
        if sql.startswith("SELECT count()"):
            return [{"c": 20_000_000}]
        return [{"date": "2024-01-01"}]

    def count(self, prefix: str) -> int:
        return sum(1 for s in self.statements if s.startswith(prefix))


# --- the cache itself ---------------------------------------------------------------


def test_records_and_reads_back_one_plan() -> None:
    cache = PlanCache()
    assert cache.get(SQL) is None
    assert not cache.planned_ok(SQL)

    cache.record(SQL, ok=True, evidence={"est_rows": 10})
    assert cache.planned_ok(SQL)
    assert cache.get(SQL).evidence == {"est_rows": 10}
    assert len(cache) == 1


def test_a_failed_plan_is_remembered_as_not_ok() -> None:
    # the engine refused the statement -> the guard must still run its own EXPLAIN
    cache = PlanCache()
    cache.record(SQL, ok=False, evidence=None)
    assert cache.get(SQL) is not None
    assert not cache.planned_ok(SQL)


def test_plans_are_keyed_by_exact_sql() -> None:
    cache = PlanCache()
    cache.record(SQL, ok=True, evidence={"est_rows": 10})
    assert not cache.planned_ok(SQL + " WHERE 1=1")
    assert cache.get(SQL.replace('"date"', '"store_id"')) is None


# --- guard: skips its EXPLAIN only on a hit ------------------------------------------


def test_guard_skips_explain_when_the_statement_already_planned() -> None:
    run = RecordingRunQuery()
    cache = PlanCache()
    cache.record(SQL, ok=True, evidence={"est_rows": 10})

    LiveSQLValidator(run).validate(SQL, plans=cache)

    assert run.count("EXPLAIN") == 0  # the plan was already proven
    assert any(s.startswith("SELECT * FROM") for s in run.statements)  # trial still ran


def test_guard_explains_on_a_miss_and_without_a_cache() -> None:
    for cache in (None, PlanCache()):
        run = RecordingRunQuery()
        LiveSQLValidator(run).validate(SQL, plans=cache)
        assert run.count("EXPLAIN") == 1


def test_guard_explains_when_the_cached_plan_failed() -> None:
    run = RecordingRunQuery()
    cache = PlanCache()
    cache.record(SQL, ok=False, evidence=None)

    LiveSQLValidator(run).validate(SQL, plans=cache)

    assert run.count("EXPLAIN") == 1


def test_guard_still_rejects_non_select_on_a_cache_hit() -> None:
    # invariant 3: guard_sql is unconditional, a cached plan never buys a pass
    run = RecordingRunQuery()
    cache = PlanCache()
    bad = "DROP TABLE dm.sales_daily"
    cache.record(bad, ok=True, evidence={})

    with pytest.raises(SQLGuardError):
        LiveSQLValidator(run).validate(bad, plans=cache)
    assert run.statements == []


def test_guard_still_runs_the_trial_on_a_cache_hit() -> None:
    # a cache hit replaces the EXPLAIN only; the LIMIT-ed execution stays mandatory
    def failing_trial(sql: str) -> list[dict]:
        if sql.startswith("SELECT * FROM"):
            raise RuntimeError("boom")
        return [{"explain": "Expression"}]

    cache = PlanCache()
    cache.record(SQL, ok=True, evidence={})
    with pytest.raises(SQLGuardError, match="trial run failed"):
        LiveSQLValidator(failing_trial).validate(SQL, plans=cache)


# --- advisor: fills the cache, reuses it, degrades unchanged --------------------------


def test_advisor_records_its_estimate_and_reuses_it() -> None:
    run = RecordingRunQuery()
    advisor = Advisor(demo_model_fixtureless(), run)
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    cache = PlanCache()

    first = advisor.review(spec, plans=cache)
    estimates_after_first = run.count("EXPLAIN ESTIMATE")
    second = advisor.review(spec, plans=cache)  # same statements -> no new round trips

    assert estimates_after_first >= 1
    assert run.count("EXPLAIN ESTIMATE") == estimates_after_first
    assert [f.rule for f in second] == [f.rule for f in first]


def test_advisor_without_a_cache_behaves_as_before() -> None:
    run_a, run_b = RecordingRunQuery(), RecordingRunQuery()
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    model = demo_model_fixtureless()

    cached = Advisor(model, run_a).review(spec, plans=PlanCache())
    plain = Advisor(model, run_b).review(spec)

    assert [f.rule for f in cached] == [f.rule for f in plain]


def test_an_unusable_estimate_is_recorded_as_not_ok() -> None:
    # empty EXPLAIN ESTIMATE output is not proof the statement plans -> stay conservative
    run = RecordingRunQuery(estimate_rows=[])
    cache = PlanCache()
    Advisor(demo_model_fixtureless(), run).review(
        DashboardSpec.model_validate(GOOD_SPEC), plans=cache
    )
    assert len(cache) >= 1
    assert not any(cache.planned_ok(sql) for sql in [SQL])
    planned = [cache.planned_ok(s.removeprefix("EXPLAIN ESTIMATE ")) for s in run.statements]
    assert not any(planned)


# --- end to end: the duplicate EXPLAIN is gone, and only when it is safe --------------


def _build_with(spec_dict, run) -> None:
    build_dashboard(
        "выручка по дням",
        demo_model_fixtureless(),
        llm=FakeLLM([spec_dict]),
        sql_validator=LiveSQLValidator(run),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        advisor=Advisor(demo_model_fixtureless(), run),
        log=lambda s: None,
    )


def test_build_plans_each_chart_once_when_review_and_guard_agree() -> None:
    run = RecordingRunQuery()
    _build_with(GOOD_SPEC, run)

    n_charts = len(DashboardSpec.model_validate(GOOD_SPEC).charts)
    # advisor: one EXPLAIN ESTIMATE per chart (pre-D-1 chart SQL, accepted risk for SOURCE)
    assert run.count("EXPLAIN ESTIMATE") == n_charts
    # D-1: guard gates the shared source SQL (not the chart SQL the advisor planned) →
    # one legitimate PlanCache miss per mart with SOURCE charts
    plain_explains = run.count("EXPLAIN ") - run.count("EXPLAIN ESTIMATE")
    assert plain_explains == 1  # source dataset for dm.sales_daily


def test_build_replans_when_normalization_rewrites_the_statement() -> None:
    # B3 label joins rewrite an FK dimension into a JOIN. Under D-1 the SOURCE chart's
    # guard statement is generate_source_sql (mart + label joins), never the advisor's
    # pre-normalization chart SQL — so the guard always plans the source statement itself.
    spec = {
        **GOOD_SPEC,
        "charts": [
            {
                "id": "by_store",
                "title": "Выручка по магазинам",
                "viz": "bar",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["store_id"],
                    "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
                },
            }
        ],
    }
    run = RecordingRunQuery()
    _build_with(spec, run)

    plain_explains = run.count("EXPLAIN ") - run.count("EXPLAIN ESTIMATE")
    assert plain_explains == 1  # source SQL planned by the guard (cache miss vs chart SQL)


def test_compile_without_plans_is_unchanged() -> None:
    # the API approve path passes no cache (preview and build are separate requests)
    run = RecordingRunQuery()
    compile_and_build(
        DashboardSpec.model_validate(GOOD_SPEC),
        demo_model_fixtureless(),
        LiveSQLValidator(run),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=lambda s: None,
    )
    # D-1: one SOURCE mart → one guard EXPLAIN of the shared source SQL (not per chart)
    assert run.count("EXPLAIN ") - run.count("EXPLAIN ESTIMATE") == 1


def test_review_and_log_threads_the_cache() -> None:
    run = RecordingRunQuery()
    cache = PlanCache()
    review_and_log(
        Advisor(demo_model_fixtureless(), run),
        DashboardSpec.model_validate(GOOD_SPEC),
        log=lambda s: None,
        plans=cache,
    )
    assert len(cache) >= 1


def test_review_and_log_tolerates_no_advisor() -> None:
    assert review_and_log(None, DashboardSpec.model_validate(GOOD_SPEC), log=lambda s: None) == []


def test_auto_overview_stays_within_its_dwh_pass_budget() -> None:
    """DWH round-trip budget for a full auto-overview build (D-2 + D-1).

    Advisor still plans each chart's pre-D-1 SQL once (EXPLAIN ESTIMATE). The guard
    gates the SQL the BI runs: one shared source dataset per SOURCE mart (PlanCache
    miss vs chart SQL is legitimate) plus per-chart SQL for OWN fallbacks. LIMIT trial
    stays mandatory on every gated statement (invariant 3).
    """
    from auto_bi.agent.autospec import build_auto_spec
    from auto_bi.agent.dataset_plan import DatasetRole, plan_datasets

    model = demo_model_fixtureless()
    spec = build_auto_spec(model, "dm.sales_daily", max_charts=8)
    run = RecordingRunQuery()
    plans = PlanCache()

    review_and_log(Advisor(model, run), spec, log=lambda s: None, plans=plans)
    compile_and_build(
        spec,
        model,
        LiveSQLValidator(run),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=lambda s: None,
        plans=plans,
    )

    ds_plan = plan_datasets(spec)
    charts = len(spec.charts)
    source_tables = len(ds_plan.source_tables)
    own_charts = sum(1 for c in spec.charts if ds_plan.chart(c.id).role is DatasetRole.OWN)
    estimates = run.count("EXPLAIN ESTIMATE")
    plain_explains = run.count("EXPLAIN ") - estimates
    trials = sum(1 for s in run.statements if s.startswith("SELECT * FROM"))
    row_counts = sum(1 for s in run.statements if s.startswith("SELECT total_rows"))

    assert estimates == charts  # one advisor plan per chart, none repeated
    # guard: source SQL per mart (always a miss vs chart SQL) + OWN chart SQL (cache hit
    # only if the advisor's statement matches post-normalization OWN SQL)
    assert plain_explains == source_tables + own_charts or plain_explains == source_tables
    assert trials == source_tables + own_charts  # LIMIT trial per gated statement
    assert row_counts == 1  # scan-fraction denominator, cached per table (one table here)
    # advisor estimates + guard explains + trials + one row_count — no other DWH traffic
    assert len(run.statements) == estimates + plain_explains + trials + row_counts


def test_stub_run_query_path_still_builds() -> None:
    # the plain stub used across the suite has no ESTIMATE support; the build must not care
    compile_and_build(
        DashboardSpec.model_validate(GOOD_SPEC),
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=lambda s: None,
        plans=PlanCache(),
    )

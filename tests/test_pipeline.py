"""Happy-path wiring test: fake LLM + stub DWH + fake Superset -> DashboardRef."""

import pytest

from auto_bi.agent.pipeline import build_dashboard, compile_and_build, prune_artifact_rows
from auto_bi.agent.sql_guard import LiveSQLValidator, SQLGuardError
from auto_bi.ir.spec import DashboardSpec
from tests.test_propose import GOOD_SPEC, FakeLLM
from tests.test_superset_adapter import FakeSuperset, make_adapter


def stub_run_query(sql: str) -> list[dict]:
    return [{"explain": "Expression"}]


def test_build_dashboard_happy_path() -> None:
    log: list[str] = []
    ref = build_dashboard(
        "выручка по дням",
        demo_model_fixtureless(),
        llm=FakeLLM([GOOD_SPEC]),
        sql_validator=LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=log.append,
    )
    assert ref.url.startswith("/superset/dashboard/")
    assert any("PROPOSE_SPEC" in line for line in log)
    assert any("SQL ok" in line for line in log)
    assert any("BUILD done" in line for line in log)


def test_build_dashboard_stops_on_sql_failure() -> None:
    def failing_run(sql: str) -> list[dict]:
        raise RuntimeError("Unknown column")

    fake_superset = FakeSuperset()
    with pytest.raises(SQLGuardError):
        build_dashboard(
            "выручка по дням",
            demo_model_fixtureless(),
            llm=FakeLLM([GOOD_SPEC]),
            sql_validator=LiveSQLValidator(failing_run),
            adapter_for=lambda _target: make_adapter(fake_superset),
            log=lambda s: None,
        )
    # nothing was created in the BI after SQL validation failed
    assert not any(m == "POST" and "chart" in p for m, p, _ in fake_superset.requests)


def test_build_dashboard_records_spec_and_build_in_store(tmp_path) -> None:
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    ref = build_dashboard(
        "выручка по дням",
        demo_model_fixtureless(),
        llm=FakeLLM([GOOD_SPEC]),
        sql_validator=LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=lambda s: None,
        store=store,
        session_id=sid,
    )
    (spec_row,) = store.specs(sid)
    assert spec_row["spec_json"]["title"] == GOOD_SPEC["title"]
    (build_row,) = store.builds(sid)
    assert build_row["spec_id"] == spec_row["id"]
    assert build_row["url"] == ref.url
    assert build_row["status"] == "ok"
    store.close()


def test_compile_and_build_marks_session_building_then_failed_on_sql_error(tmp_path) -> None:
    # B-7: before this fix, a SQL-guard failure (as opposed to an adapter.build() failure)
    # never wrote a builds-table row or flipped the session to 'failed' — it just vanished.
    from auto_bi.store import Store

    def failing_run(sql: str) -> list[dict]:
        raise RuntimeError("Unknown column")

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"))
    fake_superset = FakeSuperset()

    with pytest.raises(SQLGuardError):
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(failing_run),
            adapter_for=lambda _target: make_adapter(fake_superset),
            store=store,
            session_id=sid,
            spec_id=spec_id,
        )

    assert store.session_status(sid) == "failed"
    (build,) = store.builds(sid)
    assert build["status"] == "failed"
    assert not any(m == "POST" and "chart" in p for m, p, _ in fake_superset.requests)
    store.close()


def test_compile_and_build_marks_session_failed_on_healthcheck_failure(tmp_path) -> None:
    from auto_bi.adapters.base import AdapterHealth
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    class DeadAdapter:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=False, message="superset unreachable")

    with pytest.raises(RuntimeError, match="healthcheck failed"):
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _target: DeadAdapter(),
            store=store,
            session_id=sid,
        )

    assert store.session_status(sid) == "failed"
    (build,) = store.builds(sid)
    assert build["status"] == "failed"
    assert "healthcheck failed" in build["error"]
    store.close()


def test_compile_and_build_records_bi_artifacts_in_ownership_ledger(tmp_path) -> None:
    # ownership ledger (P0-2 criterion 4): a successful build records every BI entity in
    # Store.bi_artifacts, keyed on the session owner and ONE build_token (the revision).
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"))

    compile_and_build(
        spec,
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        store=store,
        session_id=sid,
        spec_id=spec_id,
    )

    arts = store.bi_artifacts(sid)
    assert {a["kind"] for a in arts} == {"database", "dataset", "chart", "dashboard"}
    # every row carries the session owner, the target BI, and ONE build_token (the revision)
    assert {a["owner"] for a in arts} == {"alice"}
    assert {a["target_bi"] for a in arts} == {"superset"}
    assert len({a["build_token"] for a in arts}) == 1
    # datasets carry the DWH schema.table (RBAC scoping); all rows start 'live'
    assert all(a["schema_set"] for a in arts if a["kind"] == "dataset")
    assert all(a["status"] == "live" for a in arts)
    store.close()


def test_compile_and_build_second_build_makes_first_an_orphan(tmp_path) -> None:
    # a rebuild in the same session gets a fresh build_token, so the prior build's OWNED
    # artifacts become the orphan-cleanup candidates — selected on ownership, never on name.
    # prune_orphans=False keeps this a pure SELECTION test: the auto-prune (which would delete
    # and supersede these rows) is exercised separately below.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    def _run() -> None:
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _target: make_adapter(FakeSuperset()),
            store=store,
            session_id=sid,
            prune_orphans=False,
        )

    _run()
    first_token = store.bi_artifacts(sid)[0]["build_token"]
    _run()  # rebuild in the same session -> a new build_token

    tokens = {a["build_token"] for a in store.bi_artifacts(sid)}
    assert len(tokens) == 2
    current = next(t for t in tokens if t != first_token)
    orphans = store.orphan_bi_artifacts(sid, current, owner="alice")
    assert orphans  # the first build's artifacts are the delete candidates
    assert {o["build_token"] for o in orphans} == {first_token}
    store.close()


# --- auto-prune on rebuild: ownership live-cleanup of prior-revision artifacts -----------


class RecordingAdapter:
    """A build-capable adapter that records its `delete_artifact` calls instead of hitting a BI.

    Wraps a real SupersetAdapter (`make_adapter(FakeSuperset())`) for healthcheck/namespace/
    build/drain — the same fake the ownership-ledger tests use — and intercepts the concrete
    `delete_artifact` helper the auto-prune reaches for via getattr. Recording the calls lets a
    test assert the prune fed the right prior-build ids to the BI, in the right order, with no
    live delete. `fail_ids` marks native ids whose delete raises (a per-row failure the prune
    must tolerate without failing the already-delivered build).
    """

    def __init__(self, inner, fail_ids=()) -> None:
        self._inner = inner
        self.deleted: list[tuple[str, str]] = []
        self._fail_ids = set(fail_ids)

    def healthcheck(self):
        return self._inner.healthcheck()

    def set_artifact_namespace(self, namespace: str) -> None:
        self._inner.set_artifact_namespace(namespace)

    def build(self, spec):
        return self._inner.build(spec)

    def drain_build_artifacts(self):
        return self._inner.drain_build_artifacts()

    def delete_artifact(self, kind: str, native_id: str) -> None:
        self.deleted.append((kind, native_id))
        if native_id in self._fail_ids:
            raise RuntimeError(f"BI refused to delete {kind} {native_id}")


class NoDeleteAdapter:
    """A build-capable adapter that LACKS delete_artifact (a bare-protocol prune target)."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def healthcheck(self):
        return self._inner.healthcheck()

    def set_artifact_namespace(self, namespace: str) -> None:
        self._inner.set_artifact_namespace(namespace)

    def build(self, spec):
        return self._inner.build(spec)

    def drain_build_artifacts(self):
        return self._inner.drain_build_artifacts()


def _compile(spec, store, sid, adapter_for, *, log=lambda s: None, prune_orphans=True):
    return compile_and_build(
        spec,
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=adapter_for,
        store=store,
        session_id=sid,
        log=log,
        prune_orphans=prune_orphans,
    )


def test_auto_prune_on_rebuild_deletes_prior_nonshared_in_order(tmp_path) -> None:
    # a rebuild in the same session auto-prunes the prior revision's BI artifacts by id, in the
    # stand-proven order (charts -> dashboard -> datasets), never the shared database, never the
    # freshly delivered current-build ids; the pruned ledger rows flip 'live' -> 'superseded'.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    fake = FakeSuperset()  # one fake across both builds -> native ids never collide between them
    adapters: list[RecordingAdapter] = []

    def adapter_for(_target):
        a = RecordingAdapter(make_adapter(fake))
        adapters.append(a)
        return a

    _compile(spec, store, sid, adapter_for)
    first_rows = store.bi_artifacts(sid)
    first_token = first_rows[0]["build_token"]
    by_kind = {r["kind"]: r for r in first_rows}

    _compile(spec, store, sid, adapter_for)  # rebuild -> auto-prune of the first build

    # the second build's adapter deleted the first build's non-shared artifacts, in order
    assert adapters[1].deleted == [
        ("chart", by_kind["chart"]["native_id"]),
        ("dashboard", by_kind["dashboard"]["native_id"]),
        ("dataset", by_kind["dataset"]["native_id"]),
    ]
    # the shared database is never offered for deletion
    assert "database" not in {kind for kind, _ in adapters[1].deleted}
    # nor is anything from the current (second) build
    current_ids = {
        r["native_id"] for r in store.bi_artifacts(sid) if r["build_token"] != first_token
    }
    assert current_ids.isdisjoint({nid for _, nid in adapters[1].deleted})

    # ledger: prior non-shared rows superseded; prior database + every current-build row stay live
    status = {r["id"]: r["status"] for r in store.bi_artifacts(sid)}
    assert status[by_kind["dataset"]["id"]] == "superseded"
    assert status[by_kind["chart"]["id"]] == "superseded"
    assert status[by_kind["dashboard"]["id"]] == "superseded"
    assert status[by_kind["database"]["id"]] == "live"
    assert all(
        r["status"] == "live" for r in store.bi_artifacts(sid) if r["build_token"] != first_token
    )
    store.close()


def test_auto_prune_tolerates_a_failed_delete(tmp_path) -> None:
    # a per-row delete failure never fails the build (the dashboard is already delivered): that
    # row stays 'live' for a later retry, the rest are superseded, and a warning is logged.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    fake = FakeSuperset()
    log: list[str] = []
    adapters: list[RecordingAdapter] = []
    fail_ids: set[str] = set()

    def adapter_for(_target):
        a = RecordingAdapter(make_adapter(fake), fail_ids=fail_ids)
        adapters.append(a)
        return a

    _compile(spec, store, sid, adapter_for, log=log.append)
    by_kind = {r["kind"]: r for r in store.bi_artifacts(sid)}
    fail_ids.add(by_kind["chart"]["native_id"])  # the chart delete will raise on the next build

    ref = _compile(spec, store, sid, adapter_for, log=log.append)
    assert ref.url.startswith("/superset/dashboard/")  # DashboardRef returned, no exception

    status = {r["id"]: r["status"] for r in store.bi_artifacts(sid)}
    assert status[by_kind["chart"]["id"]] == "live"  # failed delete -> stays live, retried later
    assert status[by_kind["dashboard"]["id"]] == "superseded"
    assert status[by_kind["dataset"]["id"]] == "superseded"
    assert any("prune:" in line and "не удал" in line for line in log)  # a prune warning logged
    store.close()


def test_auto_prune_noop_when_adapter_lacks_delete(tmp_path) -> None:
    # a bare-protocol adapter with no delete_artifact: the prune is a silent no-op, no error,
    # and the prior build's rows all stay 'live'.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    fake = FakeSuperset()

    def adapter_for(_target):
        return NoDeleteAdapter(make_adapter(fake))

    _compile(spec, store, sid, adapter_for)
    _compile(spec, store, sid, adapter_for)  # rebuild; adapter cannot delete -> nothing pruned
    assert all(r["status"] == "live" for r in store.bi_artifacts(sid))
    store.close()


def test_auto_prune_disabled_by_prune_orphans_false(tmp_path) -> None:
    # the AUTO_BI_PRUNE_ON_REBUILD kill-switch (wired via prune_orphans=False): no delete calls,
    # prior rows stay 'live'.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    fake = FakeSuperset()
    adapters: list[RecordingAdapter] = []

    def adapter_for(_target):
        a = RecordingAdapter(make_adapter(fake))
        adapters.append(a)
        return a

    _compile(spec, store, sid, adapter_for, prune_orphans=False)
    _compile(spec, store, sid, adapter_for, prune_orphans=False)
    assert all(a.deleted == [] for a in adapters)  # no delete calls at all
    assert all(r["status"] == "live" for r in store.bi_artifacts(sid))
    store.close()


def test_prune_artifact_rows_skips_shared_kinds_and_counts(tmp_path) -> None:
    # unit: the shared engine skips any shared kind defensively (even though both selections
    # already exclude them in SQL), and returns (removed, failed) counting per-row outcomes.
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("r")

    def _rec(kind, native_id, **over):
        return store.record_bi_artifact(
            session_id=sid,
            build_token="tok1",
            target_bi="superset",
            kind=kind,
            native_id=native_id,
            name=kind,
            **over,
        )

    db_id = _rec("database", "7", schema_set=None)
    ds_id = _rec("dataset", "10", schema_set="dm.sales_daily")
    ch_id = _rec("chart", "11", schema_set="dm.sales_daily")
    dash_id = _rec("dashboard", "12", schema_set=None)
    rows = store.bi_artifacts(sid)  # includes the shared database row (fed in defensively)

    calls: list[tuple[str, str]] = []

    def delete(kind: str, native_id: str) -> None:
        calls.append((kind, native_id))
        if native_id == "11":  # the chart delete fails
            raise RuntimeError("boom")

    removed, failed = prune_artifact_rows(store, rows, delete, log=lambda s: None)

    assert ("database", "7") not in calls  # shared kind never passed to delete
    assert (removed, failed) == (2, 1)  # dataset + dashboard removed; chart failed
    status = {r["id"]: r["status"] for r in store.bi_artifacts(sid)}
    assert status[ds_id] == "superseded" and status[dash_id] == "superseded"
    assert status[ch_id] == "live"  # failed delete -> stays live
    assert status[db_id] == "live"  # shared -> never touched
    store.close()


def demo_model_fixtureless():
    """conftest's demo_model as a plain call (this test composes fixtures manually)."""
    from tests.conftest import demo_model

    return demo_model.__wrapped__()


def test_datalens_target_gates_every_chart_sql() -> None:
    """Finding 4: D-1 source-once gating is Superset-only; DataLens keeps per-chart gate."""
    from auto_bi.adapters.base import AdapterHealth, DashboardRef
    from auto_bi.ir.spec import TargetBI
    from tests.test_query_plan import RecordingRunQuery

    class StubDataLens:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=True, message="ok")

        def build(self, spec: DashboardSpec) -> DashboardRef:
            return DashboardRef(id="dl-1", title=spec.title, url="/dl/1")

        def close(self) -> None:
            return None

    multi = {
        **GOOD_SPEC,
        "target_bi": "datalens",
        "charts": [
            {
                "id": "kpi",
                "title": "Итог",
                "viz": "big_number",
                "query": {
                    "table": "dm.sales_daily",
                    "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
                },
            },
            {
                "id": "by_day",
                "title": "По дням",
                "viz": "line",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["date"],
                    "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
                },
            },
            {
                "id": "by_store",
                "title": "По магазинам",
                "viz": "bar",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["store_id"],
                    "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
                },
            },
        ],
    }
    run = RecordingRunQuery()
    log: list[str] = []
    compile_and_build(
        DashboardSpec.model_validate(multi),
        demo_model_fixtureless(),
        LiveSQLValidator(run),
        adapter_for=lambda target: (
            StubDataLens() if target is TargetBI.DATALENS else make_adapter(FakeSuperset())
        ),
        log=log.append,
    )
    plain_explains = run.count("EXPLAIN ") - run.count("EXPLAIN ESTIMATE")
    # three charts, each generate_chart_sql is EXPLAIN+LIMIT-gated (no source-once skip)
    assert plain_explains == 3
    assert sum(1 for line in log if line.startswith("SQL ok (")) == 3
    assert not any("source:" in line for line in log)

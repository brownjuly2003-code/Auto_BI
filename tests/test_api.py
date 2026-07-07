"""HTTP API (task 2.1): dialogue over HTTP on a scripted LLM + fake builder.

Mirrors the chat contract: clarify -> approve -> build, failed edits keep the
session, SSE streams build steps. No real LLM/BI anywhere.
"""

import json
import time

from fastapi.testclient import TestClient

from auto_bi import __version__
from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.api.schemas import BuildEvent
from auto_bi.ir.spec import TargetBI
from auto_bi.llm.base import LLMError
from auto_bi.store import Store
from tests.test_machine import AMBIGUOUS_REPORT, CLEAR_REPORT, PATCHED_SPEC, ScriptedLLM
from tests.test_propose import GOOD_SPEC


class FlakyLLM(ScriptedLLM):
    """ScriptedLLM that raises when the queued item is an exception."""

    def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
        if isinstance(self._queue[0], Exception):
            raise self._queue.pop(0)
        return super().complete(
            prompt, schema, reasoning=reasoning, session_id=session_id, step=step
        )


def fake_builder(spec, log, session_id):
    log(f"SQL ok ({spec.charts[0].id})")
    log("BUILD done")
    return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")


def make_client(
    llm,
    demo_model,
    *,
    store=None,
    builder=fake_builder,
    model_path=None,
    run_query=None,
    bi_base_urls=None,
) -> TestClient:
    app = create_app(
        model=demo_model,
        llm=llm,
        store=store,
        builder=builder,
        model_path=model_path,
        run_query=run_query,
        bi_base_urls=bi_base_urls,
    )
    return TestClient(app)


def start(client: TestClient, request: str = "выручка по дням") -> dict:
    response = client.post("/api/v1/sessions", json={"request": request})
    assert response.status_code == 200, response.text
    return response.json()


def collect_events(client: TestClient, session_id: str) -> list[dict]:
    events = []
    with client.stream("GET", f"/api/v1/sessions/{session_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
                if events[-1]["kind"] in ("done", "error"):
                    break
    return events


def test_health(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    assert client.get("/api/v1/health").json() == {
        "ok": True,
        "auth": False,
        "version": __version__,
        "demo_auto_only": False,
    }
    # L-3: the OpenAPI/docs page reports the package version, not a hardcoded drifting one
    assert client.get("/openapi.json").json()["info"]["version"] == __version__


def test_clear_request_proposes_spec(demo_model) -> None:
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model)
    turn = start(client)
    assert turn["phase"] == "approve"
    assert turn["questions"] == []
    assert turn["spec"]["title"] == "Продажи"
    assert turn["session_id"]
    assert turn["error"] == ""


def test_clarify_roundtrip(demo_model) -> None:
    client = make_client(ScriptedLLM([AMBIGUOUS_REPORT, CLEAR_REPORT, GOOD_SPEC]), demo_model)
    turn = start(client, "продажи и маржа")
    assert turn["phase"] == "clarify"
    assert len(turn["questions"]) == 2
    turn = client.post(
        f"/api/v1/sessions/{turn['session_id']}/reply", json={"text": "revenue; маржу убери"}
    ).json()
    assert turn["phase"] == "approve"
    assert turn["spec"] is not None


def test_word_edit_patches_spec(demo_model) -> None:
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC]), demo_model)
    sid = start(client)["session_id"]
    turn = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "переименуй"}).json()
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"] == "Продажи (обновлено)"


def test_failed_edit_keeps_session_and_spec(demo_model) -> None:
    client = make_client(
        FlakyLLM([CLEAR_REPORT, GOOD_SPEC, LLMError("GraceKelly down")]), demo_model
    )
    sid = start(client)["session_id"]
    response = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "правка"})
    assert response.status_code == 200  # not a protocol error: session survives
    turn = response.json()
    assert "GraceKelly down" in turn["error"]
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"] == "Продажи"  # previous valid spec intact


def test_approve_builds_and_streams_events(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "api.sqlite")
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(client)["session_id"]

    accepted = client.post(f"/api/v1/sessions/{sid}/approve")
    assert accepted.status_code == 202

    events = collect_events(client, sid)
    assert [e["kind"] for e in events] == ["log", "log", "done"]
    assert events[-1]["url"] == "/superset/dashboard/7/"

    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["build_status"] == "built"
    assert state["dashboard_url"] == "/superset/dashboard/7/"
    # durable record: messages + approved spec are in the store
    assert [m["role"] for m in store.messages(sid)] == ["user", "agent"]
    (spec_row,) = store.specs(sid)
    assert spec_row["status"] == "approved"
    store.close()


def test_done_url_is_absolute_when_bi_base_is_configured(demo_model) -> None:
    # F-1: the adapter's BI-relative url would resolve against the Auto_BI host in the UI
    # (:8200 vs :8088 -> 404); with the base configured the done event and the session state
    # carry a clickable absolute link. The trailing slash on the base must not double.
    client = make_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        bi_base_urls={TargetBI.SUPERSET: "http://localhost:8088/"},
    )
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    events = collect_events(client, sid)
    assert events[-1]["url"] == "http://localhost:8088/superset/dashboard/7/"
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["dashboard_url"] == "http://localhost:8088/superset/dashboard/7/"


def test_session_trace_timeline(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "api.sqlite")
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert collect_events(client, sid)[-1]["kind"] == "done"

    # the build thread writes build_done right after the terminal SSE event
    deadline = time.monotonic() + 5
    trace: dict = {}
    while time.monotonic() < deadline:
        trace = client.get(f"/api/v1/sessions/{sid}/trace").json()
        if any(e["kind"] == "build_done" for e in trace["events"]):
            break
        time.sleep(0.02)

    assert [e["kind"] for e in trace["events"]] == [
        "grounding",
        "propose",
        "approve",
        "build_start",
        "build_done",
    ]
    assert all(e["status"] == "ok" for e in trace["events"])
    assert trace["session_id"] == sid
    store.close()


def test_observability_llm_endpoint(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "api.sqlite")
    client = make_client(ScriptedLLM([]), demo_model, store=store)
    sid = store.create_session("r")
    # the completed (Anthropic-style) call reports usage; the failed one has none
    for step, status, itok, otok in (
        ("grounding", "completed", 300, 90),
        ("propose_spec", "transport_error", None, None),
    ):
        store.log_llm_call(
            session_id=sid,
            model="claude-sonnet-4-6",
            prompt_sha256="h",
            prompt_chars=500,
            reasoning=False,
            status=status,
            latency_ms=100,
            step=step,
            completion_chars=120,
            input_tokens=itok,
            output_tokens=otok,
        )
    glob = client.get("/api/v1/observability/llm").json()
    assert glob["totals"]["calls"] == 2
    assert glob["totals"]["ok"] == 1 and glob["totals"]["failed"] == 1
    assert {r["step"] for r in glob["by_step"]} == {"grounding", "propose_spec"}
    # real tokens surface through the endpoint; only the call that reported usage is counted
    assert glob["totals"]["input_tokens"] == 300 and glob["totals"]["output_tokens"] == 90
    assert glob["totals"]["token_calls"] == 1
    # the per-session trace endpoint surfaces the same calls + a per-session summary
    trace = client.get(f"/api/v1/sessions/{sid}/trace").json()
    assert len(trace["llm_calls"]) == 2
    assert trace["llm_usage"]["totals"]["completion_chars"] == 240
    store.close()


def test_observability_requires_store(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)  # no store configured
    assert client.get("/api/v1/observability/llm").status_code == 503


def test_failed_build_reports_error_event(demo_model) -> None:
    def broken_builder(spec, log, session_id):
        log("SQL ok (c1)")
        raise RuntimeError("Superset healthcheck failed")

    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, builder=broken_builder)
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    events = collect_events(client, sid)
    assert events[-1]["kind"] == "error"
    assert "healthcheck" in events[-1]["text"]

    deadline = time.monotonic() + 5  # build thread flips the status right before the event
    while client.get(f"/api/v1/sessions/{sid}").json()["build_status"] != "failed":
        assert time.monotonic() < deadline


def test_failed_build_can_be_retried(demo_model) -> None:
    # F1 (phase-2 audit): a failed build left the session in APPROVED and every
    # retry hit 409 — re-approve after failure must rebuild the same approved spec
    attempts = []

    def flaky_builder(spec, log, session_id):
        attempts.append(session_id)
        if len(attempts) == 1:
            raise RuntimeError("Superset healthcheck failed")
        return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")

    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, builder=flaky_builder)
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert collect_events(client, sid)[-1]["kind"] == "error"
    deadline = time.monotonic() + 5
    while client.get(f"/api/v1/sessions/{sid}").json()["build_status"] != "failed":
        assert time.monotonic() < deadline

    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert collect_events(client, sid)[-1]["kind"] == "done"
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["build_status"] == "built"
    assert state["dashboard_url"] == "/superset/dashboard/7/"
    assert len(attempts) == 2


def test_target_bi_selector_routes_build_to_chosen_bi(demo_model) -> None:
    # F8: the UI BI selector sets target_bi at session start; it is re-stamped onto the
    # spec each turn (the LLM patch resets it to the default) and the build dispatches on it.
    built = {}

    def recording_builder(spec, log, session_id):
        built["target_bi"] = spec.target_bi.value
        return DashboardRef(id=1, title=spec.title, url="/x/1/")

    client = make_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC]), demo_model, builder=recording_builder
    )
    turn = client.post(
        "/api/v1/sessions", json={"request": "выручка по дням", "target_bi": "datalens"}
    ).json()
    assert turn["spec"]["target_bi"] == "datalens"  # preview reflects the choice

    sid = turn["session_id"]
    # a word edit re-proposes the spec (LLM default = superset) -> the choice is re-stamped
    edited = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "переименуй"}).json()
    assert edited["spec"]["target_bi"] == "datalens"

    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert collect_events(client, sid)[-1]["kind"] == "done"
    assert built["target_bi"] == "datalens"  # the build dispatched on the chosen BI


def test_target_bi_defaults_to_superset(demo_model) -> None:
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model)
    turn = start(client)  # no target_bi in the request -> spec default
    assert turn["spec"]["target_bi"] == "superset"


def test_unknown_target_bi_is_rejected(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    response = client.post("/api/v1/sessions", json={"request": "x", "target_bi": "powerbi"})
    assert response.status_code == 422  # unknown BI -> enum rejects, not silently coerced


def test_protocol_errors(demo_model) -> None:
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model)
    assert client.get("/api/v1/sessions/nope").status_code == 404
    assert client.post("/api/v1/sessions/nope/reply", json={"text": "x"}).status_code == 404

    sid = start(client)["session_id"]
    # events before approve: nothing to stream
    assert client.get(f"/api/v1/sessions/{sid}/events").status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    # approve twice without an edit -> the machine has nothing to approve
    # (reply after approve is NOT an error anymore — iterations, task 2.4)
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 409


def test_approve_without_builder_is_503(demo_model) -> None:
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, builder=None)
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 503


def test_dm_change_requests_lifecycle_over_http(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "dcr.sqlite")
    sid = store.create_session("обзор продаж по дням")
    req_id = store.add_dm_change_request(
        sid,
        table_name="dm.sales_daily",
        rule="no_filter_on_large_fact",
        severity="critical",
        narrative="Запрос сканирует 100% таблицы — витрина не рассчитана на такой срез.",
    )
    client = make_client(ScriptedLLM([]), demo_model, store=store)

    (row,) = client.get("/api/v1/dm-change-requests", params={"status": "open"}).json()
    assert row["id"] == req_id
    assert row["table_name"] == "dm.sales_daily"

    detail = client.get(f"/api/v1/dm-change-requests/{req_id}").json()
    assert "Заявка на изменение витрины: `dm.sales_daily`" in detail["markdown"]
    assert "обзор продаж по дням" in detail["markdown"]  # session context joined in
    assert "сканирует 100%" in detail["markdown"]

    updated = client.patch(f"/api/v1/dm-change-requests/{req_id}", json={"status": "submitted"})
    assert updated.json() == {"id": req_id, "status": "submitted"}
    assert client.get("/api/v1/dm-change-requests", params={"status": "open"}).json() == []

    # protocol errors
    assert client.get("/api/v1/dm-change-requests/999").status_code == 404
    bad = client.patch(f"/api/v1/dm-change-requests/{req_id}", json={"status": "wat"})
    assert bad.status_code == 422
    store.close()


def test_model_fields_panel(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    panel = client.get("/api/v1/model/fields").json()
    assert [t["table"] for t in panel] == ["dm.sales_daily", "dm.stores"]
    revenue = next(c for c in panel[0]["columns"] if c["name"] == "revenue")
    assert revenue == {
        "name": "revenue",
        "role": "measure",
        "type": "Decimal(18, 2)",
        "description": "Выручка, руб",
        "agg": "sum",
    }


def test_fields_first_session_over_http(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "seed.sqlite")
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    seed = {
        "groups": [
            {"label": "Тренд", "fields": ["dm.sales_daily.date", "dm.sales_daily.revenue"]},
            {"fields": ["dm.stores.city"]},
        ],
        "comment": "за последний квартал",
    }
    response = client.post("/api/v1/sessions", json={"seed": seed})
    assert response.status_code == 200, response.text
    turn = response.json()
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"] == "Продажи"
    assert "анализ раскладки" in turn["message"]  # dm.stores.city did not survive
    assert any("dm.stores.city" in n for n in turn["notes"])  # the web UI renders these
    # durable record: the rendered seed is the user message, the session label is short
    messages = store.messages(turn["session_id"])
    assert "Группа 1 «Тренд»" in messages[0]["content"]
    store.close()


def test_fields_first_protocol_errors(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    # unknown field: the panel comes from the model, so this is protocol misuse
    bad = client.post(
        "/api/v1/sessions",
        json={"seed": {"groups": [{"fields": ["dm.sales_daily.margin"]}]}},
    )
    assert bad.status_code == 422
    assert "dm.sales_daily.margin" in bad.json()["detail"]
    # neither text nor seed
    assert client.post("/api/v1/sessions", json={"request": "  "}).status_code == 422
    # empty groups fail pydantic validation
    assert client.post("/api/v1/sessions", json={"seed": {"groups": []}}).status_code == 422


def test_dm_change_requests_without_store_is_503(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model, store=None)
    assert client.get("/api/v1/dm-change-requests").status_code == 503


# --- enrichment (task 2.7) ----------------------------------------------------------


def test_gaps_then_enrich_description_over_http(demo_model, tmp_path) -> None:
    from auto_bi.semantic.model import SemanticModel

    model_path = tmp_path / "model.yaml"
    client = make_client(ScriptedLLM([]), demo_model, model_path=model_path)

    gaps = client.get("/api/v1/model/gaps").json()
    sales_gap = next(
        f
        for f in gaps["findings"]
        if f["code"] == "columns_no_description" and f["table"] == "dm.sales_daily"
    )
    assert "date" in sales_gap["detail"]

    updated = client.patch(
        "/api/v1/model/tables/dm.sales_daily/columns/date",
        json={"description": "День продажи"},
    )
    assert updated.status_code == 200, updated.text
    # the gap shrinks AND the edit is committed to model.yaml on disk
    gaps = client.get("/api/v1/model/gaps").json()
    sales_gap = next(
        f
        for f in gaps["findings"]
        if f["code"] == "columns_no_description" and f["table"] == "dm.sales_daily"
    )
    assert "date" not in sales_gap["detail"].split(", ")
    reloaded = SemanticModel.load(model_path)
    assert reloaded.table("dm.sales_daily").column("date").description == "День продажи"


def test_enrich_table_description(demo_model, tmp_path) -> None:
    from auto_bi.semantic.model import SemanticModel

    model_path = tmp_path / "model.yaml"
    client = make_client(ScriptedLLM([]), demo_model, model_path=model_path)
    response = client.patch(
        "/api/v1/model/tables/dm.stores", json={"description": "Справочник магазинов сети"}
    )
    assert response.status_code == 200
    assert (
        SemanticModel.load(model_path).table("dm.stores").description == "Справочник магазинов сети"
    )


def test_enrich_role_rules(demo_model, tmp_path) -> None:
    model_path = tmp_path / "model.yaml"
    client = make_client(ScriptedLLM([]), demo_model, model_path=model_path)
    url = "/api/v1/model/tables/dm.sales_daily/columns/store_id"

    # role -> measure with agg: ok
    ok = client.patch(url, json={"role": "measure", "agg": "count_distinct"})
    assert ok.status_code == 200
    assert ok.json()["agg"] == "count_distinct"
    # back to dimension: agg dropped automatically
    back = client.patch(url, json={"role": "dimension"})
    assert back.status_code == 200
    assert back.json()["agg"] is None
    # explicit agg on a non-measure is a contradiction (F9)
    assert client.patch(url, json={"agg": "sum"}).status_code == 422
    # unknown role / agg values
    assert client.patch(url, json={"role": "wat"}).status_code == 422
    assert client.patch(url, json={"role": "measure", "agg": "median"}).status_code == 422


def test_enrich_protocol_errors(demo_model, tmp_path) -> None:
    model_path = tmp_path / "model.yaml"
    client = make_client(ScriptedLLM([]), demo_model, model_path=model_path)
    assert (
        client.patch("/api/v1/model/tables/dm.nope", json={"description": "x"}).status_code == 404
    )
    assert (
        client.patch(
            "/api/v1/model/tables/dm.stores/columns/nope", json={"description": "x"}
        ).status_code
        == 404
    )


def test_enrich_without_model_path_is_503(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    assert client.get("/api/v1/model/gaps").status_code == 200  # read-only works
    assert (
        client.patch("/api/v1/model/tables/dm.stores", json={"description": "x"}).status_code == 503
    )


def test_iteration_rebuild_over_http(demo_model) -> None:
    # 2.4 over HTTP: build -> edit -> re-approve -> a FRESH event stream for build #2
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC]), demo_model)
    sid = start(client)["session_id"]

    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    first = collect_events(client, sid)
    assert first[-1]["kind"] == "done"

    turn = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "переименуй"}).json()
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"] == "Продажи (обновлено)"

    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    second = collect_events(client, sid)
    # the stream belongs to build #2 only: no replay of build #1's terminal event
    assert [e["kind"] for e in second] == ["log", "log", "done"]
    assert client.get(f"/api/v1/sessions/{sid}").json()["build_status"] == "built"


# --- phase-2 audit P3 fixes ------------------------------------------------------


def test_failed_start_returns_502_without_zombie_session(demo_model) -> None:
    # F2: LLMError during grounding must not register a half-born session
    client = make_client(FlakyLLM([LLMError("GraceKelly down")]), demo_model)
    response = client.post("/api/v1/sessions", json={"request": "выручка"})
    assert response.status_code == 502
    assert "GraceKelly down" in response.json()["detail"]


def test_delete_session_frees_registry_keeps_store(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "api.sqlite")
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(client)["session_id"]
    assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204
    assert client.get(f"/api/v1/sessions/{sid}").status_code == 404
    assert store.messages(sid)  # durable record survives the registry
    store.close()


def test_registry_caps_idle_sessions(demo_model, monkeypatch) -> None:
    # F3: oldest idle session is evicted once the cap is reached
    monkeypatch.setattr("auto_bi.api.sessions.MAX_SESSIONS", 2)
    responses = [CLEAR_REPORT, GOOD_SPEC] * 3
    client = make_client(ScriptedLLM(responses), demo_model)
    first = start(client)["session_id"]
    second = start(client)["session_id"]
    third = start(client)["session_id"]
    assert client.get(f"/api/v1/sessions/{first}").status_code == 404
    assert client.get(f"/api/v1/sessions/{second}").status_code == 200
    assert client.get(f"/api/v1/sessions/{third}").status_code == 200


def test_cross_origin_mutation_rejected(demo_model) -> None:
    # F5: a browser on another site cannot CSRF the unauthenticated local API
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model)
    response = client.post(
        "/api/v1/sessions",
        json={"request": "выручка"},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    same_origin = client.post(
        "/api/v1/sessions",
        json={"request": "выручка"},
        headers={"origin": "http://testserver"},
    )
    assert same_origin.status_code == 200


def test_approve_revalidates_spec_against_live_model(demo_model) -> None:
    # F6: an enrichment edit between propose and approve must fail loudly at
    # approve, not minutes later inside the build thread
    client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model)
    sid = start(client)["session_id"]
    table = demo_model.tables[0]
    table.columns = [c for c in table.columns if c.name != "revenue"]
    response = client.post(f"/api/v1/sessions/{sid}/approve")
    assert response.status_code == 409
    assert "no longer valid" in response.json()["detail"]


def test_event_stream_heartbeats_while_idle(demo_model) -> None:
    # F4: an idle stream yields None heartbeats so the HTTP layer keeps writing
    from auto_bi.api.sessions import ManagedSession

    managed = ManagedSession("s", agent=None)
    stream = managed.stream_events(poll_seconds=0.01)
    assert next(stream) is None  # no events yet -> heartbeat, not a hang
    managed.add_event(BuildEvent(kind="done", text="ok"))
    assert next(stream).kind == "done"


# --- auto-overview mode (deterministic spec, no LLM) -------------------------------


def test_auto_session_adopts_spec_into_approve(demo_model) -> None:
    # ScriptedLLM([]) proves the auto entry never calls the LLM
    client = make_client(ScriptedLLM([]), demo_model)
    response = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert response.status_code == 200, response.text
    turn = response.json()
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"].startswith("Обзор:")
    assert turn["spec"]["charts"]  # a curated spec was produced and adopted
    assert turn["session_id"]


def test_auto_session_builds_through_the_normal_path(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    sid = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"}).json()[
        "session_id"
    ]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    events = collect_events(client, sid)
    assert events[-1]["kind"] == "done"


def test_auto_session_unknown_table_404(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    response = client.post("/api/v1/sessions/auto", json={"table": "dm.nope"})
    assert response.status_code == 404


def test_auto_session_max_charts_capped(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model)
    turn = client.post(
        "/api/v1/sessions/auto", json={"table": "dm.sales_daily", "max_charts": 2}
    ).json()
    assert len(turn["spec"]["charts"]) == 2


# --- insight layer ("Что видно") over the session's spec --------------------------


def test_session_insights_reports_observations() -> None:
    # the committed model carries physical cardinality, so the auto spec has bars + a
    # share chart (the fixture model has none) -> the full set of observation types
    from auto_bi.semantic.model import SemanticModel
    from tests.test_insights import _fake_run_query

    model = SemanticModel.load("semantic/model.yaml")
    client = make_client(ScriptedLLM([]), model, run_query=_fake_run_query)
    sid = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"}).json()[
        "session_id"
    ]
    response = client.get(f"/api/v1/sessions/{sid}/insights")
    assert response.status_code == 200, response.text
    obs = response.json()["observations"]
    kinds = {o["kind"] for o in obs}
    assert {"trend", "leader", "share_lead"} <= kinds  # the headline observation types
    assert all(o["text"] for o in obs)  # every observation carries rendered prose


def test_session_insights_503_without_run_query(demo_model) -> None:
    # no run_query configured -> the layer cannot read the DWH, honest 503 (not a fake answer)
    client = make_client(ScriptedLLM([]), demo_model)
    sid = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"}).json()[
        "session_id"
    ]
    assert client.get(f"/api/v1/sessions/{sid}/insights").status_code == 503


def test_session_insights_unknown_session_404(demo_model) -> None:
    client = make_client(ScriptedLLM([]), demo_model, run_query=lambda sql: [])
    assert client.get("/api/v1/sessions/nope/insights").status_code == 404

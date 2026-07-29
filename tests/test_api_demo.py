"""Public demo mode (P8, demo_auto_only): the deterministic auto-overview path is the
only open entry — every LLM-triggering path (text/fields sessions, word edits) and
every shared-state write (enrichment) answers 403, and /health carries the flag +
capabilities so the UI can grey the tabs out. The LLM seam is DisabledLLM: no
provider, no key. Capabilities are derived from wiring, not from the flag alone."""

import pytest
from pydantic import BaseModel

from auto_bi import __version__
from auto_bi.api.capabilities import build_capabilities, llm_is_wired
from auto_bi.llm.base import DisabledLLM, LLMError
from tests.test_api import collect_events, make_client
from tests.test_machine import ScriptedLLM


def demo_client(demo_model, **kwargs):
    from fastapi.testclient import TestClient

    from auto_bi.api import create_app
    from tests.test_api import fake_builder

    app = create_app(
        model=demo_model,
        llm=DisabledLLM(),
        builder=fake_builder,
        demo_auto_only=True,
        **kwargs,
    )
    return TestClient(app)


def _expected_caps(*, demo_auto_only: bool, llm_wired: bool, enrichment: bool) -> dict:
    interactive = (not demo_auto_only) and llm_wired
    return {
        "auto_overview": True,
        "text_session": interactive,
        "fields_session": interactive,
        "word_edit": interactive,
        "enrichment": (not demo_auto_only) and enrichment,
        "llm_wired": llm_wired,
    }


def test_health_carries_the_demo_flag_and_capabilities(demo_model) -> None:
    client = demo_client(demo_model)
    health = client.get("/api/v1/health").json()
    assert health == {
        "ok": True,
        "auth": False,
        "version": __version__,
        "demo_auto_only": True,
        "capabilities": _expected_caps(demo_auto_only=True, llm_wired=False, enrichment=False),
    }
    # normal (non-demo) app: flag false, LLM wired, enrichment off without model_path
    normal = make_client(ScriptedLLM([]), demo_model)
    n_health = normal.get("/api/v1/health").json()
    assert n_health["demo_auto_only"] is False
    assert n_health["capabilities"] == _expected_caps(
        demo_auto_only=False, llm_wired=True, enrichment=False
    )


def test_capabilities_reflect_enrichment_when_model_path_set(demo_model, tmp_path) -> None:
    client = demo_client(demo_model, model_path=tmp_path / "model.yaml")
    caps = client.get("/api/v1/health").json()["capabilities"]
    # demo_auto_only still closes enrichment even with a path (gate is intentional)
    assert caps["enrichment"] is False
    assert caps["llm_wired"] is False

    from fastapi.testclient import TestClient

    from auto_bi.api import create_app
    from tests.test_api import fake_builder

    open_app = create_app(
        model=demo_model,
        llm=ScriptedLLM([]),
        builder=fake_builder,
        demo_auto_only=False,
        model_path=tmp_path / "model.yaml",
    )
    open_caps = TestClient(open_app).get("/api/v1/health").json()["capabilities"]
    assert open_caps == _expected_caps(demo_auto_only=False, llm_wired=True, enrichment=True)


def test_build_capabilities_helper_matches_health_shape() -> None:
    assert llm_is_wired(DisabledLLM()) is False
    assert llm_is_wired(ScriptedLLM([])) is True
    assert build_capabilities(
        demo_auto_only=True, llm=DisabledLLM(), model_path=None
    ) == _expected_caps(demo_auto_only=True, llm_wired=False, enrichment=False)
    assert build_capabilities(
        demo_auto_only=False, llm=ScriptedLLM([]), model_path="/tmp/m.yaml"
    ) == _expected_caps(demo_auto_only=False, llm_wired=True, enrichment=True)


def test_auto_overview_is_the_open_path_and_builds(demo_model) -> None:
    client = demo_client(demo_model)
    r = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    events = collect_events(client, sid)
    assert events[-1]["kind"] == "done"


def test_text_session_is_403(demo_model) -> None:
    client = demo_client(demo_model)
    r = client.post("/api/v1/sessions", json={"request": "выручка по дням"})
    assert r.status_code == 403
    assert "Авто" in r.json()["detail"]


def test_fields_session_is_403(demo_model) -> None:
    # fields-first is the same /sessions entry with a seed — gate before any LLM call
    client = demo_client(demo_model)
    r = client.post(
        "/api/v1/sessions",
        json={
            "request": "",
            "seed": {
                "groups": [{"label": "g", "fields": ["dm.sales_daily.revenue"]}],
                "comment": "",
            },
        },
    )
    assert r.status_code == 403
    assert "Авто" in r.json()["detail"]


def test_word_edit_is_403_even_on_an_auto_session(demo_model) -> None:
    client = demo_client(demo_model)
    sid = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"}).json()[
        "session_id"
    ]
    r = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "переименуй"})
    assert r.status_code == 403


def test_enrichment_writes_are_403(demo_model, tmp_path) -> None:
    # the demo gate answers before the model_path wiring (403, not 503): the feature is
    # deliberately off in the demo, not misconfigured
    client = demo_client(demo_model, model_path=tmp_path / "model.yaml")
    r = client.patch("/api/v1/model/tables/dm.sales_daily", json={"description": "x"})
    assert r.status_code == 403
    r = client.patch(
        "/api/v1/model/tables/dm.sales_daily/columns/revenue", json={"description": "x"}
    )
    assert r.status_code == 403


def test_dcr_status_write_is_403(demo_model) -> None:
    # shared workflow state, same rule as enrichment: the gate answers before the
    # store wiring (403, not 503/404) — the demo never creates DCRs, defense in depth
    client = demo_client(demo_model)
    r = client.patch("/api/v1/dm-change-requests/1", json={"status": "accepted"})
    assert r.status_code == 403


def test_read_surfaces_stay_open(demo_model) -> None:
    client = demo_client(demo_model)
    assert client.get("/api/v1/model/fields").status_code == 200
    assert client.get("/api/v1/model/gaps").status_code == 200


def test_disabled_llm_refuses_loudly() -> None:
    class Out(BaseModel):
        x: int

    with pytest.raises(LLMError, match="disabled"):
        DisabledLLM().complete("prompt", Out)

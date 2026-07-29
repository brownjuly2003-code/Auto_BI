"""D-2 lifecycle: adapters and LLM clients release their HTTP pools.

`make_adapter` runs per build (pipeline) and per readiness probe (serve), so an
unreleased client pool accumulates for the life of a `serve` process. close() is a
required BIAdapter method (plan_sol step 7); release goes through close_adapter.
"""

import httpx
import pytest

from auto_bi.adapters.base import AdapterHealth, BuildResult, DashboardRef
from auto_bi.adapters.datalens.adapter import DataLensAdapter
from auto_bi.adapters.datalens.client import DataLensClient
from auto_bi.adapters.factory import close_adapter, probe_health
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.agent.pipeline import compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.config import Settings
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.llm.anthropic import AnthropicClient
from auto_bi.llm.gracekelly import GraceKellyClient
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC
from tests.test_superset_adapter import DWH, FakeSuperset

# --- adapter close (concrete helper) ------------------------------------------------


def _superset_adapter_with_pool() -> tuple[SupersetAdapter, httpx.Client]:
    http = httpx.Client(
        base_url="http://superset.test", transport=httpx.MockTransport(FakeSuperset())
    )
    client = SupersetClient("http://superset.test", "admin", "pw", http=http)
    return SupersetAdapter(client, DWH, model=None), http


def test_superset_adapter_close_releases_http_pool() -> None:
    adapter, http = _superset_adapter_with_pool()
    adapter.close()
    assert http.is_closed


def test_datalens_adapter_close_releases_http_pool() -> None:
    http = httpx.Client(
        base_url="http://datalens.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    client = DataLensClient("http://datalens.test", "admin", "pw", http=http)
    adapter = DataLensAdapter(client, DWH, demo_model_fixtureless(), workbook_id="wb1")
    adapter.close()
    assert http.is_closed


def test_close_adapter_requires_close() -> None:
    # plan_sol step 7: close is required; bare objects no longer pass silently
    with pytest.raises(AttributeError):
        close_adapter(object())  # type: ignore[arg-type]


# --- probe_health (per-probe adapter in /ready) -------------------------------------


class _ClosableProbe:
    def __init__(self, *, ok: bool = True, boom: bool = False) -> None:
        self.closed = False
        self._ok = ok
        self._boom = boom

    def healthcheck(self) -> AdapterHealth:
        if self._boom:
            raise RuntimeError("bi down hard")
        return AdapterHealth(ok=self._ok, message="" if self._ok else "down")

    def close(self) -> None:
        self.closed = True


def test_probe_health_reports_and_closes_the_throwaway_adapter() -> None:
    probe = _ClosableProbe(ok=False)
    health = probe_health(lambda _target: probe, TargetBI.SUPERSET)
    assert not health.ok
    assert probe.closed


def test_probe_health_closes_even_when_healthcheck_raises() -> None:
    probe = _ClosableProbe(boom=True)
    with pytest.raises(RuntimeError):
        probe_health(lambda _target: probe, TargetBI.SUPERSET)
    assert probe.closed


# --- compile_and_build releases its per-build adapter -------------------------------


def _compile(adapter_for):
    return compile_and_build(
        DashboardSpec.model_validate(GOOD_SPEC),
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=adapter_for,
        log=lambda s: None,
    )


def test_compile_and_build_closes_adapter_on_success() -> None:
    adapter, http = _superset_adapter_with_pool()
    ref = _compile(lambda _target: adapter)
    assert ref.url.startswith("/superset/dashboard/")
    assert http.is_closed


def test_compile_and_build_closes_adapter_on_failure() -> None:
    from auto_bi.errors import CODE_BI_HEALTH, SafeError

    dead = _ClosableProbe(ok=False)  # healthcheck fails before build
    with pytest.raises(SafeError) as ei:
        _compile(lambda _target: dead)
    assert ei.value.code == CODE_BI_HEALTH
    assert dead.closed


def test_compile_and_build_close_failure_never_masks_the_build_outcome() -> None:
    class CloseBoomAdapter:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=True)

        def build(self, spec, ctx=None) -> BuildResult:
            return BuildResult(
                dashboard=DashboardRef(id=1, title="t", url="/superset/dashboard/1/")
            )

        def delete_artifact(self, kind: str, native_id: str) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("pool already broken")

    # the dashboard is already delivered; a failing pool release is logged, not raised
    ref = _compile(lambda _target: CloseBoomAdapter())
    assert ref.url == "/superset/dashboard/1/"


# --- LLM clients --------------------------------------------------------------------


def test_gracekelly_client_close_releases_http_pool(tmp_path) -> None:
    http = httpx.Client(
        base_url="http://gk.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    client = GraceKellyClient(
        Settings(_env_file=None), http=http, log_path=tmp_path / "llm_calls.jsonl"
    )
    client.close()
    assert http.is_closed


def test_anthropic_client_close_is_noop_with_injected_create(tmp_path) -> None:
    # SDK-free unit construction (injected create) owns no HTTP pool — close must be safe
    client = AnthropicClient(
        Settings(_env_file=None),
        create=lambda **kwargs: None,
        log_path=tmp_path / "llm_calls.jsonl",
    )
    client.close()

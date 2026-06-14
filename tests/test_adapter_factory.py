"""Adapter factory + pipeline dispatch by spec.target_bi (Phase 4 F1).

make_adapter wires a target to a concrete adapter without touching the network (clients
connect lazily), and compile_and_build resolves the adapter from spec.target_bi so a
"datalens" spec never silently builds in Superset.
"""

from auto_bi.adapters.datalens.adapter import DataLensAdapter
from auto_bi.adapters.factory import make_adapter
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.agent.pipeline import compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.config import Settings
from auto_bi.ir.spec import DashboardSpec, TargetBI
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC
from tests.test_superset_adapter import FakeSuperset
from tests.test_superset_adapter import make_adapter as make_superset_adapter


def test_make_adapter_superset() -> None:
    adapter = make_adapter(TargetBI.SUPERSET, Settings(), demo_model_fixtureless())
    assert isinstance(adapter, SupersetAdapter)


def test_make_adapter_datalens() -> None:
    adapter = make_adapter(TargetBI.DATALENS, Settings(), demo_model_fixtureless())
    assert isinstance(adapter, DataLensAdapter)


def test_compile_and_build_dispatches_on_target_bi() -> None:
    """The resolver is called with the spec's declared target, not a hardcoded one."""
    seen: list[TargetBI] = []

    def adapter_for(target: TargetBI):
        seen.append(target)
        return make_superset_adapter(FakeSuperset())  # any adapter; we assert the target

    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec.target_bi = TargetBI.DATALENS
    compile_and_build(
        spec,
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for,
        log=lambda s: None,
    )
    assert seen == [TargetBI.DATALENS]


def test_compile_and_build_defaults_to_superset_target() -> None:
    seen: list[TargetBI] = []

    def adapter_for(target: TargetBI):
        seen.append(target)
        return make_superset_adapter(FakeSuperset())

    spec = DashboardSpec.model_validate(GOOD_SPEC)  # no explicit target -> SUPERSET default
    compile_and_build(
        spec,
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for,
        log=lambda s: None,
    )
    assert seen == [TargetBI.SUPERSET]

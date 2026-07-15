"""Audit P0-2: independent sessions must not share BI artifact names."""

from __future__ import annotations

from auto_bi.adapters.artifacts import (
    dataset_table_name,
    namespace_fingerprint,
    new_build_namespace,
)
from auto_bi.adapters.datalens.dataset import dataset_name as dl_dataset_name
from auto_bi.adapters.superset.adapter import _dataset_name


def test_new_build_namespace_unique_even_for_same_session() -> None:
    a = new_build_namespace("sess-1")
    b = new_build_namespace("sess-1")
    assert a != b
    assert a.startswith("sess-1:")
    assert b.startswith("sess-1:")


def test_dataset_names_differ_across_namespaces() -> None:
    title, chart = "Обзор продаж", "auto1"
    a = dataset_table_name(title, chart, "session-a:aaaa")
    b = dataset_table_name(title, chart, "session-b:bbbb")
    c = dataset_table_name(title, chart, "session-a:cccc")  # same session, new build token
    assert a != b
    assert a != c
    assert b != c
    # legacy empty namespace keeps the shorter historical layout
    legacy = dataset_table_name(title, chart, "")
    assert "__" in legacy
    assert a != legacy
    assert namespace_fingerprint("session-a:aaaa", length=6) in a


def test_superset_and_datalens_helpers_agree() -> None:
    ns = "sess-x:deadbeef"
    assert _dataset_name("T", "c1", ns) == dl_dataset_name("T", "c1", ns)
    assert _dataset_name("T", "c1") == dl_dataset_name("T", "c1")


def test_legacy_slug_collision_still_unique() -> None:
    # preserved from F7: equal slugs, different raw chart ids
    a = _dataset_name("Обзор", "chart-a")
    b = _dataset_name("Обзор", "chart!a")
    assert a != b

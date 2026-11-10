"""A table a model declares with no name is a gap of the snapshot.

The name of a table is its id in the canonical graph, the first part of the id of each of
its fields, and what a diff calls a change of either. `model.yaml` is hand-edited, and its
schema takes a `name` of empty text. The graph then held a table whose id was empty text,
and comparing two refs that differed in it ended `decision verify` in a `ValidationError`
of the change that could not be named (`docs/specs/model-declarations-snapshot.md`). Such
a table is now a `gap.table_unnamed` gap, named by its place in the file, and neither it
nor its columns are in the graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.cli import main
from auto_bi.semantic.diff import diff_snapshots
from auto_bi.semantic.gateway import SemanticSnapshot, TableNode
from auto_bi.semantic.model import (
    Column,
    ColumnRole,
    DataClassification,
    Join,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    Case,
    _check_payload,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_semantic_gateway import gap_codes, model, source

CODE = "gap.table_unnamed"
ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]

#: The tables of both fixtures: where each stands, what it is called, how many columns.
TABLES = [
    pytest.param(0, "dm.sales_daily", 6, id="the first table"),
    pytest.param(1, "dm.stores", 1, id="the second table"),
]


def _reason(columns: int) -> str:
    return f"table is declared with no name: it and its {columns} column(s) are not in the graph"


def _place(index: int) -> str:
    return f"tables.{index}.name"


def _gap(index: int, columns: int) -> dict[str, str]:
    """The gap as a bundle keeps it."""
    return {
        "code": CODE,
        "severity": "block",
        "path": _place(index),
        "reason": _reason(columns),
        "source_value_ref": "",
    }


def _one_more() -> Table:
    """A table with no name that nothing else in a model refers to, facts declared."""
    return Table(
        name="",
        columns=[Column(name="x", type="String", role=ColumnRole.DIMENSION)],
        physical=Physical(engine="clickhouse"),
    )


def _unnamed(semantic: SemanticModel, index: int) -> SemanticModel:
    semantic.tables[index].name = ""
    return semantic


def _without(semantic: SemanticModel, index: int) -> SemanticModel:
    del semantic.tables[index]
    return semantic


def _frozen(tmp_path: Path, name: str, semantic: SemanticModel) -> SemanticSnapshot:
    return source(tmp_path / name, semantic=semantic).snapshot()


def _named_changes(base: SemanticSnapshot, candidate: SemanticSnapshot) -> set[tuple[str, ...]]:
    return {
        (change.code, change.severity.value, change.subject, change.detail)
        for change in diff_snapshots(base, candidate).changes
    }


# --- the snapshot -------------------------------------------------------------


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_a_table_with_no_name_is_a_blocking_gap_named_by_its_place(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    snapshot = _frozen(tmp_path, "ref", _unnamed(model(), index))
    (gap,) = [gap for gap in snapshot.gaps if gap.code == CODE]
    assert gap.severity is Severity.BLOCK
    assert gap.path == _place(index)
    assert gap.reason == _reason(columns)
    assert gap.source_value_ref == ""
    assert gap.model_dump(mode="json") == _gap(index, columns)


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_neither_the_table_nor_its_columns_are_in_the_graph(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    named = _frozen(tmp_path, "named", model())
    snapshot = _frozen(tmp_path, "ref", _unnamed(model(), index))
    kept = [table.id for table in named.graph.tables if table.id != name]
    assert [table.id for table in snapshot.graph.tables] == kept
    assert {field.table for field in snapshot.graph.fields} == set(kept)
    assert len(snapshot.graph.fields) == len(named.graph.fields) - columns
    assert not [field.id for field in snapshot.graph.fields if field.id.startswith(".")]


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_the_snapshot_is_the_one_of_the_model_without_that_table_and_the_gap(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    snapshot = _frozen(tmp_path, "ref", _unnamed(model(), index))
    without = _frozen(tmp_path, "without", _without(model(), index))
    assert snapshot.graph == without.graph
    assert snapshot.canonical_digest == without.canonical_digest
    assert gap_codes(snapshot) == [CODE, *gap_codes(without)]
    assert snapshot.gaps[1:] == without.gaps


def test_such_a_table_is_reported_once_whatever_else_it_lacks(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.append(Table(name="", columns=[]))  # no name and no physical facts
    snapshot = _frozen(tmp_path, "ref", semantic)
    assert [(gap.code, gap.path, gap.reason) for gap in snapshot.gaps] == [
        (CODE, _place(2), _reason(0))
    ]
    assert snapshot.graph == _frozen(tmp_path, "named", model()).graph


def test_each_such_table_is_named_by_its_own_place(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.insert(1, _one_more())
    semantic.tables.append(Table(name="", physical=Physical(engine="clickhouse")))
    snapshot = _frozen(tmp_path, "ref", semantic)
    assert [(gap.path, gap.reason) for gap in snapshot.gaps] == [
        (_place(1), _reason(1)),
        (_place(3), _reason(0)),
    ]
    assert snapshot.graph == _frozen(tmp_path, "named", model()).graph


def test_the_gaps_of_tables_stand_in_the_order_of_the_tables(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.insert(0, Table(name="dm.first"))
    semantic.tables.insert(1, _one_more())
    semantic.tables.append(Table(name="dm.last"))
    snapshot = _frozen(tmp_path, "ref", semantic)
    assert [(gap.code, gap.path) for gap in snapshot.gaps] == [
        ("gap.missing_physical", "tables.dm.first.physical"),
        (CODE, _place(1)),
        ("gap.missing_physical", "tables.dm.last.physical"),
    ]


def test_a_join_to_a_column_of_such_a_table_dangles(tmp_path: Path) -> None:
    semantic = _unnamed(model(), 1)
    semantic.joins = [Join(left="dm.sales_daily.store_id", right=".id")]
    snapshot = _frozen(tmp_path, "ref", semantic)
    assert gap_codes(snapshot) == [CODE, "gap.dangling_join"]
    assert snapshot.gaps[1].reason == "join endpoint is not a declared field: .id"
    assert snapshot.graph.joins == []


def test_a_name_of_blanks_is_a_name(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.append(Table(name=" ", physical=Physical(engine="clickhouse")))
    snapshot = _frozen(tmp_path, "ref", semantic)
    assert snapshot.gaps == []
    assert [table.id for table in snapshot.graph.tables] == [" ", "dm.sales_daily", "dm.stores"]


def test_no_table_of_a_graph_has_empty_text_for_its_id() -> None:
    assert TableNode(id=" ", classification=DataClassification.INTERNAL).id == " "
    with pytest.raises(ValidationError) as refused:
        TableNode(id="", classification=DataClassification.INTERNAL)
    (error,) = refused.value.errors()
    assert (error["loc"], error["type"]) == (("id",), "string_too_short")


# --- the diff -----------------------------------------------------------------


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_a_candidate_that_loses_the_name_of_a_table_is_compared(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    base = _frozen(tmp_path, "base", model())
    candidate = _frozen(tmp_path, "candidate", _unnamed(model(), index))
    changes = _named_changes(base, candidate)
    assert ("gap.appeared", "block", _place(index), f"{CODE}: {_reason(columns)}") in changes
    assert ("table.removed", "block", name, "table is gone from the semantic model") in changes
    assert not [change for change in changes if change[0] == "table.added"]


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_a_candidate_that_names_the_table_is_compared(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    base = _frozen(tmp_path, "base", _unnamed(model(), index))
    candidate = _frozen(tmp_path, "candidate", model())
    changes = _named_changes(base, candidate)
    assert ("gap.resolved", "advisory", _place(index), f"{CODE} no longer reported") in changes
    assert ("table.added", "advisory", name, "new table") in changes
    assert not [change for change in changes if change[0] == "table.removed"]


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_refs_that_hold_such_a_table_alike_do_not_differ(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    base = _frozen(tmp_path, "base", _unnamed(model(), index))
    candidate = _frozen(tmp_path, "candidate", _unnamed(model(), index))
    assert diff_snapshots(base, candidate).changes == []


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
def test_what_else_such_a_table_declares_is_not_compared(
    tmp_path: Path, index: int, name: str, columns: int
) -> None:
    described = _unnamed(model(), index)
    described.tables[index].description = "described another way"
    described.tables[index].grain = []
    described.tables[index].columns[0].type = "String"
    base = _frozen(tmp_path, "base", _unnamed(model(), index))
    candidate = _frozen(tmp_path, "candidate", described)
    assert base.canonical_digest == candidate.canonical_digest
    assert diff_snapshots(base, candidate).changes == []


# --- the gate -----------------------------------------------------------------


def _case(tmp_path: Path, base: SemanticModel, candidate: SemanticModel) -> Case:
    """A run over the two models; the asset and the result are of the refs as written."""
    case = Case(tmp_path)
    base.dump(case.base / "model.yaml")
    return case.with_candidate(candidate).with_asset().with_results(ROWS)


def _with_one_more() -> SemanticModel:
    semantic = semantic_model()
    semantic.tables.append(_one_more())
    return semantic


def _described_apart(index: int) -> SemanticModel:
    semantic = _unnamed(semantic_model(), index)
    semantic.tables[index].description = "described another way"
    return semantic


@pytest.mark.parametrize(("index", "name", "columns"), TABLES)
@pytest.mark.parametrize(("side", "other"), [("base", "candidate"), ("candidate", "base")])
def test_the_gate_blocks_on_a_ref_with_such_a_table(
    tmp_path: Path, side: str, other: str, index: int, name: str, columns: int
) -> None:
    models = {side: _unnamed(semantic_model(), index), other: semantic_model()}
    report = _case(tmp_path, models["base"], models["candidate"]).run()
    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert _gap(index, columns) in payload[side]
    assert not [gap for gap in payload[other] if gap["code"] == CODE]
    # The refs are compared: the gap is a named hole of a graph that is still read.
    assert summary_of(report, "semantic.diff") == "semantic.changed"
    appeared = "gap.appeared" if side == "candidate" else "gap.resolved"
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert (appeared, _place(index)) in {(c["code"], c["subject"]) for c in diff["changes"]}


def test_the_gate_blocks_on_refs_that_hold_such_a_table_alike(tmp_path: Path) -> None:
    report = _case(tmp_path, _with_one_more(), _with_one_more()).run()
    assert report.verdict is Verdict.BLOCK
    assert [check.check_id for check in report.blocking()] == ["semantic.gaps"]
    assert summary_of(report, "semantic.diff") == "semantic.unchanged"
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert payload == {"base": [_gap(2, 1)], "candidate": [_gap(2, 1)]}
    gaps = next(check for check in report.checks if check.check_id == "semantic.gaps")
    assert gaps.detail == "2 gap(s) across both snapshots, 2 blocking"
    assert gaps.check_version == CHECK_VERSION


def test_the_same_refs_without_such_a_table_pass(tmp_path: Path) -> None:
    report = _case(tmp_path, semantic_model(), semantic_model()).run()
    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert status_of(report, "semantic.gaps") is CheckStatus.PASS
    assert _check_payload(tmp_path, report, "semantic.gaps") == {"base": [], "candidate": []}


def test_the_evidence_versions_say_the_rule_changed() -> None:
    assert int(VERIFIER_VERSION) >= 95
    assert int(CHECK_VERSION) >= 70


# --- the command --------------------------------------------------------------

#: Every pair of refs that ended the command in an exception: base model, candidate model.
ENDED_IN_AN_EXCEPTION = {
    "the base, the first table": lambda: (_unnamed(semantic_model(), 0), semantic_model()),
    "the candidate, the first table": lambda: (semantic_model(), _unnamed(semantic_model(), 0)),
    "the base, the second table": lambda: (_unnamed(semantic_model(), 1), semantic_model()),
    "the candidate, the second table": lambda: (semantic_model(), _unnamed(semantic_model(), 1)),
    "both, the first table described apart": lambda: (
        _unnamed(semantic_model(), 0),
        _described_apart(0),
    ),
    "both, the second table described apart": lambda: (
        _unnamed(semantic_model(), 1),
        _described_apart(1),
    ),
    "one more table, the base": lambda: (_with_one_more(), semantic_model()),
    "one more table, the candidate": lambda: (semantic_model(), _with_one_more()),
}


@pytest.mark.parametrize("pair", list(ENDED_IN_AN_EXCEPTION))
def test_decision_verify_exits_by_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pair: str
) -> None:
    base, candidate = ENDED_IN_AN_EXCEPTION[pair]()
    case = _case(tmp_path, base, candidate)
    assert main(_verify_argv(case)) == 4
    printed = capsys.readouterr()
    assert printed.err == ""
    lines = printed.out.splitlines()
    assert f"{'block':<8} {'semantic.gaps':<22} semantic.gaps_present" in lines
    assert lines[-1].startswith("verdict: block (")
    (run_dir,) = (case.root / "evidence").iterdir()
    assert (run_dir / "manifest.json").is_file()

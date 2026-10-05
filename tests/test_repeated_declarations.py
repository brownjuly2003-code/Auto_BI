"""What a model declares more than once is one node of the graph, or a named gap and none.

`model.yaml` is hand-edited, and its schema takes the name of a table, a column of a table
and a join any number of times. The graph then held a node for each declaration, several
under one id, and its readers answered apart: a diff keeps the last node of an id, the gate
reads the first. Two refs that differed in the first declaration of a table alone were
compared as unchanged (`docs/specs/model-declarations-snapshot.md`). The graph now holds one
node to an id: declarations it could not tell apart are one declaration and an advisory
`gap.<kind>_repeated`, declarations it could are a blocking `gap.<kind>_conflict` and no
node.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.cli import main
from auto_bi.semantic.diff import diff_snapshots
from auto_bi.semantic.gateway import (
    FieldNode,
    JoinNode,
    SemanticGraph,
    SemanticSnapshot,
    TableNode,
)
from auto_bi.semantic.model import (
    Additivity,
    Aggregation,
    Column,
    ColumnRole,
    DataClassification,
    Join,
    Physical,
    SemanticModel,
    Table,
)
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

SALES = "dm.sales_daily"
STORES = "dm.stores"
JOIN = "dm.sales_daily.store_id->dm.stores.id"
ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]

EditTable = Callable[[Table], None]
EditColumn = Callable[[Column], None]


def _repeated(kind: str, where: str, times: int = 2) -> dict[str, str]:
    """The gap of declarations that are alike, as a bundle keeps it."""
    return {
        "code": f"gap.{kind}_repeated",
        "severity": "advisory",
        "path": where,
        "reason": f"{kind} is declared {times} times, alike: the graph holds it once",
        "source_value_ref": "",
    }


def _conflict(kind: str, where: str, times: int = 2) -> dict[str, str]:
    """The gap of declarations that are not alike, as a bundle keeps it."""
    left_out = "it and its columns are" if kind == "table" else "it is"
    return {
        "code": f"gap.{kind}_conflict",
        "severity": "block",
        "path": where,
        "reason": f"{kind} is declared {times} times and not alike: {left_out} not in the graph",
        "source_value_ref": "",
    }


def _gaps(snapshot: SemanticSnapshot) -> list[dict[str, str]]:
    return [gap.model_dump(mode="json") for gap in snapshot.gaps]


def _where(snapshot: SemanticSnapshot) -> list[tuple[str, str]]:
    return [(gap.code, gap.path) for gap in snapshot.gaps]


def _frozen(tmp_path: Path, name: str, semantic: SemanticModel) -> SemanticSnapshot:
    return source(tmp_path / name, semantic=semantic).snapshot()


def _place(semantic: SemanticModel, name: str) -> int:
    return next(index for index, table in enumerate(semantic.tables) if table.name == name)


def _again(
    semantic: SemanticModel, name: str, edit: EditTable | None = None, *, first: bool = False
) -> SemanticModel:
    """*semantic* with the table *name* declared once more, the new declaration edited.

    The new declaration stands right ahead of the one the model has, or after every table.
    """
    place = _place(semantic, name)
    copy = semantic.tables[place].model_copy(deep=True)
    if edit is not None:
        edit(copy)
    semantic.tables.insert(place if first else len(semantic.tables), copy)
    return semantic


def _column_again(
    semantic: SemanticModel,
    table: str,
    column: str,
    edit: EditColumn | None = None,
    *,
    first: bool = False,
) -> SemanticModel:
    """*semantic* with the column declared once more in its table, the new one edited."""
    columns = semantic.tables[_place(semantic, table)].columns
    place = next(index for index, declared in enumerate(columns) if declared.name == column)
    copy = columns[place].model_copy(deep=True)
    if edit is not None:
        edit(copy)
    columns.insert(place if first else len(columns), copy)
    return semantic


def _join_again(
    semantic: SemanticModel, cardinality: str = "", *, first: bool = False
) -> SemanticModel:
    """*semantic* with its join declared once more, of another cardinality when one is given."""
    copy = semantic.joins[0].model_copy(deep=True)
    if cardinality:
        copy.type = cardinality
    semantic.joins.insert(0 if first else len(semantic.joins), copy)
    return semantic


def _without_table(semantic: SemanticModel, name: str) -> SemanticModel:
    semantic.tables = [table for table in semantic.tables if table.name != name]
    return semantic


def _without_column(semantic: SemanticModel, table: str, column: str) -> SemanticModel:
    owner = semantic.tables[_place(semantic, table)]
    owner.columns = [declared for declared in owner.columns if declared.name != column]
    return semantic


def _without_joins(semantic: SemanticModel) -> SemanticModel:
    semantic.joins = []
    return semantic


# --- what one more declaration of a table may say -----------------------------------------


def _described(table: Table) -> None:
    table.description = "described another way"


def _regrained(table: Table) -> None:
    table.grain = [*table.grain, "one_more_key"]


def _public(table: Table) -> None:
    table.classification = DataClassification.PUBLIC


def _first_column_retyped(table: Table) -> None:
    table.columns[0].type = "Nullable(String)"


def _first_column_restricted(table: Table) -> None:
    table.columns[0].classification = DataClassification.RESTRICTED


def _a_column_less(table: Table) -> None:
    del table.columns[-1]


def _a_column_more(table: Table) -> None:
    table.columns.append(Column(name="one_more", type="String", role=ColumnRole.DIMENSION))


def _a_column_twice(table: Table) -> None:
    table.columns.append(table.columns[0].model_copy(deep=True))


def _another_engine(table: Table) -> None:
    table.physical = Physical(engine="greenplum")


def _another_sorting_key(table: Table) -> None:
    assert table.physical is not None
    table.physical.sorting_key = ["one_more_key"]


def _another_table_engine(table: Table) -> None:
    assert table.physical is not None
    table.physical.table_engine = "ReplacingMergeTree"


def _another_distribution_key(table: Table) -> None:
    assert table.physical is not None
    table.physical.distribution_key = ["one_more_key"]


def _another_partition_key(table: Table) -> None:
    assert table.physical is not None
    table.physical.partition_key = "toYear(one_more_key)"


def _no_physical_facts(table: Table) -> None:
    table.physical = None


#: What one more declaration says otherwise: each is a thing the graph holds of a table.
APART: list[tuple[str, EditTable]] = [
    ("described apart", _described),
    ("another grain", _regrained),
    ("another classification", _public),
    ("a column of another type", _first_column_retyped),
    ("a column of another classification", _first_column_restricted),
    ("a column less", _a_column_less),
    ("a column more", _a_column_more),
    ("a column declared twice", _a_column_twice),
    ("another engine", _another_engine),
    ("another table engine", _another_table_engine),
    ("another sorting key", _another_sorting_key),
    ("another distribution key", _another_distribution_key),
    ("another partition key", _another_partition_key),
    ("no physical facts", _no_physical_facts),
]

#: The gate is asked about three of them: the rule is the snapshot's, the grade the gate's.
GRADED: list[tuple[str, EditTable]] = [
    ("described apart", _described),
    ("a column less", _a_column_less),
    ("no physical facts", _no_physical_facts),
]


def _word_for_word(table: Table) -> None:
    return None


def _other_words(table: Table) -> None:
    table.synonyms = ["another word for it"]
    table.columns[0].synonyms = ["another word for the column"]
    table.columns[0].top_values = ["a sample"]


def _other_statistics(table: Table) -> None:
    assert table.physical is not None
    table.physical.rows = 1_000_000
    table.physical.bytes = 64_000_000
    table.physical.cardinality = {"id": 12}
    table.physical.captured_at = "2026-10-01T00:00:00Z"


def _columns_the_other_way_round(table: Table) -> None:
    table.columns.reverse()


def _default_class_said_or_left_unsaid(table: Table) -> None:
    # A table that names no class is internal: saying so, or leaving it unsaid, says the same.
    assert table.classification in (None, DataClassification.INTERNAL)
    table.classification = None if table.classification else DataClassification.INTERNAL


#: What one more declaration may say otherwise and still give the graph the same nodes.
ALIKE: list[tuple[str, EditTable]] = [
    ("word for word", _word_for_word),
    ("other words for the prompt", _other_words),
    ("other statistics", _other_statistics),
    ("columns the other way round", _columns_the_other_way_round),
    ("the default class said or left unsaid", _default_class_said_or_left_unsaid),
]

BOTH_PLACES = [pytest.param(True, id="new one first"), pytest.param(False, id="new one last")]


def _table_cases(edits: list[tuple[str, EditTable]]) -> list[object]:
    return [
        pytest.param(table, edit, id=f"{table}, {label}")
        for table in (SALES, STORES)
        for label, edit in edits
    ]


# --- a table ----------------------------------------------------------------------------


@pytest.mark.parametrize(("table", "edit"), _table_cases(ALIKE))
@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_table_declared_twice_alike_is_one_table_and_a_named_repeat(
    tmp_path: Path, table: str, edit: EditTable, first: bool
) -> None:
    once = _frozen(tmp_path, "once", model())
    snapshot = _frozen(tmp_path, "ref", _again(model(), table, edit, first=first))

    assert _gaps(snapshot) == [_repeated("table", f"tables.{table}")]
    assert snapshot.graph == once.graph
    assert snapshot.canonical_digest == once.canonical_digest


@pytest.mark.parametrize(("table", "edit"), _table_cases(APART))
@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_table_declared_twice_and_not_alike_is_a_blocking_gap_and_no_table(
    tmp_path: Path, table: str, edit: EditTable, first: bool
) -> None:
    without = _frozen(tmp_path, "without", _without_table(model(), table))
    snapshot = _frozen(tmp_path, "ref", _again(model(), table, edit, first=first))

    assert _gaps(snapshot)[0] == _conflict("table", f"tables.{table}")
    assert table not in [node.id for node in snapshot.graph.tables]
    assert table not in {field.table for field in snapshot.graph.fields}
    # The graph is the one of the model that does not declare the table at all, and what
    # follows from the table not being there is said as it is said of that model.
    assert snapshot.graph == without.graph
    assert snapshot.canonical_digest == without.canonical_digest
    assert snapshot.gaps[1:] == without.gaps


def test_no_physical_facts_and_facts_declared_empty_are_not_alike(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables[_place(semantic, STORES)].physical = Physical(engine="")
    snapshot = _frozen(tmp_path, "ref", _again(semantic, STORES, _no_physical_facts))

    # Both declarations give the same table node. Only one of them leaves the physical gate
    # with nothing to run on, and the graph does not say which of them the model means.
    assert _gaps(snapshot)[0] == _conflict("table", f"tables.{STORES}")
    assert "gap.missing_physical" not in gap_codes(snapshot)


@pytest.mark.parametrize("times", [3, 4])
def test_a_repeat_says_how_many_times_the_table_is_declared(tmp_path: Path, times: int) -> None:
    semantic = model()
    for _ in range(times - 1):
        _again(semantic, STORES)
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot) == [_repeated("table", f"tables.{STORES}", times)]
    assert snapshot.graph == _frozen(tmp_path, "once", model()).graph


@pytest.mark.parametrize("apart", [0, 1, 2], ids=["the first", "the second", "the third"])
def test_one_declaration_apart_among_three_is_a_conflict(tmp_path: Path, apart: int) -> None:
    semantic = _again(_again(model(), STORES), STORES)
    declarations = [table for table in semantic.tables if table.name == STORES]
    _described(declarations[apart])
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot)[0] == _conflict("table", f"tables.{STORES}", 3)
    assert STORES not in [node.id for node in snapshot.graph.tables]


def test_a_table_declared_twice_without_physical_facts_lacks_them_once(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables[_place(semantic, STORES)].physical = None
    snapshot = _frozen(tmp_path, "ref", _again(semantic, STORES))

    assert _where(snapshot) == [
        ("gap.table_repeated", f"tables.{STORES}"),
        ("gap.missing_physical", f"tables.{STORES}.physical"),
    ]


def test_nothing_else_is_said_of_a_table_in_conflict(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.append(Table(name="dm.aside"))  # no physical facts, no columns
    semantic.tables.append(
        Table(
            name="dm.aside",
            description="declared again, another way",
            columns=[
                Column(name="x", type="String", role=ColumnRole.DIMENSION),
                Column(name="x", type="UInt8", role=ColumnRole.DIMENSION),
            ],
        )
    )
    snapshot = _frozen(tmp_path, "ref", semantic)

    # No word of the physical facts neither declaration has, nor of the column one of them
    # declares two ways.
    assert _gaps(snapshot) == [_conflict("table", "tables.dm.aside")]
    assert snapshot.graph == _frozen(tmp_path, "once", model()).graph


def test_the_gap_of_a_table_stands_where_its_name_first_stands(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.insert(0, Table(name="dm.first"))
    semantic.tables.insert(2, Table(name="dm.middle"))
    semantic.tables.append(Table(name="dm.last"))
    _again(semantic, SALES)  # after every table
    _again(semantic, "dm.middle", _described)
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _where(snapshot) == [
        ("gap.missing_physical", "tables.dm.first.physical"),
        ("gap.table_repeated", f"tables.{SALES}"),
        ("gap.table_conflict", "tables.dm.middle"),
        ("gap.missing_physical", "tables.dm.last.physical"),
    ]


def test_names_that_differ_are_tables_of_their_own(tmp_path: Path) -> None:
    semantic = model()
    for name in ("dm.Stores", "dm.stores ", "DM.STORES"):
        semantic.tables.append(Table(name=name, physical=Physical(engine="clickhouse")))
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert snapshot.gaps == []
    assert len(snapshot.graph.tables) == 5


# --- a column ---------------------------------------------------------------------------


def _retyped(column: Column) -> None:
    column.type = "Nullable(String)"


def _another_role(column: Column) -> None:
    column.role = ColumnRole.TIME


def _restricted(column: Column) -> None:
    column.classification = DataClassification.RESTRICTED


def _described_column(column: Column) -> None:
    column.description = "described another way"


def _another_key(column: Column) -> None:
    column.fk = "dm.elsewhere.id"


def _another_aggregation(column: Column) -> None:
    column.agg = Aggregation.MAX


def _another_additivity(column: Column) -> None:
    column.additivity = Additivity.NON_ADDITIVE


#: What one more declaration of a column says otherwise: each thing the graph holds of a field.
COLUMN_APART: list[tuple[str, EditColumn]] = [
    ("another type", _retyped),
    ("another role", _another_role),
    ("another classification", _restricted),
    ("described apart", _described_column),
    ("another foreign key", _another_key),
    ("another aggregation", _another_aggregation),
    ("another additivity", _another_additivity),
]


def _same_column(column: Column) -> None:
    return None


def _column_words(column: Column) -> None:
    column.synonyms = ["another word"]
    column.top_values = ["a sample"]


def _class_of_the_table_said(column: Column) -> None:
    assert column.classification is None
    column.classification = DataClassification.INTERNAL  # what the fixture's table is


COLUMN_ALIKE: list[tuple[str, EditColumn]] = [
    ("word for word", _same_column),
    ("other words for the prompt", _column_words),
    ("the class of its table said", _class_of_the_table_said),
]

#: A column the fixture's metric reads as a dimension, and one its join reads as well.
COLUMNS = ["category", "store_id"]


def _column_cases(edits: list[tuple[str, EditColumn]]) -> list[object]:
    return [
        pytest.param(column, edit, id=f"{column}, {label}")
        for column in COLUMNS
        for label, edit in edits
    ]


@pytest.mark.parametrize(("column", "edit"), _column_cases(COLUMN_ALIKE))
@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_column_declared_twice_alike_is_one_field_and_a_named_repeat(
    tmp_path: Path, column: str, edit: EditColumn, first: bool
) -> None:
    once = _frozen(tmp_path, "once", model())
    twice = _column_again(model(), SALES, column, edit, first=first)
    snapshot = _frozen(tmp_path, "ref", twice)

    assert _gaps(snapshot) == [_repeated("field", f"fields.{SALES}.{column}")]
    assert snapshot.graph == once.graph
    assert snapshot.canonical_digest == once.canonical_digest


@pytest.mark.parametrize(("column", "edit"), _column_cases(COLUMN_APART))
@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_column_declared_twice_and_not_alike_is_a_blocking_gap_and_no_field(
    tmp_path: Path, column: str, edit: EditColumn, first: bool
) -> None:
    without = _frozen(tmp_path, "without", _without_column(model(), SALES, column))
    twice = _column_again(model(), SALES, column, edit, first=first)
    snapshot = _frozen(tmp_path, "ref", twice)

    assert _gaps(snapshot)[0] == _conflict("field", f"fields.{SALES}.{column}")
    assert f"{SALES}.{column}" not in [field.id for field in snapshot.graph.fields]
    assert SALES in [node.id for node in snapshot.graph.tables]
    # The graph is the one of the model whose table does not declare the column.
    assert snapshot.graph == without.graph
    assert snapshot.canonical_digest == without.canonical_digest
    assert snapshot.gaps[1:] == without.gaps


def test_a_repeat_says_how_many_times_the_column_is_declared(tmp_path: Path) -> None:
    semantic = _column_again(_column_again(model(), SALES, "region"), SALES, "region")
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot) == [_repeated("field", f"fields.{SALES}.region", 3)]


@pytest.mark.parametrize("first", BOTH_PLACES)
def test_one_column_apart_among_three_is_a_conflict(tmp_path: Path, first: bool) -> None:
    semantic = _column_again(model(), SALES, "category")
    snapshot = _frozen(
        tmp_path, "ref", _column_again(semantic, SALES, "category", _retyped, first=first)
    )

    # The fixture's metric reads every column of the table: with the field out of the graph
    # the metric reads a field the model does not declare, by the rule that was there.
    assert _gaps(snapshot)[0] == _conflict("field", f"fields.{SALES}.category", 3)
    assert _where(snapshot)[1:] == [
        ("gap.metric_field_missing", "metrics.metric://sales/gross_margin_pct")
    ]


def test_a_metric_that_reads_a_field_in_conflict_reads_a_field_the_model_does_not_declare(
    tmp_path: Path,
) -> None:
    snapshot = _frozen(tmp_path, "ref", _column_again(model(), SALES, "region", _retyped))

    assert _where(snapshot) == [
        ("gap.field_conflict", f"fields.{SALES}.region"),
        ("gap.metric_field_missing", "metrics.metric://sales/gross_margin_pct"),
    ]
    assert snapshot.gaps[1].reason == (
        f"metric references a field the model does not declare: {SALES}.region"
    )


def test_columns_of_two_tables_that_make_one_id_are_a_conflict(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.append(
        Table(
            name="dm",
            columns=[Column(name="stores.id", type="UInt32", role=ColumnRole.DIMENSION)],
            physical=Physical(engine="clickhouse"),
        )
    )
    snapshot = _frozen(tmp_path, "ref", semantic)

    # `dm.stores.id` is the column `id` of `dm.stores` and the column `stores.id` of `dm`:
    # one id, two fields, and the node of each says which table and column it is.
    assert _where(snapshot) == [
        ("gap.field_conflict", "fields.dm.stores.id"),
        ("gap.dangling_join", f"joins.{JOIN}"),
    ]
    assert _gaps(snapshot)[0] == _conflict("field", "fields.dm.stores.id")
    assert "dm.stores.id" not in [field.id for field in snapshot.graph.fields]
    assert [node.id for node in snapshot.graph.tables] == ["dm", SALES, STORES]
    assert snapshot.graph.joins == []


def test_a_table_and_a_column_of_it_may_both_be_repeated(tmp_path: Path) -> None:
    semantic = _column_again(model(), SALES, "region")
    snapshot = _frozen(tmp_path, "ref", _again(semantic, SALES))

    assert _gaps(snapshot) == [
        _repeated("table", f"tables.{SALES}"),
        _repeated("field", f"fields.{SALES}.region"),
    ]
    assert snapshot.graph == _frozen(tmp_path, "once", model()).graph


def test_the_gaps_of_fields_stand_after_those_of_tables_as_the_fields_first_stand(
    tmp_path: Path,
) -> None:
    semantic = model()
    semantic.tables.append(Table(name="dm.last"))
    _column_again(semantic, STORES, "id")
    _column_again(semantic, SALES, "category", _retyped)
    _column_again(semantic, SALES, "date")
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _where(snapshot)[:4] == [
        ("gap.missing_physical", "tables.dm.last.physical"),
        ("gap.field_repeated", f"fields.{SALES}.date"),
        ("gap.field_conflict", f"fields.{SALES}.category"),
        ("gap.field_repeated", f"fields.{STORES}.id"),
    ]


# --- a join -----------------------------------------------------------------------------


@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_join_declared_twice_alike_is_one_join_and_a_named_repeat(
    tmp_path: Path, first: bool
) -> None:
    once = _frozen(tmp_path, "once", model())
    snapshot = _frozen(tmp_path, "ref", _join_again(model(), first=first))

    assert _gaps(snapshot) == [_repeated("join", f"joins.{JOIN}")]
    assert snapshot.graph == once.graph
    assert snapshot.canonical_digest == once.canonical_digest


@pytest.mark.parametrize("first", BOTH_PLACES)
def test_a_join_declared_twice_and_not_alike_is_a_blocking_gap_and_no_join(
    tmp_path: Path, first: bool
) -> None:
    without = _frozen(tmp_path, "without", _without_joins(model()))
    snapshot = _frozen(tmp_path, "ref", _join_again(model(), "one_to_one", first=first))

    assert _gaps(snapshot) == [_conflict("join", f"joins.{JOIN}")]
    assert snapshot.graph.joins == []
    assert snapshot.graph == without.graph
    assert snapshot.canonical_digest == without.canonical_digest


def test_a_repeat_says_how_many_times_the_join_is_declared(tmp_path: Path) -> None:
    snapshot = _frozen(tmp_path, "ref", _join_again(_join_again(model())))

    assert _gaps(snapshot) == [_repeated("join", f"joins.{JOIN}", 3)]


def test_a_join_is_counted_by_its_own_declarations_beside_another_join(tmp_path: Path) -> None:
    other = Join(left=f"{SALES}.region", right=f"{STORES}.id")
    alike = _join_again(model())
    alike.joins.append(other)
    apart = _join_again(model(), "one_to_one")
    apart.joins.append(other)
    repeated = _frozen(tmp_path, "alike", alike)
    conflict = _frozen(tmp_path, "apart", apart)

    # Three joins in the model, two declarations of the one that is declared again.
    assert _gaps(repeated) == [_repeated("join", f"joins.{JOIN}")]
    assert [join.id for join in repeated.graph.joins] == [f"{SALES}.region->{STORES}.id", JOIN]
    assert _gaps(conflict) == [_conflict("join", f"joins.{JOIN}")]
    assert [join.id for join in conflict.graph.joins] == [f"{SALES}.region->{STORES}.id"]


@pytest.mark.parametrize("first", BOTH_PLACES)
def test_one_join_apart_among_three_is_a_conflict(tmp_path: Path, first: bool) -> None:
    semantic = _join_again(_join_again(model()), "one_to_one", first=first)
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot) == [_conflict("join", f"joins.{JOIN}", 3)]


def test_a_dangling_join_declared_twice_dangles_once(tmp_path: Path) -> None:
    semantic = model()
    semantic.joins = [
        Join(left=f"{SALES}.store_id", right="dm.nowhere.id"),
        Join(left=f"{SALES}.store_id", right="dm.nowhere.id"),
    ]
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert [(gap.code, gap.path, gap.reason) for gap in snapshot.gaps] == [
        (
            "gap.dangling_join",
            f"joins.{SALES}.store_id->dm.nowhere.id",
            "join endpoint is not a declared field: dm.nowhere.id",
        )
    ]
    assert snapshot.graph.joins == []


def test_a_dangling_join_declared_two_ways_is_a_conflict(tmp_path: Path) -> None:
    semantic = model()
    semantic.joins = [
        Join(left=f"{SALES}.store_id", right="dm.nowhere.id"),
        Join(left=f"{SALES}.store_id", right="dm.nowhere.id", type="one_to_one"),
    ]
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot) == [_conflict("join", f"joins.{SALES}.store_id->dm.nowhere.id")]


def test_joins_of_other_ends_that_make_one_id_are_a_conflict(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables.append(
        Table(
            name="t",
            columns=[
                Column(name=name, type="String", role=ColumnRole.DIMENSION)
                for name in ("x", "y->t.z", "x->t.y", "z")
            ],
            physical=Physical(engine="clickhouse"),
        )
    )
    # `t.x->t.y->t.z` is the join of `t.x` to `t.y->t.z` and the join of `t.x->t.y` to `t.z`.
    semantic.joins = [Join(left="t.x", right="t.y->t.z"), Join(left="t.x->t.y", right="t.z")]
    snapshot = _frozen(tmp_path, "ref", semantic)

    assert _gaps(snapshot) == [_conflict("join", "joins.t.x->t.y->t.z")]
    assert snapshot.graph.joins == []
    assert [field.id for field in snapshot.graph.fields if field.table == "t"] == [
        "t.x",
        "t.x->t.y",
        "t.y->t.z",
        "t.z",
    ]


def test_a_join_to_a_field_in_conflict_dangles(tmp_path: Path) -> None:
    snapshot = _frozen(tmp_path, "ref", _column_again(model(), STORES, "id", _retyped))

    assert _where(snapshot) == [
        ("gap.field_conflict", f"fields.{STORES}.id"),
        ("gap.dangling_join", f"joins.{JOIN}"),
    ]
    assert snapshot.gaps[1].reason == f"join endpoint is not a declared field: {STORES}.id"


def test_the_gaps_of_joins_stand_after_those_of_fields_as_the_joins_first_stand(
    tmp_path: Path,
) -> None:
    semantic = _column_again(model(), SALES, "category")
    semantic.joins = [
        Join(left=f"{SALES}.store_id", right=f"{STORES}.id"),
        Join(left=f"{SALES}.region", right=f"{STORES}.id"),
        Join(left=f"{SALES}.store_id", right=f"{STORES}.id"),
        Join(left=f"{SALES}.region", right=f"{STORES}.id", type="one_to_one"),
    ]
    snapshot = _frozen(tmp_path, "ref", semantic)

    # The join of `store_id` stands first in the model and second by its id.
    assert _where(snapshot) == [
        ("gap.field_repeated", f"fields.{SALES}.category"),
        ("gap.join_repeated", f"joins.{JOIN}"),
        ("gap.join_conflict", f"joins.{SALES}.region->{STORES}.id"),
    ]
    assert [join.id for join in snapshot.graph.joins] == [JOIN]


# --- the graph --------------------------------------------------------------------------

SHAPES: dict[str, Callable[[], SemanticModel]] = {
    "a table twice alike": lambda: _again(model(), SALES),
    "a table twice, apart": lambda: _again(model(), SALES, _described, first=True),
    "a table three times": lambda: _again(_again(model(), STORES), STORES, _public),
    "a column twice alike": lambda: _column_again(model(), SALES, "revenue"),
    "a column twice, apart": lambda: _column_again(model(), SALES, "revenue", _retyped),
    "a join twice alike": lambda: _join_again(model()),
    "a join twice, apart": lambda: _join_again(model(), "one_to_one"),
    "a table, a column and a join twice": lambda: _join_again(
        _column_again(_again(model(), STORES), SALES, "date")
    ),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_graph_holds_one_node_to_an_id(tmp_path: Path, shape: str) -> None:
    graph = _frozen(tmp_path, "ref", SHAPES[shape]()).graph

    for ids in (
        [node.id for node in graph.tables],
        [node.id for node in graph.fields],
        [node.id for node in graph.joins],
    ):
        assert len(ids) == len(set(ids))


def _table_node(name: str, description: str = "") -> TableNode:
    return TableNode(id=name, classification=DataClassification.INTERNAL, description=description)


def _field_node(table: str, column: str, kind: str = "String") -> FieldNode:
    return FieldNode(
        id=f"{table}.{column}",
        table=table,
        column=column,
        type=kind,
        role=ColumnRole.DIMENSION,
        classification=DataClassification.INTERNAL,
    )


def _join_node(left: str, right: str, cardinality: str = "many_to_one") -> JoinNode:
    return JoinNode(id=f"{left}->{right}", left=left, right=right, type=cardinality)


TWO_NODES_OF_ONE_ID: dict[str, tuple[str, str, Callable[[], SemanticGraph]]] = {
    "a table, alike": (
        "table",
        "t",
        lambda: SemanticGraph(tables=[_table_node("t"), _table_node("t")]),
    ),
    "a table, apart": (
        "table",
        "t",
        lambda: SemanticGraph(tables=[_table_node("t"), _table_node("u"), _table_node("t", "x")]),
    ),
    "a field, alike": (
        "field",
        "t.x",
        lambda: SemanticGraph(fields=[_field_node("t", "x"), _field_node("t", "x")]),
    ),
    "a field, apart": (
        "field",
        "t.x",
        lambda: SemanticGraph(fields=[_field_node("t", "x"), _field_node("t", "x", "UInt8")]),
    ),
    "a join, alike": (
        "join",
        "t.x->t.y",
        lambda: SemanticGraph(joins=[_join_node("t.x", "t.y"), _join_node("t.x", "t.y")]),
    ),
    "a join, apart": (
        "join",
        "t.x->t.y",
        lambda: SemanticGraph(
            joins=[_join_node("t.x", "t.y"), _join_node("t.x", "t.y", "one_to_one")]
        ),
    ),
}


@pytest.mark.parametrize("twice", list(TWO_NODES_OF_ONE_ID))
def test_no_graph_holds_two_nodes_of_one_id(twice: str) -> None:
    kind, name, build = TWO_NODES_OF_ONE_ID[twice]

    with pytest.raises(ValidationError) as refused:
        build()

    (error,) = refused.value.errors()
    assert error["type"] == "value_error"
    assert error["msg"] == (
        f"Value error, more than one {kind} node of the graph has the id {name!r}"
    )


def test_a_graph_of_one_node_to_an_id_is_held() -> None:
    graph = SemanticGraph(
        tables=[_table_node("t"), _table_node("u")],
        fields=[_field_node("t", "x"), _field_node("u", "x")],
        joins=[_join_node("t.x", "u.x"), _join_node("u.x", "t.x")],
    )

    assert (len(graph.tables), len(graph.fields), len(graph.joins)) == (2, 2, 2)


# --- the diff ---------------------------------------------------------------------------


def _named_changes(base: SemanticSnapshot, candidate: SemanticSnapshot) -> set[tuple[str, ...]]:
    return {
        (change.code, change.severity.value, change.subject, change.detail)
        for change in diff_snapshots(base, candidate).changes
    }


REPEATS: dict[str, tuple[str, str, Callable[[], SemanticModel]]] = {
    "a table": ("table", f"tables.{SALES}", lambda: _again(model(), SALES)),
    "a column": (
        "field",
        f"fields.{SALES}.revenue",
        lambda: _column_again(model(), SALES, "revenue"),
    ),
    "a join": ("join", f"joins.{JOIN}", lambda: _join_again(model())),
}


@pytest.mark.parametrize("what", list(REPEATS))
def test_a_ref_that_repeats_a_declaration_alike_differs_in_the_gap_alone(
    tmp_path: Path, what: str
) -> None:
    kind, where, build = REPEATS[what]
    once = _frozen(tmp_path, "once", model())
    twice = _frozen(tmp_path, "twice", build())
    reason = _repeated(kind, where)["reason"]

    assert twice.canonical_digest == once.canonical_digest
    assert _named_changes(once, twice) == {
        ("gap.appeared", "advisory", where, f"gap.{kind}_repeated: {reason}")
    }
    assert _named_changes(twice, once) == {
        ("gap.resolved", "advisory", where, f"gap.{kind}_repeated no longer reported")
    }


def test_a_candidate_that_declares_a_table_another_way_as_well_loses_it(tmp_path: Path) -> None:
    base = _frozen(tmp_path, "base", model())
    candidate = _frozen(tmp_path, "candidate", _again(model(), STORES, _described))
    changes = _named_changes(base, candidate)
    reason = _conflict("table", f"tables.{STORES}")["reason"]

    assert ("gap.appeared", "block", f"tables.{STORES}", f"gap.table_conflict: {reason}") in changes
    assert ("table.removed", "block", STORES, "table is gone from the semantic model") in changes


@pytest.mark.parametrize("first", BOTH_PLACES)
@pytest.mark.parametrize(("label", "edit"), APART, ids=[label for label, _ in APART])
def test_refs_that_differ_in_one_declaration_of_a_table_are_not_compared_as_unchanged(
    tmp_path: Path, label: str, edit: EditTable, first: bool
) -> None:
    # Two declarations of the table in each ref; the candidate's new one says a thing
    # otherwise. A diff that keeps the last node of an id saw no change when it stood first.
    base = _frozen(tmp_path, "base", _again(model(), SALES, first=first))
    candidate = _frozen(tmp_path, "candidate", _again(model(), SALES, edit, first=first))
    changes = _named_changes(base, candidate)

    assert _where(base)[0] == ("gap.table_repeated", f"tables.{SALES}")
    assert _where(candidate)[0] == ("gap.table_conflict", f"tables.{SALES}")
    assert ("table.removed", "block", SALES, "table is gone from the semantic model") in changes
    assert (
        "gap.resolved",
        "advisory",
        f"tables.{SALES}",
        "gap.table_repeated no longer reported",
    ) in changes
    assert [change for change in changes if change[0] == "gap.appeared" and change[1] == "block"]


def test_refs_that_hold_a_table_in_conflict_do_not_differ_in_what_it_says(tmp_path: Path) -> None:
    base = _frozen(tmp_path, "base", _again(model(), STORES, _described))
    candidate = _frozen(tmp_path, "candidate", _again(model(), STORES, _public))

    # Neither graph holds the table: what its declarations say is not compared.
    assert _gaps(base) == _gaps(candidate)
    assert base.canonical_digest == candidate.canonical_digest
    assert diff_snapshots(base, candidate).changes == []


# --- the gate ---------------------------------------------------------------------------


def _case(tmp_path: Path, base: SemanticModel, candidate: SemanticModel) -> Case:
    """A run over the two models; the asset and the result are of the refs as written."""
    case = Case(tmp_path)
    base.dump(case.base / "model.yaml")
    return case.with_candidate(candidate).with_asset().with_results(ROWS)


@pytest.mark.parametrize(("side", "other"), [("base", "candidate"), ("candidate", "base")])
@pytest.mark.parametrize(("label", "edit"), GRADED, ids=[label for label, _ in GRADED])
def test_the_gate_blocks_on_a_ref_with_a_table_in_conflict(
    tmp_path: Path, side: str, other: str, label: str, edit: EditTable
) -> None:
    models = {side: _again(semantic_model(), STORES, edit, first=True), other: semantic_model()}
    report = _case(tmp_path, models["base"], models["candidate"]).run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert payload[side][0] == _conflict("table", f"tables.{STORES}")
    assert payload[other] == []
    # The refs are compared: the gap is a named hole of a graph that is still read.
    assert summary_of(report, "semantic.diff") == "semantic.changed"
    appeared = "gap.appeared" if side == "candidate" else "gap.resolved"
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert (appeared, f"tables.{STORES}") in {(c["code"], c["subject"]) for c in diff["changes"]}


@pytest.mark.parametrize(("label", "edit"), GRADED, ids=[label for label, _ in GRADED])
def test_the_gate_blocks_on_refs_that_hold_the_same_conflict(
    tmp_path: Path, label: str, edit: EditTable
) -> None:
    # Both refs declare the table the same two ways, the one that differs first.
    base = _again(semantic_model(), STORES, edit, first=True)
    candidate = _again(semantic_model(), STORES, edit, first=True)
    report = _case(tmp_path, base, candidate).run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert payload["base"] == payload["candidate"]
    assert payload["base"][0] == _conflict("table", f"tables.{STORES}")
    assert summary_of(report, "semantic.diff") == "semantic.unchanged"


def test_the_gate_names_a_repeat_and_goes_on(tmp_path: Path) -> None:
    candidate = _join_again(_again(semantic_model(), STORES))
    report = _case(tmp_path, semantic_model(), candidate).run()

    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert report.blocking() == []
    assert status_of(report, "semantic.gaps") is CheckStatus.ADVISORY
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    assert _check_payload(tmp_path, report, "semantic.gaps") == {
        "base": [],
        "candidate": [_repeated("table", f"tables.{STORES}"), _repeated("join", f"joins.{JOIN}")],
    }
    gaps = next(check for check in report.checks if check.check_id == "semantic.gaps")
    assert gaps.detail == "2 gap(s) across both snapshots, 0 blocking"
    assert gaps.check_version == CHECK_VERSION
    assert status_of(report, "semantic.diff") is CheckStatus.ADVISORY
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert {(change["code"], change["subject"]) for change in diff["changes"]} == {
        ("gap.appeared", f"tables.{STORES}"),
        ("gap.appeared", f"joins.{JOIN}"),
    }


def test_a_ref_that_repeats_alike_is_the_snapshot_of_the_ref_that_does_not(tmp_path: Path) -> None:
    once = _case(tmp_path / "once", semantic_model(), semantic_model())
    twice = _case(tmp_path / "twice", semantic_model(), _again(semantic_model(), SALES))

    # A result attested against the one is a result attested against the other.
    assert twice.candidate_digest == once.candidate_digest
    assert twice.candidate_digest == twice.base_digest


def test_a_column_declared_twice_alike_is_a_repeat_the_superset_compile_refuses(
    tmp_path: Path,
) -> None:
    candidate = _column_again(semantic_model(), SALES, "revenue")
    report = _case(tmp_path, semantic_model(), candidate).run()

    # The graph holds the field once. The planner of the Superset dataset reads the model,
    # finds two columns under one alias and builds nothing: bi.contract says so.
    assert status_of(report, "semantic.gaps") is CheckStatus.ADVISORY
    assert _check_payload(tmp_path, report, "semantic.gaps")["candidate"] == [
        _repeated("field", f"fields.{SALES}.revenue")
    ]
    assert [check.check_id for check in report.blocking()] == ["bi.contract"]
    assert summary_of(report, "bi.contract") == "bi.compile_refused"


def test_the_same_refs_without_a_repeat_pass(tmp_path: Path) -> None:
    report = _case(tmp_path, semantic_model(), semantic_model()).run()

    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert status_of(report, "semantic.gaps") is CheckStatus.PASS
    assert _check_payload(tmp_path, report, "semantic.gaps") == {"base": [], "candidate": []}


def test_the_evidence_versions_say_the_rule_changed() -> None:
    assert int(VERIFIER_VERSION) >= 97
    assert int(CHECK_VERSION) >= 72


# --- the command ------------------------------------------------------------------------

#: Pairs of refs the command passed with nothing said of the table or the join: the base
#: model and the candidate model.
PASSED_UNSAID: dict[str, Callable[[], tuple[SemanticModel, SemanticModel]]] = {
    "the candidate declares a table again ahead of it, described apart": lambda: (
        semantic_model(),
        _again(semantic_model(), SALES, _described, first=True),
    ),
    "the candidate declares a table again after it, a column less": lambda: (
        semantic_model(),
        _again(semantic_model(), SALES, _a_column_less),
    ),
    "both declare a table two ways": lambda: (
        _again(semantic_model(), SALES, _described, first=True),
        _again(semantic_model(), SALES, _described, first=True),
    ),
    "the base declares a table again ahead of it, of another class": lambda: (
        _again(semantic_model(), SALES, _public, first=True),
        semantic_model(),
    ),
    "the candidate declares a join again ahead of it, of another cardinality": lambda: (
        semantic_model(),
        _join_again(semantic_model(), "one_to_one", first=True),
    ),
    "both declare a join with two cardinalities": lambda: (
        _join_again(semantic_model(), "one_to_one"),
        _join_again(semantic_model(), "one_to_one"),
    ),
}


@pytest.mark.parametrize("pair", list(PASSED_UNSAID))
def test_decision_verify_blocks_on_declarations_that_are_not_alike(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pair: str
) -> None:
    base, candidate = PASSED_UNSAID[pair]()
    case = _case(tmp_path, base, candidate)

    assert main(_verify_argv(case)) == 4

    printed = capsys.readouterr()
    assert printed.err == ""
    lines = printed.out.splitlines()
    assert f"{'block':<8} {'semantic.gaps':<22} semantic.gaps_present" in lines
    assert lines[-1].startswith("verdict: block (")
    (run_dir,) = (case.root / "evidence").iterdir()
    assert (run_dir / "manifest.json").is_file()


REPEATED: dict[str, Callable[[], SemanticModel]] = {
    "a table": lambda: _again(semantic_model(), STORES),
    "a table of the metric": lambda: _again(semantic_model(), SALES, first=True),
    "a join": lambda: _join_again(semantic_model()),
}


@pytest.mark.parametrize("what", list(REPEATED))
@pytest.mark.parametrize("side", ["base", "candidate", "both"])
def test_decision_verify_passes_refs_that_repeat_a_declaration_alike(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], what: str, side: str
) -> None:
    base = REPEATED[what]() if side in ("base", "both") else semantic_model()
    candidate = REPEATED[what]() if side in ("candidate", "both") else semantic_model()
    case = _case(tmp_path, base, candidate)

    assert main(_verify_argv(case)) == 0

    printed = capsys.readouterr()
    assert printed.err == ""
    lines = printed.out.splitlines()
    assert f"{'advisory':<8} {'semantic.gaps':<22} semantic.gaps_present" in lines
    assert lines[-1].startswith("verdict: pass_with_advisory (")

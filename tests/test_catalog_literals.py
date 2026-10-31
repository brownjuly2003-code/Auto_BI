"""Catalog values retain their characters and cannot supply SQL conditions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.semantic.gateway import SemanticSnapshot, SourceKind, _build_graph
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.verification import environment_probe as probe
from auto_bi.verification.environment import EnvironmentQuestions, TableQuestion, questions_for
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION
from tests.test_decision_verify import metric_contract, product_contract, semantic_model

NAMES = [
    pytest.param("x\\' OR 1=1 --", id="slash-quote"),
    pytest.param("x\\\\' OR 1=1 --", id="two-slashes-quote"),
    pytest.param("tail\\", id="tail"),
    pytest.param("a'b", id="quote"),
    pytest.param("a''b", id="two-quotes"),
    pytest.param("\\\\", id="two-slashes"),
    pytest.param("a\nb", id="line-feed"),
    pytest.param(r"a\nb", id="slash-n"),
    pytest.param(r"a\x27b", id="slash-x27"),
    pytest.param("missing\u00a5' OR 1=1 --", id="yen-quote"),
    pytest.param("missing\\\u00a5' OR 1=1 --", id="slash-yen-quote"),
    pytest.param("a\x01b", id="control"),
    pytest.param("\u00e9", id="letter"),
]
MODES = [
    ("clickhouse", True),
    *[
        (engine, standard)
        for engine in ("postgres", "greenplum", "greengage")
        for standard in (True, False)
    ],
]


def read_literals(sql: str, engine: str, standard: bool = True) -> tuple[str, list[str]]:
    """Read the emitted single-quoted forms; retain every byte outside them.

    PostgreSQL E strings and legacy plain strings interpret backslash escapes;
    ClickHouse always does. Doubled quotes denote one quote in all three forms.
    This reader covers the emitted syntax, not arbitrary SQL or dollar quoting.
    """
    outside: list[str] = []
    values: list[str] = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "0": "\0"}
    while index < len(sql):
        escape_form = engine != "clickhouse" and sql[index : index + 2] == "E'"
        if sql[index] != "'" and not escape_form:
            outside.append(sql[index])
            index += 1
            continue
        index += 2 if escape_form else 1
        backslashes = engine == "clickhouse" or escape_form or not standard
        value: list[str] = []
        while index < len(sql):
            char = sql[index]
            index += 1
            if char == "\\" and backslashes:
                assert index < len(sql), "backslash without a following character"
                char = sql[index]
                index += 1
                if char in escapes:
                    value.append(escapes[char])
                elif engine == "clickhouse" and char not in "\\'\"`":
                    value.append("\\" + char)
                else:
                    value.append(char)
            elif char == "'":
                if index < len(sql) and sql[index] == "'":
                    value.append("'")
                    index += 1
                else:
                    break
            else:
                value.append(char)
        else:
            raise AssertionError("unterminated string literal")
        values.append("".join(value))
        outside.append("<literal>")
    return "".join(outside), values


class Catalog:
    def __init__(self, engine: str, *, present: bool = True, readable: bool = True) -> None:
        self.engine = engine
        self.present = present
        self.readable = readable
        self.asked: list[str] = []
        self.role = "role_1"
        self.reached = "role_2"

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.asked.append(sql)
        if "currentUser() AS value" in sql:
            return [{"value": "gate"}]
        if "system.settings" in sql:
            return [{"value": "0"}]
        if "system.enabled_roles" in sql:
            return [{"role_name": self.role}]
        if "system.role_grants" in sql:
            return [{"user_name": "gate", "role_name": None, "granted_role_name": self.reached}]
        if "system.grants" in sql:
            return []
        if "current_user AS name" in sql:
            return [{"name": "gate", "superuser": False, "bypassrls": False}]
        if "SELECT r.rolname AS name, r.rolsuper" in sql:
            return [{"name": self.reached, "superuser": False}]
        if "has_table_privilege" in sql or "AS column_insert" in sql:
            return [
                {
                    "name": self.reached,
                    "insert": False,
                    "update": False,
                    "delete": False,
                    "truncate": False,
                    "column_insert": False,
                    "column_update": False,
                    "owner_name": "mart_owner",
                    "owns_mart": False,
                    "schema_owner_name": "schema_owner",
                    "owns_schema": False,
                }
            ]
        if "system.tables" in sql or "pg_tables" in sql:
            return [{"engine": "MergeTree", "tablename": "mart_1"}] if self.present else []
        if "AS rows_seen" in sql:
            return [{"rows_seen": 0, "max_event_time": None}]
        if "'SELECT') AS ok" in sql:
            return [{"ok": self.readable}]
        if "relrowsecurity" in sql:
            return [{"enabled": False}]
        return []


def collect_case(
    engine: str,
    slot: str = "table",
    value: str = "mart_1",
    *,
    present: bool = True,
    readable: bool = True,
    clock: str = "event_time",
) -> tuple[Catalog, EnvironmentQuestions]:
    parts = {
        "database": "db_1",
        "table": "mart_1",
        "column": "column_1",
        "role": "role_1",
        "reached": "role_2",
    }
    parts[slot] = value
    catalog = Catalog(engine, present=present, readable=readable)
    catalog.role = parts["role"]
    catalog.reached = parts["reached"]
    questions = EnvironmentQuestions(
        tables=[
            TableQuestion(
                table=f"{parts['database']}.{parts['table']}",
                event_time_column=clock,
                columns={parts["column"]: "text"},
            )
        ]
    )
    return catalog, questions


def ask(catalog: Catalog, questions: EnvironmentQuestions) -> None:
    probe.collect(catalog, questions, engine=catalog.engine, target="stand")


def from_contract(questions: EnvironmentQuestions) -> EnvironmentQuestions:
    raw = metric_contract()
    raw["grain"]["table"] = questions.tables[0].table
    raw["time"]["event_time_column"] = questions.tables[0].event_time_column
    graph = _build_graph(semantic_model(), [MetricContract.model_validate(raw)], [])
    digest = sha256_digest(graph.model_dump(mode="json"))
    snapshot = SemanticSnapshot(
        source_kind=SourceKind.NATIVE_MODEL_YAML,
        source_version="3",
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
    )
    result = questions_for(DecisionProductSpec.model_validate(product_contract()), snapshot)
    assert result.tables[0].table == questions.tables[0].table
    return result


@pytest.mark.parametrize("engine,standard", MODES)
@pytest.mark.parametrize("slot", ["database", "table", "column", "role", "reached"])
@pytest.mark.parametrize("value", NAMES)
def test_catalog_values_and_sql_structure(
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    standard: bool,
    slot: str,
    value: str,
) -> None:
    # The real identifier guard refuses punctuation before the later catalog questions.
    # Replace only the COUNT/MAX table identifier here to exercise those later literals;
    # separate tests below retain the guard and pin the actual collection order.
    monkeypatch.setattr(probe, "_qualified", lambda _: '"db_1"."mart_1"')
    baseline, base_questions = collect_case(engine, slot, "marker_1")
    actual, questions = collect_case(engine, slot, value)
    ask(baseline, base_questions)
    ask(actual, questions)
    assert len(actual.asked) == len(baseline.asked)
    changed = 0
    for old, new in zip(baseline.asked, actual.asked, strict=True):
        old_shape, old_values = read_literals(old, engine, standard)
        new_shape, new_values = read_literals(new, engine, standard)
        assert new_shape == old_shape, new
        assert new_values == [item.replace("marker_1", value) for item in old_values], new
        changed += sum("marker_1" in item for item in old_values)
    # CH columns are matched in memory; PG's enabled roles are not interpolated.
    assert (
        changed > 0
        or (engine == "clickhouse" and slot == "column")
        or (engine != "clickhouse" and slot == "role")
    )


@pytest.mark.parametrize("engine,standard", MODES)
@pytest.mark.parametrize("slot", ["database", "table"])
@pytest.mark.parametrize("value", NAMES)
def test_absent_mart_literals_before_identifier_check(
    engine: str,
    standard: bool,
    slot: str,
    value: str,
) -> None:
    baseline, base_questions = collect_case(
        engine, slot, "marker_1", present=False, clock="bad clock"
    )
    actual, questions = collect_case(engine, slot, value, present=False, clock="bad clock")
    base_questions = from_contract(base_questions)
    questions = from_contract(questions)
    ask(baseline, base_questions)
    facts = probe.collect(actual, questions, engine=engine, target="stand")
    assert facts.tables[0].exists is False
    assert not any("AS rows_seen" in sql for sql in actual.asked)
    assert len(actual.asked) == len(baseline.asked)
    for old, new in zip(baseline.asked, actual.asked, strict=True):
        shape, values = read_literals(old, engine, standard)
        assert read_literals(new, engine, standard) == (
            shape,
            [item.replace("marker_1", value) for item in values],
        )


@pytest.mark.parametrize("engine", ["clickhouse", "postgres", "greenplum", "greengage"])
def test_present_mart_retains_identifier_refusal(engine: str) -> None:
    catalog, questions = collect_case(engine, value="x\\' OR 1=1 --")
    with pytest.raises(probe.UnsafeIdentifierError):
        ask(catalog, questions)
    assert any("system.tables" in sql or "pg_tables" in sql for sql in catalog.asked)
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


@pytest.mark.parametrize("engine", ["postgres", "greenplum", "greengage"])
def test_unreadable_mart_keeps_fact_before_clock_check(engine: str) -> None:
    catalog, questions = collect_case(engine, readable=False, clock="bad clock")
    facts = probe.collect(catalog, questions, engine=engine, target="stand")
    assert facts.tables[0].exists is True
    assert facts.tables[0].readable is False
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


@pytest.mark.parametrize("engine", ["clickhouse", "postgres", "greenplum", "greengage"])
def test_ordinary_catalog_sql_bytes(engine: str) -> None:
    catalog, questions = collect_case(engine)
    ask(catalog, questions)
    encoded = json.dumps(catalog.asked, ensure_ascii=False, separators=(",", ":")).encode()
    expected = (
        "a14068adf36a63adcfae83954b08453a8b0bd2c92345f2e947ef17071fcf0ae1"
        if engine == "clickhouse"
        else "fe7a23d8ac983230c6b4cb84d9eacdfa0c60679ed7d7b93c0316ca578b411c48"
    )
    assert hashlib.sha256(encoded).hexdigest() == expected


@pytest.mark.parametrize("value", NAMES)
def test_postgres_escape_prefix_only_for_backslash(value: str) -> None:
    literal = probe._literal(value)
    assert literal.startswith("E'") == ("\\" in value)
    if "\\" not in value:
        assert literal == "'" + value.replace("'", "''") + "'"


def test_catalog_literal_version() -> None:
    assert int(VERIFIER_VERSION) >= 122
    assert int(CHECK_VERSION) >= 80


@pytest.mark.parametrize("engine", ["postgres", "greenplum", "greengage"])
@pytest.mark.parametrize("function", ["has_table_privilege", "has_any_column_privilege"])
def test_relation_name_error_precedes_absence(engine: str, function: str) -> None:
    from psycopg.errors import InvalidName

    catalog, questions = collect_case(engine, value="missing\\' OR 1=1 --", present=False)
    error = InvalidName("invalid name syntax")

    def run(sql: str) -> list[dict[str, Any]]:
        if function + "(" in sql:
            catalog.asked.append(sql)
            raise error
        return catalog(sql)

    with pytest.raises(InvalidName) as caught:
        probe.collect(run, questions, engine=engine, target="stand")
    assert caught.value is error
    assert error.sqlstate == "42602"
    assert not any("FROM pg_tables" in sql for sql in catalog.asked)

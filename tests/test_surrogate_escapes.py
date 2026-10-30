"""An escape that names a surrogate makes a YAML file one that cannot be read.

A double-quoted scalar may name a character by its code. The scanner `yaml.safe_load` runs
builds a string from the code of a surrogate as well — half of the UTF-16 form of a
character, with no UTF-8 form and no form in canonical JSON — where libyaml refuses the
escape. What became of that string was up to whoever read the file next: a schema took it
or refused it, a digest had no form for it, a report could not be printed
(`docs/specs/unreadable-inputs.md`). The loader every decision command reads YAML with
refuses the document with the place of the scalar, and each reader answers as it answers
any text PyYAML refuses.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from yaml.parser import ParserError

from auto_bi import cli, yaml_document
from auto_bi.cli import main
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.verification.model import EXIT_ERROR, Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from auto_bi.yaml_document import SurrogateEscapeError, load_yaml_document, unread_reason
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case, _check_payload, status_of, summary_of
from tests.test_investigation_inputs import Inputs, _places, _with
from tests.test_unbuilt_contract_yaml import PIN, VERIFIED, _snapshot
from tests.test_unreadable_run_inputs import (
    CAPTURE_FILES,
    RUN_FILES,
    _capture_refused,
    _case,
    _never,
    _plant_lone_surrogate,
    _refused,
)

BACKSLASH = chr(92)
NAME = "SurrogateEscapeError"
UNREAD = f"cannot read contract: {NAME}"
UNREAD_IN_A_SNAPSHOT = f"metric contract cannot be read: {NAME}"

#: A surrogate as a string holds one, alone and between two letters.
LONE = chr(0xD800)
TEXTS = [pytest.param(LONE, id="alone"), pytest.param(f"a{LONE}b", id="inside")]

#: Where the fixtures keep the escape that the tests of the commands plant.
PLANTED: dict[str, tuple[str | int, ...]] = {
    "the base model": ("tables", 0, "description"),
    "the candidate model": ("tables", 0, "description"),
    "a dashboard spec": ("title",),
    "a verified asset": ("question",),
    "the recorded results": ("asset://sales/weekly_margin_by_region", "result", "rows", 0, 0),
}


def _escape(code: int) -> str:
    """The escape of sixteen bits that names *code*, as a double-quoted scalar holds it."""
    return f"{BACKSLASH}u{code:04x}"


def _long_escape(code: int) -> str:
    """The escape of thirty-two bits that names *code*."""
    return f"{BACKSLASH}U{code:08x}"


E = _escape(0xD800)
#: The two escapes JSON reads as one character outside the basic plane.
PAIR = _escape(0xD83D) + _escape(0xDE00)
#: Calls a test lets the loader make for a text of one line: many times what it makes.
WORK = 200_000

#: Texts with one such escape, and the line and the column its scalar begins at.
REFUSED = [
    pytest.param(f'title: "{E}"\n', 1, 8, id="a value"),
    pytest.param(f'"{E}": 1\n', 1, 1, id="a key"),
    pytest.param(f'a: 1\nb:\n  - "x{_escape(0xDFFF)}y"\n', 3, 5, id="an item, between letters"),
    pytest.param(f'a: [1, {{b: "{_escape(0xDBFF)}"}}]\n', 1, 12, id="in a flow mapping"),
    pytest.param(f'a: "first\n  second {_escape(0xDC00)}"\n', 1, 4, id="in its second line"),
    pytest.param(f'a: "{_long_escape(0xD800)}"\n', 1, 4, id="an escape of thirty-two bits"),
    pytest.param(f'a: "{BACKSLASH}uD800"\n', 1, 4, id="capital digits"),
    pytest.param(f'"{E}"\n', 1, 1, id="the document itself"),
    pytest.param(f'a: "{PAIR}"\n', 1, 4, id="two that JSON reads as one character"),
    pytest.param(f'a: !!str "{E}"\n', 1, 4, id="a scalar with a tag"),
    pytest.param(f'a: &held "{E}"\n', 1, 4, id="a scalar with an anchor"),
]

#: Several such escapes in one text, and where the first of them begins.
FIRST = [
    pytest.param(f'"k{E}": "v{E}"\n', 1, 1, id="a key before its value"),
    pytest.param(f'a: "{E}"\nb: "{E}"\n', 1, 4, id="an entry before the next"),
    pytest.param(f'a:\n  - c: 1\n    d: "{E}"\nb: "{E}"\n', 3, 8, id="what an entry holds first"),
    pytest.param(f'a: ok\nb: ["x", "{E}"]\n"{E}": 1\n', 2, 10, id="an item before the next key"),
    pytest.param(f'a: ["{E}", "{E}"]\n', 1, 5, id="an item before the next"),
]

#: The letters of the escape where the scanner replaces nothing, and the text they are.
NOT_AN_ESCAPE = [
    pytest.param(f"a: '{E}'\n", E, id="in single quotes"),
    pytest.param(f"a: {E}\n", E, id="a plain scalar"),
    pytest.param(f"a: |\n  {E}\n", f"{E}\n", id="a literal block"),
    pytest.param(f"a: >\n  {E}\n", f"{E}\n", id="a folded block"),
    pytest.param(f'a: "{BACKSLASH}{E}"\n', E, id="after an escaped backslash"),
    pytest.param(f'a: b  # "{E}"\n', "b", id="in a comment"),
]

#: Something else PyYAML would refuse while it builds, beside such an escape.
NEVER_BUILT = [
    pytest.param(f'as_of: 2026-02-30\nb: "{E}"\n', 2, 4, id="a date that does not exist"),
    pytest.param(f'a: !!int "{E}"\n', 1, 4, id="a tag the scalar is no value of"),
    pytest.param(f'a: !!binary "{E}"\n', 1, 4, id="!!binary that is not base64"),
    pytest.param(f'a: "{E}"\nb: !!python/object:os.system x\n', 1, 4, id="a foreign tag"),
    pytest.param(f'? ["{E}"]\n: 1\n', 1, 4, id="a key that cannot be one"),
]


def _escaped(document: Any) -> str:
    """*document* as YAML: PyYAML writes the surrogate it holds as the escape."""
    text: str = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    assert text.lower().count(E) == 1
    return text


def _position(text: str) -> tuple[int, int]:
    """Where the one scalar of *text* that holds the escape begins, as its reader finds it."""
    [(number, line)] = [
        (number, line) for number, line in enumerate(text.split("\n"), 1) if E in line.lower()
    ]
    return number, line.rindex('"', 0, line.lower().index(E)) + 1


def _unread_at(path: Path) -> str:
    """The reason a reader of *path* is given, by where the escape stands in the file."""
    line, column = _position(path.read_text(encoding="utf-8"))
    return f"cannot read {path}: {NAME} at line {line}, column {column}"


def _unreadable(path: Path) -> dict[str, Any]:
    return {
        "code": "contract.unreadable",
        "severity": "block",
        "subject": f"$1/{path.name}",
        "source": f"$1/{path.name}",
        "message": UNREAD,
    }


def _unread_gap(name: str, raw: bytes) -> dict[str, Any]:
    return {
        "code": "gap.contract_invalid",
        "severity": "block",
        "path": f"contracts.{name}",
        "reason": UNREAD_IN_A_SNAPSHOT,
        "source_value_ref": digest_bytes(raw),
    }


class _TooMuchWork(BaseException):
    """The loader made more calls than a test allows it."""


def _loaded_within(budget: int, text: str) -> Any:
    """What the loader gives for *text*; a failure once it has made more than *budget* calls.

    Calls of builtins count: the walk of a composed document makes no others between two
    scalars, and a walk that does not end makes them without end — it would not fail a test,
    it would keep it running.
    """
    calls = 0

    def count(_frame: object, event: str, _arg: object) -> None:
        nonlocal calls
        if event in ("call", "c_call"):
            calls += 1
            if calls > budget:
                raise _TooMuchWork

    previous = sys.getprofile()
    try:
        sys.setprofile(count)
        return load_yaml_document(text)
    except _TooMuchWork:
        raise AssertionError(f"the loader made more than {budget} calls") from None
    finally:
        sys.setprofile(previous)


def _utf8() -> io.TextIOWrapper:
    """A stream that encodes what is printed to it, as the stream of a terminal does."""
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")


def _printed(stream: io.TextIOWrapper) -> str:
    stream.flush()
    buffer = stream.buffer
    assert isinstance(buffer, io.BytesIO)
    return buffer.getvalue().decode("utf-8")


# --- the loader ---------------------------------------------------------------------


def test_pyyaml_builds_a_string_of_such_an_escape() -> None:
    """What is refused here: the string has no UTF-8 form, and two escapes stay two."""
    assert yaml.safe_load(f'a: "{E}"\n') == {"a": LONE}
    assert yaml.safe_load(f'a: "{PAIR}"\n') == {"a": chr(0xD83D) + chr(0xDE00)}
    with pytest.raises(UnicodeEncodeError):
        LONE.encode("utf-8")


@pytest.mark.parametrize(("text", "line", "column"), REFUSED)
def test_such_an_escape_is_refused_where_its_scalar_begins(
    text: str, line: int, column: int
) -> None:
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(text)

    refusal = caught.value
    assert isinstance(refusal, yaml.YAMLError)
    assert refusal.__cause__ is None
    assert refusal.problem == "found an escape that names a surrogate"
    assert (refusal.problem_mark.line, refusal.problem_mark.column) == (line - 1, column - 1)
    assert unread_reason(refusal) == f"{NAME} at line {line}, column {column}"


@pytest.mark.parametrize("escape", [_escape, _long_escape], ids=["sixteen bits", "thirty-two"])
@pytest.mark.parametrize(
    ("code", "refused"),
    [
        (0xD7FF, False),
        (0xD800, True),
        (0xDBFF, True),
        (0xDC00, True),
        (0xDFFF, True),
        (0xE000, False),
    ],
    ids=["d7ff", "d800", "dbff", "dc00", "dfff", "e000"],
)
def test_the_surrogates_are_refused_and_what_stands_next_to_them_is_not(
    escape: Any, code: int, refused: bool
) -> None:
    text = f'a: "x{escape(code)}y"\n'

    if refused:
        with pytest.raises(SurrogateEscapeError):
            load_yaml_document(text)
    else:
        assert load_yaml_document(text) == {"a": f"x{chr(code)}y"}


@pytest.mark.parametrize(("text", "held"), NOT_AN_ESCAPE)
def test_the_letters_of_such_an_escape_are_text_where_nothing_is_escaped(
    text: str, held: str
) -> None:
    assert load_yaml_document(text) == {"a": held}
    assert load_yaml_document(text) == yaml.safe_load(text)


def test_a_character_outside_the_basic_plane_is_read_as_itself_and_by_its_own_escape() -> None:
    character = chr(0x1F600)

    assert load_yaml_document(f'a: "{character}"\n') == {"a": character}
    assert load_yaml_document(f"a: {character}\n") == {"a": character}
    assert load_yaml_document(f'a: "{_long_escape(0x1F600)}"\n') == {"a": character}


@pytest.mark.parametrize(("text", "line", "column"), FIRST)
def test_the_first_of_several_is_the_one_refused(text: str, line: int, column: int) -> None:
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(text)

    assert unread_reason(caught.value) == f"{NAME} at line {line}, column {column}"


def test_a_scalar_an_alias_repeats_is_refused_where_it_is_anchored() -> None:
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(f'a: ok\nb: &held ["{E}"]\nc: *held\n')

    assert unread_reason(caught.value) == f"{NAME} at line 2, column 11"


def test_a_document_that_holds_itself_is_looked_at_once() -> None:
    with pytest.raises(SurrogateEscapeError) as caught:
        _loaded_within(WORK, f'&all [*all, "{E}"]\n')
    assert unread_reason(caught.value) == f"{NAME} at line 1, column 13"

    document = _loaded_within(WORK, "&all [*all, b]\n")
    assert document[0] is document
    assert document[1] == "b"

    held = _loaded_within(WORK, "a: &held {b: *held, c: [*held]}\n")["a"]
    assert held["b"] is held
    assert held["c"][0] is held


def test_the_budget_of_a_test_ends_a_walk_that_does_not_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the test above relies on: a walk that calls nothing but builtins is ended too."""

    def spin(document: yaml.Node) -> None:
        pending = [document]
        while pending:
            pending.append(pending.pop())

    previous = sys.getprofile()
    assert _loaded_within(WORK, "a: 1\n") == {"a": 1}
    assert sys.getprofile() is previous

    monkeypatch.setattr(yaml_document, "_refuse_surrogates", spin)
    with pytest.raises(AssertionError, match=f"more than {WORK} calls"):
        _loaded_within(WORK, "a: 1\n")
    assert sys.getprofile() is previous


def test_a_node_many_aliases_repeat_is_looked_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each sequence holds the one before it twice: sixteen of them repeat two scalars 65536
    times, and the document is as many nodes as its text names."""
    levels = 16
    lines = ['a0: &a0 ["x", "y"]']
    lines += [f"a{level}: &a{level} [*a{level - 1}, *a{level - 1}]" for level in range(1, levels)]
    looked: list[str] = []
    holds = yaml_document._holds_surrogate

    def counted(text: str) -> bool:
        looked.append(text)
        return holds(text)

    monkeypatch.setattr(yaml_document, "_holds_surrogate", counted)

    document = load_yaml_document("\n".join(lines) + "\n")

    assert sorted(looked) == sorted(["x", "y", *(f"a{level}" for level in range(levels))])
    assert document["a1"][0] is document["a0"]


def test_a_document_nested_deep_is_walked() -> None:
    depth = 100
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(f'{"[" * depth}"{E}"{"]" * depth}\n')

    assert unread_reason(caught.value) == f"{NAME} at line 1, column {depth + 1}"


def test_a_text_pyyaml_refuses_itself_is_refused_as_pyyaml_refuses_it() -> None:
    text = f'a: "{E}"\nb: [\n'
    with pytest.raises(yaml.YAMLError) as theirs:
        yaml.safe_load(text)
    with pytest.raises(yaml.YAMLError) as ours:
        load_yaml_document(text)

    assert type(ours.value) is type(theirs.value) is ParserError
    assert str(ours.value) == str(theirs.value)


@pytest.mark.parametrize(("text", "line", "column"), NEVER_BUILT)
def test_nothing_is_built_of_a_document_that_holds_such_an_escape(
    text: str, line: int, column: int
) -> None:
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(text)

    assert unread_reason(caught.value) == f"{NAME} at line {line}, column {column}"


def test_what_the_refusal_says_of_itself_is_not_what_a_finding_keeps() -> None:
    """Its text shows the line of the file, as the text of an error of PyYAML does."""
    with pytest.raises(SurrogateEscapeError) as caught:
        load_yaml_document(f'owner: "held by the file {E}"\n')

    said = str(caught.value)
    assert said.startswith("found an escape that names a surrogate\n")
    assert "line 1, column 8" in said
    assert "held by the file" in said
    assert "held by the file" not in unread_reason(caught.value)


# --- the validated paths ------------------------------------------------------------


def test_a_validated_file_with_such_an_escape_is_an_unreadable_contract(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = case.contracts / "revenue.yaml"
    path.write_bytes((case.contracts / "gross_margin_pct.yaml").read_bytes())
    _plant_lone_surrogate(path, "metadata", "description")

    report = validate_paths([case.contracts])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [_unreadable(path)]
    assert report.products == [VERIFIED]
    assert report.metrics == [PIN]
    assert report.verdict is Verdict.BLOCK


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize(
    "name", ["weekly_margin.yaml", "gross_margin_pct.yaml"], ids=["the product", "the metric"]
)
def test_no_place_of_a_validated_file_takes_such_an_escape(
    tmp_path: Path, name: str, text: str
) -> None:
    """At every string and every key: the file is not read, whatever its schema makes of
    the place — a field it grades, a field it ignores or no field of it."""
    case = Case(tmp_path)
    path = case.contracts / name
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    places = _places(document)
    assert len(places) > 30

    for kind, where in places:
        path.write_text(_escaped(_with(document, kind, where, text)), encoding="utf-8")
        report = validate_paths([case.contracts])
        issues = [issue.model_dump(mode="json") for issue in report.issues]
        assert issues[0] == _unreadable(path), (kind, where)
        assert [issue["source"] for issue in issues].count(f"$1/{path.name}") == 1, (kind, where)


@pytest.mark.parametrize("keys", [("kind",), ("metadata", "id")], ids=["its kind", "its id"])
def test_decision_validate_prints_the_finding_to_a_stream_that_encodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: tuple[str, ...]
) -> None:
    """The report named the contract by what the file held, and could not be printed."""
    case = Case(tmp_path)
    path = case.contracts / "revenue.yaml"
    path.write_bytes((case.contracts / "gross_margin_pct.yaml").read_bytes())
    _plant_lone_surrogate(path, *keys)

    out = _utf8()
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["decision", "validate", str(case.contracts)]) == 4
    printed = _printed(out).splitlines()
    assert printed[:2] == [
        f"{'block':<8} {'contract.unreadable':<26} $1/{path.name}",
        f"{'':<8} {UNREAD} [$1/{path.name}]",
    ]
    assert printed[-1] == "verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))"

    out = _utf8()
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["decision", "validate", str(case.contracts), "--json"]) == 4
    assert json.loads(_printed(out))["issues"] == [_unreadable(path)]


def test_a_product_with_such_an_escape_leaves_no_product_to_verify(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = case.contracts / "weekly_margin.yaml"
    _plant_lone_surrogate(path, "scope", "dimensions", 0)

    message = _refused(case)

    assert message.endswith(f"; 1 path(s) could not be read: $1/{path.name} ({UNREAD})")
    assert "\n" not in message


# --- the semantic refs --------------------------------------------------------------


@pytest.mark.parametrize("text", TEXTS)
def test_no_place_of_a_contract_of_a_ref_takes_such_an_escape(tmp_path: Path, text: str) -> None:
    case = Case(tmp_path)
    clean = _snapshot(case.base)
    path = case.base / "metrics" / "gross_margin_pct.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    places = _places(document)
    assert len(places) > 30

    for kind, where in places:
        raw = _escaped(_with(document, kind, where, text)).encode("utf-8")
        path.write_bytes(raw)
        snapshot = _snapshot(case.base)
        assert [gap.model_dump(mode="json") for gap in snapshot.gaps] == [
            _unread_gap("gross_margin_pct.yaml", raw)
        ], (kind, where)
        assert snapshot.graph.metrics == [], (kind, where)
    assert clean.gaps == []
    assert len(clean.graph.metrics) == 1


# --- the gate -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "keys",
    [("metadata", "id"), ("dimensions", 0), ("grain", "keys", 0), ("metadata", "description")],
    ids=["its id", "a dimension", "a key of its grain", "its description"],
)
def test_the_gate_blocks_on_a_validated_contract_with_such_an_escape(
    tmp_path: Path, keys: tuple[str | int, ...]
) -> None:
    case = Case(tmp_path)
    path = case.contracts / "gross_margin_pct.yaml"
    _plant_lone_surrogate(path, *keys)

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    issues = _check_payload(tmp_path, report, "contracts.validate")["issues"]
    assert issues[0] == _unreadable(path)
    assert [issue["code"] for issue in issues] == ["contract.unreadable", "metric_ref.unknown"]
    assert int(VERIFIER_VERSION) >= 94
    assert int(CHECK_VERSION) >= 69


@pytest.mark.parametrize(("side", "other"), [("base", "candidate"), ("candidate", "base")])
@pytest.mark.parametrize(
    "keys",
    [("metadata", "description"), ("dimensions", 0)],
    ids=["its description", "a dimension"],
)
def test_the_gate_blocks_on_a_contract_of_a_ref_with_such_an_escape(
    tmp_path: Path, side: str, other: str, keys: tuple[str | int, ...]
) -> None:
    case = Case(tmp_path).with_candidate()
    path = tmp_path / side / "metrics" / "gross_margin_pct.yaml"
    _plant_lone_surrogate(path, *keys)

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert payload[side] == [_unread_gap("gross_margin_pct.yaml", path.read_bytes())]
    assert payload[other] == []


# --- the run inputs -----------------------------------------------------------------


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("which", list(RUN_FILES))
def test_no_place_of_a_run_file_takes_such_an_escape(tmp_path: Path, which: str, text: str) -> None:
    """At every string and every key: the run ends before it starts, with the file and the
    place — where a schema took the value, where it refused it and where no check read it."""
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    places = _places(document)
    assert len(places) > 20

    for kind, where in places:
        path.write_text(_escaped(_with(document, kind, where, text)), encoding="utf-8")
        expected = _unread_at(path)
        assert _refused(case) == expected, (kind, where)
        if which in CAPTURE_FILES:
            assert _capture_refused(case) == expected, (kind, where)


@pytest.mark.parametrize("which", list(RUN_FILES))
def test_decision_verify_exits_2_with_the_file_and_the_place(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], which: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _plant_lone_surrogate(path, *PLANTED[which])

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {_unread_at(path)}\n"
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("which", CAPTURE_FILES)
def test_decision_capture_exits_2_with_the_file_and_the_place_and_opens_no_query(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    which: str,
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _plant_lone_surrogate(path, *PLANTED[which])
    output = case.root / "captured.yaml"
    monkeypatch.setattr(cli, "_result_capture_target", lambda engine: (_never, "env", "reader"))
    argv = ["decision", "capture", str(case.assets), "--candidate", str(case.candidate)]

    assert main([*argv, "--dwh", "clickhouse", "--output", str(output)]) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {_unread_at(path)}\n"
    assert not output.exists()


# --- the inputs of an investigation -------------------------------------------------


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("which", ["the plan", "a result"])
def test_no_place_of_an_input_of_an_investigation_takes_such_an_escape(
    tmp_path: Path, which: str, text: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    places = _places(document)
    assert len(places) > 50

    for kind, where in places:
        path.write_text(_escaped(_with(document, kind, where, text)), encoding="utf-8")
        assert inputs.refused() == _unread_at(path), (kind, where)


@pytest.mark.parametrize(
    ("which", "old", "new"),
    [
        pytest.param("a result", "unit: RUB", f'unit: "{E}"', id="the unit of a result"),
        pytest.param("the plan", "kind: InvestigationPlan", f'"{E}": 1', id="a key of the plan"),
    ],
)
def test_decision_investigate_exits_2_with_the_file_and_the_place(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], which: str, old: str, new: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1
    path.write_text(text.replace(old, new), encoding="utf-8")

    assert inputs.investigate() == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {_unread_at(path)}\n"
    assert not inputs.output.exists()

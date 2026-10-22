"""An input `decision investigate` cannot read, or reads and cannot take, is named in one line.

The command is handed a plan and one canonical result for each metric of the plan. Each file
is answered for by its path. One that cannot be opened, is not UTF-8 or holds no YAML document
that can be built was not read: `cannot read <path>: <why>`. One that was read and is not what
its schema describes is `<path> is not a valid <kind>: <where and why>`. Either way the command
ends in one line and exit 2, and writes nothing.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi import cli
from auto_bi.investigation.application import (
    OfflineInvestigationError,
    build_offline_investigation,
    write_offline_investigation,
)
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.yaml_document import schema_reason
from tests.test_investigation_cli import ARTIFACT_NAMES, PLAN_FIXTURE, RESULT_FIXTURE
from tests.test_unbuilt_contract_yaml import UNBUILT
from tests.test_unreadable_run_inputs import (
    LONE_SURROGATE,
    NOT_A_MAPPING,
    REFUSED_AT,
    _deny,
    _past_the_loader,
    _said_of,
)

#: The two kinds of file the command reads, and what each must hold.
KINDS = {"the plan": "InvestigationPlan", "a result": "CanonicalMetricResult"}
WHICH = list(KINDS)
MODELS: list[type[InvestigationPlan] | type[CanonicalMetricResult]] = [
    InvestigationPlan,
    CanonicalMetricResult,
]
MODEL_IDS = ["plan", "result"]

#: A lone surrogate as a string holds one, alone and between two letters.
LONE = chr(0xD800)
LONE_TEXTS = [pytest.param(LONE, id="alone"), pytest.param(f"a{LONE}b", id="inside")]

#: What the fixture run prints as the content addresses of its plan and of its brief.
PLAN_DIGEST = "sha256:cfbedd5368d2edeba94f2a17c19a4eebc4079b5bd048c46c93825ff41b696fcb"
BRIEF_DIGEST = "sha256:9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567"

#: The first row of the result fixture, and the row with something else for its dimension.
FIRST_ROW = '      category: A\n    value: "100"'
ROW_WITH = '      {dimension}\n    value: "100"'

UNIT_REFUSED = "unit: Value error, must be text without a lone surrogate"
NAME_REFUSED = "Value error, row dimension names must be text without a lone surrogate"


class Inputs:
    """A plan and its one result in a directory of their own, and where the output would go."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.plan = root / "plan.yaml"
        self.result = root / "result.yaml"
        self.output = root / "out"
        self.plan.write_bytes(PLAN_FIXTURE.read_bytes())
        self.result.write_bytes(RESULT_FIXTURE.read_bytes())

    def path(self, which: str) -> Path:
        return self.plan if which == "the plan" else self.result

    def refused(self, *more_results: Path) -> str:
        """What the run is refused with; nothing is written for it."""
        with pytest.raises(OfflineInvestigationError) as caught:
            write_offline_investigation(self.plan, [self.result, *more_results], self.output)
        assert not self.output.exists()
        return str(caught.value)

    def investigate(self, *more_results: Path) -> int:
        """The command over these inputs."""
        results = [str(self.result), *(str(path) for path in more_results)]
        argv = ["decision", "investigate", str(self.plan), "--results", *results]
        return cli.main([*argv, "--output", str(self.output)])


def _document(fixture: Path) -> Any:
    return yaml.safe_load(fixture.read_text(encoding="utf-8"))


def _rewrite(path: Path, change: Callable[[Any], object]) -> None:
    """Write *path* again with *change* made to the document it holds."""
    document = _document(path)
    change(document)
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (old, text.count(old))
    path.write_text(text.replace(old, new), encoding="utf-8")


def _append(path: Path, line: bytes) -> None:
    path.write_bytes(path.read_bytes() + line)


def _places(node: Any, path: tuple[Any, ...] = ()) -> list[tuple[str, tuple[Any, ...]]]:
    """Every string of a document (`value`) and every key of its mappings (`key`)."""
    found: list[tuple[str, tuple[Any, ...]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(("key", (*path, key)))
            found.extend(_places(value, (*path, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_places(value, (*path, index)))
    elif isinstance(node, str):
        found.append(("value", path))
    return found


def _with(document: Any, kind: str, path: tuple[Any, ...], text: str) -> Any:
    """*document* with *text* as the string at *path*, or as the key the path ends in."""
    changed = copy.deepcopy(document)
    node = changed
    for step in path[:-1]:
        node = node[step]
    if kind == "value":
        node[path[-1]] = text
    else:
        node[text] = node.pop(path[-1])
    return changed


# --- a file that was not read ------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_an_input_whose_yaml_cannot_be_built_is_named(
    tmp_path: Path, which: str, line: str, raised: type[BaseException]
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    _append(path, line.encode("utf-8"))

    message = inputs.refused()

    opening = f"cannot read {path}: ConstructorError ({raised.__name__}: "
    assert message.startswith(opening)
    assert message.endswith(")")
    assert "\n" not in message
    if raised is not RecursionError:  # where the stack runs out decides what that one says
        assert message == f"{opening}{_said_of(line, raised)})"


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("text", "reason"), REFUSED_AT)
def test_an_input_pyyaml_refuses_is_named_with_the_position(
    tmp_path: Path, which: str, text: str, reason: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    path.write_text(text, encoding="utf-8")

    assert inputs.refused() == f"cannot read {path}: {reason}"


@pytest.mark.parametrize("which", WHICH)
def test_an_input_that_is_not_utf8_is_named_by_the_class(tmp_path: Path, which: str) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    _append(path, b"note: caf\xe9\n")  # Latin-1, not UTF-8

    assert inputs.refused() == f"cannot read {path}: UnicodeDecodeError"


@pytest.mark.parametrize("which", WHICH)
def test_an_input_that_may_not_be_opened_is_named_by_the_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    _deny(monkeypatch, path)

    assert inputs.refused() == f"cannot read {path}: PermissionError"


@pytest.mark.parametrize("which", WHICH)
def test_an_input_that_is_not_there_is_named_by_the_class(tmp_path: Path, which: str) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    path.unlink()

    assert inputs.refused() == f"cannot read {path}: FileNotFoundError"


@pytest.mark.parametrize("which", WHICH)
def test_an_input_that_is_a_directory_is_named(tmp_path: Path, which: str) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    path.unlink()
    path.mkdir()

    # What opening a directory is refused with is the platform's: POSIX says it is one,
    # Windows that access is denied.
    assert inputs.refused() in {
        f"cannot read {path}: IsADirectoryError",
        f"cannot read {path}: PermissionError",
    }


def test_the_result_that_could_not_be_read_is_the_one_named(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    second = tmp_path / "second.yaml"
    second.write_text("tables: [a, b\n", encoding="utf-8")
    third = tmp_path / "third.yaml"
    third.write_bytes(b"\xff")

    assert inputs.refused(second, third) == (
        f"cannot read {second}: ParserError at line 2, column 1"
    )


def test_the_plan_is_read_before_the_results(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    inputs.plan.write_bytes(b"\xff")
    inputs.result.write_text("tables: [a, b\n", encoding="utf-8")

    assert inputs.refused() == f"cannot read {inputs.plan}: UnicodeDecodeError"


@pytest.mark.parametrize("model", MODELS, ids=MODEL_IDS)
@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_a_text_that_cannot_be_built_is_a_yaml_error_of_the_model(
    model: Any, line: str, raised: type[BaseException]
) -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        model.from_yaml(line)

    assert isinstance(caught.value.__cause__, raised)


# --- a file that was read and is not what the command takes ---------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("text", NOT_A_MAPPING)
def test_an_input_that_is_no_mapping_is_named_with_its_kind(
    tmp_path: Path, which: str, text: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    path.write_text(text, encoding="utf-8")

    assert inputs.refused() == (
        f"{path} is not a valid {KINDS[which]}: "
        f"<root>: Input should be a valid dictionary or instance of {KINDS[which]}"
    )


def test_a_plan_its_schema_refuses_is_named_with_where_and_why(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    _rewrite(inputs.plan, lambda plan: plan["steps"][0].update(skill_version=2))

    assert inputs.refused() == (
        f"{inputs.plan} is not a valid InvestigationPlan: "
        "steps.0: Value error, skill_version for data_health must be 1"
    )


def test_a_result_its_schema_refuses_is_named_with_where_and_why(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    _rewrite(inputs.result, lambda result: result.pop("freshness_max_age"))

    assert inputs.refused() == (
        f"{inputs.result} is not a valid CanonicalMetricResult: <root>: Value error, "
        "source_max_event_time and freshness_max_age must be supplied together"
    )


def test_the_result_its_schema_refuses_is_the_one_named(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    second = tmp_path / "second.yaml"
    second.write_bytes(RESULT_FIXTURE.read_bytes())
    _rewrite(second, lambda result: result.update(complete="perhaps"))
    third = tmp_path / "third.yaml"
    third.write_bytes(b"result_id: 5\n")

    message = inputs.refused(second, third)

    assert message.startswith(f"{second} is not a valid CanonicalMetricResult: complete: ")
    assert "\n" not in message


def test_a_plan_its_schema_refuses_is_named_before_a_result_is_read(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    _append(inputs.plan, b"unknown: 1\n")
    inputs.result.write_bytes(b"\xff")

    assert inputs.refused() == (
        f"{inputs.plan} is not a valid InvestigationPlan: unknown: Extra inputs are not permitted"
    )


@pytest.mark.parametrize(("which", "rest"), [("the plan", 4), ("a result", 2)])
def test_an_empty_input_names_three_fields_it_lacks_and_counts_the_rest(
    tmp_path: Path, which: str, rest: int
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    path.write_bytes(b"")

    message = inputs.refused()

    assert message.startswith(f"{path} is not a valid {KINDS[which]}: ")
    assert message.count(": Field required") == 3
    assert message.endswith(f": Field required; (+{rest} more)")
    assert "\n" not in message


@pytest.mark.parametrize(
    ("unknown", "counted"),
    [(1, ""), (3, ""), (4, "; (+1 more)"), (40, "; (+37 more)")],
    ids=["one", "three", "four", "forty"],
)
def test_the_refusals_past_the_third_are_counted(
    tmp_path: Path, unknown: int, counted: str
) -> None:
    inputs = Inputs(tmp_path)
    _rewrite(inputs.plan, lambda plan: plan.update({f"key_{n:02}": n for n in range(unknown)}))

    named = "; ".join(f"key_{n:02}: Extra inputs are not permitted" for n in range(min(unknown, 3)))
    assert inputs.refused() == f"{inputs.plan} is not a valid InvestigationPlan: {named}{counted}"


@pytest.mark.parametrize(
    ("key", "place"),
    [
        pytest.param("unknown", "unknown", id="a name"),
        pytest.param("по-русски", "по-русски", id="cyrillic"),
        pytest.param("a\n  b", "a b", id="a line break"),
        pytest.param("", '""', id="empty text"),
    ],
)
def test_a_key_the_schema_does_not_know_is_the_place_of_its_refusal(
    tmp_path: Path, key: str, place: str
) -> None:
    inputs = Inputs(tmp_path)
    _rewrite(inputs.plan, lambda plan: plan.update({key: 5}))

    assert inputs.refused() == (
        f"{inputs.plan} is not a valid InvestigationPlan: {place}: Extra inputs are not permitted"
    )


def test_a_key_of_an_inner_mapping_is_named_with_the_way_to_it(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    _rewrite(inputs.plan, lambda plan: plan["steps"][2].update({"": 5}))

    assert inputs.refused() == (
        f"{inputs.plan} is not a valid InvestigationPlan: "
        'steps.2."": Extra inputs are not permitted'
    )


def test_a_key_that_is_no_text_is_refused_by_the_schema(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    _append(inputs.result, b"1: 2\n")

    assert inputs.refused() == (
        f"{inputs.result} is not a valid CanonicalMetricResult: 1: Keys should be strings"
    )


def test_a_refusal_that_quotes_a_line_break_is_given_in_one_line(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    twice = ["category", "by\n  week", "by\n  week"]
    _rewrite(inputs.plan, lambda plan: plan["scope"].update(dimensions=twice))

    assert inputs.refused() == (
        f"{inputs.plan} is not a valid InvestigationPlan: "
        "scope: Value error, duplicate entries in scope.dimensions: by week"
    )


def test_the_reason_of_a_schema_gives_no_value_of_the_document() -> None:
    document = _document(PLAN_FIXTURE)
    document["question"] = ["held-by-the-file", "held-too"]
    document["budget"]["max_steps"] = "what the file holds"
    with pytest.raises(ValidationError) as caught:
        InvestigationPlan.model_validate(document)

    reason = schema_reason(caught.value)

    assert reason.startswith("question: Input should be a valid string; budget.max_steps: ")
    assert reason.count("; ") == 1
    for held in ("held-by-the-file", "held-too", "what the file holds", "https://"):
        assert held not in reason


# --- a value the artifacts have no form for -------------------------------------------
#
# The loader refuses a file that spells a surrogate, so no file hands one to the schema
# (`tests/test_surrogate_escapes.py`). The schema refuses it by itself all the same: the
# tests below that go through a file hand it in past the loader.


def test_a_unit_that_holds_a_lone_surrogate_is_refused_by_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _past_the_loader(monkeypatch)
    inputs = Inputs(tmp_path)
    _replace(inputs.result, "unit: RUB", f"unit: {LONE_SURROGATE}")

    assert inputs.refused() == (
        f"{inputs.result} is not a valid CanonicalMetricResult: {UNIT_REFUSED}"
    )


def test_a_row_dimension_named_with_a_lone_surrogate_is_refused_by_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _past_the_loader(monkeypatch)
    inputs = Inputs(tmp_path)
    _replace(inputs.result, FIRST_ROW, ROW_WITH.format(dimension=f"{LONE_SURROGATE}: A"))

    assert inputs.refused() == (
        f"{inputs.result} is not a valid CanonicalMetricResult: rows.0: {NAME_REFUSED}"
    )


def test_such_a_name_is_refused_when_what_it_holds_is_refused_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _past_the_loader(monkeypatch)
    inputs = Inputs(tmp_path)
    _replace(inputs.result, FIRST_ROW, ROW_WITH.format(dimension=f"{LONE_SURROGATE}: 5"))

    message = inputs.refused()

    # The schema looks at what a name holds before the row is looked at as a whole; the
    # place of that refusal is the name, which pydantic gives in replacement characters.
    assert message.startswith(
        f"{inputs.result} is not a valid CanonicalMetricResult: rows.0.dimensions."
    )
    assert message.endswith(": Input should be a valid string")
    assert "\n" not in message
    assert message.encode("utf-8")


@pytest.mark.parametrize("text", LONE_TEXTS)
def test_a_result_that_declares_no_dimension_refuses_such_a_name_too(text: str) -> None:
    document = _document(RESULT_FIXTURE)
    document["dimensions"] = []
    for row in document["rows"]:
        row["dimensions"] = {}
    document["rows"][2]["dimensions"] = {text: None}

    with pytest.raises(ValidationError) as caught:
        CanonicalMetricResult.model_validate(document)

    assert schema_reason(caught.value) == f"rows.2: {NAME_REFUSED}"


@pytest.mark.parametrize("text", LONE_TEXTS)
def test_such_a_name_is_refused_wherever_it_stands_among_the_names_of_a_row(text: str) -> None:
    document = _document(RESULT_FIXTURE)
    document["rows"][4]["dimensions"] = {"category": "B", text: "B", "region": "north"}

    with pytest.raises(ValidationError) as caught:
        CanonicalMetricResult.model_validate(document)

    assert schema_reason(caught.value) == f"rows.4: {NAME_REFUSED}"


@pytest.mark.parametrize("model", MODELS, ids=MODEL_IDS)
@pytest.mark.parametrize("text", LONE_TEXTS)
def test_no_place_of_an_input_takes_a_lone_surrogate(model: Any, text: str) -> None:
    """At every string and every key of the fixture: the schema refuses, and can say so."""
    document = _document(PLAN_FIXTURE if model is InvestigationPlan else RESULT_FIXTURE)
    places = _places(document)
    assert len(places) > 50

    for kind, path in places:
        with pytest.raises(ValidationError) as caught:
            model.model_validate(_with(document, kind, path, text))
        reason = schema_reason(caught.value)
        assert reason, (kind, path)
        assert "\n" not in reason, (kind, path)
        assert reason.encode("utf-8"), (kind, path)  # a line any stream can take


@pytest.mark.parametrize(
    "unit",
    ["", "RUB", "руб.", "₽", chr(0x1F4B0), " % ", "a\nb"],
    ids=["none", "letters", "cyrillic", "a sign", "outside the BMP", "spaces", "two lines"],
)
def test_a_unit_that_is_text_is_taken_as_it_is(unit: str) -> None:
    document = _document(RESULT_FIXTURE)
    document["unit"] = unit

    result = CanonicalMetricResult.model_validate(document)

    assert result.unit == unit
    assert result.digest().startswith("sha256:")


def test_a_dimension_named_outside_the_bmp_is_taken(tmp_path: Path) -> None:
    name = f"category {chr(0x1F4B0)}"
    inputs = Inputs(tmp_path)

    def rename_in_plan(plan: Any) -> None:
        plan["scope"]["dimensions"] = [name]
        for step in plan["steps"]:
            step["dimensions"] = [name for _ in step["dimensions"]]

    def rename_in_result(result: Any) -> None:
        result["dimensions"] = [name]
        for row in result["rows"]:
            row["dimensions"] = {name: row["dimensions"]["category"]}

    _rewrite(inputs.plan, rename_in_plan)
    _rewrite(inputs.result, rename_in_result)

    artifacts = write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert {path.name for path in inputs.output.iterdir()} == ARTIFACT_NAMES
    assert artifacts.results.canonical_results[0].dimensions == [name]
    assert set(artifacts.results.canonical_results[0].rows[0].dimensions) == {name}


# --- the command -------------------------------------------------------------------


def _unbuilt(path: Path) -> None:
    _append(path, b"as_of: 2026-02-30\n")


def _refused_by_pyyaml(path: Path) -> None:
    _append(path, b"broken: [a, b\n")


def _not_utf8(path: Path) -> None:
    _append(path, b"note: \xff\xfe\n")


def _empty(path: Path) -> None:
    path.write_bytes(b"")


def _unknown_key(path: Path) -> None:
    _append(path, b"unknown_key: 1\n")


#: What is done to a file, and whether the file is read after it.
DAMAGES: dict[str, tuple[Callable[[Path], None], bool]] = {
    "cannot be built": (_unbuilt, False),
    "is refused by PyYAML": (_refused_by_pyyaml, False),
    "is not UTF-8": (_not_utf8, False),
    "is not there": (Path.unlink, False),
    "is empty": (_empty, True),
    "holds an unknown key": (_unknown_key, True),
}


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("damage", list(DAMAGES))
def test_decision_investigate_exits_2_with_one_line_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], which: str, damage: str
) -> None:
    inputs = Inputs(tmp_path)
    path = inputs.path(which)
    spoil, was_read = DAMAGES[damage]
    spoil(path)

    assert inputs.investigate() == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    if was_read:
        assert captured.err.startswith(f"error: {path} is not a valid {KINDS[which]}: ")
    else:
        assert captured.err.startswith(f"error: cannot read {path}: ")
    assert not inputs.output.exists()


@pytest.mark.parametrize(
    ("dimension", "unit"),
    [("category: A", LONE_SURROGATE), (f"{LONE_SURROGATE}: A", "RUB")],
    ids=["the unit", "a dimension name"],
)
def test_decision_investigate_exits_2_for_a_value_the_artifacts_have_no_form_for(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    dimension: str,
    unit: str,
) -> None:
    _past_the_loader(monkeypatch)
    inputs = Inputs(tmp_path)
    _replace(inputs.result, "unit: RUB", f"unit: {unit}")
    _replace(inputs.result, FIRST_ROW, ROW_WITH.format(dimension=dimension))

    assert inputs.investigate() == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert captured.err.startswith(f"error: {inputs.result} is not a valid CanonicalMetricResult: ")
    assert captured.err.endswith("without a lone surrogate\n")
    assert not inputs.output.exists()


def test_inputs_that_are_read_give_the_artifacts_they_gave(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = Inputs(tmp_path)

    assert inputs.investigate() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        f"wrote    {inputs.output}",
        f"plan     {PLAN_DIGEST}",
        f"brief    {BRIEF_DIGEST}",
        "trust    exploratory; human acceptance not measured",
    ]
    assert {path.name for path in inputs.output.iterdir()} == ARTIFACT_NAMES


@pytest.mark.parametrize("failed_write", range(1, len(ARTIFACT_NAMES) + 1))
def test_a_failed_artifact_write_leaves_no_output_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_write: int
) -> None:
    inputs = Inputs(tmp_path)
    before = set(inputs.output.parent.iterdir())
    writes = 0
    real_write_bytes = Path.write_bytes

    def fail_on_write(path: Path, data: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == failed_write:
            real_write_bytes(path, data[:8])
            raise OSError("artifact failed")
        return real_write_bytes(path, data)

    with monkeypatch.context() as failure:
        failure.setattr(Path, "write_bytes", fail_on_write)
        with pytest.raises(OfflineInvestigationError) as caught:
            write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert writes == failed_write
    assert str(caught.value).endswith("artifact failed")
    assert not inputs.output.exists()
    assert set(inputs.output.parent.iterdir()) == before

    write_offline_investigation(inputs.plan, [inputs.result], inputs.output)
    assert {path.name for path in inputs.output.iterdir()} == ARTIFACT_NAMES


def test_a_publish_failure_leaves_no_output_or_staging_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = Inputs(tmp_path)
    before = set(inputs.output.parent.iterdir())

    def fail_publish(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        assert Path(target) == inputs.output
        raise OSError("publish failed")

    monkeypatch.setattr(os, "rename", fail_publish)

    with pytest.raises(OfflineInvestigationError) as caught:
        write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert str(caught.value).endswith("publish failed")
    assert not inputs.output.exists()
    assert set(inputs.output.parent.iterdir()) == before


def test_a_failed_output_write_is_a_cli_error_without_success_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = Inputs(tmp_path)
    before = set(inputs.output.parent.iterdir())
    real_write_bytes = Path.write_bytes

    def fail_brief(path: Path, data: bytes) -> int:
        if path.name == "brief.md":
            raise OSError("write refused")
        return real_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_brief)

    assert inputs.investigate() == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: cannot write output directory {inputs.output}: write refused\n"
    assert set(inputs.output.parent.iterdir()) == before


def test_public_output_is_hidden_until_all_four_artifacts_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = Inputs(tmp_path)
    visible_during_write: list[bool] = []
    real_write_bytes = Path.write_bytes

    def observe_write(path: Path, data: bytes) -> int:
        visible_during_write.append(inputs.output.exists())
        return real_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", observe_write)

    write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert visible_during_write == [False] * len(ARTIFACT_NAMES)
    assert inputs.output.exists()
    assert {path.name for path in inputs.output.iterdir()} == ARTIFACT_NAMES


def test_an_output_appearing_during_staging_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = Inputs(tmp_path)
    before = set(inputs.output.parent.iterdir())
    real_write_bytes = Path.write_bytes

    def create_output_on_last_write(path: Path, data: bytes) -> int:
        if path.name == "brief.md":
            assert not inputs.output.exists()
            inputs.output.mkdir()
            (inputs.output / "sentinel").write_text("mine", encoding="utf-8")
        return real_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", create_output_on_last_write)

    with pytest.raises(OfflineInvestigationError) as caught:
        write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert str(caught.value) == f"output directory already exists: {inputs.output}"
    assert (inputs.output / "sentinel").read_text(encoding="utf-8") == "mine"
    assert set(inputs.output.parent.iterdir()) == before | {inputs.output}


def test_the_longest_valid_output_name_can_be_published(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    target = tmp_path / ("x" * 255)

    write_offline_investigation(inputs.plan, [inputs.result], target)

    assert {path.name for path in target.iterdir()} == ARTIFACT_NAMES


# --- what is read and cannot be used is said as before ------------------------------


def test_a_result_no_step_asks_for_is_refused_as_before(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    other = tmp_path / "other.yaml"
    other.write_bytes(RESULT_FIXTURE.read_bytes())
    _rewrite(other, lambda result: result.update(metric_ref="metric://sales/profit@1"))

    assert inputs.refused(other) == (
        "unexpected canonical result outside declared steps: metric://sales/profit@1"
    )


def test_two_results_of_one_metric_are_refused_as_before(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)

    assert inputs.refused(inputs.result) == (
        "duplicate canonical result for metric metric://sales/revenue@5"
    )


def test_an_output_directory_that_is_there_is_refused_as_before(tmp_path: Path) -> None:
    inputs = Inputs(tmp_path)
    inputs.output.mkdir()

    with pytest.raises(OfflineInvestigationError) as caught:
        write_offline_investigation(inputs.plan, [inputs.result], inputs.output)

    assert str(caught.value) == f"output directory already exists: {inputs.output}"
    assert list(inputs.output.iterdir()) == []


def test_the_models_load_the_fixtures_as_before() -> None:
    plan = InvestigationPlan.load(PLAN_FIXTURE)
    result = CanonicalMetricResult.load(RESULT_FIXTURE)

    artifacts = build_offline_investigation(plan, [result])

    assert plan.digest() == PLAN_DIGEST
    assert artifacts.brief.digest() == BRIEF_DIGEST

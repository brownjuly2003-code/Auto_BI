"""What a schema refuses of a run input is said in one line.

`decision verify` and `decision capture` named a run input that was read and is not what its
schema describes, and then printed what the schema raised the way pydantic lists it: a line
for the place, a line for the reason with the value it is about, a line for a link — for
every refusal, of a `model.yaml`, a dashboard spec, a verified asset and a recorded result
alike. A usage error is one line (`docs/specs/unreadable-inputs.md`): where and why for the
first refusals, the number of the rest, no value and no link. A recorded result is named by
the key the file records it under, since a file holds one for each asset.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from auto_bi import cli
from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.model import SemanticModel
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.reconcile import ResultSet
from auto_bi.verification.release_gate import RecordedResult
from auto_bi.verification.repository import VerifiedAssetFileError, VerifiedAssetRepository
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT, Case
from tests.test_unreadable_run_inputs import (
    CAPTURE_FILES,
    LONE_SURROGATE,
    ROWS,
    RUN_FILES,
    _append,
    _assets,
    _capture_refused,
    _case,
    _never,
    _past_the_loader,
    _refused,
    _results,
)

BACKSLASH = chr(92)
EXTRA = "Extra inputs are not permitted"
REQUIRED = "Field required"
NOT_A_RESULT = "<root>: Input should be a valid dictionary or instance of ResultSet"
NOT_A_TYPE = (
    "Input should be 'string', 'integer', 'decimal', 'float', 'boolean', 'date' or 'timestamp'"
)
NOT_AN_AGGREGATE = "Input should be 'sum', 'avg', 'min', 'max', 'count' or 'count_distinct'"
FOUR_KEYS = "key_a: 1\nkey_b: 2\nkey_c: 3\nkey_d: 4\n"

#: Keys a file that is what it must be is given beside its own, and what the schema of the
#: file then says: these four kinds of run file take no key their schema does not know.
MORE_KEYS = [
    pytest.param("unknown_key: 1\n", f"unknown_key: {EXTRA}", id="one more key"),
    pytest.param(
        FOUR_KEYS,
        f"key_a: {EXTRA}; key_b: {EXTRA}; key_c: {EXTRA}; (+1 more)",
        id="four more keys",
    ),
    pytest.param(f'"a{BACKSLASH}n  b": 1\n', f"a b: {EXTRA}", id="a key with a line break"),
    pytest.param("1: 2\n", "1: Keys should be strings", id="a key that is no text"),
]

#: The run files whose schema is the schema of the whole file, and what the refusal of each
#: says after its path.
WHOLE_FILES = {
    "the base model": "is not a valid semantic model",
    "the candidate model": "is not a valid semantic model",
    "a dashboard spec": "is not a valid DashboardSpec",
    "a verified asset": "is not a valid verified asset",
}

#: What may be raised for a file that was read besides the refusal of a schema, and how one
#: line says it.
RAISED = [
    pytest.param(ValueError("two\n  lines\tof it"), "two lines of it", id="lines"),
    pytest.param(ValueError(), "ValueError", id="nothing said"),
    pytest.param(UnicodeError("  "), "UnicodeError", id="a subclass that says blanks"),
]


class _Gap(BaseModel):
    """Stands for a schema that is not the schema of any run file."""

    reason: str


def _refusal_of_another_schema() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Gap.model_validate({})
    return caught.value


def _raising(raised: Exception) -> Callable[..., Any]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise raised

    return refuse


def _rewrite(path: Path, change: Callable[[Any], Any]) -> None:
    """Change the document *path* holds and write it back."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _capture_argv(case: Case, output: Path) -> list[str]:
    argv = ["decision", "capture", str(_assets(case)), "--candidate", str(case.candidate)]
    return [*argv, "--dwh", "clickhouse", "--output", str(output), "--captured-at", CREATED_AT]


# --- a file whose schema is the schema of the whole file ----------------------------


@pytest.mark.parametrize("which", list(WHOLE_FILES))
@pytest.mark.parametrize(("more", "refusals"), MORE_KEYS)
def test_keys_a_schema_does_not_know_are_refused_in_one_line(
    tmp_path: Path, which: str, more: str, refusals: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, more)
    expected = f"{path} {WHOLE_FILES[which]}: {refusals}"

    assert _refused(case) == expected
    if which in CAPTURE_FILES:
        assert _capture_refused(case) == expected


@pytest.mark.parametrize("ref", ["base", "candidate"])
@pytest.mark.parametrize("text", ["- a\n- b\n", "just text\n"], ids=["a list", "a text"])
def test_a_model_that_is_no_mapping_is_refused_at_the_root(
    tmp_path: Path, ref: str, text: str
) -> None:
    case = _case(tmp_path)
    path = case.root / ref / "model.yaml"
    path.write_text(text, encoding="utf-8")
    expected = (
        f"{path} is not a valid semantic model: "
        "<root>: Input should be a valid dictionary or instance of SemanticModel"
    )

    assert _refused(case) == expected
    if ref == "candidate":
        assert _capture_refused(case) == expected


@pytest.mark.parametrize("ref", ["base", "candidate"])
def test_a_value_of_a_model_is_refused_by_its_place_and_not_shown(tmp_path: Path, ref: str) -> None:
    case = _case(tmp_path)
    path = case.root / ref / "model.yaml"
    _rewrite(path, lambda document: document["tables"][0].update(columns="no such list"))
    expected = (
        f"{path} is not a valid semantic model: tables.0.columns: Input should be a valid list"
    )

    assert _refused(case) == expected
    if ref == "candidate":
        assert _capture_refused(case) == expected


def test_a_spec_that_holds_none_of_its_fields_is_told_each_of_three(tmp_path: Path) -> None:
    """Three refusals are all spelled out: nothing is left to count."""
    case = _case(tmp_path)
    path = RUN_FILES["a dashboard spec"](case)
    path.write_text("unknown_key: 1\n", encoding="utf-8")

    assert _refused(case) == (
        f"{path} is not a valid DashboardSpec: "
        f"title: {REQUIRED}; charts: {REQUIRED}; unknown_key: {EXTRA}"
    )


def test_values_of_a_spec_are_refused_by_their_places_and_not_shown(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a dashboard spec"](case)
    measures = [{"column": 5, "agg": "most"}]
    _rewrite(path, lambda document: document["charts"][0]["query"].update(measures=measures))

    assert _refused(case) == (
        f"{path} is not a valid DashboardSpec: "
        "charts.0.query.measures.0.column: Input should be a valid string; "
        f"charts.0.query.measures.0.agg: {NOT_AN_AGGREGATE}"
    )


@pytest.mark.parametrize(
    ("text", "refusals"),
    [
        pytest.param(
            "",
            f"metadata: {REQUIRED}; question: {REQUIRED}; intent: {REQUIRED}; (+3 more)",
            id="nothing",
        ),
        pytest.param(
            "unknown_key: 1\n",
            f"metadata: {REQUIRED}; question: {REQUIRED}; intent: {REQUIRED}; (+4 more)",
            id="a mapping of another kind",
        ),
        pytest.param(
            "- a\n- b\n",
            "<root>: Input should be a valid dictionary or instance of VerifiedAsset",
            id="a list",
        ),
    ],
)
def test_a_file_that_holds_no_asset_is_told_what_it_lacks_in_one_line(
    tmp_path: Path, text: str, refusals: str
) -> None:
    """Beside an asset that is one: the refusal is of the file, for both commands."""
    case = _case(tmp_path)
    path = _assets(case) / "revenue_by_region.yaml"
    path.write_text(text, encoding="utf-8")
    expected = f"{path} is not a valid verified asset: {refusals}"

    assert _refused(case) == expected
    assert _capture_refused(case) == expected


def test_values_of_an_asset_are_refused_by_their_places_and_not_shown(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a verified asset"](case)

    def change(document: Any) -> None:
        document["expectation"]["result"]["rows"] = [["Москва"], "Казань"]
        document["review"]["reviewed_at"] = ["when it was"]

    _rewrite(path, change)
    expected = (
        f"{path} is not a valid verified asset: "
        "expectation.result.rows.1: Input should be a valid list; "
        "review.reviewed_at: Value error, "
        "must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z"
    )

    assert _refused(case) == expected
    assert _capture_refused(case) == expected


def test_the_repository_says_it_in_one_line_and_keeps_what_the_schema_raised(
    tmp_path: Path,
) -> None:
    """Whoever loads a repository in a program still has every refusal, with its value."""
    case = _case(tmp_path)
    path = _assets(case) / "revenue_by_region.yaml"
    path.write_text("unknown_key: 1\n", encoding="utf-8")

    with pytest.raises(VerifiedAssetFileError) as caught:
        VerifiedAssetRepository.load(_assets(case))

    assert str(caught.value) == (
        f"{path} is not a valid verified asset: "
        f"metadata: {REQUIRED}; question: {REQUIRED}; intent: {REQUIRED}; (+4 more)"
    )
    cause = caught.value.__cause__
    assert isinstance(cause, ValidationError)
    assert cause.error_count() == 7


# --- a recorded result -----------------------------------------------------------------


def _spoil(result: dict[str, Any]) -> None:
    result["columns"][0]["type"] = "colour"
    result["rows"] = [["Москва"], "Казань"]


def test_an_attested_result_that_is_none_is_named_by_its_key(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = _results(case)
    _rewrite(path, lambda document: _spoil(document[ASSET_ID]["result"]))

    assert _refused(case) == (
        f"{path} holds an invalid recorded result for '{ASSET_ID}': "
        f"result.columns.0.type: {NOT_A_TYPE}; result.rows.1: Input should be a valid list"
    )


def test_a_bare_result_that_is_none_is_named_by_its_key(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate().with_asset().with_results(ROWS, attested=None)
    path = _results(case)
    _rewrite(path, lambda document: _spoil(document[ASSET_ID]))

    assert _refused(case) == (
        f"{path} holds an invalid recorded result for '{ASSET_ID}': "
        f"columns.0.type: {NOT_A_TYPE}; rows.1: Input should be a valid list"
    )


def test_an_attestation_that_is_none_is_named_by_the_key_of_its_result(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = _results(case)
    _rewrite(path, lambda document: document[ASSET_ID]["attestation"].update(origin="guesswork"))

    assert _refused(case) == (
        f"{path} holds an invalid recorded result for '{ASSET_ID}': "
        "attestation.origin: Input should be 'query_run', 'bi_export' or 'manual'"
    )


def test_the_first_result_that_is_none_ends_the_reading(tmp_path: Path) -> None:
    """After a result that is one: the file is read in its order, and the first refusal ends it."""
    case = _case(tmp_path)
    path = _results(case)
    _append(path, "first: 1\nsecond: 2\n")

    assert _refused(case) == f"{path} holds an invalid recorded result for 'first': {NOT_A_RESULT}"


@pytest.mark.parametrize(
    ("line", "named"),
    [
        pytest.param("1: 2\n", "1", id="a number"),
        pytest.param('"": 1\n', "''", id="empty text"),
        pytest.param("~: 1\n", "None", id="nothing"),
        pytest.param("true: 1\n", "True", id="a truth value"),
        pytest.param("it's: 1\n", '"it\'s"', id="text with a quote"),
        pytest.param(
            f'"a{BACKSLASH}n  b": 1\n', f"'a{BACKSLASH}n  b'", id="text with a line break"
        ),
        pytest.param(
            f"{LONE_SURROGATE}: 1\n", f"'{BACKSLASH}ud800'", id="text with a lone surrogate"
        ),
    ],
)
def test_the_key_of_a_result_is_named_as_the_file_gives_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, named: str
) -> None:
    """Nothing has required the key to be an asset id yet: it is named as it reads, in one line.

    The key that holds a lone surrogate reaches this past the loader, which refuses the file.
    """
    _past_the_loader(monkeypatch)
    case = _case(tmp_path)
    path = _results(case)
    _append(path, line)

    message = _refused(case)

    assert message == f"{path} holds an invalid recorded result for {named}: {NOT_A_RESULT}"
    assert "\n" not in message


def test_a_result_under_its_key_that_is_no_result_is_refused_at_that_key(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = _results(case)
    _rewrite(path, lambda document: document[ASSET_ID].update(result=1))

    assert _refused(case) == (
        f"{path} holds an invalid recorded result for '{ASSET_ID}': "
        "result: Input should be a valid dictionary or instance of ResultSet"
    )


# --- what a ref cannot be frozen with -----------------------------------------------


def test_the_refusal_of_another_schema_is_said_of_the_ref_with_that_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The places are places of what that schema describes, which is no file of the ref."""
    case = _case(tmp_path)
    monkeypatch.setattr(NativeModelSource, "read", _raising(_refusal_of_another_schema()))

    assert _refused(case) == (
        f"cannot load semantic ref {case.base}: not a valid _Gap: reason: {REQUIRED}"
    )
    assert _capture_refused(case) == (
        f"cannot load semantic ref {case.candidate}: not a valid _Gap: reason: {REQUIRED}"
    )


@pytest.mark.parametrize(("raised", "said"), RAISED)
def test_what_else_keeps_a_ref_from_being_frozen_is_said_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, said: str
) -> None:
    case = _case(tmp_path)
    monkeypatch.setattr(NativeModelSource, "read", _raising(raised))

    assert _refused(case) == f"cannot load semantic ref {case.base}: {said}"
    assert _capture_refused(case) == f"cannot load semantic ref {case.candidate}: {said}"


# --- what else may be raised for a file that was read -------------------------------


def _model_that_raises(raised: Exception) -> type[SemanticModel]:
    """The schema the candidate model is read by a second time, refusing what the ref took."""

    class Refusing(SemanticModel):
        @classmethod
        def model_validate(cls, *args: Any, **kwargs: Any) -> Any:
            raise raised

    return Refusing


@pytest.mark.parametrize(("raised", "said"), RAISED)
def test_what_else_is_raised_for_the_candidate_model_is_said_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, said: str
) -> None:
    case = _case(tmp_path)
    monkeypatch.setattr(use_cases, "SemanticModel", _model_that_raises(raised))

    assert (
        _refused(case) == f"{case.candidate / 'model.yaml'} is not a valid semantic model: {said}"
    )


def test_a_refusal_of_the_candidate_model_read_a_second_time_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    refusing = _model_that_raises(_refusal_of_another_schema())
    monkeypatch.setattr(use_cases, "SemanticModel", refusing)

    assert _refused(case) == (
        f"{case.candidate / 'model.yaml'} is not a valid semantic model: reason: {REQUIRED}"
    )


@pytest.mark.parametrize(("raised", "said"), RAISED)
def test_what_else_is_raised_for_a_spec_is_said_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, said: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a dashboard spec"](case)
    monkeypatch.setattr(DashboardSpec, "model_validate", _raising(raised))

    assert _refused(case) == f"{path} is not a valid DashboardSpec: {said}"


@pytest.mark.parametrize(("raised", "said"), RAISED)
def test_what_else_is_raised_for_the_assets_is_said_of_their_path_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, said: str
) -> None:
    case = _case(tmp_path)
    monkeypatch.setattr(VerifiedAssetRepository, "load", _raising(raised))
    expected = f"{_assets(case)} holds an invalid verified asset: {said}"

    assert _refused(case) == expected
    assert _capture_refused(case) == expected


@pytest.mark.parametrize(("raised", "said"), RAISED)
@pytest.mark.parametrize("shape", ["attested", "bare"])
def test_what_else_is_raised_for_a_result_is_said_under_its_key_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, raised: Exception, said: str
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    if shape == "attested":
        case.with_results(ROWS)
        monkeypatch.setattr(RecordedResult, "model_validate", _raising(raised))
    else:
        case.with_results(ROWS, attested=None)
        monkeypatch.setattr(ResultSet, "model_validate", _raising(raised))
    path = _results(case)

    assert _refused(case) == f"{path} holds an invalid recorded result for '{ASSET_ID}': {said}"


# --- the commands ---------------------------------------------------------------------


@pytest.mark.parametrize("which", list(RUN_FILES))
@pytest.mark.parametrize(
    "more",
    [
        pytest.param(FOUR_KEYS, id="four more keys"),
        pytest.param(f"{LONE_SURROGATE}: 1\n", id="a key that holds a lone surrogate"),
    ],
)
def test_decision_verify_exits_2_with_one_line_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    more: str,
) -> None:
    _past_the_loader(monkeypatch)  # for the key that holds a lone surrogate
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, more)

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: {path} ")
    assert captured.err.endswith("\n")
    assert captured.err.count("\n") == 1
    assert "input_value" not in captured.err
    assert "https://" not in captured.err
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("which", CAPTURE_FILES)
@pytest.mark.parametrize(
    "more",
    [
        pytest.param(FOUR_KEYS, id="four more keys"),
        pytest.param(f"{LONE_SURROGATE}: 1\n", id="a key that holds a lone surrogate"),
    ],
)
def test_decision_capture_exits_2_with_one_line_and_opens_no_query(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    more: str,
) -> None:
    _past_the_loader(monkeypatch)  # for the key that holds a lone surrogate
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, more)
    output = case.root / "captured.yaml"
    monkeypatch.setattr(cli, "_result_capture_target", lambda engine: (_never, "env", "reader"))

    assert main(_capture_argv(case, output)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: {path} ")
    assert captured.err.endswith("\n")
    assert captured.err.count("\n") == 1
    assert "input_value" not in captured.err
    assert "https://" not in captured.err
    assert not output.exists()

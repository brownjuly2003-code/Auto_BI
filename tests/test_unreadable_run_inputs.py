"""A run input `decision verify` or `decision capture` cannot read ends the run before it starts.

A contract that cannot be read is a finding: the gate still grades the rest. A run input —
the `model.yaml` of a semantic ref, a delivered dashboard spec, a verified asset, the recorded
results — leaves nothing to grade, so the command says which file and why and exits 2
(`docs/specs/unreadable-inputs.md`). Exit code 1 is the interpreter's and is no answer.
"""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi import cli, yaml_document
from auto_bi.cli import main
from auto_bi.decision.use_cases import (
    CaptureRequest,
    VerifyError,
    capture_result,
    locate_evidence,
)
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.repository import VerifiedAssetFileError, VerifiedAssetRepository
from auto_bi.yaml_document import load_yaml_document, unread_reason
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    ENVIRONMENT_REF,
    Case,
    _write_yaml,
    verified_asset,
)
from tests.test_unbuilt_contract_yaml import NO_SUCH_DATE, UNBUILT

ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]
RUN = "run-1"

#: The files a run is handed beside its contracts, and where the fixture keeps each.
RUN_FILES: dict[str, Callable[[Case], Path]] = {
    "the base model": lambda case: case.base / "model.yaml",
    "the candidate model": lambda case: case.candidate / "model.yaml",
    "a dashboard spec": lambda case: case.dashboards / "weekly_margin@7.yaml",
    "a verified asset": lambda case: _assets(case) / "weekly_margin_by_region.yaml",
    "the recorded results": lambda case: _results(case),
}
#: The two of them `decision capture` reads.
CAPTURE_FILES = ["a verified asset", "the candidate model"]

#: Texts PyYAML refuses by itself, with the class and the position it refuses them at.
REFUSED_AT = [
    pytest.param("tables: [a, b\n", "ParserError at line 2, column 1", id="an open sequence"),
    pytest.param("a: 1\nb:\n\t- x\n", "ScannerError at line 3, column 1", id="a tab that indents"),
    pytest.param("a: 1\n---\nb: 2\n", "ComposerError at line 2, column 1", id="two documents"),
    pytest.param(
        "a: 1\nb: *nobody\n", "ComposerError at line 2, column 4", id="an alias nothing anchors"
    ),
    pytest.param(
        "a: 1\nb: !!python/object:os.system x\n",
        "ConstructorError at line 2, column 4",
        id="a foreign tag",
    ),
    pytest.param(
        "a: 1\n? [x, y]\n: 2\n",
        "ConstructorError at line 2, column 3",
        id="a key that cannot be one",
    ),
]

#: Documents that are read and are not what the file must hold.
NOT_A_MAPPING = [
    pytest.param("- a\n- b\n", id="a list"),
    pytest.param("just text\n", id="a text"),
]

#: Documents that a semantic model reader must reject because the YAML root is not a mapping.
NONMAPPING_MODEL_DOCUMENTS = [
    pytest.param("", id="an empty file"),
    pytest.param(" \n# comment only\n", id="whitespace and comments only"),
    pytest.param("---\n", id="a document marker"),
    pytest.param("null\n", id="null"),
    pytest.param("~\n", id="tilde null"),
    pytest.param("false\n", id="false"),
    pytest.param("0\n", id="zero"),
    pytest.param("0.0\n", id="zero point zero"),
    pytest.param("[]\n", id="an empty list"),
    pytest.param("''\n", id="an empty YAML string"),
]

#: A lone surrogate as a YAML file holds one: an escape in a double-quoted scalar. PyYAML
#: builds a string of it, and the loader of this program refuses the file
#: (`tests/test_surrogate_escapes.py`).
LONE_SURROGATE = '"' + chr(92) + 'ud800"'


def _assets(case: Case) -> Path:
    assert case.assets is not None
    return case.assets


def _results(case: Case) -> Path:
    assert case.results is not None
    return case.results


def _case(tmp_path: Path) -> Case:
    """A run that reads every kind of run file and passes when none is damaged."""
    return Case(tmp_path).with_candidate().with_asset().with_results(ROWS)


def _append(path: Path, line: str) -> None:
    path.write_bytes(path.read_bytes() + line.encode("utf-8"))


def _deny(monkeypatch: pytest.MonkeyPatch, denied: Path) -> None:
    """Make *denied* a file this process may not open, on any platform and as any user."""
    for name in ("read_text", "read_bytes"):
        original = getattr(Path, name)

        def refuse(
            self: Path, *args: Any, _original: Callable[..., Any] = original, **kwargs: Any
        ) -> Any:
            if self == denied:
                raise PermissionError(errno.EACCES, "Permission denied", str(self))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, refuse)


def _deny_look(monkeypatch: pytest.MonkeyPatch, *denied: Path) -> None:
    """Make *denied* paths this process may not look at: their directory may not be searched.

    The refusal is the operating system's — `os.stat` raises it — so it reaches whatever
    looks, however the `pathlib` of the interpreter reads such an answer: up to Python 3.13
    its looks raise it, from 3.14 on they answer "no".
    """
    original = os.stat
    refused = {os.fspath(path) for path in denied}

    def stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if isinstance(path, (str, os.PathLike)) and os.fspath(path) in refused:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), os.fspath(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


def _plant_lone_surrogate(path: Path, *keys: str | int) -> None:
    """Put a lone surrogate at *keys* of the YAML mapping in *path*."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    node = document
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = "PLANTED"
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    assert text.count("PLANTED") == 1
    path.write_text(text.replace("PLANTED", LONE_SURROGATE), encoding="utf-8")


def _past_the_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand what reads a YAML document the string PyYAML builds of an escaped surrogate.

    No file does that any more: the loader refuses it. What stands behind the loader — a
    schema, the graph of a ref, a digest, a line that is printed — is still held to its own
    answer for such a string, which a reader that is not this loader can hand it.
    """
    monkeypatch.setattr(yaml_document, "_refuse_surrogates", lambda document: None)


def _said_of(line: str, raised: type[BaseException]) -> str:
    """What the interpreter says of *line* when its value is built."""
    with pytest.raises(raised) as caught:
        yaml.safe_load(line)
    return str(caught.value)


def _refusal(case: Case, **overrides: Any) -> str:
    """What `decision verify` refuses *case* with."""
    with pytest.raises(VerifyError) as caught:
        case.run(**overrides)
    return str(caught.value)


def _refused(case: Case, **overrides: Any) -> str:
    """What `decision verify` refuses *case* with before anything is written for it."""
    message = _refusal(case, **overrides)
    assert not (case.root / "evidence").exists()
    return message


def _never() -> Any:
    raise AssertionError("no query may be opened for a run file that cannot be read")


def _never_probe(*_args: Any) -> Any:
    raise AssertionError("no environment probe may be opened for a run file that cannot be read")


def _capture_refused(case: Case) -> str:
    """The one line `decision capture` refuses *case* with; no query and no output for it."""
    output = case.root / "captured.yaml"
    request = CaptureRequest(
        assets=_assets(case),
        candidate=case.candidate,
        output=output,
        engine="clickhouse",
        environment_ref=ENVIRONMENT_REF,
        executed_by="auto_bi_ro",
        captured_at=CREATED_AT,
        query_factory=_never,
        asset_id=ASSET_ID,
    )
    with pytest.raises(VerifyError) as caught:
        capture_result(request)
    assert not output.exists()
    return str(caught.value)


# --- why a file could not be read ----------------------------------------------


@pytest.mark.parametrize(("text", "reason"), REFUSED_AT)
def test_the_reason_gives_the_position_pyyaml_refuses_a_text_at(text: str, reason: str) -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_document(text)
    assert unread_reason(caught.value) == reason


@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_the_reason_says_what_was_raised_while_a_document_was_built(
    line: str, raised: type[BaseException]
) -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_document(line)
    reason = unread_reason(caught.value)
    assert reason.startswith(f"ConstructorError ({raised.__name__}: ")
    assert reason.endswith(")")
    assert "\n" not in reason
    if raised is not RecursionError:  # where the stack runs out decides what that one says
        assert reason == f"ConstructorError ({raised.__name__}: {_said_of(line, raised)})"


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        pytest.param(ValueError("two\n  lines\tof it"), "ValueError: two lines of it", id="lines"),
        pytest.param(ValueError(), "ValueError", id="nothing said"),
        pytest.param(KeyError("perhaps"), "KeyError: 'perhaps'", id="a key"),
    ],
)
def test_what_was_raised_is_given_in_one_line(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, reason: str
) -> None:
    def refuse(*_args: Any) -> Any:
        raise raised

    monkeypatch.setattr(yaml.SafeLoader, "construct_document", refuse)
    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_document("kind: MetricContract\n")
    assert unread_reason(caught.value) == f"ConstructorError ({reason})"


def test_the_reason_is_the_class_alone_where_nothing_tells_more() -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_document('a: "bell \x07"\n')
    assert unread_reason(caught.value) == "ReaderError"

    assert unread_reason(yaml.YAMLError("no mark and no cause")) == "YAMLError"
    assert unread_reason(PermissionError(errno.EACCES, "Permission denied", "x")) == (
        "PermissionError"
    )
    assert unread_reason(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")) == (
        "UnicodeDecodeError"
    )
    # What is not a refusal of the document says nothing more, whatever led to it.
    chained = OSError("the device is gone")
    chained.__cause__ = ValueError("a reason of its own")
    assert unread_reason(chained) == "OSError"


# --- `decision verify`: a run file that cannot be read --------------------------


@pytest.mark.parametrize("which", list(RUN_FILES))
@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_a_run_file_that_cannot_be_built_is_a_usage_error_that_names_it(
    tmp_path: Path, which: str, line: str, raised: type[BaseException]
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, line)

    message = _refused(case)

    opening = f"cannot read {path}: ConstructorError ({raised.__name__}: "
    assert message.startswith(opening)
    assert message.endswith(")")
    assert "\n" not in message
    if raised is not RecursionError:
        assert message == f"{opening}{_said_of(line, raised)})"


@pytest.mark.parametrize("which", list(RUN_FILES))
@pytest.mark.parametrize(("text", "reason"), REFUSED_AT)
def test_a_run_file_pyyaml_refuses_is_named_with_the_position(
    tmp_path: Path, which: str, text: str, reason: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    path.write_text(text, encoding="utf-8")

    assert _refused(case) == f"cannot read {path}: {reason}"


@pytest.mark.parametrize("which", list(RUN_FILES))
def test_a_run_file_that_is_not_text_is_named_by_the_class(tmp_path: Path, which: str) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    path.write_bytes(path.read_bytes() + b"note: caf\xe9\n")  # Latin-1, not UTF-8

    assert _refused(case) == f"cannot read {path}: UnicodeDecodeError"

    bell = _case(tmp_path / "bell")
    path = RUN_FILES[which](bell)
    _append(path, 'note: "bell \x07"\n')

    assert _refused(bell) == f"cannot read {path}: ReaderError"


@pytest.mark.parametrize("which", list(RUN_FILES))
def test_a_run_file_that_cannot_be_opened_is_named_by_the_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _deny(monkeypatch, path)

    assert _refused(case) == f"cannot read {path}: PermissionError"


@pytest.mark.parametrize("ref", ["base", "candidate"])
def test_a_metric_contract_of_a_ref_that_cannot_be_opened_is_named_as_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    case = _case(tmp_path)
    path = case.root / ref / "metrics" / "gross_margin_pct.yaml"
    _deny(monkeypatch, path)

    assert _refused(case) == f"cannot read {path}: PermissionError"


@pytest.mark.parametrize("ref", ["base", "candidate"])
@pytest.mark.parametrize("looked_at", ["model.yaml", "metrics"])
def test_a_ref_that_may_not_be_looked_into_is_named_by_the_path_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str, looked_at: str
) -> None:
    case = _case(tmp_path)
    path = case.root / ref / looked_at
    _deny_look(monkeypatch, path)

    assert _refused(case) == f"cannot read {path}: PermissionError"
    if ref == "candidate":
        assert _capture_refused(case) == f"cannot read {path}: PermissionError"


def test_a_refusal_that_names_no_file_is_said_of_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)

    def refuse(self: NativeModelSource, revision: str = "") -> Any:
        raise OSError("the device is gone")

    monkeypatch.setattr(NativeModelSource, "read", refuse)

    assert _refused(case) == f"cannot read {case.base / 'model.yaml'}: OSError"


# --- `decision verify`: a run file that is read and is not what it must be -------


@pytest.mark.parametrize("ref", ["base", "candidate"])
@pytest.mark.parametrize(
    "text",
    [*NOT_A_MAPPING, pytest.param("unknown_key: 1\n", id="a mapping the schema refuses")],
)
def test_a_model_that_is_read_and_is_no_model_is_named_as_that(
    tmp_path: Path, ref: str, text: str
) -> None:
    case = _case(tmp_path)
    path = case.root / ref / "model.yaml"
    path.write_text(text, encoding="utf-8")

    message = _refusal(case)

    assert message.startswith(f"{path} is not a valid semantic model: ")
    assert "\n" not in message
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("ref", ["base", "candidate"])
@pytest.mark.parametrize(
    ("file", "keys", "refused"),
    [
        pytest.param(
            "model.yaml",
            ("tables", 0, "description"),
            "$.tables[0].description: string contains a lone surrogate",
            id="a table of the model",
        ),
        pytest.param(
            "metrics/gross_margin_pct.yaml",
            ("metadata", "description"),
            "$.metrics[0].description: string contains a lone surrogate",
            id="a metric contract",
        ),
    ],
)
def test_a_value_the_graph_has_no_digest_for_is_said_of_the_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    file: str,
    keys: tuple[str | int, ...],
    refused: str,
) -> None:
    """Read, and accepted by the schema of its file: the digest's refusal names no file."""
    _past_the_loader(monkeypatch)
    case = _case(tmp_path)
    root = case.root / ref
    _plant_lone_surrogate(root / file, *keys)

    assert _refused(case) == f"cannot load semantic ref {root}: {refused}"
    if ref == "candidate":
        assert _capture_refused(case) == f"cannot load semantic ref {root}: {refused}"


@pytest.mark.parametrize("ref", ["base", "candidate"])
def test_a_refusal_of_another_schema_is_said_of_the_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    """Only what the schema of the model refuses is said of `model.yaml`.

    A dimension the schema of the contract accepts and no gap can quote: the refusal is the
    one of the gap's schema, raised while the graph of the ref is built.
    """
    _past_the_loader(monkeypatch)
    case = _case(tmp_path)
    root = case.root / ref
    _plant_lone_surrogate(root / "metrics" / "gross_margin_pct.yaml", "dimensions", 0)

    message = _refused(case)

    assert message.startswith(f"cannot load semantic ref {root}: not a valid SemanticGap: reason: ")
    assert "\n" not in message
    assert "is not a valid semantic model" not in message
    if ref == "candidate":
        assert _capture_refused(case) == message


@pytest.mark.parametrize(
    "surface",
    [
        pytest.param("base", id="base"),
        pytest.param("candidate", id="candidate"),
        pytest.param("capture", id="capture"),
    ],
)
@pytest.mark.parametrize("text", NONMAPPING_MODEL_DOCUMENTS)
def test_a_nonmapping_model_is_refused_before_the_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    surface: str,
    text: str,
) -> None:
    """Every nonmapping model document refuses before grading, capture, or evidence."""
    case = _case(tmp_path)
    path = case.base / "model.yaml" if surface == "base" else case.candidate / "model.yaml"
    path.write_text(text, encoding="utf-8")

    if surface == "capture":
        message = _capture_refused(case)
        output = case.root / "captured.yaml"
        argv = ["decision", "capture", str(_assets(case)), "--candidate", str(case.candidate)]
        argv += ["--dwh", "clickhouse", "--output", str(output), "--captured-at", CREATED_AT]
    else:
        message = _refused(case, probe=_never_probe)
        output = None
        argv = _verify_argv(case)

    assert message.startswith(f"{path} is not a valid semantic model: <root>: ")
    assert "\n" not in message
    assert not (case.root / "evidence").exists()
    assert main(argv) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {message}\n"
    assert captured.err.count("\n") == 1
    assert not (case.root / "evidence").exists()
    if output is not None:
        assert not output.exists()


@pytest.mark.parametrize("which", ["a dashboard spec", "the recorded results"])
@pytest.mark.parametrize("text", [*NOT_A_MAPPING, pytest.param("", id="nothing")])
def test_a_spec_or_a_results_file_that_is_no_mapping_is_named_as_before(
    tmp_path: Path, which: str, text: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    path.write_text(text, encoding="utf-8")

    assert _refused(case) == f"{path} must contain a mapping"


@pytest.mark.parametrize(
    "text",
    [
        *NOT_A_MAPPING,
        pytest.param("", id="nothing"),
        pytest.param("unknown_key: 1\n", id="a mapping the schema refuses"),
    ],
)
def test_an_asset_file_that_is_no_asset_is_named_as_that_file(tmp_path: Path, text: str) -> None:
    """Beside an asset that is one: the directory fails, and the error says which file."""
    case = _case(tmp_path)
    path = _assets(case) / "revenue_by_region.yaml"
    path.write_text(text, encoding="utf-8")

    for message in (_refused(case), _capture_refused(case)):
        assert message.startswith(f"{path} is not a valid verified asset: ")
        assert "\n" not in message


def test_two_assets_of_one_id_are_named_under_their_repository(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _write_yaml(_assets(case) / "again.yaml", verified_asset(case.base_digest))
    problem = f"duplicate verified asset id: {ASSET_ID}"
    expected = f"{_assets(case)} holds an invalid verified asset: {problem}"

    assert _refused(case) == expected
    assert _capture_refused(case) == expected


def test_a_file_under_the_assets_that_may_not_be_looked_at_is_named_as_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused while the files are listed, before any of them is read."""
    case = _case(tmp_path)
    path = RUN_FILES["a verified asset"](case)
    _deny_look(monkeypatch, path)

    assert _refused(case) == f"cannot read {path}: PermissionError"
    assert _capture_refused(case) == f"cannot read {path}: PermissionError"


# --- the repository names the file it cannot load --------------------------------


def test_the_repository_names_a_file_it_cannot_read(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = _assets(case) / "revenue_by_region.yaml"
    path.write_text(NO_SUCH_DATE, encoding="utf-8")

    with pytest.raises(VerifiedAssetFileError) as caught:
        VerifiedAssetRepository.load(_assets(case))

    assert isinstance(caught.value, ValueError)
    assert str(caught.value) == (
        f"cannot read {path}: ConstructorError (ValueError: {_said_of(NO_SUCH_DATE, ValueError)})"
    )
    assert isinstance(caught.value.__cause__, yaml.YAMLError)


def test_the_repository_names_a_file_it_cannot_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a verified asset"](case)
    _deny(monkeypatch, path)

    with pytest.raises(VerifiedAssetFileError) as caught:
        VerifiedAssetRepository.load(_assets(case))

    assert str(caught.value) == f"cannot read {path}: PermissionError"
    assert isinstance(caught.value.__cause__, PermissionError)


def test_the_repository_names_a_file_that_holds_no_asset(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = _assets(case) / "revenue_by_region.yaml"
    path.write_text("unknown_key: 1\n", encoding="utf-8")

    with pytest.raises(VerifiedAssetFileError) as caught:
        VerifiedAssetRepository.load(_assets(case))

    assert str(caught.value).startswith(f"{path} is not a valid verified asset: ")
    assert isinstance(caught.value.__cause__, ValidationError)


def test_the_repository_loads_the_assets_it_can_read_as_before(tmp_path: Path) -> None:
    case = _case(tmp_path)
    (_assets(case) / "notes.txt").write_text(NO_SUCH_DATE, encoding="utf-8")  # not YAML by name

    repository = VerifiedAssetRepository.load(_assets(case))
    alone = VerifiedAssetRepository.load(_assets(case) / "weekly_margin_by_region.yaml")

    assert [asset.metadata.id for asset in repository.assets] == [ASSET_ID]
    assert [asset.digest() for asset in alone.assets] == [
        asset.digest() for asset in repository.assets
    ]


# --- `decision capture` -----------------------------------------------------------


@pytest.mark.parametrize("which", CAPTURE_FILES)
@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_capture_names_a_run_file_that_cannot_be_built(
    tmp_path: Path, which: str, line: str, raised: type[BaseException]
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, line)

    message = _capture_refused(case)

    opening = f"cannot read {path}: ConstructorError ({raised.__name__}: "
    assert message.startswith(opening)
    assert message.endswith(")")
    assert "\n" not in message
    if raised is not RecursionError:
        assert message == f"{opening}{_said_of(line, raised)})"


@pytest.mark.parametrize("which", CAPTURE_FILES)
def test_capture_names_a_run_file_it_cannot_read_as_verify_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    refused = _case(tmp_path / "refused")
    path = RUN_FILES[which](refused)
    path.write_text("tables: [a, b\n", encoding="utf-8")
    assert _capture_refused(refused) == f"cannot read {path}: ParserError at line 2, column 1"

    not_text = _case(tmp_path / "not-text")
    path = RUN_FILES[which](not_text)
    path.write_bytes(path.read_bytes() + b"note: caf\xe9\n")
    assert _capture_refused(not_text) == f"cannot read {path}: UnicodeDecodeError"

    denied = _case(tmp_path / "denied")
    path = RUN_FILES[which](denied)
    _deny(monkeypatch, path)
    assert _capture_refused(denied) == f"cannot read {path}: PermissionError"


def test_capture_names_a_candidate_model_that_is_no_model(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = case.candidate / "model.yaml"
    path.write_text("unknown_key: 1\n", encoding="utf-8")

    assert _capture_refused(case).startswith(f"{path} is not a valid semantic model: ")


# --- an evidence directory that cannot take the bundle -----------------------------


def test_an_evidence_path_that_is_a_file_is_a_usage_error(tmp_path: Path) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    evidence.write_text("kept\n", encoding="utf-8")

    message = _refusal(case)

    assert message.startswith(f"cannot write the evidence bundle under {evidence}: cannot write ")
    assert re.search(r" of run 'run-[0-9a-f]+': \w+Error$", message)
    assert evidence.read_text(encoding="utf-8") == "kept\n"


def test_a_write_the_run_directory_refuses_is_a_usage_error_and_leaves_no_bundle(
    tmp_path: Path,
) -> None:
    clean = _case(tmp_path / "clean")
    written = clean.run()
    run_dir = clean.root / "evidence" / written.run_id
    folder = next(path.name for path in sorted(run_dir.iterdir()) if path.is_dir())

    case = _case(tmp_path / "refused")
    refused_dir = case.root / "evidence" / written.run_id
    refused_dir.mkdir(parents=True)
    (refused_dir / folder).write_text("in the way\n", encoding="utf-8")

    message = _refusal(case)

    evidence = case.root / "evidence"
    assert message.startswith(f"cannot write the evidence bundle under {evidence}: cannot write ")
    assert f"'{folder}/" in message
    assert message.endswith("Error")
    assert not (refused_dir / "manifest.json").exists()
    with pytest.raises(VerifyError, match="no evidence bundle"):
        locate_evidence(evidence)

    # What the refusal left behind is no bundle and is in nobody's way: the same run, once
    # the directory takes it, is the bundle a run without the refusal wrote.
    (refused_dir / folder).unlink()
    again = case.run()
    assert again.run_id == written.run_id
    assert again.evidence_digest == written.evidence_digest


def test_a_symlink_inside_the_run_directory_is_a_usage_error(tmp_path: Path) -> None:
    clean = _case(tmp_path / "clean")
    written = clean.run()
    run_dir = clean.root / "evidence" / written.run_id
    folder = next(path.name for path in sorted(run_dir.iterdir()) if path.is_dir())

    case = _case(tmp_path / "linked")
    linked_dir = case.root / "evidence" / written.run_id
    linked_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, linked_dir / folder, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privileges on Windows
        pytest.skip("creating a symlink requires privileges on this machine")

    message = _refusal(case)

    evidence = case.root / "evidence"
    assert message.startswith(f"cannot write the evidence bundle under {evidence}: ")
    assert "symlink" in message
    assert list(outside.iterdir()) == []
    assert not (linked_dir / "manifest.json").exists()


def test_the_store_answers_a_refused_write_with_its_own_error(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.write_text("kept\n", encoding="utf-8")
    store = FilesystemArtifactStore(root)

    with pytest.raises(ArtifactStoreError) as caught:
        store.put(RUN, "checks/one.json", b"{}")

    message = str(caught.value)
    assert re.fullmatch(r"cannot write 'checks/one\.json' of run 'run-1': \w+Error", message)
    assert isinstance(caught.value.__cause__, OSError)
    assert root.read_text(encoding="utf-8") == "kept\n"


def test_the_store_answers_a_refused_look_with_its_own_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that may not be searched refuses the look itself."""
    store = FilesystemArtifactStore(tmp_path)
    store.put(RUN, "checks/one.json", b"{}")
    looked_at = os.lstat

    def refuse(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if isinstance(path, (str, os.PathLike)) and tmp_path in Path(os.fspath(path)).parents:
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
        return looked_at(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", refuse)

    with pytest.raises(ArtifactStoreError) as looked:
        store.exists(RUN, "checks/one.json")
    where = "'checks/one.json' of run 'run-1'"
    assert str(looked.value) == f"cannot look for {where}: PermissionError"
    assert isinstance(looked.value.__cause__, PermissionError)

    with pytest.raises(ArtifactStoreError) as read:
        store.get(RUN, "checks/one.json")
    assert str(read.value) == f"cannot read {where}: PermissionError"

    with pytest.raises(ArtifactStoreError) as wrote:
        store.put(RUN, "checks/two.json", b"{}")
    assert str(wrote.value) == "cannot write 'checks/two.json' of run 'run-1': PermissionError"


def test_a_refused_look_into_the_evidence_directory_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    looked = os.lstat

    def refuse(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if isinstance(path, (str, os.PathLike)) and evidence in Path(os.fspath(path)).parents:
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
        return looked(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", refuse)

    message = _refusal(case)

    assert message.startswith(f"cannot write the evidence bundle under {evidence}: cannot ")
    assert message.endswith(": PermissionError")
    assert not evidence.exists()


# --- the commands as they are run ---------------------------------------------------


@pytest.mark.parametrize("which", list(RUN_FILES))
@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(NO_SUCH_DATE, id="a date that does not exist"),
        pytest.param("certified: !!bool perhaps\n", id="a !!bool that is not one"),
        pytest.param("broken: [a, b\n", id="an open sequence"),
    ],
)
def test_decision_verify_exits_2_with_one_line_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], which: str, damage: str
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, damage)

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: cannot read {path}: ")
    assert captured.err.count("\n") == 1
    assert captured.err.endswith("\n")
    assert not (case.root / "evidence").exists()


def test_decision_verify_exits_2_when_the_evidence_path_is_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    evidence.write_text("kept\n", encoding="utf-8")

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: cannot write the evidence bundle under {evidence}: ")
    assert captured.err.count("\n") == 1


@pytest.mark.parametrize("which", CAPTURE_FILES)
@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(NO_SUCH_DATE, id="a date that does not exist"),
        pytest.param("certified: !!bool perhaps\n", id="a !!bool that is not one"),
        pytest.param("broken: [a, b\n", id="an open sequence"),
    ],
)
def test_decision_capture_exits_2_with_one_line_and_opens_no_query(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    damage: str,
) -> None:
    case = _case(tmp_path)
    path = RUN_FILES[which](case)
    _append(path, damage)
    output = case.root / "captured.yaml"
    monkeypatch.setattr(cli, "_result_capture_target", lambda engine: (_never, "env", "reader"))

    argv = ["decision", "capture", str(_assets(case)), "--candidate", str(case.candidate)]
    argv += ["--dwh", "clickhouse", "--output", str(output), "--captured-at", CREATED_AT]
    assert main(argv) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: cannot read {path}: ")
    assert captured.err.count("\n") == 1
    assert not output.exists()

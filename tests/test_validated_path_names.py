"""A path is named by the place of the validated path that reached it.

Durable spec: docs/specs/validated-path-names.md. An issue named a file by the text of the
validated path it was found under, and the report is sealed whole into the run id: the same
commit verified from another checkout directory by absolute paths, or given through `..`
instead of `contracts`, was another report and another run id once an issue named a file. A path
that could not be gone through was an issue for each validated path that reached it. Now a path
is named by the place of the validated path that reached it first — `$1` for the first path
given — and the names below that path; each file is read once and each unread path is one issue.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision import validate
from auto_bi.decision.use_cases import VerifyRequest, verify
from auto_bi.decision.validate import validate_paths
from auto_bi.semantic.gateway import NATIVE_SOURCE_VERSION
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, GateReport
from tests.test_decision_verify import CREATED_AT, Case
from tests.test_one_line_errors import NO_PRODUCT, _refused_over
from tests.test_unlisted_paths import _answer, _deny_listing

BS = chr(92)
NOPE = "kind: Nope\n"
NOTE = "$1/notes/x.yaml"
MISSING = "path does not exist"
UNLISTED = "cannot list directory: PermissionError"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _sources(paths: Sequence[str | Path]) -> list[str]:
    return [issue.source for issue in validate_paths(paths).issues]


def _unread(paths: Sequence[str | Path]) -> list[tuple[str, str]]:
    """What each `contract.unreadable` issue of *paths* names, and why; it is its subject too."""
    issues = validate_paths(paths).issues
    assert all(issue.code == "contract.unreadable" for issue in issues)
    assert all(issue.subject == issue.source for issue in issues)
    return [(issue.source, issue.message) for issue in issues]


def _checkout(root: Path) -> Case:
    """A checkout of the run fixtures with one file below `contracts/` that is no contract."""
    root.mkdir(parents=True)
    case = Case(root).with_candidate()
    _write(case.contracts / "notes" / "x.yaml", NOPE)
    return case


def _run(case: Case, paths: Sequence[str | Path], evidence: Path) -> GateReport:
    return verify(
        VerifyRequest(
            contract_paths=paths,
            base=case.base,
            candidate=case.candidate,
            evidence_dir=evidence,
            created_at=CREATED_AT,
            dashboards_dir=case.dashboards,
        )
    )


def _sealed(report: GateReport, evidence: Path) -> list[tuple[str, str]]:
    """The issues the run sealed in its `contracts.validate` payload: code and name."""
    path = evidence / report.run_id / "checks" / "check-contracts.validate.json"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return [(issue["code"], issue["source"]) for issue in payload["issues"]]


# --- one name wherever the files lie ---------------------------------------------------------


def test_the_same_files_are_one_report_and_one_run_wherever_they_lie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two checkout directories of the same files, the path relative, with `./`, with a trailing
    separator, absolute or through `..`, inside the working directory or outside it: one name of
    the file, one run id, one bundle."""
    a, b = _checkout(tmp_path / "checkout-a"), _checkout(tmp_path / "checkout-b")
    given = [
        (a, "contracts"),
        (a, "./contracts/"),
        (a, f"contracts{os.sep}"),
        (a, str(a.contracts)),
        (a, os.path.join("..", "checkout-a", "contracts")),
        (b, "contracts"),
        (b, str(b.contracts)),
        (b, os.path.join("..", "checkout-a", "contracts")),  # outside the working directory
    ]
    runs: set[tuple[str, str]] = set()
    for number, (case, path) in enumerate(given):
        monkeypatch.chdir(case.root)
        evidence = tmp_path / f"evidence-{number}"
        report = _run(case, [path], evidence)
        assert _sealed(report, evidence) == [("contract.unknown_kind", NOTE)], path
        runs.add((report.run_id, report.evidence_digest))
    assert len(runs) == 1


def test_the_working_directory_given_as_dot_is_named_by_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.` names no directory: what it holds is named below `$1`, never below the checkout."""
    _write(tmp_path / "contracts" / "notes" / "x.yaml", NOPE)
    monkeypatch.chdir(tmp_path / "contracts")
    for given in (".", Path(), "./", Path("notes", "..")):
        assert _sources([given]) == [NOTE], given


def test_a_file_several_paths_reach_is_named_by_the_first_that_reached_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "contracts"
    _write(root / "notes" / "x.yaml", NOPE)
    assert _sources([root, root / "notes"]) == [NOTE]
    assert _sources([root / "notes", root]) == ["$1/x.yaml"]
    assert _sources([root / "notes" / "x.yaml", root]) == ["$1"]
    assert _sources([root, root]) == [NOTE]


def test_the_place_is_the_place_among_every_path_given(tmp_path: Path) -> None:
    """A repeat keeps its place, so `$3` is the third path given; and the same paths in another
    order are other names (a boundary)."""
    root = tmp_path / "contracts"
    root.mkdir()
    gone = tmp_path / "gone"
    assert _unread([root, root, gone]) == [("$3", MISSING)]
    assert _unread([root, gone]) == [("$2", MISSING)]
    assert _unread([gone, root]) == [("$1", MISSING)]


@pytest.mark.skipif(sys.platform != "win32", reason="drives and extended paths are Windows'")
def test_a_drive_and_an_extended_path_are_no_part_of_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "contracts"
    _write(root / "notes" / "x.yaml", NOPE)
    monkeypatch.chdir(tmp_path)
    for given in (
        f"{tmp_path.drive}contracts",  # relative to the working directory of the drive
        root.as_posix(),
        f"{BS}{BS}?{BS}{root}",
        str(root).lower(),
    ):
        assert _sources([given]) == [NOTE], given


@pytest.mark.skipif(sys.platform != "win32", reason="administrative shares are Windows'")
def test_a_share_is_no_part_of_the_name(tmp_path: Path) -> None:
    root = tmp_path / "contracts"
    _write(root / "notes" / "x.yaml", NOPE)
    share = f"{BS}{BS}localhost{BS}{tmp_path.drive[0]}${str(root)[2:]}"
    if not os.path.isdir(share):
        pytest.skip(f"no administrative share reaches {root}")
    assert _sources([share]) == [NOTE]


@pytest.mark.parametrize(
    ("root", "reached"),
    [
        pytest.param(PureWindowsPath("C:/w/contracts"), "C:/w/contracts/notes/x.yaml", id="C:/"),
        pytest.param(PureWindowsPath("C:"), "C:notes/x.yaml", id="a drive alone"),
        pytest.param(PureWindowsPath("//server/share/"), "//server/share/notes/x.yaml", id="UNC"),
        pytest.param(PureWindowsPath("//?/C:/w"), "//?/C:/w/notes/x.yaml", id="extended"),
        pytest.param(PurePosixPath("/"), "/notes/x.yaml", id="the root"),
        pytest.param(PurePosixPath("//"), "//notes/x.yaml", id="two slashes"),
        pytest.param(PurePosixPath("."), "./notes/x.yaml", id="dot"),
        pytest.param(PurePosixPath(".."), "../notes/x.yaml", id="dot-dot"),
    ],
)
def test_the_name_holds_the_names_below_the_validated_path_alone(
    root: PurePath, reached: str
) -> None:
    """On every system: what the walk joins to a validated path loses the path's anchor, drive,
    share and names when it is named; the path itself is its place."""
    assert validate._named("$1", root, type(root)(reached)) == NOTE
    assert validate._named("$2", root, root) == "$2"


# --- once however many paths reach it ---------------------------------------------------------


def test_a_missing_path_is_reported_before_an_earlier_files_parse_error(tmp_path: Path) -> None:
    bad = _write(tmp_path / "bad.yaml", "tables: [a, b\n")
    missing = tmp_path / "missing.yaml"

    assert _unread([bad, missing]) == [
        ("$2", MISSING),
        ("$1", "cannot read contract: ParserError"),
    ]


def test_a_path_that_is_not_there_is_one_issue_however_it_is_given_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "contracts"
    root.mkdir()
    gone = root / "gone.yaml"
    monkeypatch.chdir(tmp_path)
    for again in (gone, Path("contracts", "gone.yaml"), f"contracts{os.sep}.{os.sep}gone.yaml"):
        assert _unread([root, gone, again]) == [("$2", MISSING)], again


def test_a_directory_that_cannot_be_listed_is_one_issue_under_its_first_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "contracts"
    archive = root / "archive"
    archive.mkdir(parents=True)
    _deny_listing(monkeypatch, archive)
    assert _unread([root, archive]) == [("$1/archive", UNLISTED)]
    assert _unread([archive, root]) == [("$1", UNLISTED)]
    assert _unread([archive, archive]) == [("$1", UNLISTED)]


def test_a_path_refused_a_look_is_one_issue_however_many_times_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "contracts" / "x.yaml", NOPE)
    _answer(monkeypatch, path, PermissionError(errno.EACCES, "denied"))
    assert _unread([path, path]) == [("$1", "cannot look at path: PermissionError")]


def test_a_run_given_a_path_twice_is_the_run_of_it_given_once(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    gone = tmp_path / "gone"
    once = _run(case, [case.contracts, gone], tmp_path / "once")
    twice = _run(case, [case.contracts, gone, gone], tmp_path / "twice")
    assert _sealed(twice, tmp_path / "twice") == [("contract.unreadable", "$2")]
    assert (twice.run_id, twice.evidence_digest) == (once.run_id, once.evidence_digest)


def test_a_relative_path_keeps_its_text_where_the_working_directory_cannot_be_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "contracts" / "notes" / "x.yaml", NOPE)
    monkeypatch.chdir(tmp_path)

    def removed() -> str:
        raise FileNotFoundError(errno.ENOENT, "the working directory was removed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "getcwd", removed)
        files = _sources(["contracts", "contracts"])
        missing = _unread([Path("contracts", "gone.yaml"), Path("contracts", "gone.yaml")])
    assert files == [NOTE]
    assert missing == [("$1", MISSING)]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows removes no working directory")
def test_a_removed_working_directory_leaves_a_relative_path_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = tmp_path / "removed"
    removed.mkdir()
    monkeypatch.chdir(removed)
    removed.rmdir()
    try:
        os.getcwd()
    except FileNotFoundError:
        pass
    else:
        pytest.skip("this system still tells a removed working directory")
    assert _unread(["gone.yaml", "gone.yaml"]) == [("$1", MISSING)]


# --- the command and the boundaries -----------------------------------------------------------


def test_decision_validate_answers_alike_for_any_spelling_of_the_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "contracts"
    _write(root / "notes" / "x.yaml", NOPE)
    monkeypatch.chdir(tmp_path)
    answers = []
    for paths in (["contracts"], ["./contracts/", str(root), "contracts/notes/x.yaml"]):
        assert main(["decision", "validate", *paths]) == EXIT_ERROR
        assert main(["decision", "validate", *paths, "--json"]) == EXIT_ERROR
        answers.append(capsys.readouterr().out)
    assert answers[0] == answers[1]
    assert f"[{NOTE}]" in answers[0]
    assert str(tmp_path) not in answers[0]


def test_a_name_below_a_validated_path_is_quoted_on_the_line_where_it_is_not_printable(
    tmp_path: Path,
) -> None:
    """A name a directory lists is in the name of the path, and the line of a refusal spells it
    as it spells a path (one-line-errors): a line separator is printed as its escape."""
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    _write(case.contracts / "notes" / f"a{chr(0x2028)}b.yaml", "- not a mapping\n")
    assert _refused_over(case, case.contracts) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: "
        "'$1/notes/a\\u2028b.yaml' (contract file must contain a YAML mapping)"
    )


def test_an_invalid_file_given_itself_is_the_subject_by_the_name_it_was_given(
    tmp_path: Path,
) -> None:
    """A boundary: a contract that declares no id is the subject by the name of its file, and a
    file given itself has the name it was given — on a filesystem that folds case, another
    spelling is another subject."""
    root = tmp_path / "contracts"
    _write(root / "extra.yaml", "kind: MetricContract\n")
    upper = root / "EXTRA.YAML"
    if not upper.exists():
        pytest.skip("this filesystem keeps the case of file names")
    [listed] = validate_paths([root]).issues
    [given] = validate_paths([upper]).issues
    assert (listed.code, listed.subject, listed.source) == (
        "contract.invalid",
        "extra.yaml",
        "$1/extra.yaml",
    )
    assert (given.code, given.subject, given.source) == ("contract.invalid", "EXTRA.YAML", "$1")


def test_the_versions() -> None:
    assert int(VERIFIER_VERSION) >= 121
    assert int(CHECK_VERSION) >= 80
    assert NATIVE_SOURCE_VERSION == "3"

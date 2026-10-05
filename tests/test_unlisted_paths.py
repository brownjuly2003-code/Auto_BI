"""A directory a command cannot list is not an empty one, and a path it may not look at is not
an absent one.

`Path.rglob` drops the refusal of a directory the filesystem will not list: what the directory
holds is missing from the result and nothing in the result says so. A reader that answers for
every file under a path then answers for the files it was shown — with the verified assets in
such a directory `decision verify` passed without them. From Python 3.14 on `Path.exists`,
`Path.is_file` and `Path.is_dir` drop the refusal of a look as well, so a path under a directory
that may not be searched reads as one that is not there. The tests refuse where the operating
system does — `os.scandir`, `os.stat` — and hold whatever the interpreter's `pathlib` makes of
such an answer (`docs/specs/unreadable-inputs.md`).
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from auto_bi import cli, filesystem
from auto_bi.cli import main
from auto_bi.decision.use_cases import (
    CaptureRequest,
    VerifyError,
    VerifyRequest,
    capture_result,
    verify,
)
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.manifest import MANIFEST_PATH
from auto_bi.filesystem import paths_under
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import EXIT_ERROR, Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from auto_bi.verification.repository import VerifiedAssetRepository
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    ENVIRONMENT_REF,
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    metric_contract,
    status_of,
    summary_of,
)
from tests.test_unreadable_run_inputs import (
    _assets,
    _capture_refused,
    _case,
    _deny_look,
    _never,
    _refusal,
    _refused,
    _results,
)

VERIFIED = f"{PRODUCT_ID}@1"
PIN = "metric://sales/gross_margin_pct@3"
NO_PRODUCT = "no decision product contract found in the given paths"

LOOKS = [filesystem.exists, filesystem.is_file, filesystem.is_dir]
LOOK_IDS = ["exists", "is_file", "is_dir"]

#: What the operating system answers for a path that is not there.
NOT_THERE = [errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP]
NOT_THERE_IDS = ["no such entry", "below what is no directory", "no descriptor", "an endless link"]

Lock = Callable[..., None]


def _deny_listing(
    monkeypatch: pytest.MonkeyPatch,
    *denied: str | Path,
    refusal: type[OSError] = PermissionError,
    code: int = errno.EACCES,
) -> None:
    """Make each of *denied* a directory this process may not list, on any platform, as anyone."""
    original = os.scandir
    refused = {os.fspath(path) for path in denied}

    def scandir(path: Any = ".") -> Any:
        if os.fspath(path) in refused:
            raise refusal(code, os.strerror(code), os.fspath(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)


def _answer(monkeypatch: pytest.MonkeyPatch, path: Path, error: OSError) -> None:
    """Make the operating system answer a look at *path* with *error*."""
    original = os.stat

    def stat(looked_at: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if isinstance(looked_at, (str, os.PathLike)) and os.fspath(looked_at) == str(path):
            raise error
        return original(looked_at, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


class _Untold:
    """An entry of a directory whose kind the filesystem will not tell."""

    def __init__(self, directory: Path, name: str, error: OSError) -> None:
        self.name = name
        self.path = os.path.join(directory, name)
        self._error = error

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        raise self._error


class _Scan(list[_Untold]):
    """What `os.scandir` gives: the entries, and a scan to close."""

    def __enter__(self) -> _Scan:
        return self

    def __exit__(self, *raised: object) -> None:
        return None


def _untell(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    refusal: type[OSError],
    code: int,
    gone: str | None = None,
) -> None:
    """List *directory* as it is, and refuse to say which of its entries are directories.

    The entry named *gone* went away while it was asked: the answer for it is that nothing is
    there. The entries are asked in the order of their names.
    """
    original = os.scandir
    with original(directory) as scan:
        names = sorted(entry.name for entry in scan)

    def answer(name: str) -> OSError:
        if name == gone:
            code_gone = errno.ENOENT
            return FileNotFoundError(code_gone, os.strerror(code_gone), str(directory / name))
        return refusal(code, os.strerror(code), str(directory / name))

    def scandir(path: Any = ".") -> Any:
        if os.fspath(path) != os.fspath(directory):
            return original(path)
        return _Scan(_Untold(directory, name, answer(name)) for name in names)

    monkeypatch.setattr(os, "scandir", scandir)


@pytest.fixture
def lock() -> Iterator[Lock]:
    """Take permissions of a directory away for as long as the test runs: all of them, or all
    but the one named — `0o400` leaves a directory that is listed and may not be searched.

    The test is skipped where that does not keep this process out: as root, and on a
    filesystem that holds no such permission.
    """
    locked: list[Path] = []

    def take(directory: Path, mode: int = 0) -> None:
        directory.chmod(mode)
        locked.append(directory)
        try:
            if mode & 0o400:  # it may be listed: then it is the search that must be refused
                os.stat(directory / "no such entry")
            else:
                with os.scandir(directory):
                    pass
        except PermissionError:
            return
        except FileNotFoundError:
            pass
        pytest.skip(f"this process is not kept out of a directory of mode {mode:03o}")

    yield take
    for directory in reversed(locked):
        directory.chmod(0o755)


def _unlisted(directory: Path, refusal: str = "PermissionError") -> dict[str, Any]:
    """The one issue a directory under the validated paths that cannot be listed gives."""
    return {
        "code": "contract.unreadable",
        "severity": "block",
        "subject": str(directory),
        "source": str(directory),
        "message": f"cannot list directory: {refusal}",
    }


def _unlooked(path: Path) -> dict[str, Any]:
    """The one issue a validated path that may not be looked at gives."""
    return {
        "code": "contract.unreadable",
        "severity": "block",
        "subject": str(path),
        "source": str(path),
        "message": "cannot look at path: PermissionError",
    }


def _issues(paths: list[Path]) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in validate_paths(paths).issues]


def _refused_as_laid_out(case: Case) -> str:
    """What `decision verify` refuses *case* with, its candidate taken as it lies on disk."""
    request = VerifyRequest(
        contract_paths=[case.contracts],
        base=case.base,
        candidate=case.candidate,
        evidence_dir=case.root / "evidence",
        created_at=CREATED_AT,
        dashboards_dir=case.dashboards,
        assets_dir=case.assets,
        results_file=case.results,
    )
    with pytest.raises(VerifyError) as caught:
        verify(request)
    assert not (case.root / "evidence").exists()
    return str(caught.value)


def _tree(root: Path) -> Path:
    """Directories in directories, names that begin with a dot, a directory named like a file."""
    tree = root / "tree"
    for folder in ("a/b/c", "a/.hidden", "d.yaml", "empty", "kept/inner"):
        (tree / folder).mkdir(parents=True)
    for file in (
        "top.yaml",
        ".dot.yaml",
        "a/one.yml",
        "a/b/two.yaml",
        "a/b/c/three.txt",
        "a/.hidden/four.yaml",
        "d.yaml/in_a_directory_named_like_a_file.yaml",
        "kept/five.yaml",
        "kept/inner/six.yaml",
    ):
        (tree / file).write_text("x: 1\n", encoding="utf-8")
    return tree


def _link(target: Path, link: Path, *, to_directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=to_directory)
    except OSError:
        pytest.skip("this process may not create a symbolic link")


# --- a look at a path ----------------------------------------------------------------


def test_a_look_says_what_is_there(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    file = tree / "top.yaml"
    folder = tree / "kept"

    assert [look(file) for look in LOOKS] == [True, True, False]
    assert [look(folder) for look in LOOKS] == [True, False, True]
    assert [look(str(file)) for look in LOOKS] == [True, True, False]
    # A directory named like a file is a directory.
    assert [look(tree / "d.yaml") for look in LOOKS] == [True, False, True]


def test_a_look_follows_a_link(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    _link(tree / "top.yaml", tree / "to_a_file.yaml")
    _link(tree / "kept", tree / "to_a_directory", to_directory=True)

    assert [look(tree / "to_a_file.yaml") for look in LOOKS] == [True, True, False]
    assert [look(tree / "to_a_directory") for look in LOOKS] == [True, False, True]


@pytest.mark.parametrize(
    "what", ["nothing", "below a file", "a link to nothing", "a link to itself", "a NUL in it"]
)
def test_a_look_says_no_of_what_is_not_there(tmp_path: Path, what: str) -> None:
    tree = _tree(tmp_path)
    path = tree / "nowhere"
    if what == "below a file":
        path = tree / "top.yaml" / "below"
    elif what == "a link to nothing":
        _link(tree / "nowhere", tree / "broken")
        path = tree / "broken"
    elif what == "a link to itself":
        path = tree / "loop"
        _link(path, path)
    elif what == "a NUL in it":
        path = tree / "a\0b"

    assert [look(path) for look in LOOKS] == [False, False, False]


@pytest.mark.parametrize("look", LOOKS, ids=LOOK_IDS)
def test_a_look_that_is_refused_raises_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, look: Callable[[Path], bool]
) -> None:
    tree = _tree(tmp_path)
    _deny_look(monkeypatch, tree / "top.yaml", tree / "kept")

    for path in (tree / "top.yaml", tree / "kept"):
        with pytest.raises(PermissionError) as caught:
            look(path)
        assert caught.value.errno == errno.EACCES
        assert caught.value.filename == str(path)
    # What was not refused is answered as before.
    assert look(tree / "a") is (look is not filesystem.is_file)


@pytest.mark.parametrize("look", LOOKS, ids=LOOK_IDS)
def test_any_other_refusal_of_a_look_raises_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, look: Callable[[Path], bool]
) -> None:
    tree = _tree(tmp_path)
    path = tree / "top.yaml"
    _answer(monkeypatch, path, OSError(errno.EIO, os.strerror(errno.EIO), str(path)))

    with pytest.raises(OSError, match="top") as caught:
        look(path)
    assert caught.value.errno == errno.EIO


@pytest.mark.parametrize("code", NOT_THERE, ids=NOT_THERE_IDS)
def test_each_answer_for_a_path_that_is_not_there_is_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    tree = _tree(tmp_path)
    path = tree / "top.yaml"
    _answer(monkeypatch, path, OSError(code, os.strerror(code), str(path)))

    assert [look(path) for look in LOOKS] == [False, False, False]


@pytest.mark.parametrize(
    "winerror", [21, 123, 1921], ids=["not ready", "no such name", "an endless link"]
)
def test_the_answers_of_windows_for_a_path_that_is_not_there_are_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    """Told by the code Windows gives beside `errno` — given here on whatever runs the test."""
    tree = _tree(tmp_path)
    path = tree / "top.yaml"
    error = OSError(errno.EINVAL, "refused", str(path))
    error.winerror = winerror
    _answer(monkeypatch, path, error)

    assert [look(path) for look in LOOKS] == [False, False, False]

    error.winerror = 5  # access is denied
    for look in LOOKS:
        with pytest.raises(OSError, match="refused"):
            look(path)


@pytest.mark.parametrize("mode", [0o000, 0o400], ids=["no permission", "listed, not searched"])
def test_a_look_under_a_directory_that_may_not_be_searched_raises_where_modes_are_kept(
    tmp_path: Path, lock: Lock, mode: int
) -> None:
    tree = _tree(tmp_path)
    lock(tree / "kept", mode)

    for look in LOOKS:
        for path in (tree / "kept" / "five.yaml", tree / "kept" / "inner", tree / "kept" / "no"):
            with pytest.raises(PermissionError) as caught:
                look(path)
            assert caught.value.filename == str(path)
    # The directory itself is looked at through the one above it.
    assert filesystem.is_dir(tree / "kept")


# --- the paths under a directory ---------------------------------------------------


def test_the_walk_gives_the_paths_rglob_gives(tmp_path: Path) -> None:
    tree = _tree(tmp_path)

    walked = sorted(paths_under(tree))

    assert walked == sorted(tree.rglob("*"))
    assert len(walked) == 17  # eight directories and nine files
    assert sorted(paths_under(str(tree))) == walked
    refused: list[OSError] = []
    assert sorted(paths_under(tree, refused)) == walked
    assert refused == []


def test_the_walk_gives_a_link_and_does_not_follow_it(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.yaml").write_text("x: 1\n", encoding="utf-8")
    _link(outside, tree / "to_a_directory", to_directory=True)
    _link(outside / "elsewhere.yaml", tree / "to_a_file.yaml")
    _link(tmp_path / "nowhere", tree / "to_nothing.yaml")

    walked = sorted(paths_under(tree))

    assert walked == sorted(tree.rglob("*"))
    assert {"to_a_directory", "to_a_file.yaml", "to_nothing.yaml"} <= {path.name for path in walked}
    assert tree / "to_a_directory" / "elsewhere.yaml" not in walked


def test_a_path_that_is_no_directory_holds_nothing(tmp_path: Path) -> None:
    tree = _tree(tmp_path)

    for root in (tmp_path / "nowhere", tree / "top.yaml"):
        refused: list[OSError] = []
        assert list(paths_under(root)) == list(root.rglob("*")) == []
        assert list(paths_under(root, refused)) == []
        assert refused == []


@pytest.mark.parametrize("where", ["", "kept", "kept/inner"], ids=["itself", "below", "deep"])
def test_a_directory_that_cannot_be_listed_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    tree = _tree(tmp_path)
    denied = tree / where if where else tree
    _deny_listing(monkeypatch, denied)

    with pytest.raises(PermissionError) as caught:
        list(paths_under(tree))

    assert caught.value.filename == str(denied)
    assert caught.value.errno == errno.EACCES


def test_a_directory_that_cannot_be_listed_is_kept_while_the_rest_is_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    _deny_listing(monkeypatch, tree / "kept", tree / "a" / "b")

    refused: list[OSError] = []
    walked = sorted(paths_under(tree, refused))

    assert sorted(str(error.filename) for error in refused) == [
        str(tree / "a" / "b"),
        str(tree / "kept"),
    ]
    assert all(isinstance(error, PermissionError) for error in refused)
    # The two directories are entries of the ones that were listed; nothing below them is.
    hidden = {path for path in everything if (tree / "kept") in path.parents}
    hidden |= {path for path in everything if (tree / "a" / "b") in path.parents}
    assert len(hidden) == 6
    assert walked == sorted(set(everything) - hidden)


def test_the_directory_asked_for_that_cannot_be_listed_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    _deny_listing(monkeypatch, tree)

    refused: list[OSError] = []

    assert list(paths_under(tree, refused)) == []
    assert [str(error.filename) for error in refused] == [str(tree)]


@pytest.mark.parametrize("code", NOT_THERE, ids=NOT_THERE_IDS)
def test_a_directory_that_went_away_is_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    _deny_listing(monkeypatch, tree / "kept", refusal=OSError, code=code)

    refused: list[OSError] = []
    walked = sorted(paths_under(tree, refused))

    assert refused == []
    assert walked == sorted(path for path in everything if (tree / "kept") not in path.parents)
    assert sorted(paths_under(tree)) == walked


def test_any_other_refusal_of_a_listing_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    _deny_listing(monkeypatch, tree / "kept", refusal=OSError, code=errno.EIO)

    with pytest.raises(OSError, match="kept") as caught:
        list(paths_under(tree))
    assert caught.value.errno == errno.EIO

    refused: list[OSError] = []
    list(paths_under(tree, refused))
    assert [(type(error), error.errno) for error in refused] == [(OSError, errno.EIO)]


def test_a_directory_whose_entries_are_not_told_apart_is_refused_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listed, and no entry may be looked at: which of them are directories is not known."""
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    _untell(monkeypatch, tree / "kept", PermissionError, errno.EACCES)

    refused: list[OSError] = []
    walked = sorted(paths_under(tree, refused))

    # One refusal for the directory, not one for each of its two entries.
    assert [(type(error), error.errno, error.filename) for error in refused] == [
        (PermissionError, errno.EACCES, str(tree / "kept"))
    ]
    # Its entries are given; what is under them was not walked.
    assert walked == sorted(set(everything) - {tree / "kept" / "inner" / "six.yaml"})
    with pytest.raises(PermissionError) as caught:
        list(paths_under(tree))
    assert caught.value.filename == str(tree / "kept")


def test_an_entry_that_went_away_while_it_was_asked_is_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    _untell(monkeypatch, tree / "kept", FileNotFoundError, errno.ENOENT)

    refused: list[OSError] = []

    assert sorted(paths_under(tree, refused)) == sorted(
        set(everything) - {tree / "kept" / "inner" / "six.yaml"}
    )
    assert refused == []


@pytest.mark.parametrize("gone", ["five.yaml", "inner"], ids=["asked first", "asked last"])
def test_an_entry_that_went_away_does_not_hide_the_one_whose_kind_is_not_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gone: str
) -> None:
    """Of the two entries one went away and the other is there, and its kind is refused."""
    tree = _tree(tmp_path)
    _untell(monkeypatch, tree / "kept", PermissionError, errno.EACCES, gone=gone)

    refused: list[OSError] = []
    list(paths_under(tree, refused))

    assert [(type(error), error.errno, error.filename) for error in refused] == [
        (PermissionError, errno.EACCES, str(tree / "kept"))
    ]
    with pytest.raises(PermissionError) as caught:
        list(paths_under(tree))
    assert caught.value.filename == str(tree / "kept")


@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no permission", "searched, not listed"])
def test_a_directory_that_may_not_be_listed_is_refused_where_modes_are_kept(
    tmp_path: Path, lock: Lock, mode: int
) -> None:
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    lock(tree / "kept", mode)

    refused: list[OSError] = []
    walked = sorted(paths_under(tree, refused))

    assert [(type(error), str(error.filename)) for error in refused] == [
        (PermissionError, str(tree / "kept"))
    ]
    assert walked == sorted(path for path in everything if (tree / "kept") not in path.parents)
    # `rglob` gives the same paths and says nothing of the directory it could not list.
    assert sorted(tree.rglob("*")) == walked
    with pytest.raises(PermissionError):
        list(paths_under(tree))


def test_a_directory_that_is_listed_and_not_searched_refuses_what_is_below_it(
    tmp_path: Path, lock: Lock
) -> None:
    tree = _tree(tmp_path)
    everything = sorted(paths_under(tree))
    lock(tree / "kept", 0o400)

    refused: list[OSError] = []
    walked = sorted(paths_under(tree, refused))

    # Its entries are given; the directory among them cannot be entered.
    assert walked == sorted(set(everything) - {tree / "kept" / "inner" / "six.yaml"})
    assert [type(error) for error in refused] == [PermissionError]
    assert Path(str(refused[0].filename)) in (tree / "kept", tree / "kept" / "inner")
    assert sorted(tree.rglob("*")) == walked


# --- the validated paths -----------------------------------------------------------


def test_a_validated_directory_that_cannot_be_listed_is_an_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    kept = case.contracts / "archive"
    _write_yaml(kept / "gross_margin_pct.yaml", metric_contract())  # a second file of one id
    assert [issue["code"] for issue in _issues([case.contracts])] == ["contract.duplicate_id"]
    _deny_listing(monkeypatch, kept)

    report = validate_paths([case.contracts])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [_unlisted(kept)]
    assert report.products == [VERIFIED]
    assert report.metrics == [PIN]
    assert report.verdict is Verdict.BLOCK


def test_the_validated_directory_itself_that_cannot_be_listed_is_an_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    _deny_listing(monkeypatch, case.contracts)

    report = validate_paths([case.contracts])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [_unlisted(case.contracts)]
    assert report.products == []
    assert report.metrics == []
    assert report.verdict is Verdict.BLOCK


def test_unlisted_directories_come_first_in_the_order_of_their_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    kept = [case.contracts / name for name in ("m", "c", "x", "a", "t", "f")]
    for directory in kept:
        directory.mkdir()
    broken = case.contracts / "a_broken.yaml"
    broken.write_text("tables: [a, b\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    _deny_listing(monkeypatch, *kept, other)

    issues = _issues([other, case.contracts])

    # The path given first; then the directories of the second in the order of their
    # paths, not in the order the walk came to them; then what the files it read gave.
    assert issues[:7] == [_unlisted(other), *(_unlisted(directory) for directory in sorted(kept))]
    assert [(issue["code"], issue["source"], issue["message"]) for issue in issues[7:]] == [
        ("contract.unreadable", str(broken), "cannot read contract: ParserError")
    ]


def test_a_validated_path_is_named_as_the_report_names_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given as `.`, the directory under it is named as the files under it are: without it."""
    case = Case(tmp_path)
    (case.contracts / "archive").mkdir()
    monkeypatch.chdir(case.contracts)
    _deny_listing(monkeypatch, os.path.join(".", "archive"))  # as the walk comes to it

    issues = _issues([Path()])

    assert issues == [_unlisted(Path("archive"))]
    assert issues[0]["source"] == "archive"


def test_a_refusal_of_a_listing_is_reported_by_its_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    kept = case.contracts / "archive"
    kept.mkdir()
    _deny_listing(monkeypatch, kept, refusal=OSError, code=errno.EIO)

    assert _issues([case.contracts]) == [_unlisted(kept, "OSError")]


def test_a_validated_directory_whose_entries_are_not_told_apart_is_an_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    kept = case.contracts / "archive"
    (kept / "older").mkdir(parents=True)
    _write_yaml(kept / "older" / "weekly_margin.yaml", {"kind": "DecisionProduct"})
    _untell(monkeypatch, kept, PermissionError, errno.EACCES)

    # What is under `older` was not walked, and the report says of which directory.
    assert _issues([case.contracts]) == [_unlisted(kept)]


@pytest.mark.parametrize("which", ["a file", "a directory"])
def test_a_validated_path_that_may_not_be_looked_at_is_an_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    case = Case(tmp_path)
    product = case.contracts / "weekly_margin.yaml"
    metric = case.contracts / "gross_margin_pct.yaml"
    denied = metric
    if which == "a directory":
        denied = case.contracts / "metrics"
        denied.mkdir()
        metric.rename(denied / "gross_margin_pct.yaml")
    _deny_look(monkeypatch, denied)

    report = validate_paths([denied, product])

    assert report.issues[0].model_dump(mode="json") == _unlooked(denied)
    # The product is still loaded and validated: its pin has no contract among what was read.
    assert [issue.code for issue in report.issues] == ["contract.unreadable", "metric_ref.unknown"]
    assert report.products == [VERIFIED]
    assert report.metrics == []


def test_a_path_that_is_not_there_is_reported_as_before(tmp_path: Path) -> None:
    case = Case(tmp_path)
    nowhere = tmp_path / "nowhere"

    issues = _issues([nowhere, case.contracts])

    assert issues == [
        {
            "code": "contract.unreadable",
            "severity": "block",
            "subject": str(nowhere),
            "source": str(nowhere),
            "message": "path does not exist",
        }
    ]


def test_contracts_in_directories_below_the_path_are_validated_as_before(tmp_path: Path) -> None:
    case = Case(tmp_path)
    flat = validate_paths([case.contracts])
    below = case.contracts / "sales" / "margin"
    below.mkdir(parents=True)
    (case.contracts / "gross_margin_pct.yaml").rename(below / "gross_margin_pct.yaml")
    (case.contracts / ".notes").mkdir()
    (case.contracts / ".notes" / "readme.txt").write_text("not a contract\n", encoding="utf-8")

    report = validate_paths([case.contracts])

    assert report == flat
    assert report.issues == []
    assert report.metrics == [PIN]


def test_decision_validate_names_the_directory_and_exits_by_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    kept = case.contracts / "archive"
    kept.mkdir()
    _deny_listing(monkeypatch, kept)

    assert main(["decision", "validate", str(case.contracts)]) == 4
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == [
        f"{'block':<8} {'contract.unreadable':<26} {kept}",
        f"{'':<8} cannot list directory: PermissionError [{kept}]",
    ]
    assert printed[-1] == "verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))"

    assert main(["decision", "validate", str(case.contracts), "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["issues"] == [_unlisted(kept)]


def test_decision_validate_without_a_product_shows_what_it_could_not_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    _deny_listing(monkeypatch, case.contracts)

    assert main(["decision", "validate", str(case.contracts)]) == EXIT_ERROR

    assert capsys.readouterr().out.splitlines() == [
        f"{'block':<8} {'contract.unreadable':<26} {case.contracts}",
        f"{'':<8} cannot list directory: PermissionError [{case.contracts}]",
        "verdict: block (0 product(s), 0 metric contract(s), 1 issue(s))",
        f"error: {NO_PRODUCT}",
    ]


def test_decision_validate_names_a_path_it_may_not_look_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    product = case.contracts / "weekly_margin.yaml"
    metric = case.contracts / "gross_margin_pct.yaml"
    _deny_look(monkeypatch, metric)

    assert main(["decision", "validate", str(metric), str(product)]) == 4

    assert capsys.readouterr().out.splitlines()[:2] == [
        f"{'block':<8} {'contract.unreadable':<26} {metric}",
        f"{'':<8} cannot look at path: PermissionError [{metric}]",
    ]


def test_the_gate_blocks_on_a_validated_directory_that_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    kept = case.contracts / "archive"
    kept.mkdir()
    assert _case(tmp_path / "nothing denied").run().verdict is not Verdict.BLOCK
    _deny_listing(monkeypatch, kept)

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert _check_payload(tmp_path, report, "contracts.validate")["issues"] == [_unlisted(kept)]


def test_decision_verify_exits_by_the_verdict_and_writes_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    kept = case.contracts / "archive"
    kept.mkdir()
    _deny_listing(monkeypatch, kept)

    assert main(_verify_argv(case)) == 4

    monkeypatch.undo()
    [run] = list((case.root / "evidence").iterdir())
    assert (run / MANIFEST_PATH).is_file()


def test_the_evidence_versions_are_those_of_a_gate_that_reports_the_directory() -> None:
    """A bundle of verifier 92 may hold a report that read such a directory as an empty one."""
    assert int(VERIFIER_VERSION) >= 93
    assert int(CHECK_VERSION) >= 68


# --- no product among what could be read -----------------------------------------------


def test_no_product_names_the_path_that_could_not_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    _deny_listing(monkeypatch, case.contracts)

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: "
        f"{case.contracts} (cannot list directory: PermissionError)"
    )


def test_no_product_names_a_contract_file_that_could_not_be_read(tmp_path: Path) -> None:
    case = _case(tmp_path)
    product = case.contracts / "weekly_margin.yaml"
    product.write_text("kind: [DecisionProduct\n", encoding="utf-8")

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: "
        f"{product} (cannot read contract: ParserError)"
    )


def test_no_product_names_a_path_that_is_not_there(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.contracts = tmp_path / "no contracts"

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: {case.contracts} (path does not exist)"
    )


def test_an_unknown_product_names_the_paths_that_could_not_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    kept = case.contracts / "archive"
    kept.mkdir()
    _deny_listing(monkeypatch, kept)

    with pytest.raises(VerifyError) as caught:
        case.run(product_id="decision://sales/other")

    assert str(caught.value) == (
        f"no decision product 'decision://sales/other' in the given paths ({PRODUCT_ID}); "
        f"1 path(s) could not be read: {kept} (cannot list directory: PermissionError)"
    )
    assert not (case.root / "evidence").exists()


def test_no_product_names_three_paths_and_counts_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    kept = [case.contracts / f"kept_{index}" for index in range(5)]
    for directory in kept:
        directory.mkdir()
    _deny_listing(monkeypatch, *kept[:3])

    named = ", ".join(
        f"{directory} (cannot list directory: PermissionError)" for directory in kept[:3]
    )
    assert _refused(case) == f"{NO_PRODUCT}; 3 path(s) could not be read: {named}"

    _deny_listing(monkeypatch, *kept)
    assert _refused(case) == f"{NO_PRODUCT}; 5 path(s) could not be read: {named} (+2 more)"


def test_no_product_reads_as_before_when_every_path_was_read(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(VerifyError) as caught:
        case.run(product_id="decision://sales/other")
    assert str(caught.value) == (
        f"no decision product 'decision://sales/other' in the given paths ({PRODUCT_ID})"
    )

    # A contract that is read and is no contract is not a path that could not be read.
    (case.contracts / "weekly_margin.yaml").write_text("kind: Other\n", encoding="utf-8")
    assert _refused(case) == NO_PRODUCT

    (case.contracts / "weekly_margin.yaml").unlink()
    assert _refused(case) == NO_PRODUCT


def test_decision_verify_exits_2_and_names_what_it_could_not_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    _deny_listing(monkeypatch, case.contracts)

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"error: {NO_PRODUCT}; 1 path(s) could not be read: "
        f"{case.contracts} (cannot list directory: PermissionError)\n"
    )
    assert not (case.root / "evidence").exists()


# --- a semantic ref ----------------------------------------------------------------


@pytest.mark.parametrize("ref", ["base", "candidate"])
@pytest.mark.parametrize("where", ["", "sales"], ids=["itself", "below"])
def test_a_metrics_directory_that_cannot_be_listed_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str, where: str
) -> None:
    case = _case(tmp_path)
    metrics = case.root / ref / "metrics"
    denied = metrics / where if where else metrics
    denied.mkdir(exist_ok=True)
    _deny_listing(monkeypatch, denied)

    assert _refused(case) == f"cannot read {denied}: PermissionError"
    if ref == "candidate":
        assert _capture_refused(case) == f"cannot read {denied}: PermissionError"


@pytest.mark.parametrize("ref", ["base", "candidate"])
def test_a_metric_contract_of_a_ref_that_may_not_be_looked_at_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    """Refused while the contracts are listed, before any of them is read."""
    case = _case(tmp_path)
    below = case.root / ref / "metrics" / "sales"
    below.mkdir()
    contract = below / "gross_margin_pct.yaml"
    (case.root / ref / "metrics" / "gross_margin_pct.yaml").rename(contract)
    _deny_look(monkeypatch, contract)

    assert _refused(case) == f"cannot read {contract}: PermissionError"
    if ref == "candidate":
        assert _capture_refused(case) == f"cannot read {contract}: PermissionError"


def test_the_source_raises_the_refusal_of_a_listing_and_of_a_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    metrics = case.base / "metrics"
    source = NativeModelSource(case.base / "model.yaml", metrics)
    clean = source.snapshot()

    with monkeypatch.context() as patch:
        _deny_listing(patch, metrics)
        with pytest.raises(PermissionError) as caught:
            source.snapshot()
        assert caught.value.filename == str(metrics)

    for denied in (metrics / "gross_margin_pct.yaml", metrics):
        with monkeypatch.context() as patch:
            _deny_look(patch, denied)
            with pytest.raises(PermissionError) as caught:
                source.snapshot()
            assert caught.value.filename == str(denied)

    assert source.snapshot() == clean


def test_contracts_in_directories_below_the_metrics_are_frozen_as_before(tmp_path: Path) -> None:
    case = _case(tmp_path)
    metrics = case.base / "metrics"
    flat = NativeModelSource(case.base / "model.yaml", metrics).snapshot()
    below = metrics / "sales" / "margin"
    below.mkdir(parents=True)
    (metrics / "gross_margin_pct.yaml").rename(below / "gross_margin_pct.yaml")
    (metrics / "empty").mkdir()
    (metrics / "notes.txt").write_text("not a contract\n", encoding="utf-8")
    (metrics / "named_like_a_contract.yaml").mkdir()

    moved = NativeModelSource(case.base / "model.yaml", metrics).snapshot()

    assert moved == flat
    assert [node.id for node in moved.graph.metrics] == ["metric://sales/gross_margin_pct"]


# --- the verified assets -----------------------------------------------------------


@pytest.mark.parametrize("where", ["", "archive"], ids=["itself", "below"])
def test_an_assets_directory_that_cannot_be_listed_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    case = _case(tmp_path)
    denied = _assets(case) / where if where else _assets(case)
    denied.mkdir(exist_ok=True)
    _deny_listing(monkeypatch, denied)

    assert _refused(case) == f"cannot read {denied}: PermissionError"
    assert _capture_refused(case) == f"cannot read {denied}: PermissionError"


def test_the_repository_raises_the_refusal_of_a_listing_and_of_a_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    asset = _assets(case) / "weekly_margin_by_region.yaml"

    with monkeypatch.context() as patch:
        _deny_listing(patch, _assets(case))
        with pytest.raises(PermissionError) as caught:
            VerifiedAssetRepository.load(_assets(case))
        assert caught.value.filename == str(_assets(case))

    for denied in (asset, _assets(case)):
        with monkeypatch.context() as patch:
            _deny_look(patch, denied)
            with pytest.raises(PermissionError) as caught:
                VerifiedAssetRepository.load(_assets(case))
            assert caught.value.filename == str(denied)

    assert [item.metadata.id for item in VerifiedAssetRepository.load(_assets(case)).assets] == [
        ASSET_ID
    ]


def test_an_empty_assets_directory_is_an_empty_one(tmp_path: Path) -> None:
    without = Case(tmp_path / "without").with_candidate()
    empty = Case(tmp_path / "empty").with_candidate()
    empty.assets = empty.root / "assets"
    empty.assets.mkdir()

    report = empty.run()

    expected = without.run()
    assert report.verdict is expected.verdict
    assert [(check.check_id, check.status, check.summary_code) for check in report.checks] == [
        (check.check_id, check.status, check.summary_code) for check in expected.checks
    ]
    assert _capture_refused(empty) == (
        f"no verified asset {ASSET_ID!r} under {empty.assets} (none)"
    )


def test_assets_in_directories_below_the_path_are_loaded_as_before(tmp_path: Path) -> None:
    case = _case(tmp_path)
    flat = VerifiedAssetRepository.load(_assets(case))
    below = _assets(case) / "sales" / "margin"
    below.mkdir(parents=True)
    (_assets(case) / "weekly_margin_by_region.yaml").rename(below / "by_region.yaml")
    (_assets(case) / "empty").mkdir()
    (_assets(case) / "notes.txt").write_text("not an asset\n", encoding="utf-8")
    (_assets(case) / "named_like_an_asset.yaml").mkdir()

    moved = VerifiedAssetRepository.load(_assets(case))

    assert [asset.metadata.id for asset in moved.assets] == [ASSET_ID]
    assert [asset.digest() for asset in moved.assets] == [asset.digest() for asset in flat.assets]


# --- a run input that may not be looked at ----------------------------------------------


def test_an_assets_path_that_may_not_be_looked_at_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    _deny_look(monkeypatch, _assets(case))

    assert _refused(case) == f"cannot read {_assets(case)}: PermissionError"
    assert _capture_refused(case) == f"cannot read {_assets(case)}: PermissionError"


def test_a_dashboards_directory_that_may_not_be_looked_at_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    _deny_look(monkeypatch, case.dashboards)

    assert _refused(case) == f"cannot read {case.dashboards}: PermissionError"


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".json"])
def test_a_dashboard_spec_that_may_not_be_looked_at_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    case = _case(tmp_path)
    spec = case.dashboards / "weekly_margin@7.yaml"
    denied = spec.with_suffix(suffix)
    if suffix != ".yaml":
        spec.unlink()  # so that the look goes on to the next name the spec may have
    _deny_look(monkeypatch, denied)

    assert _refused(case) == f"cannot read {denied}: PermissionError"


def test_a_results_file_that_may_not_be_looked_at_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    _deny_look(monkeypatch, _results(case))

    assert _refused(case) == f"cannot read {_results(case)}: PermissionError"


def test_an_output_that_may_not_be_looked_at_ends_the_capture_before_the_engine_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
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
    _deny_look(monkeypatch, output)

    with pytest.raises(VerifyError) as caught:
        capture_result(request)

    monkeypatch.undo()
    refusal = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(output))
    assert str(caught.value) == f"cannot write captured result {output}: {refusal}"
    assert not output.exists()


def test_paths_that_are_not_there_are_named_as_before(tmp_path: Path) -> None:
    case = _case(tmp_path)

    assets, case.assets = _assets(case), tmp_path / "no assets"
    assert _refusal(case) == f"verified assets path not found: {case.assets}"
    case.assets = assets

    dashboards, case.dashboards = case.dashboards, tmp_path / "no dashboards"
    assert _refusal(case) == f"dashboard specs directory not found: {case.dashboards}"
    case.dashboards = dashboards

    results, case.results = _results(case), tmp_path / "no results.yaml"
    assert _refusal(case) == f"recorded results file not found: {case.results}"
    case.results = results

    (case.candidate / "model.yaml").unlink()
    assert _refusal(case) == f"semantic ref {case.candidate} has no model.yaml"


def test_a_dashboard_spec_that_is_not_there_is_the_finding_of_the_gate_as_before(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    (case.dashboards / "weekly_margin@7.yaml").unlink()

    report = case.run()

    assert status_of(report, "bi.contract") is CheckStatus.REVIEW


# --- directories whose permissions are taken away --------------------------------------


@pytest.mark.parametrize(
    "where",
    ["assets", "assets/archive", "base/metrics", "candidate/metrics", "candidate/metrics/sales"],
)
@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no permission", "searched, not listed"])
def test_a_run_directory_that_may_not_be_listed_ends_the_run_where_modes_are_kept(
    tmp_path: Path, lock: Lock, where: str, mode: int
) -> None:
    case = _case(tmp_path)
    directory = case.root / where
    directory.mkdir(exist_ok=True)
    lock(directory, mode)

    assert _refused(case) == f"cannot read {directory}: PermissionError"


@pytest.mark.parametrize(
    ("where", "file"),
    [
        ("assets", "weekly_margin_by_region.yaml"),
        ("base/metrics", "gross_margin_pct.yaml"),
        ("candidate/metrics", "gross_margin_pct.yaml"),
    ],
)
def test_a_run_directory_that_is_listed_and_not_searched_ends_the_run_where_modes_are_kept(
    tmp_path: Path, lock: Lock, where: str, file: str
) -> None:
    """Its files are named by the listing and none of them may be looked at."""
    case = _case(tmp_path)
    directory = case.root / where
    lock(directory, 0o400)

    assert _refused(case) == f"cannot read {directory / file}: PermissionError"


@pytest.mark.parametrize("where", ["assets", "candidate/metrics"])
@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no permission", "searched, not listed"])
def test_a_capture_directory_that_may_not_be_listed_ends_the_capture_where_modes_are_kept(
    tmp_path: Path, lock: Lock, where: str, mode: int
) -> None:
    case = _case(tmp_path)
    directory = case.root / where
    lock(directory, mode)

    assert _capture_refused(case) == f"cannot read {directory}: PermissionError"


@pytest.mark.parametrize("where", ["", "archive"], ids=["itself", "below"])
@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no permission", "searched, not listed"])
def test_a_validated_directory_that_may_not_be_listed_is_reported_where_modes_are_kept(
    tmp_path: Path, lock: Lock, where: str, mode: int
) -> None:
    case = Case(tmp_path)
    directory = case.contracts / where if where else case.contracts
    directory.mkdir(exist_ok=True)
    lock(directory, mode)

    assert _issues([case.contracts]) == [_unlisted(directory)]


def test_validated_files_under_a_directory_that_may_not_be_searched_are_reported(
    tmp_path: Path, lock: Lock
) -> None:
    case = Case(tmp_path)
    product = case.contracts / "weekly_margin.yaml"
    metric = case.contracts / "gross_margin_pct.yaml"
    lock(case.contracts)

    report = validate_paths([metric, product])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [
        _unlooked(metric),
        _unlooked(product),
    ]
    assert report.products == []


def test_a_validated_directory_that_is_listed_and_not_searched_reports_each_file(
    tmp_path: Path, lock: Lock
) -> None:
    """Its files are named by the listing, and each is a contract that cannot be read."""
    case = Case(tmp_path)
    lock(case.contracts, 0o400)

    report = validate_paths([case.contracts])

    assert [(issue.code, issue.source, issue.message) for issue in report.issues] == [
        ("contract.unreadable", str(case.contracts / name), "cannot read contract: PermissionError")
        for name in ("gross_margin_pct.yaml", "weekly_margin.yaml")
    ]
    assert report.products == []


@pytest.mark.parametrize(
    ("given", "refused"),
    [
        ("assets", "assets"),
        ("dashboards", "dashboards"),
        ("results.yaml", "results.yaml"),
        ("base", "base/model.yaml"),
        ("candidate", "candidate/model.yaml"),
    ],
)
def test_a_run_input_under_a_directory_that_may_not_be_searched_ends_the_run(
    tmp_path: Path, lock: Lock, given: str, refused: str
) -> None:
    """The path is where it was said to be, and whether it is there may not be looked at."""
    case = _case(tmp_path / "run")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (case.root / given).rename(blocked / given)
    if given == "assets":
        case.assets = blocked / given
    elif given == "dashboards":
        case.dashboards = blocked / given
    elif given == "results.yaml":
        case.results = blocked / given
    elif given == "base":
        case.base = blocked / given
    else:
        case.candidate = blocked / given
    lock(blocked)

    assert _refused_as_laid_out(case) == f"cannot read {blocked / refused}: PermissionError"


# --- the commands --------------------------------------------------------------------


@pytest.mark.parametrize("where", ["assets", "base/metrics", "candidate/metrics"])
def test_decision_verify_exits_2_with_one_line_for_a_directory_it_cannot_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    case = _case(tmp_path)
    directory = case.root / where
    _deny_listing(monkeypatch, directory)

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: cannot read {directory}: PermissionError\n"
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("where", ["assets", "dashboards", "results.yaml"])
def test_decision_verify_exits_2_with_one_line_for_a_path_it_may_not_look_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    case = _case(tmp_path)
    path = case.root / where
    _deny_look(monkeypatch, path)

    assert main(_verify_argv(case)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: cannot read {path}: PermissionError\n"
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("where", ["assets", "candidate/metrics"])
def test_decision_capture_exits_2_with_one_line_for_a_directory_it_cannot_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    case = _case(tmp_path)
    directory = case.root / where
    output = case.root / "captured.yaml"
    _deny_listing(monkeypatch, directory)
    monkeypatch.setattr(cli, "_result_capture_target", lambda engine: (_never, "env", "reader"))

    argv = ["decision", "capture", str(_assets(case)), "--candidate", str(case.candidate)]
    argv += ["--dwh", "clickhouse", "--output", str(output), "--captured-at", CREATED_AT]
    assert main(argv) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: cannot read {directory}: PermissionError\n"
    assert not output.exists()

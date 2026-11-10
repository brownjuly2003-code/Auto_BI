"""Evidence the filesystem refuses to show is not evidence that is not there.

`decision evidence` re-verifies a bundle somebody was handed and answers whether it is still
the bundle it says it is. A manifest or an artifact that is missing is a finding about the
bundle, and the verdict is `block`. A path this reader may not look at or read is no finding:
the artifact is there or it is not, and the reader was not let know. Read as an absence, a
bundle that is whole was reported as damaged, or — where `pathlib` drops the refusal of a
look, from Python 3.14 on — as no bundle at all; up to 3.13 most of these ended in a
traceback. The tests refuse where the operating system does (`os.stat`, `os.lstat`,
`os.listdir`, the read itself) and hold the answer whatever the interpreter's `pathlib` makes
of such a refusal (`docs/specs/unreadable-inputs.md`).
"""

from __future__ import annotations

import ast
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import auto_bi.evidence.store as store_module
from auto_bi import filesystem
from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.use_cases import VerifyError, locate_evidence
from auto_bi.evidence.inspect import EvidenceReadError, inspect_bundle, read_artifact
from auto_bi.evidence.manifest import MANIFEST_PATH, verify_bundle
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore
from auto_bi.verification.model import EXIT_ERROR, Verdict, exit_code_for
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import CREATED_AT, detail_of, status_of, summary_of
from tests.test_evidence_store import RUN, full_bundle
from tests.test_unlisted_paths import NOT_THERE, NOT_THERE_IDS
from tests.test_unreadable_run_inputs import _case

CHECK = "check-freshness"
CHECK_PATH = "checks/check-freshness.json"
REPORT = "report-summary-md"
REPORT_PATH = "reports/summary.md"

#: What the command adds when the path it may not look into may hold another run.
ASK = "; name the run to read with --run"

#: How a path is refused: the look at it, or the read of what it holds.
HOWS = ["the look", "the read"]
#: The two parts of a bundle a reader opens.
PARTS = ["the manifest", "an artifact"]
#: The three things `decision evidence` prints.
FORMS: dict[str, list[str]] = {
    "as text": [],
    "as JSON": ["--json"],
    "an artifact": ["--artifact", CHECK],
}
#: A refusal by its code, and the class the operating system raises it as.
REFUSED_AS = [
    pytest.param(errno.EACCES, "PermissionError", id="no permission"),
    pytest.param(errno.EIO, "OSError", id="an error of the device"),
]

Lock = Callable[..., None]


@dataclass(frozen=True)
class Laid:
    """One bundle written under an evidence path, and where its parts lie."""

    root: Path
    #: The check as `--artifact` prints it.
    check_text: str

    @property
    def run(self) -> Path:
        return self.root / RUN

    @property
    def manifest(self) -> Path:
        return self.run / MANIFEST_PATH

    @property
    def check(self) -> Path:
        return self.run / "checks" / "check-freshness.json"

    @property
    def report(self) -> Path:
        return self.run / "reports" / "summary.md"

    @property
    def store(self) -> FilesystemArtifactStore:
        return FilesystemArtifactStore(self.root)

    def part(self, key: str) -> Path:
        """A directory or a file of the layout, by the letter the tables below name it with."""
        return {
            "A": self.root.parent,
            "E": self.root,
            "E/manifest": self.root / MANIFEST_PATH,
            "R": self.run,
            "R/manifest": self.manifest,
            "C": self.check.parent,
            "K": self.check,
        }[key]


@pytest.fixture
def laid(tmp_path: Path) -> Laid:
    root = tmp_path / "above" / "evidence"
    full_bundle(root).write()
    return Laid(root, (root / RUN / CHECK_PATH).read_text(encoding="utf-8"))


def _refusal_class() -> type[ArtifactStoreError]:
    """The store's error for what the filesystem refused.

    Asked for by name: the module is collected, and each test fails by itself, where the
    store has no such error.
    """
    refusal: type[ArtifactStoreError] = store_module.ArtifactRefusalError
    return refusal


def _is_link(path: str | os.PathLike[str]) -> bool:
    """`filesystem.is_link`, asked for by name for the same reason."""
    answer: bool = filesystem.is_link(path)
    return answer


def _held(error: ArtifactStoreError) -> OSError:
    """What the operating system raised, as the store's refusal holds it."""
    refusal = getattr(error, "refusal", None)
    assert isinstance(refusal, OSError)
    assert error.__cause__ is refusal
    return refusal


def _denied(path: Any, code: int = errno.EACCES) -> OSError:
    """What the operating system raises for *path*: a `PermissionError` unless told another."""
    return OSError(code, os.strerror(code), os.fspath(path))


def _refuse_look(
    monkeypatch: pytest.MonkeyPatch,
    *denied: Path,
    code: int = errno.EACCES,
    links: bool = True,
    follows: bool = True,
    error: OSError | None = None,
) -> None:
    """Make the operating system refuse a look at each of *denied*, on any platform, as anyone.

    A look that follows a link is `os.stat`, one that does not is `os.lstat`. Both are refused
    unless told otherwise, as both are under a directory that may not be searched.
    """
    refused = {os.fspath(path) for path in denied}
    for name, wanted in (("stat", follows), ("lstat", links)):
        if not wanted:
            continue
        original = getattr(os, name)

        def look(
            path: Any, *args: Any, _original: Callable[..., Any] = original, **kwargs: Any
        ) -> Any:
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) in refused:
                raise error if error is not None else _denied(path, code)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(os, name, look)


def _refuse_read(monkeypatch: pytest.MonkeyPatch, *denied: Path, code: int = errno.EACCES) -> None:
    """Make each of *denied* a file this process may not read, on any platform, as anyone."""
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self in denied:
            raise _denied(self, code)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def _refuse(monkeypatch: pytest.MonkeyPatch, path: Path, how: str) -> None:
    if how == "the look":
        _refuse_look(monkeypatch, path)
    else:
        _refuse_read(monkeypatch, path)


def _refuse_listing(
    monkeypatch: pytest.MonkeyPatch, directory: Path, code: int = errno.EACCES
) -> None:
    """Make *directory* one this process may not list, however the listing is asked for."""
    for name in ("listdir", "scandir"):
        original = getattr(os, name)

        def listing(
            path: Any = ".", *args: Any, _original: Callable[..., Any] = original, **kwargs: Any
        ) -> Any:
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) == os.fspath(directory):
                raise _denied(path, code)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(os, name, listing)


def _kept_out(path: Path, mode: int, *, is_directory: bool) -> bool:
    """Whether what *mode* does not permit at *path* is refused this process."""

    def refused(ask: Callable[[], object]) -> bool:
        try:
            ask()
        except PermissionError:
            return True
        except OSError:
            return False
        return False

    if not is_directory:
        return refused(path.read_bytes)
    listing = bool(mode & 0o400) or refused(lambda: os.listdir(path))
    search = bool(mode & 0o100) or refused(lambda: os.stat(path / "no such entry"))
    return listing and search


@pytest.fixture
def lock() -> Iterator[Lock]:
    """Take permissions of a directory or a file away for as long as the test runs: all of
    them, or all but those named — `0o400` leaves a directory that is listed and may not be
    searched, `0o300` one that is searched and may not be listed.

    The test is skipped where that does not keep this process out: as root, and on a
    filesystem that holds no such permission.
    """
    locked: list[tuple[Path, int]] = []

    def take(path: Path, mode: int = 0) -> None:
        status = os.stat(path)
        locked.append((path, stat.S_IMODE(status.st_mode)))
        path.chmod(mode)
        if not _kept_out(path, mode, is_directory=stat.S_ISDIR(status.st_mode)):
            pytest.skip(f"this process is not kept out of a path of mode {mode:03o}")

    yield take
    for path, mode in reversed(locked):
        path.chmod(mode)


def _link(target: Path, link: Path, *, to_directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=to_directory)
    except (OSError, NotImplementedError):
        pytest.skip("this process may not create a symbolic link")


def _held_by(laid: Laid) -> dict[str, bytes]:
    """The manifest and every artifact of the bundle, by the path the store knows each by."""
    raw = laid.manifest.read_bytes()
    held = {MANIFEST_PATH: raw}
    for artifact in json.loads(raw.decode("utf-8"))["artifacts"]:
        held[artifact["path"]] = laid.run.joinpath(*artifact["path"].split("/")).read_bytes()
    return held


class _Kept:
    """A store of another kind: what it holds, and what it answers for one path."""

    def __init__(self, held: dict[str, bytes], path: str, answer: Exception) -> None:
        self._held = held
        self._path = path
        self._answer = answer

    def put(self, run_id: str, path: str, payload: bytes) -> None:
        raise AssertionError("a reader writes nothing")

    def get(self, run_id: str, path: str) -> bytes:
        if path == self._path:
            raise self._answer
        return self._held[path]

    def exists(self, run_id: str, path: str) -> bool:
        return path in self._held


def _located(path: Path, run_id: str = "") -> str:
    return locate_evidence(path, run_id).run_id


def _not_located(path: Path, run_id: str = "") -> str:
    with pytest.raises(VerifyError) as caught:
        locate_evidence(path, run_id)
    return str(caught.value)


def _evidence(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["decision", "evidence", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _addresses(laid: Laid) -> list[list[str]]:
    """The three ways one run is named: its evidence path, that path and the run, itself."""
    return [[str(laid.root)], [str(laid.root), "--run", RUN], [str(laid.run)]]


def _assert_read(laid: Laid, form: str, answer: tuple[int, str, str]) -> None:
    """*answer* is the bundle read, verified and printed in *form*."""
    code, out, err = answer
    assert (code, err) == (0, "")
    if form == "as text":
        assert out.endswith("verdict: pass (0 problem(s))\n")
    elif form == "as JSON":
        assert json.loads(out)["verdict"] == "pass"
    else:
        assert out == laid.check_text + "\n"


# --- a look at a link ------------------------------------------------------------------


def test_a_link_is_told_from_what_it_leads_to(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"x")
    folder = tmp_path / "folder"
    folder.mkdir()
    _link(file, tmp_path / "to_a_file")
    _link(folder, tmp_path / "to_a_directory", to_directory=True)
    _link(tmp_path / "nowhere", tmp_path / "to_nothing")
    _link(tmp_path / "to_itself", tmp_path / "to_itself")

    assert _is_link(file) is False
    assert _is_link(folder) is False
    assert _is_link(str(file)) is False
    for name in ("to_a_file", "to_a_directory", "to_nothing", "to_itself"):
        assert _is_link(tmp_path / name) is True
        assert _is_link(str(tmp_path / name)) is True
    # What a link leads to is told by the looks that follow it.
    assert filesystem.is_file(tmp_path / "to_a_file")
    assert filesystem.is_dir(tmp_path / "to_a_directory")
    assert not filesystem.exists(tmp_path / "to_nothing")
    assert not filesystem.exists(tmp_path / "to_itself")


@pytest.mark.parametrize("what", ["nothing", "below a file", "a NUL in it"])
def test_what_is_not_there_is_no_link(tmp_path: Path, what: str) -> None:
    (tmp_path / "file").write_bytes(b"x")
    path = {
        "nothing": tmp_path / "nowhere",
        "below a file": tmp_path / "file" / "below",
        "a NUL in it": tmp_path / "a\0b",
    }[what]

    assert _is_link(path) is False


@pytest.mark.parametrize(("code", "raised_as"), REFUSED_AS)
def test_a_refused_look_at_a_link_raises_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, raised_as: str
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"x")
    _refuse_look(monkeypatch, path, code=code, follows=False)

    with pytest.raises(OSError, match="file") as caught:
        _is_link(path)

    assert type(caught.value).__name__ == raised_as
    assert caught.value.errno == code
    assert caught.value.filename == str(path)
    # The look that follows a link was not refused, and is not asked.
    assert filesystem.is_file(path)


@pytest.mark.parametrize("code", NOT_THERE, ids=NOT_THERE_IDS)
def test_each_answer_for_a_path_that_is_not_there_is_no_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    path = tmp_path / "link"
    _link(tmp_path / "nowhere", path)
    assert _is_link(path) is True
    _refuse_look(monkeypatch, path, code=code)

    assert _is_link(path) is False


@pytest.mark.parametrize(
    "winerror", [21, 123, 1921], ids=["not ready", "no such name", "an endless link"]
)
def test_the_answers_of_windows_for_a_path_that_is_not_there_are_no_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    """Told by the code Windows gives beside `errno` — given here on whatever runs the test."""
    path = tmp_path / "link"
    _link(tmp_path / "nowhere", path)
    error = OSError(errno.EINVAL, "refused", str(path))
    error.winerror = winerror
    _refuse_look(monkeypatch, path, error=error)

    assert _is_link(path) is False

    error.winerror = 5  # access is denied
    with pytest.raises(OSError, match="refused"):
        _is_link(path)


@pytest.mark.parametrize("mode", [0o000, 0o400], ids=["no permission", "listed, not searched"])
def test_a_look_at_a_link_under_a_directory_that_may_not_be_searched_raises_where_modes_are_kept(
    tmp_path: Path, lock: Lock, mode: int
) -> None:
    folder = tmp_path / "kept"
    folder.mkdir()
    (folder / "file").write_bytes(b"x")
    lock(folder, mode)

    for path in (folder / "file", folder / "no"):
        with pytest.raises(PermissionError) as caught:
            _is_link(path)
        assert caught.value.filename == str(path)
    # The directory itself is looked at through the one above it.
    assert _is_link(folder) is False


# --- the store ---------------------------------------------------------------------------


@pytest.mark.parametrize("look", ["that follows no link", "that follows a link"])
def test_a_look_the_filesystem_refuses_is_a_refusal_of_the_store(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, look: str
) -> None:
    _refuse_look(
        monkeypatch,
        laid.check,
        links=look == "that follows no link",
        follows=look == "that follows a link",
    )
    where = f"{CHECK_PATH!r} of run {RUN!r}"

    with pytest.raises(_refusal_class()) as looked:
        laid.store.exists(RUN, CHECK_PATH)
    assert str(looked.value) == f"cannot look for {where}: PermissionError"
    assert isinstance(_held(looked.value), PermissionError)
    assert _held(looked.value).filename == str(laid.check)

    with pytest.raises(_refusal_class()) as read:
        laid.store.get(RUN, CHECK_PATH)
    assert str(read.value) == f"cannot read {where}: PermissionError"
    assert isinstance(_held(read.value), PermissionError)
    assert _held(read.value).filename == str(laid.check)

    # What lies beside it is looked at and read as before.
    assert laid.store.exists(RUN, MANIFEST_PATH) is True
    assert laid.store.get(RUN, REPORT_PATH) == laid.report.read_bytes()


def test_a_refused_look_at_a_directory_on_the_way_is_a_refusal_of_the_store(
    laid: Laid, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_look(monkeypatch, laid.check.parent)

    for ask in (laid.store.exists, laid.store.get):
        with pytest.raises(_refusal_class()) as caught:
            ask(RUN, CHECK_PATH)
        assert _held(caught.value).filename == str(laid.check.parent)
    with pytest.raises(_refusal_class()) as wrote:
        laid.store.put(RUN, "checks/another.json", b"{}")
    assert str(wrote.value) == f"cannot write 'checks/another.json' of run {RUN!r}: PermissionError"
    assert _held(wrote.value).filename == str(laid.check.parent)
    assert not (laid.check.parent / "another.json").exists()


@pytest.mark.parametrize(("code", "raised_as"), REFUSED_AS)
def test_a_read_the_filesystem_refuses_is_a_refusal_of_the_store(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, code: int, raised_as: str
) -> None:
    _refuse_read(monkeypatch, laid.check, code=code)

    assert laid.store.exists(RUN, CHECK_PATH) is True
    with pytest.raises(_refusal_class()) as caught:
        laid.store.get(RUN, CHECK_PATH)

    assert str(caught.value) == f"cannot read {CHECK_PATH!r} of run {RUN!r}: {raised_as}"
    assert _held(caught.value).errno == code
    assert _held(caught.value).filename == str(laid.check)


def test_a_write_the_filesystem_refuses_is_a_refusal_of_the_store(laid: Laid) -> None:
    below = f"{CHECK_PATH}/below.json"  # a file lies where a directory would have to be

    with pytest.raises(_refusal_class()) as caught:
        laid.store.put(RUN, below, b"{}")

    said = str(caught.value)
    assert said.startswith(f"cannot write {below!r} of run {RUN!r}: ")
    assert said.endswith(f": {type(_held(caught.value)).__name__}")
    assert laid.check.read_text(encoding="utf-8") == laid.check_text


def test_an_artifact_that_went_away_between_the_look_and_the_read_is_missing(
    laid: Laid, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_read(monkeypatch, laid.check, code=errno.ENOENT)

    with pytest.raises(ArtifactStoreError) as caught:
        laid.store.get(RUN, CHECK_PATH)

    assert type(caught.value) is ArtifactStoreError
    assert str(caught.value) == f"artifact {CHECK_PATH!r} of run {RUN!r} is missing"
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("what", ["nothing", "a directory", "a pipe"])
def test_a_path_that_holds_no_regular_file_holds_no_artifact(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    laid.check.unlink()
    if what == "a directory":
        laid.check.mkdir()
    elif what == "a pipe":
        if not hasattr(os, "mkfifo"):
            pytest.skip("this platform lays no named pipe in a directory")
        os.mkfifo(laid.check)
    opened: list[Path] = []
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == laid.check:  # a pipe nobody writes to would never answer
            opened.append(self)
            raise _denied(self, errno.EIO)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert laid.store.exists(RUN, CHECK_PATH) is False
    with pytest.raises(ArtifactStoreError) as caught:
        laid.store.get(RUN, CHECK_PATH)

    assert type(caught.value) is ArtifactStoreError
    assert str(caught.value) == f"artifact {CHECK_PATH!r} of run {RUN!r} is missing"
    # What the path holds is told by the look: nothing is opened to find that out.
    assert opened == []


@pytest.mark.parametrize("where", ["the artifact", "a directory on the way"])
@pytest.mark.parametrize("leads_to", ["what is there", "nothing"])
def test_a_link_in_the_run_is_the_answer_of_the_store_itself(
    laid: Laid, tmp_path: Path, where: str, leads_to: str
) -> None:
    kept = tmp_path / "kept"
    shutil.copytree(laid.check.parent, kept)
    target = kept if leads_to == "what is there" else tmp_path / "nowhere"
    if where == "the artifact":
        laid.check.unlink()
        _link(target / "check-freshness.json", laid.check)
        said = f"artifact path is a symlink: {laid.check}"
    else:
        shutil.rmtree(laid.check.parent)
        _link(target, laid.check.parent, to_directory=True)
        said = f"artifact path crosses a symlink: {laid.check.parent}"

    assert laid.store.exists(RUN, CHECK_PATH) is False
    with pytest.raises(ArtifactStoreError) as read:
        laid.store.get(RUN, CHECK_PATH)
    with pytest.raises(ArtifactStoreError) as wrote:
        laid.store.put(RUN, CHECK_PATH, b"{}")

    for caught in (read, wrote):
        assert type(caught.value) is ArtifactStoreError
        assert str(caught.value) == said
    assert sorted(path.name for path in kept.iterdir()) == ["check-freshness.json"]
    assert (kept / "check-freshness.json").read_text(encoding="utf-8") == laid.check_text


@pytest.mark.parametrize(
    ("run_id", "path"),
    [
        ("Run_1", "a.json"),
        ("../elsewhere", "a.json"),
        (RUN, "/etc/passwd"),
        (RUN, "../a.json"),
        (RUN, "a//b.json"),
        (RUN, ""),
    ],
)
def test_what_the_store_refuses_by_its_own_rule_is_no_refusal_of_the_filesystem(
    tmp_path: Path, run_id: str, path: str
) -> None:
    store = FilesystemArtifactStore(tmp_path)

    assert store.exists(run_id, path) is False
    with pytest.raises(ArtifactStoreError) as read:
        store.get(run_id, path)
    with pytest.raises(ArtifactStoreError) as wrote:
        store.put(run_id, path, b"{}")

    assert type(read.value) is ArtifactStoreError
    assert type(wrote.value) is ArtifactStoreError
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", [0o000, 0o400], ids=["no permission", "listed, not searched"])
@pytest.mark.parametrize("locked", ["R", "C"], ids=["the run directory", "the directory of it"])
def test_the_store_keeps_the_refusal_of_a_directory_where_modes_are_kept(
    laid: Laid, lock: Lock, locked: str, mode: int
) -> None:
    lock(laid.part(locked), mode)
    refused = laid.check.parent if locked == "R" else laid.check

    for ask in (laid.store.exists, laid.store.get):
        with pytest.raises(_refusal_class()) as caught:
            ask(RUN, CHECK_PATH)
        assert isinstance(_held(caught.value), PermissionError)
        assert _held(caught.value).filename == str(refused)


def test_the_store_keeps_the_refusal_of_a_file_where_modes_are_kept(laid: Laid, lock: Lock) -> None:
    lock(laid.check)

    assert laid.store.exists(RUN, CHECK_PATH) is True
    with pytest.raises(_refusal_class()) as caught:
        laid.store.get(RUN, CHECK_PATH)

    assert str(caught.value) == f"cannot read {CHECK_PATH!r} of run {RUN!r}: PermissionError"
    assert _held(caught.value).filename == str(laid.check)


# --- the re-check of a bundle ------------------------------------------------------------


@pytest.mark.parametrize("how", HOWS)
def test_a_manifest_that_may_not_be_read_is_not_graded_as_missing(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    _refuse(monkeypatch, laid.manifest, how)

    with pytest.raises(_refusal_class()) as caught:
        verify_bundle(laid.store, RUN)

    assert _held(caught.value).filename == str(laid.manifest)


@pytest.mark.parametrize("how", HOWS)
@pytest.mark.parametrize("in_hand", [False, True], ids=["manifest read", "manifest in hand"])
def test_an_artifact_that_may_not_be_read_is_not_graded_as_missing(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, how: str, in_hand: bool
) -> None:
    raw = laid.manifest.read_bytes() if in_hand else None
    _refuse(monkeypatch, laid.check, how)

    with pytest.raises(_refusal_class()) as caught:
        verify_bundle(laid.store, RUN, raw=raw)

    assert _held(caught.value).filename == str(laid.check)


@pytest.mark.parametrize("what", ["nothing", "a directory"])
def test_a_manifest_and_an_artifact_that_are_not_there_are_graded_as_before(
    laid: Laid, what: str
) -> None:
    laid.check.unlink()
    if what == "a directory":
        laid.check.mkdir()

    problems = verify_bundle(laid.store, RUN)
    assert [(problem.code, problem.detail) for problem in problems] == [
        ("evidence.artifact_missing", f"{CHECK}: {CHECK_PATH} is not in the store")
    ]

    laid.manifest.unlink()
    if what == "a directory":
        laid.manifest.mkdir()

    assert [problem.code for problem in verify_bundle(laid.store, RUN)] == [
        "evidence.manifest_missing"
    ]


def test_a_store_of_another_kind_is_graded_by_what_it_raises(laid: Laid) -> None:
    held = _held_by(laid)

    gone = _Kept(held, CHECK_PATH, ArtifactStoreError("gone"))
    assert [problem.code for problem in verify_bundle(gone, RUN)] == ["evidence.artifact_missing"]
    no_manifest = _Kept(held, MANIFEST_PATH, ArtifactStoreError("gone"))
    assert [problem.code for problem in verify_bundle(no_manifest, RUN)] == [
        "evidence.manifest_missing"
    ]

    for path in (CHECK_PATH, MANIFEST_PATH):
        answer = _refusal_class()("the store was refused", _denied(path, errno.EIO))
        with pytest.raises(_refusal_class()) as caught:
            verify_bundle(_Kept(held, path, answer), RUN)
        assert caught.value is answer


# --- the reader of a bundle ---------------------------------------------------------------


@pytest.mark.parametrize("how", HOWS)
@pytest.mark.parametrize("part", PARTS)
def test_an_inspection_of_a_bundle_that_may_not_be_read_names_the_path(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, part: str, how: str
) -> None:
    path = laid.manifest if part == "the manifest" else laid.check
    _refuse(monkeypatch, path, how)

    with pytest.raises(EvidenceReadError) as caught:
        inspect_bundle(laid.store, RUN)

    assert str(caught.value) == f"cannot read {path}: PermissionError"
    assert isinstance(caught.value.__cause__, _refusal_class())


@pytest.mark.parametrize("line_break", ["\n", "\r"], ids=["LF", "CR"])
def test_an_inspection_names_a_refused_path_with_a_line_break_in_one_line(
    laid: Laid, line_break: str
) -> None:
    name = f"{laid.check}{line_break}suffix"
    answer = _refusal_class()("the store was refused", _denied(name))

    with pytest.raises(EvidenceReadError) as caught:
        inspect_bundle(_Kept(_held_by(laid), CHECK_PATH, answer), RUN)

    assert str(caught.value) == f"cannot read {name!r}: PermissionError"
    assert len(str(caught.value).splitlines()) == 1


@pytest.mark.parametrize("how", HOWS)
@pytest.mark.parametrize("part", PARTS)
def test_an_artifact_is_not_read_out_of_a_bundle_that_may_not_be_read(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, part: str, how: str
) -> None:
    path = laid.manifest if part == "the manifest" else laid.check
    _refuse(monkeypatch, path, how)

    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(laid.store, RUN, CHECK)

    assert str(caught.value) == f"cannot read {path}: PermissionError"
    assert isinstance(caught.value.__cause__, _refusal_class())


@pytest.mark.parametrize("how", HOWS)
def test_an_artifact_is_read_past_another_that_may_not_be(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    expected = laid.report.read_bytes()
    _refuse(monkeypatch, laid.check, how)

    artifact, payload = read_artifact(laid.store, RUN, REPORT)

    assert artifact.path == REPORT_PATH
    assert payload == expected


def test_a_refusal_that_names_no_path_is_said_as_the_store_says_it(laid: Laid) -> None:
    held = _held_by(laid)
    said = f"cannot read {CHECK_PATH!r} of run {RUN!r}: OSError"
    answer = _refusal_class()(said, OSError("the device gave no name"))

    with pytest.raises(EvidenceReadError) as inspected:
        inspect_bundle(_Kept(held, CHECK_PATH, answer), RUN)
    with pytest.raises(EvidenceReadError) as read:
        read_artifact(_Kept(held, CHECK_PATH, answer), RUN, CHECK)
    with pytest.raises(EvidenceReadError) as opened:
        inspect_bundle(_Kept(held, MANIFEST_PATH, answer), RUN)
    with pytest.raises(EvidenceReadError) as opened_for_one:
        read_artifact(_Kept(held, MANIFEST_PATH, answer), RUN, CHECK)

    for caught in (inspected, read, opened, opened_for_one):
        assert str(caught.value) == said
        assert caught.value.__cause__ is answer


@pytest.mark.parametrize("what", ["nothing", "a directory"])
def test_the_reader_grades_what_is_not_there_as_before(laid: Laid, what: str) -> None:
    laid.check.unlink()
    if what == "a directory":
        laid.check.mkdir()

    inspection = inspect_bundle(laid.store, RUN)
    assert inspection.verdict is Verdict.BLOCK
    assert [problem.code for problem in inspection.problems] == ["evidence.artifact_missing"]
    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(laid.store, RUN, CHECK)
    assert str(caught.value) == f"{CHECK}: {CHECK_PATH} is not in the store"

    laid.manifest.unlink()
    with pytest.raises(EvidenceReadError) as no_bundle:
        inspect_bundle(laid.store, RUN)
    assert str(no_bundle.value) == (
        f"run {RUN!r} has no {MANIFEST_PATH}: this is not an evidence bundle"
    )


# --- where a bundle lies ------------------------------------------------------------------


def test_an_evidence_path_that_may_not_be_looked_into_is_not_one_that_holds_no_bundle(
    laid: Laid, monkeypatch: pytest.MonkeyPatch
) -> None:
    under_the_path = laid.root / MANIFEST_PATH
    _refuse_look(monkeypatch, under_the_path, laid.manifest)

    assert _not_located(laid.root) == f"cannot read {under_the_path}: PermissionError"
    assert _not_located(laid.root, RUN) == f"cannot read {under_the_path}: PermissionError"
    assert _not_located(laid.run) == f"cannot read {laid.manifest}: PermissionError"
    assert _not_located(laid.run, RUN) == f"cannot read {laid.manifest}: PermissionError"


@pytest.mark.parametrize("line_break", ["\n", "\r"], ids=["LF", "CR"])
def test_a_refused_look_names_a_path_with_a_line_break_in_one_line(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, line_break: str
) -> None:
    root = laid.root / f"name{line_break}suffix"
    manifest = root / MANIFEST_PATH
    _refuse_look(monkeypatch, manifest)

    said = _not_located(root)

    assert said == f"cannot read {str(manifest)!r}: PermissionError"
    assert len(said.splitlines()) == 1


def test_an_evidence_path_that_may_not_be_looked_at_is_not_one_that_is_not_there(
    laid: Laid, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_look(monkeypatch, laid.root)

    assert _not_located(laid.root) == f"cannot read {laid.root}: PermissionError"
    assert _not_located(laid.root, RUN) == f"cannot read {laid.root}: PermissionError"
    # The run directory is told by its own manifest.
    assert _located(laid.run) == RUN


@pytest.mark.parametrize(("code", "raised_as"), REFUSED_AS)
def test_an_evidence_path_that_may_not_be_listed_is_not_one_that_holds_no_run(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, code: int, raised_as: str
) -> None:
    _refuse_listing(monkeypatch, laid.root, code=code)

    assert _not_located(laid.root) == f"cannot read {laid.root}: {raised_as}"
    # A run that is named is looked for by its name: the listing is not asked for.
    assert _located(laid.root, RUN) == RUN
    assert _located(laid.run) == RUN


@pytest.mark.parametrize(
    "name", ["aaa", "zzz"], ids=["listed before the run", "listed after the run"]
)
def test_a_directory_beside_the_run_that_may_not_be_looked_into_is_not_passed_over(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    beside = laid.root / name
    beside.mkdir()
    _refuse_look(monkeypatch, beside / MANIFEST_PATH)

    assert _not_located(laid.root) == (
        f"cannot read {beside / MANIFEST_PATH}: PermissionError{ASK}"
    )
    # Naming the run takes the question away: that directory is not looked into.
    assert _located(laid.root, RUN) == RUN
    assert _located(laid.run) == RUN


@pytest.mark.parametrize("line_break", ["\n", "\r"], ids=["LF", "CR"])
def test_a_refused_directory_name_with_a_line_break_is_reported_in_one_line(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, line_break: str
) -> None:
    name = f"aaa{line_break}suffix"
    manifest = laid.root / name / MANIFEST_PATH
    _refuse_look(monkeypatch, manifest)
    monkeypatch.setattr(os, "listdir", lambda path: [name, RUN])

    said = _not_located(laid.root)

    assert said == f"cannot read {str(manifest)!r}: PermissionError{ASK}"
    assert len(said.splitlines()) == 1


def test_the_run_that_was_named_is_not_asked_for_again(
    laid: Laid, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_look(monkeypatch, laid.manifest)
    said = f"cannot read {laid.manifest}: PermissionError"

    assert _not_located(laid.root) == said + ASK
    assert _not_located(laid.root, RUN) == said
    assert _not_located(laid.run) == said


@pytest.mark.parametrize("code", NOT_THERE, ids=NOT_THERE_IDS)
def test_each_answer_for_a_manifest_that_is_not_there_passes_a_directory_over(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    beside = laid.root / "aaa"
    beside.mkdir()
    _refuse_look(monkeypatch, beside / MANIFEST_PATH, code=code)

    assert _located(laid.root) == RUN


def test_what_is_not_there_is_said_of_an_evidence_path_as_before(
    laid: Laid, tmp_path: Path
) -> None:
    nowhere = tmp_path / "nowhere"
    empty = tmp_path / "empty"
    empty.mkdir()

    assert _not_located(nowhere) == f"evidence path not found: {nowhere}"
    assert _not_located(laid.manifest) == f"evidence path not found: {laid.manifest}"
    assert _not_located(empty) == (
        f"no evidence bundle under {empty}: no run directory holds {MANIFEST_PATH}"
    )
    assert _not_located(laid.root, "run-other") == (
        f"run 'run-other' under {laid.root} has no {MANIFEST_PATH}"
    )
    assert _not_located(laid.run, "run-other") == f"{laid.run} is run {RUN!r}, not 'run-other'"


def test_what_lies_beside_the_run_and_holds_no_manifest_is_passed_over_as_before(
    laid: Laid,
) -> None:
    (laid.root / "notes.txt").write_text("kept\n", encoding="utf-8")
    (laid.root / "empty").mkdir()

    assert _located(laid.root) == RUN

    shutil.copytree(laid.run, laid.root / "run-other")
    assert _not_located(laid.root) == (
        f"{laid.root} holds 2 runs ({RUN}, run-other); name the one to read with --run"
    )
    assert _located(laid.root, "run-other") == "run-other"


# --- decision evidence, as it is run -------------------------------------------------------


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("how", HOWS)
@pytest.mark.parametrize("part", PARTS)
def test_decision_evidence_exits_2_and_names_the_path_it_may_not_read(
    laid: Laid,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    part: str,
    how: str,
    form: str,
) -> None:
    path = laid.manifest if part == "the manifest" else laid.check
    _refuse(monkeypatch, path, how)
    # A manifest that may not be looked at is met while the runs are told apart, unless the
    # run was named.
    asked = [part == "the manifest" and how == "the look", False, False]

    for address, asks in zip(_addresses(laid), asked, strict=True):
        code, out, err = _evidence(capsys, *address, *FORMS[form])

        assert code == EXIT_ERROR
        assert out == ""
        assert err == f"error: cannot read {path}: PermissionError{ASK if asks else ''}\n"


@pytest.mark.parametrize("form", list(FORMS))
def test_decision_evidence_exits_2_on_an_evidence_path_it_may_not_list(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], form: str
) -> None:
    _refuse_listing(monkeypatch, laid.root)
    by_the_path, named, itself = _addresses(laid)

    code, out, err = _evidence(capsys, *by_the_path, *FORMS[form])

    assert (code, out) == (EXIT_ERROR, "")
    assert err == f"error: cannot read {laid.root}: PermissionError\n"
    # The run that is named, and the run directory itself, are read without the listing.
    _assert_read(laid, form, _evidence(capsys, *named, *FORMS[form]))
    _assert_read(laid, form, _evidence(capsys, *itself, *FORMS[form]))


@pytest.mark.parametrize("form", list(FORMS))
def test_decision_evidence_asks_for_the_run_beside_a_directory_it_may_not_look_into(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], form: str
) -> None:
    beside = laid.root / "lost+found"
    beside.mkdir()
    _refuse_look(monkeypatch, beside / MANIFEST_PATH)
    by_the_path, named, itself = _addresses(laid)

    code, out, err = _evidence(capsys, *by_the_path, *FORMS[form])

    assert (code, out) == (EXIT_ERROR, "")
    assert err == f"error: cannot read {beside / MANIFEST_PATH}: PermissionError{ASK}\n"
    _assert_read(laid, form, _evidence(capsys, *named, *FORMS[form]))
    _assert_read(laid, form, _evidence(capsys, *itself, *FORMS[form]))


@pytest.mark.parametrize("how", HOWS)
def test_decision_evidence_prints_an_artifact_past_another_it_may_not_read(
    laid: Laid, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], how: str
) -> None:
    report = laid.report.read_text(encoding="utf-8")
    _refuse(monkeypatch, laid.check, how)

    for address in _addresses(laid):
        code, out, err = _evidence(capsys, *address, "--artifact", REPORT)

        assert (code, err) == (0, "")
        assert out == report + "\n"


@pytest.mark.parametrize("what", ["nothing", "a directory"])
def test_decision_evidence_grades_an_artifact_that_is_not_there_as_before(
    laid: Laid, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    laid.check.unlink()
    if what == "a directory":
        laid.check.mkdir()

    for address in _addresses(laid):
        code, out, err = _evidence(capsys, *address)
        assert (code, err) == (exit_code_for(Verdict.BLOCK), "")
        assert f"evidence.artifact_missing    {CHECK}: {CHECK_PATH} is not in the store\n" in out
        assert out.endswith("verdict: block (1 problem(s))\n")

        code, out, err = _evidence(capsys, *address, "--json")
        assert (code, err) == (exit_code_for(Verdict.BLOCK), "")
        assert [problem["code"] for problem in json.loads(out)["problems"]] == [
            "evidence.artifact_missing"
        ]

        code, out, err = _evidence(capsys, *address, "--artifact", CHECK)
        assert (code, out) == (EXIT_ERROR, "")
        assert err == f"error: {CHECK}: {CHECK_PATH} is not in the store\n"


#: What `decision evidence` ends with when permissions are taken from one directory or file
#: of a bundle: `A` the directory above the evidence path, `E` that path, `R` the run
#: directory, `C` the directory of the checks, `K` one check in it. Mode `0o400` leaves a
#: directory listed and not searched, `0o300` searched and not listed. The three answers are
#: for the three ways the run is named — its evidence path, that path and the run, the run
#: directory itself: the path the command says it cannot read, `+ask` where it asks for the
#: run, `None` where the bundle is read and verified.
KEPT_OUT: list[tuple[str, int, tuple[str | None, str | None, str | None]]] = [
    ("A", 0o000, ("E/manifest", "E/manifest", "R/manifest")),
    ("A", 0o400, ("E/manifest", "E/manifest", "R/manifest")),
    ("A", 0o300, (None, None, None)),
    ("E", 0o000, ("E/manifest", "E/manifest", "R/manifest")),
    ("E", 0o400, ("E/manifest", "E/manifest", "R/manifest")),
    ("E", 0o300, ("E", None, None)),
    ("R", 0o000, ("R/manifest+ask", "R/manifest", "R/manifest")),
    ("R", 0o400, ("R/manifest+ask", "R/manifest", "R/manifest")),
    ("R", 0o300, (None, None, None)),
    ("C", 0o000, ("K", "K", "K")),
    ("C", 0o400, ("K", "K", "K")),
    ("C", 0o300, (None, None, None)),
    ("R/manifest", 0o000, ("R/manifest", "R/manifest", "R/manifest")),
    ("K", 0o000, ("K", "K", "K")),
]


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize(
    ("locked", "mode", "answers"), KEPT_OUT, ids=[f"{row[0]} {row[1]:03o}" for row in KEPT_OUT]
)
def test_decision_evidence_keeps_the_refusal_where_modes_are_kept(
    laid: Laid,
    lock: Lock,
    capsys: pytest.CaptureFixture[str],
    locked: str,
    mode: int,
    answers: tuple[str | None, str | None, str | None],
    form: str,
) -> None:
    lock(laid.part(locked), mode)

    for address, answer in zip(_addresses(laid), answers, strict=True):
        said = _evidence(capsys, *address, *FORMS[form])

        if answer is None:
            _assert_read(laid, form, said)
            continue
        key, _, asks = answer.partition("+")
        code, out, err = said
        assert code == EXIT_ERROR
        assert out == ""
        assert err == f"error: cannot read {laid.part(key)}: PermissionError{ASK if asks else ''}\n"


# --- the gate, which reads back what it wrote ---------------------------------------------


def _read_back_refused(
    monkeypatch: pytest.MonkeyPatch, evidence: Path, picked: Callable[[Path], bool]
) -> None:
    """Refuse this process the read of what *picked* names of what it writes under *evidence*."""
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if evidence in self.parents and picked(self):
            raise _denied(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def _could_not_read_back(evidence: Path, named: str) -> str:
    """The line the gate ends with for a bundle under *evidence* it wrote and could not read."""
    return (
        rf"error: cannot read back the evidence bundle under {re.escape(str(evidence))}: "
        rf"cannot read {named} of run 'run-[0-9a-f]+': PermissionError\n"
    )


@pytest.mark.parametrize("part", PARTS)
def test_a_bundle_the_gate_wrote_and_may_not_read_back_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], part: str
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"

    def picked(path: Path) -> bool:
        if part == "the manifest":
            return path.name == MANIFEST_PATH
        return path.parent.name == "checks"

    with monkeypatch.context() as patched:
        _read_back_refused(patched, evidence, picked)
        code = main(_verify_argv(case))
    captured = capsys.readouterr()

    assert code == EXIT_ERROR
    assert captured.out == ""
    named = "'manifest.json'" if part == "the manifest" else r"'checks/[\w.-]+\.json'"
    assert re.fullmatch(_could_not_read_back(evidence, named), captured.err)
    # Nothing was graded, and what was written is whole: a reader that is let reads a bundle
    # that passes.
    location = locate_evidence(evidence)
    assert inspect_bundle(location.store, location.run_id).verdict is Verdict.PASS


def test_the_gate_raises_what_it_was_refused_and_grades_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    raised: list[BaseException] = []
    run_gate = use_cases.run_gate

    def watched(*args: Any, **kwargs: Any) -> Any:
        try:
            return run_gate(*args, **kwargs)
        except Exception as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(use_cases, "run_gate", watched)

    with monkeypatch.context() as patched:
        _read_back_refused(patched, evidence, lambda path: path.name == MANIFEST_PATH)
        with pytest.raises(VerifyError) as caught:
            case.run()

    run_id = _located(evidence)
    said = f"cannot read 'manifest.json' of run {run_id!r}: PermissionError"
    assert str(caught.value) == f"cannot read back the evidence bundle under {evidence}: {said}"
    (error,) = raised
    assert type(error).__name__ == "EvidenceReadBackError"
    assert not isinstance(error, ArtifactStoreError)
    assert str(error) == said
    assert isinstance(error.__cause__, _refusal_class())
    assert _held(error.__cause__).filename == str(evidence / run_id / MANIFEST_PATH)


@pytest.mark.parametrize("line_break", ["\n", "\r"], ids=["LF", "CR"])
def test_the_gate_reports_a_readback_refusal_under_a_line_break_path_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line_break: str
) -> None:
    case = _case(tmp_path)
    evidence = case.root / f"evidence{line_break}suffix"
    request = use_cases.VerifyRequest(
        contract_paths=[case.contracts],
        base=case.base,
        candidate=case.candidate,
        evidence_dir=evidence,
        created_at=CREATED_AT,
        dashboards_dir=case.dashboards,
        assets_dir=case.assets,
        results_file=case.results,
    )

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise use_cases.EvidenceReadBackError("read refused")

    monkeypatch.setattr(use_cases, "run_gate", refused)

    with pytest.raises(VerifyError) as caught:
        use_cases.verify(request)

    assert str(caught.value) == (
        f"cannot read back the evidence bundle under {str(evidence)!r}: read refused"
    )
    assert len(str(caught.value).splitlines()) == 1


def test_a_bundle_that_is_read_back_is_reported_as_before(tmp_path: Path) -> None:
    report = _case(tmp_path).run()

    assert status_of(report, "evidence.bundle") is CheckStatus.PASS
    assert summary_of(report, "evidence.bundle") == "evidence.verified"
    assert detail_of(report, "evidence.bundle").endswith(" artifact(s), 0 problem(s)")


def test_an_artifact_that_is_gone_when_the_bundle_is_read_back_is_graded_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    put = FilesystemArtifactStore.put

    def put_and_lose(self: FilesystemArtifactStore, run_id: str, path: str, payload: bytes) -> None:
        put(self, run_id, path, payload)
        if path == MANIFEST_PATH:  # the last write of a bundle: one check is lost after it
            min((evidence / run_id / "checks").iterdir()).unlink()

    monkeypatch.setattr(FilesystemArtifactStore, "put", put_and_lose)

    report = case.run()

    assert status_of(report, "evidence.bundle") is CheckStatus.BLOCK
    assert summary_of(report, "evidence.bundle") == "evidence.invalid"
    assert detail_of(report, "evidence.bundle").endswith(" artifact(s), 1 problem(s)")
    assert report.verdict is Verdict.BLOCK


def test_a_run_over_a_manifest_it_may_not_read_names_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case(tmp_path)
    evidence = case.root / "evidence"
    assert main(_verify_argv(case)) == 0
    capsys.readouterr()
    run_id = _located(evidence)
    manifest = evidence / run_id / MANIFEST_PATH
    written = manifest.read_bytes()

    with monkeypatch.context() as patched:
        _refuse_read(patched, manifest)
        code = main(_verify_argv(case))
    captured = capsys.readouterr()

    assert code == EXIT_ERROR
    assert captured.out == ""
    assert captured.err == (
        f"error: cannot write the evidence bundle under {evidence}: "
        f"cannot read 'manifest.json' of run {run_id!r}: PermissionError\n"
    )
    assert manifest.read_bytes() == written


def _open_up(tree: Path) -> None:
    """Give this process back what a umask took from what was written under *tree*."""
    pending = [tree]
    while pending:
        path = pending.pop()
        status = os.lstat(path)
        if stat.S_ISDIR(status.st_mode):
            os.chmod(path, stat.S_IMODE(status.st_mode) | 0o700)
            pending.extend(path / name for name in os.listdir(path))
        elif stat.S_ISREG(status.st_mode):
            os.chmod(path, stat.S_IMODE(status.st_mode) | 0o600)


def test_a_bundle_written_unreadable_ends_the_run_where_modes_are_kept(tmp_path: Path) -> None:
    """The command as a process, under a umask that takes the read of what it writes."""
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("this process reads a file whose mode gives it no read")
    case = _case(tmp_path)
    evidence = case.root / "evidence"

    try:
        ran = subprocess.run(
            [sys.executable, "-B", "-m", "auto_bi.cli", *_verify_argv(case)],
            capture_output=True,
            encoding="utf-8",
            check=False,
            umask=0o400,
        )
    finally:
        if evidence.exists():
            _open_up(evidence)

    assert ran.returncode == EXIT_ERROR
    assert ran.stdout == ""
    assert re.fullmatch(_could_not_read_back(evidence, "'manifest.json'"), ran.stderr)
    location = locate_evidence(evidence)
    assert inspect_bundle(location.store, location.run_id).verdict is Verdict.PASS


# --- how the readers look ----------------------------------------------------------------

#: The looks and listings of `pathlib` and `os.path` that drop a refusal on some interpreter.
DROPPING = {
    "exists",
    "is_file",
    "is_dir",
    "is_symlink",
    "is_mount",
    "is_junction",
    "iterdir",
    "glob",
    "rglob",
    "walk",
    "isfile",
    "isdir",
    "islink",
    "lexists",
}


@pytest.mark.parametrize("module", [store_module, use_cases], ids=["the store", "the use cases"])
def test_the_readers_of_evidence_look_through_the_functions_that_keep_a_refusal(
    module: Any,
) -> None:
    """A look of `pathlib` reads alike on the interpreters up to 3.13 and is another answer
    from 3.14 on: such a look is found here on whichever of them runs the test.

    Named, not only called: a look that is handed on as a function, or imported by its name,
    is asked the same question in the end.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

    named: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            ours = isinstance(node.value, ast.Name) and node.value.id == "filesystem"
            name = "" if ours else node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.alias):
            name = node.name.rpartition(".")[2]
        else:
            continue
        if name in DROPPING:
            named.append((node.lineno, name))

    assert named == []

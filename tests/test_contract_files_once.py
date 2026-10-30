"""A contract file is read once, however many of the validated paths reach it.

Durable spec: docs/specs/contract-files-once.md. `decision validate` and `decision verify` read
every file under each path they are given, and a file reached through two of them — a directory
and a file in it, one directory twice, one file spelled two ways — was read twice: the second
reading was `contract.duplicate_id`, "defined in more than one file", and a contract set that
holds each id once blocked. A file is now told by the filesystem's identity of it.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from auto_bi import filesystem
from auto_bi.cli import main
from auto_bi.decision.use_cases import VerifyRequest, verify
from auto_bi.decision.validate import load_contracts, validate_paths
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import Severity, Verdict
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateReport,
)
from tests.test_decision_cli import _verify_argv
from tests.test_decision_validate import codes, write_contracts
from tests.test_decision_verify import CREATED_AT, Case, status_of, summary_of

METRIC = "metric://sales/gross_margin_pct@3"
PRODUCT = "decision://sales/weekly_margin@1"
DEFINED_TWICE = "the same contract id is defined in more than one file"


def _contracts(tmp_path: Path, **edits: Any) -> Path:
    root = tmp_path / "contracts"
    root.mkdir()
    return write_contracts(root, **edits)


def _assert_read_once(paths: list[Path]) -> None:
    report = validate_paths(paths)
    assert report.issues == []
    assert report.verdict is Verdict.PASS
    assert report.products == [PRODUCT]
    assert report.metrics == [METRIC]


def _link(make: Any, target: Path, link: Path) -> None:
    try:
        make(target, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this filesystem makes no such link: {error}")


# --- one file, many paths --------------------------------------------------------------------


@pytest.mark.parametrize("file_first", [False, True], ids=["directory-first", "file-first"])
def test_a_directory_and_a_file_in_it_are_read_once(tmp_path: Path, file_first: bool) -> None:
    root = _contracts(tmp_path)
    paths = [root, root / "gross_margin_pct.yaml"]
    _assert_read_once(paths[::-1] if file_first else paths)


def test_a_directory_given_twice_is_read_once(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    _assert_read_once([root, root])


def test_a_directory_and_the_directory_above_it_are_read_once(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    nested = root / "metrics"
    nested.mkdir()
    (root / "gross_margin_pct.yaml").rename(nested / "gross_margin_pct.yaml")
    _assert_read_once([nested, root])


@pytest.mark.parametrize(
    "spelling",
    ["dot", "parent", "relative"],
)
def test_one_file_spelled_two_ways_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    root = _contracts(tmp_path)
    (root / "sub").mkdir()
    name = "gross_margin_pct.yaml"
    if spelling == "dot":
        other = root / "." / name
    elif spelling == "parent":
        other = root / "sub" / ".." / name
    else:
        monkeypatch.chdir(tmp_path)
        other = Path("contracts") / name
    _assert_read_once([root, other])


def test_a_spelling_in_another_case_is_read_once_where_the_filesystem_folds_case(
    tmp_path: Path,
) -> None:
    root = _contracts(tmp_path)
    upper = root / "GROSS_MARGIN_PCT.YAML"
    if not upper.exists() or not os.path.samefile(upper, root / "gross_margin_pct.yaml"):
        pytest.skip("this filesystem tells names apart by case")
    _assert_read_once([root, upper])


def test_a_hard_link_is_the_file(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    _link(os.link, root / "gross_margin_pct.yaml", root / "linked.yaml")
    _assert_read_once([root])


def test_a_symbolic_link_to_a_file_is_the_file(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    _link(os.symlink, root / "gross_margin_pct.yaml", root / "linked.yaml")
    _assert_read_once([root])


def test_a_directory_reached_through_a_link_is_read_once(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    alias = tmp_path / "alias"
    _link(lambda target, link: os.symlink(target, link, target_is_directory=True), root, alias)
    _assert_read_once([root, alias / "gross_margin_pct.yaml"])


# --- two files stay two ----------------------------------------------------------------------


def test_two_files_of_equal_bytes_are_two_files(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(root / "gross_margin_pct.yaml", copy / "gross_margin_pct.yaml")
    report = validate_paths([root, copy])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$2/gross_margin_pct.yaml"
    assert report.issues[0].message == DEFINED_TWICE
    assert report.verdict is Verdict.BLOCK


@pytest.mark.skipif(sys.platform == "win32", reason="Windows folds `..` in the text of a path")
def test_a_parent_through_a_link_is_the_parent_the_link_leads_to(tmp_path: Path) -> None:
    """`root/link/../x` names the `x` beside the link's target: folding the text names another."""
    root = _contracts(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "sub").mkdir(parents=True)
    shutil.copy(root / "gross_margin_pct.yaml", elsewhere / "gross_margin_pct.yaml")
    _link(
        lambda target, link: os.symlink(target, link, target_is_directory=True),
        elsewhere / "sub",
        root / "link",
    )
    other = Path(f"{root}/link/../gross_margin_pct.yaml")
    assert not os.path.samefile(other, root / "gross_margin_pct.yaml")
    report = validate_paths([root / "gross_margin_pct.yaml", root / "weekly_margin.yaml", other])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$3"


# --- the file keeps the first of its paths ----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_codes"), [("active", []), ("draft", ["metric.draft", "metric.draft"])]
)
def test_argument_order_keeps_a_report_without_path_names(
    tmp_path: Path, status: str, expected_codes: list[str]
) -> None:
    other_product = "decision://sales/daily_margin@1"
    other_metric = "metric://sales/daily_margin_pct@3"

    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = status

    def relabel_product(data: dict[str, Any]) -> None:
        data["metadata"]["id"] = "decision://sales/daily_margin"
        data["scope"]["metrics"] = [other_metric]

    def relabel_metric(data: dict[str, Any]) -> None:
        relabel(data)
        data["metadata"]["id"] = "metric://sales/daily_margin_pct"

    root = _contracts(tmp_path, metric=relabel)
    other = tmp_path / "other"
    other.mkdir()
    write_contracts(other, product=relabel_product, metric=relabel_metric)
    paths = [
        root / "weekly_margin.yaml",
        root / "gross_margin_pct.yaml",
        other / "weekly_margin.yaml",
        other / "gross_margin_pct.yaml",
    ]
    report = validate_paths(paths)

    assert codes(report) == expected_codes
    assert [(issue.source, issue.subject) for issue in report.issues] == (
        [(other_product, other_metric), (PRODUCT, METRIC)] if status == "draft" else []
    )
    assert all(
        issue.message == "metric contract is still a draft: its definition is not agreed yet"
        for issue in report.issues
    )
    assert report.products == [other_product, PRODUCT]
    assert report.metrics == [other_metric, METRIC]
    assert validate_paths(paths[::-1]).model_dump_json() == report.model_dump_json()


def _unknown_status(data: dict[str, Any]) -> None:
    data["metadata"]["status"] = "unknown"


def test_a_file_the_schema_refuses_is_refused_once_under_its_first_path(tmp_path: Path) -> None:
    root = _contracts(tmp_path, metric=_unknown_status)
    (root / "sub").mkdir()
    spelled = root / "sub" / ".." / "gross_margin_pct.yaml"
    assert str(spelled) != str(root / "gross_margin_pct.yaml")
    report = validate_paths([spelled, root])
    assert codes(report) == ["contract.invalid", "metric_ref.unknown"]
    assert report.issues[0].source == "$1"

    report = validate_paths([root, spelled])
    assert codes(report) == ["contract.invalid", "metric_ref.unknown"]
    assert report.issues[0].source == "$1/gross_margin_pct.yaml"


def test_the_files_keep_the_order_they_were_found_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contracts(tmp_path)
    for name in ("a_first.yaml", "b_second.yaml"):
        (root / name).write_text("kind: Unknown\n", encoding="utf-8")
    numbers = {"a_first.yaml": 9, "b_second.yaml": 1, "gross_margin_pct.yaml": 5}
    monkeypatch.setattr(
        filesystem, "file_identity", lambda path: (0, numbers.get(Path(path).name, 7))
    )
    report = validate_paths([root, root / "b_second.yaml", root / "a_first.yaml"])
    assert [(issue.code, Path(issue.source).name) for issue in report.issues] == [
        ("contract.unknown_kind", "a_first.yaml"),
        ("contract.unknown_kind", "b_second.yaml"),
    ]


def test_a_refused_product_is_named_once(tmp_path: Path) -> None:
    root = _contracts(tmp_path, product=_unknown_status)
    loaded = load_contracts([root, root / "weekly_margin.yaml"])
    assert [issue.code for issue in loaded.report.issues] == ["contract.invalid"]
    assert [refused.source for refused in loaded.refused_products] == ["$1/weekly_margin.yaml"]


# --- a path without an identity ---------------------------------------------------------------


def test_a_path_without_an_identity_is_told_by_its_absolute_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative or absolute, one text once made absolute; `..` is not folded into it."""
    root = _contracts(tmp_path)
    (root / "sub").mkdir()
    monkeypatch.setattr(filesystem, "file_identity", lambda path: None)
    _assert_read_once([root, root / "gross_margin_pct.yaml"])
    monkeypatch.chdir(tmp_path)
    _assert_read_once([root, Path("contracts") / "gross_margin_pct.yaml"])
    report = validate_paths([root, root / "sub" / ".." / "gross_margin_pct.yaml"])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$2"


def test_paths_without_an_identity_are_not_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contracts(tmp_path)
    monkeypatch.setattr(filesystem, "file_identity", lambda path: None)
    _assert_read_once([root])


def test_a_path_without_an_identity_is_told_by_its_text_with_its_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text in another case is another path: one file read twice, never two files read once."""
    root = _contracts(tmp_path)
    upper = root / "GROSS_MARGIN_PCT.YAML"
    if not upper.exists():
        shutil.copy(root / "gross_margin_pct.yaml", upper)
    monkeypatch.setattr(filesystem, "file_identity", lambda path: None)
    report = validate_paths([root, upper])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$2"


@pytest.mark.parametrize("first_has_one", [False, True], ids=["then-identity", "then-none"])
def test_one_path_is_one_file_whether_a_look_gives_it_an_identity_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_has_one: bool
) -> None:
    root = _contracts(tmp_path)
    path = root / "gross_margin_pct.yaml"
    real = filesystem.file_identity
    answers = iter([real(path), None] if first_has_one else [None, real(path)])
    monkeypatch.setattr(
        filesystem,
        "file_identity",
        lambda other: next(answers) if os.fspath(other) == os.fspath(path) else real(other),
    )
    _assert_read_once([path, root])
    assert next(answers, "spent") == "spent"


def test_the_text_of_a_repeat_is_the_file_it_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spelling read as a repeat by identity is that file when a later look gives it none."""
    root = _contracts(tmp_path)
    (root / "sub").mkdir()
    spelled = root / "sub" / ".." / "gross_margin_pct.yaml"
    real = filesystem.file_identity
    answers = iter([real(spelled), None])
    monkeypatch.setattr(
        filesystem,
        "file_identity",
        lambda other: next(answers) if os.fspath(other) == os.fspath(spelled) else real(other),
    )
    _assert_read_once([root, spelled, spelled])
    assert next(answers, "spent") == "spent"


def test_the_identity_a_repeat_shows_is_the_file_it_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path read without an identity is the file a later look at its text gives an identity."""
    root = _contracts(tmp_path)
    (root / "sub").mkdir()
    path = root / "gross_margin_pct.yaml"
    spelled = root / "sub" / ".." / "gross_margin_pct.yaml"
    real = filesystem.file_identity
    answers = iter([None, real(path)])
    monkeypatch.setattr(
        filesystem,
        "file_identity",
        lambda other: next(answers) if os.fspath(other) == os.fspath(path) else real(other),
    )
    _assert_read_once([path, root, spelled])
    assert next(answers, "spent") == "spent"


@pytest.mark.skipif(sys.platform != "win32", reason="named streams are an NTFS feature")
def test_a_named_stream_is_not_its_file(tmp_path: Path) -> None:
    """Every stream of an NTFS file has the file's number: a stream is told by its text."""
    root = _contracts(tmp_path)
    path = root / "gross_margin_pct.yaml"
    stream = Path(f"{path}:other.yaml")
    try:
        stream.write_bytes(path.read_bytes())
    except OSError as error:
        pytest.skip(f"this filesystem keeps no named stream: {error}")
    assert os.stat(stream).st_ino == os.stat(path).st_ino
    assert filesystem.file_identity(stream) is None
    report = validate_paths([path, stream])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$2"
    # A boundary: the file under its unnamed stream's name is told by its text too.
    unnamed = Path(f"{path}::$DATA")
    report = validate_paths([path, unnamed])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].source == "$2"


@pytest.mark.skipif(sys.platform == "win32", reason="a colon names a stream on Windows")
def test_a_colon_is_part_of_a_name_beyond_windows(tmp_path: Path) -> None:
    path = tmp_path / "a:b.yaml"
    path.write_text("kind: Unknown\n", encoding="utf-8")
    status = os.stat(path)
    assert filesystem.file_identity(path) == (status.st_dev, status.st_ino)


def test_a_semantic_ref_still_reads_a_hard_link_as_a_second_file(tmp_path: Path) -> None:
    """A boundary: the contracts path of a semantic ref is read as before."""
    metrics = Case(tmp_path).base / "metrics"
    _link(os.link, metrics / "gross_margin_pct.yaml", metrics / "linked.yaml")
    snapshot = NativeModelSource(metrics.parent / "model.yaml", metrics).snapshot()
    assert [(gap.code, gap.severity) for gap in snapshot.gaps] == [
        ("gap.metric_duplicate", Severity.BLOCK)
    ]
    report = validate_paths([metrics])
    assert report.issues == []
    assert report.metrics == [METRIC]


def test_the_identity_is_the_device_and_the_number_of_the_file(tmp_path: Path) -> None:
    root = _contracts(tmp_path)
    path = root / "gross_margin_pct.yaml"
    status = os.stat(path)
    assert filesystem.file_identity(path) == (status.st_dev, status.st_ino)
    assert filesystem.file_identity(root / "." / path.name) == filesystem.file_identity(path)
    assert filesystem.file_identity(root / "weekly_margin.yaml") != filesystem.file_identity(path)


class _Status:
    def __init__(self, st_dev: int, st_ino: int) -> None:
        self.st_dev = st_dev
        self.st_ino = st_ino


def _answer_stat(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> None:
    """`os.stat` answers *answers* for their names — a status, or an error it raises."""
    real = os.stat

    def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        answer = answers.get(os.fspath(path)) if isinstance(path, str) else None
        if answer is None:
            return real(path, *args, **kwargs)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(filesystem.os, "stat", stat)


def test_the_identity_holds_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_stat(monkeypatch, {"<a>": _Status(1, 7), "<b>": _Status(2, 7)})
    assert filesystem.file_identity("<a>") == (1, 7)
    assert filesystem.file_identity("<a>") != filesystem.file_identity("<b>")


def test_files_with_one_number_on_two_devices_are_both_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contracts(tmp_path)
    first = root / "gross_margin_pct.yaml"
    second = tmp_path / "copy.yaml"
    shutil.copy(first, second)
    identities = {first: (1, 7), second: (2, 7)}
    monkeypatch.setattr(filesystem, "file_identity", lambda path: identities[Path(path)])

    report = validate_paths([first, second])

    assert codes(report) == ["contract.duplicate_id"]
    assert report.issues[0].subject == "metric://sales/gross_margin_pct"
    assert report.issues[0].source == "$2"
    assert report.issues[0].message == DEFINED_TWICE
    assert report.metrics == [METRIC]
    assert report.verdict is Verdict.BLOCK


def test_a_filesystem_that_numbers_no_file_gives_no_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_stat(monkeypatch, {"<a>": _Status(5, 0)})
    assert filesystem.file_identity("<a>") is None


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "No such file or directory"),
        PermissionError(13, "Permission denied"),
        ValueError("embedded null byte"),
    ],
    ids=["not-there", "refused", "no-form"],
)
def test_a_path_that_cannot_be_looked_at_gives_no_identity(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _answer_stat(monkeypatch, {"<a>": error})
    assert filesystem.file_identity("<a>") is None


# --- the commands ----------------------------------------------------------------------------


def test_decision_validate_reads_the_file_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _contracts(tmp_path)
    assert main(["decision", "validate", str(root), str(root / "gross_margin_pct.yaml")]) == 0
    once = capsys.readouterr().out
    assert main(["decision", "validate", str(root)]) == 0
    assert once == capsys.readouterr().out
    assert once.splitlines()[-1] == "verdict: pass (1 product(s), 1 metric contract(s), 0 issue(s))"


def test_a_run_given_a_directory_and_a_file_in_it_is_the_run_of_the_directory(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate()

    def run(paths: list[Path], evidence: str) -> GateReport:
        return verify(
            VerifyRequest(
                contract_paths=paths,
                base=case.base,
                candidate=case.candidate,
                evidence_dir=tmp_path / evidence,
                created_at=CREATED_AT,
                dashboards_dir=case.dashboards,
            )
        )

    alone = run([case.contracts], "alone")
    both = run([case.contracts, case.contracts / "gross_margin_pct.yaml"], "both")
    assert alone.verdict is Verdict.PASS_WITH_ADVISORY
    assert status_of(both, "contracts.validate") is CheckStatus.PASS
    assert summary_of(both, "contracts.validate") == "contracts.clean"
    assert both.verdict == alone.verdict
    assert both.run_id == alone.run_id
    assert both.evidence_digest == alone.evidence_digest


def test_decision_verify_exits_as_for_the_directory_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path)
    case.with_candidate()
    argv = _verify_argv(case)
    alone = main(argv)
    alone_out = capsys.readouterr().out
    shutil.rmtree(case.root / "evidence")
    both = main([*argv[:3], str(case.contracts / "gross_margin_pct.yaml"), *argv[3:]])
    assert both == alone
    assert capsys.readouterr().out == alone_out
    assert DEFINED_TWICE not in alone_out


def test_the_versions() -> None:
    assert int(VERIFIER_VERSION) >= 116
    assert int(CHECK_VERSION) >= 79

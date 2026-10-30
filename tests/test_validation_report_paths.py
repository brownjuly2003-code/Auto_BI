"""The validation report is one report on every system (T-121).

Durable spec: docs/specs/validation-report-paths.md. An issue of a contract file names the file
by its path, and the report is sealed whole as an input of a run (`_static_inputs`): its text
is part of the run id. The path was written by `str`, with the separator of the system, and
what a validated directory holds was taken in the order `sorted` gives paths — on Windows
without case. The same files read on Windows and on POSIX gave issues in other words and in
another order, and of an id two files define the run was handed the other contract: another
report, another run.

Now the report names a file with `/` between the names, and takes what a validated directory
holds in the order of its paths, name by name, by code point with case. The names are those of
docs/specs/validated-path-names.md: the place of the validated path that reached the file, `$1`
for the first, then the names below it.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision.validate import load_contracts, validate_paths
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    metric_contract,
    status_of,
)
from tests.test_schema_refused_products import REPEATED, _delivering_twice
from tests.test_unlisted_paths import _answer, _deny_listing
from tests.test_unreadable_run_inputs import _refused

METRIC_ID = "metric://sales/gross_margin_pct"
NOPE = "kind: Nope\n"
UNKNOWN_KIND = "kind must be DecisionProduct or MetricContract"
UNLISTED = "cannot list directory: PermissionError"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _owned_by(owner: str) -> dict[str, Any]:
    """The metric contract of the run fixtures, owned by *owner*."""
    data = metric_contract()
    data["metadata"]["owner"] = owner
    return data


def _sources(paths: list[Any]) -> list[str]:
    return [issue.source for issue in validate_paths(paths).issues]


# --- one file, one text --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        pytest.param("kind: [\n", "contract.unreadable", id="no-yaml"),
        pytest.param("- 1\n", "contract.unreadable", id="no-mapping"),
        pytest.param(NOPE, "contract.unknown_kind", id="unknown-kind"),
        pytest.param("kind: MetricContract\n", "contract.invalid", id="invalid"),
    ],
)
def test_an_issue_of_a_file_names_it_with_a_slash_between_the_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, code: str
) -> None:
    """However the file is reached — its directory, as a path or as text, or the file itself."""
    monkeypatch.chdir(tmp_path)
    _write(Path("contracts", "sub", "x.yaml"), text)

    for given, name in (
        (Path("contracts"), "$1/sub/x.yaml"),
        ("contracts", "$1/sub/x.yaml"),
        (Path("contracts", "sub", "x.yaml"), "$1"),
    ):
        issues = validate_paths([given]).issues
        assert [(issue.code, issue.source) for issue in issues] == [(code, name)]


def test_the_file_that_defines_an_id_again_is_named_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_yaml(Path("contracts", "sub", "a.yaml"), metric_contract())
    _write_yaml(Path("contracts", "sub", "b.yaml"), metric_contract())

    issues = validate_paths([Path("contracts")]).issues
    assert [(issue.code, issue.subject, issue.source) for issue in issues] == [
        ("contract.duplicate_id", METRIC_ID, "$1/sub/b.yaml")
    ]


@pytest.mark.parametrize(
    "given",
    [Path("contracts", "sub", "gone.yaml"), "contracts/sub/gone.yaml"],
    ids=["path", "text"],
)
def test_a_path_that_is_not_there_is_named_by_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, given: Path | str
) -> None:
    monkeypatch.chdir(tmp_path)
    [issue] = validate_paths([given]).issues
    assert (issue.subject, issue.source, issue.message) == ("$1", "$1", "path does not exist")


def test_a_path_that_may_not_be_looked_at_is_named_by_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write(Path("contracts", "sub", "x.yaml"), NOPE)
    _answer(monkeypatch, path, PermissionError(errno.EACCES, "denied"))

    [issue] = validate_paths([path]).issues
    assert (issue.code, issue.subject, issue.source, issue.message) == (
        "contract.unreadable",
        "$1",
        "$1",
        "cannot look at path: PermissionError",
    )


def test_a_directory_that_cannot_be_listed_is_named_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    denied = Path("contracts", "sub", "deep")
    denied.mkdir(parents=True)
    _deny_listing(monkeypatch, denied)

    [issue] = validate_paths([Path("contracts")]).issues
    assert (issue.subject, issue.source, issue.message) == ("$1/sub/deep", "$1/sub/deep", UNLISTED)


def test_a_file_under_an_absolute_path_is_named_with_a_slash_between_its_names(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "contracts" / "sub" / "x.yaml", NOPE)

    [source] = _sources([tmp_path / "contracts"])
    assert source == "$1/sub/x.yaml"


@pytest.mark.skipif(sys.platform == "win32", reason="a name on Windows holds no backslash")
def test_a_backslash_a_name_holds_is_kept_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a name may hold `\\`, it is part of the name, not a separator: it is not turned
    into `/`, which would name a file in a directory."""
    monkeypatch.chdir(tmp_path)
    _write(Path("contracts", "a\\b.yaml"), NOPE)

    assert _sources([Path("contracts")]) == ["$1/a\\b.yaml"]


def test_a_product_its_schema_refused_is_named_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_yaml(Path("contracts", "sub", "weekly_margin.yaml"), _delivering_twice())

    loaded = load_contracts([Path("contracts")])
    [refused] = loaded.refused_products
    assert refused.source == "$1/sub/weekly_margin.yaml"
    assert [(issue.code, issue.source) for issue in loaded.report.issues] == [
        ("contract.invalid", "$1/sub/weekly_margin.yaml")
    ]


def test_a_run_that_cannot_find_its_product_names_the_refused_file_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    _write_yaml(case.contracts / "sub" / "weekly_margin.yaml", _delivering_twice())
    monkeypatch.chdir(tmp_path)
    case.contracts = Path("contracts")

    assert _refused(case, product_id=PRODUCT_ID).endswith(
        f"'{PRODUCT_ID}' in $1/sub/weekly_margin.yaml ({REPEATED})"
    )


def test_decision_validate_prints_the_report_that_names_files_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(Path("contracts", "sub", "x.yaml"), NOPE)

    assert main(["decision", "validate", "contracts"]) == EXIT_ERROR
    assert f"{'':<8} {UNKNOWN_KIND} [$1/sub/x.yaml]\n" in capsys.readouterr().out
    assert main(["decision", "validate", "contracts", "--json"]) == EXIT_ERROR
    [issue] = json.loads(capsys.readouterr().out)["issues"]
    assert issue["source"] == "$1/sub/x.yaml"


# --- what a directory holds, in the order of its paths -------------------------------------


def test_what_a_directory_holds_is_taken_in_the_order_of_its_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name by name, by code point with case: `B.yaml` before `a.yaml` on every system, and
    what `sub/` holds before `sub-x.yaml` and `sub.yaml`, whose names follow `sub`."""
    monkeypatch.chdir(tmp_path)
    for name in ("sub.yaml", "a.yaml", "sub-x.yaml", "B.yaml", "sub/x.yaml", "sub/Y.yaml"):
        _write(Path("contracts", name), NOPE)

    assert _sources([Path("contracts")]) == [
        "$1/B.yaml",
        "$1/a.yaml",
        "$1/sub/Y.yaml",
        "$1/sub/x.yaml",
        "$1/sub-x.yaml",
        "$1/sub.yaml",
    ]


def test_names_only_case_tells_apart_stand_by_code_point_where_case_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    contracts = Path("contracts")
    probe = _write(contracts / "Probe", "")
    if (contracts / "probe").exists():
        pytest.skip("this filesystem folds the case of file names")
    probe.unlink()
    for name in ("m.yaml", "M.yaml"):
        _write(contracts / name, NOPE)

    assert _sources([contracts]) == ["$1/M.yaml", "$1/m.yaml"]


@pytest.mark.parametrize(
    ("name", "f_first"),
    [
        pytest.param("e\N{COMBINING ACUTE ACCENT}.yaml", False, id="decomposed"),
        pytest.param("\N{LATIN SMALL LETTER E WITH ACUTE}.yaml", True, id="composed"),
    ],
)
def test_a_name_is_compared_as_it_is_listed_not_as_a_normal_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, f_first: bool
) -> None:
    """`e` and a combining acute accent stand before `f`; the one code point NFC writes for
    them stands after it. Neither form is normalized into the other before it is compared."""
    monkeypatch.chdir(tmp_path)
    for each in ("f.yaml", name):
        _write(Path("contracts", each), NOPE)
    if name not in os.listdir("contracts"):
        pytest.skip("this filesystem hands back another normal form than was written")

    pair = [f"$1/{name}", "$1/f.yaml"]
    assert _sources([Path("contracts")]) == (pair[::-1] if f_first else pair)


def test_the_paths_given_keep_their_order_and_each_directory_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only what one directory holds is ordered: the validated paths are gone through as given."""
    monkeypatch.chdir(tmp_path)
    for name in ("z/a.yaml", "z/B.yaml", "a/x.yaml"):
        _write(Path("contracts", name), NOPE)

    assert _sources([Path("contracts", "z"), Path("contracts", "a")]) == [
        "$1/B.yaml",
        "$1/a.yaml",
        "$2/x.yaml",
    ]


@pytest.mark.parametrize(
    ("kept", "again"),
    [("B.yaml", "a.yaml"), ("sub/m.yaml", "sub.yaml")],
    ids=["case", "directory"],
)
def test_of_an_id_two_files_define_the_one_first_in_that_order_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: str, again: str
) -> None:
    """The run is handed the contract of the file that stands first, on every system; the other
    file is the one that defines the id again."""
    monkeypatch.chdir(tmp_path)
    _write_yaml(Path("contracts", again), _owned_by("team-again"))
    _write_yaml(Path("contracts", kept), _owned_by("team-kept"))

    loaded = load_contracts([Path("contracts")])
    assert loaded.metrics[METRIC_ID].metadata.owner == "team-kept"
    assert [(issue.code, issue.source) for issue in loaded.report.issues] == [
        ("contract.duplicate_id", f"$1/{again}")
    ]


def test_directories_that_cannot_be_listed_are_named_in_the_order_of_their_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    names = ("sub-x", "a", "sub/deep", "B")
    for name in names:
        Path("contracts", name).mkdir(parents=True)
    _deny_listing(monkeypatch, *(Path("contracts", name) for name in names))

    assert _sources([Path("contracts")]) == ["$1/B", "$1/a", "$1/sub/deep", "$1/sub-x"]


def test_products_its_schema_refused_are_named_in_the_order_of_their_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("a.yaml", "B.yaml"):
        _write_yaml(Path("contracts", name), _delivering_twice())

    refused = load_contracts([Path("contracts")]).refused_products
    assert [product.source for product in refused] == ["$1/B.yaml", "$1/a.yaml"]


# --- the run ---------------------------------------------------------------------------------


def test_the_run_seals_the_report_that_names_files_with_a_slash_in_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path).with_candidate()
    for name in ("a.yaml", "B.yaml"):
        _write(case.contracts / "notes" / name, NOPE)
    monkeypatch.chdir(tmp_path)
    case.contracts = Path("contracts")

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    payload = _check_payload(tmp_path, report, "contracts.validate")
    assert [(issue["code"], issue["source"]) for issue in payload["issues"]] == [
        ("contract.unknown_kind", "$1/notes/B.yaml"),
        ("contract.unknown_kind", "$1/notes/a.yaml"),
    ]


def test_the_versions() -> None:
    assert int(VERIFIER_VERSION) >= 118
    assert int(CHECK_VERSION) >= 79

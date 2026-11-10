"""A symbolic link below `metrics/` is frozen as the text git commits for it.

Durable spec: docs/specs/metrics-links.md. A native snapshot reads every contract file below
the contracts path of a semantic ref (`NativeModelSource`). It read a symbolic link through:
the link led to a contract and the snapshot held a second contract of its id, block
`gap.metric_duplicate`; it led nowhere or to a directory and the snapshot held nothing of it.
Git commits a symbolic link as its text, and a checkout makes the link where `core.symlinks` is
true and writes a file of that text where it is false: one commit was two snapshots, and two
run ids, by the machine it was checked out on. A link is now read as its text, so it is the
file a checkout without links writes. A hard link is not told apart from a copy: git commits it
as one, and a checkout writes one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic import gateway
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from tests.test_contract_read_order import _ref
from tests.test_decision_verify import Case
from tests.test_semantic_gateway import contract

#: A backslash, built so that no tool along the way reads it as an escape.
B = chr(92)
METRIC = "metric://sales/gross_margin_pct"
CONTRACT = {"gross_margin_pct.yaml": contract()}
NOT_A_CONTRACT = "file in the contracts path is not a MetricContract"
TWO_CONTRACTS = (
    "metric is defined by 2 contracts (versions 3, 3): which of them the ref means is not known"
)


def _symlink(text: str, link: Path, *, to_directory: bool = False) -> None:
    """Make *link* hold *text* as git makes a link it checks out: on Windows with `\\`."""
    try:
        os.symlink(text.replace("/", os.sep), link, target_is_directory=to_directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this process may not make a symbolic link: {error}")


def _hard_link(target: Path, link: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this filesystem makes no hard link: {error}")


def _linked(root: Path, text: str, *, to_directory: bool = False) -> SemanticSnapshot:
    """A ref of the contract and, beside it, `linked.yaml`: a symbolic link holding *text*."""
    source = _ref(root, CONTRACT)
    _symlink(text, root / "metrics" / "linked.yaml", to_directory=to_directory)
    return source.snapshot()


def _written(root: Path, text: str) -> SemanticSnapshot:
    """The same ref as a checkout without links writes it: `linked.yaml` a file of *text*."""
    return _ref(root, {**CONTRACT, "linked.yaml": text}).snapshot()


def _gaps(snapshot: SemanticSnapshot) -> list[tuple[str, Severity, str, str, str]]:
    return [
        (gap.code, gap.severity, gap.path, gap.reason, gap.source_value_ref)
        for gap in snapshot.gaps
    ]


def _another_id(path: Path) -> None:
    """A loadable contract of `metric://sales/elsewhere` at *path*."""
    data: dict[str, Any] = contract()
    data["metadata"] = {**data["metadata"], "id": "metric://sales/elsewhere"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n")


# --- a symbolic link is the file of its text ------------------------------------------------


def test_a_link_to_a_contract_beside_it_is_the_file_of_its_text(tmp_path: Path) -> None:
    linked = _linked(tmp_path / "linked", "gross_margin_pct.yaml")
    written = _written(tmp_path / "written", "gross_margin_pct.yaml")

    assert linked == written
    assert _gaps(linked) == [
        (
            "gap.contract_invalid",
            Severity.BLOCK,
            "contracts.linked.yaml",
            NOT_A_CONTRACT,
            digest_bytes(b"gross_margin_pct.yaml"),
        )
    ]
    assert [(node.id, node.version) for node in linked.graph.metrics] == [(METRIC, 3)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("../shared/elsewhere.yaml", id="up-and-across"),
        pytest.param("sub/x.yaml", id="down"),
        pytest.param("/no/such/x.yaml", id="rooted"),
        pytest.param("C:/no/such/x.yaml", id="drive"),
    ],
)
def test_a_link_is_the_text_git_commits_wherever_it_leads(tmp_path: Path, text: str) -> None:
    """Git for Windows makes each link with `\\`; the text read has `/`, as git committed it.
    What the link leads to is not read: `elsewhere.yaml` is a contract of another id."""
    _another_id(tmp_path / "linked" / "shared" / "elsewhere.yaml")

    linked = _linked(tmp_path / "linked", text)

    assert linked == _written(tmp_path / "written", text)
    assert _gaps(linked) == [
        (
            "gap.contract_invalid",
            Severity.BLOCK,
            "contracts.linked.yaml",
            NOT_A_CONTRACT,
            digest_bytes(text.encode()),
        )
    ]
    assert linked.metric("metric://sales/elsewhere") is None


@pytest.mark.parametrize(
    ("leads_to", "text"),
    [("nothing", "nowhere.yaml"), ("a directory", "../shared"), ("itself", "linked.yaml")],
)
def test_a_link_is_a_contract_file_whatever_it_leads_to(
    tmp_path: Path, leads_to: str, text: str
) -> None:
    if leads_to == "a directory":
        (tmp_path / "linked" / "shared").mkdir(parents=True)

    linked = _linked(tmp_path / "linked", text, to_directory=leads_to == "a directory")

    assert linked == _written(tmp_path / "written", text)
    assert [(gap.code, gap.path) for gap in linked.gaps] == [
        ("gap.contract_invalid", "contracts.linked.yaml")
    ]


def test_no_look_is_made_through_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filesystem refuses every look through `linked.yaml` at what it leads to, as at a
    file in a directory this process may not search: the look is at the link alone."""
    written = _written(tmp_path / "written", "gross_margin_pct.yaml")
    source = _ref(tmp_path / "linked", CONTRACT)
    _symlink("gross_margin_pct.yaml", tmp_path / "linked" / "metrics" / "linked.yaml")
    look = os.stat

    def refused(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(path).name == "linked.yaml" and kwargs.get("follow_symlinks", True):
            raise PermissionError(13, "a look through the link is refused", str(path))
        return look(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", refused)

    assert source.snapshot() == written


def test_a_link_without_a_contract_suffix_is_no_contract_file(tmp_path: Path) -> None:
    """Nor is a directory reached through a link walked."""
    _another_id(tmp_path / "shared" / "metrics" / "elsewhere.yaml")
    source = _ref(tmp_path / "linked", CONTRACT)
    metrics = tmp_path / "linked" / "metrics"
    _symlink("../../shared/metrics", metrics / "shared", to_directory=True)
    _symlink("gross_margin_pct.yaml", metrics / "linked.txt")

    assert source.snapshot() == _ref(tmp_path / "plain", CONTRACT).snapshot()


@pytest.mark.parametrize(
    ("read_back", "on_windows"),
    [
        pytest.param(f"sub{B}x.yaml", "sub/x.yaml", id="relative"),
        pytest.param("sub/x.yaml", "sub/x.yaml", id="relative-with-slash"),
        pytest.param(f"{B}no{B}x.yaml", "/no/x.yaml", id="rooted"),
        pytest.param(f"{B}{B}?{B}C:{B}x.yaml", "C:/x.yaml", id="drive"),
        pytest.param(
            f"{B}{B}?{B}UNC{B}server{B}share{B}x.yaml", "//server/share/x.yaml", id="share"
        ),
    ],
)
def test_the_text_read_back_is_the_text_git_commits(read_back: str, on_windows: str) -> None:
    """What `os.readlink` gives back for the links git for Windows makes of the texts it
    commits, recorded on Windows: on POSIX the text read back is the text committed."""
    expected = on_windows if sys.platform == "win32" else read_back
    assert gateway._link_text(read_back) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="a backslash separates names on Windows")
def test_a_backslash_in_a_link_text_stays_on_posix(tmp_path: Path) -> None:
    text = f"a{B}b.yaml"
    source = _ref(tmp_path / "linked", CONTRACT)
    os.symlink(text, tmp_path / "linked" / "metrics" / "linked.yaml")

    assert source.snapshot() == _written(tmp_path / "written", text)


def test_a_link_text_yaml_cannot_read_is_the_file_of_that_text(tmp_path: Path) -> None:
    """The text is parsed as the bytes of any contract file: no reason is fixed for a link."""
    linked = _linked(tmp_path / "linked", "[x.yaml")

    assert linked == _written(tmp_path / "written", "[x.yaml")
    [gap] = linked.gaps
    assert (gap.code, gap.path, gap.source_value_ref) == (
        "gap.contract_invalid",
        "contracts.linked.yaml",
        digest_bytes(b"[x.yaml"),
    )
    assert gap.reason.startswith("metric contract cannot be read: ")
    assert gap.reason != NOT_A_CONTRACT


@pytest.mark.skipif(sys.platform == "win32", reason="a link's text on Windows is UTF-16")
def test_a_link_text_no_utf_8_decodes_is_the_file_of_those_bytes(tmp_path: Path) -> None:
    """`os.readlink` gives back the byte 0xff as a surrogate; the link is read as that byte."""
    held = b"\xff.yaml"
    source = _ref(tmp_path / "linked", CONTRACT)
    try:
        os.symlink(held, tmp_path / "linked" / "metrics" / "linked.yaml")
    except OSError as error:
        pytest.skip(f"this filesystem keeps no such link text: {error}")
    written = _ref(tmp_path / "written", CONTRACT)
    (tmp_path / "written" / "metrics" / "linked.yaml").write_bytes(held)

    snapshot = source.snapshot()

    assert snapshot == written.snapshot()
    assert _gaps(snapshot) == [
        (
            "gap.contract_invalid",
            Severity.BLOCK,
            "contracts.linked.yaml",
            "metric contract cannot be read: UnicodeDecodeError",
            digest_bytes(held),
        )
    ]


# --- what is still read as before -------------------------------------------------------------


def test_a_hard_link_is_frozen_as_the_copy_a_checkout_writes(tmp_path: Path) -> None:
    source = _ref(tmp_path / "hard", CONTRACT)
    metrics = tmp_path / "hard" / "metrics"
    _hard_link(metrics / "gross_margin_pct.yaml", metrics / "linked.yaml")
    copied = _ref(tmp_path / "copied", {**CONTRACT, "linked.yaml": contract()})

    snapshot = source.snapshot()

    assert snapshot == copied.snapshot()
    assert _gaps(snapshot) == [
        ("gap.metric_duplicate", Severity.BLOCK, f"metrics.{METRIC}", TWO_CONTRACTS, "")
    ]
    assert [(node.id, node.version) for node in snapshot.graph.metrics] == [(METRIC, 3)] * 2


def test_the_contracts_path_and_the_model_are_read_through_a_link(tmp_path: Path) -> None:
    """The caller names them; a link there is followed, a file or a directory alike."""
    ref = tmp_path / "ref"
    _ref(ref, CONTRACT)
    links = tmp_path / "links"
    links.mkdir()
    _symlink("../ref/model.yaml", links / "model.yaml")
    _symlink("../ref/metrics/gross_margin_pct.yaml", links / "gross_margin_pct.yaml")
    _symlink("../ref/metrics", links / "metrics", to_directory=True)

    def frozen(root: Path, contracts: str) -> SemanticSnapshot:
        return NativeModelSource(root / "model.yaml", root / contracts).snapshot()

    one_file = frozen(links, "gross_margin_pct.yaml")
    directory = frozen(links, "metrics")

    assert one_file == frozen(ref, "metrics/gross_margin_pct.yaml")
    assert directory == frozen(ref, "metrics")
    assert one_file.gaps == directory.gaps == []
    assert [node.id for node in one_file.graph.metrics] == [METRIC]
    assert [node.id for node in directory.graph.metrics] == [METRIC]


def test_validate_still_reads_a_symbolic_link_as_the_file_it_leads_to(tmp_path: Path) -> None:
    """A boundary: `decision validate` of the directory passes where the snapshot blocks."""
    metrics = Case(tmp_path).base / "metrics"
    _symlink("gross_margin_pct.yaml", metrics / "linked.yaml")

    snapshot = NativeModelSource(metrics.parent / "model.yaml", metrics).snapshot()
    report = validate_paths([metrics])

    assert [(gap.code, gap.path) for gap in snapshot.gaps] == [
        ("gap.contract_invalid", "contracts.linked.yaml")
    ]
    assert report.issues == []
    assert report.verdict is Verdict.PASS
    assert report.metrics == [f"{METRIC}@3"]


# --- the run ----------------------------------------------------------------------------------


def test_a_run_over_a_link_is_the_run_over_the_file_of_its_text(tmp_path: Path) -> None:
    linked = Case(tmp_path / "linked").with_candidate()
    _symlink("gross_margin_pct.yaml", linked.candidate / "metrics" / "linked.yaml")
    written = Case(tmp_path / "written").with_candidate()
    (written.candidate / "metrics" / "linked.yaml").write_bytes(b"gross_margin_pct.yaml")

    report = linked.run()

    assert report.verdict is Verdict.BLOCK
    assert report.run_id == written.run().run_id


def test_a_run_over_a_hard_link_is_the_run_over_a_copy(tmp_path: Path) -> None:
    hard = Case(tmp_path / "hard").with_candidate()
    metrics = hard.candidate / "metrics"
    _hard_link(metrics / "gross_margin_pct.yaml", metrics / "linked.yaml")
    copied = Case(tmp_path / "copied").with_candidate()
    metrics = copied.candidate / "metrics"
    shutil.copyfile(metrics / "gross_margin_pct.yaml", metrics / "linked.yaml")

    report = hard.run()

    assert report.verdict is Verdict.BLOCK
    assert report.run_id == copied.run().run_id


def test_a_validated_path_that_reaches_a_link_is_two_runs(tmp_path: Path) -> None:
    """A boundary: `decision validate` reads a link through, and its report is part of the
    run's key, so a link in a validated directory and the file of its text are two runs."""
    linked = Case(tmp_path / "linked").with_candidate()
    written = Case(tmp_path / "written").with_candidate()
    for directory in (linked.contracts, linked.candidate / "metrics"):
        _symlink("gross_margin_pct.yaml", directory / "linked.yaml")
    for directory in (written.contracts, written.candidate / "metrics"):
        (directory / "linked.yaml").write_bytes(b"gross_margin_pct.yaml")

    assert [issue.code for issue in validate_paths([linked.contracts]).issues] == []
    assert [(issue.code, issue.source) for issue in validate_paths([written.contracts]).issues] == [
        ("contract.unreadable", "$1/linked.yaml")
    ]
    assert linked.run().run_id != written.run().run_id


# --- one commit, with and without links -------------------------------------------------------


def _git(repo: Path, *args: str, data: bytes | None = None) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
        input=data,
        capture_output=True,
        env=env,
        check=True,
        timeout=60,
    )
    return done.stdout.decode().strip()


def test_one_commit_is_one_snapshot_whether_a_checkout_makes_links_or_not(
    tmp_path: Path,
) -> None:
    """The index of a commit holds `metrics/linked.yaml` as a symbolic link (mode 120000) to
    the contract beside it; `git checkout-index` writes the tree with `core.symlinks` true and
    false."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    repo = tmp_path / "repo"
    _ref(repo, CONTRACT)
    _git(repo, "init", "-q")
    _git(repo, "add", "model.yaml", "metrics")
    blob = _git(repo, "hash-object", "-w", "--stdin", data=b"gross_margin_pct.yaml")
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},metrics/linked.yaml")
    trees: dict[str, Path] = {}
    for symlinks in ("true", "false"):
        tree = tmp_path / f"symlinks-{symlinks}"
        prefix = f"--prefix={tree.as_posix()}/"
        _git(repo, "-c", f"core.symlinks={symlinks}", "checkout-index", "-a", prefix)
        trees[symlinks] = tree
    if not (trees["true"] / "metrics" / "linked.yaml").is_symlink():
        pytest.skip("git made no symbolic link here")
    assert not (trees["false"] / "metrics" / "linked.yaml").is_symlink()

    made, written = (
        NativeModelSource(tree / "model.yaml", tree / "metrics").snapshot()
        for tree in trees.values()
    )

    assert made == written
    assert [(gap.code, gap.path) for gap in made.gaps] == [
        ("gap.contract_invalid", "contracts.linked.yaml")
    ]

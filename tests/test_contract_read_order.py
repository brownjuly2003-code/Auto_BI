"""The contracts of a semantic ref are frozen in an order of their own (T-120).

A native snapshot reads every contract file below `metrics/` (`NativeModelSource`). Its source
digest keeps each file's digest under its file name, not under the directory that holds it, so
a contract moved between those directories leaves the digest as it was (T-111). The rest of
the snapshot followed the order its files were listed in — `sorted` paths, the directory first
and, on Windows, without case: the gaps of two contracts, and the nodes of a metric id that two
contracts define, stood in another order when one file moved to another directory or when the
same ref was read on another system, and the snapshot, sealed whole and part of the run's
identity, was another one — with the source digest and the canonical digest unchanged where
only gaps moved, and with another canonical digest where the nodes of an id did.

Now the contracts are frozen in an order of what they hold: a file that does not load is
answered in the order of file names, by code point, then of bytes; a contract that loads stands
in the graph and is answered by its gaps in the order of metric ids, then of versions, then of
bytes. Neither the directory nor the order a listing gives is part of either order. See
`docs/specs/contract-read-order.md`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.verification.release_gate import VERIFIER_VERSION, CheckStatus, GateReport
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    _write_yaml,
    metric_contract,
    status_of,
)
from tests.test_semantic_gateway import contract, gap_codes, model

ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]
BROKEN = "kind: MetricContract\nmetadata: {id: metric://x/y}\n"


def _opaque(metric_id: str, contract_of: Any = contract) -> dict[str, Any]:
    """A contract of *metric_id* whose formula is reviewed SQL: one REVIEW gap of its own."""
    data = contract_of()
    data["metadata"] = {**data["metadata"], "id": metric_id}
    data["formula"] = {
        "kind": "reviewed_sql",
        "sql": "SELECT 1",
        "review": {
            "reviewer": "data-governance",
            "reviewed_at": "2026-08-01T10:00:00Z",
            "rationale": "legacy definition, kept verbatim",
        },
    }
    return data


def _version(version: int, **metadata: Any) -> dict[str, Any]:
    data = contract()
    data["metadata"] = {**data["metadata"], "version": version, **metadata}
    return data


def _ref(root: Path, files: dict[str, Any]) -> NativeModelSource:
    """A semantic ref of the gateway test model with *files* below `metrics/`."""
    root.mkdir(parents=True, exist_ok=True)
    model().dump(root / "model.yaml")
    for name, data in files.items():
        path = root / "metrics" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
        path.write_text(payload, encoding="utf-8", newline="\n")
    return NativeModelSource(root / "model.yaml", root / "metrics")


def _snapshot(root: Path, files: dict[str, Any]) -> SemanticSnapshot:
    return _ref(root, files).snapshot()


def _paths(snapshot: SemanticSnapshot) -> list[str]:
    return [gap.path for gap in snapshot.gaps]


X, Y = "metric://sales/x", "metric://sales/y"


# --- a contract that loads: the order of ids, versions and bytes ------------------------


def test_a_contract_moved_to_another_directory_leaves_the_snapshot_as_it_was(
    tmp_path: Path,
) -> None:
    """`z/x.yaml` is listed after `y.yaml`, `x.yaml` before it: one snapshot all the same."""
    flat = _snapshot(tmp_path / "flat", {"x.yaml": _opaque(X), "y.yaml": _opaque(Y)})
    moved = _snapshot(tmp_path / "moved", {"z/x.yaml": _opaque(X), "y.yaml": _opaque(Y)})

    assert _paths(flat) == [f"metrics.{X}.formula", f"metrics.{Y}.formula"]
    assert moved.source_digest == flat.source_digest
    assert moved == flat


def test_the_gaps_of_contracts_follow_their_metric_ids_not_their_file_names(
    tmp_path: Path,
) -> None:
    """`a.yaml` holds `y` and `b.yaml` holds `x`: the gap of `x` comes first."""
    snapshot = _snapshot(tmp_path, {"a.yaml": _opaque(Y), "b.yaml": _opaque(X)})

    assert _paths(snapshot) == [f"metrics.{X}.formula", f"metrics.{Y}.formula"]


def test_file_names_in_another_case_do_not_move_the_gaps(tmp_path: Path) -> None:
    """The listing of `Y.yaml` and `x.yaml` sorts `Y.yaml` first where case is kept and last
    where it is folded; the order of the ids is the same on every system."""
    snapshot = _snapshot(tmp_path, {"Y.yaml": _opaque(Y), "x.yaml": _opaque(X)})
    folded = _snapshot(tmp_path / "folded", {"y.yaml": _opaque(Y), "X.yaml": _opaque(X)})

    assert _paths(snapshot) == [f"metrics.{X}.formula", f"metrics.{Y}.formula"]
    assert _paths(folded) == _paths(snapshot)


def test_the_gaps_of_one_contract_keep_their_own_order(tmp_path: Path) -> None:
    """The order is of contracts, not of gaps: a contract's formula gap still stands before
    its missing fields, which stand in the order of their names."""
    second = _opaque(X)
    second["grain"] = {"table": "dm.sales_daily", "keys": ["date", "zz_key", "aa_key"]}
    snapshot = _snapshot(tmp_path, {"a.yaml": _opaque(Y), "b.yaml": second})

    assert gap_codes(snapshot) == [
        "gap.opaque_metric_formula",
        "gap.metric_field_missing",
        "gap.metric_field_missing",
        "gap.opaque_metric_formula",
    ]
    assert [gap.reason.rsplit(": ", 1)[-1] for gap in snapshot.gaps[1:3]] == [
        "dm.sales_daily.aa_key",
        "dm.sales_daily.zz_key",
    ]
    assert snapshot.gaps[3].path == f"metrics.{Y}.formula"


def test_two_contracts_of_one_id_stand_in_the_order_of_their_versions(tmp_path: Path) -> None:
    """Version 4 in `a/m.yaml` and 3 in `b/m.yaml`, or the other way round: one graph, one
    canonical digest, and the node a lookup by id finds is version 3 in both."""
    one = _snapshot(tmp_path / "one", {"a/m.yaml": _version(4), "b/m.yaml": _version(3)})
    other = _snapshot(tmp_path / "other", {"a/m.yaml": _version(3), "b/m.yaml": _version(4)})
    named = _snapshot(tmp_path / "named", {"a.yaml": _version(10), "b.yaml": _version(9)})

    assert [node.version for node in one.graph.metrics] == [3, 4]
    assert other.graph == one.graph
    assert other.canonical_digest == one.canonical_digest
    assert other.source_digest == one.source_digest
    assert other == one
    metric = one.metric("metric://sales/gross_margin_pct")
    assert metric is not None and metric.version == 3
    assert [node.version for node in named.graph.metrics] == [9, 10]


def test_two_contracts_of_one_id_and_version_stand_in_the_order_of_their_bytes(
    tmp_path: Path,
) -> None:
    """Contracts of one id and one version that say different things are told apart by the
    digest of their bytes, wherever they lie and whatever their names."""
    first, second = _version(3, owner="finance"), _version(3, owner="marketing")
    one = _snapshot(tmp_path / "one", {"a.yaml": first, "b.yaml": second})
    other = _snapshot(tmp_path / "other", {"a.yaml": second, "b.yaml": first})
    below = _snapshot(tmp_path / "below", {"z/a.yaml": first, "b.yaml": second})

    by_bytes = sorted(
        (digest_bytes(path.read_bytes()), yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in (tmp_path / "one" / "metrics").iterdir()
    )
    assert [node.owner for node in one.graph.metrics] == [
        data["metadata"]["owner"] for _, data in by_bytes
    ]
    assert other.graph == one.graph
    assert other.canonical_digest == one.canonical_digest
    assert below == one


# --- a file that does not load: the order of names and bytes ----------------------------


def test_files_that_do_not_load_are_answered_in_the_order_of_their_names(tmp_path: Path) -> None:
    """`b.yaml` before `z/a.yaml` in a listing, and `a.yaml` before `B.yaml` where case is
    folded: the names are compared by code point, the directory not at all."""
    below = _snapshot(tmp_path / "below", {"z/a.yaml": BROKEN, "b.yaml": BROKEN})
    cased = _snapshot(tmp_path / "cased", {"a.yaml": BROKEN, "B.yaml": BROKEN})

    assert _paths(below) == ["contracts.a.yaml", "contracts.b.yaml"]
    assert _paths(cased) == ["contracts.B.yaml", "contracts.a.yaml"]


@pytest.mark.parametrize(
    ("name", "f_first"),
    [
        pytest.param("e\N{COMBINING ACUTE ACCENT}.yaml", False, id="decomposed"),
        pytest.param("\N{LATIN SMALL LETTER E WITH ACUTE}.yaml", True, id="composed"),
    ],
)
def test_a_name_is_compared_as_it_is_listed_not_as_a_normal_form(
    tmp_path: Path, name: str, f_first: bool
) -> None:
    """`e` and a combining acute accent stand before `f`; the one code point NFC writes for
    them stands after it. Neither form is normalized into the other before it is compared."""
    snapshot = _snapshot(tmp_path, {"f.yaml": BROKEN, name: BROKEN})
    if name not in os.listdir(tmp_path / "metrics"):
        pytest.skip("this filesystem hands back another normal form than was written")

    pair = [f"contracts.{name}", "contracts.f.yaml"]
    assert _paths(snapshot) == (pair[::-1] if f_first else pair)


def test_files_of_one_name_that_do_not_load_stand_in_the_order_of_their_bytes(
    tmp_path: Path,
) -> None:
    """Two `m.yaml` that do not load, in `a/` and in `b/`, either way round: one snapshot,
    whose two gaps stand in the order of the digests they name."""
    texts = [BROKEN, "kind: MetricContract\nmetadata: {id: metric://x/z}\n"]
    one = _snapshot(tmp_path / "one", {"a/m.yaml": texts[0], "b/m.yaml": texts[1]})
    other = _snapshot(tmp_path / "other", {"a/m.yaml": texts[1], "b/m.yaml": texts[0]})

    digests = sorted(digest_bytes(text.encode("utf-8")) for text in texts)
    assert [gap.source_value_ref for gap in one.gaps] == digests
    assert other == one


def test_files_are_answered_by_name_before_contracts_by_id(tmp_path: Path) -> None:
    """A file that does not load is answered before the gaps of the model and the contracts,
    as before, whatever their names."""
    snapshot = _snapshot(
        tmp_path, {"0/z.yaml": BROKEN, "a.yaml": _opaque(Y), "m/b.yaml": _opaque(X)}
    )

    assert _paths(snapshot) == [
        "contracts.z.yaml",
        f"metrics.{X}.formula",
        f"metrics.{Y}.formula",
    ]


def test_the_order_does_not_come_from_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The files are handed in the opposite order of the listing: the snapshot is the same."""
    files = {
        "a.yaml": _opaque(Y),
        "b/x.yaml": _opaque(X),
        "c.yaml": BROKEN,
        "d/c.yaml": "kind: Other\n",
        "e.yaml": _version(4),
        "f/e.yaml": _version(3),
    }
    source = _ref(tmp_path, files)
    listed = source._contract_files()
    before = source.snapshot()
    monkeypatch.setattr(source, "_contract_files", lambda: list(reversed(listed)))

    assert source.snapshot() == before


# --- the run: one ref laid out two ways is one run --------------------------------------


def _laid_out(root: Path, files: dict[str, dict[str, Any]]) -> GateReport:
    case = Case(root).with_candidate()
    for name, data in files.items():
        _write_yaml(case.candidate / "metrics" / name, data)
    return case.with_asset().with_results(ROWS).run(product_id=PRODUCT_ID)


def test_a_candidate_laid_out_another_way_is_the_same_run(tmp_path: Path) -> None:
    """Two candidate refs that hold the same files, one of them in a sub-directory of
    `metrics/`: the same idempotency key, run id and evidence."""
    x, y = _opaque(X, metric_contract), _opaque(Y, metric_contract)
    flat = _laid_out(tmp_path / "flat", {"x.yaml": x, "y.yaml": y})
    moved = _laid_out(tmp_path / "moved", {"z/x.yaml": x, "y.yaml": y})

    assert status_of(flat, "semantic.gaps") is CheckStatus.REVIEW
    assert moved.idempotency_key == flat.idempotency_key
    assert moved.run_id == flat.run_id

    def sealed(root: Path, report: GateReport) -> dict[str, str]:
        bundle = read_bundle(FilesystemArtifactStore(root / "evidence"), report.run_id)
        return {item.artifact_id: item.digest for item in bundle.artifacts}

    assert sealed(tmp_path / "moved", moved) == sealed(tmp_path / "flat", flat)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows folds the case of file names")
def test_two_names_only_case_tells_apart_are_two_files(tmp_path: Path) -> None:
    """Where the filesystem keeps case, `M.yaml` and `m.yaml` are two contract files of one
    id, and their order is of versions whatever their names."""
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    probe = metrics / "Probe"
    probe.write_text("", encoding="utf-8")
    if os.path.exists(metrics / "probe"):
        pytest.skip("this filesystem folds the case of file names")
    probe.unlink()
    shutil.rmtree(metrics)

    snapshot = _snapshot(tmp_path, {"m.yaml": _version(4), "M.yaml": _version(3)})

    assert [node.version for node in snapshot.graph.metrics] == [3, 4]
    assert gap_codes(snapshot) == ["gap.metric_duplicate"]


def test_the_verifier_version_moved() -> None:
    assert int(VERIFIER_VERSION) >= 117

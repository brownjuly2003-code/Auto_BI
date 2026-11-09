"""dbt formulas through the command line, capture, verify and evidence (ADR 0008, slice S4).

The inputs are the tracked pilot `docs/pilot/2026-11-06-dbt-formula-pin`: two artifacts `dbt
parse` wrote (dbt-core 1.12.6, metricflow 0.213.0) — the base one is the legacy fixture byte for
byte, the candidate one reads `net_revenue` for the `revenue` measure — the semantic refs and
contracts that pin them, and the three bundles the pilot's runs wrote. A test that needs a bundle
broken works on a copy.
"""

from __future__ import annotations

import errno
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.cli import main
from auto_bi.decision.use_cases import DbtArtifact, VerifyError, VerifyRequest, verify
from auto_bi.decision.validate import load_contracts
from auto_bi.evidence import manifest as evidence_manifest
from auto_bi.evidence.canonical import canonical_bytes, digest_bytes, sha256_digest
from auto_bi.evidence.ids import ref_id, slugify
from auto_bi.evidence.manifest import closure_artifact_id, read_bundle, verify_bundle
from auto_bi.evidence.model import ArtifactKind, EvidenceBundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot, SnapshotRead
from auto_bi.semantic.metricflow import SemanticManifest, read_semantic_manifest
from auto_bi.semantic.model import SemanticModel
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, GateInputs
from tests.test_decision_verify import ASSET_ID, ENVIRONMENT_REF, Case

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "docs" / "pilot" / "2026-11-06-dbt-formula-pin"
BASE_ARTIFACT = PILOT / "dbt" / "base" / "semantic_manifest.json"
CANDIDATE_ARTIFACT = PILOT / "dbt" / "candidate" / "semantic_manifest.json"
REF = "gross_margin_pct"
METRIC = "metric://sales/gross_margin_pct"
CREATED_AT = "2026-11-06T12:00:00Z"
#: Each case of the pilot: the candidate semantic ref, its artifact, the run under
#: `evidence/<case>` and the exit of `decision verify`.
CASES = {
    "repinned": ("candidate-repinned", CANDIDATE_ARTIFACT, "run-2714c8710f9a30d1", 4),
    "drift": ("candidate-drift", CANDIDATE_ARTIFACT, "run-ec05cc3a068b720a", 4),
    "control": ("base", BASE_ARTIFACT, "run-d13507ded3ecfb04", 0),
}
RUN = CASES["repinned"][2]


def _manifest(artifact: Path) -> SemanticManifest:
    read = read_semantic_manifest(artifact.read_bytes())
    assert read.manifest is not None, read.problems
    return read.manifest


def _read(folder: str | Path, artifact: Path | None = BASE_ARTIFACT) -> SnapshotRead:
    root = PILOT / folder
    return NativeModelSource(
        root / "model.yaml", root / "metrics", semantic_manifest=artifact, dbt_database="x"
    ).read()


def _argv(case: str, evidence: Path, *, base: bool = True, candidate: bool = True) -> list[str]:
    folder, artifact, _, _ = CASES[case]
    argv = [
        "decision",
        "verify",
        str(PILOT / "contracts"),
        "--base",
        str(PILOT / "base"),
        "--candidate",
        str(PILOT / folder),
        "--dashboards",
        str(PILOT / "dashboards"),
        "--environment",
        "synthetic-offline",
        "--created-at",
        CREATED_AT,
        "--evidence",
        str(evidence),
    ]
    if base:
        argv += ["--base-semantic-manifest", str(BASE_ARTIFACT), "--base-dbt-database", "x"]
    if candidate:
        argv += ["--candidate-semantic-manifest", str(artifact), "--candidate-dbt-database", "x"]
    return argv


def _files(run: Path) -> dict[str, bytes]:
    return {
        path.relative_to(run).as_posix(): path.read_bytes()
        for path in sorted(run.rglob("*"))
        if path.is_file()
    }


# --- decision pin ------------------------------------------------------------------------------


def test_pin_prints_the_pin_a_contract_writes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["decision", "pin", str(CANDIDATE_ARTIFACT), REF]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    pin = _manifest(CANDIDATE_ARTIFACT).closure_digest(REF)
    assert captured.out == f"{pin}  {REF}\n"
    # the value the re-pinned contract of the pilot carries
    contract = yaml.safe_load(
        (PILOT / "candidate-repinned" / "metrics" / "gross_margin_pct.yaml").read_bytes()
    )
    assert contract["formula"]["external"]["source_revision"] == pin


def test_pin_without_names_is_every_metric_in_name_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["decision", "pin", str(BASE_ARTIFACT)]) == 0
    manifest = _manifest(BASE_ARTIFACT)
    lines = capsys.readouterr().out.splitlines()
    names = sorted(json.loads(BASE_ARTIFACT.read_bytes())["metrics"], key=lambda m: m["name"])
    assert lines == [f"{manifest.closure_digest(m['name'])}  {m['name']}" for m in names]
    assert len(lines) == 8


def test_pin_says_each_metric_once_in_the_order_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["decision", "pin", str(BASE_ARTIFACT), "revenue", REF, "revenue"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[1] for line in lines] == ["revenue", REF]


def test_pin_json_is_the_only_thing_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["decision", "pin", str(BASE_ARTIFACT), REF, "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "artifact_digest": digest_bytes(BASE_ARTIFACT.read_bytes()),
        "schema_version": "0.213.0",
        "pins": {REF: _manifest(BASE_ARTIFACT).closure_digest(REF)},
    }


def test_pin_says_each_problem_of_an_artifact_the_reader_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(BASE_ARTIFACT.read_bytes())
    data["metrics"].append(data["metrics"][0])  # a metric named twice
    data["semantic_models"].append(data["semantic_models"][0])  # and a semantic model
    path = tmp_path / "semantic_manifest.json"
    path.write_bytes(json.dumps(data).encode("utf-8"))
    read = read_semantic_manifest(path.read_bytes())
    assert read.manifest is None
    assert len(read.problems) >= 2

    assert main(["decision", "pin", str(path), REF]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == len(read.problems)
    for line, problem in zip(lines, read.problems, strict=True):
        assert line.startswith("error: ")
        assert line.endswith(f": {problem.code} at {problem.path}: {problem.reason}")


def test_pin_of_a_metric_the_artifact_lacks_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["decision", "pin", str(BASE_ARTIFACT), REF, "net_margin", "net_margin"]
    assert main(argv) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert captured.err.rstrip().endswith("holds no metric 'net_margin'")


def test_pin_of_an_artifact_that_cannot_be_read_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["decision", "pin", str(tmp_path / "absent.json")]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read" in captured.err


# --- the read of a semantic ref ----------------------------------------------------------------


def test_the_read_gives_the_closure_each_bound_formula_pins() -> None:
    read = _read("base")
    (node,) = read.snapshot.graph.metrics
    assert node.binding is not None
    assert read.closures == {REF: _manifest(BASE_ARTIFACT).closure(REF)}
    assert sha256_digest(read.closures[REF], allow_float=True) == node.binding.closure_digest
    root = PILOT / "base"
    source = NativeModelSource(
        root / "model.yaml", root / "metrics", semantic_manifest=BASE_ARTIFACT, dbt_database="x"
    )
    assert read.snapshot == source.snapshot()


def test_a_drifted_artifact_gives_the_closure_it_holds() -> None:
    read = _read("candidate-drift", CANDIDATE_ARTIFACT)
    (node,) = read.snapshot.graph.metrics
    assert node.binding is not None
    assert node.binding.closure_digest != node.binding.source_revision
    assert read.closures == {REF: _manifest(CANDIDATE_ARTIFACT).closure(REF)}


@pytest.mark.parametrize("given", ["none", "refused", "lacking"])
def test_no_closure_where_the_artifact_gave_none(tmp_path: Path, given: str) -> None:
    artifact: Path | None = None
    if given != "none":
        artifact = tmp_path / "semantic_manifest.json"
        data = json.loads(BASE_ARTIFACT.read_bytes())
        if given == "refused":
            data["metrics"].append(data["metrics"][0])
        else:
            data["metrics"] = [m for m in data["metrics"] if m["name"] != REF]
        artifact.write_bytes(json.dumps(data).encode("utf-8"))
    read = _read("base", artifact)
    (node,) = read.snapshot.graph.metrics
    assert node.binding is not None
    assert node.binding.closure_digest is None
    assert read.closures == {}


def test_the_artifact_is_read_once_for_the_snapshot_and_its_closures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[Path] = []
    read_bytes = Path.read_bytes

    def counted(self: Path) -> bytes:
        reads.append(self)
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    read = _read("base")
    assert reads.count(BASE_ARTIFACT) == 1
    assert list(read.closures) == [REF]


def test_a_ref_without_a_dbt_formula_reads_an_artifact_into_its_source_digest_alone(
    tmp_path: Path,
) -> None:
    root = Case(tmp_path).base
    plain = NativeModelSource(root / "model.yaml", root / "metrics").read()
    read = NativeModelSource(
        root / "model.yaml", root / "metrics", semantic_manifest=BASE_ARTIFACT, dbt_database="x"
    ).read()
    assert read.closures == {}
    assert read.snapshot.graph == plain.snapshot.graph
    assert read.snapshot.canonical_digest == plain.snapshot.canonical_digest
    assert read.snapshot.source_digest != plain.snapshot.source_digest


def _two_contracts(tmp_path: Path) -> Path:
    """A copy of the base ref with a second contract of the same dbt metric."""
    root = tmp_path / "ref"
    shutil.copytree(PILOT / "base", root)
    contract = yaml.safe_load((root / "metrics" / "gross_margin_pct.yaml").read_bytes())
    contract["metadata"]["id"] = "metric://sales/gross_margin_pct_copy"
    (root / "metrics" / "copy.yaml").write_text(
        yaml.safe_dump(contract, allow_unicode=True), encoding="utf-8"
    )
    return root


def test_two_contracts_of_one_dbt_metric_share_its_closure(tmp_path: Path) -> None:
    read = _read(_two_contracts(tmp_path))
    bindings = [node.binding for node in read.snapshot.graph.metrics]
    assert len(bindings) == 2
    assert {binding.ref for binding in bindings if binding is not None} == {REF}
    assert read.closures == {REF: _manifest(BASE_ARTIFACT).closure(REF)}


# --- the gate's inputs -------------------------------------------------------------------------


def _inputs(base: SnapshotRead, candidate: SnapshotRead, **closures: Any) -> GateInputs:
    loaded = load_contracts([PILOT / "contracts"])
    model = yaml.safe_load((PILOT / "candidate-repinned" / "model.yaml").read_bytes())
    return GateInputs(
        product=next(iter(loaded.products.values())),
        validation=loaded.report,
        base=base.snapshot,
        candidate=candidate.snapshot,
        candidate_model=SemanticModel.model_validate(model),
        base_closures=closures.get("base_closures", base.closures),
        candidate_closures=closures.get("candidate_closures", candidate.closures),
    )


def test_the_gate_takes_the_closures_its_snapshots_bind() -> None:
    inputs = _inputs(_read("base"), _read("candidate-repinned", CANDIDATE_ARTIFACT))
    assert list(inputs.base_closures) == [REF]
    assert inputs.base_closures != inputs.candidate_closures


@pytest.mark.parametrize(
    ("given", "message"),
    [
        ("none", "the candidate closures are not those of the refs its snapshot binds"),
        ("another ref too", "the candidate closures are not those of the refs its snapshot binds"),
        ("the base's", "the candidate closure of 'gross_margin_pct' is not the one bound"),
    ],
)
def test_the_gate_refuses_closures_its_snapshot_does_not_bind(given: str, message: str) -> None:
    base = _read("base")
    candidate = _read("candidate-repinned", CANDIDATE_ARTIFACT)
    closures = {
        "none": {},
        "another ref too": {
            **candidate.closures,
            "revenue": _manifest(CANDIDATE_ARTIFACT).closure("revenue"),
        },
        "the base's": base.closures,
    }[given]
    with pytest.raises(ValueError, match=re.escape(message)):
        _inputs(base, candidate, candidate_closures=closures)


@pytest.mark.parametrize("which", [0, -1], ids=["first", "last"])
def test_the_gate_refuses_a_closure_one_binding_of_its_ref_does_not_address(
    tmp_path: Path, which: int
) -> None:
    read = _read(_two_contracts(tmp_path))
    graph = read.snapshot.graph.model_copy(deep=True)
    # either binding may be the one that misses: neither the first nor the last one wins
    bound = [
        i
        for i, node in enumerate(graph.metrics)
        if node.binding is not None and node.binding.ref == REF
    ]
    assert len(bound) == 2
    index = bound[which]
    binding = graph.metrics[index].binding
    assert binding is not None
    other = _manifest(CANDIDATE_ARTIFACT).closure_digest(REF)
    graph.metrics[index] = graph.metrics[index].model_copy(
        update={"binding": binding.model_copy(update={"closure_digest": other})}
    )
    payload = graph.model_dump(mode="json")
    snapshot = SemanticSnapshot.model_validate(
        {
            **read.snapshot.model_dump(mode="json"),
            "graph": payload,
            "canonical_digest": sha256_digest(payload),
        }
    )
    tampered = SnapshotRead(snapshot, read.closures)
    with pytest.raises(ValueError, match="the base closure of 'gross_margin_pct' is not the one"):
        _inputs(tampered, _read("base"))


def test_a_snapshot_without_a_closure_takes_none() -> None:
    base = _read("base", None)
    with pytest.raises(ValueError, match="the base closures are not those"):
        _inputs(base, _read("base"), base_closures=_read("base").closures)


# --- decision verify ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["base", "candidate"])
def test_verify_without_the_artifact_names_the_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    evidence = tmp_path / "evidence"
    argv = _argv("repinned", evidence, base=side != "base", candidate=side != "candidate")
    assert main(argv) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert METRIC in captured.err
    assert captured.err.rstrip().endswith(f"give the artifact with --{side}-semantic-manifest")
    assert not evidence.exists()


@pytest.mark.parametrize("side", ["base", "candidate"])
def test_verify_without_the_artifact_asks_the_engine_nothing(tmp_path: Path, side: str) -> None:
    asked: list[Any] = []
    folder, artifact, _, _ = CASES["repinned"]
    request = VerifyRequest(
        contract_paths=[PILOT / "contracts"],
        base=PILOT / "base",
        candidate=PILOT / folder,
        evidence_dir=tmp_path / "evidence",
        created_at=CREATED_AT,
        dashboards_dir=PILOT / "dashboards",
        probe=lambda questions: asked.append(questions),
        base_dbt=None if side == "base" else DbtArtifact(BASE_ARTIFACT, "x"),
        candidate_dbt=None if side == "candidate" else DbtArtifact(artifact, "x"),
    )
    with pytest.raises(VerifyError, match=f"give the artifact with --{side}-semantic-manifest"):
        verify(request)
    assert asked == []
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("side", ["base", "candidate"])
def test_a_dbt_database_without_its_artifact_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    argv = _argv("repinned", tmp_path / "evidence", **{side: False})
    assert main([*argv, f"--{side}-dbt-database", "x"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert f"--{side}-dbt-database is given without --{side}-semantic-manifest" in err


def test_an_artifact_that_is_not_there_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _argv("repinned", tmp_path / "evidence", candidate=False)
    absent = tmp_path / "absent.json"
    assert main([*argv, "--candidate-semantic-manifest", str(absent)]) == EXIT_ERROR
    assert "dbt semantic manifest not found" in capsys.readouterr().err
    assert not (tmp_path / "evidence").exists()


def test_an_artifact_that_is_a_directory_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _argv("repinned", tmp_path / "evidence", candidate=False)
    folder = tmp_path / "artifact"
    folder.mkdir()
    assert main([*argv, "--candidate-semantic-manifest", str(folder)]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "dbt semantic manifest is not a file" in err and "not found" not in err
    assert not (tmp_path / "evidence").exists()


def _deny_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A copy of the candidate artifact this process may not open, on any platform."""
    denied = tmp_path / "artifact.json"
    shutil.copyfile(CANDIDATE_ARTIFACT, denied)
    original = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self == denied:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    return denied


def test_an_artifact_that_cannot_be_read_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    denied = _deny_artifact(monkeypatch, tmp_path)
    argv = _argv("repinned", tmp_path / "evidence", candidate=False)
    assert main([*argv, "--candidate-semantic-manifest", str(denied)]) == EXIT_ERROR
    assert capsys.readouterr().err.rstrip().endswith(f"cannot read {denied}: PermissionError")
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_pilot_bundles_are_what_verify_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    _, _, run, exit_code = CASES[case]
    evidence = tmp_path / "evidence"
    assert main(_argv(case, evidence)) == exit_code
    assert [path.name for path in evidence.iterdir()] == [run]
    assert _files(evidence / run) == _files(PILOT / "evidence" / case / run)


def test_each_side_keeps_the_closure_its_snapshot_binds() -> None:
    store = FilesystemArtifactStore(PILOT / "evidence" / "repinned")
    bundle = read_bundle(store, RUN)
    kept = {a.artifact_id: a for a in bundle.artifacts if a.kind is ArtifactKind.FORMULA_CLOSURE}
    snapshots = {a.artifact_id: a for a in bundle.artifacts}
    for side, artifact in (("base", BASE_ARTIFACT), ("candidate", CANDIDATE_ARTIFACT)):
        stored = kept.pop(closure_artifact_id(side, REF))
        assert stored.artifact_id == ref_id(f"closure-{side}", REF)
        assert stored.artifact_id.startswith(f"closure-{side}-gross-margin-pct-")
        assert stored.media_type == "application/json"
        assert stored.classification.value == "internal"
        closure = _manifest(artifact).closure(REF)
        assert store.get(RUN, stored.path) == canonical_bytes(closure, allow_float=True)
        snapshot = json.loads(store.get(RUN, snapshots[f"snapshot-{side}"].path))
        (node,) = snapshot["graph"]["metrics"]
        assert stored.digest == node["binding"]["closure_digest"]
        assert stored.digest == _manifest(artifact).closure_digest(REF)
    assert kept == {}


def test_a_run_without_a_bound_formula_keeps_no_closure(tmp_path: Path) -> None:
    report = Case(tmp_path).run()
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    assert [a for a in bundle.artifacts if a.kind is ArtifactKind.FORMULA_CLOSURE] == []


# --- re-verifying a bundle ---------------------------------------------------------------------


def _bundle(tmp_path: Path) -> tuple[FilesystemArtifactStore, Path]:
    root = tmp_path / "evidence"
    shutil.copytree(PILOT / "evidence" / "repinned", root)
    return FilesystemArtifactStore(root), root / RUN


def _rewrite(run: Path, change: Callable[[list[dict[str, Any]]], None]) -> None:
    """Change the manifest's artifacts and store it canonical, as a writer would have."""
    data = json.loads((run / "manifest.json").read_bytes())
    change(data["artifacts"])
    (run / "manifest.json").write_bytes(EvidenceBundle.model_validate(data).canonical_bytes())


def _blob(run: Path, payload: bytes) -> dict[str, Any]:
    digest = digest_bytes(payload)
    path = f"blobs/sha256/{digest.removeprefix('sha256:')}"
    (run / path).write_bytes(payload)
    return {"digest": digest, "size_bytes": len(payload), "path": path}


def _artifact(artifacts: list[dict[str, Any]], artifact_id: str) -> dict[str, Any]:
    return next(a for a in artifacts if a["artifact_id"] == artifact_id)


def _problems(store: FilesystemArtifactStore) -> list[tuple[str, str]]:
    return [(p.code, p.severity.value) for p in verify_bundle(store, RUN)]


def test_the_pilot_bundles_verify() -> None:
    for case, (_, _, run, _) in CASES.items():
        assert verify_bundle(FilesystemArtifactStore(PILOT / "evidence" / case), run) == []


def test_bundles_written_before_closures_raise_no_closure_problem() -> None:
    # `*/evidence/run-*`: the pilot of this slice keeps its runs one level deeper
    pilots = ROOT / "docs" / "pilot"
    runs = [run for run in sorted(pilots.glob("*/evidence/run-*")) if run.is_dir()]
    assert len(runs) >= 6
    for run in runs:
        problems = verify_bundle(FilesystemArtifactStore(run.parent), run.name)
        assert [p.code for p in problems if p.code.startswith("evidence.closure_")] == []


def test_a_closure_the_bundle_lost_is_missing(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    lost = closure_artifact_id("candidate", REF)
    _rewrite(run, lambda artifacts: artifacts.remove(_artifact(artifacts, lost)))
    (problem,) = verify_bundle(store, RUN)
    assert (problem.code, problem.severity.value) == ("evidence.closure_missing", "block")
    pin = _manifest(CANDIDATE_ARTIFACT).closure_digest(REF)
    assert problem.detail == (
        f"{lost}: a binding names the closure {pin} of {REF!r}, and the bundle keeps none"
    )


def test_a_closure_no_binding_names_is_unbound(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    stray = closure_artifact_id("base", "revenue")

    def add(artifacts: list[dict[str, Any]]) -> None:
        artifacts.append(
            {**_artifact(artifacts, closure_artifact_id("base", REF)), "artifact_id": stray}
        )

    _rewrite(run, add)
    (problem,) = verify_bundle(store, RUN)
    assert (problem.code, problem.severity.value) == ("evidence.closure_unbound", "block")
    assert problem.detail.startswith(f"{stray}: ")


def test_a_closure_other_than_the_one_bound_is_a_mismatch(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    kept = closure_artifact_id("base", REF)

    def swap(artifacts: list[dict[str, Any]]) -> None:
        other = _artifact(artifacts, closure_artifact_id("candidate", REF))
        _artifact(artifacts, kept).update(
            digest=other["digest"], size_bytes=other["size_bytes"], path=other["path"]
        )

    _rewrite(run, swap)
    (problem,) = verify_bundle(store, RUN)
    assert (problem.code, problem.severity.value) == ("evidence.closure_mismatch", "block")
    held = _manifest(CANDIDATE_ARTIFACT).closure_digest(REF)
    bound = _manifest(BASE_ARTIFACT).closure_digest(REF)
    assert problem.detail == (
        f"{kept}: the closure of {REF!r} hashes to {held}, its bindings name {bound}"
    )


def test_a_closure_one_binding_of_its_ref_does_not_name_is_a_mismatch(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    held = _manifest(CANDIDATE_ARTIFACT).closure_digest(REF)
    other = _manifest(BASE_ARTIFACT).closure_digest(REF)
    payload = json.dumps(
        {
            "graph": {
                "metrics": [
                    {"id": METRIC, "binding": {"ref": REF, "closure_digest": held}},
                    {"id": f"{METRIC}_copy", "binding": {"ref": REF, "closure_digest": other}},
                ]
            }
        }
    ).encode()
    _rewrite(run, lambda a: _artifact(a, "snapshot-candidate").update(_blob(run, payload)))
    (problem,) = verify_bundle(store, RUN)
    assert (problem.code, problem.severity.value) == ("evidence.closure_mismatch", "block")
    bound = ", ".join(sorted([held, other]))
    assert problem.detail == (
        f"{closure_artifact_id('candidate', REF)}: the closure of {REF!r} hashes to {held}, "
        f"its bindings name {bound}"
    )


def test_an_artifact_of_another_kind_is_no_snapshot_of_its_side(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    _rewrite(
        run,
        lambda a: _artifact(a, "snapshot-candidate").update(kind=ArtifactKind.BI_CONTRACT.value),
    )
    assert _problems(store) == [("evidence.closure_unbound", "block")]


def test_verify_bundle_holds_no_payload_past_its_hash_but_the_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = _bundle(tmp_path)
    held: list[set[str]] = []
    check = evidence_manifest._closure_problems

    def spy(bundle: EvidenceBundle, intact: dict[str, bytes]) -> Any:
        held.append(set(intact))
        return check(bundle, intact)

    monkeypatch.setattr(evidence_manifest, "_closure_problems", spy)
    assert verify_bundle(store, RUN) == []
    assert held == [{"snapshot-base", "snapshot-candidate"}]


def _snapshot_payload(**binding: Any) -> bytes:
    return json.dumps({"graph": {"metrics": [{"id": METRIC, "binding": binding}]}}).encode()


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"{",
        b"[]",
        b'{"graph": []}',
        b'{"graph": {}}',
        b'{"graph": {"metrics": {}}}',
        b'{"graph": {"metrics": [1]}}',
        b'{"graph": {"metrics": [{"binding": "x"}]}}',
        _snapshot_payload(ref=1, closure_digest=None),
        _snapshot_payload(closure_digest=None),
        _snapshot_payload(ref=REF, closure_digest=1),
        _snapshot_payload(ref="\ud800", closure_digest=None),
        _snapshot_payload(ref=REF, closure_digest="sha256:\udfff"),
    ],
)
def test_a_snapshot_whose_bindings_cannot_be_read_leaves_its_side_unchecked(
    tmp_path: Path, payload: bytes
) -> None:
    store, run = _bundle(tmp_path)
    _rewrite(run, lambda a: _artifact(a, "snapshot-candidate").update(_blob(run, payload)))
    (problem,) = verify_bundle(store, RUN)
    assert (problem.code, problem.severity.value) == ("evidence.closure_unverified", "block")
    assert problem.detail == (
        "snapshot-candidate holds no graph of metrics, so the closures of the candidate side "
        "are not checked"
    )


@pytest.mark.parametrize(
    "payload",
    [
        b'{"graph": {"metrics": []}}',
        b'{"graph": {"metrics": [{"binding": null}]}}',
        _snapshot_payload(ref=REF, closure_digest=None),
    ],
)
def test_a_snapshot_that_binds_no_closure_leaves_a_kept_one_unbound(
    tmp_path: Path, payload: bytes
) -> None:
    store, run = _bundle(tmp_path)
    _rewrite(run, lambda a: _artifact(a, "snapshot-candidate").update(_blob(run, payload)))
    assert _problems(store) == [("evidence.closure_unbound", "block")]


def test_the_other_side_is_still_checked(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    lost = closure_artifact_id("base", REF)

    def change(artifacts: list[dict[str, Any]]) -> None:
        _artifact(artifacts, "snapshot-candidate").update(_blob(run, b"[]"))
        artifacts.remove(_artifact(artifacts, lost))

    _rewrite(run, change)
    assert _problems(store) == [
        ("evidence.closure_unverified", "block"),
        ("evidence.closure_missing", "block"),
    ]


def test_a_snapshot_that_does_not_hash_is_not_read_for_closures(tmp_path: Path) -> None:
    store, run = _bundle(tmp_path)
    snapshot = next(
        a for a in read_bundle(store, RUN).artifacts if a.artifact_id == "snapshot-candidate"
    )
    (run / snapshot.path).write_bytes(b"[]")
    assert _problems(store) == [("evidence.digest_mismatch", "block")]


def test_decision_evidence_reports_a_lost_closure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, run = _bundle(tmp_path)
    lost = closure_artifact_id("base", REF)
    _rewrite(run, lambda artifacts: artifacts.remove(_artifact(artifacts, lost)))
    assert main(["decision", "evidence", str(run)]) == 4
    out = capsys.readouterr().out
    assert "evidence.closure_missing" in out
    assert "verdict: block" in out


# --- decision capture --------------------------------------------------------------------------


def _engine(monkeypatch: pytest.MonkeyPatch, asked: list[str]) -> None:
    from decimal import Decimal

    from auto_bi.verification.result_probe import QueryColumn, QueryResult

    def target(engine: str) -> tuple[Any, str, str]:
        def factory() -> Any:
            asked.append(engine)
            return lambda _sql: QueryResult(
                columns=(
                    QueryColumn("region", "String", nullable=False),
                    QueryColumn("gross_margin_pct", "Decimal(18, 6)", nullable=False),
                ),
                rows=(("Москва", Decimal("0.412000")),),
            )

        return factory, ENVIRONMENT_REF, "auto_bi_ro"

    monkeypatch.setattr("auto_bi.cli._result_capture_target", target)


def _capture_argv(case: Case, output: Path, *extra: str) -> list[str]:
    assert case.assets is not None
    return [
        "decision",
        "capture",
        str(case.assets),
        "--candidate",
        str(PILOT / "candidate-repinned"),
        "--asset",
        ASSET_ID,
        "--dwh",
        "clickhouse",
        "--output",
        str(output),
        "--captured-at",
        CREATED_AT,
        *extra,
    ]


def test_capture_attests_the_candidate_its_artifact_resolves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    _engine(monkeypatch, asked)
    case = Case(tmp_path).with_asset()
    output = tmp_path / "captured.yaml"
    dbt = [
        "--candidate-semantic-manifest",
        str(CANDIDATE_ARTIFACT),
        "--candidate-dbt-database",
        "x",
    ]
    assert main(_capture_argv(case, output, *dbt)) == 0
    assert capsys.readouterr().err == ""
    assert asked == ["clickhouse"]
    recorded = yaml.safe_load(output.read_text(encoding="utf-8"))[ASSET_ID]
    digest = _read("candidate-repinned", CANDIDATE_ARTIFACT).snapshot.canonical_digest
    assert recorded["attestation"]["semantic_digest"] == digest
    # the candidate the pilot's verify graded
    store = FilesystemArtifactStore(PILOT / "evidence" / "repinned")
    diff = next(
        a for a in read_bundle(store, RUN).artifacts if a.artifact_id == "check-semantic.diff"
    )
    assert json.loads(store.get(RUN, diff.path))["candidate_digest"] == digest


def test_capture_without_the_artifact_names_the_option_and_asks_no_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    _engine(monkeypatch, asked)
    case = Case(tmp_path).with_asset()
    output = tmp_path / "captured.yaml"
    assert main(_capture_argv(case, output)) == EXIT_ERROR
    err = capsys.readouterr().err
    assert METRIC in err
    assert err.rstrip().endswith("give the artifact with --candidate-semantic-manifest")
    assert asked == []
    assert not output.exists()


def test_capture_takes_no_dbt_database_without_its_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    _engine(monkeypatch, asked)
    case = Case(tmp_path).with_asset()
    output = tmp_path / "captured.yaml"
    assert main(_capture_argv(case, output, "--candidate-dbt-database", "x")) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "--candidate-dbt-database is given without --candidate-semantic-manifest" in err
    assert asked == []


def test_capture_names_an_artifact_that_cannot_be_read_and_asks_no_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    _engine(monkeypatch, asked)
    denied = _deny_artifact(monkeypatch, tmp_path)
    case = Case(tmp_path).with_asset()
    output = tmp_path / "captured.yaml"
    dbt = ["--candidate-semantic-manifest", str(denied), "--candidate-dbt-database", "x"]
    assert main(_capture_argv(case, output, *dbt)) == EXIT_ERROR
    assert capsys.readouterr().err.rstrip().endswith(f"cannot read {denied}: PermissionError")
    assert asked == []
    assert not output.exists()


# --- ids and versions --------------------------------------------------------------------------


def test_a_letter_outside_ascii_is_folded_like_any_other() -> None:
    assert slugify("decision://sales/weekly_margin@1") == "decision-sales-weekly-margin-1"
    assert slugify("маржа_net") == "net"
    assert slugify("маржа") == "ref"
    # the Kelvin sign and a dotted capital I lower to ASCII letters, and are folded first
    assert slugify("\u212a_\u0130_net") == "net"
    artifact_id = closure_artifact_id("base", "валовая_маржа")
    assert re.fullmatch(r"closure-base-ref-[0-9a-f]{64}", artifact_id)


def test_the_versions() -> None:
    assert int(VERIFIER_VERSION) >= 129
    assert int(CHECK_VERSION) >= 83

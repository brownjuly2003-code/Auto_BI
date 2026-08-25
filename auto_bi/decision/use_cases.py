"""Application services for the DecisionOps CLI (DECISIONOPS_ARCHITECTURE §6, §12.1).

This is the only layer that reads the working tree. It resolves the paths a `decision
verify` invocation names into the loaded inputs §8.1 lists, then hands them to
`verification/release_gate.py`, which decides nothing about where they came from. A REST
`/api/v2` handler later calls the same function with the same request object.

Everything a run depends on is a file in Git: the contracts, both semantic refs, the
delivered dashboard specs, the verified assets and — for now — the candidate results. That
is what makes the gate reproducible offline; nothing here opens a socket of its own. The one
live input is §8.2 step 7's environment probe, and it arrives as an injected callable
(`EnvironmentProbe`) rather than as connection settings, so an offline run is still the
default and the caller that does hold a read-only client decides when to hand one in.

`decision evidence` reads from the same layer for the same reason: resolving `<run-or-path>`
to one run directory is a filesystem question, and `evidence/inspect.py` — which decides
whether a bundle is still trustworthy — must not have to answer it.

A **semantic ref** is a directory holding `model.yaml`, with metric contracts in an
optional `metrics/` beside it. A **dashboard spec** is named after the ref that pins it —
`weekly_margin@7.yaml` for `dashboard://weekly_margin@7` — so a product pinned to version 7
cannot silently load version 8's layout. A missing one is a finding, not a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.manifest import MANIFEST_PATH
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore
from auto_bi.ir.spec import DashboardSpec
from auto_bi.refs import ContractRef
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.semantic.model import SemanticModel
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    questions_for,
)
from auto_bi.verification.reconcile import ResultSet
from auto_bi.verification.release_gate import (
    GateInputs,
    GateReport,
    NoResults,
    RecordedResults,
    ResultSource,
    run_gate,
)
from auto_bi.verification.repository import VerifiedAssetRepository

MODEL_FILE = "model.yaml"
METRICS_DIR = "metrics"

_SPEC_SUFFIXES = (".yaml", ".yml", ".json")


class VerifyError(Exception):
    """A usage or input error — the run never started, so there is no verdict (exit 2).

    Shared by every `decision` use case: pointing at a directory that holds no bundle is
    the same class of answer as pointing at one that holds no product.
    """


#: Asks a live engine the questions §8.2 step 7 derives from the product and its
#: candidate snapshot. One call, typed facts back, no state in between.
EnvironmentProbe = Callable[[EnvironmentQuestions], EnvironmentFacts]


@dataclass(frozen=True)
class VerifyRequest:
    contract_paths: Sequence[str | Path]
    base: str | Path
    candidate: str | Path
    evidence_dir: str | Path
    created_at: str
    dashboards_dir: str | Path | None = None
    assets_dir: str | Path | None = None
    results_file: str | Path | None = None
    environment_ref: str = ""
    #: Names the product to verify when the paths hold more than one.
    product_id: str = ""
    #: Asks a running engine §8.2 step 7's questions. Absent means the run has no engine to
    #: ask and the gate says so; the callable is injected rather than built here so a caller
    #: that already holds a read-only client — or a test — needs no connection settings.
    probe: EnvironmentProbe | None = None


def verify(request: VerifyRequest) -> GateReport:
    """Load everything §8.1 names and run the gate over it."""
    loaded = load_contracts(request.contract_paths)
    product = _select_product(loaded.products, request.product_id)

    base = _snapshot(Path(request.base))
    candidate_root = Path(request.candidate)
    candidate = _snapshot(candidate_root)
    candidate_model = _load_model(candidate_root / MODEL_FILE)

    inputs = GateInputs(
        product=product,
        validation=loaded.report,
        base=base,
        candidate=candidate,
        candidate_model=candidate_model,
        dashboards=_load_dashboards(request.dashboards_dir, product),
        assets=_load_assets(request.assets_dir),
        results=_load_results(request.results_file),
        environment_ref=request.environment_ref,
        environment=_probe(request.probe, product, candidate),
    )
    store = FilesystemArtifactStore(request.evidence_dir)
    return run_gate(inputs, store=store, created_at=request.created_at)


def _probe(
    probe: EnvironmentProbe | None, product: DecisionProductSpec, candidate: SemanticSnapshot
) -> EnvironmentFacts | None:
    """Ask the engine about the marts this product reads — or nothing, if none was given.

    The questions are derived twice, here and inside the gate. They are a pure function of
    the same product and snapshot, so the second derivation cannot disagree with the first,
    and it keeps the bundle's payload self-contained.
    """
    if probe is None:
        return None
    return probe(questions_for(product, candidate))


def _select_product(
    products: dict[str, DecisionProductSpec], product_id: str
) -> DecisionProductSpec:
    """One run verifies one product: the idempotency key and the bundle both name one."""
    if product_id:
        product = products.get(product_id)
        if product is None:
            known = ", ".join(sorted(products)) or "none"
            raise VerifyError(f"no decision product {product_id!r} in the given paths ({known})")
        return product
    if not products:
        raise VerifyError("no decision product contract found in the given paths")
    if len(products) > 1:
        known = ", ".join(sorted(products))
        raise VerifyError(
            f"the given paths hold {len(products)} decision products ({known}); "
            "name the one to verify with --product"
        )
    return next(iter(products.values()))


def _snapshot(root: Path) -> SemanticSnapshot:
    model_path = root / MODEL_FILE
    if not model_path.is_file():
        raise VerifyError(f"semantic ref {root} has no {MODEL_FILE}")
    metrics = root / METRICS_DIR
    source = NativeModelSource(model_path, metrics if metrics.is_dir() else None)
    return source.snapshot()


def _load_model(path: Path) -> SemanticModel:
    try:
        return SemanticModel.model_validate(_read_mapping(path))
    except ValueError as exc:
        raise VerifyError(f"{path} is not a valid semantic model: {exc}") from None


def _load_dashboards(
    directory: str | Path | None, product: DecisionProductSpec
) -> dict[str, DashboardSpec]:
    """Resolve each pinned delivery ref to its spec file; absent is the gate's finding."""
    if directory is None:
        return {}
    root = Path(directory)
    if not root.is_dir():
        raise VerifyError(f"dashboard specs directory not found: {root}")
    specs: dict[str, DashboardSpec] = {}
    for delivery in product.delivery.dashboards:
        path = _dashboard_file(root, delivery.ref)
        if path is None:
            continue
        try:
            specs[delivery.ref] = DashboardSpec.model_validate(_read_mapping(path))
        except ValueError as exc:
            raise VerifyError(f"{path} is not a valid DashboardSpec: {exc}") from None
    return specs


def _dashboard_file(root: Path, ref: str) -> Path | None:
    """`dashboard://weekly_margin@7` -> `weekly_margin@7.yaml`, and nothing else.

    Dropping the version from the file name would let a product pinned to 7 load 8's layout
    — the exact drift a pinned ref exists to prevent (§7.1).
    """
    parsed = ContractRef.parse(ref)
    stem = f"{parsed.path[-1]}@{parsed.version}"
    for suffix in _SPEC_SUFFIXES:
        candidate = root / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _load_assets(directory: str | Path | None) -> VerifiedAssetRepository:
    if directory is None:
        return VerifiedAssetRepository([])
    root = Path(directory)
    if not root.exists():
        raise VerifyError(f"verified assets path not found: {root}")
    try:
        return VerifiedAssetRepository.load(root)
    except ValueError as exc:
        raise VerifyError(f"{root} holds an invalid verified asset: {exc}") from None


def _load_results(path: str | Path | None) -> ResultSource:
    """Candidate answers recorded outside the gate, as `{asset id: ResultSet}`."""
    if path is None:
        return NoResults()
    file = Path(path)
    if not file.is_file():
        raise VerifyError(f"recorded results file not found: {file}")
    raw = _read_mapping(file)
    try:
        return RecordedResults({key: ResultSet.model_validate(value) for key, value in raw.items()})
    except ValueError as exc:
        raise VerifyError(f"{file} holds an invalid recorded result: {exc}") from None


@dataclass(frozen=True)
class EvidenceLocation:
    """Where one bundle lives: the store that owns its paths, and the run inside it."""

    store: FilesystemArtifactStore
    run_id: str


def locate_evidence(path: str | Path, run_id: str = "") -> EvidenceLocation:
    """Resolve `<run-or-path>` (§12.1) to exactly one run.

    Both addresses are accepted because both are what people have: the evidence root a
    pipeline was pointed at, and the single run directory someone copied out of it. What is
    not accepted is a guess — a root holding several runs names them and asks, because
    reading the wrong bundle is worse than reading none.
    """
    root = Path(path)
    if (root / MANIFEST_PATH).is_file():  # the run directory itself
        if run_id and run_id != root.name:
            raise VerifyError(f"{root} is run {root.name!r}, not {run_id!r}")
        return EvidenceLocation(FilesystemArtifactStore(root.parent), root.name)
    if not root.is_dir():
        raise VerifyError(f"evidence path not found: {root}")

    store = FilesystemArtifactStore(root)
    if run_id:
        try:
            store.run_root(run_id)  # the store owns the rule; here it is a usage error
        except ArtifactStoreError as exc:
            raise VerifyError(str(exc)) from None
        if not (root / run_id / MANIFEST_PATH).is_file():
            raise VerifyError(f"run {run_id!r} under {root} has no {MANIFEST_PATH}")
        return EvidenceLocation(store, run_id)

    runs = sorted(child.name for child in root.iterdir() if (child / MANIFEST_PATH).is_file())
    if not runs:
        raise VerifyError(
            f"no evidence bundle under {root}: no run directory holds {MANIFEST_PATH}"
        )
    if len(runs) > 1:
        raise VerifyError(
            f"{root} holds {len(runs)} runs ({', '.join(runs)}); " "name the one to read with --run"
        )
    return EvidenceLocation(store, runs[0])


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise VerifyError(f"cannot read {path}: {type(exc).__name__}") from None
    if not isinstance(raw, dict):
        raise VerifyError(f"{path} must contain a mapping")
    return raw

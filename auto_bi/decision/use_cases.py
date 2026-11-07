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

A ref whose contracts take a formula from a dbt `semantic_manifest.json` is read with that
artifact (`DbtArtifact`), and without it is refused: `decision verify` names one for each
side, `decision capture` one for the candidate, and both read it with one loader
(ADR 0008, item 11).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from auto_bi import filesystem
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import (
    LoadedContracts,
    RefusedProduct,
    ValidationReport,
    load_contracts,
)
from auto_bi.engine import is_known_engine, sqlglot_dialect
from auto_bi.evidence.manifest import MANIFEST_PATH, EvidenceConflictError
from auto_bi.evidence.signing import EvidenceSignatureError, EvidenceSigner
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore
from auto_bi.ir.spec import DashboardSpec
from auto_bi.refs import ContractRef
from auto_bi.scalars import Timestamp
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot, SnapshotRead
from auto_bi.semantic.model import SemanticModel
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    questions_for,
)
from auto_bi.verification.reconcile import ResultSet
from auto_bi.verification.release_gate import (
    EvidenceReadBackError,
    GateInputs,
    GateReport,
    NoResults,
    RecordedResult,
    RecordedResults,
    ResultSource,
    run_gate,
)
from auto_bi.verification.repository import (
    ExpectationKind,
    VerifiedAsset,
    VerifiedAssetFileError,
    VerifiedAssetRepository,
)
from auto_bi.verification.result_probe import (
    ResultCaptureError,
    ResultQuery,
)
from auto_bi.verification.result_probe import (
    capture as capture_live_result,
)
from auto_bi.yaml_document import load_yaml_document, schema_reason, unread_reason

MODEL_FILE = "model.yaml"
METRICS_DIR = "metrics"

_SPEC_SUFFIXES = (".yaml", ".yml", ".json")
#: How many unread paths a refusal that found no product names before it counts the rest.
_MAX_UNREAD_NAMED = 3
#: How many product files the schema refused it names before it counts the rest.
_MAX_REFUSED_NAMED = 3

#: The moment a run or a capture is stated to be of, as a manifest and an attestation take it.
_MOMENT: TypeAdapter[str] = TypeAdapter(Timestamp)
#: Where the environment and the actor of a capture come from, for the refusal of either.
_CAPTURE_ENVIRONMENT = "--environment, or the configured host:port/database it defaults to,"
_CAPTURE_ACTOR = "the configured DWH user"
_BASE_DBT = "--base-semantic-manifest"
_CANDIDATE_DBT = "--candidate-semantic-manifest"


class VerifyError(Exception):
    """A usage or input error — the run never started, so there is no verdict (exit 2).

    Shared by every `decision` use case: pointing at a directory that holds no bundle is
    the same class of answer as pointing at one that holds no product.
    """


class EnvironmentProbeError(Exception):
    """The engine a run was to ask §8.2 step 7's questions did not answer them.

    A probe raises it for what its driver raised: a connection refused, a session lost, a
    question the engine failed. What the engine refused is not raised — that is a fact of
    the environment, and the gate grades it. The use case ends the run on it as a usage
    error: an engine that is not there is no environment the gate could say anything of,
    and a verdict without the facts the run was asked to gather is not the one asked for.

    The message is one line: the class of what the driver raised and what it said, each
    run of whitespace as one space — or the class alone, when it says nothing.
    """

    def __init__(self, engine: str, target: str, error: Exception) -> None:
        said = " ".join(str(error).split())
        raised = f"{type(error).__name__}: {said}" if said else type(error).__name__
        super().__init__(f"{engine} at {target}: {raised}")


#: Asks a live engine the questions §8.2 step 7 derives from the product and its
#: candidate snapshot. One call, typed facts back, no state in between; an engine that
#: did not answer is raised as `EnvironmentProbeError`.
EnvironmentProbe = Callable[[EnvironmentQuestions], EnvironmentFacts]
ResultQueryFactory = Callable[[], ResultQuery]


@dataclass(frozen=True)
class DbtArtifact:
    """The dbt `semantic_manifest.json` the formulas of one semantic ref are resolved from.

    `database` is the dbt database declared with it: empty for an engine with two-part names,
    `None` when none is declared (ADR 0008, item 3).
    """

    path: str | Path
    database: str | None = None


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
    #: Optional private-key port. Key bytes stay outside the request and evidence bundle.
    signer: EvidenceSigner | None = None
    #: The dbt artifact of each side; required for a side whose contracts take a formula from one.
    base_dbt: DbtArtifact | None = None
    candidate_dbt: DbtArtifact | None = None


@dataclass(frozen=True)
class CaptureRequest:
    """Filesystem and live-query inputs for one reviewed result capture."""

    assets: str | Path
    candidate: str | Path
    output: str | Path
    engine: str
    environment_ref: str
    executed_by: str
    captured_at: str
    query_factory: ResultQueryFactory
    asset_id: str = ""
    #: The candidate's dbt artifact, read as `decision verify` reads it for the candidate.
    candidate_dbt: DbtArtifact | None = None


@dataclass(frozen=True)
class CaptureReport:
    """The local artifact written by :func:`capture_result`."""

    asset_id: str
    output: Path
    row_count: int
    candidate_digest: str


def verify(request: VerifyRequest) -> GateReport:
    """Load everything §8.1 names and run the gate over it."""
    # What the request states of the run itself is settled before anything is read. A moment
    # the manifest does not take, or an environment evidence has no form for, would end the
    # run after its inputs were loaded and its checks graded, with nothing to show for it.
    _check_moment("--created-at", request.created_at)
    _check_text("--environment", request.environment_ref)

    loaded = load_contracts(request.contract_paths)
    product = _select_product(loaded, request.product_id)

    base = _snapshot(Path(request.base), request.base_dbt, _BASE_DBT)
    candidate_root = Path(request.candidate)
    candidate = _snapshot(candidate_root, request.candidate_dbt, _CANDIDATE_DBT)
    candidate_model = _load_model(candidate_root / MODEL_FILE)

    dashboards = _load_dashboards(request.dashboards_dir, product)
    repository = (
        _asset_repository(Path(request.assets_dir))
        if request.assets_dir is not None
        else VerifiedAssetRepository([])
    )

    inputs = GateInputs(
        product=product,
        validation=loaded.report,
        validated_metrics=loaded.metrics,
        base=base.snapshot,
        candidate=candidate.snapshot,
        base_closures=base.closures,
        candidate_closures=candidate.closures,
        candidate_model=candidate_model,
        dashboards=dashboards,
        assets=_load_assets(repository, product),
        results=_load_results(request.results_file, repository),
        environment_ref=request.environment_ref,
        # Asked last: a run refused for any input above opens no session with the engine.
        environment=_probe(request.probe, product, candidate.snapshot),
    )
    store = FilesystemArtifactStore(request.evidence_dir)
    try:
        return run_gate(
            inputs,
            store=store,
            created_at=request.created_at,
            signer=request.signer,
        )
    except (EvidenceConflictError, EvidenceSignatureError) as exc:
        # A usage error, not a verdict: the verification did not get to record itself.
        raise VerifyError(str(exc)) from None
    except ArtifactStoreError as exc:
        # Nor did it here: the store refused a path, or the filesystem refused the store.
        # What was written before the refusal has no manifest and is not a bundle.
        raise VerifyError(
            f"cannot write the evidence bundle under {request.evidence_dir}: {exc}"
        ) from None
    except EvidenceReadBackError as exc:
        # Nor here, where the bundle is written: the run was refused the second reading of
        # it, and a bundle nobody read back is reported neither as verified nor as invalid.
        raise VerifyError(
            f"cannot read back the evidence bundle under "
            f"{filesystem.display_path(request.evidence_dir)}: {exc}"
        ) from None


def capture_result(request: CaptureRequest) -> CaptureReport:
    """Execute one selected asset and write a new gate-ready recorded-results YAML file."""
    # What the capture is attested with is settled first: no file is read and no engine is
    # asked for a result whose attestation could not be written, or could not be read back.
    _check_moment("--captured-at", request.captured_at)
    _check_text(_CAPTURE_ENVIRONMENT, request.environment_ref)
    _check_text(_CAPTURE_ACTOR, request.executed_by)

    output = Path(request.output)
    try:
        taken = filesystem.exists(output)
    except OSError as exc:
        # Under a directory that may not be searched the look is refused, and the write
        # would be: whether the file is there is not known, and no engine is asked for a
        # result there is nowhere to put.
        raise VerifyError(f"cannot write captured result {output}: {exc}") from None
    if taken:
        raise VerifyError(f"output file already exists: {output}; refusing to overwrite it")

    repository = _asset_repository(Path(request.assets))
    asset = _select_asset(repository, request.asset_id, Path(request.assets))
    _check_capture_contract(asset, request.engine)
    candidate = _snapshot(Path(request.candidate), request.candidate_dbt, _CANDIDATE_DBT).snapshot

    try:
        recorded = capture_live_result(
            asset,
            request.query_factory(),
            candidate_digest=candidate.canonical_digest,
            environment_ref=request.environment_ref,
            executed_by=request.executed_by,
            captured_at=request.captured_at,
        )
    except (ResultCaptureError, ValueError) as exc:
        raise VerifyError(f"cannot capture {asset.metadata.id}: {exc}") from None

    payload = yaml.safe_dump(
        {asset.metadata.id: recorded.model_dump(mode="json")},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        line_break="\n",
    )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except FileExistsError:
        raise VerifyError(
            f"output file already exists: {output}; refusing to overwrite it"
        ) from None
    except OSError as exc:
        raise VerifyError(f"cannot write captured result {output}: {exc}") from None

    return CaptureReport(
        asset_id=asset.metadata.id,
        output=output,
        row_count=len(recorded.result.rows),
        candidate_digest=candidate.canonical_digest,
    )


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
    try:
        return probe(questions_for(product, candidate))
    except EnvironmentProbeError as exc:
        raise VerifyError(f"cannot ask {exc}") from None


def _select_product(loaded: LoadedContracts, product_id: str) -> DecisionProductSpec:
    """One run verifies one product: the idempotency key and the bundle both name one.

    That the paths hold no such product is known only of what was read, so the refusal
    names what was not: the product may be in a directory that could not be listed or in
    a file that could not be read. Before that it names what was read and is no product: a
    product file its schema refused — of the id asked for, or any, when none was.
    """
    products = loaded.products
    if product_id:
        product = products.get(product_id)
        if product is None:
            known = ", ".join(sorted(products)) or "none"
            refused = [r for r in loaded.refused_products if r.declared == product_id]
            raise VerifyError(
                f"no decision product {product_id!r} in the given paths ({known})"
                f"{_refused_products(refused)}{_unread_paths(loaded.report)}"
            )
        return product
    if not products:
        raise VerifyError(
            "no decision product contract found in the given paths"
            f"{_refused_products(loaded.refused_products)}{_unread_paths(loaded.report)}"
        )
    if len(products) > 1:
        known = ", ".join(sorted(products))
        raise VerifyError(
            f"the given paths hold {len(products)} decision products ({known}); "
            "name the one to verify with --product"
        )
    return next(iter(products.values()))


def _refused_products(refused: Sequence[RefusedProduct]) -> str:
    """The decision products of the validated paths their schema refused, and why, if any."""
    if not refused:
        return ""
    named = ", ".join(_refused_product(product) for product in refused[:_MAX_REFUSED_NAMED])
    rest = len(refused) - _MAX_REFUSED_NAMED
    more = f" (+{rest} more)" if rest > 0 else ""
    return f"; {len(refused)} decision product(s) failed the schema: {named}{more}"


def _refused_product(product: RefusedProduct) -> str:
    where = filesystem.display_path(product.source)
    if product.declared is None:
        return f"{where} ({product.reason})"
    return f"{product.declared!r} in {where} ({product.reason})"


def _unread_paths(report: ValidationReport) -> str:
    """What the validated paths hold that the report could not read, if anything.

    Each path is spelled as a diagnostic line spells one, as the file of a refused product is.
    """
    unread = [issue for issue in report.issues if issue.code == "contract.unreadable"]
    if not unread:
        return ""
    named = ", ".join(
        f"{filesystem.display_path(issue.source)} ({issue.message})"
        for issue in unread[:_MAX_UNREAD_NAMED]
    )
    rest = len(unread) - _MAX_UNREAD_NAMED
    more = f" (+{rest} more)" if rest > 0 else ""
    return f"; {len(unread)} path(s) could not be read: {named}{more}"


def _asset_repository(path: Path) -> VerifiedAssetRepository:
    """Every verified asset under *path*, for `verify` and for `capture` alike.

    A file that cannot be read or holds no asset is named as that file, and so are a path
    under *path* that may not be looked at while its files are listed and a directory
    under it that cannot be listed — which is no directory without assets. Only what is
    wrong with the assets together — two of one id — is said of the path.
    """
    if not _looked_at(path, filesystem.exists):
        raise VerifyError(f"verified assets path not found: {path}")
    try:
        return VerifiedAssetRepository.load(path)
    except VerifiedAssetFileError as exc:
        raise VerifyError(str(exc)) from None
    except OSError as exc:
        raise VerifyError(f"cannot read {exc.filename or path}: {unread_reason(exc)}") from None
    except ValueError as exc:
        raise VerifyError(f"{path} holds an invalid verified asset: {_refusal(exc)}") from None


def _select_asset(
    repository: VerifiedAssetRepository, asset_id: str, source: Path
) -> VerifiedAsset:
    if asset_id:
        asset = repository.get(asset_id)
        if asset is None:
            known = ", ".join(item.metadata.id for item in repository.assets) or "none"
            raise VerifyError(f"no verified asset {asset_id!r} under {source} ({known})")
        return asset
    if not repository.assets:
        raise VerifyError(f"no verified asset under {source}")
    if len(repository.assets) > 1:
        known = ", ".join(item.metadata.id for item in repository.assets)
        raise VerifyError(
            f"{source} holds {len(repository.assets)} verified assets ({known}); "
            "name the one to capture with --asset"
        )
    return repository.assets[0]


def _check_capture_contract(asset: VerifiedAsset, engine: str) -> None:
    if not is_known_engine(engine):
        raise VerifyError(f"unknown capture engine {engine!r}")
    if asset.expectation.kind is not ExpectationKind.RESULT or asset.expectation.result is None:
        raise VerifyError(f"{asset.metadata.id} has no expected result to capture")
    query = asset.reference_query
    if query is None:
        raise VerifyError(f"{asset.metadata.id} has no reviewed reference_query")
    if sqlglot_dialect(query.engine) != sqlglot_dialect(engine):
        raise VerifyError(
            f"{asset.metadata.id} uses {query.engine}, but capture requested {engine}; "
            "the reviewed query and DWH driver must be from the same engine family"
        )


def _snapshot(root: Path, dbt: DbtArtifact | None, option: str) -> SnapshotRead:
    """One semantic ref, frozen with its dbt artifact — or the file that kept it from being read.

    A metric contract of the ref that is read and is no contract is a gap of the snapshot
    and the gate grades it. A `model.yaml` that cannot be read or is no model leaves nothing
    to freeze, and neither does a file of the ref that cannot be opened at all: there are
    no bytes of it for the snapshot to say what it was read from. Nor can a ref be frozen
    whose files are read and accepted and hold a value its graph cannot be addressed with.
    Each of these is said in one line.

    The dbt artifact is read once, for the snapshot and the closures evidence keeps. An
    artifact the reader refuses is bytes the snapshot read, and its problems are the gate's
    gaps; a path that holds no file is said here. A ref whose contracts take a formula from a
    dbt artifact, read without one, is refused with the *option* that gives it: a snapshot that
    could not resolve the formula is no input the run was meant to have (ADR 0008, item 11).
    """
    model_path = root / MODEL_FILE
    manifest = None if dbt is None else Path(dbt.path)
    try:
        if not filesystem.is_file(model_path):
            raise VerifyError(f"semantic ref {root} has no {MODEL_FILE}")
        if manifest is not None and not filesystem.is_file(manifest):
            raise VerifyError(f"dbt semantic manifest not found: {manifest}")
        metrics = root / METRICS_DIR
        source = NativeModelSource(
            model_path,
            metrics if filesystem.is_dir(metrics) else None,
            semantic_manifest=manifest,
            dbt_database=None if dbt is None else dbt.database,
        )
        read = source.read()
    except OSError as exc:
        # The model, its metrics directory, a directory under it, a contract in one or the
        # dbt artifact, refused when looked at, when listed or when opened: the error knows
        # which path it was.
        unread = exc.filename or model_path
        raise VerifyError(f"cannot read {unread}: {unread_reason(exc)}") from None
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise VerifyError(f"cannot read {model_path}: {unread_reason(exc)}") from None
    except ValueError as exc:
        if not isinstance(exc, ValidationError):
            # Said of the ref: which of its files held the value, the error does not know,
            # and the model is not to be blamed for a contract beside it.
            raise VerifyError(f"cannot load semantic ref {root}: {_refusal(exc)}") from None
        if exc.title == SemanticModel.__name__:
            raise VerifyError(
                f"{model_path} is not a valid semantic model: {schema_reason(exc)}"
            ) from None
        # The refusal of another schema, raised while the graph of the ref was built: it
        # is said of the ref as well, with the name of the schema that refused — the
        # places of its refusals are places of what that schema describes, not of a file.
        raise VerifyError(
            f"cannot load semantic ref {root}: not a valid {exc.title}: {schema_reason(exc)}"
        ) from None
    if dbt is None:
        nodes = read.snapshot.graph.metrics
        bound = sorted({node.id for node in nodes if node.binding is not None})
        if bound:
            raise VerifyError(
                f"semantic ref {root} takes the formula of {', '.join(bound)} from a dbt "
                f"semantic manifest; give the artifact with {option}"
            )
    return read


def _load_model(path: Path) -> SemanticModel:
    try:
        return SemanticModel.model_validate(_read_mapping(path))
    except ValueError as exc:
        raise VerifyError(f"{path} is not a valid semantic model: {_refusal(exc)}") from None


def _load_dashboards(
    directory: str | Path | None, product: DecisionProductSpec
) -> dict[str, DashboardSpec]:
    """Resolve each pinned delivery ref to its spec file; absent is the gate's finding."""
    if directory is None:
        return {}
    root = Path(directory)
    if not _looked_at(root, filesystem.is_dir):
        raise VerifyError(f"dashboard specs directory not found: {root}")
    specs: dict[str, DashboardSpec] = {}
    for delivery in product.delivery.dashboards:
        path = _dashboard_file(root, delivery.ref)
        if path is None:
            continue
        try:
            specs[delivery.ref] = DashboardSpec.model_validate(_read_mapping(path))
        except ValueError as exc:
            raise VerifyError(f"{path} is not a valid DashboardSpec: {_refusal(exc)}") from None
    return specs


def _dashboard_file(root: Path, ref: str) -> Path | None:
    """`dashboard://sales/weekly_margin@7` -> `sales/weekly_margin@7.yaml`, and nothing else.

    Dropping the version from the file name would let a product pinned to 7 load 8's layout
    — the exact drift a pinned ref exists to prevent (§7.1). Dropping a segment would let
    `dashboard://ops/weekly_margin@7` load the layout of `dashboard://sales/weekly_margin@7`,
    so every segment but the last is a directory. A segment is lowercase letters, digits,
    `_` and `-` (`auto_bi.refs`): the text of the path stays under *root*, and no two refs
    spell one path. Links are followed, as by every look at an input, so a symlink or a
    junction under *root* may still lead the path out of it, or two refs to one file.
    """
    parsed = ContractRef.parse(ref)
    *directories, name = parsed.path
    stem = f"{name}@{parsed.version}"
    for suffix in _SPEC_SUFFIXES:
        candidate = root.joinpath(*directories, f"{stem}{suffix}")
        if _looked_at(candidate, filesystem.is_file):
            return candidate
    return None


def _load_assets(
    repository: VerifiedAssetRepository, product: DecisionProductSpec
) -> VerifiedAssetRepository:
    """The repository, narrowed to the assets that name one of the product's metrics.

    The run verifies one product. An asset of another product's metric is neither its
    evidence nor its finding: graded here, its invalidation would put that product's review
    on this one's verdict, and its comparison would seal a claim about a number this product
    never delivers. Versions are ignored on both sides, as `resolve_impact` ignores them —
    whether the product's pin still resolves is graded by the gate's `contracts.validate` step.
    """
    in_scope = {str(ContractRef.parse(ref).without_version()) for ref in product.scope.metrics}
    return VerifiedAssetRepository(
        [asset for asset in repository.assets if in_scope.intersection(asset.metric_ids())]
    )


def _load_results(path: str | Path | None, repository: VerifiedAssetRepository) -> ResultSource:
    """Candidate answers recorded outside the gate, keyed by verified-asset id.

    Two shapes, and which one a file uses is read from the file rather than from a flag:

    ```yaml
    asset://…:                       # bare: the result and nothing about its origin
      columns: [...]
      rows: [...]
    asset://…:                       # attested: the result and where it came from
      result: {columns: [...], rows: [...]}
      attestation: {origin: query_run, captured_at: ..., query_digest: ...}
    ```

    The bare shape stays valid because a file written before attestations existed is still a
    usable input; the gate grades it as `provenance.unattested` rather than refusing to read
    it. The two shapes cannot be confused — a `ResultSet` has no `result` key — so the
    detection is a fact about the document, not a guess about intent.

    Validate every value before checking keys, retaining the first malformed result's
    diagnostic. Then require exact asset ids in the full supplied repository, including
    assets outside the selected product. Unknown keys must not silently discard answers.
    """
    if path is None:
        return NoResults()
    file = Path(path)
    if not _looked_at(file, filesystem.is_file):
        raise VerifyError(f"recorded results file not found: {file}")
    results: dict[str, RecordedResult] = {}
    for key, value in _read_mapping(file).items():
        try:
            results[key] = _recorded_result(value)
        except ValueError as exc:
            raise VerifyError(
                f"{file} holds an invalid recorded result for {key!r}: {_refusal(exc)}"
            ) from None
    known_ids = {asset.metadata.id for asset in repository.assets}
    for key in results:
        if key not in known_ids:
            raise VerifyError(
                f"{filesystem.display_path(file)} holds a recorded result "
                f"for unknown verified asset {key!r}"
            )
    return RecordedResults(results)


def _recorded_result(value: Any) -> RecordedResult:
    if isinstance(value, Mapping) and "result" in value:
        return RecordedResult.model_validate(value)
    return RecordedResult(result=ResultSet.model_validate(value))


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

    Nor is a refusal read as an absence. A path that may not be looked at is not a path
    that is not there, and a directory under the root that may not be looked into is not
    one that holds no run: each is the usage error of a path the command cannot read.
    """
    root = Path(path)
    if _looked_at(root / MANIFEST_PATH, filesystem.is_file):  # the run directory itself
        if run_id and run_id != root.name:
            raise VerifyError(
                f"{filesystem.display_path(root)} is run {root.name!r}, not {run_id!r}"
            )
        return EvidenceLocation(FilesystemArtifactStore(root.parent), root.name)
    if not _looked_at(root, filesystem.is_dir):
        raise VerifyError(f"evidence path not found: {filesystem.display_path(root)}")

    store = FilesystemArtifactStore(root)
    if run_id:
        try:
            store.run_root(run_id)  # the store owns the rule; here it is a usage error
        except ArtifactStoreError as exc:
            raise VerifyError(str(exc)) from None
        if not _looked_at(root / run_id / MANIFEST_PATH, filesystem.is_file):
            raise VerifyError(
                f"run {run_id!r} under {filesystem.display_path(root)} has no {MANIFEST_PATH}"
            )
        return EvidenceLocation(store, run_id)

    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        raise VerifyError(
            f"cannot read {filesystem.display_path(root)}: {unread_reason(exc)}"
        ) from None
    runs = [name for name in names if _holds_run(root / name)]
    if not runs:
        raise VerifyError(
            f"no evidence bundle under {filesystem.display_path(root)}: "
            f"no run directory holds {MANIFEST_PATH}"
        )
    if len(runs) > 1:
        raise VerifyError(
            f"{filesystem.display_path(root)} holds {len(runs)} runs "
            f"({', '.join(filesystem.display_path(name) for name in runs)}); "
            "name the one to read with --run"
        )
    return EvidenceLocation(store, runs[0])


def _holds_run(directory: Path) -> bool:
    """Whether *directory*, found under an evidence root, holds a manifest.

    A directory that may not be looked into is not passed over. It may hold the run that
    was meant, or a second one, and which run is read would then be a guess. Naming the run
    takes the question away, and the error says so.
    """
    manifest = directory / MANIFEST_PATH
    try:
        return filesystem.is_file(manifest)
    except OSError as exc:
        raise VerifyError(
            f"cannot read {filesystem.display_path(manifest)}: {unread_reason(exc)}; "
            "name the run to read with --run"
        ) from None


def _looked_at(path: Path, look: Callable[[Path], bool]) -> bool:
    """What *look* says of *path* — or the usage error of a path that may not be looked at.

    Under a directory that may not be searched the look itself is refused. That is not
    "the path is not there": read as that, a run input that is where it was said to be
    would be named as missing, and a dashboard spec that is there would become the gate's
    finding of one that is absent.
    """
    try:
        return look(path)
    except OSError as exc:
        raise VerifyError(
            f"cannot read {filesystem.display_path(path)}: {unread_reason(exc)}"
        ) from None


def _check_moment(name: str, moment: str) -> None:
    """Refuse a stated moment that is not the timestamp evidence carries.

    The manifest and an attestation hold one form of a moment, and ages are counted between
    two of them; any other text is refused by the name it was stated under. The moment is
    not quoted: it may hold a line break or a lone surrogate, and a usage error is one line
    that can be printed.
    """
    try:
        _MOMENT.validate_python(moment)
    except ValidationError:
        raise VerifyError(
            f"{name} must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z"
        ) from None


def _check_text(name: str, text: str) -> None:
    """Refuse stated text evidence has no form for: a lone surrogate.

    A command line is decoded so that a byte which is no UTF-8 becomes one. Canonical JSON
    has no form for it, and the YAML a capture writes spells it as an escape no command
    reads back. The text is not quoted either.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise VerifyError(f"{name} must be text without a lone surrogate") from None


def _refusal(exc: ValueError) -> str:
    """Why a file that was read is not taken, in the one line a usage error is.

    Of what a schema refuses: where and why for its first refusals, without the values and
    the links its own listing spreads over lines. Of anything else that was raised: what it
    says, each run of whitespace as one space — or its class, when it says nothing.
    """
    if isinstance(exc, ValidationError):
        return schema_reason(exc)
    return " ".join(str(exc).split()) or type(exc).__name__


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = load_yaml_document(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise VerifyError(f"cannot read {path}: {unread_reason(exc)}") from None
    if not isinstance(raw, dict):
        raise VerifyError(f"{path} must contain a mapping")
    return raw

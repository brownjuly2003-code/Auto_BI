"""The tools of the DecisionOps MCP server (ADR 0012, D5): the `decision` use cases, as tools.

Each tool calls the use case its `decision` command calls and answers with what the command
prints: the JSON of `--json`, put under a key where the command prints no object — the list of
`runs` under `runs`, the text of an artifact under `payload`. A call the command would end with
exit 2 — an input refused,
no verdict reached — is a tool execution error (`is_error`), and its text is the command's
error line. A verdict is never an error: `block` is an answer. So is a bundle that fails its
re-verification: `evidence` answers with the inspection, as the command does. The one other
error is the command's other exit 4 without an inspection: an artifact read out of a bundle
whose manifest is not the digest the call pinned.

`impact` is the one tool without a command of its own: the `impact.products` step of
`decision verify`, run without the rest (`decision/use_cases.py`, `impact`).

The tools write nothing but the bundles of `verify`, and only into the directory the server
was started with — `verify` is offered only then. They reach no BI and, unlike
`decision verify --dwh`, no engine. Paths are read as the command reads them, relative to
the working directory of the server, with the rights of the user who started it: the server
is no sandbox. The keys a bundle is signed and trusted with are the server's configuration,
never a call's argument.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import ConfigDict, Field, ValidationError

from auto_bi.decision.use_cases import (
    DbtArtifact,
    ImpactRequest,
    VerifyError,
    VerifyRequest,
    impact,
    locate_evidence,
    verify,
)
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.signing import EvidenceSignatureError, EvidenceSigner, EvidenceVerifier
from auto_bi.strict import StrictModel
from auto_bi.yaml_document import schema_reason

_Path = Annotated[str, Field(min_length=1)]


class _Arguments(StrictModel):
    """The arguments of a call: these names, of these JSON types, and no other."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ValidateArguments(_Arguments):
    paths: list[_Path] = Field(
        min_length=1, description="Contract files or directories (YAML or JSON)"
    )


class ImpactArguments(_Arguments):
    paths: list[_Path] = Field(
        min_length=1, description="Contract files or directories (YAML or JSON)"
    )
    base: _Path = Field(description="Base semantic ref: a directory holding model.yaml")
    candidate: _Path = Field(description="Candidate semantic ref: a directory holding model.yaml")
    base_semantic_manifest: _Path | None = Field(
        default=None,
        description="dbt semantic_manifest.json the base ref's dbt formulas are resolved from",
    )
    base_dbt_database: str | None = Field(
        default=None,
        description="dbt database of the base artifact's relations; empty for two-part names",
    )
    candidate_semantic_manifest: _Path | None = Field(
        default=None,
        description="dbt semantic_manifest.json the candidate ref's dbt formulas are "
        "resolved from",
    )
    candidate_dbt_database: str | None = Field(
        default=None,
        description="dbt database of the candidate artifact's relations; empty for "
        "two-part names",
    )
    dashboards: _Path | None = Field(
        default=None, description="Directory of delivered DashboardSpecs"
    )
    product: str = Field(
        default="", description="Product id; the paths hold several and one is meant"
    )


class VerifyArguments(ImpactArguments):
    assets: _Path | None = Field(
        default=None, description="Verified asset repository (file or directory)"
    )
    results: _Path | None = Field(
        default=None,
        description="Recorded candidate results: {asset id: ResultSet | {result, attestation}}",
    )
    environment: str = Field(default="", description="DWH/BI environment ref for the run key")
    created_at: str = Field(
        default="",
        description="RFC 3339 UTC bundle timestamp; the current time when empty",
    )


class EvidenceArguments(_Arguments):
    path: _Path = Field(description="Evidence directory, or one run directory inside it")
    run: str = Field(default="", description="Run id, when the directory holds several")
    artifact: str = Field(
        default="", description="Read this artifact's payload instead of the inspection"
    )
    expect_digest: str = Field(
        default="", description="Manifest digest the bundle must have; a mismatch blocks"
    )


class RunsArguments(_Arguments):
    path: _Path = Field(description="Evidence root containing run directories")


@dataclass(frozen=True)
class Outcome:
    """What a call answers: one JSON object, and whether it is a tool execution error."""

    structured: dict[str, Any]
    is_error: bool = False

    @classmethod
    def error(cls, message: str, **context: Any) -> Outcome:
        return cls({"error": message, **context}, is_error=True)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    arguments: type[_Arguments]
    read_only: bool
    run: Callable[[ToolBox, Any], Outcome]

    def definition(self) -> dict[str, Any]:
        """The tool as `tools/list` lists it."""
        annotations: dict[str, Any] = {"title": self.title, "readOnlyHint": self.read_only}
        if not self.read_only:
            annotations |= {"destructiveHint": False, "idempotentHint": False}
        annotations["openWorldHint"] = False
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": input_schema(self.arguments),
            "annotations": annotations,
        }


def input_schema(arguments: type[_Arguments]) -> dict[str, Any]:
    """The JSON Schema of *arguments*, as pydantic writes it, without its titles."""
    schema = arguments.model_json_schema()
    schema.pop("title", None)
    for spec in schema["properties"].values():
        spec.pop("title", None)
    return schema


@dataclass(frozen=True)
class ToolBox:
    """The tools of one server, and what it was started with.

    `evidence_dir` is where `verify` writes its bundles; without it there is no `verify`.
    `signer` seals them, `verifier` is the trust root `evidence` checks signatures with.
    """

    evidence_dir: Path | None = None
    signer: EvidenceSigner | None = None
    verifier: EvidenceVerifier | None = None
    clock: Callable[[], str] = field(default=_now)

    def tools(self) -> list[Tool]:
        """The tools this server offers, in the order `tools/list` gives them."""
        return [tool for tool in _TOOLS if tool.name != "verify" or self.evidence_dir is not None]

    def find(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools() if tool.name == name), None)

    def call(self, tool: Tool, arguments: Mapping[str, Any]) -> Outcome:
        """Run *tool* with *arguments*; arguments its schema refuses are an execution error."""
        try:
            parsed = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            return Outcome.error(f"invalid arguments: {schema_reason(exc)}")
        return tool.run(self, parsed)


def _dbt(path: str | None, database: str | None, side: str) -> DbtArtifact | None:
    """The artifact of *side*, refused as the command refuses a database without one."""
    if path is None:
        if database is not None:
            raise VerifyError(f"{side}_dbt_database is given without {side}_semantic_manifest")
        return None
    return DbtArtifact(path, database)


def _validate(box: ToolBox, arguments: ValidateArguments) -> Outcome:
    report = validate_paths(arguments.paths).model_dump(mode="json")
    if not report["products"]:
        return Outcome.error("no decision product contract found in the given paths", report=report)
    return Outcome(report)


def _impact(box: ToolBox, arguments: ImpactArguments) -> Outcome:
    try:
        answer = impact(
            ImpactRequest(
                contract_paths=arguments.paths,
                base=arguments.base,
                candidate=arguments.candidate,
                dashboards_dir=arguments.dashboards,
                product_id=arguments.product,
                base_dbt=_dbt(
                    arguments.base_semantic_manifest, arguments.base_dbt_database, "base"
                ),
                candidate_dbt=_dbt(
                    arguments.candidate_semantic_manifest,
                    arguments.candidate_dbt_database,
                    "candidate",
                ),
            )
        )
    except VerifyError as exc:
        return Outcome.error(str(exc))
    return Outcome(answer.model_dump(mode="json"))


def _verify(box: ToolBox, arguments: VerifyArguments) -> Outcome:
    assert box.evidence_dir is not None  # the tool is offered only with one
    try:
        report = verify(
            VerifyRequest(
                contract_paths=arguments.paths,
                base=arguments.base,
                candidate=arguments.candidate,
                evidence_dir=box.evidence_dir,
                created_at=arguments.created_at or box.clock(),
                dashboards_dir=arguments.dashboards,
                assets_dir=arguments.assets,
                results_file=arguments.results,
                environment_ref=arguments.environment,
                product_id=arguments.product,
                signer=box.signer,
                base_dbt=_dbt(
                    arguments.base_semantic_manifest, arguments.base_dbt_database, "base"
                ),
                candidate_dbt=_dbt(
                    arguments.candidate_semantic_manifest,
                    arguments.candidate_dbt_database,
                    "candidate",
                ),
            )
        )
    except (VerifyError, EvidenceSignatureError) as exc:
        return Outcome.error(str(exc))
    return Outcome(report.model_dump(mode="json"))


def _evidence(box: ToolBox, arguments: EvidenceArguments) -> Outcome:
    from auto_bi.evidence.inspect import EvidenceReadError, inspect_bundle, read_artifact

    try:
        location = locate_evidence(arguments.path, arguments.run)
        if arguments.artifact:
            artifact, payload = read_artifact(
                location.store,
                location.run_id,
                arguments.artifact,
                expected_digest=arguments.expect_digest or None,
                verifier=box.verifier,
            )
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                return Outcome.error(
                    f"{artifact.artifact_id} is not text ({artifact.media_type}, "
                    f"{artifact.size_bytes} bytes); it is stored at {artifact.path}"
                )
            return Outcome({"artifact": artifact.model_dump(mode="json"), "payload": text})
        inspection = inspect_bundle(
            location.store,
            location.run_id,
            expected_digest=arguments.expect_digest or None,
            verifier=box.verifier,
        )
    except (VerifyError, EvidenceReadError, EvidenceSignatureError) as exc:
        return Outcome.error(str(exc))
    return Outcome(inspection.model_dump(mode="json"))


def _runs(box: ToolBox, arguments: RunsArguments) -> Outcome:
    from auto_bi.decision.runs import FilesystemRunRepository, RunDiscoveryError

    try:
        rows = FilesystemRunRepository(arguments.path).list_runs()
    except RunDiscoveryError as exc:
        return Outcome.error(str(exc))
    return Outcome({"runs": [row.model_dump(mode="json") for row in rows]})


_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="validate",
        title="Validate decision contracts",
        description=(
            "Validate the decision products under the paths against their metric contracts, "
            "as `auto_bi decision validate` does. Answers its JSON report; the verdict is in "
            "it. Reads files only."
        ),
        arguments=ValidateArguments,
        read_only=True,
        run=_validate,
    ),
    Tool(
        name="impact",
        title="Impact of a semantic change",
        description=(
            "Diff the base and candidate semantic refs and say which decision products, "
            "dashboards, owners and charts each change reaches, as `auto_bi decision verify` "
            "resolves it in its impact.products step, for every product under the paths or "
            "the one named. No verdict, no evidence: reads files only."
        ),
        arguments=ImpactArguments,
        read_only=True,
        run=_impact,
    ),
    Tool(
        name="verify",
        title="Run the release gate",
        description=(
            "Run the Analytics Release & Evidence Gate for one decision product, as "
            "`auto_bi decision verify` does without --dwh: offline, no BI and no engine "
            "reached. Writes the evidence bundle into the server's evidence directory and "
            "answers the gate report; the verdict is in it."
        ),
        arguments=VerifyArguments,
        read_only=False,
        run=_verify,
    ),
    Tool(
        name="evidence",
        title="Re-verify an evidence bundle",
        description=(
            "Read an evidence bundle back and re-verify every content address, as "
            "`auto_bi decision evidence` does: answers the inspection, whose verdict grades "
            "the bundle, not the release; or, given an artifact id, that artifact's text "
            "payload. Reads files only."
        ),
        arguments=EvidenceArguments,
        read_only=True,
        run=_evidence,
    ),
    Tool(
        name="runs",
        title="List evidence runs",
        description=(
            "List the runs under an evidence root without checking their integrity, as "
            "`auto_bi decision runs` does. Reads files only."
        ),
        arguments=RunsArguments,
        read_only=True,
        run=_runs,
    ),
)

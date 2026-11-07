"""CLI entrypoint: `auto_bi build "<description>"` (Phase 0 happy path, no dialogue)."""

import argparse
import logging
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn, TextIO

from auto_bi import __version__

if TYPE_CHECKING:
    from _typeshed import SupportsWrite
    from rich.console import Console

    from auto_bi.agent.machine import AgentTurn
    from auto_bi.config import Settings
    from auto_bi.decision.use_cases import DbtArtifact, EnvironmentProbe, ResultQueryFactory
    from auto_bi.introspect.base import RunQuery
    from auto_bi.store import Store

logger = logging.getLogger(__name__)


class _HelpArgumentParser(argparse.ArgumentParser):
    """Escape unencodable parser text without changing streams or exit codes."""

    #: Set on `decision` and its commands, and on `auto_bi` for a command line of `decision`:
    #: what the parser refuses is said in one line.
    one_line = False

    def error(self, message: str) -> NoReturn:
        super().error(_one_line(message) if self.one_line else message)

    def _print_message(self, message: str, file: "SupportsWrite[str] | None" = None) -> None:
        try:
            super()._print_message(message, file)
        except UnicodeEncodeError:
            stream = file or sys.stderr
            encoding = getattr(stream, "encoding", None) or "ascii"
            # Keep argparse's handling of non-encoding write errors on both attempts.
            super()._print_message(
                message.encode(encoding, "backslashreplace").decode(encoding), file
            )

    def print_help(self, file: "SupportsWrite[str] | None" = None) -> None:
        stream = sys.stdout if file is None else file
        text = self.format_help()
        try:
            stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(stream, "encoding", None) or "ascii"
            stream.write(text.encode(encoding, "backslashreplace").decode(encoding))


def main(argv: list[str] | None = None) -> int:
    parser = _HelpArgumentParser(prog="auto_bi", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a dashboard from a text description")
    build.add_argument(
        "description",
        nargs="?",
        default="",
        help="Dashboard description in natural language (omit when using --auto)",
    )
    build.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    build.add_argument(
        "--target",
        choices=["superset", "datalens"],
        default="superset",
        help="BI target to build into (default: superset)",
    )
    build.add_argument(
        "--auto",
        metavar="TABLE",
        default="",
        help="Auto-overview: build a curated dashboard from a datamart (no text, no LLM)",
    )
    build.add_argument(
        "--max-charts",
        type=int,
        default=8,
        help="Auto mode: maximum number of charts (default: 8)",
    )

    raw = sub.add_parser(
        "raw",
        help="X-5 escape hatch: build a table dashboard from a raw SELECT (no LLM, no IR)",
    )
    raw.add_argument("--sql-file", required=True, help="File with the SELECT to run (UTF-8)")
    raw.add_argument("--title", default="Raw SQL", help="Dashboard/chart title")
    raw.add_argument(
        "--table",
        default="dm",
        help="Base table/schema label for the dataset name (the SQL itself names its tables)",
    )
    raw.add_argument(
        "--columns",
        default="",
        help="Comma-separated result columns to display (optional; empty => all columns)",
    )
    raw.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    raw.add_argument(
        "--target", choices=["superset"], default="superset", help="BI target (raw: superset only)"
    )

    chat = sub.add_parser("chat", help="Dialogue: clarify -> preview spec -> approve -> build")
    chat.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    intro = sub.add_parser("introspect", help="Introspect DWH and write a draft model.yaml")
    intro.add_argument("--database", default=None, help="Database/schema (default: settings)")
    intro.add_argument("--output", default="semantic/model.yaml", help="Where to write the draft")
    intro.add_argument(
        "--engine",
        choices=["clickhouse", "greenplum"],
        default="clickhouse",
        help="DWH engine to introspect (default: clickhouse; greenplum uses AUTO_BI_GP_*)",
    )

    gaps = sub.add_parser("gaps", help="Deterministic gaps report over an introspected model")
    gaps.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    gaps.add_argument("--output", default="", help="Write markdown here (default: stdout)")
    gaps.add_argument(
        "--offline",
        action="store_true",
        help="Skip live time-grain profiling (no DWH connection)",
    )

    serve = sub.add_parser("serve", help="HTTP API for the web UI (FastAPI + uvicorn)")
    serve.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8200)
    serve.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Root logger level (default: INFO)",
    )
    serve.add_argument(
        "--log-format",
        default="text",
        choices=["text", "json"],
        help="'text' (default) keeps uvicorn's own colored console output; 'json' emits "
        "structured JSON logs (incl. uvicorn's) for a log aggregator",
    )

    ev = sub.add_parser("eval", help="Run the eval suites (advisor: offline; golden: live LLM)")
    ev.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    ev.add_argument("--suite", choices=["advisor", "golden", "all"], default="all")
    ev.add_argument("--cases", default="", help="Comma-separated case ids to run (subset)")
    ev.add_argument(
        "--llm-mode",
        choices=["live", "replay", "record", "refresh-fingerprints"],
        default="live",
        help="golden suite only: 'live' calls the configured provider (default); "
        "'replay' answers from recorded fixtures, offline, enforces prompt fingerprints "
        "(CI); 'record' calls the provider and writes fixtures; "
        "'refresh-fingerprints' offline-stamps prompt/template hashes onto existing "
        "responses (no provider — reviewed procedure before commit)",
    )
    ev.add_argument(
        "--fixtures-dir",
        default="tests/fixtures/golden_llm",
        help="Directory of per-case fixture files for --llm-mode replay/record",
    )

    dbt = sub.add_parser(
        "dbt-import",
        help="Enrich model.yaml from dbt artifacts (descriptions, relationships); "
        "fills EMPTY values only — hand edits always win",
    )
    dbt.add_argument("--manifest", required=True, help="Path to dbt manifest.json")
    dbt.add_argument("--catalog", default="", help="Path to dbt catalog.json (optional)")
    dbt.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    dbt.add_argument(
        "--dry-run", action="store_true", help="Report what would change without writing"
    )

    prune = sub.add_parser(
        "prune",
        help="Delete prior-revision BI artifacts via the ownership ledger "
        "(each session's latest build always survives)",
    )
    prune.add_argument("--session", default=None, help="Prune only this session id")
    prune.add_argument(
        "--dry-run", action="store_true", help="List the delete candidates without deleting"
    )
    prune.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    decision = sub.add_parser(
        "decision", help="DecisionOps contracts, evidence, and read-only result capture"
    )
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    validate = decision_sub.add_parser(
        "validate", help="Validate Git-tracked decision products against their metric contracts"
    )
    validate.add_argument("paths", nargs="+", help="Contract files or directories (YAML or JSON)")
    validate.add_argument(
        "--json", action="store_true", help="Emit the report as JSON and nothing else"
    )

    capture = decision_sub.add_parser(
        "capture",
        help="Execute one verified asset's reviewed SQL and write an attested result",
    )
    capture.add_argument("assets", help="Verified asset repository (file or directory)")
    capture.add_argument(
        "--candidate", required=True, help="Candidate semantic ref: a directory holding model.yaml"
    )
    _dbt_arguments(capture, "candidate")
    capture.add_argument(
        "--asset", default="", help="Asset id, required when the repository holds several"
    )
    capture.add_argument(
        "--dwh",
        required=True,
        choices=["clickhouse", "greenplum", "greengage", "postgres"],
        help="Configured read-only DWH driver used to execute the reviewed query",
    )
    capture.add_argument(
        "--environment",
        default="",
        help="Attested environment ref (default: configured host:port/database)",
    )
    capture.add_argument("--output", required=True, help="New recorded-results YAML file")
    capture.add_argument(
        "--captured-at",
        default="",
        help="RFC 3339 UTC capture timestamp; defaults to the current time",
    )

    verify = decision_sub.add_parser(
        "verify",
        help="Run the Analytics Release & Evidence Gate for one decision product",
    )
    verify.add_argument("paths", nargs="+", help="Contract files or directories (YAML or JSON)")
    verify.add_argument(
        "--base", required=True, help="Base semantic ref: a directory holding model.yaml"
    )
    verify.add_argument(
        "--candidate", required=True, help="Candidate semantic ref: a directory holding model.yaml"
    )
    _dbt_arguments(verify, "base")
    _dbt_arguments(verify, "candidate")
    verify.add_argument(
        "--evidence", required=True, help="Directory the evidence bundle is written to"
    )
    verify.add_argument(
        "--signing-key",
        default="",
        help="Ed25519 PKCS#8 private-key PEM used to seal the evidence manifest",
    )
    verify.add_argument("--dashboards", default=None, help="Directory of delivered DashboardSpecs")
    verify.add_argument("--assets", default=None, help="Verified asset repository (file or dir)")
    verify.add_argument(
        "--results",
        default=None,
        help="Recorded candidate results: {asset id: ResultSet | {result, attestation}}",
    )
    verify.add_argument("--product", default="", help="Product id, when the paths hold several")
    verify.add_argument("--environment", default="", help="DWH/BI environment ref for the run key")
    verify.add_argument(
        "--dwh",
        default="",
        choices=["", "clickhouse", "greenplum", "greengage", "postgres"],
        help="Ask this engine step 7's questions, read-only, using the configured settings",
    )
    verify.add_argument(
        "--created-at",
        default="",
        help="RFC 3339 UTC bundle timestamp; supply it to make the run byte-reproducible",
    )
    verify.add_argument(
        "--json", action="store_true", help="Emit the report as JSON and nothing else"
    )

    evidence = decision_sub.add_parser(
        "evidence",
        help="Read a written evidence bundle back and re-verify every content address",
    )
    evidence.add_argument("path", help="Evidence directory, or one run directory inside it")
    evidence.add_argument("--run", default="", help="Run id, when the directory holds several")
    evidence.add_argument(
        "--artifact", default="", help="Print this artifact's payload and nothing else"
    )
    evidence.add_argument(
        "--expect-digest",
        default="",
        help="Manifest digest the bundle must have; a mismatch blocks",
    )
    evidence.add_argument(
        "--trust-key",
        action="append",
        default=[],
        help="Trusted Ed25519 public-key PEM; repeat during key rotation",
    )
    evidence.add_argument(
        "--json", action="store_true", help="Emit the inspection as JSON and nothing else"
    )

    pin = decision_sub.add_parser(
        "pin",
        help="Print the pin of each dbt metric of a semantic_manifest.json "
        "(external.source_revision of a dbt_semantic_manifest formula)",
    )
    pin.add_argument("manifest", help="dbt semantic_manifest.json")
    pin.add_argument("metrics", nargs="*", help="dbt metric names (default: every metric)")
    pin.add_argument("--json", action="store_true", help="Emit the pins as JSON and nothing else")

    runs = decision_sub.add_parser(
        "runs", help="List local runs without checking evidence integrity"
    )
    runs.add_argument("path", help="Evidence root containing run directories")
    runs.add_argument("--json", action="store_true", help="Emit the catalogue as JSON")

    investigate = decision_sub.add_parser(
        "investigate",
        help="Run a typed investigation over canonical results (offline, no DWH/BI/LLM)",
    )
    investigate.add_argument("plan", help="InvestigationPlan YAML file")
    investigate.add_argument(
        "--results",
        nargs="+",
        required=True,
        help="CanonicalMetricResult YAML files, exactly one per investigated metric",
    )
    investigate.add_argument(
        "--output", required=True, help="New directory for canonical plan/results/brief artifacts"
    )
    for command_parser in (decision, *decision_sub.choices.values()):
        command_parser.one_line = True
    # `auto_bi` refuses what no command takes, quoted after `unrecognized arguments: ` — what a
    # decision command does not take among it — and on CPython 3.12.6 and earlier an option
    # after `decision runs` that could be one of its own, `--=x`. A command line argparse hands
    # to `decision` has it as its first argument that does not begin with `-`: what comes before
    # is options of `auto_bi`, and they take no value.
    arguments = sys.argv[1:] if argv is None else argv
    command = next((argument for argument in arguments if not argument.startswith("-")), None)
    parser.one_line = command == "decision"

    args = parser.parse_args(argv)

    if args.command == "build":
        if args.auto:
            return _build_auto(args.auto, args.model_path, args.target, args.max_charts)
        if not args.description:
            parser.error("build needs a description or --auto <table>")
        return _build(args.description, args.model_path, args.target)
    if args.command == "raw":
        return _build_raw(
            args.sql_file, args.title, args.table, args.columns, args.model_path, args.target
        )
    if args.command == "chat":
        return _chat(args.model_path)
    if args.command == "serve":
        return _serve(args.model_path, args.host, args.port, args.log_level, args.log_format)
    if args.command == "introspect":
        return _introspect(args.database, args.output, args.engine)
    if args.command == "gaps":
        return _gaps(args.model_path, args.output, args.offline)
    if args.command == "eval":
        return _eval(args.model_path, args.suite, args.cases, args.llm_mode, args.fixtures_dir)
    if args.command == "dbt-import":
        return _dbt_import(args.manifest, args.catalog, args.model_path, args.dry_run)
    if args.command == "prune":
        return _prune(args.session, args.dry_run, args.model_path)
    if args.command == "decision":
        if args.decision_command == "runs":
            return _decision_runs(args)
        if args.decision_command == "capture":
            return _decision_capture(args)
        if args.decision_command == "verify":
            return _decision_verify(args)
        if args.decision_command == "evidence":
            return _decision_evidence(args)
        if args.decision_command == "pin":
            return _decision_pin(args)
        if args.decision_command == "investigate":
            return _decision_investigate(args)
        return _decision_validate(args.paths, args.json)
    return 0


def _decision_runs(args: argparse.Namespace) -> int:
    from auto_bi.decision.runs import FilesystemRunRepository, RunDiscoveryError

    try:
        rows = FilesystemRunRepository(args.path).list_runs()
    except RunDiscoveryError as exc:
        _say(_one_line(str(exc)), file=sys.stderr)
        return 2
    if args.json:
        _say_json([row.model_dump(mode="json") for row in rows])
    else:
        _say("Local runs (integrity_checked=false; use decision evidence to verify):")
        for row in rows:
            _say_one_line(
                f"{row.run_id}  {row.product_ref!r}  {row.created_at!r}  "
                f"{row.manifest_digest}  manifest_run_id={row.manifest_run_id}"
            )
    return 0


def _say(text: str, file: TextIO | None = None) -> None:
    """Print one line of a decision command's answer, whatever the stream encodes.

    A stream refuses a character it has no form for, and a command prints what it found: a
    path, an id, a detail. Left to itself that ends the command in a traceback — after the
    bundle is sealed or the artifacts are written — with an exit code that is no verdict.
    The character is spelled by its backslash escape instead, the way the interpreter prints
    to its own error stream; what the stream does encode is printed as it is.
    """
    stream = sys.stdout if file is None else file
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "backslashreplace").decode(encoding), file=stream)


def _one_line(text: str) -> str:
    """*text* with each character that is not printable spelled by its escape: one line.

    A refusal quotes what it refuses — a path found under a directory, a key of a document,
    what a validator or an engine said — and any of them may hold a line break, which splits
    the refusal into lines, or a control a terminal acts on. Each such character is spelled
    as Python escapes it (``\\n``, ``\\x1b``, ``\\u2028``); every other character, a backslash
    among them, is left as it is.
    """
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def _say_error(message: object) -> None:
    """Print why a decision command stopped: one line, after ``error: ``, on standard error."""
    _say(f"error: {_one_line(str(message))}", file=sys.stderr)


def _say_one_line(text: str) -> None:
    """Print a line of an answer that names what the command read: one line, as a refusal is.

    A line of a text answer names a file found under a directory, quotes a key of a document,
    the detail a check wrote of an attestation, a caveat or a version of a bundle, a run of a
    store. Any of them may hold a line break or a carriage return — each makes two lines of one
    for a reader of lines, and the second may read `verdict: pass` — or an escape. Each
    character that is not printable is spelled by its escape (`_one_line`) before `_say` offers
    the line. A JSON answer is not spelled.
    """
    _say(_one_line(text))


def _say_json(document: Any) -> None:
    """Print a JSON answer; in ASCII, by the escapes of JSON, when the stream refuses it.

    A backslash escape of `_say` is not one JSON reads, and JSON has its own for every
    character outside ASCII, a lone surrogate too: the document printed is the same document.
    """
    import json

    try:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(document, ensure_ascii=True, indent=2))


def _dbt_arguments(command: argparse.ArgumentParser, side: str) -> None:
    """The dbt artifact the *side* ref's formulas are resolved from (ADR 0008, item 11)."""
    command.add_argument(
        f"--{side}-semantic-manifest",
        default=None,
        help=f"dbt semantic_manifest.json the {side} ref's dbt_semantic_manifest formulas "
        "are resolved from; required when its contracts have one",
    )
    command.add_argument(
        f"--{side}-dbt-database",
        default=None,
        help=f"dbt database of the {side} artifact's relations, empty for two-part names "
        f"(with --{side}-semantic-manifest)",
    )


def _dbt_artifact(args: argparse.Namespace, side: str) -> "DbtArtifact | None":
    """The *side* artifact the command line names, or a usage error for a database alone."""
    from auto_bi.decision.use_cases import DbtArtifact, VerifyError

    path = getattr(args, f"{side}_semantic_manifest")
    database = getattr(args, f"{side}_dbt_database")
    if path is None:
        if database is not None:
            raise VerifyError(f"--{side}-dbt-database is given without --{side}-semantic-manifest")
        return None
    return DbtArtifact(path, database)


def _decision_pin(args: argparse.Namespace) -> int:
    """`auto_bi decision pin` — the pin a contract writes for a dbt metric (ADR 0008, item 4).

    Each line is the pin of a metric — `sha256:` of the RFC 8785 form of its closure, the
    value of `external.source_revision` — and its name, every metric of the artifact in name
    order when none is named. The artifact is read as the gate reads it: one it refuses is said
    problem by problem, and it and a metric it does not hold exit 2.
    """
    from pathlib import Path

    from auto_bi.semantic.metricflow import read_semantic_manifest
    from auto_bi.verification.model import EXIT_ERROR
    from auto_bi.yaml_document import unread_reason

    path = Path(args.manifest)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _say_error(f"cannot read {path}: {unread_reason(exc)}")
        return EXIT_ERROR
    read = read_semantic_manifest(raw)
    if read.manifest is None:
        for problem in read.problems:
            _say_error(f"{path}: {problem.code} at {problem.path}: {problem.reason}")
        return EXIT_ERROR
    names = list(dict.fromkeys(args.metrics)) or read.manifest.metric_names()
    unknown = [name for name in names if not read.manifest.has_metric(name)]
    if unknown:
        _say_error(f"{path} holds no metric {', '.join(repr(name) for name in unknown)}")
        return EXIT_ERROR
    pins = {name: read.manifest.closure_digest(name) for name in names}
    if args.json:
        _say_json(
            {
                "artifact_digest": read.artifact_digest,
                "schema_version": read.schema_version,
                "pins": pins,
            }
        )
    else:
        for name, pin in pins.items():
            _say_one_line(f"{pin}  {name}")
    return 0


def _decision_validate(paths: list[str], as_json: bool) -> int:
    """`auto_bi decision validate` — offline cross-contract check (ARCHITECTURE §12.1).

    Exit code carries the verdict (0 pass/advisory, 3 needs_review, 4 block); a run that
    validated no product at all exits 2 rather than pretending to pass.
    """
    from auto_bi.decision.validate import validate_paths
    from auto_bi.verification.model import EXIT_ERROR, exit_code_for

    report = validate_paths(paths)
    if as_json:
        _say_json(report.model_dump(mode="json"))
    else:
        for issue in report.issues:
            _say_one_line(f"{issue.severity.value:<8} {issue.code:<26} {issue.subject}")
            _say_one_line(f"{'':<8} {issue.message} [{issue.source}]")
        for ref in report.products:
            _say_one_line(f"product  {ref}")
        for ref in report.metrics:
            _say_one_line(f"metric   {ref}")
        _say(
            f"verdict: {report.verdict.value} "
            f"({len(report.products)} product(s), {len(report.metrics)} metric contract(s), "
            f"{len(report.issues)} issue(s))"
        )

    if not report.products:
        message = "error: no decision product contract found in the given paths"
        _say(message, file=sys.stderr if as_json else sys.stdout)
        return EXIT_ERROR
    return exit_code_for(report.verdict)


def _decision_investigate(args: argparse.Namespace) -> int:
    """Run the internal offline investigation alpha over supplied canonical results."""
    from auto_bi.investigation.application import (
        OfflineInvestigationError,
        write_offline_investigation,
    )
    from auto_bi.verification.model import EXIT_ERROR

    try:
        artifacts = write_offline_investigation(args.plan, args.results, args.output)
    except OfflineInvestigationError as exc:
        # Supplied names and paths must not insert lines or terminal controls.
        _say_error(exc)
        return EXIT_ERROR
    _say(f"wrote    {args.output}")
    _say(f"plan     {artifacts.plan.digest()}")
    _say(f"brief    {artifacts.brief.digest()}")
    _say("trust    exploratory; human acceptance not measured")
    return 0


def _quiet_driver_log(name: str) -> None:
    """Keep dependency warnings off the error stream a decision command answers on.

    No `decision` command configures logging, so a record a driver logs is printed by
    `logging.lastResort` — on stderr, above the one line the command answers with.
    clickhouse_connect logs a warning of each request it fails before it raises, and one with
    the traceback of a response it could not read to the end. A `NullHandler` on the driver's
    logger is a handler found, so the last resort is not used; a process that configured
    logging still has the records where its own handlers put them. Added once, however many
    times a client is built. The same fallback can expose SQL in sqlglot parser warnings.
    """
    driver = logging.getLogger(name)
    if not any(isinstance(handler, logging.NullHandler) for handler in driver.handlers):
        driver.addHandler(logging.NullHandler())


def _decision_settings() -> "Settings":
    """Load settings for DecisionOps without printing values or source error messages."""
    from pydantic import ValidationError

    from auto_bi.config import Settings, get_settings
    from auto_bi.decision.use_cases import VerifyError

    try:
        return get_settings()
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        reasons = []
        for error in errors[:3]:
            name = error["loc"][0] if error["loc"] else None
            field = (
                name if isinstance(name, str) and name in Settings.model_fields else "<settings>"
            )
            reasons.append(f"{field} ({error['type']})")
        if len(errors) > len(reasons):
            reasons.append(f"(+{len(errors) - len(reasons)} more)")
        raise VerifyError(f"cannot load settings: ValidationError: {'; '.join(reasons)}") from exc
    except (OSError, UnicodeError) as exc:
        raise VerifyError(f"cannot load settings: {type(exc).__name__}") from exc


def _result_capture_target(engine: str) -> tuple["ResultQueryFactory", str, str]:
    """Build a lazy driver factory plus the environment and actor it will use."""
    from auto_bi.engine import GREENPLUM, sqlglot_dialect
    from auto_bi.verification.result_probe import (
        make_clickhouse_result_query,
        make_postgres_result_query,
    )

    settings = _decision_settings()
    if sqlglot_dialect(engine) == sqlglot_dialect(GREENPLUM):
        return (
            lambda: make_postgres_result_query(settings),
            f"{settings.gp_host}:{settings.gp_port}/{settings.gp_database}",
            settings.gp_user,
        )
    _quiet_driver_log("clickhouse_connect")
    return (
        lambda: make_clickhouse_result_query(settings),
        f"{settings.ch_host}:{settings.ch_port}/{settings.ch_database}",
        settings.ch_user,
    )


def _decision_capture(args: argparse.Namespace) -> int:
    """Capture one reviewed query without making the offline release gate open a socket."""
    from datetime import UTC, datetime

    from auto_bi.decision.use_cases import CaptureRequest, VerifyError, capture_result
    from auto_bi.verification.model import EXIT_ERROR

    captured_at = args.captured_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        candidate_dbt = _dbt_artifact(args, "candidate")
        query_factory, configured_environment, executed_by = _result_capture_target(args.dwh)
        report = capture_result(
            CaptureRequest(
                assets=args.assets,
                candidate=args.candidate,
                output=args.output,
                engine=args.dwh,
                environment_ref=args.environment or configured_environment,
                executed_by=executed_by,
                captured_at=captured_at,
                query_factory=query_factory,
                asset_id=args.asset,
                candidate_dbt=candidate_dbt,
            )
        )
    except VerifyError as exc:
        _say_error(exc)
        return EXIT_ERROR

    _say(f"wrote    {report.output}")
    _say(f"asset    {report.asset_id}")
    _say(f"rows     {report.row_count}")
    _say(f"snapshot {report.candidate_digest}")
    return 0


def _environment_probe(engine: str) -> "EnvironmentProbe | None":
    """A read-only look at the configured engine for §8.2 step 7, or nothing.

    The probe is built here rather than inside the use case so that a run without `--dwh`
    stays what it has always been: files in, verdict out, no socket. Building it connects to
    nothing: the client is made when the use case asks, which it does after the run's stated
    facts and every input were taken — a run refused for one of them opens no session.
    Credentials come from the same settings the rest of the CLI uses and never travel into
    the bundle — the facts carry host, port and database, which is where the numbers came
    from, not how we got in.

    What the driver raises — a connection refused, a session lost, a question the engine
    failed — is raised as `EnvironmentProbeError`, and the run ends as a usage error. What
    the engine refused is not raised: it is a fact. Local identifier quoting and SQL guard
    refusals are separate usage errors; other unexpected exceptions are raised as is.
    """
    if not engine:
        return None

    from auto_bi.agent.sql_guard import SQLGuardError
    from auto_bi.decision.use_cases import EnvironmentProbeError, VerifyError
    from auto_bi.engine import GREENPLUM, sqlglot_dialect
    from auto_bi.verification.environment import EnvironmentFacts, EnvironmentQuestions
    from auto_bi.verification.environment_probe import UnsafeIdentifierError, collect
    from auto_bi.verification.runtime_probe import (
        make_clickhouse_runtime_probe,
        make_postgres_runtime_probe,
    )

    settings = _decision_settings()
    connect: Callable[[Settings], RunQuery]
    driver_errors: tuple[type[Exception], ...]
    if sqlglot_dialect(engine) == sqlglot_dialect(GREENPLUM):
        import psycopg

        from auto_bi.introspect.greenplum import make_run_query_pg

        def connect(configured: "Settings") -> "RunQuery":
            return make_run_query_pg(configured, timeout_ms=configured.environment_query_timeout_ms)

        run_cost = make_postgres_runtime_probe(settings)
        driver_errors = (psycopg.Error,)
        target = f"{settings.gp_host}:{settings.gp_port}/{settings.gp_database}"
    else:
        # Before the driver is imported: it logs while it loads, too.
        _quiet_driver_log("clickhouse_connect")
        from clickhouse_connect.driver.exceptions import ClickHouseError, StreamFailureError

        from auto_bi.introspect.clickhouse import make_run_query

        def connect(configured: "Settings") -> "RunQuery":
            return make_run_query(configured, timeout_ms=configured.environment_query_timeout_ms)

        run_cost = make_clickhouse_runtime_probe(settings)
        # A result the engine stopped sending midway is raised outside the driver's hierarchy.
        driver_errors = (ClickHouseError, StreamFailureError)
        target = f"{settings.ch_host}:{settings.ch_port}/{settings.ch_database}"

    def probe(questions: EnvironmentQuestions) -> EnvironmentFacts:
        try:
            run_query = connect(settings)
            _quiet_driver_log("sqlglot")
            try:
                return collect(
                    run_query, questions, engine=engine, target=target, run_cost=run_cost
                )
            except UnsafeIdentifierError as error:
                raise VerifyError(f"cannot build environment query: {error}") from None
            except SQLGuardError:
                # Parser diagnostics may contain SQL, line breaks and terminal controls.
                raise VerifyError("cannot execute environment query: SQLGuardError") from None
        except driver_errors as error:
            raise EnvironmentProbeError(engine, target, error) from error

    return probe


def _decision_verify(args: argparse.Namespace) -> int:
    """`auto_bi decision verify` — the one-shot release gate (ARCHITECTURE §8.2, §12.1).

    The exit code carries the verdict, never the operational status: a completed run that
    blocks exits 4, while a run that could not start exits 2 (invariant 8).
    """
    from datetime import UTC, datetime

    from auto_bi.decision.use_cases import VerifyError, VerifyRequest, verify
    from auto_bi.evidence.signing import EvidenceSignatureError, load_ed25519_signer
    from auto_bi.verification.model import EXIT_ERROR, exit_code_for

    created_at = args.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        signer = load_ed25519_signer(args.signing_key) if args.signing_key else None
        report = verify(
            VerifyRequest(
                contract_paths=args.paths,
                base=args.base,
                candidate=args.candidate,
                evidence_dir=args.evidence,
                created_at=created_at,
                dashboards_dir=args.dashboards,
                assets_dir=args.assets,
                results_file=args.results,
                environment_ref=args.environment,
                product_id=args.product,
                probe=_environment_probe(args.dwh),
                signer=signer,
                base_dbt=_dbt_artifact(args, "base"),
                candidate_dbt=_dbt_artifact(args, "candidate"),
            )
        )
    except (VerifyError, EvidenceSignatureError) as exc:
        _say_error(exc)
        return EXIT_ERROR

    if args.json:
        _say_json(report.model_dump(mode="json"))
    else:
        for check in report.checks:
            _say(f"{check.status.value:<8} {check.check_id:<22} {check.summary_code}")
            _say_one_line(f"{'':<8} {check.detail}")
        _say(f"run      {report.run_id}  evidence {report.evidence_digest}")
        _say(f"status   {report.operational_status.value}")
        _say(f"verdict: {report.verdict.value} ({len(report.blocking())} blocking check(s))")
    return exit_code_for(report.verdict)


def _decision_evidence(args: argparse.Namespace) -> int:
    """`auto_bi decision evidence` — read a bundle back and re-verify it (§9.3, §12.1).

    The verdict grades the evidence, not the release it recorded: an intact bundle exits 0
    whatever the run inside it decided, and a tampered one exits 4 whatever it says.
    """
    from auto_bi.decision.use_cases import VerifyError, locate_evidence
    from auto_bi.evidence.inspect import (
        EvidenceDigestError,
        EvidenceReadError,
        inspect_bundle,
        read_artifact,
    )
    from auto_bi.evidence.signing import (
        EvidenceSignatureError,
        load_ed25519_trust_store,
    )
    from auto_bi.verification.model import EXIT_ERROR, Verdict, exit_code_for

    try:
        verifier = load_ed25519_trust_store(args.trust_key) if args.trust_key else None
        location = locate_evidence(args.path, args.run)
        if args.artifact:
            artifact, payload = read_artifact(
                location.store,
                location.run_id,
                args.artifact,
                expected_digest=args.expect_digest or None,
                verifier=verifier,
            )
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                _say_error(
                    f"{artifact.artifact_id} is not text ({artifact.media_type}, "
                    f"{artifact.size_bytes} bytes); it is stored at {artifact.path}"
                )
                return EXIT_ERROR
            try:
                print(text)
            except UnicodeEncodeError:
                # The payload is the evidence: spelled with escapes it would be other text.
                encoding = getattr(sys.stdout, "encoding", None) or "an unnamed encoding"
                _say_error(
                    f"{artifact.artifact_id} holds text the output stream cannot encode "
                    f"({encoding}); it is stored at {artifact.path}"
                )
                return EXIT_ERROR
            return 0
        inspection = inspect_bundle(
            location.store,
            location.run_id,
            expected_digest=args.expect_digest or None,
            verifier=verifier,
        )
    except EvidenceDigestError as exc:
        _say_error(exc)
        return exit_code_for(Verdict.BLOCK)
    except (VerifyError, EvidenceReadError, EvidenceSignatureError) as exc:
        _say_error(exc)
        return EXIT_ERROR

    if args.json:
        _say_json(inspection.model_dump(mode="json"))
    else:
        _say_one_line(f"run       {inspection.run_id}")
        _say(f"digest    {inspection.manifest_digest}")
        bundle = inspection.bundle
        if bundle is not None:
            _say_one_line(f"product   {bundle.product_ref}")
            _say_one_line(f"created   {bundle.created_at}   verifier {bundle.verifier_version}")
            _say(f"integrity {bundle.integrity.value}")
            if bundle.signature is not None:
                status = "verified" if inspection.signature_verified else "unverified"
                _say_one_line(f"signature {status}   key {bundle.signature.key_id}")
            for claim in bundle.claims:
                _say_one_line(f"claim     {claim.claim_type.value:<13} {claim.summary_code:<24} ")
                _say_one_line(f"{'':<10} {claim.claim_id} <- {', '.join(claim.evidence_refs)}")
                for caveat in claim.caveats:
                    _say_one_line(f"{'':<10} caveat: {caveat}")
            for artifact in bundle.artifacts:
                _say_one_line(
                    f"artifact  {artifact.classification.value:<13} {artifact.kind.value:<18} "
                    f"{artifact.size_bytes:>8} B  {artifact.artifact_id}"
                )
        for problem in inspection.problems:
            _say_one_line(f"{problem.severity.value:<9} {problem.code:<28} {problem.detail}")
        _say(f"verdict: {inspection.verdict.value} ({len(inspection.problems)} problem(s))")
    return exit_code_for(inspection.verdict)


def _build(description: str, model_path: str, target: str = "superset") -> int:
    from functools import partial
    from pathlib import Path

    from auto_bi.adapters.factory import make_adapter
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.pipeline import build_dashboard
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.ir.spec import TargetBI
    from auto_bi.llm.factory import make_llm
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output semantic/model.yaml")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    target_bi = TargetBI(target)
    store = Store(settings.store_path)
    session_id = store.create_session(description)
    store.add_message(session_id, "user", description)
    run_query = make_run_query(settings)
    ref = build_dashboard(
        description,
        model,
        llm=make_llm(settings, store=store),
        sql_validator=LiveSQLValidator(run_query),
        adapter_for=partial(make_adapter, settings=settings, model=model),
        include_samples=settings.send_samples,
        store=store,
        session_id=session_id,
        target_bi=target_bi,
        advisor=Advisor(model, run_query),
        prune_orphans=settings.prune_on_rebuild,
    )
    base = settings.datalens_url if target_bi == TargetBI.DATALENS else settings.superset_url
    print(f"\nДашборд готов: {base.rstrip('/')}{ref.url}")
    return 0


def _build_raw(
    sql_file: str,
    title: str,
    table: str,
    columns: str,
    model_path: str,
    target: str = "superset",
) -> int:
    """X-5 escape hatch: build a one-chart TABLE dashboard from an operator-supplied SELECT.

    No LLM and no IR compilation — the SQL is used verbatim, gated live (guard_sql SELECT-only +
    EXPLAIN + LIMIT trial) exactly like generated SQL. The moat is deliberately bypassed (no
    advisor, no normalization); the model is loaded only for the adapter's dataset schema."""
    from functools import partial
    from pathlib import Path

    from auto_bi.adapters.factory import make_adapter
    from auto_bi.agent.pipeline import compile_and_build
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, TargetBI, Viz
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(sql_file).exists():
        print(f"SQL file not found: {sql_file}")
        return 2
    sql = Path(sql_file).read_text(encoding="utf-8").strip()
    if not sql:
        print(f"SQL file is empty: {sql_file}")
        return 2
    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    spec = DashboardSpec(
        title=title,
        target_bi=TargetBI(target),
        charts=[
            ChartSpec(
                id="raw",
                title=title,
                viz=Viz.TABLE,
                query=ChartQuery(table=table, dimensions=cols, raw_sql=sql),
            )
        ],
    )
    store = Store(settings.store_path)
    session_id = store.create_session(f"[raw] {title}")
    ref = compile_and_build(
        spec,
        model,
        LiveSQLValidator(make_run_query(settings)),
        partial(make_adapter, settings=settings, model=model),
        store=store,
        session_id=session_id,
        prune_orphans=settings.prune_on_rebuild,
    )
    print(f"\nДашборд готов: {settings.superset_url.rstrip('/')}{ref.url}")
    return 0


def _build_auto(table_name: str, model_path: str, target: str, max_charts: int) -> int:
    """Auto-overview: a curated dashboard built deterministically from a datamart (no LLM)."""
    from functools import partial
    from pathlib import Path

    from auto_bi.adapters.factory import make_adapter
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.autospec import build_auto_spec
    from auto_bi.agent.insights import analyze_spec
    from auto_bi.agent.pipeline import compile_and_build, review_and_log
    from auto_bi.agent.query_plan import PlanCache
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.ir.spec import TargetBI
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output semantic/model.yaml")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    target_bi = TargetBI(target)
    try:
        spec = build_auto_spec(model, table_name, max_charts=max_charts, target_bi=target_bi)
    except ValueError as exc:
        print(f"Авто-режим: {exc}")
        return 2

    store = Store(settings.store_path)
    session_id = store.create_session(f"авто-обзор: {table_name}")
    spec_id = store.save_spec(session_id, spec.model_dump(mode="json"))
    print(f"Авто-обзор «{table_name}»: {len(spec.charts)} чартов")
    for chart in spec.charts:
        print(f"  - [{chart.viz.value}] {chart.title}")

    run_query = make_run_query(settings)
    # D-2 §3: review and build run back to back, so one plan cache serves both — the guard
    # skips re-planning a statement the advisor just planned identically.
    plans = PlanCache()
    review_and_log(Advisor(model, run_query), spec, plans=plans)
    ref = compile_and_build(
        spec,
        model,
        LiveSQLValidator(run_query),
        partial(make_adapter, settings=settings, model=model),
        store=store,
        session_id=session_id,
        spec_id=spec_id,
        plans=plans,
    )
    base = settings.datalens_url if target_bi == TargetBI.DATALENS else settings.superset_url
    print(f"\nДашборд готов: {base.rstrip('/')}{ref.url}")

    # a deterministic "Что видно" layer over the real aggregates — a separate surface from
    # the dashboard, best-effort (a failed read never fails the build).
    try:
        section = analyze_spec(spec, model, run_query, plans=plans).render()
        if section:
            print(f"\n{section}")
    except Exception:
        pass
    return 0


def _prune(session: str | None, dry_run: bool, model_path: str) -> int:
    """Operator cleanup: delete prior-revision BI artifacts via the ownership ledger.

    Selection is `Store.stale_bi_artifacts` — live rows of builds that are NOT their
    session's latest build (each session's latest dashboard always survives; prune removes
    superseded revisions, never other sessions' current dashboards). Deletion goes through
    the same engine as the in-pipeline auto-prune (`prune_artifact_rows`: delete-by-id,
    chart -> dashboard -> dataset, shared kinds never touched); rows whose delete fails
    stay 'live' and are retried by a later prune.
    """
    from functools import partial
    from pathlib import Path

    from auto_bi.adapters.factory import close_adapter, make_adapter
    from auto_bi.agent.cleanup import prune_artifact_rows
    from auto_bi.config import get_settings
    from auto_bi.ir.spec import TargetBI
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output semantic/model.yaml")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    store = Store(settings.store_path)
    rows = store.stale_bi_artifacts(session_id=session)
    if not rows:
        print("Сирот прошлых ревизий нет.")
        return 0

    by_target: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_target.setdefault(row["target_bi"], []).append(row)
    print(f"Кандидаты на удаление (прошлые ревизии, всего {len(rows)}):")
    for target, target_rows in sorted(by_target.items()):
        print(f"  {target}:")
        for row in target_rows:
            sid = (row["session_id"] or "")[:8]
            print(f"    {row['kind']} {row['native_id']} «{row['name']}» (сессия {sid}…)")
    if dry_run:
        print("Dry-run: ничего не удалено.")
        return 0

    adapter_for = partial(make_adapter, settings=settings, model=model)
    removed_total = 0
    failed_total = 0
    skipped_total = 0
    for target, target_rows in sorted(by_target.items()):
        try:
            adapter = adapter_for(TargetBI(target))
        except Exception as exc:
            print(f"{target}: неизвестный target ({exc}) — {len(target_rows)} строк пропущено")
            skipped_total += len(target_rows)
            continue
        try:
            health = adapter.healthcheck()
            if not health.ok:
                print(
                    f"{target}: недоступен ({health.message}) — {len(target_rows)} строк "
                    "пропущено"
                )
                skipped_total += len(target_rows)
                continue
            # delete_artifact is a required BIAdapter method (plan_sol step 7)
            removed, failed = prune_artifact_rows(
                store, target_rows, adapter.delete_artifact, print
            )
            removed_total += removed
            failed_total += failed
        finally:
            close_adapter(adapter)  # D-2 lifecycle: one adapter (and pool) per target
    print(
        f"Итог: удалено {removed_total}, не удалось {failed_total}, пропущено {skipped_total}"
        + (" (останутся до следующего прунинга)" if failed_total or skipped_total else "")
    )
    return 0 if not (failed_total or skipped_total) else 1


APPROVE_WORDS = {"да", "ок", "ok", "строй", "собирай", "build", "yes", "y", "+"}
QUIT_WORDS = {"выход", "quit", "exit", "q"}


def _chat(model_path: str) -> int:  # pragma: no cover — interactive wiring, logic in machine
    from functools import partial
    from pathlib import Path

    from rich.console import Console
    from rich.panel import Panel

    from auto_bi.adapters.factory import make_adapter
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.machine import AgentPhase, AgentSession
    from auto_bi.agent.pipeline import compile_and_build
    from auto_bi.agent.propose import SpecValidationError
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.llm.base import LLMError
    from auto_bi.llm.factory import make_llm
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    console = Console()
    if not Path(model_path).exists():
        console.print(f"[red]Semantic model not found: {model_path}[/red]")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    run_query = make_run_query(settings)
    store = Store(settings.store_path)
    adapter_for = partial(make_adapter, settings=settings, model=model)

    console.print(
        Panel(
            "Опишите дашборд словами. Команды: [bold]да[/bold] — собрать предложенный, "
            "правка текстом — изменить, [bold]выход[/bold] — завершить.",
            title="auto_bi chat",
        )
    )
    llm = make_llm(settings, store=store)  # one client (and HTTP pool) per REPL
    while True:
        request = console.input("\n[bold cyan]Вы:[/bold cyan] ").strip()
        if not request:
            continue
        if request.lower() in QUIT_WORDS:
            return 0

        session_id = store.create_session(request)
        agent = AgentSession(
            model,
            llm,
            Advisor(model, run_query),
            store=store,
            session_id=session_id,
            include_samples=settings.send_samples,
        )
        try:
            turn = agent.start(request)
            while True:
                _render_turn(console, turn)
                if turn.phase == AgentPhase.CLARIFY:
                    answer = console.input("\n[bold cyan]Вы:[/bold cyan] ").strip()
                    if answer.lower() in QUIT_WORDS:
                        return 0
                    turn = agent.reply(answer)
                    continue
                if turn.phase == AgentPhase.APPROVE:
                    answer = console.input(
                        "\n[bold cyan]Вы[/bold cyan] [dim](да = собрать / правка словами / "
                        "отмена)[/dim]: "
                    ).strip()
                    if answer.lower() in QUIT_WORDS:
                        return 0
                    if answer.lower() in {"отмена", "нет", "cancel"}:
                        store.set_session_status(session_id, "abandoned")
                        break
                    if answer.lower() in APPROVE_WORDS:
                        spec = agent.approve()
                        ref = compile_and_build(
                            spec,
                            model,
                            LiveSQLValidator(run_query),
                            adapter_for,
                            log=lambda s: console.print(f"[dim]{s}[/dim]"),
                            store=store,
                            session_id=session_id,
                            prune_orphans=settings.prune_on_rebuild,
                        )
                        base = (
                            settings.datalens_url
                            if spec.target_bi.value == "datalens"
                            else settings.superset_url
                        )
                        url = base.rstrip("/") + ref.url
                        console.print(f"\n[bold green]Дашборд готов:[/bold green] {url}")
                        # iterations (2.4): keep the session — a further edit patches the
                        # built spec and re-enters APPROVE for a rebuild
                        more = console.input(
                            "\n[bold cyan]Вы[/bold cyan] [dim](правка словами = доработать / "
                            "Enter = закончить)[/dim]: "
                        ).strip()
                        if not more or more.lower() in QUIT_WORDS:
                            break
                        try:
                            turn = agent.reply(more)
                        except (SpecValidationError, LLMError) as exc:
                            console.print(
                                f"[red]Правка не применена: {exc}[/red] "
                                "[dim]Дашборд остаётся прежним.[/dim]"
                            )
                            break
                        continue
                    try:
                        turn = agent.reply(answer)
                    except (SpecValidationError, LLMError) as exc:
                        # failed word edit must not lose the session: the machine keeps
                        # the previous valid spec and stays in APPROVE
                        console.print(
                            f"[red]Правка не применена: {exc}[/red] "
                            "[dim]Текущий дашборд без изменений.[/dim]"
                        )
                    continue
                break
        except Exception as exc:  # session must not kill the REPL
            console.print(f"[red]Ошибка: {exc}[/red]")
            store.set_session_status(session_id, "failed")


def _serve(  # pragma: no cover — wiring only
    model_path: str, host: str, port: int, log_level: str = "INFO", log_format: str = "text"
) -> int:
    from functools import partial
    from pathlib import Path

    import uvicorn

    from auto_bi.adapters.base import AdapterHealth, DashboardRef
    from auto_bi.adapters.factory import make_adapter, probe_health
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.pipeline import compile_and_build, reconcile_interrupted_builds
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.api import create_app
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.ir.spec import DashboardSpec, TargetBI
    from auto_bi.llm.budget import parse_prices
    from auto_bi.llm.factory import make_llm
    from auto_bi.logging_setup import configure_logging
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    configure_logging(log_level, log_format)

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output " + model_path)
        return 2

    settings = get_settings()
    # C-2: a misspelled AUTO_BI_* variable is silently ignored by pydantic
    # (extra="ignore") — surface it so a typo'd security flag is never silently inert.
    from auto_bi.config import warn_unknown_env_settings
    from auto_bi.deployment_profile import (
        VALID_PROFILES,
        normalize_profile,
        validate_deployment_profile,
    )

    warn_unknown_env_settings(logger)

    # plan_sol step 4: validated deployment profiles (local|demo|production).
    raw_profile = (settings.profile or "local").strip().lower()
    if raw_profile not in VALID_PROFILES:
        logger.warning(
            "unknown AUTO_BI_PROFILE=%r — treating as 'local' (valid: %s)",
            settings.profile,
            ", ".join(sorted(VALID_PROFILES)),
        )
    profile_check = validate_deployment_profile(settings, bind_host=host)
    for w in profile_check.warnings:
        logger.warning("profile %s: %s", profile_check.profile, w)
    if not profile_check.ok:
        print(profile_check.format_message())
        return 2
    logger.info("deployment profile: %s", normalize_profile(settings.profile))

    # P0-3 fail-closed remote bind: non-loopback + auth off + not a demo profile requires
    # an explicit operator consent flag (Docker/trusted LAN). HF demo binds 127.0.0.1.
    # (Also enforced inside validate_deployment_profile for demo/production; this keeps
    # the local-profile path fail-closed without forcing AUTO_BI_PROFILE=demo.)
    loopback = {"127.0.0.1", "localhost", "::1"}
    if host not in loopback and not (
        settings.auth_enabled or settings.demo_auto_only or settings.allow_insecure_remote
    ):
        print(
            f"Refusing to serve on {host}:{port} with auth disabled.\n"
            "Enable AUTO_BI_AUTH_ENABLED=true, run a demo profile "
            "(AUTO_BI_DEMO_AUTO_ONLY=true), bind to 127.0.0.1, or set "
            "AUTO_BI_ALLOW_INSECURE_REMOTE=true for a trusted network only."
        )
        return 2

    model = SemanticModel.load(model_path)
    run_query = make_run_query(settings)
    # Build target is dispatched per durable spec; recovery needs the same resolver
    # before the legacy stuck-session reaper runs.
    adapter_for = partial(make_adapter, settings=settings, model=model)
    store = Store(settings.store_path)
    reconciled = reconcile_interrupted_builds(store, adapter_for, log=logger.info)
    if reconciled:
        logger.info(
            "reconciled %d durable interrupted build attempt(s): %s",
            len(reconciled),
            [r.get("attempt_id") for r in reconciled],
        )
    reaped = store.reap_stuck_builds()  # B-7: trace for builds a previous crash/restart lost
    if reaped:
        logger.info(
            "reaped %d orphaned build(s) interrupted by a previous restart: %s",
            len(reaped),
            reaped,
        )
    # plan_sol step 8: surface delivered_pending ledger gaps as audit trace (no invented
    # ledger rows — operator rebuild / manual repair is the recovery path).
    pending = store.reconcile_pending_ledgers()
    if pending:
        logger.warning(
            "reconcile: %d delivered_pending build(s) need ledger attention: %s",
            len(pending),
            [r.get("id") for r in pending],
        )
    if settings.auth_enabled:
        from auto_bi.auth import seed_users

        n = seed_users(store, settings)
        logger.info("auth enabled: seeded %d user(s)", n)
        _start_token_purge_thread(store)
    if settings.retention_enabled:  # D-3: age out telemetry tables, opt-in
        logger.info(
            "store retention enabled: llm_calls %dd, trace_events %dd, bi_artifacts %dd, "
            "sweep every %dh",
            settings.retention_llm_calls_days,
            settings.retention_trace_events_days,
            settings.retention_bi_artifacts_days,
            settings.retention_sweep_hours,
        )
        _start_retention_thread(store, settings)
    if settings.session_rate_enabled:  # O-2: LLM-call quota, opt-in
        logger.info(
            "session rate limit enabled: %d LLM session call(s)/day/IP",
            settings.session_rate_per_day,
        )
    # B-2: Secure cookie on by default unless bound to a loopback host (local dev), or
    # forced either way via AUTO_BI_AUTH_COOKIE_SECURE.
    cookie_secure = (
        settings.auth_cookie_secure
        if settings.auth_cookie_secure is not None
        else host not in {"127.0.0.1", "localhost", "::1"}
    )

    def builder(spec: DashboardSpec, log: Callable[[str], None], session_id: str) -> DashboardRef:
        return compile_and_build(
            spec,
            model,
            LiveSQLValidator(run_query),
            adapter_for,
            log,
            store=store,
            session_id=session_id,
            prune_orphans=settings.prune_on_rebuild,
        )

    def bi_healthcheck() -> AdapterHealth:
        # v1 scope is ClickHouse+Superset (CLAUDE.md "Скоуп"); DataLens is the v2/stand-only
        # target, so readiness checks the BI that's actually deployed. probe_health releases
        # the throwaway adapter's HTTP pool — /ready is polled, one leaked pool per probe
        # otherwise (D-2 lifecycle).
        return probe_health(adapter_for, TargetBI.SUPERSET)

    def llm_healthcheck() -> AdapterHealth:
        provider = settings.llm_provider.strip().lower()
        if provider != "gracekelly":
            # Hosted providers (anthropic, mistral, …) have no separate process to be
            # "up/down" locally, and an actual completion call would cost tokens on every
            # readiness probe — report configured-and-constructible (already proven by
            # make_llm below).
            return AdapterHealth(ok=True, message=f"{provider}: no live check (avoids token cost)")
        import httpx

        try:
            resp = httpx.get(f"{settings.gracekelly_url.rstrip('/')}/health", timeout=3.0)
            resp.raise_for_status()
            return AdapterHealth(ok=True)
        except Exception as exc:
            return AdapterHealth(ok=False, message=f"gracekelly unreachable: {exc}")

    from auto_bi.llm.base import DisabledLLM, LLMClient

    llm: LLMClient
    # plan_sol step 2: log only the policy decision + eligible classes, never values.
    if settings.send_samples:
        logger.warning(
            "AUTO_BI_SEND_SAMPLES=true: DWH top-values may be sent to the LLM for "
            "public/internal columns only; confidential/restricted never leave the process"
        )
    else:
        logger.info("AUTO_BI_SEND_SAMPLES=false: DWH values stay local (safe default)")

    if settings.demo_auto_only:
        # P8 public demo: no LLM provider/key at all — the API 403-gates every
        # LLM-triggering path, DisabledLLM is the wiring-bug backstop behind it.
        llm = DisabledLLM()
        logger.info("demo_auto_only: text/fields/enrichment disabled, LLM not wired")
    else:
        llm = make_llm(settings, store=store)
        # plan_sol step 1: a text-enabled public profile must not come up advertising
        # text/fields while the LLM is unreachable (audit P0-1). Local dev leaves
        # require_llm_ready false so a missing GraceKelly still starts the API.
        if settings.require_llm_ready:
            llm_ready = llm_healthcheck()
            if not llm_ready.ok:
                print(
                    "Refusing to serve text-enabled profile: LLM is not ready.\n"
                    f"  {llm_ready.message}\n"
                    "Fix the LLM (GraceKelly tunnel / provider credentials) or set "
                    "AUTO_BI_DEMO_AUTO_ONLY=true for auto-overview only."
                )
                return 2
            logger.info("require_llm_ready: LLM probe ok (%s)", llm_ready.message or "ok")
    app = create_app(
        model=model,
        llm=llm,
        advisor=Advisor(model, run_query),
        run_query=run_query,  # "Что видно" insight layer reads charts read-only
        store=store,
        builder=builder,
        bi_healthcheck=bi_healthcheck,
        llm_healthcheck=llm_healthcheck,
        include_samples=settings.send_samples,
        model_path=model_path,  # enrichment UI пишет правки обратно в model.yaml
        auth_enabled=settings.auth_enabled,
        auth_token_ttl_hours=settings.auth_token_ttl_hours,
        cookie_secure=cookie_secure,
        session_rate_enabled=settings.session_rate_enabled,
        session_rate_per_day=settings.session_rate_per_day,
        work_rate_enabled=settings.work_rate_enabled,
        work_rate_per_day=settings.work_rate_per_day,
        max_concurrent_builds=settings.max_concurrent_builds,
        sse_max_streams=settings.sse_max_streams,
        sse_max_streams_per_session=settings.sse_max_streams_per_session,
        # F-1: the UI link must point at the BI host, not the Auto_BI host serving the
        # page. The PUBLIC url wins when it differs from the API url the adapter calls
        # (P8 demo: adapter -> 127.0.0.1:8088, viewer -> https://<space>.hf.space).
        bi_base_urls={
            TargetBI.SUPERSET: settings.superset_public_url or settings.superset_url,
            TargetBI.DATALENS: settings.datalens_url,
        },
        demo_auto_only=settings.demo_auto_only,
        metrics_enabled=settings.metrics_enabled,
        # D-3: the spend metric prices the same ledger with the same table as the budget
        # guard, so a listed-model change moves both together
        llm_prices=parse_prices(settings.llm_budget_prices),
    )
    uvicorn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "log_level": log_level.lower(),
        # F-2: rewrite request.client from X-Forwarded-For so the per-IP quotas (B-3
        # login limiter, O-2 session quota) see real client IPs behind a reverse proxy
        # instead of collapsing into one shared bucket. Explicit although it is
        # uvicorn's default — the quotas' correctness depends on it.
        "proxy_headers": True,
    }
    if settings.forwarded_allow_ips is not None:
        # which peers are trusted to SET those headers; uvicorn's default trusts
        # loopback only — enough for a same-host proxy, must be widened for a
        # containerized one (DEPLOYMENT §4/§6).
        uvicorn_kwargs["forwarded_allow_ips"] = settings.forwarded_allow_ips
        logger.info("trusting proxy headers from: %s", settings.forwarded_allow_ips)
    if log_format == "json":
        # skip uvicorn's own dictConfig so its "uvicorn"/"uvicorn.access" loggers propagate
        # to the root logger configure_logging() just set up — one consistent JSON stream.
        uvicorn_kwargs["log_config"] = None
    try:
        uvicorn.run(app, **uvicorn_kwargs)
    finally:
        # D-2 lifecycle: the LLM client lives for the whole server; release its HTTP pool
        # on shutdown. getattr — close() is not part of the LLMClient Protocol (DisabledLLM
        # and fakes have none).
        llm_close = getattr(llm, "close", None)
        if callable(llm_close):
            llm_close()
    return 0


def _start_token_purge_thread(  # pragma: no cover — wiring only
    store: "Store", interval_seconds: float = 3600.0
) -> None:
    """Daemon thread that sweeps expired `auth_tokens` rows once an hour (B-4 follow-up):
    `token_user` already filters expired rows out, so this is just housekeeping against
    unbounded growth of a table that otherwise never shrinks. `Store.purge_expired_tokens`
    carries the tested logic; this loop is pure wiring, like the build thread above."""
    import threading
    import time

    def _loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                store.purge_expired_tokens()
            except Exception:
                logger.exception("token purge sweep failed")

    threading.Thread(target=_loop, name="auth-token-purge", daemon=True).start()


def _start_retention_thread(
    store: "Store", settings: "Settings"
) -> None:  # pragma: no cover — wiring only
    """Daemon thread that ages out the telemetry tables (D-3).

    Sweeps ONCE at startup and every `retention_sweep_hours` after: a server that restarts
    more often than the interval would otherwise never sweep at all. `Store.purge_retention`
    carries the tested logic and the choice of which tables are eligible; this loop is pure
    wiring, like the token purge above."""
    import threading
    import time

    interval = max(1, settings.retention_sweep_hours) * 3600.0

    def _sweep() -> None:
        deleted = store.purge_retention(
            llm_calls_days=settings.retention_llm_calls_days,
            trace_events_days=settings.retention_trace_events_days,
            bi_artifacts_days=settings.retention_bi_artifacts_days,
        )
        if any(deleted.values()):
            logger.info(
                "retention sweep deleted %s",
                ", ".join(f"{n} row(s) from {t}" for t, n in deleted.items() if n),
            )

    def _loop() -> None:
        while True:
            try:
                _sweep()
            except Exception:
                logger.exception("retention sweep failed")
            time.sleep(interval)

    threading.Thread(target=_loop, name="store-retention", daemon=True).start()


def _render_turn(
    console: "Console", turn: "AgentTurn"
) -> None:  # pragma: no cover — presentation only
    from rich.panel import Panel

    if turn.message:
        console.print(Panel(turn.message, title="агент", title_align="left"))
    for i, q in enumerate(turn.questions, 1):
        console.print(f"  [yellow]{i}. {q}[/yellow]")
    severity_color = {"info": "blue", "warn": "yellow", "critical": "red"}
    for v in turn.verdicts:
        color = severity_color.get(v.severity.value, "white")
        console.print(
            Panel(
                f"{v.text}"
                + ("\n\nВарианты: " + "; ".join(v.suggestions) if v.suggestions else ""),
                title=f"advisor · {v.chart_id} · {v.verdict_class.value}",
                title_align="left",
                border_style=color,
            )
        )


def _eval(
    model_path: str,
    suite: str,
    cases_csv: str,
    llm_mode: str = "live",
    fixtures_dir: str = "tests/fixtures/golden_llm",
) -> int:
    from pathlib import Path

    from rich.console import Console
    from rich.table import Table as RichTable

    from auto_bi.config import get_settings
    from auto_bi.eval.cases import advisor_cases_for_engine, golden_cases_for_engine
    from auto_bi.eval.runner import (
        EvalReport,
        advisor_suite_ok,
        golden_suite_ok,
        run_advisor_suite,
        run_golden_suite,
    )
    from auto_bi.semantic.model import SemanticModel

    console = Console()
    if not Path(model_path).exists():
        console.print(f"[red]Semantic model not found: {model_path}[/red]")
        return 2
    model = SemanticModel.load(model_path)
    wanted = {c.strip() for c in cases_csv.split(",") if c.strip()}
    # one engine per model -> pick the matching case sets (CH demo vs GP demo)
    engine = next((t.physical.engine for t in model.tables if t.physical), "clickhouse")
    console.print(f"[dim]model engine: {engine}[/dim]")

    def _render(title: str, report: EvalReport) -> None:
        table = RichTable(title=title)
        table.add_column("case")
        table.add_column("kind")
        table.add_column("result")
        table.add_column("detail", overflow="fold")
        for r in report.results:
            mark = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
            table.add_row(r.case_id, r.kind, mark, r.detail)
        console.print(table)
        console.print(f"{report.passed}/{report.total} passed\n")

    ok = True
    if suite in ("advisor", "all"):
        advisor_cases = [
            c for c in advisor_cases_for_engine(engine) if not wanted or c.id in wanted
        ]
        report = run_advisor_suite(model, advisor_cases)
        _render(f"Advisor anti-pattern suite — {engine} (deterministic)", report)
        ok &= advisor_suite_ok(report)

    golden_cases = golden_cases_for_engine(engine)
    if suite in ("golden", "all") and not golden_cases:
        console.print(
            f"[yellow]no golden dialogue cases for engine {engine!r} — golden-case design "
            f"is a separate (S2) task; the advisor suite covers the {engine} rule pack.[/yellow]"
        )
    elif suite in ("golden", "all"):
        from auto_bi.llm.base import LLMClient

        golden_selected = [c for c in golden_cases if not wanted or c.id in wanted]

        llm: LLMClient
        if llm_mode == "replay":
            from auto_bi.llm.fixture import FixtureLLMClient

            llm = FixtureLLMClient(fixtures_dir, enforce_fingerprint=True)
            console.print(
                f"[dim]golden: {len(golden_selected)} cases, replay из {fixtures_dir}"
                " (офлайн, fingerprint enforced, без провайдера/ключа)…[/dim]"
            )
        elif llm_mode == "refresh-fingerprints":
            from auto_bi.llm.fixture import FixtureLLMClient

            llm = FixtureLLMClient(fixtures_dir, refresh_fingerprints=True)
            console.print(
                f"[dim]golden: {len(golden_selected)} cases, refresh fingerprints в "
                f"{fixtures_dir} (офлайн, ответы не меняются)…[/dim]"
            )
        else:
            from auto_bi.llm.factory import make_llm
            from auto_bi.store import Store

            settings = get_settings()
            store = Store(settings.store_path)
            live_llm = make_llm(settings, store=store)
            provider = settings.llm_provider.strip().lower()
            model_id = {
                "gracekelly": settings.gracekelly_model,
                "anthropic": settings.anthropic_model,
                "mistral": settings.mistral_model,
            }.get(provider, provider)
            provider_detail = (
                f"{settings.gracekelly_url}, {model_id}" if provider == "gracekelly" else model_id
            )
            if llm_mode == "record":
                from auto_bi.llm.fixture import RecordingLLMClient

                llm = RecordingLLMClient(
                    live_llm, fixtures_dir, provider=provider, model_id=model_id
                )
                console.print(
                    f"[dim]golden: {len(golden_selected)} cases через {provider} "
                    f"({provider_detail}), запись фикстур в {fixtures_dir}…[/dim]"
                )
            else:
                llm = live_llm
                console.print(
                    f"[dim]golden: {len(golden_selected)} cases через {provider} "
                    f"({provider_detail})…[/dim]"
                )
        report = run_golden_suite(
            model,
            llm,
            cases=golden_selected,
            progress=lambda r: console.print(
                f"  [dim]{r.case_id}[/dim] "
                + ("[green]PASS[/green]" if r.passed else f"[red]FAIL[/red] {r.detail}")
            ),
        )
        mode_label = {
            "live": "live LLM",
            "replay": "offline replay",
            "record": "recording",
            "refresh-fingerprints": "fingerprint refresh",
        }[llm_mode]
        _render(f"Golden dialogue suite ({mode_label})", report)
        if not wanted:  # thresholds only make sense on the full set
            ok &= golden_suite_ok(report, mode=llm_mode)
        elif llm_mode in ("replay", "refresh-fingerprints"):
            # subset still must be fully green under deterministic modes
            ok &= all(r.passed for r in report.results)

    return 0 if ok else 1


def _gaps(model_path: str, output: str, offline: bool) -> int:
    from pathlib import Path

    from auto_bi.introspect.gaps import find_gaps
    from auto_bi.semantic.model import SemanticModel

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate it first: auto_bi introspect --output " + model_path)
        return 2

    run_query = None
    if not offline:
        from auto_bi.config import get_settings
        from auto_bi.introspect.clickhouse import make_run_query

        run_query = make_run_query(get_settings())

    report = find_gaps(SemanticModel.load(model_path), run_query)
    markdown = report.to_markdown()
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(markdown, encoding="utf-8")
        print(f"Gaps report written to {output}: {len(report.findings)} findings")
    else:
        print(markdown)
    return 0


def _dbt_import(manifest_path: str, catalog_path: str, model_path: str, dry_run: bool) -> int:
    from pathlib import Path

    from auto_bi.semantic.dbt_import import dbt_enrich, load_artifact
    from auto_bi.semantic.model import SemanticModel

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate it first: auto_bi introspect --output " + model_path)
        return 2
    if not Path(manifest_path).exists():
        print(f"dbt manifest not found: {manifest_path}")
        return 2

    model = SemanticModel.load(model_path)
    catalog = load_artifact(catalog_path) if catalog_path else None
    report = dbt_enrich(model, load_artifact(manifest_path), catalog)

    for label, items in (
        ("описания таблиц", report.table_descriptions),
        ("описания колонок", report.column_descriptions),
        ("joins из relationships", report.joins_added),
        ("fk проставлены", report.fks_set),
    ):
        if items:
            print(f"{label} ({len(items)}):")
            for item in items:
                print(f"  + {item}")
    if report.kept_existing:
        print(f"не тронуто (ручные значения выигрывают): {len(report.kept_existing)}")
    for label, items in (
        ("dbt-модели без таблицы в model.yaml", report.unmatched_models),
        ("dbt-колонки без колонки в model.yaml", report.unmatched_columns),
    ):
        if items:
            print(f"{label} ({len(items)}): {', '.join(items)}")

    if report.changed == 0:
        print("Изменений нет — модель уже согласована с dbt-артефактами.")
        return 0
    if dry_run:
        print(f"dry-run: {report.changed} изменений НЕ записано в {model_path}")
        return 0
    model.dump(model_path)
    print(f"{model_path} обновлён: {report.changed} изменений")
    return 0


def _introspect(database: str | None, output: str, engine: str = "clickhouse") -> int:
    from auto_bi.config import get_settings

    settings = get_settings()
    if engine == "greenplum":
        from auto_bi.introspect.greenplum import GreenplumIntrospector, make_run_query_pg

        # for GP, --database overrides the schema (default AUTO_BI_GP_SCHEMA); connection
        # params (db/host/...) come from AUTO_BI_GP_*. GP supports introspection + advisor;
        # it is not a BI build target (the BI connection always points at ClickHouse).
        gp_introspector = GreenplumIntrospector(
            make_run_query_pg(settings), schema=settings.gp_schema
        )
        model = gp_introspector.introspect(database)
    else:
        from auto_bi.introspect.clickhouse import ClickHouseIntrospector, make_run_query

        ch_introspector = ClickHouseIntrospector(make_run_query(settings))
        model = ch_introspector.introspect(database or settings.ch_database)
    model.dump(output)
    n_cols = sum(len(t.columns) for t in model.tables)
    print(f"Draft written to {output}: {len(model.tables)} tables, {n_cols} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())

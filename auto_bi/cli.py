"""CLI entrypoint: `auto_bi build "<description>"` (Phase 0 happy path, no dialogue)."""

import argparse
import logging
import sys

from auto_bi import __version__

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto_bi", description=__doc__)
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
        choices=["live", "replay", "record"],
        default="live",
        help="golden suite only: 'live' calls the configured provider (default); "
        "'replay' answers from recorded fixtures, offline, no provider/key needed "
        "(CI); 'record' calls the configured provider and writes fixtures for later replay",
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
    return 0


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
    review_and_log(Advisor(model, run_query), spec)
    ref = compile_and_build(
        spec,
        model,
        LiveSQLValidator(run_query),
        partial(make_adapter, settings=settings, model=model),
        store=store,
        session_id=session_id,
        spec_id=spec_id,
    )
    base = settings.datalens_url if target_bi == TargetBI.DATALENS else settings.superset_url
    print(f"\nДашборд готов: {base.rstrip('/')}{ref.url}")

    # a deterministic "Что видно" layer over the real aggregates — a separate surface from
    # the dashboard, best-effort (a failed read never fails the build).
    try:
        section = analyze_spec(spec, model, run_query).render()
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

    from auto_bi.adapters.factory import make_adapter
    from auto_bi.agent.pipeline import prune_artifact_rows
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

    by_target: dict[str, list[dict]] = {}
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
        health = adapter.healthcheck()
        if not health.ok:
            print(f"{target}: недоступен ({health.message}) — {len(target_rows)} строк пропущено")
            skipped_total += len(target_rows)
            continue
        delete = getattr(adapter, "delete_artifact", None)
        if not callable(delete):
            print(f"{target}: адаптер без delete_artifact — {len(target_rows)} строк пропущено")
            skipped_total += len(target_rows)
            continue
        removed, failed = prune_artifact_rows(store, target_rows, delete, print)
        removed_total += removed
        failed_total += failed
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

    from auto_bi.adapters.base import AdapterHealth
    from auto_bi.adapters.factory import make_adapter
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.pipeline import compile_and_build
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.api import create_app
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.ir.spec import TargetBI
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

    warn_unknown_env_settings(logger)
    # P0-3 fail-closed remote bind: non-loopback + auth off + not a demo profile requires
    # an explicit operator consent flag (Docker/trusted LAN). HF demo binds 127.0.0.1.
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
    store = Store(settings.store_path)
    reaped = store.reap_stuck_builds()  # B-7: trace for builds a previous crash/restart lost
    if reaped:
        logger.info(
            "reaped %d orphaned build(s) interrupted by a previous restart: %s",
            len(reaped),
            reaped,
        )
    if settings.auth_enabled:
        from auto_bi.auth import seed_users

        n = seed_users(store, settings)
        logger.info("auth enabled: seeded %d user(s)", n)
        _start_token_purge_thread(store)
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
    # the build target is dispatched per-spec (spec.target_bi); the API/UI selector sets it
    adapter_for = partial(make_adapter, settings=settings, model=model)

    def builder(spec, log, session_id):
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
        # target, so readiness checks the BI that's actually deployed.
        return adapter_for(TargetBI.SUPERSET).healthcheck()

    def llm_healthcheck() -> AdapterHealth:
        if settings.llm_provider.strip().lower() != "gracekelly":
            # Anthropic is a hosted API with no separate process to be "up/down" locally,
            # and an actual completion call would cost tokens on every readiness probe —
            # report configured-and-constructible (already proven by make_llm below).
            return AdapterHealth(ok=True, message="anthropic: no live check (avoids token cost)")
        import httpx

        try:
            resp = httpx.get(f"{settings.gracekelly_url.rstrip('/')}/health", timeout=3.0)
            resp.raise_for_status()
            return AdapterHealth(ok=True)
        except Exception as exc:
            return AdapterHealth(ok=False, message=f"gracekelly unreachable: {exc}")

    from auto_bi.llm.base import DisabledLLM, LLMClient

    llm: LLMClient
    if settings.demo_auto_only:
        # P8 public demo: no LLM provider/key at all — the API 403-gates every
        # LLM-triggering path, DisabledLLM is the wiring-bug backstop behind it.
        llm = DisabledLLM()
        logger.info("demo_auto_only: text/fields/enrichment disabled, LLM not wired")
    else:
        llm = make_llm(settings, store=store)
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
    )
    uvicorn_kwargs: dict = {
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
        # containerized one (DEPLOYMENT §3/§5).
        uvicorn_kwargs["forwarded_allow_ips"] = settings.forwarded_allow_ips
        logger.info("trusting proxy headers from: %s", settings.forwarded_allow_ips)
    if log_format == "json":
        # skip uvicorn's own dictConfig so its "uvicorn"/"uvicorn.access" loggers propagate
        # to the root logger configure_logging() just set up — one consistent JSON stream.
        uvicorn_kwargs["log_config"] = None
    uvicorn.run(app, **uvicorn_kwargs)
    return 0


def _start_token_purge_thread(  # pragma: no cover — wiring only
    store, interval_seconds: float = 3600.0
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


def _render_turn(console, turn) -> None:  # pragma: no cover — presentation only
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

    def _render(title: str, report) -> None:
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

            llm = FixtureLLMClient(fixtures_dir)
            console.print(
                f"[dim]golden: {len(golden_selected)} cases, replay из {fixtures_dir}"
                " (офлайн, без провайдера/ключа)…[/dim]"
            )
        else:
            from auto_bi.llm.factory import make_llm
            from auto_bi.store import Store

            settings = get_settings()
            store = Store(settings.store_path)
            live_llm = make_llm(settings, store=store)
            provider = settings.llm_provider.strip().lower()
            provider_detail = (
                f"{settings.gracekelly_url}, {settings.gracekelly_model}"
                if provider == "gracekelly"
                else settings.anthropic_model
            )
            if llm_mode == "record":
                from auto_bi.llm.fixture import RecordingLLMClient

                llm = RecordingLLMClient(live_llm, fixtures_dir)
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
        mode_label = {"live": "live LLM", "replay": "offline replay", "record": "recording"}[
            llm_mode
        ]
        _render(f"Golden dialogue suite ({mode_label})", report)
        if not wanted:  # thresholds only make sense on the full set
            ok &= golden_suite_ok(report)

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

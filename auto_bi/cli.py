"""CLI entrypoint: `auto_bi build "<description>"` (Phase 0 happy path, no dialogue)."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto_bi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a dashboard from a text description")
    build.add_argument("description", help="Dashboard description in natural language")
    build.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    chat = sub.add_parser("chat", help="Dialogue: clarify -> preview spec -> approve -> build")
    chat.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    intro = sub.add_parser("introspect", help="Introspect DWH and write a draft model.yaml")
    intro.add_argument("--database", default=None, help="Database/schema (default: settings)")
    intro.add_argument("--output", default="semantic/model.yaml", help="Where to write the draft")

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

    ev = sub.add_parser("eval", help="Run the eval suites (advisor: offline; golden: live LLM)")
    ev.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")
    ev.add_argument("--suite", choices=["advisor", "golden", "all"], default="all")
    ev.add_argument("--cases", default="", help="Comma-separated case ids to run (subset)")

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

    args = parser.parse_args(argv)

    if args.command == "build":
        return _build(args.description, args.model_path)
    if args.command == "chat":
        return _chat(args.model_path)
    if args.command == "serve":
        return _serve(args.model_path, args.host, args.port)
    if args.command == "introspect":
        return _introspect(args.database, args.output)
    if args.command == "gaps":
        return _gaps(args.model_path, args.output, args.offline)
    if args.command == "eval":
        return _eval(args.model_path, args.suite, args.cases)
    if args.command == "dbt-import":
        return _dbt_import(args.manifest, args.catalog, args.model_path, args.dry_run)
    return 0


def _build(description: str, model_path: str) -> int:
    from pathlib import Path

    from auto_bi.adapters.base import DWHConfig
    from auto_bi.adapters.superset.adapter import SupersetAdapter
    from auto_bi.adapters.superset.client import SupersetClient
    from auto_bi.agent.pipeline import build_dashboard
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.llm.gracekelly import GraceKellyClient
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output semantic/model.yaml")
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    adapter = SupersetAdapter(
        SupersetClient(settings.superset_url, settings.superset_user, settings.superset_password),
        DWHConfig(
            host=settings.ch_host_from_bi or settings.ch_host,
            port=settings.ch_port_from_bi or settings.ch_port,
            database=settings.ch_database,
            user=settings.ch_user,
            password=settings.ch_password,
        ),
    )
    store = Store(settings.store_path)
    session_id = store.create_session(description)
    store.add_message(session_id, "user", description)
    ref = build_dashboard(
        description,
        model,
        llm=GraceKellyClient(settings, store=store),
        sql_validator=LiveSQLValidator(make_run_query(settings)),
        adapter=adapter,
        include_samples=settings.send_samples,
        store=store,
        session_id=session_id,
    )
    print(f"\nДашборд готов: {settings.superset_url.rstrip('/')}{ref.url}")
    return 0


APPROVE_WORDS = {"да", "ок", "ok", "строй", "собирай", "build", "yes", "y", "+"}
QUIT_WORDS = {"выход", "quit", "exit", "q"}


def _chat(model_path: str) -> int:  # pragma: no cover — interactive wiring, logic in machine
    from pathlib import Path

    from rich.console import Console
    from rich.panel import Panel

    from auto_bi.adapters.base import DWHConfig
    from auto_bi.adapters.superset.adapter import SupersetAdapter
    from auto_bi.adapters.superset.client import SupersetClient
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.machine import AgentPhase, AgentSession
    from auto_bi.agent.pipeline import compile_and_build
    from auto_bi.agent.propose import SpecValidationError
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.llm.base import LLMError
    from auto_bi.llm.gracekelly import GraceKellyClient
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
    adapter = SupersetAdapter(
        SupersetClient(settings.superset_url, settings.superset_user, settings.superset_password),
        DWHConfig(
            host=settings.ch_host_from_bi or settings.ch_host,
            port=settings.ch_port_from_bi or settings.ch_port,
            database=settings.ch_database,
            user=settings.ch_user,
            password=settings.ch_password,
        ),
    )

    console.print(
        Panel(
            "Опишите дашборд словами. Команды: [bold]да[/bold] — собрать предложенный, "
            "правка текстом — изменить, [bold]выход[/bold] — завершить.",
            title="auto_bi chat",
        )
    )
    llm = GraceKellyClient(settings, store=store)  # one client (and HTTP pool) per REPL
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
                            adapter,
                            log=lambda s: console.print(f"[dim]{s}[/dim]"),
                            store=store,
                            session_id=session_id,
                        )
                        url = settings.superset_url.rstrip("/") + ref.url
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


def _serve(model_path: str, host: str, port: int) -> int:  # pragma: no cover — wiring only
    from pathlib import Path

    import uvicorn

    from auto_bi.adapters.base import DWHConfig
    from auto_bi.adapters.superset.adapter import SupersetAdapter
    from auto_bi.adapters.superset.client import SupersetClient
    from auto_bi.advisor.core import Advisor
    from auto_bi.agent.pipeline import compile_and_build
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.api import create_app
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
    from auto_bi.llm.gracekelly import GraceKellyClient
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

    if not Path(model_path).exists():
        print(f"Semantic model not found: {model_path}")
        print("Generate the draft first: auto_bi introspect --output " + model_path)
        return 2

    settings = get_settings()
    model = SemanticModel.load(model_path)
    run_query = make_run_query(settings)
    store = Store(settings.store_path)
    adapter = SupersetAdapter(
        SupersetClient(settings.superset_url, settings.superset_user, settings.superset_password),
        DWHConfig(
            host=settings.ch_host_from_bi or settings.ch_host,
            port=settings.ch_port_from_bi or settings.ch_port,
            database=settings.ch_database,
            user=settings.ch_user,
            password=settings.ch_password,
        ),
    )

    def builder(spec, log, session_id):
        return compile_and_build(
            spec,
            model,
            LiveSQLValidator(run_query),
            adapter,
            log,
            store=store,
            session_id=session_id,
        )

    app = create_app(
        model=model,
        llm=GraceKellyClient(settings, store=store),
        advisor=Advisor(model, run_query),
        store=store,
        builder=builder,
        include_samples=settings.send_samples,
    )
    uvicorn.run(app, host=host, port=port)
    return 0


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


def _eval(model_path: str, suite: str, cases_csv: str) -> int:
    from pathlib import Path

    from rich.console import Console
    from rich.table import Table as RichTable

    from auto_bi.config import get_settings
    from auto_bi.eval.cases import ADVISOR_CASES, GOLDEN_CASES
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
        cases = [c for c in ADVISOR_CASES if not wanted or c.id in wanted]
        report = run_advisor_suite(model, cases)
        _render("Advisor anti-pattern suite (deterministic)", report)
        ok &= advisor_suite_ok(report)

    if suite in ("golden", "all"):
        from auto_bi.llm.gracekelly import GraceKellyClient

        settings = get_settings()
        llm = GraceKellyClient(settings)
        cases = [c for c in GOLDEN_CASES if not wanted or c.id in wanted]
        console.print(
            f"[dim]golden: {len(cases)} cases через GraceKelly "
            f"({settings.gracekelly_url}, {settings.gracekelly_model})…[/dim]"
        )
        report = run_golden_suite(
            model,
            llm,
            cases=cases,
            progress=lambda r: console.print(
                f"  [dim]{r.case_id}[/dim] "
                + ("[green]PASS[/green]" if r.passed else f"[red]FAIL[/red] {r.detail}")
            ),
        )
        _render("Golden dialogue suite (live LLM)", report)
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


def _introspect(database: str | None, output: str) -> int:
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import ClickHouseIntrospector, make_run_query

    settings = get_settings()
    introspector = ClickHouseIntrospector(make_run_query(settings))
    model = introspector.introspect(database or settings.ch_database)
    model.dump(output)
    n_cols = sum(len(t.columns) for t in model.tables)
    print(f"Draft written to {output}: {len(model.tables)} tables, {n_cols} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())

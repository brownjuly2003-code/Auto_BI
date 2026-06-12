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

    args = parser.parse_args(argv)

    if args.command == "build":
        return _build(args.description, args.model_path)
    if args.command == "chat":
        return _chat(args.model_path)
    if args.command == "introspect":
        return _introspect(args.database, args.output)
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
    from auto_bi.agent.sql_guard import LiveSQLValidator
    from auto_bi.config import get_settings
    from auto_bi.introspect.clickhouse import make_run_query
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
    while True:
        request = console.input("\n[bold cyan]Вы:[/bold cyan] ").strip()
        if not request:
            continue
        if request.lower() in QUIT_WORDS:
            return 0

        session_id = store.create_session(request)
        llm = GraceKellyClient(settings, store=store)
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
                        break
                    turn = agent.reply(answer)
                    continue
                break
        except Exception as exc:  # session must not kill the REPL
            console.print(f"[red]Ошибка: {exc}[/red]")
            store.set_session_status(session_id, "failed")


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

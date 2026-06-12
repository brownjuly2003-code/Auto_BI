"""CLI entrypoint: `auto_bi build "<description>"` (Phase 0 happy path, no dialogue)."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto_bi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a dashboard from a text description")
    build.add_argument("description", help="Dashboard description in natural language")
    build.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    intro = sub.add_parser("introspect", help="Introspect DWH and write a draft model.yaml")
    intro.add_argument("--database", default=None, help="Database/schema (default: settings)")
    intro.add_argument("--output", default="semantic/model.yaml", help="Where to write the draft")

    args = parser.parse_args(argv)

    if args.command == "build":
        return _build(args.description, args.model_path)
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
    ref = build_dashboard(
        description,
        model,
        llm=GraceKellyClient(settings),
        sql_validator=LiveSQLValidator(make_run_query(settings)),
        adapter=adapter,
        include_samples=settings.send_samples,
    )
    print(f"\nДашборд готов: {settings.superset_url.rstrip('/')}{ref.url}")
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

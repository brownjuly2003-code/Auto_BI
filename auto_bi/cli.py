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
        # Wired up in task 0.8 once IR/SQL_GEN/Superset adapter exist.
        print("auto_bi build: pipeline not implemented yet (Phase 0 in progress)")
        return 1
    if args.command == "introspect":
        return _introspect(args.database, args.output)
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

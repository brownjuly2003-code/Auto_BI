"""CLI entrypoint: `auto_bi build "<description>"` (Phase 0 happy path, no dialogue)."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto_bi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a dashboard from a text description")
    build.add_argument("description", help="Dashboard description in natural language")
    build.add_argument("--model-path", default="semantic/model.yaml", help="Semantic model file")

    args = parser.parse_args(argv)

    if args.command == "build":
        # Wired up in task 0.8 once IR/SQL_GEN/Superset adapter exist.
        print("auto_bi build: pipeline not implemented yet (Phase 0 in progress)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

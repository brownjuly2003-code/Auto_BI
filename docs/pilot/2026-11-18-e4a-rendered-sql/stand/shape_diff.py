"""E4a: what differs in the shape of Superset's answers between two runs (4.1.2 against 6.1).

For each answer the inventory reads — dashboard, its charts, its datasets — and the chart detail
and the GET type=query answer the measurement adds, collects the key paths with their JSON types
over the whole corpus of a run; lists paths only one side has and paths whose type changed.
Usage: python shape_diff.py rec/41-c1 rec/61-c1
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# A JSON string holding JSON is walked too: the inventory parses these.
EMBEDDED = {"json_metadata", "position_json", "params", "query_context", "form_data"}


def kind(value: Any) -> str:
    return type(value).__name__


def walk(value: Any, path: str, out: dict[str, set[str]]) -> None:
    if isinstance(value, str) and path.rsplit(".", 1)[-1] in EMBEDDED:
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            out[path].add("json-text")
            walk(parsed, path + "{}", out)
            return
    out[path].add(kind(value))
    if isinstance(value, dict):
        for key, item in value.items():
            # Keys that are ids or names (position_json, label_colors) are not shape.
            name = (
                "<id>" if key.isdigit() or "-" in key or key.startswith(("CHART", "ROW")) else key
            )
            walk(item, f"{path}.{name}", out)
    elif isinstance(value, list):
        for item in value:
            walk(item, path + "[]", out)


def shapes(run: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted(run.glob("dashboard_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for name, (status, body) in record["inventory"].items():
            if status == 200:
                walk(body.get("result"), name, out)
        for chart in record["charts"].values():
            status, body = chart["detail"]
            if status == 200:
                walk(body.get("result"), "chart_detail", out)
            status, body = chart["get_query"]
            walk(body, f"get_query[{status}]", out)
    return out


def main() -> None:
    left, right = (shapes(Path(arg)) for arg in sys.argv[1:3])
    for title, paths in (
        ("only " + sys.argv[1], sorted(set(left) - set(right))),
        ("only " + sys.argv[2], sorted(set(right) - set(left))),
    ):
        print(f"== {title}: {len(paths)}")
        for path in paths:
            if not any(
                path.startswith(p + ".") or path.startswith(p + "[") for p in paths if p != path
            ):
                print("  ", path, sorted((left if title.endswith(sys.argv[1]) else right)[path]))
    print("== type changed")
    for path in sorted(set(left) & set(right)):
        if left[path] != right[path]:
            print("  ", path, sorted(left[path]), "->", sorted(right[path]))


if __name__ == "__main__":
    main()

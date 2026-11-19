"""E4a: per-chart comparison of the SQL the inventory can get by GET with what the engine ran.

Reads the records of measure.py (rec/<run>/dashboard_<id>.json) and writes rows.json (one row per
chart) and prints the counts. Stdlib, plus sqlglot when importable: Superset 6.1 runs the SQL
through sqlglot before the engine (`count(` -> `COUNT(`, `!=` -> `<>`) and shows the text from
before, so the `*_sqlglot` counts compare both sides after a sqlglot round trip in the engine's
dialect.
A text counts as run when, normalized, it equals a statement of the engine log of the window
(non-empty; review r1 replaced a substring match). The dashboard's window is one: from the first
open to the last request of the dashboard, not one per request. `norm` collapses whitespace and
drops a trailing `;` and `FORMAT` outside quoted literals and line comments only; a line comment
keeps its line end.
Usage: python analyze.py rec/41-admin [rec/... ...]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_WS = re.compile(r"\s+")
_FORMAT = re.compile(r"\s+FORMAT\s+\w+\s*$", re.IGNORECASE)
# A quoted literal or identifier keeps its text; a line comment keeps its text and its line end
# (review r2: `SELECT 1 -- x` + newline + `FROM t` is not `SELECT 1 -- x FROM t`).
_QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|--[^\n]*")
_DATES = re.compile(r"'\d{4}-\d\d-\d\d(?:[ T]\d\d:\d\d:\d\d(?:\.\d+)?)?'")

try:
    import logging

    import sqlglot
    from sqlglot import exp

    logging.getLogger("sqlglot").setLevel(logging.ERROR)
except ImportError:  # pragma: no cover - the playwright venv has no sqlglot
    sqlglot = None


# Superset 6.1's sqlglot writes `x IS NOT NULL` as `NOT x IS NULL`; this sqlglot keeps the two
# apart, so the generated text is brought to one spelling.
_NOT_NULL = re.compile(r'\bNOT ("[^"]+"|`[^`]+`|[\w.]+) IS NULL\b')


def canon(sql: str, dialect: str) -> str | None:
    if sqlglot is None:
        return None
    try:
        text = sqlglot.parse_one(norm(sql), read=dialect).sql(dialect=dialect)
    except Exception:
        return None
    return _NOT_NULL.sub(r"\1 IS NOT NULL", text)


def engine_canon(log: dict[str, Any]) -> set[str]:
    texts: set[str | None] = {canon(e["query"], "clickhouse") for e in log.get("clickhouse", [])}
    texts |= {
        canon(e["text"].split("statement: ", 1)[1], "postgres")
        for e in log.get("postgres", [])
        if e["level"] == "LOG" and e["text"].startswith("statement: ")
    }
    return {text for text in texts if text}


def reads(sqls: list[str]) -> set[str] | None:
    """The source columns the statements read: the names of their column references, without a
    reference to a select alias of the statement (ORDER BY or GROUP BY an alias). The dialect is
    ClickHouse when the text quotes with backticks, else PostgreSQL. None when sqlglot is missing
    or cannot parse a statement."""
    if sqlglot is None or not sqls:
        return None
    out: set[str] = set()
    for sql in sqls:
        dialect = "clickhouse" if "`" in sql else "postgres"
        try:
            tree = sqlglot.parse_one(norm(sql), read=dialect)
        except Exception:
            return None
        aliases = {a.alias for a in tree.find_all(exp.Alias)}
        for column in tree.find_all(exp.Column):
            if column.name in aliases and column.find_ancestor(exp.Alias) is None:
                continue
            out.add(column.name.lower())
    return out


def ran_sqlglot(sql: str, engine: set[str]) -> bool | None:
    if sqlglot is None:
        return None
    for dialect in ("clickhouse", "postgres"):
        text = canon(sql, dialect)
        if text is not None and text in engine:
            return True
    return False


def norm(sql: str) -> str:
    text = _FORMAT.sub("", sql.strip()).rstrip(";").strip()
    out, pos, after_comment = [], 0, False
    for match in _QUOTED.finditer(text):
        gap = _WS.sub(" ", text[pos : match.start()])
        out.append("\n" + gap.lstrip() if after_comment else gap)
        token = match.group(0)
        after_comment = token.startswith("--")
        out.append(token.rstrip() if after_comment else token)
        pos = match.end()
    gap = _WS.sub(" ", text[pos:])
    out.append("\n" + gap.lstrip() if after_comment else gap)
    return "".join(out).strip()


def no_dates(sql: str) -> str:
    return _DATES.sub("'<date>'", sql)


def chart_of(entry: dict[str, Any]) -> int | None:
    sent = entry.get("sent")
    if isinstance(sent, dict):
        form = sent.get("form_data") or {}
        if form.get("slice_id") is not None:
            return int(form["slice_id"])
    match = re.search(r"slice_id%22%3A(\d+)|slice_id=(\d+)", entry.get("url", ""))
    return int(match.group(1) or match.group(2)) if match else None


def answer_sqls(entry: dict[str, Any]) -> list[str]:
    answer = entry.get("answer")
    if not isinstance(answer, dict):
        return []
    if isinstance(answer.get("result"), list):
        return [r["query"] for r in answer["result"] if isinstance(r, dict) and r.get("query")]
    if answer.get("query"):  # legacy explore_json
        return [answer["query"]]
    return []


def engine_texts(log: dict[str, Any]) -> list[str]:
    texts = [norm(e["query"]) for e in log.get("clickhouse", [])]
    texts += [
        norm(e["text"].split("statement: ", 1)[1])
        for e in log.get("postgres", [])
        if e["level"] == "LOG" and e["text"].startswith("statement: ")
    ]
    return texts


def ran(sql: str, engine: list[str]) -> bool:
    target = norm(sql)
    return bool(target) and target in engine


def rows_of(record: dict[str, Any], run: str) -> list[dict[str, Any]]:
    rows = []
    requests: dict[int, list[dict[str, Any]]] = {}
    for entry in record.get("dashboard_requests", []):
        requests.setdefault(chart_of(entry) or -1, []).append(entry)
    dash_engine = engine_texts(record.get("dashboard_engine", {}))
    dash_canon = engine_canon(record.get("dashboard_engine", {}))
    for key, chart in record["charts"].items():
        cid = int(key)
        status, detail = chart["detail"]
        result = detail.get("result", {}) if status == 200 else {}
        params = json.loads(result.get("params") or "{}")
        qc_text = result.get("query_context")
        qc = json.loads(qc_text) if qc_text else None
        get_status, get_body = chart["get_query"]
        get_sqls = (
            [r.get("query", "") for r in get_body.get("result", [])] if get_status == 200 else []
        )
        context = "dashboard"
        entries = requests.get(cid, [])
        engine, engine_c = dash_engine, dash_canon
        if not entries and key in record.get("explore", {}):
            context = "explore"
            entries = record["explore"][key]["requests"]
            engine = engine_texts(record["explore"][key]["engine"])
            engine_c = engine_canon(record["explore"][key]["engine"])
        shown = [sql for entry in entries for sql in answer_sqls(entry)]
        errors = [entry.get("status") for entry in entries if entry.get("status") not in (200, 202)]
        get_engine = engine_texts(chart["get_query_engine"])
        row = {
            "run": run,
            "dashboard": record["dashboard"],
            "chart": cid,
            "viz": result.get("viz_type") or params.get("viz_type"),
            "has_qc": qc is not None,
            "qc_fresh": (qc or {}).get("form_data") == params if qc else None,
            "get_status": get_status,
            "get_error": None if get_status == 200 else str(get_body)[:200],
            "get_n": len(get_sqls),
            "get_ran_engine": len(get_engine),
            "context": context if entries else "none",
            "requests": len(entries),
            "request_errors": errors,
            "shown_n": len(shown),
            "shown_ran": [ran(sql, engine) for sql in shown],
            "get_ran": [ran(sql, engine) for sql in get_sqls],
            "shown_ran_sqlglot": [ran(s, engine) or ran_sqlglot(s, engine_c) for s in shown],
            "get_ran_sqlglot": [ran(s, engine) or ran_sqlglot(s, engine_c) for s in get_sqls],
            "equal": bool(get_sqls) and [norm(s) for s in get_sqls] == [norm(s) for s in shown],
            "equal_but_dates": bool(get_sqls)
            and [no_dates(norm(s)) for s in get_sqls] == [no_dates(norm(s)) for s in shown],
        }
        rows.append(row)
    return rows


def main() -> None:
    rows: list[dict[str, Any]] = []
    for arg in sys.argv[1:]:
        run = Path(arg)
        for path in sorted(run.glob("dashboard_*.json")):
            rows.extend(rows_of(json.loads(path.read_text(encoding="utf-8")), run.name))
        (run / "rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        counts = Counter()
        for row in rows:
            counts["charts"] += 1
            counts["has_qc"] += row["has_qc"]
            counts["qc_fresh"] += bool(row["qc_fresh"])
            counts[f"get_{row['get_status']}"] += 1
            counts["get_ran_engine>0"] += row["get_ran_engine"] > 0
            counts[f"context_{row['context']}"] += 1
            counts["shown_all_ran"] += bool(row["shown_n"]) and all(row["shown_ran"])
            counts["get_all_ran"] += bool(row["get_n"]) and all(row["get_ran"])
            if sqlglot is not None:
                counts["shown_all_ran_sqlglot"] += bool(row["shown_n"]) and all(
                    row["shown_ran_sqlglot"]
                )
                counts["get_all_ran_sqlglot"] += bool(row["get_n"]) and all(row["get_ran_sqlglot"])
            counts["equal"] += row["equal"]
            counts["equal_but_dates"] += row["equal_but_dates"]
        print(run.name, dict(sorted(counts.items())))
        rows = []


if __name__ == "__main__":
    main()

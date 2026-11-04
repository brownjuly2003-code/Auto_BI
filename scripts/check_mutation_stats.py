#!/usr/bin/env python3
"""Fail CI unless mutmut results agree with stats and stay within the function baseline."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path, PureWindowsPath

COUNT_FIELDS = (
    "killed",
    "survived",
    "total",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
OPTIONAL_COUNT_FIELDS = ("not_checked", "caught_by_type_check")
WEAK_KINDS = ("survived", "no_tests")
EXIT_STATUSES = {
    1: "killed",
    3: "killed",
    0: "survived",
    5: "no_tests",
    33: "no_tests",
    2: "check_was_interrupted_by_user",
    None: "not_checked",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught_by_type_check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
}
FUNCTION_NAME = re.compile(r"(?:[a-zA-Z_]\w*\.)+x(?:_\w+|ǁ\w+ǁ\w+)")
MUTANT_NAME = re.compile(FUNCTION_NAME.pattern + r"__mutmut_[0-9]+")
MUTANT_DEFINITION = re.compile(r"x.+__mutmut_[0-9]+")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _baseline(path: Path) -> dict[str, dict[str, int]]:
    payload = _read_json(path)
    if not isinstance(payload, dict) or set(payload) != {"measured", "functions"}:
        raise ValueError(f"{path}: baseline must contain measured and functions")
    measured = payload["measured"]
    if not isinstance(measured, str) or not measured.strip():
        raise ValueError(f"{path}: baseline measured must be a non-empty string")
    functions = payload["functions"]
    if not isinstance(functions, dict):
        raise ValueError(f"{path}: baseline functions must be a JSON object")
    for function, counts in functions.items():
        if not FUNCTION_NAME.fullmatch(function) or "__mutmut_" in function:
            raise ValueError(f"{path}: invalid baseline function: {function}")
        if not isinstance(counts, dict) or not counts or set(counts) - set(WEAK_KINDS):
            raise ValueError(
                f"{path}: {function}: baseline requires survived and/or no_tests counts"
            )
        for kind, count in counts.items():
            if type(count) is not int or count <= 0:
                raise ValueError(f"{path}: {function}: baseline {kind} must be a positive integer")
    return functions


def _mutation_targets(path: Path) -> list[Path]:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        targets = config["tool"]["mutmut"]["only_mutate"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"{path}: only_mutate must be a non-empty list of target files")
    paths: list[Path] = []
    for target in targets:
        if not isinstance(target, str):
            raise ValueError(f"{path}: invalid only_mutate target: {target!r}")
        if any(character in target for character in "*?["):
            raise ValueError(f"{path}: only_mutate patterns are not supported: {target}")
        source = Path(target)
        windows_source = PureWindowsPath(target)
        if (
            source.anchor
            or windows_source.anchor
            or ".." in source.parts
            or ".." in windows_source.parts
            or source.suffix != ".py"
        ):
            raise ValueError(f"{path}: only_mutate target must be a relative .py file: {target}")
        if source in paths:
            raise ValueError(f"{path}: duplicate only_mutate target: {target}")
        paths.append(source)
    return paths


def _mutant_inventory(source: Path, module: str) -> set[str]:
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, ValueError, SyntaxError) as exc:
        raise ValueError(f"cannot read {source}: {exc}") from exc
    return {
        f"{module}.{node.name}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and MUTANT_DEFINITION.fullmatch(node.name)
    }


def _mutant_results(
    directory: Path, targets: list[Path]
) -> tuple[dict[str, str], list[str], set[str]]:
    if not directory.is_dir():
        raise ValueError(f"mutants directory missing or not a directory: {directory}")
    root = directory.resolve()
    errors: list[str] = []
    incomplete_functions: set[str] = set()
    inventories: dict[Path, set[str]] = {}
    for target in targets:
        source = directory / target
        meta = source.with_suffix(source.suffix + ".meta")
        for path in (source, meta):
            if not path.resolve().is_relative_to(root):
                raise ValueError(
                    f"{path}: target path must stay inside mutants directory: {directory}"
                )
        meta_exists = meta.is_file()
        if meta_exists:
            inventories[meta] = set()
        else:
            errors.append(f"{meta}: target .meta missing or not a file")
        try:
            inventory = _mutant_inventory(source, ".".join(target.with_suffix("").parts))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not inventory:
            errors.append(f"{source}: empty mutant inventory")
        if not meta_exists:
            for mutant in sorted(inventory):
                errors.append(f"{meta}: missing result: {mutant}")
                incomplete_functions.add(_function(mutant))
        else:
            inventories[meta] = inventory
    results: dict[str, str] = {}
    for path, expected in inventories.items():
        payload = _read_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("exit_code_by_key"), dict):
            raise ValueError(f"{path}: exit_code_by_key must be a JSON object")
        for mutant, code in payload["exit_code_by_key"].items():
            if not MUTANT_NAME.fullmatch(mutant):
                raise ValueError(f"{path}: invalid mutant name: {mutant}")
            if mutant in results:
                raise ValueError(f"{path}: duplicate mutant: {mutant}")
            if code is not None and type(code) is not int:
                raise ValueError(f"{path}: {mutant}: exit code must be an integer or null")
            results[mutant] = EXIT_STATUSES.get(code, "suspicious")
        actual = set(payload["exit_code_by_key"])
        for mutant in sorted(expected - actual):
            errors.append(f"{path}: missing result: {mutant}")
            incomplete_functions.add(_function(mutant))
        for mutant in sorted(actual - expected):
            errors.append(f"{path}: unexpected result: {mutant}")
    try:
        for path in sorted(directory.rglob("*.meta")):
            if path not in inventories:
                errors.append(f"{path}: .meta does not belong to a target")
    except OSError as exc:
        errors.append(f"cannot enumerate .meta files in {directory}: {exc}")
    return results, errors, incomplete_functions


def _function(mutant: str) -> str:
    return mutant.rsplit("__mutmut_", 1)[0]


def validate_stats(
    payload: object,
    results: dict[str, str],
    baseline: dict[str, dict[str, int]],
    incomplete_functions: set[str],
) -> tuple[list[str], list[str], int, int]:
    counts, errors = _counts(payload)
    actual = Counter(results.values())
    actual["total"] = len(results)
    total = actual["total"]
    effective_kills = actual["killed"] + actual["timeout"]
    if total == 0:
        errors.append("total=0: empty mutation run")
    for field, count in counts.items():
        if count != actual[field]:
            errors.append(
                f"stats {field}={count} does not match per-mutant {field}={actual[field]}"
            )

    by_function: dict[str, Counter[str]] = {}
    for mutant, status in sorted(results.items()):
        by_function.setdefault(_function(mutant), Counter())[status] += 1
        if status not in ("killed", "timeout", *WEAK_KINDS):
            errors.append(f"{mutant}: rejected status {status}")

    notices: list[str] = []
    for function in sorted(by_function.keys() | baseline.keys()):
        if function not in by_function and function not in incomplete_functions:
            notices.append(f"{function}: produced no mutants; review the baseline entry")
        for kind in WEAK_KINDS:
            count = by_function.get(function, Counter())[kind]
            allowed = baseline.get(function, {}).get(kind, 0)
            if count > allowed:
                errors.append(f"{function}: {kind}={count} exceeds baseline={allowed}")
            elif count < allowed and function not in incomplete_functions:
                notices.append(
                    f"{function}: {kind}={count} below baseline={allowed}; "
                    "baseline must be lowered in the same change"
                )
    return errors, notices, effective_kills, total


def _counts(payload: object) -> tuple[dict[str, int], list[str]]:
    if not isinstance(payload, dict):
        return {}, ["stats payload must be a JSON object"]
    counts: dict[str, int] = {}
    errors: list[str] = []
    for field in (*COUNT_FIELDS, *OPTIONAL_COUNT_FIELDS):
        if field in OPTIONAL_COUNT_FIELDS and field not in payload:
            continue
        value = payload.get(field)
        if type(value) is not int or value < 0:
            errors.append(f"{field} must be a non-negative integer")
            continue
        counts[field] = value
    return counts, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats", type=Path)
    parser.add_argument("--mutants-dir", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--pyproject", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        payload = _read_json(args.stats)
        baseline = _baseline(args.baseline)
        targets = _mutation_targets(args.pyproject)
        results, inventory_errors, incomplete_functions = _mutant_results(args.mutants_dir, targets)
    except (OSError, ValueError) as exc:
        print(f"mutation gate: {exc}", file=sys.stderr)
        return 1

    errors, notices, effective_kills, total = validate_stats(
        payload, results, baseline, incomplete_functions
    )
    errors = inventory_errors + errors
    if errors:
        print(f"mutation gate failed ({args.stats}):\n" + "\n".join(errors), file=sys.stderr)
    else:
        print(f"mutation gate: {effective_kills}/{total} effective kills, within baseline")
    for notice in notices:
        print(notice)

    measured: dict[str, dict[str, int]] = {}
    for mutant, status in results.items():
        if status in WEAK_KINDS:
            counts = measured.setdefault(_function(mutant), {})
            counts[status] = counts.get(status, 0) + 1
    if incomplete_functions:
        print("mutation gate: baseline proposal withheld because results are incomplete")
    elif measured != baseline:
        print("mutation gate: baseline proposal:")
        print(
            json.dumps(
                {
                    "measured": "TODO: fill in measurement date and environment",
                    "functions": measured,
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

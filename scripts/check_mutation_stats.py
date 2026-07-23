#!/usr/bin/env python3
"""Fail CI unless a completed mutmut smoke run has no weak outcomes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
REJECT_FIELDS = (
    "survived",
    "no_tests",
    "skipped",
    "suspicious",
    "check_was_interrupted_by_user",
    "segfault",
)


def _counts(payload: object) -> tuple[dict[str, int], list[str]]:
    if not isinstance(payload, dict):
        return {}, ["stats payload must be a JSON object"]
    counts: dict[str, int] = {}
    errors: list[str] = []
    for field in COUNT_FIELDS:
        value = payload.get(field)
        if type(value) is not int or value < 0:
            errors.append(f"{field} must be a non-negative integer")
            continue
        counts[field] = value
    return counts, errors


def validate_stats(payload: object) -> tuple[list[str], int, int]:
    counts, errors = _counts(payload)
    if errors:
        return errors, 0, 0

    total = counts["total"]
    effective_kills = counts["killed"] + counts["timeout"]
    if total == 0:
        errors.append("total=0")
    errors.extend(f"{field}={counts[field]}" for field in REJECT_FIELDS if counts[field])
    if effective_kills != total:
        errors.append(f"effective_kills={effective_kills} does not match total={total}")
    return errors, effective_kills, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats", type=Path)
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.stats.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"mutation gate: cannot read {args.stats}: {exc}", file=sys.stderr)
        return 1

    errors, effective_kills, total = validate_stats(payload)
    if errors:
        print(f"mutation gate failed: {', '.join(errors)}", file=sys.stderr)
        return 1
    print(f"mutation gate: {effective_kills}/{total} effective kills, no weak outcomes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

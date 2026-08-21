#!/usr/bin/env python3
"""Validate strategy documents from the command line.

Two ways to run it, and they differ in where you have to be standing:

    # From anywhere - the script bootstraps its own import path.
    python /path/to/prooftrade/dsl_v2/cli.py strategy.json

    # From the `prooftrade` directory, where `dsl_v2` is importable.
    python -m dsl_v2.cli examples/*.json --explain
    python -m dsl_v2.cli strategy.json --json     # machine-readable issues
    cat strategy.json | python -m dsl_v2.cli -

`python -m` needs the package on `sys.path`, which means running it from `prooftrade`.
Running the file directly works from any directory because of the bootstrap below.

Exit status: 0 when every document is valid, 1 when any has an error, 2 on bad usage.
Warnings never affect the exit status unless --strict is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Invoked as a plain script (`python dsl_v2/cli.py`), so there is no package
    # context and the relative import below would fail. Put the package's parent on
    # sys.path so the tool works from any working directory - a validator you can only
    # run from one directory is a validator people stop running.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dsl_v2.validate import validate_json
else:
    from .validate import validate_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dsl_v2.cli", description=__doc__)
    parser.add_argument("paths", nargs="+", help="JSON files, or - for stdin")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit machine-readable results")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    parser.add_argument("--explain", action="store_true",
                        help="print the plain-language rendering of valid documents")
    args = parser.parse_args(argv)

    failed = False
    payload: list[dict] = []

    for raw in args.paths:
        label = "<stdin>" if raw == "-" else raw
        try:
            text = sys.stdin.read() if raw == "-" else Path(raw).read_text()
        except OSError as exc:
            print(f"{label}: cannot read ({exc.strerror})", file=sys.stderr)
            return 2

        result = validate_json(text)
        if not result.ok or (args.strict and result.warnings):
            failed = True

        if args.as_json:
            payload.append({"document": label, **result.as_dict()})
            continue

        status = "VALID" if result.ok else "INVALID"
        print(f"{label}: {status}"
              f" ({len(result.errors)} error(s), {len(result.warnings)} warning(s))")
        if result.errors or result.warnings:
            for issue in result.errors + result.warnings:
                print(issue.format())
        if args.explain and result.strategy is not None:
            rendered = result.strategy.render()
            print(f"  {rendered['summary']}")
            print(f"  {rendered['entry']}")
            for line in rendered["exits"]:
                print(f"  exit: {line}")
            print(f"  {rendered['sizing']}")
            print(f"  {rendered['execution']}")
            print(f"  {rendered['costs']}")

    if args.as_json:
        print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

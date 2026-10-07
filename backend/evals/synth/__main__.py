"""CLI: render or check a synthetic dataset.

    uv run python -m evals.synth --dataset bills_v0            # write every case
    uv run python -m evals.synth --dataset bills_v0 --case ID  # write one case
    uv run python -m evals.synth --dataset bills_v0 --check    # compare with committed files

Exit codes: 0 ok, 1 `--check` found differences, 2 usage / spec error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.paths import EvalPaths
from evals.synth.generate import SpecError, check, generate


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: Sequence[str] | None = None, paths: EvalPaths | None = None) -> int:
    parser = _Parser(prog="python -m evals.synth", description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--case", action="append", dest="cases", help="only this case id")
    parser.add_argument("--check", action="store_true", help="compare with committed files")
    parser.add_argument("--out", type=Path, help="write into this directory instead")
    args = parser.parse_args(argv)
    paths = paths or EvalPaths()
    spec_path = paths.specs / f"{args.dataset}.yaml"
    if not spec_path.is_file():
        print(f"error: no generator spec for dataset {args.dataset!r}", file=sys.stderr)
        return 2
    try:
        if args.check:
            result = check(args.dataset, paths, args.cases)
            for case_id in result.fallback_used:
                print(f"{case_id}: raster bytes differ, equal by fallback (text layer / size)")
            for case_id in result.missing:
                print(f"{case_id}: missing in the committed dataset")
            for case_id in result.extra:
                print(f"{case_id}: committed but not in the spec")
            for case_id in result.differing:
                print(f"{case_id}: differs from the spec")
            if not result.ok:
                print("check FAILED: regenerate with `python -m evals.synth --dataset ...`")
                return 1
            print("check ok")
            return 0
        written = generate(args.dataset, paths, args.cases, args.out)
    except SpecError as exc:
        for line in exc.problems:
            print(line, file=sys.stderr)
        return 2
    print(f"wrote {len(written)} case(s) for {args.dataset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

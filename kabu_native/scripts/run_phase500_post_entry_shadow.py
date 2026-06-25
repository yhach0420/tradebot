#!/usr/bin/env python3
"""Phase500 post-entry forward shadow review runner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase500 post-entry forward shadow review")
    parser.add_argument("--day", default="")
    parser.add_argument("--session-csv", default="")
    args = parser.parse_args()

    mod_path = KABU / "src" / "research" / "phase500_post_entry_shadow_review.py"
    spec = importlib.util.spec_from_file_location("phase500_post", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    from research.structural_trade_normalize import resolve_reports_dir

    job = mod.PostEntryShadowReview(repo_root=REPO, reports_dir=resolve_reports_dir(REPO))
    session = Path(args.session_csv) if args.session_csv else None
    result = job.run(day=args.day or None, session_csv=session)
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(
        json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str),
        flush=True,
    )
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

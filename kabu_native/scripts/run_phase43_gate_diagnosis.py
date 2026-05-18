#!/usr/bin/env python3
"""
Phase 43: Small paper gate failure diagnosis.

例::
    python kabu_native/scripts/run_phase43_gate_diagnosis.py \\
        --validation-json kabu_native/results/research/logic_lab/phase41_data_oos/phase40_top_quartile_oos/top_quartile_oos_validation.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.small_paper_gate_diagnosis import (
        build_small_paper_gate_diagnosis,
        write_gate_diagnosis_outputs,
    )

    parser = argparse.ArgumentParser(description="Phase43 small paper gate diagnosis")
    parser.add_argument(
        "--validation-json",
        type=Path,
        default=native_root
        / "results"
        / "research"
        / "logic_lab"
        / "phase41_data_oos"
        / "phase40_top_quartile_oos"
        / "top_quartile_oos_validation.json",
    )
    parser.add_argument(
        "--trades-csv",
        type=Path,
        default=None,
        help="top_quartile_oos_trades.csv for peak recomputation",
    )
    parser.add_argument("--config", type=Path, default=native_root / "configs" / "small_paper_top_quartile.yaml")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    args = parser.parse_args()

    vpath = args.validation_json
    if not vpath.is_absolute():
        vpath = repo_root / vpath
    if not vpath.is_file():
        print(f"validation json not found: {vpath}", file=sys.stderr)
        return 2

    trades_csv = args.trades_csv
    if trades_csv is None:
        trades_csv = vpath.parent / "top_quartile_oos_trades.csv"
    if trades_csv and not trades_csv.is_absolute():
        trades_csv = repo_root / trades_csv

    cfg = args.config if args.config.is_absolute() else (repo_root / args.config)

    log = logging.getLogger("run_phase43")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    report = json.loads(vpath.read_text(encoding="utf-8"))
    diagnosis = build_small_paper_gate_diagnosis(
        report,
        config_path=cfg if cfg.is_file() else None,
        trades_csv=trades_csv if trades_csv.is_file() else None,
        native_root=native_root,
    )

    out_dir = args.output_dir
    if not out_dir.is_absolute():
        out_dir = native_root / out_dir
    day_key = date.today().strftime("%Y%m%d")
    json_path, csv_path = write_gate_diagnosis_outputs(diagnosis, output_dir=out_dir, day_key=day_key)

    log.info(
        "failed_gates=%s decision=%s",
        diagnosis.get("failed_gate_ids"),
        diagnosis.get("recommended_decision"),
    )
    rev = diagnosis.get("revised_candidate_evaluation") or {}
    if rev:
        log.info(
            "revised_candidate=%s peak=%s",
            rev.get("move_to_small_paper_candidate"),
            rev.get("peak_concurrent_observed"),
        )

    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"failed: {diagnosis.get('failed_gate_ids')}")
    print(f"recommended_decision: {diagnosis.get('recommended_decision')}")
    print(f"reported candidate: {diagnosis.get('move_to_small_paper_candidate_reported')}")
    if rev:
        print(f"revised candidate (fixed peak): {rev.get('move_to_small_paper_candidate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

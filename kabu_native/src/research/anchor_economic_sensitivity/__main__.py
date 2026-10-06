"""Offline decomposition runner. Paper not started. Runtime not changed."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.anchor_economic_sensitivity.decompose import run_all
from research.anchor_economic_sensitivity.load import assert_day_contract, load_prior_json, load_prior_trades
from research.anchor_economic_sensitivity.publish import build_report, write_artifacts

def main() -> int:
    print("SAFETY submit/cancel/live=0/0/0 offline_research_only", flush=True)
    print("CLOCK_GRID unchanged. No best-shift adoption.", flush=True)
    prior = load_prior_json()
    iso, port, rank_rows = load_prior_trades()
    check = assert_day_contract(iso, port)
    print(
        f"DAYS n={check['n']} dev_ok={check['development_ok']} holdout_ok={check['holdout_ok']} "
        f"iso={check['isolated_rows']} port={check['portfolio_rows']} rank_rows={len(rank_rows)}",
        flush=True,
    )
    if not check["development_ok"] or not check["holdout_ok"]:
        print("BLOCKED day contract mismatch", check, flush=True)
        return 2
    result = run_all(
        comparisons=prior.get("comparisons") or [],
        iso=iso,
        port=port,
        rank_rows=rank_rows,
    )
    clf = result["classify"]
    report = build_report(inventory_check=check, prior_meta=prior, result=result)
    paths = write_artifacts(report)
    print(report.get("verdict"), paths["report_json"], flush=True)
    print(
        f"PRIMARY={clf.get('PRIMARY_ROOT_CAUSE')} SECONDARY={clf.get('SECONDARY_ROOT_CAUSE')} "
        f"RANK={clf.get('CROSS_SECTIONAL_RANK_ROBUSTNESS')} ECON={clf.get('ECONOMIC_TIMING_ROBUSTNESS')}",
        flush=True,
    )
    print("STOP CLOCK_GRID / ENTRY / EXIT unchanged. submit/cancel/live=0/0/0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

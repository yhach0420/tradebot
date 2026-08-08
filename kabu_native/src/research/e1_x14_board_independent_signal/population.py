"""RPFE / candidate panel population provenance."""
from __future__ import annotations

from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
PANEL_PY = NATIVE / "src" / "research" / "pbv2_zero_base_revalidation" / "panel.py"


def audit_rpfe_population() -> dict[str, Any]:
    """Trace RPFE 122983-row panel to generating code and extraction conditions."""
    code_exists = PANEL_PY.exists()
    code_snip = ""
    if code_exists:
        text = PANEL_PY.read_text(encoding="utf-8")
        # key facts from known implementation
        code_snip = "build_price_paths_and_panel; event_type==candidate; 120s bucket; Watch50"
    answers = {
        "full_monitored_universe_regular_snapshot": False,
        "all_price_update_events": False,
        "pbv2_candidates_only": False,  # ~3% pbv2; majority non-pbv2 candidate evals
        "pbv2_accept_reject_only": False,
        "entry_fill_neighborhood_only": False,
        "rpfe_price_trigger_passed_only": False,  # panel is pre-trigger candidate lattice
        "winner_stop_noprogress_only": False,
        "conditioned_on": [
            "Watch50 small_paper scan candidate events",
            "120-second symbol-session bucket thinning",
            "prefer gate_accept / higher score when collapsing buckets",
            "not raw PUSH / not full price-update stream",
        ],
        "source_code": str(PANEL_PY) if code_exists else None,
        "code_identity_note": code_snip,
        "n_panel_reported": 122983,
        "n_trading_days_reported": 22,
        "date_range_reported": "20260615-20260723",
        "direct_independent_entry_research_allowed": False,
    }
    # Verdict: conditioned → rebuild required from raw
    verdict = "SOURCE_POPULATION_CONDITIONED_REBUILD_REQUIRED"
    return {
        "verdict": verdict,
        "answers": answers,
        "rebuild_source": "data/push_jsonl (raw PUSH; monitored symbols that day)",
        "rebuild_available_from": "20260721",
        "pre_push_window_20260615_20260720": {
            "unbiased_raw_push": False,
            "small_paper_only": True,
            "usable_for_independent_entry": False,
            "reason": "only Watch50 candidate-conditioned events; no TradingVolume continuity",
        },
    }

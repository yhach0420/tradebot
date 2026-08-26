"""Write and freeze P4-4 precommit.json. SHA is the ablation identity. No change after SHA."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.early_guard_exact_ablation_p4_4 import (
    CANONICAL_GUARD_N,
    CLASS_HARMFUL,
    CLASS_MIXED,
    CLASS_SUPPORTED,
    DOCUMENT_ID,
    GUARD_EXIT_REASON,
    HIST_FOREGONE_WINNER,
    HIST_RATIO,
    HIST_SAVED_LOSS,
    P1_MAXDD,
    P1_PF,
    P1_PNL,
    P1_TRADES,
    TASK_LABEL,
    VARIANT_ID,
)
from small_paper.v1r_exit_v2_contract import FROZEN_CONTINUATION, FROZEN_GUARD

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "early_guard_exact_ablation_p4_4"
JST = timezone(timedelta(hours=9))


def contract_body() -> dict[str, Any]:
    return {
        "task": "P4-4",
        "ANALYSIS_ID": "P4_4_EARLY_GUARD_EXACT_ABLATION",
        "DOCUMENT_ID": DOCUMENT_ID,
        "LABEL": TASK_LABEL,
        "NOT": ["OOS", "prospective", "robust", "new strategy validation", "production approval"],
        "baseline_identity": {
            "trades": P1_TRADES,
            "pnl": P1_PNL,
            "PF": P1_PF,
            "maxDD": P1_MAXDD,
            "early_guard_exit_reason": GUARD_EXIT_REASON,
            "early_guard_n": CANONICAL_GUARD_N,
            "match_required": "89/89 fill_key (date, symbol, fill_time)",
        },
        "ablation": {
            "id": VARIANT_ID,
            "disable": "0-120s Early Guard trigger only (detect_guard_trigger not applied)",
            "unchanged": [
                "ENTRY",
                "selection",
                "Clock",
                "fill",
                "CAP",
                "same-symbol",
                "600 Continuation Gate",
                "750 Extension",
                "session close",
            ],
            "architecture_when_off": "Arch E with guard=None, cont_rule=FROZEN_CONTINUATION",
            "not": "rewrite 89 trades as 600-hold without state-machine",
        },
        "frozen_guard": dict(FROZEN_GUARD),
        "frozen_continuation": dict(FROZEN_CONTINUATION),
        "state_machine_semantics": {
            "engine": "CollectorEngine + Dual Lane exact replay (same as P4-1 baseline)",
            "includes": [
                "ENTRY",
                "PENDING",
                "FILL",
                "OPEN",
                "CAP",
                "same-symbol",
                "EXIT",
                "SLOT RELEASE",
            ],
            "guard_off_patch": (
                "small_paper.v1r_live_dual_lane.apply_arch_e_to_bundle "
                "-> apply_architecture(arch=E, guard=None, cont_rule=FROZEN_CONTINUATION)"
            ),
            "no_mid_hold_gate": True,
        },
        "comparison_metrics": {
            "portfolio": "FULL14 / TOP3 / REST11 trades W/L/D GP GL PnL PF maxDD AM/PM exit reasons",
            "local_89": "destination and PnL of baseline IMBALANCE fills under Guard OFF",
            "saved_foregone": (
                "On matched overlapping fills: wait_pnl = Guard-OFF pnl, "
                "guard_pnl = baseline Guard-ON pnl, delta = guard_pnl - wait_pnl. "
                "saved_loss += delta if wait_pnl < 0 and delta > 0. "
                "foregone_winner += -delta if wait_pnl > 0 and delta < 0. "
                "net_guard_value = saved_loss - foregone_winner."
            ),
            "portfolio_effect": "newly/lost admit/fill, same-symbol, capacity",
            "historical_reference_not_target": {
                "saved_loss": HIST_SAVED_LOSS,
                "foregone_winner": HIST_FOREGONE_WINNER,
                "ratio": HIST_RATIO,
            },
        },
        "classification": {
            "SUPPORTED": (
                "FULL14 baseline PnL > Guard-OFF PnL AND REST11 baseline PnL > Guard-OFF PnL "
                "AND net_guard_value > 0"
            ),
            "HARMFUL": (
                "FULL14 baseline PnL < Guard-OFF PnL AND REST11 baseline PnL < Guard-OFF PnL "
                "AND net_guard_value < 0"
            ),
            "MIXED": "neither SUPPORTED nor HARMFUL (excluding integrity)",
            "ids": {
                "SUPPORTED": CLASS_SUPPORTED,
                "MIXED": CLASS_MIXED,
                "HARMFUL": CLASS_HARMFUL,
            },
            "not_strategy_adoption": True,
        },
        "no_retune": {
            "no_imb_threshold_search": True,
            "no_persist_search": True,
            "no_monitor_to_search": True,
            "one_comparison_only": "current Guard ON vs EARLY_GUARD_OFF_ONLY",
            "no_mid_hold_reopen": True,
            "no_runtime_change": True,
        },
    }


def contract_sha(body: dict[str, Any]) -> str:
    blob = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_precommit() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    body = contract_body()
    sha = contract_sha(body)
    doc = {
        **body,
        "SHA": sha,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "note": "SHA covers contract_body only. Contract must not change after this SHA exists.",
    }
    path = OUT / "precommit.json"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("SHA") != sha:
            raise RuntimeError(
                f"P4_4_PRECOMMIT_SHA_DRIFT disk={old.get('SHA')} now={sha}. "
                "Contract change after SHA is forbidden."
            )
        return old
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc

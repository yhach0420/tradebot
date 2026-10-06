"""Run the sealed sequential-setup study and stop before entry execution."""
from __future__ import annotations

import json

import numpy as np

from research.stock_specific_sequential_setup import (
    ANALYSIS_ID,
    C1_FIRST,
    C1_LAST,
    CAPTURE_FIRST,
    CAPTURE_LAST,
    DEV_FIRST,
    DEV_LAST,
    FAMILIES,
    FV_FIRST,
    UNIVERSE_ID,
    UNIVERSE_SHA256,
)
from research.stock_specific_sequential_setup.capture import confirm
from research.stock_specific_sequential_setup.decide import decide
from research.stock_specific_sequential_setup.episodes import self_check
from research.stock_specific_sequential_setup.publish import publish
from research.stock_specific_sequential_setup.scan import scan


EXPLANATIONS = {
    "PULLBACK_HIGH_RECLAIM": [
        "Trend context is the reused EMA9>EMA21 and rising EMA21 state, not the entry.",
        "The trigger buys only after a pullback, so it is not the impulse high itself.",
        "Pullback is a later close back below the impulse close.",
        "Stabilization is the first later bar that stops making a lower low and a lower close.",
        "Reacceleration is a close back through the pullback's own prior high.",
        "The setup is invalid when EMA trend context fails, or when a new pullback low clears stabilization before that reclaim.",
    ],
    "EMA9_RECLAIM": [
        "Trend context remains the rising EMA9/EMA21 regime.",
        "Entry is after price has traded at or below EMA9 during the pullback, so it is not the first break of the high.",
        "Pullback is a close back below the impulse close.",
        "Stabilization is a bar that no longer extends the pullback low and no longer closes down.",
        "Reacceleration is a close back above EMA9 after that touch.",
        "The setup is invalid when trend context fails or price undercuts the pullback low before the reclaim.",
    ],
    "PRIOR_BAR_HIGH_RECLAIM": [
        "Trend context is the same EMA regime.",
        "The trigger requires a prior pullback, so a single new high is not enough.",
        "Pullback is a close back below the impulse close.",
        "Stabilization is the first bar that stops extending the decline.",
        "Reacceleration is a close above the immediately prior bar's high.",
        "The setup is invalid when trend context fails or the pullback low is extended again.",
    ],
    "CLOSE_PROGRESSION": [
        "Trend context is the EMA regime only.",
        "A higher close after a pullback is not by itself a break of resistance.",
        "Pullback is a close back below the impulse close.",
        "Stabilization requires both a non-lower low and a non-lower close.",
        "Reacceleration is the strict higher close on or after stabilization.",
        "The setup is invalid when trend context fails.",
    ],
    "VOLUME_REEXPANSION": [
        "Trend context is the EMA regime.",
        "Volume expansion is measured after the pullback, not at the impulse high.",
        "Pullback is a close back below the impulse close.",
        "Stabilization is the pause in lower lows and lower closes.",
        "Reacceleration is volume above the prior five completed bars, the existing V1 window, with no multiplier search.",
        "The setup is invalid when trend context fails. Volume alone does not keep it valid.",
    ],
    "RCI_RECOVERY": [
        "Trend context is the EMA regime.",
        "RCI recovery is recorded after the pullback low, not as a cross through -80.",
        "Pullback is a close back below the impulse close.",
        "Stabilization is the price pause. RCI does not define that pause.",
        "Reacceleration is RCI rising from its pullback minimum. The V1 cross is not this trigger.",
        "The setup is invalid when trend context fails. An RCI cross without the prior stages does not create an entry.",
    ],
}


def _median(xs) -> float | None:
    arr = np.asarray([np.nan if x is None else float(x) for x in xs], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _state_rows(scanned: dict) -> tuple[list, list, list]:
    volume, rci, structure = [], [], []
    for name in FAMILIES:
        bucket = scanned["signals"][name]
        volume.append(
            {
                "family": name,
                "pullback_over_impulse_median": _median(bucket["vol_pb_ratio"]),
                "reacceleration_over_pullback_median": _median(bucket["vol_re_ratio"]),
                "signed_volume_gate": False,
            }
        )
        rci.append(
            {
                "family": name,
                "rci_impulse_median": _median(bucket["rci_impulse"]),
                "rci_pullback_min_median": _median(bucket["rci_min"]),
                "rci_signal_median": _median(bucket["rci_signal"]),
                "rci_cross_is_entry": False,
            }
        )
        structure.append({"family": name, "room_to_resistance_bps_median": _median(bucket["room"]), "structure_bars": 20, "used_as_fitted_gate": False})
    return volume, rci, structure


def _rows_for(decision: dict) -> dict[str, list]:
    families = decision["families"]
    reaccel = []
    dev = []
    c1 = []
    folds = []
    conc = []
    for name in FAMILIES:
        row = families[name]
        reaccel.append({"family": name, "pass": row["pass"], "n": row["n"], "median_ret3_bps": row["median_ret3_bps"], "median_ret5_bps": row["median_ret5_bps"], "median_ret10_bps": row["median_ret10_bps"], "median_mfe5_bps": row["median_mfe5_bps"], "median_mae5_bps": row["median_mae5_bps"], "failed_gates": [k for k, v in row["gates"].items() if not v]})
        dev.append({"family": name, "n": row["dev_n"], "median_ret3_bps": row["dev_median_ret3_bps"], "median_ret5_bps": row["dev_median_ret5_bps"], "median_ret10_bps": row["dev_median_ret10_bps"], "median_mfe5_bps": row["dev_median_mfe5_bps"], "median_mae5_bps": row["dev_median_mae5_bps"]})
        c1.append({"family": name, "n": row["c1_n"], "median_ret3_bps": row["c1_median_ret3_bps"], "median_ret5_bps": row["c1_median_ret5_bps"], "median_ret10_bps": row["c1_median_ret10_bps"], "median_mfe5_bps": row["c1_median_mfe5_bps"], "median_mae5_bps": row["c1_median_mae5_bps"]})
        for item in row["dev_folds"]:
            folds.append({"period": "DEV", "family": name, **item})
        for item in row["c1_folds"]:
            folds.append({"period": "C1", "family": name, **item})
        conc.append({"family": name, "top_day": row["top_day"], "top_day_share": row["top_day_share"], "top_symbol": row["top_symbol"], "top_symbol_share": row["top_symbol_share"]})
    return {"reacceleration_rows": reaccel, "dev_rows": dev, "c1_rows": c1, "fold_rows": folds, "concentration_rows": conc}


def main() -> int:
    checked = self_check()
    if not all(checked.values()):
        print(json.dumps({"VERDICT": "FAIL_CLOSED", "SELF_CHECK": checked}), flush=True)
        return 2
    scanned = scan()
    decision = decide(scanned)
    volume_rows, rci_rows, structure_rows = _state_rows(scanned)
    del scanned["signals"]
    del scanned["features"]
    selected = decision["selected"]
    capture = {"ran": False, "n": 0, "reason": "historical_mechanism_not_supported", "window": f"{CAPTURE_FIRST}:{CAPTURE_LAST}"}
    actionability = {"ran": False, "primary_horizon_sec": 180, "pass_180s": False, "reason": "capture_confirmation_not_run"}
    if selected:
        capture = confirm(selected)
        passed = bool(capture.get("pass"))
        actionability = {"ran": True, "primary_horizon_sec": 180, "pass_180s": passed, "raw_mid_180": capture.get("raw_mid_180"), "bid_anchor_180": capture.get("bid_anchor_180"), "ask_to_bid_180": capture.get("ask_to_bid_180")}
        if not passed:
            decision["verdict"] = "STOCK_SPECIFIC_SEQUENTIAL_SETUP_MECHANISM_NOT_SUPPORTED_V1"
            decision["next"] = "STOP"
            decision["failed_transition"] = f"CAPTURE_{capture.get('failed_gate')}"
            selected = None
    packed = _rows_for(decision)
    explanation = []
    if selected:
        for i, text in enumerate(EXPLANATIONS[selected], start=1):
            explanation.append({"mechanism": selected, "question": i, "answer": text})
    else:
        explanation.append({"mechanism": None, "question": "why_none", "answer": "No reacceleration family passed the frozen direction, fold, period, support, and concentration gates."})
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "historical_episode_n": scanned["episode_n"],
        "selected_primary_mechanism": selected,
        "failed_transition": decision["failed_transition"],
        "min_date": scanned["min_date"],
        "max_date": scanned["max_date"],
        "manifest": {"analysis_id": ANALYSIS_ID, "verdict": decision["verdict"], "next": decision["next"], "selected": selected},
        "data_seal": {
            "historical_first": DEV_FIRST,
            "historical_last": C1_LAST,
            "dev": f"{DEV_FIRST}:{DEV_LAST}",
            "c1": f"{C1_FIRST}:{C1_LAST}",
            "frozen_validation_not_opened": True,
            "fv_first_excluded": FV_FIRST,
            "capture_window_not_read": False if capture.get("ran") else f"{CAPTURE_FIRST}:{CAPTURE_LAST}",
            "max_date_read": scanned["max_date"],
            "rows_read": scanned["rows_read"],
            "NEW_DATA_ACQUIRED": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "PROSPECTIVE_ROWS_READ": 0,
        },
        "universe": {
            "id": UNIVERSE_ID,
            "sha256": scanned["universe_sha256"],
            "expected_sha256": UNIVERSE_SHA256,
            "n": scanned["universe_n"],
            "order_sha256": scanned["symbols_order_sha256"],
            "v1_signal_population_is_sole_universe": False,
        },
        "episode_definition": [
            {"stage": "TREND_CONTEXT", "rule": "EMA9[t] > EMA21[t] AND EMA21[t] > EMA21[t-3]. Not an entry."},
            {"stage": "IMPULSE", "rule": "Inside that trend, close rises and high exceeds the episode's prior structure high. Prior displacement only."},
            {"stage": "PULLBACK", "rule": "A later close below the impulse close. No depth threshold was fit."},
            {"stage": "STABILIZATION", "rule": "A later bar whose low is not below the prior low and whose close is not below the prior close."},
            {"stage": "REACCELERATION", "rule": "One of the six frozen primitives, first bar only, after stabilization. V1 RCI cross is not a primitive."},
        ],
        "trend_rows": [row for row in decision["stages"] if row["stage"] == "TREND_CONTEXT"],
        "impulse_rows": [row for row in decision["stages"] if row["stage"] == "IMPULSE"],
        "pullback_rows": decision["features"] + [row for row in decision["stages"] if row["stage"] == "PULLBACK"],
        "volume_rows": volume_rows,
        "rci_rows": rci_rows,
        "structure_rows": structure_rows,
        "stage_counts": {"episodes": scanned["episode_n"], "impulse": scanned["impulse_n"], "pullback": scanned["pullback_n"], "stabilization": scanned["stabilization_n"]},
        **packed,
        "capture": capture,
        "actionability": actionability,
        "explanation_rows": explanation,
        "verdict_row": {"verdict": decision["verdict"], "next": decision["next"], "selected": selected, "failed_transition": decision["failed_transition"], "strategy_pass": False},
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0, "ENTRY_FROZEN": False, "EXIT_RESEARCHED": False, "COMPLETE_STRATEGY_RUN": False, "OLD_EXIT_COLLECTOR_UPDATED": False, "RANGE_BREAK_USED": False, "DIRECTIONAL_VOLUME_USED": False, "DO_NOT_OPTIMIZE_OLD_EXIT": True, "DO_NOT_WAIT_FOR_MORE_EXIT_DATA": True},
        "families": decision["families"],
        "self_check": checked,
    }
    if scanned["universe_sha256"] != UNIVERSE_SHA256 or scanned["max_date"] > C1_LAST:
        report["verdict"] = "FAIL_CLOSED"
        report["next"] = "STOP"
    publish(report)
    chosen = decision["families"].get(selected) if selected else None
    print(
        json.dumps(
            {
                "VERDICT": report["verdict"],
                "NEXT": report["next"],
                "EPISODES": scanned["episode_n"],
                "SELECTED": selected,
                "FAILED": decision["failed_transition"],
                "MAX_DATE": scanned["max_date"],
                "DEV": None if not chosen else [chosen["dev_n"], chosen["dev_median_ret5_bps"]],
                "C1": None if not chosen else [chosen["c1_n"], chosen["c1_median_ret5_bps"]],
            }
        ),
        flush=True,
    )
    return 0 if report["verdict"] != "FAIL_CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

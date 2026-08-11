"""EXIT candidate space generators for V1R global search."""
from __future__ import annotations

from typing import Any


def generate_all_candidates() -> list[dict[str, Any]]:
    """Large but finite candidate set. Parameters chosen on TRAIN only later."""
    cands: list[dict[str, Any]] = []

    # A. Fixed time
    for h in (30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 750, 900):
        cands.append({"family": "TIME", "id": f"TIME_{h}", "fixed_hold_sec": float(h)})

    # B. Fixed stop + 600 fallback
    for s in (20, 30, 40, 50, 75, 100, 150, 200, 300):
        cands.append({
            "family": "STOP", "id": f"STOP_{s}_T600",
            "hard_stop_bps": float(s), "fixed_hold_sec": 600.0,
        })

    # C. Fixed take + 600
    for t in (20, 30, 40, 50, 75, 100, 150, 200, 300):
        cands.append({
            "family": "TAKE", "id": f"TAKE_{t}_T600",
            "profit_target_bps": float(t), "fixed_hold_sec": 600.0,
        })

    # D. STOP + TAKE + 600
    for s in (30, 50, 75, 100, 150):
        for t in (50, 75, 100, 150, 200):
            if t <= s:
                continue
            cands.append({
                "family": "STOP_TAKE", "id": f"ST_{s}_TP_{t}_T600",
                "hard_stop_bps": float(s), "profit_target_bps": float(t),
                "fixed_hold_sec": 600.0,
            })

    # E. MFE trailing
    for act in (20, 30, 45, 60, 75, 100, 150):
        for gb in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            for minh in (0.0, 30.0, 60.0, 120.0):
                cands.append({
                    "family": "TRAIL",
                    "id": f"TRAIL_a{act}_g{int(gb*100)}_m{int(minh)}_T600",
                    "trail_activate_bps": float(act),
                    "trail_giveback_frac": float(gb),
                    "trail_min_hold_sec": float(minh),
                    "fixed_hold_sec": 600.0,
                })

    # F. Absolute MFE giveback
    for act in (30, 45, 60, 100):
        for absg in (20, 30, 40, 50, 75, 100):
            cands.append({
                "family": "ABS_GIVEBACK",
                "id": f"ABSGB_a{act}_g{absg}_T600",
                "trail_activate_bps": float(act),
                "trail_giveback_abs_bps": float(absg),
                "fixed_hold_sec": 600.0,
            })

    # G. MAE recovery failure
    for mae_t in (20, 30, 40, 50, 75, 100):
        for win in (5, 10, 20, 30, 45, 60):
            for need in (0, 10, 20):  # recovery to fill+need; 0 = back to flat
                cands.append({
                    "family": "MAE_RECOVERY",
                    "id": f"MAER_m{mae_t}_w{win}_r{need}_T600",
                    "mae_trigger_bps": float(mae_t),
                    "mae_recovery_window_sec": float(win),
                    "mae_recovery_need_bps": float(need),
                    "fixed_hold_sec": 600.0,
                })

    # H. No progress
    for sec in (30, 45, 60, 90, 120, 180):
        for mfe in (10, 20, 30, 40, 50):
            cands.append({
                "family": "NO_PROGRESS",
                "id": f"NOP_t{sec}_m{mfe}_T600",
                "no_progress_sec": float(sec),
                "no_progress_min_mfe": float(mfe),
                "fixed_hold_sec": 600.0,
            })
        for ret_th in (0, 10, 20):
            cands.append({
                "family": "NO_PROGRESS",
                "id": f"NOP_t{sec}_ret{ret_th}_T600",
                "no_progress_sec": float(sec),
                "no_progress_min_ret": float(ret_th),
                "fixed_hold_sec": 600.0,
            })

    # I. Early failure / state transition (ret path sequences)
    for off in (5, 10, 20, 30, 45, 60, 90):
        for mae_t in (20, 30, 40, 50, 75):
            for ret_t in (10, 20, 30, 40):
                cands.append({
                    "family": "EARLY_FAIL",
                    "id": f"EF_o{off}_m{mae_t}_r{ret_t}_T600",
                    "early_off_sec": float(off),
                    "early_mae_bps": float(mae_t),
                    "early_ret_bps": float(ret_t),
                    "fixed_hold_sec": 600.0,
                })
    # sequence: worsen at t1, no rebound by t2
    for t1, t2 in ((10, 30), (20, 45), (30, 60), (30, 90), (45, 90)):
        for drop in (20, 30, 40, 50):
            cands.append({
                "family": "STATE_SEQ",
                "id": f"SEQ_{t1}_{t2}_d{drop}_T600",
                "seq_t1": float(t1),
                "seq_t2": float(t2),
                "seq_drop_bps": float(drop),
                "fixed_hold_sec": 600.0,
            })

    # J. Imbalance reversal (needs board feats)
    for pers in (5, 10, 20, 30):
        for thr in (-0.1, -0.2, -0.3, -0.4):
            cands.append({
                "family": "IMBALANCE",
                "id": f"IMB_p{pers}_t{int(thr*100)}_T600",
                "imb_persist_sec": float(pers),
                "imb_threshold": float(thr),
                "fixed_hold_sec": 600.0,
            })

    # K. Bid depth collapse
    for pers in (5, 10, 20, 30):
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7):
            cands.append({
                "family": "BID_DEPTH",
                "id": f"BD_p{pers}_f{int(frac*100)}_T600",
                "bid_depth_persist_sec": float(pers),
                "bid_depth_drop_frac": float(frac),
                "fixed_hold_sec": 600.0,
            })

    # L. Spread expansion
    for mult in (1.5, 2.0, 3.0):
        cands.append({
            "family": "SPREAD",
            "id": f"SPR_x{mult}_T600",
            "spread_mult": float(mult),
            "fixed_hold_sec": 600.0,
        })
    for ab in (15, 25, 40, 60):
        cands.append({
            "family": "SPREAD",
            "id": f"SPR_abs{ab}_T600",
            "spread_abs_bps": float(ab),
            "fixed_hold_sec": 600.0,
        })

    # M. Event-rate decay
    for win in (10, 20, 30, 60):
        for frac in (0.75, 0.5, 0.25):
            cands.append({
                "family": "EVENT_DECAY",
                "id": f"ER_w{win}_f{int(frac*100)}_T600",
                "er_window_sec": float(win),
                "er_frac": float(frac),
                "fixed_hold_sec": 600.0,
            })

    # N. Momentum fade
    for look in (10, 20, 30, 60):
        for thr in (-5, -10, -20, 0):
            cands.append({
                "family": "MOM_FADE",
                "id": f"MF_l{look}_t{thr}_T600",
                "mom_look_sec": float(look),
                "mom_ret_bps": float(thr),
                "require_prior_mfe": 30.0,
                "fixed_hold_sec": 600.0,
            })

    # O. Sell pressure failure (scenario)
    for off in (20, 30, 45, 60):
        for mae_t in (30, 40, 50, 75):
            for ret_t in (15, 25, 35):
                cands.append({
                    "family": "SELL_FAIL",
                    "id": f"SF_o{off}_m{mae_t}_r{ret_t}_T600",
                    "early_off_sec": float(off),
                    "early_mae_bps": float(mae_t),
                    "early_ret_bps": float(ret_t),
                    "require_no_rebound": True,
                    "fixed_hold_sec": 600.0,
                })

    # P. Recovery completion
    for act in (30, 45, 60, 100):
        for gb in (0.4, 0.5, 0.6, 0.7):
            for minh in (30, 60, 120):
                cands.append({
                    "family": "RECOVERY_DONE",
                    "id": f"RD_a{act}_g{int(gb*100)}_m{minh}_T600",
                    "trail_activate_bps": float(act),
                    "trail_giveback_frac": float(gb),
                    "trail_min_hold_sec": float(minh),
                    "fixed_hold_sec": 600.0,
                })

    # Hybrid: stop + trail
    for s in (50, 75, 100):
        for act in (45, 75):
            for gb in (0.4, 0.5):
                cands.append({
                    "family": "HYBRID",
                    "id": f"HY_S{s}_Ta{act}_g{int(gb*100)}_T600",
                    "hard_stop_bps": float(s),
                    "trail_activate_bps": float(act),
                    "trail_giveback_frac": float(gb),
                    "fixed_hold_sec": 600.0,
                })
    # Hybrid: no-progress + trail
    for sec in (60, 90):
        for act in (45, 75):
            cands.append({
                "family": "HYBRID",
                "id": f"HY_NP{sec}_Ta{act}_T600",
                "no_progress_sec": float(sec),
                "no_progress_min_mfe": 20.0,
                "trail_activate_bps": float(act),
                "trail_giveback_frac": 0.5,
                "fixed_hold_sec": 600.0,
            })
    # Hybrid: early fail + trail
    for off in (30, 45):
        for act in (45, 75):
            cands.append({
                "family": "HYBRID",
                "id": f"HY_EF{off}_Ta{act}_T600",
                "early_off_sec": float(off),
                "early_mae_bps": 40.0,
                "early_ret_bps": 25.0,
                "trail_activate_bps": float(act),
                "trail_giveback_frac": 0.5,
                "fixed_hold_sec": 600.0,
            })

    # Baseline marker
    cands.append({"family": "BASELINE", "id": "FIXED600", "fixed_hold_sec": 600.0})

    # de-dupe by id
    seen = set()
    out = []
    for c in cands:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


def family_counts(cands: list[dict[str, Any]]) -> dict[str, int]:
    from collections import Counter
    return dict(Counter(c["family"] for c in cands))

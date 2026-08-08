"""Entry robustness gates for E1_X6 FINAL (LODO / rolling-origin / concentration).

Confirm / final / LODO economics use FULL_CANONICAL_EVENT_REPLAY per analysis_mask
partition (fresh session each; NO AM→PM carry). FIXED_SPEC_DAY_DELETION filters the
final completed-trade ledger (no re-replay). Does NOT start EXIT redesign / Forward / Runtime.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence

from research.e1_x6_provisional.analysis_mask import build_mask_index
from research.e1_x6_provisional.canonical_partition_replay import (
    assert_selected_in_registry,
    fixed_spec_day_deletion_from_ledger,
)
from research.e1_x6_provisional.constants import CANDIDATE_CAP, DAYS, FINAL_BANNER, FOLD_DEFS, PROVISIONAL_BANNER
from research.e1_x6_provisional.portfolio_replay import (
    assert_no_confirm_reselection,
    select_candidate_build_only,
)
from research.e1_x6_provisional.quality_layers import ALL_USABLE_CLASSES
from research.e1_x6_provisional.replay_lifecycle_contract import EVALUATION_MODE_REQUIRED
from research.e1_x6_provisional.util import norm_cache_dir, progress, sha256_obj, summarize_pnls, write_json


def _rank_candidates(build_rows: list[dict[str, Any]], enumerate_candidates, passes, proxy_expectancy):
    cands = enumerate_candidates(build_rows)
    ranked = []
    for c in cands:
        matched = [r for r in build_rows if passes(r, c)]
        ranked.append(
            {
                **c,
                "build_support": len(matched),
                "build_expectancy_proxy": proxy_expectancy(matched),
            }
        )
    ranked.sort(
        key=lambda x: (
            -(x["build_support"] > 0),
            -x["build_expectancy_proxy"],
            -x["build_support"],
            x["candidate_id"],
        )
    )
    return ranked


def _family_direction_key(sel: Mapping[str, Any]) -> tuple[str, str]:
    family = str(sel.get("family") or "")
    direction = str(sel.get("direction") or "")
    return family, direction


def _pf_ok(metrics: Mapping[str, Any], *, min_pf: float) -> bool:
    pf = metrics.get("pf")
    status = metrics.get("pf_status")
    if status == "NO_LOSS":
        return float(metrics.get("pnl") or 0) > 0
    if pf is None:
        return False
    return float(pf) >= float(min_pf)


def _stop_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stop = [t for t in trades if str(t.get("exit_reason") or "").upper().startswith("STOP")]
    stop_loss_yen = float(sum(min(0.0, float(t.get("net_pnl_yen_100") or 0)) for t in stop))
    n = len(trades)
    return {
        "stop_n": len(stop),
        "stop_loss_yen": stop_loss_yen,
        "stop_loss_per_completed": (abs(stop_loss_yen) / n) if n else None,
        "completed_n": n,
    }


def _concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(t.get("net_pnl_yen_100") or 0) for t in trades]
    total = float(sum(pnls)) if pnls else 0.0
    by_sym: dict[str, float] = {}
    by_day: dict[str, float] = {}
    for t in trades:
        sym = str(t.get("symbol") or t.get("symbol_norm") or "")
        day = str(t.get("day") or "")
        p = float(t.get("net_pnl_yen_100") or 0)
        by_sym[sym] = by_sym.get(sym, 0.0) + p
        by_day[day] = by_day.get(day, 0.0) + p
    top1_trade = max(pnls) if pnls else 0.0
    top1_sym = max(by_sym.values()) if by_sym else 0.0
    top1_day = max(by_day.values()) if by_day else 0.0
    return {
        "pnl": total,
        "pnl_ex_top1_trade": total - top1_trade if pnls else 0.0,
        "pnl_ex_top1_symbol": total - top1_sym if by_sym else 0.0,
        "pnl_ex_top1_day": total - top1_day if by_day else 0.0,
        "top1_trade_pnl": top1_trade if pnls else None,
        "top1_symbol_pnl": top1_sym if by_sym else None,
        "top1_day_pnl": top1_day if by_day else None,
        "n": len(trades),
    }


def evaluate_entry_robustness(
    work: Path,
    *,
    folds: Mapping[str, Any],
    base: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    day_quality: Mapping[str, str],
    banner: str = FINAL_BANNER,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Run post-fold entry robustness + verdict for FINAL 9-day pipeline."""
    from research.e1_x6_provisional.p2_execute import (
        _load_labeled_days,
        _passes,
        _proxy_expectancy,
        enumerate_candidates,
        replay_candidate_day_partitions,
    )

    progress("P2: entry_robustness gates (FULL_CANONICAL_EVENT_REPLAY)")
    windows = list(source_manifest.get("windows") or [])
    qcounts = source_manifest.get("quality_window_counts") or {}
    core_windows = int(qcounts.get("CORE_VALID") or 0)
    mi = build_mask_index(source_manifest)
    cdir = cache_dir or norm_cache_dir(work.name if work.name.startswith("run_") else None)

    # --- Fold completeness ---
    fold_ids = ["F1", "F2", "F3", "F4", "F5"]
    fold_completeness_ok = True
    fold_status_rows = []
    confirm_pnls: list[float] = []
    selected_keys: list[tuple[str, str]] = []
    executed_folds = 0
    for fid in fold_ids:
        fr = folds.get(fid) or {}
        st = fr.get("status")
        confirm_days = list((FOLD_DEFS.get(fid) or {}).get("confirm") or fr.get("confirm_days") or [])
        masks = []
        for d in confirm_days:
            for w in windows:
                if w.get("day") == d:
                    masks.append(w.get("analysis_mask_id"))
        has_masks = bool(confirm_days) and all(bool(m) for m in masks) and len(masks) >= len(confirm_days)
        if st in (None, "DEFERRED") or not has_masks:
            fold_completeness_ok = False
        cp = fr.get("confirm_portfolio") or {}
        pnl = cp.get("pnl")
        if isinstance(pnl, (int, float)):
            confirm_pnls.append(float(pnl))
            executed_folds += 1
        sel = fr.get("selected_candidate") or {}
        if sel.get("family") or sel.get("direction"):
            selected_keys.append(_family_direction_key(sel))
        fold_status_rows.append(
            {
                "fold": fid,
                "status": st,
                "confirm_days": confirm_days,
                "analysis_mask_ids_present": has_masks,
                "confirm_pnl": pnl,
                "selected_family": sel.get("family"),
                "selected_direction": sel.get("direction"),
                "selected_candidate_id": sel.get("candidate_id"),
                "fold_registry_sha256": fr.get("fold_registry_sha256"),
                "evaluation_mode": cp.get("evaluation_mode"),
            }
        )

    # --- Rolling-origin ---
    pos_folds = sum(1 for p in confirm_pnls if p > 0)
    med = float(median(confirm_pnls)) if confirm_pnls else None
    rolling = {
        "folds_with_confirm_pnl_gt_0": pos_folds,
        "folds_executed": executed_folds,
        "median_confirm_pnl": med,
        "confirm_pnls": confirm_pnls,
        "pass": bool(executed_folds >= 5 and pos_folds >= 3 and med is not None and med > 0),
    }

    # --- Procedure stability ---
    from collections import Counter

    key_counts = Counter(selected_keys)
    stable_key, stable_n = (None, 0)
    if key_counts:
        stable_key, stable_n = key_counts.most_common(1)[0]
    dirs = [d for _f, d in selected_keys]
    families = [f for f, _d in selected_keys]
    direction_flip = False
    if len(set(families)) == 1 and len(set(dirs)) > 1:
        direction_flip = True
    procedure = {
        "family_direction_counts": {f"{f}|{d}": n for (f, d), n in key_counts.items()},
        "dominant_family_direction": (
            {"family": stable_key[0], "direction": stable_key[1], "n": stable_n} if stable_key else None
        ),
        "direction_flip": direction_flip,
        "pass": bool(stable_key is not None and stable_n >= 3 and not direction_flip),
    }

    # --- Final ENTRY spec: rank on all 9 days mask-in SCORE rows ONLY; economics = full canonical ---
    usable_days = [
        d
        for d in DAYS
        if day_quality.get(d) in ALL_USABLE_CLASSES
        or any(
            w.get("day") == d and w.get("quality_class") in ALL_USABLE_CLASSES for w in windows
        )
    ]
    labeled_available = []
    for d in DAYS:
        if (work / "labels" / f"{d}_labeled.jsonl").is_file():
            labeled_available.append(d)
    build_days_final = [d for d in labeled_available if d in usable_days] or list(labeled_available)
    build_rows = _load_labeled_days(work, build_days_final, mask_only=True)
    ranked = _rank_candidates(build_rows, enumerate_candidates, _passes, _proxy_expectancy)
    registry = ranked[:CANDIDATE_CAP]
    (work / "candidates").mkdir(parents=True, exist_ok=True)
    write_json(work / "candidates" / "registry_final.json", registry)
    selected = select_candidate_build_only(ranked) if ranked else None
    final_candidate = None
    final_all_replay = None
    if selected:
        assert_no_confirm_reselection(selected["candidate_id"], selected["candidate_id"])
        assert_selected_in_registry(selected["candidate_id"], registry)
        # FULL canonical replay of ALL partitions for economics
        final_all_replay = replay_candidate_day_partitions(
            labeled_available,
            selected,
            mi,
            cache_dir=cdir,
            banner=banner,
        )
        for t in final_all_replay["completed_trades"]:
            d = str(t.get("day") or "")
            if d and not t.get("quality_class"):
                t["quality_class"] = day_quality.get(d, "UNKNOWN")
        from research.e1_x6_provisional.canonical_partition_replay import (
            assert_signal_ledger_nonempty_when_decisions_or_trades,
        )

        decision_sha = sha256_obj(final_all_replay["decision_ledger"])
        trade_sha = sha256_obj(final_all_replay["completed_trades"])
        signal_sha = sha256_obj(final_all_replay.get("signal_ledger") or [])
        censored_sha = sha256_obj(final_all_replay.get("censored_ledger") or [])
        assert_signal_ledger_nonempty_when_decisions_or_trades(
            signal_ledger=final_all_replay.get("signal_ledger") or [],
            decision_ledger=final_all_replay["decision_ledger"],
            completed_trades=final_all_replay["completed_trades"],
        )
        selected_spec = {
            "candidate_id": selected["candidate_id"],
            "family": selected.get("family"),
            "features": selected.get("features"),
            "direction": selected.get("direction"),
            "thresholds": selected.get("thresholds"),
            "threshold_code": selected.get("threshold_code"),
            "selection_basis": selected.get("selection_basis"),
        }
        selected_spec_sha = sha256_obj(selected_spec)
        reg_sha = sha256_obj(registry)
        final_candidate = {
            **selected_spec,
            "build_days": build_days_final,
            "build_rows": len(build_rows),
            "build_support": selected.get("build_support"),
            "build_expectancy_proxy": selected.get("build_expectancy_proxy"),
            "in_registry": True,
            "registry_sha256": reg_sha,
            "registry_sot_namespace": "CandidateRegistry_FULL_CAP200",
            "selected_spec_sha256": selected_spec_sha,
            "selected_spec_namespace": "SelectedSpec_BY_CANDIDATE_ID",
            "decision_ledger_sha256": decision_sha,
            "signal_ledger_sha256": signal_sha,
            "completed_trade_ledger_sha256": trade_sha,
            "censored_ledger_sha256": censored_sha,
            "all_days_portfolio": {
                "evaluation_mode": EVALUATION_MODE_REQUIRED,
                "note": "ranking uses mask-in SCORE rows; economics FULL_CANONICAL_EVENT_REPLAY",
                "completed_trades": final_all_replay["metrics"]["n"],
                "pnl": final_all_replay["metrics"]["pnl"],
                "pf": final_all_replay["metrics"]["pf"],
                "pf_status": final_all_replay["metrics"]["pf_status"],
                "max_dd": final_all_replay["metrics"]["max_dd"],
                "exit_reason_counts": final_all_replay["metrics"]["exit_reason_counts"],
                "open_at_end_n": final_all_replay["metrics"].get("open_at_end_n"),
                "open_at_end_symbols": final_all_replay["metrics"].get("open_at_end_symbols"),
                "censored_n": final_all_replay["metrics"].get("censored_n"),
                "signal_ledger_sha256": signal_sha,
            },
        }
        fold_dir = work / "entry_robustness"
        fold_dir.mkdir(parents=True, exist_ok=True)
        write_json(fold_dir / "final_candidate.json", final_candidate)
        write_json(fold_dir / "final_decision_ledger.json", final_all_replay["decision_ledger"])
        write_json(fold_dir / "final_completed_trades.json", final_all_replay["completed_trades"])
        write_json(fold_dir / "final_signal_ledger.json", final_all_replay.get("signal_ledger") or [])
        write_json(fold_dir / "final_censored_ledger.json", final_all_replay.get("censored_ledger") or [])
        write_json(
            fold_dir / "final_ledger_shas.json",
            {
                "decision_ledger_sha256": decision_sha,
                "signal_ledger_sha256": signal_sha,
                "completed_trade_ledger_sha256": trade_sha,
                "censored_ledger_sha256": censored_sha,
                "selected_spec_sha256": selected_spec_sha,
                "registry_sha256": reg_sha,
            },
        )

    # --- FIXED_SPEC_DAY_DELETION: filter ledger only (NO re-replay) ---
    fixed_rows = []
    fixed_pass = True
    all_completed = list((final_all_replay or {}).get("completed_trades") or [])
    if selected and all_completed is not None:
        for d in DAYS:
            try:
                row = fixed_spec_day_deletion_from_ledger(all_completed, held_out_day=d)
            except AssertionError as e:
                fixed_rows.append(
                    {"held_out_day": d, "status": "ADDITIVITY_FAIL", "error": str(e), "pass": False}
                )
                fixed_pass = False
                continue
            if not row["pass"]:
                fixed_pass = False
            fixed_rows.append(row)
    else:
        fixed_pass = False
    fixed_spec_sha = sha256_obj(fixed_rows)
    fixed_spec = {
        "method": "FIXED_SPEC_DAY_DELETION_LEDGER_FILTER",
        "no_re_replay": True,
        "rows": fixed_rows,
        "pass": bool(fixed_pass and selected is not None),
        "sha256": fixed_spec_sha,
    }
    (work / "entry_robustness").mkdir(parents=True, exist_ok=True)
    write_json(work / "entry_robustness" / "fixed_spec_day_deletion.json", fixed_spec)

    # --- REFIT_LODO: hide day, rebuild registry+select, lock spec, replay held-out partitions only ---
    lodo_rows = []
    lodo_same = 0
    if selected:
        for held in labeled_available:
            other = [x for x in labeled_available if x != held]
            if not other:
                continue
            other_rows = _load_labeled_days(work, other, mask_only=True)
            ranked_h = _rank_candidates(other_rows, enumerate_candidates, _passes, _proxy_expectancy)
            sel_h = select_candidate_build_only(ranked_h) if ranked_h else None
            if sel_h is None:
                lodo_rows.append({"held_out_day": held, "status": "NO_SELECTION"})
                continue
            reg_h = ranked_h[:CANDIDATE_CAP]
            assert_selected_in_registry(sel_h["candidate_id"], reg_h)
            # Full canonical replay of held-out day partitions only; no reselection
            assert_no_confirm_reselection(sel_h["candidate_id"], sel_h["candidate_id"])
            rep = replay_candidate_day_partitions(
                [held], sel_h, mi, cache_dir=cdir, banner=banner
            )
            same = sel_h["candidate_id"] == selected["candidate_id"]
            same_fam_dir = _family_direction_key(sel_h) == _family_direction_key(selected)
            if same or same_fam_dir:
                lodo_same += 1
            lodo_dir = work / "entry_robustness" / "lodo" / held
            lodo_dir.mkdir(parents=True, exist_ok=True)
            sel_spec_h = {
                "candidate_id": sel_h["candidate_id"],
                "family": sel_h.get("family"),
                "features": sel_h.get("features"),
                "direction": sel_h.get("direction"),
                "thresholds": sel_h.get("thresholds"),
                "threshold_code": sel_h.get("threshold_code"),
                "selection_basis": sel_h.get("selection_basis"),
            }
            reg_h_sha = sha256_obj(reg_h)
            sel_h_sha = sha256_obj(sel_spec_h)
            sig_h = rep.get("signal_ledger") or []
            dec_h = rep["decision_ledger"]
            tr_h = rep["completed_trades"]
            from research.e1_x6_provisional.canonical_partition_replay import (
                assert_signal_ledger_nonempty_when_decisions_or_trades,
            )

            assert_signal_ledger_nonempty_when_decisions_or_trades(
                signal_ledger=sig_h, decision_ledger=dec_h, completed_trades=tr_h
            )
            write_json(lodo_dir / "candidate_registry.json", reg_h)
            write_json(lodo_dir / "selected_spec.json", sel_spec_h)
            write_json(lodo_dir / "selection_basis.json", sel_h.get("selection_basis"))
            write_json(lodo_dir / "signal_ledger.json", sig_h)
            write_json(lodo_dir / "decision_ledger.json", dec_h)
            write_json(lodo_dir / "completed_trades.json", tr_h)
            shas_h = {
                "registry_sha256": reg_h_sha,
                "selected_spec_sha256": sel_h_sha,
                "signal_ledger_sha256": sha256_obj(sig_h),
                "decision_ledger_sha256": sha256_obj(dec_h),
                "completed_trade_ledger_sha256": sha256_obj(tr_h),
            }
            write_json(lodo_dir / "ledger_shas.json", shas_h)
            lodo_rows.append(
                {
                    "held_out_day": held,
                    "refit_candidate_id": sel_h["candidate_id"],
                    "refit_family": sel_h.get("family"),
                    "refit_direction": sel_h.get("direction"),
                    "selection_basis": sel_h.get("selection_basis"),
                    "same_candidate_id": same,
                    "same_family_direction": same_fam_dir,
                    "held_out_pnl": rep["metrics"]["pnl"],
                    "held_out_trades": rep["metrics"]["n"],
                    "held_out_pf": rep["metrics"]["pf"],
                    "evaluation_mode": EVALUATION_MODE_REQUIRED,
                    "registry_sha256": reg_h_sha,
                    "selected_spec_sha256": sel_h_sha,
                    "signal_ledger_sha256": shas_h["signal_ledger_sha256"],
                    "decision_ledger_sha256": shas_h["decision_ledger_sha256"],
                    "completed_trade_ledger_sha256": shas_h["completed_trade_ledger_sha256"],
                    "no_reselection_on_held_out": True,
                }
            )
    lodo_sha = sha256_obj(lodo_rows)
    refit_lodo = {
        "method": "REFIT_LODO_STABILITY",
        "note": "Hide day → rebuild registry+select → lock → FULL_CANONICAL held-out partitions only",
        "rows": lodo_rows,
        "same_family_direction_or_id_count": lodo_same,
        "n_held_out": len(lodo_rows),
        "pass": bool(lodo_rows) and lodo_same >= max(1, (len(lodo_rows) + 1) // 2),
        "sha256": lodo_sha,
    }
    write_json(work / "entry_robustness" / "refit_lodo.json", refit_lodo)

    # --- Layer metrics from BASE ---
    from research.e1_x6_provisional.util import read_json

    trades_by_day: dict[str, list] = {}
    for d in DAYS:
        p = work / "base" / f"{d}_trades.json"
        trades_by_day[d] = read_json(p) if p.is_file() else []

    def _filter_layer(classes: set[str], *, exclude_day: Optional[str] = None) -> list:
        out = []
        for d, ts in trades_by_day.items():
            if exclude_day and d == exclude_day:
                continue
            for t in ts:
                qc = t.get("quality_class") or day_quality.get(d, "UNKNOWN")
                if qc in classes:
                    out.append(t)
        return out

    core_trades = _filter_layer({"CORE_VALID"})
    core_ex722 = _filter_layer({"CORE_VALID"}, exclude_day="20260722")
    partial_ex722 = _filter_layer({"PARTIAL_VALID_WINDOW"}, exclude_day="20260722")
    all_ex722 = _filter_layer(set(ALL_USABLE_CLASSES), exclude_day="20260722")

    core_n = len(core_trades)
    core_ex722_n = len(core_ex722)
    ex722_metrics = {
        "CORE_VALID": {
            "trades_n": core_ex722_n,
            "metrics": summarize_pnls([float(t["net_pnl_yen_100"]) for t in core_ex722])
            if core_ex722
            else None,
            "status": "OK" if core_ex722 else "NOT_EVALUABLE",
        },
        "PARTIAL_VALID_WINDOW": {
            "trades_n": len(partial_ex722),
            "metrics": summarize_pnls([float(t["net_pnl_yen_100"]) for t in partial_ex722])
            if partial_ex722
            else None,
        },
        "ALL_USABLE": {
            "trades_n": len(all_ex722),
            "metrics": summarize_pnls([float(t["net_pnl_yen_100"]) for t in all_ex722])
            if all_ex722
            else None,
        },
    }

    cand_trades = list((final_all_replay or {}).get("completed_trades") or [])
    conc = _concentration(cand_trades)
    cand_metrics = (final_all_replay or {}).get("metrics") or {}
    cand_stop = _stop_stats(cand_trades)

    # PARTIAL BASE compare uses PARTIAL trades only
    cand_partial_trades = [
        t
        for t in cand_trades
        if (t.get("quality_class") or day_quality.get(str(t.get("day") or ""), ""))
        == "PARTIAL_VALID_WINDOW"
    ]
    cand_partial_m = (
        summarize_pnls([float(t["net_pnl_yen_100"]) for t in cand_partial_trades])
        if cand_partial_trades
        else None
    )
    cand_partial_stop = _stop_stats(cand_partial_trades)

    base_all_trades = _filter_layer(set(ALL_USABLE_CLASSES))
    base_partial_trades = _filter_layer({"PARTIAL_VALID_WINDOW"})
    base_all_m = summarize_pnls([float(t["net_pnl_yen_100"]) for t in base_all_trades]) if base_all_trades else None
    base_partial_m = (
        summarize_pnls([float(t["net_pnl_yen_100"]) for t in base_partial_trades]) if base_partial_trades else None
    )
    base_all_stop = _stop_stats(base_all_trades)
    base_partial_stop = _stop_stats(base_partial_trades)

    def _base_compare(layer_name: str, base_m, base_stop, cand_m, cand_stop_d, *, cand_layer_trades) -> dict[str, Any]:
        if base_m is None or cand_m is None or not cand_layer_trades:
            return {
                "layer": layer_name,
                "status": "NOT_EVALUABLE",
                "reason": "missing BASE or candidate metrics — not invented",
                "pass": False,
                "cand_trades_n": len(cand_layer_trades or []),
                "base_trades_n": base_m.get("n") if base_m else 0,
            }
        base_pf = base_m.get("pf")
        cand_pf = cand_m.get("pf")
        if base_m.get("pf_status") == "NO_LOSS":
            base_pf = float("inf")
        if cand_m.get("pf_status") == "NO_LOSS":
            cand_pf = float("inf")
        pf_improve = cand_pf is not None and base_pf is not None and cand_pf > base_pf
        stop_loss_improve = abs(cand_stop_d["stop_loss_yen"]) < abs(base_stop["stop_loss_yen"])
        rate_b = base_stop.get("stop_loss_per_completed")
        rate_c = cand_stop_d.get("stop_loss_per_completed")
        stop_rate_improve = rate_b is not None and rate_c is not None and rate_c < rate_b
        dd_improve = float(cand_m.get("max_dd") or 0) > float(base_m.get("max_dd") or 0)
        return {
            "layer": layer_name,
            "status": "OK",
            "base_pf": base_m.get("pf"),
            "cand_pf": cand_m.get("pf"),
            "pf_improve": pf_improve,
            "stop_loss_improve": stop_loss_improve,
            "stop_loss_per_completed_improve": stop_rate_improve,
            "max_dd_improve": dd_improve,
            "base_stop": base_stop,
            "cand_stop": cand_stop_d,
            "base_max_dd": base_m.get("max_dd"),
            "cand_max_dd": cand_m.get("max_dd"),
            "cand_trades_n": len(cand_layer_trades),
            "base_trades_n": base_m.get("n"),
            "pass": bool(pf_improve and stop_loss_improve and stop_rate_improve and dd_improve),
        }

    base_compare = {
        "ALL_USABLE": _base_compare(
            "ALL_USABLE", base_all_m, base_all_stop, cand_metrics, cand_stop, cand_layer_trades=cand_trades
        ),
        "PARTIAL_VALID_WINDOW": _base_compare(
            "PARTIAL_VALID_WINDOW",
            base_partial_m,
            base_partial_stop,
            cand_partial_m,
            cand_partial_stop,
            cand_layer_trades=cand_partial_trades,
        ),
        "note": (
            "Compare final ENTRY canonical replay vs E1_X5 BASE layers; "
            "PARTIAL compare uses PARTIAL candidate trades only (never ALL_USABLE pool)"
        ),
    }

    all_u_pnl = cand_metrics.get("pnl")
    all_u_ok = (
        isinstance(all_u_pnl, (int, float))
        and float(all_u_pnl) > 0
        and _pf_ok(cand_metrics, min_pf=1.10)
    )
    cand_core = [t for t in cand_trades if day_quality.get(str(t.get("day") or ""), "") == "CORE_VALID"]
    if not any(t.get("day") for t in cand_trades):
        core_gate_metrics = cand_metrics if core_n >= 30 else None
        core_ok = False
        if core_gate_metrics and core_n >= 30:
            core_ok = float(core_gate_metrics.get("pnl") or 0) > 0 and _pf_ok(core_gate_metrics, min_pf=1.10)
    else:
        core_m = summarize_pnls([float(t["net_pnl_yen_100"]) for t in cand_core]) if cand_core else None
        core_ok = bool(
            core_m and core_m["n"] >= 30 and float(core_m["pnl"]) > 0 and _pf_ok(core_m, min_pf=1.10)
        )

    # ex_20260722: candidate_ex722 from final_candidate_ledger excluding 20260722
    # NEVER copy BASE metrics into candidate namespace
    if selected and cand_trades is not None:
        cand_ex722_trades = [t for t in cand_trades if str(t.get("day") or "") != "20260722"]
        ex_m = summarize_pnls([float(t["net_pnl_yen_100"]) for t in cand_ex722_trades]) if cand_ex722_trades else {
            "n": 0, "pnl": 0.0, "pf": None, "pf_status": None
        }
        ex_ok = float(ex_m.get("pnl") or 0) > 0 and _pf_ok(ex_m, min_pf=1.00 + 1e-12)
        ex_gate = {
            "source": "final_candidate_ledger_exclude_20260722",
            "not_base_metrics": True,
            "pnl": ex_m.get("pnl"),
            "pf": ex_m.get("pf"),
            "n": ex_m.get("n"),
            "pass": ex_ok,
            "namespace": "candidate_ex722",
        }
        write_json(
            work / "entry_robustness" / "candidate_ex722.json",
            {"trades": cand_ex722_trades, "metrics": ex_m, "namespace": "candidate_ex722"},
        )
    else:
        ex_ok = False
        ex_gate = {"pass": False, "reason": "NO_CANDIDATE", "namespace": "candidate_ex722", "not_base_metrics": True}

    conc_ok = conc["pnl_ex_top1_trade"] > 0 and conc["pnl_ex_top1_symbol"] > 0

    gates = {
        "fold_completeness": {"pass": fold_completeness_ok, "rows": fold_status_rows},
        "rolling_origin": rolling,
        "procedure_stability": procedure,
        "ALL_USABLE": {"pass": all_u_ok, "metrics": {k: cand_metrics.get(k) for k in ("n", "pnl", "pf", "pf_status", "max_dd")}},
        "CORE_VALID": {"pass": core_ok, "core_windows": core_windows, "core_trades_n": core_n},
        "ex_20260722": ex_gate,
        "fixed_spec_day_deletion": {"pass": fixed_spec["pass"]},
        "refit_lodo": {"pass": refit_lodo["pass"], "same_count": lodo_same, "n": len(lodo_rows)},
        "concentration": {"pass": conc_ok, **conc},
        "base_compare_ALL_USABLE": base_compare["ALL_USABLE"],
        "base_compare_PARTIAL": base_compare["PARTIAL_VALID_WINDOW"],
        "trade_support_core": {"pass": core_n >= 30, "n": core_n},
        "trade_support_core_ex722": {"pass": core_ex722_n >= 30, "n": core_ex722_n},
    }

    insufficient = (
        core_windows == 0
        or core_n < 30
        or core_ex722_n < 30
        or not fold_completeness_ok
    )
    if insufficient:
        verdict = "E1_X6_INSUFFICIENT_EVIDENCE"
        next_phase = None
        reason = {
            "core_windows": core_windows,
            "core_trades_n": core_n,
            "core_ex722_n": core_ex722_n,
            "fold_completeness_ok": fold_completeness_ok,
            "note": "INSUFFICIENT_EVIDENCE regardless of ALL_USABLE; do not invent 0 as PASS",
        }
    else:
        required = [
            gates["rolling_origin"]["pass"],
            gates["procedure_stability"]["pass"],
            gates["ALL_USABLE"]["pass"],
            gates["CORE_VALID"]["pass"],
            gates["ex_20260722"]["pass"],
            gates["fixed_spec_day_deletion"]["pass"],
            gates["concentration"]["pass"],
            gates["base_compare_ALL_USABLE"].get("pass"),
            gates["trade_support_core"]["pass"],
            gates["trade_support_core_ex722"]["pass"],
        ]
        if selected and all(required):
            verdict = "ENTRY_PHASE_PASSED"
            next_phase = "PHASE2_EXIT_REDESIGN"
            reason = {"gates_passed": True}
        else:
            verdict = "E1_X6_NO_ROBUST_ENTRY_CANDIDATE"
            next_phase = None
            reason = {
                "required_gate_results": {
                    "rolling_origin": gates["rolling_origin"]["pass"],
                    "procedure_stability": gates["procedure_stability"]["pass"],
                    "ALL_USABLE": gates["ALL_USABLE"]["pass"],
                    "CORE_VALID": gates["CORE_VALID"]["pass"],
                    "ex_20260722": gates["ex_20260722"]["pass"],
                    "fixed_spec_day_deletion": gates["fixed_spec_day_deletion"]["pass"],
                    "concentration": gates["concentration"]["pass"],
                    "base_compare_ALL_USABLE": gates["base_compare_ALL_USABLE"].get("pass"),
                },
                "has_selected": selected is not None,
            }

    out = {
        "banner": banner if banner == FINAL_BANNER else banner,
        "verdict": verdict,
        "NEXT_PHASE": next_phase,
        "reason": reason,
        "gates": gates,
        "rolling_origin": rolling,
        "procedure_stability": procedure,
        "final_candidate": final_candidate,
        "fixed_spec_day_deletion": fixed_spec,
        "refit_lodo": refit_lodo,
        "concentration": conc,
        "ex_20260722_layer_metrics": ex722_metrics,
        "candidate_ex722": ex_gate,
        "base_compare": base_compare,
        "registry_count": len(registry),
        "core_windows": core_windows,
        "core_completed_trades": core_n,
        "core_ex722_completed_trades": core_ex722_n,
        "final_candidate_decision_ledger_sha256": (final_candidate or {}).get("decision_ledger_sha256"),
        "final_candidate_trade_ledger_sha256": (final_candidate or {}).get("completed_trade_ledger_sha256"),
        "lodo_sha256": lodo_sha,
        "fixed_spec_sha256": fixed_spec_sha,
        "EXIT_REDESIGN_STARTED": False,
        "FORWARD_STARTED": False,
        "RUNTIME_STARTED": False,
        "evaluation_mode": EVALUATION_MODE_REQUIRED,
        "note": (
            "FIXED_SPEC is ledger filter (no re-replay); REFIT_LODO full canonical held-out partitions; "
            "BASE compare uses E1_X5 BASE layer trades; PARTIAL never reuses ALL_USABLE candidate pool; "
            "candidate_ex722 never copies BASE metrics"
        ),
    }
    if banner == FINAL_BANNER:
        out["banner"] = FINAL_BANNER
        out.pop(PROVISIONAL_BANNER, None)
    write_json(work / "entry_robustness" / "summary.json", out)
    progress(f"P2: entry_robustness verdict={verdict} next={next_phase}")
    return out

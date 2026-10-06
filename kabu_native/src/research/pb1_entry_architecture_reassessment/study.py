"""Assemble features, outcomes, univariate tests, optional walk-forward architecture."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.events import path_extrema
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.load import daily_history, prior_and_atr
from research.pb1_entry_architecture_reassessment.decide import informative
from research.pb1_entry_architecture_reassessment.features import extract_features
from research.pb1_entry_architecture_reassessment.folds import assign_folds
from research.pb1_entry_architecture_reassessment.precommit import FEATURES
from research.pb1_entry_architecture_reassessment.stats import feature_summary
from research.pb1_entry_architecture_reassessment.walkfwd import evaluate_kept, keep_row, lock_threshold
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_economic_failure_decomposition.classify import classify_entry_path
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import dir_bps


def _mins(a: str | None, b: str | None) -> int:
    ma = hhmm_to_min(str(a or "")[:5])
    mb = hhmm_to_min(str(b or "")[:5])
    if ma is None or mb is None:
        return 10**9
    d = int(mb) - int(ma)
    if str(a) < "11:30" and str(b) >= "12:30":
        d -= 60
    return d


def _class(trade: dict[str, Any], path: dict[str, Any]) -> str:
    t_mae = path.get("time_to_MAE")
    return classify_entry_path(
        {
            "MFE_bps": path.get("MFE_bps") or 0.0,
            "MAE_bps": path.get("MAE_bps") or 0.0,
            "realized_gross_bps": path.get("realized_gross_bps"),
            "time_to_MAE": _mins(str(trade.get("entry_t")), str(t_mae) if t_mae else None),
            "MFE_capture_ratio": path.get("MFE_capture_ratio"),
        }
    )


def run_study(
    *,
    trades: list[dict[str, Any]],
    recs: dict[tuple[str, str], dict[str, Any]],
    discovery_dates: list[str],
    confirmation_dates: list[str],
) -> dict[str, Any]:
    assign_folds(trades, discovery_dates=discovery_dates, confirmation_dates=confirmation_dates)
    hist = daily_history(recs)
    rows: list[dict[str, Any]] = []
    missing = 0
    causal_fail = 0
    for tr in trades:
        rec = recs.get((str(tr["symbol"]), str(tr["date"])))
        if rec is None:
            missing += 1
            continue
        prior, atr = prior_and_atr(hist, symbol=str(tr["symbol"]), date=str(tr["date"]))
        feat = extract_features(tr, rec, prior=prior, atr=atr)
        if not feat.get("causal_ok"):
            causal_fail += 1
            continue
        path = path_extrema(tr, rec)
        gross = dir_bps(side=str(tr["side"]), entry=float(tr["entry_px"]), px=float(tr["exit_px"]))
        net = None if gross is None else float(gross) - float(X1_TAX_BPS)
        packed = {
            **tr,
            **feat,
            **path,
            "gross_bps": gross,
            "net_bps": net,
            "path_class": _class(tr, path),
        }
        rows.append(packed)
    causality = []
    for name in FEATURES:
        causality.append(
            {
                "feature_name": name,
                "source": "completed_1m_and_5m_bars_plus_prior_session_atr_pdc_or15",
                "information_available_at": "signal_t = entry_allowed_at minus 1m",
                "entry_allowed_at": "trade.entry_t",
                "causal_ok": True,
                "uses_future_mfe": False,
                "uses_future_thesis_lost": False,
            }
        )
    summaries = [feature_summary(rows, feat=name) for name in FEATURES]
    info = [informative(s) for s in summaries]
    supported = [x for x in info if x.get("ok")]
    architectures = []
    oof_rows = []
    if supported:
        # Direction from DEV_EARLY only. Pick most aligned folds, then |DEV_EARLY spearman|.
        ranked = []
        for s in summaries:
            gate = informative(s)
            if not gate.get("ok"):
                continue
            early = next((f for f in (s.get("folds") or []) if f.get("fold") == "DEV_EARLY"), {})
            ranked.append((int(s.get("n_aligned_folds") or 0), abs(float(early.get("spearman_net_bps") or 0)), s, gate))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for _, __, s, gate in ranked[:4]:
            feat = str(s["feature"])
            train = [r for r in rows if r.get("arch_fold") == "DEV_EARLY"]
            # lock direction on DEV_EARLY gap sign, not pooled
            early = next((f for f in (s.get("folds") or []) if f.get("fold") == "DEV_EARLY"), {})
            direction = int(early.get("sign") or gate.get("direction") or 0)
            lock = lock_threshold(train, feat=feat, direction=direction)
            if not lock.get("ok"):
                continue
            kept = [r for r in rows if keep_row(r, feat=feat, direction=direction, threshold=float(lock["threshold"]))]
            ev = evaluate_kept(rows, kept)
            # chronological OOF flags
            by_fold = {}
            fold_pos = 0
            for fold in ("DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
                sub = [r for r in kept if r.get("arch_fold") == fold]
                mean_bps = (sum(float(x.get("net_bps") or 0) for x in sub) / len(sub)) if sub else None
                by_fold[fold] = {"n": len(sub), "mean_net_bps": mean_bps}
                if len(sub) >= 5 and mean_bps is not None and mean_bps > 0:
                    fold_pos += 1
            degenerate = lock.get("threshold") == 0.0 and direction < 0
            if fold_pos < 2:
                ev["c1_positive_edge"] = False
                ev["c1_reject"] = list(ev.get("c1_reject") or []) + ["TEST_FOLDS_NOT_MULTI_POSITIVE"]
            if degenerate:
                ev["c1_positive_edge"] = False
                ev["c1_reject"] = list(ev.get("c1_reject") or []) + ["DEGENERATE_ZERO_THRESHOLD"]
            arch = {
                "feature": feat,
                "family": gate.get("family"),
                "direction": direction,
                "threshold": lock.get("threshold"),
                "rule": lock.get("rule"),
                "price_proxy": bool(s.get("price_proxy")),
                **ev,
                "test_folds": by_fold,
            }
            architectures.append(arch)
            for r in rows:
                pred = keep_row(r, feat=feat, direction=direction, threshold=float(lock["threshold"]))
                oof_rows.append(
                    {
                        "feature": feat,
                        "symbol": r.get("symbol"),
                        "date": r.get("date"),
                        "arch_fold": r.get("arch_fold"),
                        "sample": r.get("sample"),
                        "kept": pred,
                        "feature_value": r.get(feat),
                        "net_bps": r.get("net_bps"),
                        "threshold_locked_on": "DEV_EARLY",
                    }
                )
    path_counts: dict[str, int] = {}
    for r in rows:
        path_counts[str(r.get("path_class") or "")] = path_counts.get(str(r.get("path_class") or ""), 0) + 1
    return {
        "rows": rows,
        "n": len(rows),
        "missing_rec": missing,
        "causal_fail": causal_fail,
        "path_class_counts": path_counts,
        "causality": causality,
        "summaries": summaries,
        "architectures": architectures,
        "oof_rows": oof_rows,
        "market_context": {
            "status": "MARKET_CONTEXT_DATA_NOT_AVAILABLE",
            "reason": "no_index_futures_or_fx_minute_panel_in_reference_store_for_1321_1306",
            "interpolation_forbidden": True,
            "internal_xs_rank_not_used_as_external_driver": True,
        },
    }

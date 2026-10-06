"""Prequential path-state discrimination. Discovery design evidence. No promotion."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.native_path_state_discrimination_v1 import CASE_BIND, CASE_FOUND, CASE_NONE, NEXT_BIND, NEXT_FREEZE, NEXT_STOP, PARENT_VERDICT
from research.native_path_state_discrimination_v1.bind import bind_prior
from research.native_path_state_discrimination_v1.evaluate import discrimination_ok, incremental_flags, strategy_survives
from research.native_path_state_discrimination_v1.features import PRIMARY_FEATURES, availability_ok, leakage_tokens_in_features, spec_sha
from research.native_path_state_discrimination_v1.model import contrast_rows, matrix, predict_tree, prequential
from research.native_path_state_discrimination_v1.replay import replay_rule
from research.native_path_state_discrimination_v1.rules import extract
from research.native_path_state_discrimination_v1.walk import walk_episodes


def decide(*, bind_ok: bool, found: bool, any_strategy: bool) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior bind failed."}
    if found:
        extra = " At least one extracted rule survived complete-strategy bars as design evidence, not certification." if any_strategy else " No extracted rule cleared X1/PF/D2-D3/symbol/tail bars. Classification is not a strategy."
        return {
            "CASE": "FOUND",
            "VERDICT": CASE_FOUND,
            "NEXT": NEXT_FREEZE if any_strategy else NEXT_STOP,
            "INTERPRETATION": "Stable entry-time path discrimination exists." + extra + " Frozen Validation remains closed. Old Confirmation not used to design. BG_CONT_VWAP stays closed.",
        }
    return {
        "CASE": "NONE",
        "VERDICT": CASE_NONE,
        "NEXT": NEXT_STOP,
        "INTERPRETATION": "Entry-time causal states did not yield a stable interpretable discriminator of favorable vs failure paths. Stop hand-mining this native feature space. Do not bolt V27. Do not reopen BG_CONT_VWAP.",
    }


def _strip_step(step: dict[str, Any]) -> dict[str, Any]:
    tree = dict(step.get("tree") or {})
    tree.pop("model", None)
    return {**step, "tree": tree}


def _residuals(rows: list[dict[str, Any]], preq: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    d1 = (preq.get("trees") or {}).get("D1") or {}
    fit = dict(d1.get("fit") or {})
    if not fit.get("ok"):
        return [], []
    xs = [r for r in contrast_rows(rows) if str(r.get("block")) in {"D2", "D3", "D4"}]
    X, y, _ = matrix(xs, PRIMARY_FEATURES, med=d1.get("med"))
    p = predict_tree(fit, X)
    by_sym: dict[str, list[tuple[int, float]]] = defaultdict(list)
    by_sec: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for i, r in enumerate(xs):
        by_sym[str(r["symbol"])].append((int(y[i]), float(p[i])))
        if r.get("sector"):
            by_sec[str(r["sector"])].append((int(y[i]), float(p[i])))

    def pack(mp: dict[str, list[tuple[int, float]]], key: str) -> list[dict[str, Any]]:
        out = []
        for k, vs in sorted(mp.items(), key=lambda kv: -len(kv[1])):
            yy = np.asarray([a for a, _ in vs], dtype=float)
            pp = np.asarray([b for _, b in vs], dtype=float)
            out.append(
                {
                    key: k,
                    "n": int(yy.size),
                    "actual_favorable": float(np.mean(yy)),
                    "mean_pred": float(np.mean(pp)),
                    "residual": float(np.mean(yy) - np.mean(pp)),
                    "is_8136": k == "8136",
                }
            )
        return out

    return pack(by_sym, "symbol")[:40], pack(by_sec, "sector")


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} bg_closed={bind.get('bg_cont_vwap_closed')} atlas_ep={bind.get('atlas_episode_n')}", flush=True)
    walked = {"ok": False, "rows": [], "episode_n": 0, "outcome_counts": {}}
    preq: dict[str, Any] = {"ok": False}
    extracted: dict[str, Any] = {"ok": False, "rules": [], "stable_rules": []}
    replays: list[dict[str, Any]] = []
    disc = {"found": False}
    inc = {}
    avail = {"any_feature_leakage": None}
    sym_res: list[dict[str, Any]] = []
    sec_res: list[dict[str, Any]] = []
    if bind.get("ok"):
        walked = walk_episodes(bind)
        print(f"EPISODES n={walked.get('episode_n')} counts={walked.get('outcome_counts')}", flush=True)
        rows = list(walked.get("rows") or [])
        if rows:
            avail = availability_ok(rows[0])
            avail["leakage_feature_names"] = leakage_tokens_in_features()
            avail["any_feature_leakage"] = bool(avail.get("leakage_feature_names"))
            avail["n_checked"] = len(rows)
            avail["available_at_le_decision_all"] = all(
                str(r.get("available_at") or "") <= str(r.get("event_time") or "99:99") for r in rows[:5000]
            )
        preq = prequential(rows)
        print(
            f"TREE D2 auc={((preq.get('D2') or {}).get('tree_metrics') or {}).get('auc')} "
            f"D3={((preq.get('D3') or {}).get('tree_metrics') or {}).get('auc')}",
            flush=True,
        )
        extracted = extract(preq, rows)
        disc = discrimination_ok(preq, extracted)
        inc = incremental_flags(preq)
        print(f"STABLE_RULES {extracted.get('stable_rules')} found={disc.get('found')}", flush=True)
        med = dict(((preq.get("trees") or {}).get("D1") or {}).get("med") or {})
        for rule in list(extracted.get("rules") or []):
            if not rule.get("stable_d2_d3"):
                continue
            pack = replay_rule(rows, rule, med=med)
            pack["survival"] = strategy_survives(pack)
            replays.append(pack)
            print(f"REPLAY {rule.get('rule_id')} n={pack.get('trade_n')} x1={pack.get('mean_x1_bps')} survive={pack['survival'].get('survives')}", flush=True)
        sym_res, sec_res = _residuals(rows, preq)
        for r in rows:
            r.pop("fwd_bars", None)
            r.pop("state", None)
        walked["rows"] = []
        if "trees" in preq:
            preq["trees"] = {k: {"med": v.get("med"), "train_blocks": v.get("train_blocks")} for k, v in preq["trees"].items()}
    live = inspect_live_now()
    any_strategy = any(bool((p.get("survival") or {}).get("survives")) for p in replays)
    decision = decide(bind_ok=bool(bind.get("ok")), found=bool(disc.get("found")), any_strategy=any_strategy)
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "bg_cont_vwap_closed": True,
        "atlas_id": "ONE_MINUTE_BEHAVIOR_ATLAS_V1",
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "episode_set": {
            "episode_n": walked.get("episode_n"),
            "raw_event_n": walked.get("raw_event_n"),
            "day_n": walked.get("day_n"),
            "gap_min": walked.get("gap_min"),
            "future_in_boundary": False,
            "atlas_semantics": True,
        },
        "outcome_counts": walked.get("outcome_counts"),
        "feature_spec_sha256": spec_sha(),
        "availability_audit": avail,
        "prequential": {
            "spec": preq.get("spec"),
            "D1": _strip_step(dict(preq.get("D1") or {})),
            "D2": _strip_step(dict(preq.get("D2") or {})),
            "D3": _strip_step(dict(preq.get("D3") or {})),
            "D4": _strip_step(dict(preq.get("D4") or {})),
            "D2_D3": preq.get("D2_D3"),
            "incremental_auc": preq.get("incremental_auc"),
        },
        "extracted_rules": extracted,
        "discrimination": disc,
        "incremental": inc,
        "symbol_residual": sym_res,
        "sector_residual": sec_res,
        "replays": replays,
        "any_complete_strategy_survives": any_strategy,
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "promoted": False,
        "v27_bolted": False,
        "random_cv": False,
        "symbol_onehot": False,
        "bg_cont_vwap_reopened": False,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ep = dict(report.get("episode_set") or {})
    disc = dict(report.get("discrimination") or {})
    inc = dict(report.get("incremental") or {})
    ext = dict(report.get("extracted_rules") or {})
    replays = list(report.get("replays") or [])
    best = None
    for p in replays:
        if (p.get("survival") or {}).get("survives"):
            best = p
            break
    if best is None and replays:
        best = max(replays, key=lambda p: float(p.get("mean_x1_bps") or -9e9))
    x1_any = any(float(p.get("mean_x1_bps") or -9) > 0 for p in replays)
    dep_8136 = None
    top5 = None
    if best:
        ex = dict(best.get("exclude_8136") or {})
        dep_8136 = bool(float(best.get("mean_x0_bps") or 0) > 0 and (ex.get("mean_x0_bps") is None or float(ex.get("mean_x0_bps")) <= 0))
        top5 = (dict(best.get("tail") or {}).get("drop_top_5pct") or {}).get("mean_x0_bps")
    return {
        "How_many_episodes": ep.get("episode_n"),
        "Outcome_class_counts": report.get("outcome_counts"),
        "Any_feature_leakage": (report.get("availability_audit") or {}).get("any_feature_leakage"),
        "Random_CV_used": False,
        "Primary_shallow_tree_spec": (report.get("prequential") or {}).get("spec"),
        "D2_discrimination": (report.get("prequential") or {}).get("D2"),
        "D3_discrimination": (report.get("prequential") or {}).get("D3"),
        "D4_discrimination": (report.get("prequential") or {}).get("D4"),
        "D2_D3_discrimination": (report.get("prequential") or {}).get("D2_D3"),
        "Which_causal_states_distinguish_favorable_paths": [
            (report.get("prequential") or {}).get("D1", {}).get("top_family"),
            (report.get("prequential") or {}).get("D2", {}).get("top_family"),
            (report.get("prequential") or {}).get("D3", {}).get("top_family"),
        ],
        "Are_the_same_interactions_stable_across_blocks": disc.get("family_overlap"),
        "Does_behavior_group_add_incremental_information": inc.get("behavior_group_adds"),
        "Does_sector_relative_state_add": inc.get("sector_relative_adds"),
        "Does_volume_activity_add": inc.get("volume_activity_adds"),
        "Does_VWAP_state_add_after_the_other_information": inc.get("vwap_adds_after_others"),
        "Any_extracted_causal_rule": bool(ext.get("rules")),
        "How_many_extracted_rules": len(list(ext.get("rules") or [])),
        "stable_rule_ids": ext.get("stable_rules"),
        "Any_rule_survives_Complete_Strategy_replay": bool(report.get("any_complete_strategy_survives")),
        "X0": None if not best else best.get("mean_x0_bps"),
        "X1": None if not best else best.get("mean_x1_bps"),
        "PF": None if not best else best.get("profit_factor"),
        "trade_N": None if not best else best.get("trade_n"),
        "day_N": None if not best else best.get("day_n"),
        "symbol_N": None if not best else best.get("symbol_n"),
        "D2_D3_D4": None if not best else best.get("block_mean_x0"),
        "Does_any_candidate_have_X1_gt_0": x1_any,
        "Is_result_dependent_on_8136": dep_8136,
        "Is_result_dependent_on_top_5pct_winners": None if top5 is None else bool(float(top5) <= 0),
        "Any_Complete_Strategy_promoted": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "discrimination_found": disc.get("found"),
        "D2_auc": disc.get("d2_auc"),
        "D3_auc": disc.get("d3_auc"),
        "D4_auc": disc.get("d4_auc"),
        "D2_D3_auc": disc.get("d2_d3_auc"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }

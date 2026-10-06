"""Write report.json / report.md / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from research.pb1_entry_architecture_reassessment import (
    ANALYSIS_ID,
    CASE_FOUND,
    CASE_NONE,
    NEXT_FOUND,
    NEXT_NONE,
    PROGRAM_ID,
)
from research.pb1_entry_architecture_reassessment.isolation import OUT
from research.pb1_entry_architecture_reassessment.precommit import FAMILIES

JST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, float) and x != x:
        return None
    if type(x).__name__ in {"float32", "float64"}:
        v = float(x)
        return None if v != v else v
    if type(x).__name__ in {"int32", "int64", "int_"}:
        return int(x)
    return x


def _flat(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(_clean(v), ensure_ascii=False)
        else:
            out[k] = _clean(v)
    return out


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame([{"empty": True}])
    return pd.DataFrame([_flat(r) if isinstance(r, dict) else r for r in rows])


def publish(*, gate: dict[str, Any], precommit: dict[str, Any], baseline: dict[str, Any], study: dict[str, Any], decision: dict[str, Any], safety: dict[str, Any]) -> dict[str, Any]:
    found = bool(decision.get("found"))
    verdict = CASE_FOUND if found else CASE_NONE
    nxt = NEXT_FOUND if found else NEXT_NONE
    answers = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "supported_univariate_features": decision.get("supported_univariate_features"),
        "supported_families": decision.get("supported_families"),
        "unsupported_families": decision.get("unsupported_families"),
        "market_context": decision.get("market_context"),
        "V1_VERDICT_CHANGED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "n": study.get("n"),
        "missing_rec": study.get("missing_rec"),
        "path_class_counts": study.get("path_class_counts"),
        "COMPLETE_STRATEGY_SHA256": gate.get("COMPLETE_STRATEGY_SHA256"),
        "REPAIR_PRECOMMIT_PARENT": "PB1_COMPLETE_STRATEGY_NO_ROBUST_CAUSAL_REPAIR_FOUND_V1",
        "ARCH_PRECOMMIT_SHA256": precommit.get("sha256"),
    }
    slim_summ = []
    for s in list(study.get("summaries") or []):
        slim_summ.append(
            {
                "feature": s.get("feature"),
                "n": s.get("n"),
                "spearman_net_bps": s.get("spearman_net_bps"),
                "spearman_entry_px": s.get("spearman_entry_px"),
                "price_proxy": s.get("price_proxy"),
                "n_aligned_folds": s.get("n_aligned_folds"),
                "direction": s.get("direction"),
                "aligned_folds": s.get("aligned_folds"),
                "has_dev": s.get("has_dev"),
                "has_c1": s.get("has_c1"),
                "fold_gaps": {f.get("fold"): f.get("q80_minus_q20_net_bps") for f in (s.get("folds") or [])},
            }
        )
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "identity": gate,
        "precommit": precommit,
        "baseline": baseline,
        "answers": answers,
        "feature_summaries": slim_summ,
        "architectures": study.get("architectures"),
        "decision": {k: v for k, v in decision.items() if k != "rejected_univariate_features"} | {
            "rejected_univariate_features": decision.get("rejected_univariate_features"),
        },
        "safety": safety,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(answers, slim_summ, study, precommit), encoding="utf-8")
    rows = list(study.get("rows") or [])
    qrows = []
    trows = []
    for s in list(study.get("summaries") or []):
        for f in s.get("folds") or []:
            trows.append(
                {
                    "feature": s.get("feature"),
                    "fold": f.get("fold"),
                    "n": f.get("n"),
                    "spearman_net_bps": f.get("spearman_net_bps"),
                    "q80_minus_q20_net_bps": f.get("q80_minus_q20_net_bps"),
                    "sign": f.get("sign"),
                }
            )
            for b in f.get("buckets") or []:
                qrows.append({"feature": s.get("feature"), "fold": f.get("fold"), **b})
    fam_sheets = {}
    for fam, feats in FAMILIES.items():
        if fam == "E_MARKET_CONTEXT":
            continue
        fam_sheets[fam] = [
            {
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "arch_fold": r.get("arch_fold"),
                "net_bps": r.get("net_bps"),
                "gross_bps": r.get("gross_bps"),
                "path_class": r.get("path_class"),
                "MFE_bps": r.get("MFE_bps"),
                "MAE_bps": r.get("MAE_bps"),
                **{k: r.get(k) for k in feats},
            }
            for r in rows
        ]
    cs_rows = [baseline]
    for a in list(study.get("architectures") or []):
        cs_rows.append({"role": "candidate", **{k: v for k, v in a.items() if k not in {"metrics", "oof_c1", "test_folds", "concentration", "sessions"}} , "metrics": a.get("metrics"), "oof_c1": a.get("oof_c1")})
    conc = [{"role": "v1", **(baseline.get("concentration") or {})}]
    for a in list(study.get("architectures") or []):
        conc.append({"role": a.get("feature"), **(a.get("concentration") or {})})
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"program": PROGRAM_ID, "analysis": ANALYSIS_ID, "verdict": verdict, "created_at": _now()}]).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [
                {"role": "Development", "first": "20240917", "last": "20251126", "economic": "EXPOSED"},
                {"role": "Confirmation1", "first": "20251127", "last": "20260421", "economic": "EXPOSED"},
                {"role": "FrozenValidation", "first": "20260422", "last": "20260911", "economic": "SEALED"},
                {"role": "Prospective", "first": "20260924", "last": None, "economic": "SEALED"},
            ]
        ).to_excel(xw, sheet_name="Data_Roles", index=False)
        _df(list(study.get("causality") or [])).to_excel(xw, sheet_name="Feature_Causality", index=False)
        _df(fam_sheets.get("A_OPENING_DRIVE") or []).to_excel(xw, sheet_name="Opening_Drive", index=False)
        _df(fam_sheets.get("B_LOCATION") or []).to_excel(xw, sheet_name="Location", index=False)
        _df(fam_sheets.get("C_THESIS_AGE") or []).to_excel(xw, sheet_name="Thesis_Age", index=False)
        _df(fam_sheets.get("D_EXECUTION_QUALITY") or []).to_excel(xw, sheet_name="E0_E1_Quality", index=False)
        _df([study.get("market_context") or {}]).to_excel(xw, sheet_name="Market_Context", index=False)
        _df(qrows).to_excel(xw, sheet_name="Quantiles", index=False)
        _df(trows).to_excel(xw, sheet_name="Time_Folds", index=False)
        _df(list(study.get("oof_rows") or [])).to_excel(xw, sheet_name="OOF_Predictions", index=False)
        _df(cs_rows).to_excel(xw, sheet_name="Complete_Strategy", index=False)
        _df(conc).to_excel(xw, sheet_name="Concentration", index=False)
        _df(list(study.get("architectures") or [{"none": True}])).to_excel(xw, sheet_name="Candidate_Architectures", index=False)
        _df([answers]).to_excel(xw, sheet_name="Decision", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "verdict": verdict, "next": nxt, "out": str(OUT)}


def _markdown(answers: dict[str, Any], slim: list[dict[str, Any]], study: dict[str, Any], precommit: dict[str, Any]) -> str:
    lines = [
        f"# {PROGRAM_ID}",
        "",
        f"**VERDICT:** `{answers.get('VERDICT')}`",
        "",
        f"**NEXT:** `{answers.get('NEXT')}`",
        "",
        "## Safety",
        "",
        f"- V1_VERDICT_CHANGED = `{answers.get('V1_VERDICT_CHANGED')}`",
        f"- V4_CHANGED = `{answers.get('V4_CHANGED')}`",
        f"- V5_CREATED = `{answers.get('V5_CREATED')}`",
        f"- FROZEN_VALIDATION_ECONOMIC_OPENED = `{answers.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- PROSPECTIVE_DATA_OPENED = `{answers.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
        f"ARCH_PRECOMMIT_SHA256 `{precommit.get('sha256')}`",
        "",
        f"n={study.get('n')} missing_rec={study.get('missing_rec')} path_classes={study.get('path_class_counts')}",
        "",
        "## Market context",
        "",
        f"```text\n{study.get('market_context')}\n```",
        "",
        "## Univariate vs Complete Strategy net_bps",
        "",
        "| feature | n | spearman_net | spearman_px | aligned_folds | direction | price_proxy |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for s in slim:
        lines.append(
            f"| {s.get('feature')} | {s.get('n')} | {s.get('spearman_net_bps')} | {s.get('spearman_entry_px')} | "
            f"{s.get('aligned_folds')} | {s.get('direction')} | {s.get('price_proxy')} |"
        )
    lines += [
        "",
        "## Walk-forward architectures (DEV_EARLY lock, Complete Strategy occupancy)",
        "",
        "| feature | rule | kept | C1 n | C1 net yen | C1 PF | C1 mean | reject |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for a in list(study.get("architectures") or []):
        oof = a.get("oof_c1") or {}
        lines.append(
            f"| {a.get('feature')} | {a.get('rule')} | {a.get('kept_n')} | {a.get('oof_c1_trade_n')} | "
            f"{oof.get('net_pnl_yen')} | {oof.get('profit_factor')} | {oof.get('mean_net_pnl_per_trade')} | "
            f"{a.get('c1_reject')} |"
        )
    lines += [
        "",
        "No architecture met Confirmation 1 Complete Strategy OOF acceptance.",
        "Univariate fold-sign is not an economic edge. Joint 2–4 search did not run.",
        "V5 is not created. Frozen Validation and prospective economics stay sealed.",
        "",
    ]
    return "\n".join(lines) + "\n"

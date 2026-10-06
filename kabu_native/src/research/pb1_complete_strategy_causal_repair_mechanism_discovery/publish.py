"""Write report.json / report.md / audit.xlsx only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import json

import pandas as pd

from research.pb1_complete_strategy_causal_repair_mechanism_discovery import (
    ANALYSIS_ID,
    CASE_FOUND,
    CASE_NONE,
    NEXT_FOUND,
    NEXT_NONE,
    PROGRAM_ID,
)
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.isolation import OUT
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.precommit import EXIT_CANDIDATES, FOLDS, SIZING_CANDIDATES

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


def _flat_row(d: dict[str, Any]) -> dict[str, Any]:
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
    return pd.DataFrame([_flat_row(r) if isinstance(r, dict) else r for r in rows])


def publish(*, gate: dict[str, Any], precommit: dict[str, Any], baseline: dict[str, Any], study: dict[str, Any], decision: dict[str, Any], safety: dict[str, Any]) -> dict[str, Any]:
    found = bool(decision.get("found"))
    verdict = CASE_FOUND if found else CASE_NONE
    nxt = NEXT_FOUND if found else NEXT_NONE
    exit_summ = dict(study.get("exit_summ") or {})
    sizing_summ = dict(study.get("sizing_summ") or {})
    fold_rows = []
    for mech, summ in exit_summ.items():
        for fold in FOLDS:
            s = dict((summ.get("folds") or {}).get(fold) or {})
            fold_rows.append({"mechanism": mech, "fold": fold, **s})
    mech_cmp = []
    for mech, summ in exit_summ.items():
        mech_cmp.append(
            {
                "mechanism": mech,
                "family": "A_EXIT" if mech != "ASF_FIRST_STRUCTURAL_KILL" else "ASF",
                "accepted": bool(summ.get("accepted")),
                "delta_net_bps": summ.get("delta_net_bps"),
                "delta_gross_bps": summ.get("delta_gross_bps"),
                "winner_damage_bps": summ.get("winner_damage_bps"),
                "loss_reduction_bps": summ.get("loss_reduction_bps"),
                "fired_n": summ.get("fired_n"),
                "n": summ.get("n"),
                "dev_positive": summ.get("dev_positive"),
                "conf_positive_n": summ.get("conf_positive_n"),
                "reject_reasons": ",".join(summ.get("reject_reasons") or []),
            }
        )
    supported = [r for r in mech_cmp if r["accepted"]]
    rejected = [r for r in mech_cmp if not r["accepted"]]
    sizing_rows = [{"family": k, **{kk: vv for kk, vv in v.items() if kk != "folds"}} for k, v in sizing_summ.items()]
    tax_rows = [
        {
            "mechanism": m,
            "delta_gross_bps": exit_summ[m].get("delta_gross_bps"),
            "delta_net_bps": exit_summ[m].get("delta_net_bps"),
            "survives_8bps": bool(exit_summ[m].get("survives_tax")),
        }
        for m in EXIT_CANDIDATES
    ]
    conc = []
    for fam, rows in (study.get("sizing_rows") or {}).items():
        bag: dict[str, float] = {}
        for r in rows:
            bag[str(r.get("symbol") or "")] = bag.get(str(r.get("symbol") or ""), 0.0) + abs(float(r.get("net_yen") or 0))
        tot = sum(bag.values()) or 1.0
        top = sorted(bag.items(), key=lambda kv: -kv[1])[:8]
        for i, (sym, yen) in enumerate(top, start=1):
            conc.append({"family": fam, "rank": i, "symbol": sym, "abs_net_yen": yen, "share": yen / tot})
    answers = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "supported_repair_families": decision.get("supported_repair_families"),
        "unsupported_repair_families": decision.get("unsupported_repair_families"),
        "V1_VERDICT_CHANGED": False,
        "CONFIRMATION1_RESCORED_AS_V1": False,
        "V4_ENTRY_CHANGED": False,
        "V5_CREATED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "asf_identifiable_frac": study.get("asf_identifiable_frac"),
        "atr_ref": study.get("atr_ref"),
        "prepared_n": study.get("prepared_n"),
        "missing_rec": study.get("missing_rec"),
        "path_class_counts": study.get("path_class_counts"),
        "COMPLETE_STRATEGY_SHA256": gate.get("COMPLETE_STRATEGY_SHA256"),
        "REPAIR_PRECOMMIT_SHA256": precommit.get("sha256"),
    }
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "identity": gate,
        "precommit": precommit,
        "baseline": baseline,
        "answers": answers,
        "exit_summaries": exit_summ,
        "sizing_summaries": sizing_summ,
        "month_exit": study.get("month_exit"),
        "decision": decision,
        "safety": safety,
        "trades": [],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(
        json.dumps(_clean(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = _markdown(answers, exit_summ, sizing_summ, precommit, study)
    (OUT / "report.md").write_text(md, encoding="utf-8")
    give = list(study.get("giveback_rows") or [])
    win = list(study.get("winner_rows") or [])
    noexp = list(study.get("noexp_rows") or [])
    recross = list(study.get("recross_rows") or [])
    asf = list(study.get("asf_rows") or [])
    exit_rows = []
    for mech, rows in (study.get("by_mech") or {}).items():
        for r in rows:
            exit_rows.append({"mechanism": mech, **{k: v for k, v in r.items() if k != "capture" or True}})
    exposure = []
    for fam, rows in (study.get("sizing_rows") or {}).items():
        ns = [float(r.get("notional_yen") or 0) for r in rows]
        exposure.append(
            {
                "family": fam,
                "n": len(rows),
                "mean_notional": (sum(ns) / len(ns)) if ns else None,
                "max_notional": max(ns) if ns else None,
                "min_notional": min(ns) if ns else None,
            }
        )
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"program": PROGRAM_ID, "analysis": ANALYSIS_ID, "created_at": _now(), "verdict": verdict}]).to_excel(xw, sheet_name="Manifest", index=False)
        _df([baseline]).to_excel(xw, sheet_name="Baseline", index=False)
        _df(
            [
                {
                    "sha256": precommit.get("sha256"),
                    "precommit_id": (precommit.get("contract") or {}).get("precommit_id"),
                    "mfe_trailing_forbidden": True,
                    "loser_only_oracle_forbidden": True,
                    "full_sample_best_threshold_forbidden": True,
                    "require_dev_positive": True,
                    "min_confirmation_folds_positive": 2,
                }
            ]
        ).to_excel(xw, sheet_name="Repair_Precommit", index=False)
        _df(give).to_excel(xw, sheet_name="Giveback_Events", index=False)
        _df(exit_rows).to_excel(xw, sheet_name="Exit_Candidates", index=False)
        _df(win).to_excel(xw, sheet_name="Winner_Damage", index=False)
        _df(noexp).to_excel(xw, sheet_name="No_Expansion", index=False)
        _df(recross).to_excel(xw, sheet_name="Recross", index=False)
        _df(asf).to_excel(xw, sheet_name="ASF_Causal_Clock", index=False)
        _df(sizing_rows).to_excel(xw, sheet_name="Sizing", index=False)
        _df(exposure).to_excel(xw, sheet_name="Exposure", index=False)
        _df(tax_rows).to_excel(xw, sheet_name="Execution_Tax", index=False)
        _df(fold_rows).to_excel(xw, sheet_name="Time_Folds", index=False)
        _df(conc).to_excel(xw, sheet_name="Symbol_Concentration", index=False)
        _df(mech_cmp).to_excel(xw, sheet_name="Mechanism_Comparison", index=False)
        _df(supported or [{"none": True}]).to_excel(xw, sheet_name="Supported_Mechanisms", index=False)
        _df(rejected or [{"none": True}]).to_excel(xw, sheet_name="Rejected_Mechanisms", index=False)
        _df([answers]).to_excel(xw, sheet_name="Decision", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "verdict": verdict, "next": nxt, "out": str(OUT)}


def _markdown(answers: dict[str, Any], exit_summ: dict[str, Any], sizing_summ: dict[str, Any], precommit: dict[str, Any], study: dict[str, Any]) -> str:
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
        f"- CONFIRMATION1_RESCORED_AS_V1 = `{answers.get('CONFIRMATION1_RESCORED_AS_V1')}`",
        f"- V4_ENTRY_CHANGED = `{answers.get('V4_ENTRY_CHANGED')}`",
        f"- V5_CREATED = `{answers.get('V5_CREATED')}`",
        f"- FROZEN_VALIDATION_ECONOMIC_OPENED = `{answers.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- PROSPECTIVE_DATA_OPENED = `{answers.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
        f"REPAIR_PRECOMMIT_SHA256 `{precommit.get('sha256')}`",
        "",
        f"prepared_n={study.get('prepared_n')} missing_rec={study.get('missing_rec')} "
        f"asf_identifiable_frac={study.get('asf_identifiable_frac')} atr_ref={study.get('atr_ref')}",
        "",
        "## supported_repair_families",
        "",
        f"```text\n{answers.get('supported_repair_families')}\n```",
        "",
        "## unsupported_repair_families",
        "",
        f"```text\n{answers.get('unsupported_repair_families')}\n```",
        "",
        "## Exit mechanisms vs V1 (mean delta_net_bps)",
        "",
        "| mechanism | n | fired | d_net_bps | d_gross_bps | winner_dmg | loss_red | DEV+ | C1+ | accepted |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for mech in EXIT_CANDIDATES:
        s = exit_summ.get(mech) or {}
        lines.append(
            f"| {mech} | {s.get('n')} | {s.get('fired_n')} | {s.get('delta_net_bps')} | {s.get('delta_gross_bps')} | "
            f"{s.get('winner_damage_bps')} | {s.get('loss_reduction_bps')} | {s.get('dev_positive')} | "
            f"{s.get('conf_positive_n')} | {s.get('accepted')} |"
        )
    lines += ["", "## Sizing", ""]
    for fam in SIZING_CANDIDATES:
        s = sizing_summ.get(fam) or {}
        lines.append(
            f"- `{fam}` mean_net_bps={s.get('mean_net_bps')} mean_net_yen={s.get('mean_net_yen')} "
            f"top1_share={s.get('top1_symbol_abs_yen_share')} HHI={s.get('hhi_abs_yen')}"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Triggers are completed-bar market state. MFE is diagnostic only.",
        "- Same EXIT clock applied to winners and losers. No loser-only oracle.",
        "- CAP=5 and same-symbol preserved. No V5 implementation this task.",
        "",
    ]
    return "\n".join(lines) + "\n"

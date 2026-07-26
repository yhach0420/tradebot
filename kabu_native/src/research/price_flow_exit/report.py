"""Emit price_flow_exit report.md / report.json / audit.xlsx."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name)[:31])
        if not rows:
            ws.append(["empty"])
            continue
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in ("report.md", "report.json", "audit.xlsx"):
            p.unlink()
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    ev = p.get("evaluation") or {}
    e0 = (ev.get("cohorts") or {}).get("E0") or {}
    e1 = (ev.get("cohorts") or {}).get("E1") or {}
    lines = [
        "# Price-Flow EXIT / Executable MFE Bottleneck Audit",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        f"- bottleneck: {ev.get('bottleneck')}",
        f"- insufficient_oos: {ev.get('insufficient_oos')}",
        f"- EXIT baseline parity: {(e0.get('parity') or {})}",
        "",
        "## A/B/C/D (E0 OOS)",
        "",
        str(e0.get("abcd")),
        "",
        "## A/B/C/D (E1 VCIE OOS)",
        "",
        str(e1.get("abcd")),
        "",
        "## X0–X6 (E0 OOS pnl_5bps / PF / stop / hold)",
        "",
    ]
    for mid, m in (e0.get("modes") or {}).items():
        lines.append(
            f"- {mid}: pnl5={m.get('total_pnl_5bps')} PF={m.get('PF_5bps')} stop={m.get('stop_rate')} "
            f"early={m.get('early_stop_rate')} np={m.get('np_rate')} hold={m.get('avg_hold_sec')} dd={m.get('max_dd_5bps')}"
        )
    lines += [
        "",
        f"- E0 best_mode: {e0.get('best_mode')}",
        f"- E1 best_mode: {e1.get('best_mode')}",
        "",
        "## Safety",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')} entry_unchanged={p.get('entry_unchanged')}",
        "",
        "## 本線採用しない理由",
        "",
        v.get("no_production_reason") or "",
        "高解像度OOSが10日未満のため PRICE_FLOW_EXIT_INSUFFICIENT_OOS。",
        "",
    ]
    return "\n".join(lines) + "\n"


def _mode_row(cohort: Mapping[str, Any], mid: str) -> dict[str, Any]:
    m = (cohort.get("modes") or {}).get(mid) or {}
    return {"mode": mid, **{k: m.get(k) for k in (
        "n", "total_pnl_5bps", "PF_5bps", "stop_rate", "early_stop_rate", "np_rate",
        "avg_hold_sec", "median_hold_sec", "max_dd_5bps", "pos_days", "neg_days",
        "mean_early_exit_regret_pct", "metric_integrity_blocked",
    )}}


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ev = p.get("evaluation") or {}
    cohorts = ev.get("cohorts") or {}
    e0 = cohorts.get("E0") or {}
    e1 = cohorts.get("E1") or {}
    return {
        "README": [{"title": "Price-Flow EXIT", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "DATA_SOURCES": p.get("data_sources") or [{"source": "none"}],
        "SOURCE_AUDIT": [{"capture_days": ",".join(ev.get("capture_days") or []), "oos_days": ",".join(ev.get("oos_days") or [])}],
        "ENTRY_COHORTS": [{"cohort": k, "n": (v or {}).get("n_total"), "n_oos": (v or {}).get("n_oos")} for k, v in cohorts.items()],
        "EXIT_BASELINE": [e0.get("parity") or {"status": "empty"}],
        "EXIT_PARITY": [e0.get("parity") or {"status": "empty"}],
        "EXECUTABLE_PRICE": [{"rule": "BidPrice only for executable MFE; CurrentPrice substitute → QUOTE_NOT_EVALUABLE"}],
        "MFE_MAE": [{"cohort": "E0", **(e0.get("mfe_stats") or {})}, {"cohort": "E1", **(e1.get("mfe_stats") or {})}],
        "MFE_TIMING": [{"note": "time_to_mfe / positive_duration in sample_rows"}],
        "POSITIVE_DURATION": [{"cohort": "E0_sample", **r} for r in (e0.get("sample_rows") or [])[:50]],
        "ABCD_CLASSIFICATION": [{"cohort": "E0", **(e0.get("abcd") or {})}, {"cohort": "E1", **(e1.get("abcd") or {})}],
        "ENTRY_BOTTLENECK": [{"bottleneck": ev.get("bottleneck"), "E0_unrecoverable": (e0.get("abcd") or {}).get("entry_unrecoverable_ratio")}],
        "EXIT_BOTTLENECK": [{"E0_improvable": (e0.get("abcd") or {}).get("exit_improvable_ratio"), "E0_C": (e0.get("abcd") or {}).get("C_ratio")}],
        "PRICE_FLOW_FEATURES": [{"params": ev.get("params")}],
        "X0_CURRENT_EXIT": [_mode_row(e0, "X0")],
        "X1_FAILED_BREAKOUT": [_mode_row(e0, "X1"), _mode_row(e1, "X1")],
        "X2_NO_FOLLOW_THROUGH": [_mode_row(e0, "X2"), _mode_row(e1, "X2")],
        "X3_BREAK_EVEN": [_mode_row(e0, "X3"), _mode_row(e1, "X3")],
        "X4_IMPULSE_DECAY": [_mode_row(e0, "X4"), _mode_row(e1, "X4")],
        "X5_VOLUME_EXHAUSTION": [_mode_row(e0, "X5"), _mode_row(e1, "X5")],
        "X6_COMPOSITE": [_mode_row(e0, "X6"), _mode_row(e1, "X6")],
        "PBV2_EXIT_RESULTS": [_mode_row(e0, m) for m in ("X0", "X1", "X2", "X3", "X4", "X5", "X6")],
        "VCIE_EXIT_RESULTS": [_mode_row(e1, m) for m in ("X0", "X1", "X2", "X3", "X4", "X5", "X6")],
        "CAP5_RESULTS": [{"note": "same ENTRY fixed; CAP=5 ranking not re-opened; slot occupancy via hold_sec"}],
        "MFE_CAPTURE": [{"cohort": "E0", **(e0.get("capture_stats") or {})}, {"cohort": "E1", **(e1.get("capture_stats") or {})}],
        "PROFIT_GIVEBACK": [{"note": "see early_exit_regret in mode metrics"}],
        "EARLY_EXIT_REGRET": [
            {"cohort": "E0", "mode": m, "mean_regret": ((e0.get("modes") or {}).get(m) or {}).get("mean_early_exit_regret_pct")}
            for m in ("X1", "X2", "X3", "X4", "X5", "X6")
        ],
        "LOST_WINNERS": [{"note": "X0 winners that specialized exits cut early — see regret"}],
        "NOPROGRESS": [{"cohort": "E0", "np_rate": ((e0.get("modes") or {}).get("X0") or {}).get("np_rate")}],
        "HOLD_TIME": [{"cohort": "E0", "avg": ((e0.get("modes") or {}).get("X0") or {}).get("avg_hold_sec"), "median": ((e0.get("modes") or {}).get("X0") or {}).get("median_hold_sec")}],
        "SLOT_OCCUPANCY": [{"proxy": "sum hold_sec", "E0_X0_avg_hold": ((e0.get("modes") or {}).get("X0") or {}).get("avg_hold_sec")}],
        "REENTRY_EPISODES": [{"rule": "setup_id / breakout_episode_id tracked; cooldown in VCIE reconstruct"}],
        "WALK_FORWARD": [{"warmup": ev.get("warmup_day"), "oos": ",".join(ev.get("oos_days") or [])}],
        "DAILY_RESULTS": [
            {"cohort": "E0", "mode": "X0", **d} for d in (((e0.get("modes") or {}).get("X0") or {}).get("daily") or [])
        ] or [{"status": "empty"}],
        "SYMBOL_DEPENDENCY": [{"note": "sample_rows include symbol"}],
        "VERDICT": [p.get("verdict") or {"final": "PRICE_FLOW_EXIT_OFFLINE_ONLY"}],
    }

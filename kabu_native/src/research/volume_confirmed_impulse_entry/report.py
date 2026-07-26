"""Emit VCIE report.md / report.json / audit.xlsx (3 files only)."""
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
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    ev = p.get("evaluation") or {}
    methods = ev.get("methods") or {}
    cov = ev.get("coverage") or {}
    lines = [
        "# Volume Confirmed Impulse Entry (VCIE)",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        "## Source audit",
        "",
        str((p.get("source_audit") or {}).get("notes")),
        f"- capture_days: {p.get('capture_days')}",
        f"- complete_event_rows: {p.get('complete_event_rows')}",
        f"- true_volume_delta: {p.get('true_volume_delta')}",
        f"- trade_side: {p.get('trade_side')}",
        "",
        "## Coverage gate",
        "",
        str(cov),
        "",
        "## Method OOS (pnl_5bps / PF / n)",
        "",
    ]
    for mid in (
        "V0_PBv2",
        "V1_CROSS",
        "V2_VOLUME",
        "V3_TRADE_SIDE",
        "V4_FULL_VCIE",
        "V5_PBV2_OR",
        "V6_PBV2_AND",
        "V7_INDEPENDENT",
    ):
        o = (methods.get(mid) or {}).get("oos") or {}
        lines.append(
            f"- {mid}: n={o.get('n')} pnl5={o.get('total_pnl_5bps')} PF={o.get('PF_5bps')} "
            f"stop={o.get('stop_rate')} early={o.get('early_stop_rate')} np={o.get('np_rate')}"
        )
    lines += [
        "",
        "## Triggers",
        "",
        str(ev.get("triggers_summary")),
        "",
        "## Overlap",
        "",
        str(ev.get("overlap")),
        "",
        "## Day-matched",
        "",
        str(ev.get("matched_comparison")),
        "",
        "## Safety",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _method_row(methods: Mapping[str, Any], mid: str) -> dict[str, Any]:
    m = methods.get(mid) or {}
    o = m.get("oos") or {}
    return {"method": mid, **{k: o.get(k) for k in (
        "n", "total_pnl_5bps", "PF_5bps", "stop_rate", "early_stop_rate", "np_rate",
        "large_rise_capture", "winner_capture", "pos_days", "neg_days", "metric_integrity_blocked",
    )}, "trigger_n": m.get("trigger_n"), "cap5": m.get("cap5")}


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ev = p.get("evaluation") or {}
    methods = ev.get("methods") or {}
    sa = p.get("source_audit") or {}
    return {
        "README": [{"title": "VCIE", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "DATA_SOURCES": p.get("data_sources") or [{"source": "none"}],
        "SOURCE_AUDIT": [
            {"verdict": sa.get("verdict"), "notes": sa.get("notes"), "trade_side_policy": sa.get("trade_side_policy")},
            *[{**a, "section": "answers"} for k, a in (sa.get("answers") or {}).items() for a in ([a] if isinstance(a, dict) else [{"value": a}])],
        ],
        "EVENT_PANEL": [
            {
                "complete_event_rows": p.get("complete_event_rows"),
                "capture_days": ",".join(p.get("capture_days") or []),
                "event_panel_ready": p.get("event_panel_ready"),
            }
        ],
        "VOLUME_DELTA_AUDIT": (
            [{"day": d, **st} for d, st in (p.get("load_stats") or {}).items()]
            or [{"status": "empty"}]
        ),
        "TRADE_SIDE_AUDIT": [
            {"policy": p.get("trade_side"), "direct": False, "quote_inferred": True, "tick_rule": True},
        ],
        "FEATURE_LINEAGE": p.get("feature_lineage") or [{"feature": "none"}],
        "FEATURE_COVERAGE": [{"note": "volume impulse requires capture PUSH; missing→NOT_EVALUABLE"}],
        "PRICE_CONTEXT": [{"rule": "micro/range high exclude current bar"}],
        "VOLUME_IMPULSE": [{"thresholds": ev.get("last_thresholds")}],
        "PRICE_CROSS": [{"rule": "prev<=level AND curr>level", "ready": p.get("price_cross_ready")}],
        "BREAKOUT_HOLD": [{"ready": p.get("breakout_hold_ready"), "default": "5 sec or 2 ticks"}],
        "V0_PBV2": [_method_row(methods, "V0_PBv2")],
        "V1_CROSS": [_method_row(methods, "V1_CROSS")],
        "V2_VOLUME": [_method_row(methods, "V2_VOLUME")],
        "V3_TRADE_SIDE": [_method_row(methods, "V3_TRADE_SIDE")],
        "V4_FULL_VCIE": [_method_row(methods, "V4_FULL_VCIE")],
        "V5_PBV2_OR": [_method_row(methods, "V5_PBV2_OR")],
        "V6_PBV2_AND": [_method_row(methods, "V6_PBV2_AND")],
        "V7_INDEPENDENT": [_method_row(methods, "V7_INDEPENDENT")],
        "WALK_FORWARD": [{"oos_days": ",".join(ev.get("oos_days") or []), "capture_days": ",".join(ev.get("capture_days") or [])}],
        "DAY_MATCHED": [ev.get("matched_comparison") or {"status": "empty"}],
        "CAP5": [{"method": mid, **((m.get("cap5") or {}) if isinstance(m.get("cap5"), dict) else {"cap5": m.get("cap5")})} for mid, m in methods.items()] or [{"status": "empty"}],
        "EARLY_STOP": [{"method": mid, "early_stop_rate": (m.get("oos") or {}).get("early_stop_rate"), "stop_rate": (m.get("oos") or {}).get("stop_rate")} for mid, m in methods.items()],
        "NOPROGRESS": [{"method": mid, "np_rate": (m.get("oos") or {}).get("np_rate")} for mid, m in methods.items()],
        "MFE_MAE": [{"hypotheses": ev.get("hypotheses")}],
        "LARGE_RISE": [{"method": mid, "large_rise_capture": (m.get("oos") or {}).get("large_rise_capture")} for mid, m in methods.items()],
        "DAILY_RESULTS": [
            {"method": mid, **d}
            for mid, m in methods.items()
            for d in ((m.get("oos") or {}).get("daily") or [])
        ] or [{"status": "empty"}],
        "DEPENDENCY": [ev.get("overlap") or {"status": "empty"}],
        "VERDICT": [p.get("verdict") or {"final": "VCIE_OFFLINE_ONLY"}],
        "TRIGGER_SAMPLES": (ev.get("trigger_samples") or [{"status": "empty"}])[:2000],
    }

"""Emit report.md / report.json / audit.xlsx only."""
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
    st = p.get("strategies") or {}
    ep = p.get("episode") or {}
    caps = (p.get("cap5") or {}).get("portfolios") or {}
    lines = [
        "# Entry–Exit Contract Integrity (EEC_v2)",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        v.get("summary") or "",
        "",
        "## Episode",
        "",
        str(ep.get("totals")),
        "",
        "## Strategies (after true-episode dedupe)",
        "",
    ]
    for sid in ("EC1", "EC2", "EC3"):
        s = st.get(sid) or {}
        a = s.get("after_dedupe") or {}
        lines.append(
            f"- {sid}: pairing={s.get('pairing')} verdict={s.get('strategy_verdict')} "
            f"n={a.get('n')} pnl={a.get('total_pnl_5bps')} PF={a.get('PF_5bps')} "
            f"struct={a.get('structural_success_rate')} econ={a.get('economic_success_rate')} "
            f"capture={a.get('mean_capture_ratio_positive_mfe_only')}"
        )
        r = a.get("reality") or {}
        for rk in ("R0", "R1", "R2", "R3"):
            rr = r.get(rk) or {}
            lines.append(f"  - {rk}: pnl={rr.get('pnl_5bps')} PF={rr.get('PF_5bps')} n={rr.get('evaluable_n')}")
    lines += ["", "## CAP=5", ""]
    for pid in ("P2", "P3", "P4", "P5"):
        c = caps.get(pid) or {}
        t = c.get("turnover") or {}
        lines.append(
            f"- {pid}: accepted={c.get('accepted')} pnl={c.get('pnl_5bps')} PF={c.get('PF_5bps')} "
            f"trades/day={t.get('trades_per_day')} uncapped={c.get('uncapped_reference')}"
        )
    lines += [
        "",
        f"- P5 reduction: {(p.get('cap5') or {}).get('p5_reduction')}",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- thresholds_unchanged={p.get('entry_exit_thresholds_unchanged')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    st = p.get("strategies") or {}
    caps = (p.get("cap5") or {}).get("portfolios") or {}
    empty = [{"status": "empty"}]

    def samples(sid: str) -> list[dict]:
        return ((st.get(sid) or {}).get("after_dedupe") or {}).get("sample_rows") or []

    reality_rows = []
    for sid in ("EC1", "EC2", "EC3"):
        r = ((st.get(sid) or {}).get("after_dedupe") or {}).get("reality") or {}
        for rk, rr in r.items():
            reality_rows.append({"strategy": sid, "ladder": rk, **(rr or {})})

    return {
        "README": [{"title": "EEC_v2 Integrity", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "EPISODE_SUMMARY": [(p.get("episode") or {}).get("totals") or {"status": "empty"}],
        "EPISODE_BY_STRATEGY": [{"strategy": s, **((st.get(s) or {}).get("episode") or {})} for s in ("EC1", "EC2", "EC3")],
        "DEDUPE_BEFORE_AFTER": [
            {
                "strategy": s,
                "before": (st.get(s) or {}).get("pnl_pf_before"),
                "after": (st.get(s) or {}).get("pnl_pf_after"),
            }
            for s in ("EC1", "EC2", "EC3")
        ],
        "ECONOMIC_SUCCESS": [
            {
                "strategy": s,
                "structural": ((st.get(s) or {}).get("after_dedupe") or {}).get("structural_success_rate"),
                "economic": ((st.get(s) or {}).get("after_dedupe") or {}).get("economic_success_rate"),
                "captured": ((st.get(s) or {}).get("after_dedupe") or {}).get("captured_success_rate"),
                "under_captured": ((st.get(s) or {}).get("after_dedupe") or {}).get("under_captured_success_rate"),
            }
            for s in ("EC1", "EC2", "EC3")
        ],
        "MFE_CAPTURE": [
            {
                "strategy": s,
                "mean_pos_only": ((st.get(s) or {}).get("after_dedupe") or {}).get("mean_capture_ratio_positive_mfe_only"),
                "median_pos_only": ((st.get(s) or {}).get("after_dedupe") or {}).get("median_capture_ratio_positive_mfe_only"),
            }
            for s in ("EC1", "EC2", "EC3")
        ],
        "EXECUTION_REALISM": reality_rows or empty,
        "SAMPLE_TRADES": samples("EC1")[:40] + samples("EC2")[:30] + samples("EC3")[:20] or empty,
        "CAP5_RESULTS": [caps[k] for k in ("P2", "P3", "P4", "P5") if k in caps] or empty,
        "TURNOVER": [{"portfolio": k, **((caps.get(k) or {}).get("turnover") or {})} for k in ("P2", "P3", "P4", "P5")],
        "PAIRING": [{"strategy": s, "pairing": (st.get(s) or {}).get("pairing"), "verdict": (st.get(s) or {}).get("strategy_verdict")} for s in ("EC1", "EC2", "EC3")],
        "P5_REDUCTION": [(p.get("cap5") or {}).get("p5_reduction") or {"status": "empty"}],
        "WALK_FORWARD": [{"warmup": p.get("warmup_day"), "oos": ",".join(p.get("oos_days") or []), "thresholds_frozen": True}],
        "VERDICT": [p.get("verdict") or {"final": "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY"}],
    }

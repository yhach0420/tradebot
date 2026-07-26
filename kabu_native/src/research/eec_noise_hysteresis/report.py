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
    q = (p.get("quadrants") or {}).get("summary") or {}
    arms = p.get("arms") or {}
    res = p.get("resolution") or {}
    pop = p.get("population") or {}
    lines = [
        "# EEC_v3 Adaptive Noise Band & Hysteresis (EC2 diagnostic)",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- SoT: `{p.get('sot')}`",
        f"- final: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        v.get("summary") or "",
        "",
        "## Purpose / Constraints",
        "",
        "- EC2を採用候補として最適化しない。ノイズ境界診断のみ。",
        "- offline only / mainline unchanged / Shadow·Forward禁止 / submit=cancel=live_order=0",
        "- symbol固有・時刻固有閾値禁止。noise gridは事前定義18組のみ。",
        "",
        "## Population (true episodes)",
        "",
        f"- raw_triggers: {pop.get('raw_triggers')}",
        f"- true_episodes: {pop.get('true_episodes')}",
        f"- one_entry: {pop.get('one_entry')} / blocked: {pop.get('episode_blocked')}",
        f"- oos_n: {pop.get('oos_n')}",
        f"- train-selected noise: `{p.get('noise_selected_train')}`",
        "",
        "## Q1–Q4 STRUCTURAL × ECONOMIC",
        "",
    ]
    for qn in ("Q1", "Q2", "Q3", "Q4"):
        s = q.get(qn) or {}
        lines.append(
            f"- {qn}: n={s.get('n')} ({s.get('ratio')}) pnl={s.get('pnl_5bps')} "
            f"mfe={s.get('mean_mfe')} mae={s.get('mean_mae')} uptick={s.get('mean_uptick')}"
        )
    qd = (p.get("quadrants") or {}).get("by_day") or {}
    lines += ["", "### by day", "", str(qd), "", "## Arms (OOS)", ""]
    for a in ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7"):
        s = arms.get(a) or {}
        r1 = ((s.get("reality") or {}).get("R1") or {})
        r3 = ((s.get("reality") or {}).get("R3") or {})
        lines.append(
            f"- {a}: traded={s.get('n_traded')} skip={s.get('n_skipped_no_confirm')} "
            f"pnl={s.get('total_pnl_5bps')} PF={s.get('PF_5bps')} tpd={s.get('trades_per_day')} "
            f"R1PF={r1.get('PF_5bps')} R3PF={r3.get('PF_5bps')} "
            f"cap5={ (s.get('cap5') or {}).get('pnl_5bps') } "
            f"false_entry={s.get('false_entry_n')} false_inv={s.get('false_invalidation_n')} "
            f"w2r={s.get('warning_to_recovery_n')} w2i={s.get('warning_to_invalidation_n')} "
            f"delay={s.get('mean_confirm_delay_sec')} lost_opp={s.get('lost_opportunity_n')}"
        )
    lines += [
        "",
        "## Noise train grid (warmup A3)",
        "",
        f"- rows: {len(p.get('noise_train_grid') or [])}",
        f"- OOS A3 top3: {p.get('noise_oos_grid_A3')}",
        "",
        "## Execution resolution",
        "",
        f"- push_interval: {res.get('push_interval')}",
        f"- price_update_interval: {res.get('price_update_interval')}",
        f"- board_update_interval: {res.get('board_update_interval')}",
        f"- same_quote_hold: {res.get('same_quote_hold')}",
        f"- r3_is_next_push_wait: {res.get('r3_is_next_push_wait')}",
        f"- insufficient: {res.get('insufficient_event_resolution')}",
        f"- note: {res.get('r3_interpretation')}",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    arms = p.get("arms") or {}
    q = p.get("quadrants") or {}
    empty = [{"status": "empty"}]
    arm_rows = []
    for a, s in arms.items():
        arm_rows.append(
            {
                "arm": a,
                "n_traded": s.get("n_traded"),
                "n_skipped_no_confirm": s.get("n_skipped_no_confirm"),
                "pnl": s.get("total_pnl_5bps"),
                "PF": s.get("PF_5bps"),
                "trades_per_day": s.get("trades_per_day"),
                "R0_PF": ((s.get("reality") or {}).get("R0") or {}).get("PF_5bps"),
                "R1_PF": ((s.get("reality") or {}).get("R1") or {}).get("PF_5bps"),
                "R2_PF": ((s.get("reality") or {}).get("R2") or {}).get("PF_5bps"),
                "R3_PF": ((s.get("reality") or {}).get("R3") or {}).get("PF_5bps"),
                "cap5_pnl": (s.get("cap5") or {}).get("pnl_5bps"),
                "cap5_PF": (s.get("cap5") or {}).get("PF_5bps"),
                "false_entry_n": s.get("false_entry_n"),
                "false_invalidation_n": s.get("false_invalidation_n"),
                "true_invalidation_n": s.get("true_invalidation_n"),
                "warning_to_recovery_n": s.get("warning_to_recovery_n"),
                "warning_to_invalidation_n": s.get("warning_to_invalidation_n"),
                "confirm_delay": s.get("mean_confirm_delay_sec"),
                "lost_opportunity_n": s.get("lost_opportunity_n"),
                "pos_days": s.get("pos_days"),
                "neg_days": s.get("neg_days"),
                "dd_trade_sequence_max_dd": s.get("dd_trade_sequence_max_dd"),
            }
        )
    day_rows = []
    for d, counts in (q.get("by_day") or {}).items():
        day_rows.append({"day": d, **counts})
    return {
        "README": [
            {
                "title": "EEC_v3 Noise Hysteresis",
                "run_id": p.get("run_id"),
                "verdict": (p.get("verdict") or {}).get("final"),
                "sot": p.get("sot"),
            }
        ],
        "POPULATION": [p.get("population") or {"status": "empty"}],
        "QUADRANTS": [{"q": k, **v} for k, v in (q.get("summary") or {}).items()] or empty,
        "QUAD_BY_DAY": day_rows or empty,
        "QUAD_SAMPLES": q.get("sample_rows") or empty,
        "NOISE_TRAIN_GRID": p.get("noise_train_grid") or empty,
        "NOISE_SELECTED": [p.get("noise_selected_train") or {"status": "empty"}],
        "NOISE_OOS_TOP3": p.get("noise_oos_grid_A3") or empty,
        "ARMS": arm_rows or empty,
        "A0_SAMPLES": (arms.get("A0") or {}).get("sample_rows") or empty,
        "A3_SAMPLES": (arms.get("A3") or {}).get("sample_rows") or empty,
        "RESOLUTION": [p.get("resolution") or {"status": "empty"}],
        "VERDICT": [p.get("verdict") or {"final": "EEC_V3_OFFLINE_ONLY"}],
    }

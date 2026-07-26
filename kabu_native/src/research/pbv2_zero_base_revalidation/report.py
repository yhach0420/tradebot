"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


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
    # purge extras
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
    integ = p.get("integrity_gates") or v.get("integrity") or {}
    best = p.get("best_candidate") or {}
    cp_best = ((p.get("capture_preserving") or {}).get("best_capture_preserving") or {})
    pb = (p.get("walk_forward") or {}).get("pbv2_baseline") or {}
    dyn = ((p.get("capture_preserving") or {}).get("dynamic_coverage") or (p.get("walk_forward") or {}).get("dynamic_meta") or {})
    lr = p.get("large_rise_summary") or {}
    label = p.get("label_meta") or {}
    lines = [
        "# PBv2 Zero-Base Candidate Revalidation",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- prior_sot_run: `{p.get('prior_sot_run')}`",
        f"- generated_at: `{p.get('generated_at')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 評価基盤4項目",
        "",
        f"- session_coverage: **{integ.get('session_coverage')}**",
        f"- outcome_evaluable: **{integ.get('outcome_evaluable')}**",
        f"- pf_integrity: **{integ.get('pf_integrity')}**",
        f"- feature_availability_bias: **{integ.get('feature_availability_bias')}**",
        f"- all_pass: {integ.get('all_pass')}",
        "",
        f"- n_outcome_evaluable: {label.get('n_outcome_evaluable')}",
        f"- n_pnl_evaluable: {label.get('n_pnl_evaluable')}",
        f"- n_large_rise_evaluable: {label.get('n_large_rise_evaluable')}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        "## Capture-preserving 最良",
        "",
        f"- method: `{cp_best.get('method_id')}`",
        f"- oos: {cp_best.get('oos')}",
        "",
        "## 参考: top_only_imb_near_tv（eligible cohortのみ）",
        "",
        f"- rule_id: `{best.get('rule_id')}`",
        f"- board_class: TOP_ONLY (not FULL_L2)",
        f"- features: {best.get('features')}",
        f"- ops: {best.get('ops')}",
        f"- thresholds (last fold): {best.get('last_thresholds')}",
        "",
        "## 特徴量の意味",
        "",
    ]
    meaning = {
        "f_rise5": "直近5分リターン（押し目=負）",
        "f_mom": "モメンタム継続スコア",
        "f_near_high": "日中高値からの距離%",
        "f_vwap": "VWAP乖離%",
        "f_tv": "売買代金",
        "f_imb": "静的板イムバランス",
        "f_np_imb_chg_60": "60秒板イムバランス変化",
        "f_np_bid_chg_60": "60秒買い数量変化",
        "f_np_ask_chg_60": "60秒売り数量変化",
        "f_np_tv_chg_pct_60": "60秒出来高変化率",
        "f_np_ret_60": "60秒リターン",
        "f_chase": "追いかけフラグ/強度",
        "f_atr": "ATR%",
        "f_spread": "スプレッドbps",
    }
    for f in best.get("features") or []:
        lines.append(f"- `{f}`: {meaning.get(f, 'see FEATURE_DICTIONARY')}")
    lines += [
        "",
        "## カバレッジ",
        "",
        f"- 使用営業日: {p.get('trading_days')}",
        f"- candidate panel件数: {p.get('n_panel')}",
        f"- PBv2 candidate件数: {p.get('n_pbv2_candidates')}",
        f"- non-PBv2件数: {p.get('n_non_pbv2')}",
        f"- large-rise episode件数: {lr.get('large_rise_episode_total')}",
        f"- 動的板 any-feature 日数: {len(dyn.get('lane_c_any_feature_days') or [])}",
        f"- 動的板 full-coverage 日数: {len(dyn.get('lane_c_complete_required_days') or [])}",
        f"- warmup日: {dyn.get('warmup_day')}",
        f"- 動的板 chronological OOS 日数: {len(dyn.get('lane_c_oos_evaluable_days') or [])}",
        "",
        "## PBv2比較（chronological OOS）",
        "",
        f"- PBv2 pnl_5bps: {((pb.get('oos') or {}).get('pnl_5bps'))}",
        f"- PBv2 PF: {((pb.get('oos') or {}).get('pf'))}",
        f"- PBv2 STOP率: {((pb.get('oos') or {}).get('stop_rate'))}",
        f"- PBv2 NoProgress率: {((pb.get('oos') or {}).get('np_rate'))}",
        f"- 最良 pnl_5bps: {((best.get('oos') or {}).get('pnl_5bps'))}",
        f"- 最良 PF: {((best.get('oos') or {}).get('pf'))}",
        f"- 最良 STOP率: {((best.get('oos') or {}).get('stop_rate'))}",
        f"- 最良 NoProgress率: {((best.get('oos') or {}).get('np_rate'))}",
        f"- large-rise捕捉率 PBv2: {lr.get('pbv2_capture_rate')}",
        f"- large-rise捕捉率 zero-base: {lr.get('zero_base_capture_rate')}",
        f"- Winner犠牲: {p.get('winner_sacrifice')}",
        "",
        "## CAP=5",
        "",
    ]
    for row in p.get("cap5") or []:
        lines.append(
            f"- {row.get('method')}: trades={row.get('accepted_trades')} pnl5={row.get('pnl_5bps')} "
            f"PF={row.get('pf')} STOP={row.get('stop_rate')} NP={row.get('np_rate')} "
            f"cap_reject={row.get('rejected_by_cap')}"
        )
    lines += [
        "",
        "## データ品質",
        "",
        f"- leakage: {p.get('leakage')}",
        f"- board quality notes: {p.get('board_quality_notes')}",
        f"- DQ issues: {len(p.get('dq_issues') or [])}",
        "",
        "## 採用しない理由 / 次に必要なデータ",
        "",
        v.get("no_production_reason") or "",
        "",
        v.get("next_data_need") or "",
        "",
        "## 安全確認",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- 現行本線変更なし: {p.get('mainline_unchanged')}",
        f"- Shadow有効化なし / Forward実装なし",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    wf = p.get("walk_forward") or {}
    dyn = wf.get("dynamic_meta") or {}
    cov = p.get("coverage_by_day") or {}
    sheets: dict[str, list[dict[str, Any]]] = {
        "README": [
            {
                "title": "PBv2 zero-base revalidation",
                "run_id": p.get("run_id"),
                "verdict": (p.get("verdict") or {}).get("final"),
                "note": "Paper/offline only. Exactly 3 artifacts.",
            }
        ],
        "DATA_SOURCES": p.get("data_sources") or [{"source": "none"}],
        "COVERAGE_BY_DAY": [{"day": k, **v} for k, v in sorted(cov.items())] or [{"day": "none"}],
        "SESSION_COVERAGE": p.get("session_coverage_audit") or [{"status": "empty"}],
        "INTEGRITY_GATES": [p.get("integrity_gates") or {"status": "empty"}],
        "PARETO_FRONTIER": (p.get("pareto_frontier") or [])[:5000] or [{"status": "empty"}],
        "CAPTURE_METHODS": [
            {
                "method_id": m.get("method_id"),
                "total_pnl_5bps": (m.get("oos") or {}).get("total_pnl_5bps"),
                "PF_5bps": (m.get("oos") or {}).get("PF_5bps"),
                "n": (m.get("oos") or {}).get("n"),
                "keep_ratio_vs_pbv2": m.get("keep_ratio_vs_pbv2"),
                "replacement_eligible": m.get("replacement_eligible"),
                "reject_reason": m.get("reject_reason"),
                **(m.get("capture_vs_pbv2") or {}),
            }
            for m in ((p.get("capture_preserving") or {}).get("methods") or [])
        ]
        or [{"status": "empty"}],
        "CANDIDATE_PANEL_AUDIT": p.get("panel_audit") or [{"status": "empty"}],
        "FEATURE_DICTIONARY": p.get("feature_dictionary") or [{"feature": "none"}],
        "FEATURE_COVERAGE": p.get("feature_coverage") or [{"feature": "none"}],
        "BOARD_QUALITY": p.get("board_quality_rows") or [{"status": "empty"}],
        "LANE_A_RESULTS": _rules_to_rows(wf.get("dense") or []),
        "LANE_B_RESULTS": _rules_to_rows(wf.get("static") or []),
        "LANE_C_RESULTS": _rules_to_rows(wf.get("dynamic") or []),
        "COMBINED_RESULTS": _rules_to_rows(wf.get("combined") or []),
        "WALK_FORWARD_FOLDS": wf.get("folds") or [{"status": "empty"}],
        "THRESHOLD_HISTORY": p.get("threshold_history") or [{"status": "empty"}],
        "BASELINE_COMPARISON": p.get("baseline_comparison") or [{"status": "empty"}],
        "CAP5_PORTFOLIO": p.get("cap5") or [{"status": "empty"}],
        "LARGE_RISE_EPISODES": (p.get("large_rise_episodes") or [])[:5000] or [{"status": "empty"}],
        "MISSED_RISE_REASONS": p.get("missed_rise_reasons") or [{"status": "empty"}],
        "STOP_ANALYSIS": p.get("stop_analysis") or [{"status": "empty"}],
        "NOPROGRESS_ANALYSIS": p.get("np_analysis") or [{"status": "empty"}],
        "WINNER_SACRIFICE": p.get("winner_sacrifice_rows") or [{"status": "empty"}],
        "DAILY_METRICS": p.get("daily_metrics") or [{"status": "empty"}],
        "SYMBOL_DEPENDENCY": p.get("symbol_dependency") or [{"status": "empty"}],
        "DQ_ISSUES": p.get("dq_issues") or [{"status": "none"}],
        "FINAL_RANKING": p.get("final_ranking") or [{"status": "empty"}],
        "VERDICT": [p.get("verdict") or {"final": "ZERO_BASE_OFFLINE_ONLY"}],
    }
    # inject dynamic day meta into LANE_C
    sheets["LANE_C_RESULTS"].append(
        {
            "rule_id": "_coverage_meta",
            "lane_c_any_feature_days": ",".join(dyn.get("lane_c_any_feature_days") or []),
            "lane_c_complete_required_days": ",".join(dyn.get("lane_c_complete_required_days") or []),
            "lane_c_oos_evaluable_days": ",".join(dyn.get("lane_c_oos_evaluable_days") or []),
            "warmup_day": dyn.get("warmup_day"),
        }
    )
    return sheets


def _rules_to_rows(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rules:
        oos = r.get("oos") or {}
        out.append(
            {
                "rule_id": r.get("rule_id"),
                "series": r.get("series"),
                "description": r.get("description"),
                "features": ",".join(r.get("features") or []),
                "ops": ",".join(r.get("ops") or []),
                "last_thresholds": str(r.get("last_thresholds")),
                "oos_pnl_5bps": oos.get("pnl_5bps"),
                "oos_pf": oos.get("pf"),
                "oos_n": oos.get("n"),
                "oos_stop_rate": oos.get("stop_rate"),
                "oos_np_rate": oos.get("np_rate"),
                "oos_pos_days": oos.get("pos_days"),
                "oos_neg_days": oos.get("neg_days"),
                "oos_large_rise_capture": oos.get("large_rise_capture"),
            }
        )
    return out or [{"rule_id": "none"}]

"""Finalize authoritative E1 PM Discord audit artifacts from live summary."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from small_paper.discord_message_builder import (
    _yen_display,
    build_shadow_observation_embed_payload,
    collect_active_shadow_observations,
)
from small_paper.shadow_registry import discord_inventory_from_registry

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "results" / "research" / "e1_x5_pm_discord_audit_20260727"
SESSION = REPO / "results" / "small_paper" / "20260727" / "live_session_122519"


def main() -> int:
    summary = json.loads((SESSION / "small_paper_summary_pm.json").read_text(encoding="utf-8"))
    e1 = summary["e1_x5_forward_shadow"]
    prev = json.loads((OUT / "report.json").read_text(encoding="utf-8"))

    active = collect_active_shadow_observations(summary)
    e1_row = next(r for r in active if "E1" in r["name"])
    fw_row = next(r for r in active if "Flat" in r["name"])
    bd_row = next(r for r in active if "Board" in r["name"])
    assert e1_row["count"] == 173
    assert e1_row["block_count"] == 173
    assert e1_row["delta"] == _yen_display(e1["total_pnl_yen_100"])
    assert e1_row["pf_delta"] is None
    assert abs(e1["avg_pnl_yen_100"] * e1["trades"] - e1["total_pnl_yen_100"]) < 0.1

    st = pd.read_csv(SESSION / "structural_trades.csv")
    st["pnl_yen_100"] = st["entry_price"] * (st["realized_pnl_pct"] / 100.0) * 100.0
    pbv2_285a = float(st.loc[st.symbol == "285A.T", "pnl_yen_100"].sum()) if (st.symbol == "285A.T").any() else 0.0
    pbv2_pnl = float(summary["canonical_total_pnl_yen_100"])
    e1_pnl = float(e1["total_pnl_yen_100"])

    embed = build_shadow_observation_embed_payload({"active_shadows": active}, am_pm="PM")
    e1_embed = embed["description"].split("\n\n")[0]

    report = {
        "run_id": datetime.now(JST).strftime("%Y%m%d_%H%M%S"),
        "phase": "e1_x5_pm_discord_audit_20260727",
        "discord_path": {
            "functions": [
                "src/small_paper/shadow_registry.py::discord_inventory_from_registry",
                "src/small_paper/discord_message_builder.py::collect_active_shadow_observations",
                "src/small_paper/discord_message_builder.py::build_shadow_observation_embed_payload",
                "src/small_paper/discord_message_builder.py::_yen_display",
                "src/small_paper/e1_x5_forward_shadow.py::E1X5ForwardShadowSession.summary",
                "src/small_paper/shadow_summary_runtime_hook.py",
            ],
            "registry_row": next(x for x in discord_inventory_from_registry() if "E1" in x["name"]),
            "json_fields": {
                "target_count": "e1_x5_forward_shadow_trades (= len(exits) = 173 completed trades)",
                "block_count": "same trades value reused as block_count (not reject blocks)",
                "delta_yen": "e1_x5_forward_shadow_total_pnl_yen_100 via _yen_display",
                "delta_formula": "sum over exits of (exit_bid-entry_ask)*100 - entry_ask*100*0.0005",
                "comparison_base": "NONE (absolute E1 PnL, not shadow-minus-PBv2)",
                "pf_delta_N_A": "looks up e1_x5_forward_shadow_pf_delta (absent); profit_factor_yen_100 unused",
                "window": "2026-07-27 12:33-15:23 live_session_122519",
                "am_contamination": False,
                "unit": "completed EXIT trades",
            },
            "reproduced_discord_row": e1_row,
            "reproduced_flat_weak_row": fw_row,
            "reproduced_board_dynamic_row": bd_row,
            "embed_e1_block": e1_embed,
        },
        "detail_173": {
            "persisted_exit_ledger": False,
            "reconstructability": "NOT_AVAILABLE_FROM_DISK",
            "aggregate_identity_proof": {
                "trades": e1["trades"],
                "total_pnl_yen_100": e1["total_pnl_yen_100"],
                "avg_pnl_yen_100": e1["avg_pnl_yen_100"],
                "avg_times_trades": e1["avg_pnl_yen_100"] * e1["trades"],
                "equals_total": True,
                "wins": e1["wins"],
                "losses": e1["losses"],
                "exit_reasons": e1["exit_reasons"],
            },
            "sum_reproduction": {
                "discord_delta_equals_summary_total": True,
                "value": -336949.05,
                "method": "by construction total_pnl = sum(trade nets); Discord displays that field",
            },
            "checks": {
                "duplicate_pbv2_pnl_into_discord_delta": False,
                "pbv2_285A_minus_67000_included_in_e1_delta": False,
                "pbv2_285A_pnl_yen_100": pbv2_285a,
                "open_counted_as_completed_trades": False,
                "score_below_threshold_as_block": False,
                "am_missing_score_as_block": False,
                "entry12_at_1240_relation": "mid-session progress in same PM process; final trades=173",
            },
            "offline_replay_diagnostic": {
                "trades": prev.get("ledger_stats", {}).get("completed"),
                "pnl": prev.get("ledger_stats", {}).get("net_pnl_yen_100"),
                "match_live": False,
                "note": "diagnostic only; not Discord source of truth",
            },
        },
        "e1_actual_pm_final_from_live_summary": {
            "source": "small_paper_summary_pm.json -> e1_x5_forward_shadow",
            "window": "12:33-15:23 PARTIAL_PM_FORWARD",
            "evaluated_count": e1["evaluated_count"],
            "missing_score_count": e1["missing_score_count"],
            "candidate_count": e1["candidate_count"],
            "entries_n": e1["entries_n"],
            "completed_trades": e1["trades"],
            "open": e1["open_positions"],
            "cap_blocked": e1["cap_blocked"],
            "same_symbol_blocked": e1["same_symbol_blocked"],
            "net_pnl_yen_100": e1["total_pnl_yen_100"],
            "profit_factor_yen_100": e1["profit_factor_yen_100"],
            "wins": e1["wins"],
            "losses": e1["losses"],
            "flats": e1["trades"] - e1["wins"] - e1["losses"],
            "exit_reasons": e1["exit_reasons"],
            "avg_bps": e1["avg_bps"],
            "avg_pnl_yen_100": e1["avg_pnl_yen_100"],
            "max_drawdown": e1["max_drawdown"],
            "snap_1240_continuity": {
                "at_1240_approx": {
                    "completed": 7,
                    "pnl": -9235.95,
                    "entries": 12,
                    "open": 5,
                    "evaluated": 696,
                },
                "final": {
                    "completed": 173,
                    "pnl": -336949.05,
                    "entries": 173,
                    "open": 0,
                    "evaluated": e1["evaluated_count"],
                },
                "same_session_continuous": True,
                "first_7_exact_rows": "NOT_PERSISTED",
            },
            "unavailable_without_ledger": [
                "avg_hold_sec",
                "best_worst_trade",
                "pnl_by_symbol",
                "pnl_by_time_band",
                "top1_trade_dependency",
                "per_trade_pbv2_overlap_ids",
            ],
        },
        "pbv2_same_window_compare": {
            "e1_standalone_pnl": e1_pnl,
            "pbv2_canonical_total_pnl_yen_100": pbv2_pnl,
            "e1_minus_pbv2": e1_pnl - pbv2_pnl,
            "pbv2_completed_trades": int(summary.get("flat_weak_range_shadow_completed") or 39),
        },
        "design_fit": {
            "reject_style_appropriate_for_e1": False,
            "why": "E1 is independent CAP5 strategy; Discord maps trades->block and absolute PnL->delta",
            "flat_weak_and_board_dynamic": {
                "flat_weak_target_count": summary["flat_weak_range_shadow_target_count"],
                "flat_weak_true_block_count": summary["flat_weak_range_shadow_block_count"],
                "flat_weak_delta_yen": summary["flat_weak_range_shadow_delta_yen"],
                "flat_weak_delta_pf": summary["flat_weak_range_shadow_delta_pf"],
                "board_dynamic_exit_count": summary["board_dynamic_shadow_exit_count"],
                "board_dynamic_total_delta_yen": summary["board_dynamic_shadow_total_delta_yen"],
                "discord_block_equals_count_key": True,
                "explanation": (
                    "Both show 対象39/block39 because Discord sets block_count=count_key. "
                    "FlatWeak true block_count=17 is ignored. "
                    "Both delta fields are +2200.0 independently in summary."
                ),
            },
        },
        "conclusions": {
            "1_meaning": (
                "−336,949円 = E1_X5 PM完了173取引の net PnL合計（100株・5bps）。"
                "PBv2回避deltaではない。対象/block=173は完了取引数の二重ラベル。"
            ),
            "2_correctness": (
                "Discordフィールド読取は正しい。意味ラベルは誤り（集計形式の誤適用）。"
                "PBv2重複加算・AM混入・openの完了計上はなし。"
                "行明細は未永続化のため1行再構築不可。合計は avg×n=total で恒等確認。"
            ),
            "3_actual_pm": {
                "trades": 173,
                "pnl": -336949.05,
                "pf": 0.4224840804532763,
                "W_L": "63/110",
                "exits": e1["exit_reasons"],
                "evaluated_missing_candidate": [
                    e1["evaluated_count"],
                    e1["missing_score_count"],
                    e1["candidate_count"],
                ],
            },
            "4_adopt": {
                "verdict": "YES_PARTIAL_PM_FORWARD",
                "am_excluded": True,
                "caveat": "表示はreject型で誤解を招くが、中身は独立戦略の実評価結果",
            },
            "5_min_fix": [
                "E1 Discord行を trades/evaluated/missing/net PnL/PF 表示に変更",
                "block件数に trades を流用しない",
                "total_pnl を delta円 と呼ばない（独立戦略用フォーマッタ分岐）",
                "pf は profit_factor_yen_100 を使う",
                "e1 exits を session CSV に永続化",
            ],
        },
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "code_changed": False},
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# E1_X5 PM Discord Audit (2026-07-27)",
        "",
        "## 結論",
        "",
        "Discord の **−336,949円 / 対象173 / block173** は、E1_X5 の **完了取引173件の絶対PnL合計** を reject型 Observation の **delta/block** ラベルで表示したもの。PBv2回避損益ではない。",
        "",
        "**PARTIAL_PM_FORWARD として採用可**（AM除外）。表示形式は不適切。",
        "",
        "## 1. Discord計算経路（実コード）",
        "",
        "| 項目 | 定義 |",
        "|---|---|",
        "| 対象件数=173 | `e1_x5_forward_shadow_trades` = `len(exits)` |",
        "| block件数=173 | 上記を `collect_active_shadow_observations` が `block_count` に流用 |",
        "| delta円=-336,949 | `e1_x5_forward_shadow_total_pnl_yen_100` → `_yen_display` |",
        "| 比較元損益 | **なし**（絶対PnL。shadow−actual ではない） |",
        "| PF差=N/A | `e1_x5_forward_shadow_pf_delta` 未出力（実PF `profit_factor_yen_100=0.422` 未使用） |",
        "| 期間 | PM `live_session_122519` 12:33–15:23。AM非混入 |",
        "| 単位 | **完了EXIT取引** |",
        "",
        "関数チェーン:",
        "",
        "1. `shadow_registry.discord_inventory_from_registry`（E1: count_key=trades, delta_key=total_pnl）",
        "2. `discord_message_builder.collect_active_shadow_observations`",
        "3. `build_shadow_observation_embed_payload`",
        "4. `shadow_summary_runtime_hook` → Discord",
        "",
        "再現された E1 行:",
        "",
        "```",
        e1_embed,
        "```",
        "",
        "## 2. 173件明細",
        "",
        "**exit ledger は永続化されていない**ため、1行ずつの再構築は不可。",
        "",
        "集計恒等式:",
        "",
        f"- trades={e1['trades']}",
        f"- avg_pnl={e1['avg_pnl_yen_100']}",
        f"- avg×n={e1['avg_pnl_yen_100'] * e1['trades']:.2f}",
        f"- total={e1['total_pnl_yen_100']}",
        "",
        "Discord delta はこの total そのもの → **厳密に −336,949.05円**。",
        "",
        "確認結果:",
        "",
        "- PBv2 PnLの重複加算経路: **なし**",
        f"- PBv2 285A.T = {pbv2_285a:.2f}円は E1 Discord delta に含まれない",
        "- openの完了計上: **なし**（trades=exitsのみ、最終open=0）",
        "- threshold未達→block: **なし**",
        "- AM missing-score→block: **なし**",
        "- 12:40 ENTRY12/完了7 は同一PMの途中値。最終173へ継続",
        "",
        "## 3. E1_X5 本来のPM最終成績（live summary）",
        "",
        f"- evaluated / missing / candidate: **{e1['evaluated_count']} / {e1['missing_score_count']} / {e1['candidate_count']}**",
        f"- ENTRY / completed / open / CAP blocked: **{e1['entries_n']} / {e1['trades']} / {e1['open_positions']} / {e1['cap_blocked']}**",
        f"- net PnL: **{e1['total_pnl_yen_100']:.2f}**",
        f"- PF: **{e1['profit_factor_yen_100']}**",
        f"- 勝/負/引分: **{e1['wins']} / {e1['losses']} / {e1['trades'] - e1['wins'] - e1['losses']}**",
        f"- EXIT内訳: **{e1['exit_reasons']}**",
        f"- avg_bps / avg_pnl: **{e1['avg_bps']} / {e1['avg_pnl_yen_100']}**",
        f"- max_drawdown: **{e1['max_drawdown']}**",
        "",
        "銘柄別・best/worst・平均保有・trade ID別重複は exit ledger 未保存のため算出不可。",
        "",
        "## 4. PBv2同一時間帯比較",
        "",
        f"- E1単独: **{e1_pnl:.2f}**",
        f"- PBv2 canonical_total（同PMセッション）: **{pbv2_pnl:.2f}**",
        f"- E1 − PBv2: **{e1_pnl - pbv2_pnl:.2f}**",
        "",
        "## 5. 集計設計の適合性",
        "",
        "- E1を reject型 対象/block/delta で出すのは **不適切**",
        (
            f"- FlatWeak+BoardDynamic 両方 対象39/block39/delta+2200: "
            f"Discordが count_key を block に流用（FlatWeak真のblock={summary['flat_weak_range_shadow_block_count']}）。"
            f" delta+2200は各summaryフィールドが同値。"
        ),
        "",
        "## 最小修正案（未実装）",
        "",
    ]
    lines.extend(f"- {x}" for x in report["conclusions"]["5_min_fix"])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        pd.DataFrame([e1_row, fw_row, bd_row]).to_excel(xw, sheet_name="discord_active_rows", index=False)
        pd.DataFrame([report["discord_path"]["json_fields"]]).to_excel(xw, sheet_name="discord_field_defs", index=False)
        pd.DataFrame([e1]).to_excel(xw, sheet_name="e1_live_summary", index=False)
        pd.DataFrame([report["detail_173"]["aggregate_identity_proof"]]).to_excel(
            xw, sheet_name="aggregate_identity", index=False
        )
        pd.DataFrame([report["detail_173"]["checks"]]).to_excel(xw, sheet_name="detail_checks", index=False)
        pd.DataFrame([report["pbv2_same_window_compare"]]).to_excel(xw, sheet_name="pbv2_compare", index=False)
        pd.DataFrame(
            st[
                [
                    "symbol",
                    "entry_time",
                    "close_time",
                    "entry_price",
                    "close_price",
                    "close_reason",
                    "realized_pnl_pct",
                    "pnl_yen_100",
                ]
            ]
        ).to_excel(xw, sheet_name="pbv2_trades", index=False)
        pd.DataFrame([report["design_fit"]["flat_weak_and_board_dynamic"]]).to_excel(
            xw, sheet_name="fw_bd_audit", index=False
        )
        pd.DataFrame(
            [
                {
                    "conclusion_key": k,
                    "value": json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v,
                }
                for k, v in report["conclusions"].items()
            ]
        ).to_excel(xw, sheet_name="conclusions", index=False)
        pd.DataFrame(
            [
                {
                    "note": "per-trade E1 exit ledger NOT persisted; 173 detail rows unavailable",
                    "replay_diag_trades": report["detail_173"]["offline_replay_diagnostic"]["trades"],
                    "replay_diag_pnl": report["detail_173"]["offline_replay_diagnostic"]["pnl"],
                }
            ]
        ).to_excel(xw, sheet_name="ledger_limitation", index=False)
        top20 = prev.get("top20_abs_delta_contrib") or []
        if top20:
            pd.DataFrame(top20).to_excel(xw, sheet_name="replay_diag_top20", index=False)

    print("UPDATED", OUT)
    print("e1", e1_pnl, "pbv2", pbv2_pnl)
    print(e1_embed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

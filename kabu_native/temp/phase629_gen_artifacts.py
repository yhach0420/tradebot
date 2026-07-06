"""Phase629: generate stage_dependency_graph.drawio + static CSVs."""
import csv
import html
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "reports" / "phase629_stage_refactoring"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- drawio ----
_id = 1


def nid() -> str:
    global _id
    _id += 1
    return f"n{_id}"


def node(x, y, w, h, label, style):
    i = nid()
    return i, (
        f'<mxCell id="{i}" value="{html.escape(label)}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def edge(src, dst, label=""):
    i = nid()
    lab = f' value="{html.escape(label)}"' if label else ""
    return (
        f'<mxCell id="{i}"{lab} style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=10;" '
        f'edge="1" parent="1" source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>'
    )


STAGE_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=11;align=left;verticalAlign=top;spacing=6;"
DC_STYLE = "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=10;"
SIDE_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=10;align=left;verticalAlign=top;spacing=6;"
EXT_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;fontSize=10;align=left;verticalAlign=top;spacing=6;"

cells = []
Y = 40
X = 60
W = 340
H = 96
GAP = 44

stages = [
    ("Stage0: Payload Normalize\n_stage0_normalize_payload\n"
     "enrich_payload / candidate生成 / price ring\nscan begin + flush_on_begin / tick counters",
     "Stage0NormalizedPayload"),
    ("Stage1: Freshness\n_stage1_evaluate_freshness\n"
     "am_pm/universe pre-gate → expectancy fields →\ncompute_entry_freshness / evaluate_entry_data_freshness (v2)",
     "Stage1FreshnessResult"),
    ("Stage2: PBv2\n_stage2_evaluate_pbv2\n"
     "_enrich_trade_for_pullback_guard →\nExposureGate.evaluate_entry (cluster guard含む) →\n_record_pbv2_internal_reject",
     "Stage2PBv2Result (GateDecision immutable)"),
    ("Stage3: Cluster Guard\n_stage3_cluster_decision\n"
     "Stage2結果の read-only 分類\nPASS / REJECT / FEATURE_INCOMPLETE",
     "Stage3ClusterDecision (frozen)"),
    ("Stage4: OR Overlay\n_stage4_finalize_decision\n"
     "_maybe_try_or_overlay_entry\nor_overlay_reason / final_reject_reason\nPBv2 reason 変更禁止",
     "Stage4FinalEntryDecision"),
    ("Stage6①: Post Entry (candidate記録)\n_stage6_record_candidate\n"
     "candidate event / profiler / latency trace\nrecord_symbol_eval audit / on_post_eval",
     "Stage6CandidateRecord"),
]

prev_dc = None
stage_ids = []
for label, dc_label in stages:
    sid, c = node(X, Y, W, H, label, STAGE_STYLE)
    cells.append(c)
    stage_ids.append(sid)
    did, c2 = node(X + W + 60, Y + 20, 300, 44, dc_label, DC_STYLE)
    cells.append(c2)
    cells.append(edge(sid, did, "出力"))
    if prev_dc is not None:
        cells.append(edge(prev_dc, sid, "入力"))
    prev_dc = did
    Y += H + GAP

# branch after stage6-1
y_branch = Y
s5, c = node(X - 20, y_branch, W // 2 + 60, H, "Stage5: Entry Execute\n_stage5_execute_entry\nqueue / flush / register_entry\npositions / accepted rows", STAGE_STYLE)
cells.append(c)
s6r, c = node(X + W // 2 + 80, y_branch, W // 2 + 80, H, "Stage6②: Post Entry (reject記録)\n_stage6_record_reject\nreject row / rejected event\nDiscord notify", STAGE_STYLE)
cells.append(c)
cells.append(edge(prev_dc, s5, "accept"))
cells.append(edge(prev_dc, s6r, "reject"))

# side nodes
ws, c = node(X + W + 430, 40, 240, 60, "WebSocket PUSH\n(_loop / run_push_replay_dry_run)", SIDE_STYLE)
cells.append(c)
cells.append(edge(ws, stage_ids[0], "payload"))
ob, c = node(X + W + 430, 140, 240, 74, "held-position tick (EXIT)\n_observer_open_position_tick\nStage0→Stage1間 (非ENTRY stage)", EXT_STYLE)
cells.append(c)
cells.append(edge(stage_ids[0], ob, ""))
tr, c = node(X + W + 430, 250, 240, 74, "StageTraceLogger\nDEBUG時のみ start/end 記録\nENTRY_PIPELINE_STAGE_TRACE=1", EXT_STYLE)
cells.append(c)

xml = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<mxfile host="app.diagrams.net" type="device">\n'
    '<diagram id="phase629_stages" name="phase629_stage_dependency">\n'
    f'<mxGraphModel dx="1200" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
    f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="1400" math="0" shadow="0">\n'
    "<root>\n"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>\n'
    + "\n".join(cells)
    + "\n</root>\n</mxGraphModel>\n</diagram>\n</mxfile>\n"
)
(OUT / "stage_dependency_graph.drawio").write_text(xml, encoding="utf-8")

# ---------------------------------------------------------------- CSVs ------
resp_rows = [
    ["stage", "component", "file", "responsibility", "input", "output", "can_change_gate_decision", "can_block_entry"],
    ["Stage0", "_stage0_normalize_payload", "src/small_paper/pilot_runner.py",
     "scan begin/flush_on_begin; symbol解決; ExtensionBus.on_push_tick; tick/gap counters; price ring append; feature_bridge.update+enrich_payload; candidate trade生成 (HBRecent/board imbalance含む)",
     "WebSocket PUSH payload", "Stage0NormalizedPayload", "no", "no (symbol未解決時のみ処理終了)"],
    ["(非stage)", "_observer_open_position_tick", "src/small_paper/pilot_runner.py",
     "保有中銘柄のEXIT tick処理 (observer.on_tick → exit events)。Stage0→Stage1間、元コードと同順序",
     "Stage0NormalizedPayload", "(exit events書込)", "no", "no"],
    ["Stage1", "_stage1_evaluate_freshness", "src/small_paper/pilot_runner.py",
     "am_pm_entry_stop/outside_refresh_universe pre-gate; expectancy score fields; compute_entry_freshness; evaluate_entry_data_freshness (v2: event/board/trade stale + tag); staleカウンタ",
     "Stage0NormalizedPayload", "Stage1FreshnessResult (short_circuit_decision含む)", "yes (stale/pre-gate reject生成)", "yes"],
    ["Stage2", "_stage2_evaluate_pbv2", "src/small_paper/pilot_runner.py",
     "_enrich_trade_for_pullback_guard; ExposureGate.evaluate_entry (PBv2全ガード連鎖: score_v2/momentum/board/quality/high_drift/cluster guard/価格リスク/CAP等); pbv2_internal_reason永続化 (Phase627)",
     "Stage0NormalizedPayload, Stage1FreshnessResult(fresh時のみ)", "Stage2PBv2Result (GateDecision=immutable)", "yes (PBv2判定本体)", "yes"],
    ["Stage3", "_stage3_cluster_decision", "src/small_paper/pilot_runner.py + entry_pipeline_stages.classify_cluster_stage",
     "Stage2内で実行済みの entry_cluster_guard 結果を read-only 分類 (PASS/REJECT/FEATURE_INCOMPLETE)。guard本体はExposureGate内のまま (ロジック移動禁止のため)",
     "Stage2PBv2Result, trade", "Stage3ClusterDecision (frozen)", "no", "no"],
    ["Stage4", "_stage4_finalize_decision", "src/small_paper/pilot_runner.py",
     "_maybe_try_or_overlay_entry (PBv2 reject時のみ); or_overlay_reason/final_reject_reason記録; gate_evaluations増分。PBv2 reason変更禁止",
     "Stage2PBv2Result (or short-circuit), Stage1FreshnessResult", "Stage4FinalEntryDecision", "yes (OR overlayがrejectをacceptに変え得る)", "yes"],
    ["Stage5", "_stage5_execute_entry", "src/small_paper/pilot_runner.py",
     "accept時: batch queue (queue_accepted_candidate/maybe_flush_after_eval/_process_scan_flush) または直接 _execute_accepted_entry (register_entry/positions/accepted rows)",
     "Stage0NormalizedPayload, Stage4FinalEntryDecision, Stage6CandidateRecord", "(positions/accepted rows書込)", "no", "yes (max_entries_per_scan reject)"],
    ["Stage6", "_stage6_record_candidate", "src/small_paper/pilot_runner.py",
     "candidate event書込; stage profiler finish; latency trace finish; score5 ordinal; entry_scan record_symbol_eval audit; ExtensionBus.on_post_eval",
     "Stage0/1/4 dataclasses", "Stage6CandidateRecord", "no", "no"],
    ["Stage6", "_stage6_record_reject", "src/small_paper/pilot_runner.py",
     "reject row構築 (理由別フィールド+errors.jsonl); rejected event書込; Discord notify (cap blocked/rejected)",
     "Stage0NormalizedPayload, Stage4FinalEntryDecision, Stage6CandidateRecord", "(rejects/events/Discord書込)", "no", "no"],
    ["orchestrator", "_process_push_payload", "src/small_paper/pilot_runner.py",
     "Stage0→(observer tick)→Stage1→[Stage2→Stage3]→Stage4→Stage6①→(accept:Stage5 / reject:Stage6②)。StageTraceLogger (DEBUG時のみ)",
     "PUSH payload", "(なし)", "no", "no"],
]
with (OUT / "stage_responsibility.csv").open("w", encoding="utf-8-sig", newline="") as f:
    csv.writer(f).writerows(resp_rows)

io_rows = [
    ["stage", "input_dataclass", "output_dataclass", "state_read", "state_write", "side_effects"],
    ["Stage0", "(raw PUSH Mapping)", "Stage0NormalizedPayload",
     "entry_scan scan state; last_symbol_tick; symbol_price_ring; feature_bridge rolling state",
     "state.push_messages/stale_tick_count/data_gap_count/quality_*; price ring; or_overlay day tick; slm_guard ingest",
     "scan flush_on_begin (満了時 Stage5相当のflush実行); ExtensionBus.on_push_tick"],
    ["Stage1", "Stage0NormalizedPayload", "Stage1FreshnessResult",
     "am_pm_policy; entry_eligible_symbols; entry_scan freshness設定",
     "state.outside_refresh_universe_reject_count / event_stale_reject_count / board_stale_reject_count / trade_stale_tag_count / stale_reason_counts; trade[freshness系フィールド]",
     "ExtensionBus.mark_freshness_check"],
    ["Stage2", "Stage0NormalizedPayload + Stage1FreshnessResult", "Stage2PBv2Result",
     "gate全ガード状態 (cooloff/cluster/cap/day_pnl等); symbol_universe_meta; price ring",
     "trade[guard fields, pbv2_internal_reason/gate]; state.pbv2_internal_reason_counts",
     "ExtensionBus.mark_pbv2_start/end; stage profiler marks"],
    ["Stage3", "Stage2PBv2Result + trade", "Stage3ClusterDecision (frozen)",
     "decision/trade の cluster guard fields", "(なし)", "(なし: read-only)"],
    ["Stage4", "Stage2PBv2Result (None可) + Stage1FreshnessResult", "Stage4FinalEntryDecision",
     "state.or_overlay; observer.has_open; entry_eligible_symbols",
     "trade[or_overlay_reason/final_reject_reason]; state.gate_evaluations; or_overlay counters",
     "OR overlay評価 (reject→OR accept可)"],
    ["Stage5", "Stage0 + Stage4 + Stage6CandidateRecord", "(なし)",
     "entry_scan batch queue; observer positions; gate.state.open_slots",
     "accepted rows; positions; register_entry; scan flush (max_entries_per_scan reject含む)",
     "writer.append_event(accepted); Discord entry notify (Stage5内 _execute_accepted_entry経由)"],
    ["Stage6①", "Stage0 + Stage1 + Stage4", "Stage6CandidateRecord",
     "discord_ux score5; entry_scan audit writer",
     "state.events(candidate); score5 ordinal",
     "writer.append_event(candidate); record_symbol_eval audit; ExtensionBus.on_post_eval; profiler finish_tick; latency trace finish"],
    ["Stage6②", "Stage0 + Stage4 + Stage6CandidateRecord", "(なし)",
     "decision理由別フィールド; discord.active",
     "state.reject_rows; state.events(rejected); 理由別カウンタ",
     "writer.append_event(rejected); writer.append_error(理由別); Discord notify_rejected/notify_entry_cap_blocked"],
]
with (OUT / "stage_io_matrix.csv").open("w", encoding="utf-8-sig", newline="") as f:
    csv.writer(f).writerows(io_rows)

cg_rows = [
    ["before_location", "before_lines_approx", "after_function", "notes"],
    ["_process_push_payload 冒頭 (scan begin〜candidate生成〜quality counters)", "2195-2329",
     "_stage0_normalize_payload", "コード逐語移動。bare return → None返却 (symbol未解決)"],
    ["_process_push_payload observer.on_tick ブロック", "2310-2329",
     "_observer_open_position_tick", "EXIT tick処理。Stage0→Stage1間で同順序実行"],
    ["_process_push_payload if/elif/else (am_pm/universe/freshness)", "2330-2428",
     "_stage1_evaluate_freshness", "pre-gate分岐はshort_circuit_decisionとして返却。ref_now未定義分岐は ref_now_unbound=True で保存"],
    ["_process_push_payload PBv2ブロック", "2429-2445",
     "_stage2_evaluate_pbv2", "_enrich_trade_for_pullback_guard〜_record_pbv2_internal_reject"],
    ["(新規: 分類のみ)", "-",
     "_stage3_cluster_decision", "cluster guard実行はStage2内のまま。結果のread-only分類のみ追加 (挙動影響なし)"],
    ["_process_push_payload OR overlay〜gate_evaluations", "2446-2457",
     "_stage4_finalize_decision", "or_overlay_reason/final_reject_reason設定含む"],
    ["_process_push_payload candidate event〜on_post_eval", "2459-2537",
     "_stage6_record_candidate", "ref_now NameError挙動を明示的UnboundLocalErrorとして保存 (既存潜在バグの挙動維持)"],
    ["_process_push_payload accept分岐", "2539-2588",
     "_stage5_execute_entry", "batch queue/flush or 直接execute"],
    ["_process_push_payload reject分岐", "2589-3039",
     "_stage6_record_reject", "理由別reject row構築〜Discord notifyまで逐語移動"],
    ["_process_push_payload (関数自体)", "2186-3039",
     "_process_push_payload (orchestrator)", "シグネチャ不変。Stage0→6を dataclass 受け渡しで順次呼出し + StageTraceLogger"],
]
with (OUT / "before_after_callgraph.csv").open("w", encoding="utf-8-sig", newline="") as f:
    csv.writer(f).writerows(cg_rows)

print("artifacts written to", OUT)

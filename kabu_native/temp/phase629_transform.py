"""Phase629: mechanical dedent/split of _stage5_execute_entry accept/reject bodies."""
from pathlib import Path

FP = Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
text = FP.read_text(encoding="utf-8")
lines = text.split("\n")

# locate markers
def find(pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    raise SystemExit(f"marker not found from {start}")

i_def5 = find(lambda l: l.startswith("def _stage5_execute_entry("))
i_iftrue = find(lambda l: l == "    if True:", i_def5)
i_else = find(lambda l: l == "    else:", i_iftrue)
# reject body ends right before the blank lines preceding def _quality_ge_0_55_count
i_qual = find(lambda l: l.startswith("def _quality_ge_0_55_count("), i_else)
# trim trailing blank lines between reject body end and def
j = i_qual - 1
while lines[j].strip() == "":
    j -= 1
i_reject_end = j  # inclusive

def dedent4(seg):
    out = []
    for l in seg:
        if l.startswith("    "):
            out.append(l[4:])
        elif l.strip() == "":
            out.append(l)
        else:
            raise SystemExit(f"unexpected indent: {l!r}")
    return out

accept_body = dedent4(lines[i_iftrue + 1 : i_else])
reject_body = dedent4(lines[i_else + 1 : i_reject_end + 1])

stage6_reject_header = '''

def _stage6_record_reject(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    final: Stage4FinalEntryDecision,
    rec: Stage6CandidateRecord,
) -> None:
    """Phase629 Stage6 (part 2): reject row / rejected event / Discord notify.

    Code moved verbatim from the _process_push_payload reject branch.
    """
    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    enriched = norm.enriched
    msg_i = norm.msg_i
    decision = final.decision
    score5_ord = rec.score5_ord'''.split("\n")

orchestrator = '''

def _process_push_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
    t0_push_received_at: Optional[str] = None,
    t0_mono: Optional[float] = None,
) -> None:
    """Phase629 ENTRY pipeline orchestrator (Stage0..Stage6).

    Structure-only refactoring: every stage function contains the original
    _process_push_payload code moved verbatim; execution order, side effects
    and outputs are identical to the pre-Phase629 single-function version.
    Stages exchange data exclusively through the Stage* dataclasses.
    """
    trace = StageTraceLogger(symbol=symbol or "", msg_i=msg_i)
    trace.start("stage0_payload_normalize")
    norm = _stage0_normalize_payload(
        ctx,
        payload,
        msg_i,
        symbol=symbol,
        t0_push_received_at=t0_push_received_at,
        t0_mono=t0_mono,
    )
    trace.end("stage0_payload_normalize", note="no_symbol" if norm is None else "")
    if norm is None:
        return
    if trace.enabled:
        trace.symbol = norm.symbol
    _observer_open_position_tick(ctx, norm)
    trace.start("stage1_freshness")
    fresh = _stage1_evaluate_freshness(ctx, norm)
    trace.end("stage1_freshness", note=fresh.pre_gate_reason or (fresh.stale_reason or ""))
    pbv2: Optional[Stage2PBv2Result] = None
    if fresh.short_circuit_decision is None:
        trace.start("stage2_pbv2")
        pbv2 = _stage2_evaluate_pbv2(ctx, norm)
        trace.end("stage2_pbv2", note=str(getattr(pbv2.decision, "reason", "") or ""))
        trace.start("stage3_cluster_guard")
        cluster = _stage3_cluster_decision(norm, pbv2)
        trace.end("stage3_cluster_guard", note=cluster.status)
    trace.start("stage4_or_overlay")
    final = _stage4_finalize_decision(ctx, norm, fresh, pbv2)
    trace.end("stage4_or_overlay", note=final.entry_route)
    trace.start("stage6_post_entry")
    rec = _stage6_record_candidate(ctx, norm, fresh, final)
    trace.end("stage6_post_entry", note="candidate_recorded")
    if final.decision.accept:
        trace.start("stage5_entry_execute")
        _stage5_execute_entry(ctx, norm, final, rec)
        trace.end("stage5_entry_execute")
    else:
        trace.start("stage6_post_entry")
        _stage6_record_reject(ctx, norm, final, rec)
        trace.end("stage6_post_entry", note="reject_recorded")
'''.split("\n")

new_lines = (
    lines[: i_iftrue]
    + accept_body
    + stage6_reject_header
    + reject_body
    + orchestrator
    + [""]
    + lines[i_qual:]
)
FP.write_text("\n".join(new_lines), encoding="utf-8")
print("transform done:", len(lines), "->", len(new_lines))

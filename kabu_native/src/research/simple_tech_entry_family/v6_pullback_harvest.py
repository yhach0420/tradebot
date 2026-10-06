"""Join V1 TREND_PASS opps with V6 RCA markout/PQ1. No recapture. No C14."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family import PULLBACK_LOOKBACK, RCI_CROSS_LEVEL, VOLUME_MULT
from research.simple_tech_entry_family.harvest import CACHE
from research.simple_tech_entry_family.v6_harvest import V6_CACHE, attach_tq_pq_day

def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


PR_CACHE = CACHE / "v6_pullback_rule"

KEEP_MARK = (
    "executable_signal",
    "markout_30",
    "markout_60",
    "markout_180",
    "markout_300",
    "cost_recovered_30",
    "cost_recovered_60",
    "cost_recovered_180",
    "cost_recovered_300",
    "mfe_bps",
    "mae_bps",
    "PQ1",
)


def _key(row: dict[str, Any]) -> tuple[str, str, float]:
    return (
        str(row.get("date") or ""),
        str(row.get("symbol") or "").replace(".T", ""),
        float(row.get("t0") or 0.0),
    )


def _ok(v: Any) -> bool:
    return _finite(v)


def attach_downstream_day(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attach_tq_pq_day(opps)
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in opps:
        s = _bare(r.get("symbol"))
        if s:
            by[s].append(r)
    pb = int(PULLBACK_LOOKBACK)
    rci_lv = float(RCI_CROSS_LEVEL)
    vol_m = float(VOLUME_MULT)
    for xs in by.values():
        xs.sort(key=lambda r: int(r.get("i") or 0))
        highs = [float(r["high"]) if _ok(r.get("high")) else None for r in xs]
        closes = [float(r["close"]) if _ok(r.get("close")) else None for r in xs]
        lo_bb = [float(r["bb_lower"]) if _ok(r.get("bb_lower")) else None for r in xs]
        for k, r in enumerate(xs):
            ok_bb = True
            window = list(range(max(0, k - pb + 1), k + 1))
            if len(window) < pb:
                ok_bb = False
            else:
                for j in window:
                    cl, bb = closes[j], lo_bb[j]
                    if cl is None or bb is None or float(cl) < float(bb) - 1e-12:
                        ok_bb = False
                        break
            prev = r.get("rci9_prev")
            cur = r.get("rci9")
            rci_ok = _ok(prev) and _ok(cur) and float(prev) <= rci_lv + 1e-12 and float(cur) > rci_lv
            hi_prev = highs[k - 1] if k >= 1 else None
            cl = closes[k]
            e9 = float(r["ema9"]) if _ok(r.get("ema9")) else None
            up = float(r["bb_upper"]) if _ok(r.get("bb_upper")) else None
            pa_ok = (
                cl is not None
                and e9 is not None
                and hi_prev is not None
                and up is not None
                and float(cl) > float(e9)
                and float(cl) > float(hi_prev)
                and float(cl) <= float(up) + 1e-12
            )
            vol = float(r["volume"]) if _ok(r.get("volume")) else None
            med = float(r["vol_med5"]) if _ok(r.get("vol_med5")) else None
            vol_ok = vol is not None and float(vol) > 0 and med is not None and float(vol) >= vol_m * float(med) - 1e-12
            reason = str(r.get("board_reason") or "")
            r["bb_close_ok"] = bool(ok_bb)
            r["rci_ok"] = bool(rci_ok)
            r["pa_ok"] = bool(pa_ok)
            r["vol_ok"] = bool(vol_ok)
            r["board_ok"] = reason == ""
            r["s1_v1"] = bool(r.get("s1"))
            r["s2_v1"] = bool(r.get("s2"))
            r["s3_v1"] = bool(r.get("s3"))
            r["s4_v1"] = bool(r.get("s4"))
            r["s5_v1"] = bool(r.get("s5"))
            r["s6_v1"] = bool(r.get("s6"))
            r["s7_v1"] = bool(r.get("s7"))
    return opps


def join_pre_pullback(v1_opps: list[dict[str, Any]], v6_rows: list[dict[str, Any]]) -> dict[str, Any]:
    attach_downstream_day(v1_opps)
    pre = [r for r in v1_opps if r.get("s1")]
    by_v6 = {_key(r): r for r in v6_rows}
    rows: list[dict[str, Any]] = []
    missing = 0
    pq_mismatch = 0
    for opp in pre:
        k = _key(opp)
        v6 = by_v6.get(k)
        if v6 is None:
            missing += 1
            continue
        rec = {kk: v6.get(kk) for kk in KEEP_MARK}
        rec["date"] = str(opp.get("date") or rec.get("date") or "")
        rec["symbol"] = str(opp.get("symbol") or rec.get("symbol") or "").replace(".T", "")
        rec["t0"] = float(opp.get("t0") or rec.get("t0") or 0.0)
        rec["session"] = "AM"
        rec["PQ1"] = v6.get("PQ1") if _finite(v6.get("PQ1")) else opp.get("PQ1")
        if _finite(v6.get("PQ1")) and _finite(opp.get("PQ1")) and abs(float(v6["PQ1"]) - float(opp["PQ1"])) > 1e-6:
            pq_mismatch += 1
        rec["bb_close_ok"] = bool(opp.get("bb_close_ok"))
        rec["rci_ok"] = bool(opp.get("rci_ok"))
        rec["pa_ok"] = bool(opp.get("pa_ok"))
        rec["vol_ok"] = bool(opp.get("vol_ok"))
        rec["board_ok"] = bool(opp.get("board_ok"))
        rec["s1_v1"] = True
        rec["s2_v1"] = bool(opp.get("s2_v1"))
        rec["s3_v1"] = bool(opp.get("s3_v1"))
        rec["s4_v1"] = bool(opp.get("s4_v1"))
        rec["s5_v1"] = bool(opp.get("s5_v1"))
        rec["s6_v1"] = bool(opp.get("s6_v1"))
        rec["s7_v1"] = bool(opp.get("s7_v1"))
        rec["board_reason"] = str(opp.get("board_reason") or "")
        rec["executable_signal"] = bool(v6.get("executable_signal"))
        rows.append(rec)
    return {
        "ok": missing == 0 and len(rows) == len(pre),
        "missing_n": missing,
        "pre_n": len(pre),
        "joined_n": len(rows),
        "pq_mismatch_n": pq_mismatch,
        "rows": rows,
    }


def save_pr_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "v6_rca_spec_sha": body.get("v6_rca_spec_sha"),
            "parent_spec_sha": body.get("parent_spec_sha"),
            "joined_n": body.get("joined_n"),
            "pre_n": body.get("pre_n"),
            "missing_n": body.get("missing_n"),
            "pq_mismatch_n": body.get("pq_mismatch_n"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


__all__ = ["PR_CACHE", "V6_CACHE", "join_pre_pullback", "save_pr_day_cache", "attach_downstream_day"]

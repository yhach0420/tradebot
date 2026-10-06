"""Join V1 PRE_VOLUME opps with V4 RCA VQ2/markout rows. No recapture. No C14."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.harvest import CACHE
from research.simple_tech_entry_family.v4_harvest import V4_CACHE

PR_CACHE = CACHE / "v4_persistence_rule"

KEEP = (
    "date",
    "session",
    "symbol",
    "t0",
    "bar_minute",
    "executable_signal",
    "ask_t0",
    "ask_qty",
    "bid_t0",
    "ask_reason",
    "volume",
    "vol_accel",
    "VQ1",
    "VQ2",
    "rci9",
    "ema9",
    "ema21",
    "close",
    "bb_upper",
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
    "v1_signal",
)


def _key(row: dict[str, Any]) -> tuple[str, str, float]:
    return (
        str(row.get("date") or ""),
        str(row.get("symbol") or "").replace(".T", ""),
        float(row.get("t0") or 0.0),
    )


def join_pre_volume(v1_opps: list[dict[str, Any]], v4_rows: list[dict[str, Any]]) -> dict[str, Any]:
    pre = [r for r in v1_opps if r.get("s4")]
    by_v4 = {_key(r): r for r in v4_rows}
    rows: list[dict[str, Any]] = []
    missing = 0
    for opp in pre:
        k = _key(opp)
        v4 = by_v4.get(k)
        if v4 is None:
            missing += 1
            continue
        rec = {kk: v4.get(kk) for kk in KEEP}
        rec["date"] = str(opp.get("date") or rec.get("date") or "")
        rec["symbol"] = str(opp.get("symbol") or rec.get("symbol") or "").replace(".T", "")
        rec["t0"] = float(opp.get("t0") or rec.get("t0") or 0.0)
        rec["vol_accel"] = opp.get("vol_accel") if opp.get("vol_accel") is not None else v4.get("vol_accel")
        rec["s5_v1"] = bool(opp.get("s5"))
        rec["s6_v1"] = bool(opp.get("s6"))
        rec["s7_v1"] = bool(opp.get("s7"))
        rec["board_reason"] = str(opp.get("board_reason") or "")
        rec["board_ok"] = rec["board_reason"] == ""
        rec["session"] = "AM"
        rows.append(rec)
    return {
        "ok": missing == 0 and len(rows) == len(pre),
        "missing_n": missing,
        "pre_n": len(pre),
        "joined_n": len(rows),
        "rows": rows,
    }


def save_pr_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "v4_rca_spec_sha": body.get("v4_rca_spec_sha"),
            "parent_spec_sha": body.get("parent_spec_sha"),
            "joined_n": body.get("joined_n"),
            "pre_n": body.get("pre_n"),
            "missing_n": body.get("missing_n"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


__all__ = ["PR_CACHE", "V4_CACHE", "join_pre_volume", "save_pr_day_cache"]

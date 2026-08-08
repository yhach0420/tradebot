"""Patch X33C weighting residual identity (reporting only; no performance rewrite)."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[3] / "results" / "research" / "e1_x33c_baseline_economics"


def _fix_weighting(report: dict) -> dict:
    w = report.get("weighting") or {}
    fixed = 0
    for H in (60, 180, 300, 600, 900):
        drag_k = f"drag_{H}"
        spr_k = f"spread_only_drag_{H}"
        res_k = f"residual_drag_{H}"
        if drag_k not in w or spr_k not in w:
            continue
        for mode in (
            "episode_weighted",
            "symbol_session_balanced",
            "day_balanced",
            "symbol_day_balanced",
        ):
            d = (w[drag_k] or {}).get(mode)
            s = (w[spr_k] or {}).get(mode)
            if d is None or s is None:
                continue
            correct = float(d) + float(s)
            old = (w.get(res_k) or {}).get(mode)
            if res_k not in w:
                w[res_k] = {}
            w[res_k][mode] = correct
            if old is None or abs(float(old) - correct) > 1e-9:
                fixed += 1
    report["weighting"] = w
    report["residual_identity"] = {
        "formula": "RESIDUAL = EXECUTION_DRAG + SPREAD_MAGNITUDE",
        "execution_drag_formula": "EXECUTION_DRAG = EXEC_RETURN - MID_RETURN",
        "weighting_residual_patched": True,
        "cells_patched": fixed,
        "top_level_residual": report.get("residual_execution_drag"),
        "note": "performance mid/exec/drag/spread magnitudes unchanged; residual sign display only",
    }
    # verify top-level
    drag = report.get("execution_drag") or {}
    spr = report.get("spread_only_drag") or {}
    res = report.get("residual_execution_drag") or {}
    checks = {}
    for H in ("300", "600"):
        if H in drag and H in spr and H in res:
            checks[H] = abs(float(res[H]) - (float(drag[H]) + float(spr[H]))) < 1e-6
    report["residual_identity"]["top_level_ok"] = all(checks.values()) and bool(checks)
    report["residual_identity"]["top_level_checks"] = checks
    return report


def main() -> None:
    path = OUT / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    # assert performance pins unchanged intent
    em = report["episode_mean"]
    assert abs(em["exec600"] - (-4.960955144850852)) < 1e-9
    assert abs(em["mid600"] - 9.932008087474399) < 1e-9
    report = _fix_weighting(report)
    assert report["residual_identity"]["top_level_ok"], report["residual_identity"]
    # verify weighting identity after patch
    w = report["weighting"]
    for mode in ("episode_weighted", "symbol_session_balanced", "day_balanced"):
        d = w["drag_600"][mode]
        s = w["spread_only_drag_600"][mode]
        r = w["residual_drag_600"][mode]
        assert abs(r - (d + s)) < 1e-6, (mode, d, s, r)
        assert abs(w["exec_600"][mode] - (w["mid_600"][mode] + d)) < 1e-6
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    # refresh md note
    md = OUT / "report.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        if "residual_identity patched" not in text:
            text += "\n- residual_identity patched: weighting RESIDUAL = DRAG + SPREAD_MAG\n"
            md.write_text(text, encoding="utf-8")
    print("X33C residual weighting patched", report["residual_identity"])


if __name__ == "__main__":
    main()

"""Render prior-60 daily bars plus machine zones. No future intraday outcome on the definition chart."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from research.support_resistance_test_design_audit_v1 import NEAR_ATR
from research.support_resistance_test_design_audit_v1.isolation import OUT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _candle(ax, i: int, o: float, h: float, l: float, c: float) -> None:
    up = c >= o
    color = "#2E7D32" if up else "#C62828"
    ax.plot([i, i], [l, h], color="#333333", lw=0.7, zorder=2)
    body = max(abs(c - o), (h - l) * 0.02 if _finite(h) and _finite(l) else 1e-6)
    ax.add_patch(
        Rectangle((i - 0.32, min(o, c)), 0.64, body, facecolor=color, edgecolor=color, lw=0, zorder=3)
    )


def _face_heuristic(row: dict[str, Any], snap: dict[str, Any], atr: float, pdc: float | None) -> dict[str, Any]:
    res = list(snap.get("resistance_active") or [])
    sup = list(snap.get("support_active") or [])
    near_res = int(row.get("n_res_near_2atr") or 0)
    near_sup = int(row.get("n_sup_near_2atr") or 0)
    clutter = bool(row.get("clutter")) or (len(res) + len(sup) > 6)
    nearest_ok = _finite(row.get("nearest_res_above_open_atr")) and float(row["nearest_res_above_open_atr"]) <= NEAR_ATR
    obvious = (not clutter) and near_res <= 3 and near_sup <= 3 and (near_res + near_sup) >= 1
    return {
        "clutter": clutter,
        "nearby_res": near_res,
        "nearby_sup": near_sup,
        "nearest_overhead_within_2atr": bool(nearest_ok),
        "would_look_like_obvious_sr_before_the_day": bool(obvious),
        "heuristic_note": (
            "Obvious only if a small nearby set exists without clutter. "
            "This is a chart-density heuristic, not PnL."
        ),
    }


def render_sample(sample_id: int, pack: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    row = dict(pack["row"])
    snap = pack["snap"]
    hist = list(pack["hist"] or [])
    atr = pack.get("atr")
    fig, ax = plt.subplots(figsize=(11.2, 5.2), dpi=110)
    n = len(hist)
    for i, d in enumerate(hist):
        _candle(ax, i, float(d["open"]), float(d["high"]), float(d["low"]), float(d["close"]))
    pdc = float(hist[-1]["close"]) if hist else None
    res = list(snap.get("resistance_active") or [])
    sup = list(snap.get("support_active") or [])
    for z in res:
        ax.axhspan(float(z["lo"]), float(z["hi"]), color="#C62828", alpha=0.12, zorder=1)
        ax.axhline(float(z["center"]), color="#C62828", lw=0.7, alpha=0.7, zorder=1)
    for z in sup:
        ax.axhspan(float(z["lo"]), float(z["hi"]), color="#1565C0", alpha=0.12, zorder=1)
        ax.axhline(float(z["center"]), color="#1565C0", lw=0.7, alpha=0.7, zorder=1)
    ax.set_xlim(-1, max(n, 1))
    ax.set_title(
        f"{row['symbol']}  session={row['date']}  {row.get('block')}  {row.get('sector') or ''}  "
        f"vol={row.get('vol_regime')}  res={len(res)} sup={len(sup)}  "
        f"(prior 60 daily bars only; no session outcome)",
        fontsize=9,
    )
    ax.set_xlabel("prior completed sessions (oldest → last close before sample day)")
    ax.set_ylabel("price")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    rel = f"charts/{int(sample_id):03d}_{row['symbol']}_{row['date']}.png"
    path = out_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    hv = _face_heuristic(row, snap, atr, pdc)
    # reaction / activation index (no future session)
    rx_rows = []
    for z in res + sup:
        rx_rows.append(
            {
                "sample_id": sample_id,
                "symbol": row["symbol"],
                "date": row["date"],
                "role": z.get("role"),
                "center": z.get("center"),
                "lo": z.get("lo"),
                "hi": z.get("hi"),
                "half_width": z.get("half_width"),
                "touch_count": z.get("touch_count"),
                "distinct_touch_days": z.get("distinct_touch_days"),
                "first_reaction_date": z.get("first_reaction_date"),
                "last_reaction_date": z.get("last_reaction_date"),
                "ZONE_ACTIVATED_AT": z.get("ZONE_ACTIVATED_AT"),
                "touch_dates": ",".join(sorted({str(m.get("reaction_date") or "") for m in list(z.get("members") or [])})),
            }
        )
    return {
        "sample_id": sample_id,
        "symbol": row["symbol"],
        "date": row["date"],
        "block": row.get("block"),
        "sector": row.get("sector"),
        "vol_regime": row.get("vol_regime"),
        "atr": atr if _finite(atr) else row.get("atr"),
        "n_res_active": len(res),
        "n_sup_active": len(sup),
        "n_res_near_2atr": row.get("n_res_near_2atr"),
        "n_sup_near_2atr": row.get("n_sup_near_2atr"),
        "n_res_stale_3atr": row.get("n_res_stale_3atr"),
        "n_sup_stale_3atr": row.get("n_sup_stale_3atr"),
        "nearest_res_above_open_atr": row.get("nearest_res_above_open_atr"),
        "nearest_sup_below_open_atr": row.get("nearest_sup_below_open_atr"),
        "res_material_overlap_pairs": row.get("res_material_overlap_pairs"),
        "sup_material_overlap_pairs": row.get("sup_material_overlap_pairs"),
        "chart_path": rel.replace("\\", "/"),
        "future_intraday_shown": False,
        **hv,
        "zone_index": rx_rows,
    }


def render_all(sample_snaps: dict[tuple[str, str], dict[str, Any]], sample_keys: list[tuple[str, str]]) -> dict[str, Any]:
    out_dir = OUT
    index = []
    zone_index = []
    missing = 0
    for i, key in enumerate(sample_keys, start=1):
        pack = sample_snaps.get(key)
        if not pack:
            missing += 1
            continue
        rec = render_sample(i, pack, out_dir)
        zone_index.extend(rec.pop("zone_index"))
        index.append(rec)
        if i % 40 == 0:
            print(f"CHART {i}/{len(sample_keys)}", flush=True)
    n = len(index)
    obvious_n = sum(1 for r in index if r.get("would_look_like_obvious_sr_before_the_day"))
    clutter_n = sum(1 for r in index if r.get("clutter"))
    return {
        "n": n,
        "missing": missing,
        "obvious_n": obvious_n,
        "obvious_rate": (obvious_n / n) if n else None,
        "clutter_n": clutter_n,
        "clutter_rate": (clutter_n / n) if n else None,
        "blocks": {b: sum(1 for r in index if r.get("block") == b) for b in ("D1", "D2", "D3", "D4")},
        "vol_regimes": {
            k: sum(1 for r in index if r.get("vol_regime") == k) for k in ("low", "mid", "high", "unknown")
        },
        "sector_n": len({str(r.get("sector") or "") for r in index}),
        "index": index,
        "zone_index": zone_index,
        "future_intraday_shown": False,
        "face_valid_overall": bool(n >= 200 and (obvious_n / n) >= 0.60 and (clutter_n / n) <= 0.35),
    }

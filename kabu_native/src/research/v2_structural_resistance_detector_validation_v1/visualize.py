"""Render real PUSH charts for visual validation. Embed via openpyxl; delete temp PNGs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render_chart(item: dict[str, Any], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    t = item["t"]
    px = item["px"]
    ax.plot(t, px, color="black", lw=0.8, label="CurrentPrice")
    if item.get("bt"):
        ax.plot(item["bt"], item["bid"], color="tab:blue", lw=0.55, label="Bid", alpha=0.85)
        ax.plot(item["bt"], item["ask"], color="tab:orange", lw=0.55, label="Ask", alpha=0.85)

    structure = item.get("structure") or {}
    # Recognized swing highs
    for s in structure.get("swings") or []:
        if s.get("kind") != "HIGH":
            continue
        st = float(s["recognized_at"])
        if st < t[0] or st > t[-1]:
            continue
        ax.scatter([s["source_t"]], [s["price"]], marker="v", color="crimson", s=28, zorder=5)
        ax.axvline(st, color="crimson", lw=0.4, alpha=0.35)

    # Detected zones
    for z in structure.get("zones") or []:
        ft = float(z["first_seen_t"])
        if ft > t[-1]:
            continue
        color = {
            "UPPER_REJECTION_ZONE": "red",
            "CONSOLIDATION_ZONE": "gray",
            "SINGLE_SWING_HIGH": "purple",
            "UNRESOLVED_ZONE": "olive",
        }.get(z["zone_type"], "pink")
        ax.axhspan(float(z["zone_low"]), float(z["zone_high"]), color=color, alpha=0.12, zorder=0)
        ax.axhline(float(z["zone_center"]), color=color, lw=0.6, ls=":", alpha=0.8)

    # PRE_BREAK_HIGH and ENTRY
    if item.get("pre_break") is not None:
        ax.axhline(float(item["pre_break"]), color="red", ls="--", lw=0.9, label="V2 PRE_BREAK_HIGH")
    ax.axhline(float(item["entry_px"]), color="green", ls="--", lw=0.8, label="ENTRY_ASK")
    ax.axvline(float(item["entry_t"]), color="green", lw=0.9, label="ENTRY")
    ax.axvline(float(item["exit_t"]), color="purple", lw=0.9, label="EXIT")

    # Next resistance
    nxt = (structure.get("next_resistance") or {})
    if nxt.get("status") == "KNOWN" and nxt.get("level") is not None:
        ax.axhline(float(nxt["level"]), color="navy", ls="-.", lw=0.8, label="NEXT_RESISTANCE")

    # Runtime break / retest markers for matched zone
    rt = structure.get("runtime") or {}
    anat = rt.get("anatomy") or {}
    if anat.get("first_trade_above_t") is not None:
        ax.axvline(float(anat["first_trade_above_t"]), color="darkorange", lw=0.8, ls=":", label="BREAK")
    if rt.get("retest_t") is not None:
        ax.axvline(float(rt["retest_t"]), color="teal", lw=0.8, ls=":", label="RETEST")
    if rt.get("support_confirm_t") is not None:
        ax.axvline(float(rt["support_confirm_t"]), color="darkgreen", lw=0.8, ls=":", label="SUPPORT_CONF")
    if rt.get("failed_t") is not None:
        ax.axvline(float(rt["failed_t"]), color="brown", lw=0.8, ls=":", label="FAILED_BO")

    for ev in item.get("ratchet_events") or []:
        ax.axvline(float(ev["t"]), color="magenta", lw=0.6, alpha=0.7)
        ax.axhline(float(ev["support"]), color="magenta", lw=0.4, alpha=0.5)

    matched = "Y" if structure.get("pre_break_in_structural_zone") else "N"
    ztype = (structure.get("matched_zone") or {}).get("zone_type") or "none"
    title = (
        f"{item['kind']} {item['symbol']} {item['date']} {item['reason']} "
        f"pnl={item['pnl_yen']:.0f} r={item['ratchets']} PB_in_zone={matched} type={ztype}"
    )
    ax.set_title(title, fontsize=9)
    ax.legend(fontsize=6, loc="best", ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)

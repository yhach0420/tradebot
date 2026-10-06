"""Market-language mechanism. No Full Causal economics. No numeric freeze."""
from __future__ import annotations

from typing import Any

from research.post_open_causal_upside_mechanism_discovery_v1 import CASE_FOUND, NEXT_FOUND


def interpret(*, uni: list[dict[str, Any]], tree: dict[str, Any], inter: list[dict[str, Any]], found: bool) -> dict[str, Any]:
    stable = [u for u in uni if u.get("mechanism_candidate")]
    names = [u["feature"] for u in stable]
    families = {
        "price": any(x.startswith("PRICE_") or x.startswith("TRADE_RANGE") for x in names),
        "participation": any("VOLUME" in x or "VALUE" in x or "TRADE_N" in x for x in names),
        "depth": any("DEPTH" in x or x.startswith("L1_") or x in ("BID1_QTY", "ASK1_QTY") for x in names),
        "quote": any("UPDATE_N" in x for x in names),
        "market": any(x.startswith("UNIVERSE_") for x in names),
        "calc": any(x.startswith("CALCPRICE") for x in names),
        "under_over": any(x.startswith("UNDER") or x.startswith("OVER") for x in names),
        "prevclose": any("PREVIOUS_CLOSE" in x for x in names),
        "spread": "SPREAD_BPS" in names,
    }
    best = inter[0] if inter else {}
    if not found:
        story = (
            "Post-open 09:10-11:10の中立clockでは、Ask1で買える時点のあと10分は、"
            "母集団としてexecutable Bid markoutがマイナスである。"
            "スプレッドが狭い、quote更新が多い、実約定代金・件数が多い状態では、"
            "上昇継続率は母集団より改善するが、Ask1→10分後Bid1のmarkoutは平均も中央値もプラスにならない。"
            "depth-3 treeの全leafはclass=DOWNであり、qualifying leafは0である。"
            "市場全体のbreadth、前日終値距離、CalcPriceは安定univariateに入らない。"
            "既存Captureのcausal情報だけでは、説明可能で再現可能なpost-open upside stateを構成できない。"
        )
        return {
            "MARKET_MECHANISM": story,
            "ENTRY_THESIS": None,
            "STATE_TRANSITION": None,
            "THESIS_FAILURE": None,
            "TECHNICAL_EXIT": None,
            "EXACT_CLOSED_DUPLICATE": False,
            "COMPLETE_STRATEGY_PRECOMMITTED": False,
            "stable_features": names,
            "family_useful": families,
            "strongest_interaction": best,
        }
    parts = []
    if families["market"]:
        parts.append("市場全体が同時に上向き")
    if families["participation"]:
        parts.append("その銘柄の実約定参加が増えている")
    if families["depth"]:
        parts.append("上側売りdepthが相対的に薄い、またはL1が買いに偏る")
    if families["price"]:
        parts.append("直前の実約定パス自体が上方向")
    if families["quote"]:
        parts.append("quote更新が片側に偏る")
    story = (
        "上昇が続くのは、単に直前価格が上がっている時ではない。"
        + "、".join(parts)
        + "状態のときに限り、10分のpath-edgeとexecutable markoutがともに改善する。"
        if parts
        else "安定leafは存在するが、家族の寄与が薄い。"
    )
    return {
        "MARKET_MECHANISM": story,
        "ENTRY_THESIS": (
            "Neutral clockではない。次runで、このstateが観測された連続セッションの "
            "fresh Ask1 を ENTRY とする。数値thresholdは本runではfreezeしない。"
        ),
        "STATE_TRANSITION": "pre-T causal features enter the qualifying tree leaf / interaction cell.",
        "THESIS_FAILURE": "path-edge turns negative or Bid1 recrosses below the entry Ask1 state that defined the upside.",
        "TECHNICAL_EXIT": (
            "ENTRY-aligned: first Observed Trade (or first causal Bid1) that invalidates the "
            "upside state — typically Bid1 below the entry Ask1 after the continuation fails. "
            "Not the 10m research window."
        ),
        "EXACT_CLOSED_DUPLICATE": False,
        "COMPLETE_STRATEGY_PRECOMMITTED": False,
        "NEXT_ANALYSIS": NEXT_FOUND,
        "VERDICT_IF_USED": CASE_FOUND,
        "stable_features": names,
        "family_useful": families,
        "tree_rules": tree.get("rules") if tree.get("ok") else None,
        "strongest_interaction": best,
    }

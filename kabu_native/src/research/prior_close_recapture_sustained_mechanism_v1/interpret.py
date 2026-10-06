"""Market-language mechanism and optional next-strategy spec. No economics."""
from __future__ import annotations

from typing import Any

from research.prior_close_recapture_sustained_mechanism_v1 import (
    CASE_FOUND,
    FEATURE_IDS,
    LABEL_FAILED,
    LABEL_SUSTAINED,
    NEXT_FOUND,
    PARENT_STRATEGY_ID,
)

WORDS = {
    "MAX_DEPTH_BELOW_PCLOSE_BPS": "下側に沈んだ実約定の深さ（bps）",
    "MAX_DEPTH_BELOW_PCLOSE_TICKS": "下側に沈んだ実約定の深さ（tick）",
    "BELOW_DWELL_SEC": "below開始からrecaptureまでの滞在時間",
    "BELOW_OTU_N": "below区間の実約定回数",
    "BELOW_DISTINCT_PRICE_N": "below区間で約定した価格level数",
    "BELOW_TRADING_VOLUME_DELTA": "below開始からrecaptureまでのTradingVolume増分",
    "BELOW_TRADING_VALUE_DELTA": "below開始からrecaptureまでのTradingValue増分",
    "RECAPTURE_OVERSHOOT_BPS": "recaptureがPreviousCloseを超えた幅（bps）",
    "RECAPTURE_OVERSHOOT_TICKS": "recaptureがPreviousCloseを超えた幅（tick）",
    "PRIOR_RECAPTURE_N": "同日同銘柄の先行recapture回数",
    "TIME_SINCE_PREVIOUS_RECAPTURE_SEC": "前回recaptureからの経過秒",
    "SPREAD_BPS": "recapture時点のfresh Bid1/Ask1スプレッド",
    "BID1_QTY": "recapture時点のfresh Bid1数量",
    "ASK1_QTY": "recapture時点のfresh Ask1数量",
    "CALCPRICE_MINUS_CURRENT": "CalcPrice − CurrentPrice（診断）",
    "CALCPRICE_MINUS_PCLOSE": "CalcPrice − PreviousClose（診断）",
    "UNDER_BUY_QTY": "UnderBuyQty（診断）",
    "OVER_SELL_QTY": "OverSellQty（診断）",
    "UNDER_MINUS_OVER": "UnderBuyQty − OverSellQty（診断）",
    "MINUTES_FROM_0900": "09:00からの経過分",
}


def _dir_clause(row: dict[str, Any]) -> str:
    name = WORDS.get(str(row.get("feature")), str(row.get("feature")))
    if row.get("direction") == "higher_in_sustained":
        return f"{name}が大きいほどSUSTAINEDが多い"
    if row.get("direction") == "lower_in_sustained":
        return f"{name}が小さいほどSUSTAINEDが多い"
    return f"{name}は方向が安定しない"


def interpret(*, uni: list[dict[str, Any]], tree: dict[str, Any], found: bool) -> dict[str, Any]:
    stable = [u for u in uni if u.get("mechanism_candidate")]
    story_lines = [_dir_clause(u) for u in stable[:5]]
    if not found or not stable:
        market = (
            "Failed recapture is the default after an X1 fill. Most prints that recapture "
            "PreviousClose later print back below it. ENTRY-time directions exist: longer below dwell, "
            "longer gap since a prior recapture, slightly larger overshoot, thinner Under/Over qty. "
            "Several of those directions hold across B1-B5. They still do not isolate a success pocket: "
            "even the best quartile remains mostly FAILED, and a depth-2 tree labels every leaf FAILED. "
            "That is not an ENTRY rule. PreviousClose recapture is information exhausted as a Complete Strategy family."
        )
        return {
            "MARKET_MECHANISM": market,
            "STABLE_FEATURE_SENTENCES": story_lines,
            "EXISTING_CLOSED_STRATEGY_DUPLICATE": False,
            "ENTRY_CAUSAL_AT_SIGNAL": True,
            "EXIT_FROM_SAME_THESIS": True,
            "COMPLETE_STRATEGY_PRECOMMITTED": False,
            "NEXT_STRATEGY": None,
        }
    market = (
        "Successful recaptureは、PreviousCloseを瞬間的に上抜けしただけでは足りない。"
        + " ".join(story_lines)
        + " Repeated failure / 浅い跨ぎはFAILED側に寄る。"
        + " これはV5の『belowのあと最初の上抜けを無条件ENTRY』とは異なる状態説明である。"
    )
    tree_rule = str(tree.get("rules") or "").strip() if tree.get("ok") else None
    spec = {
        "ANALYSIS_ID_NEXT": NEXT_FOUND,
        "KIND": "NEW_MECHANISM_FROM_RECAPTURE_SUCCESS_NOT_V5_RESCUE",
        "PARENT_CLOSED": PARENT_STRATEGY_ID,
        "ENTRY_THESIS": (
            "Continuous AM Observed Tradeが凍結PreviousCloseを下回ったあと、"
            "同episodeでCurrentPrice > PreviousCloseとなるrecapture OTU自体をENTRY候補とし、"
            "そのINGRESSまでに観測された安定特徴がSUSTAINED側の状態であること。"
        ),
        "ENTRY_RULE": tree_rule or "See stable univariate directions; freeze exact numeric rule in the next Full Causal analysis before economics.",
        "EXIT_THESIS": (
            "recaptureが『前日終値を実約定で取り戻し維持する』ことなので、"
            "fill後の最初のObserved Trade CurrentPrice < PreviousCloseでthesis失敗。"
            "EXIT fireはそのOTU。EXIT fillは最初のcausal Bid1。synthetic Bid禁止。"
        ),
        "EXIT_ID": "Z_PRIOR_CLOSE_LOSS",
        "EXECUTION": "X1_IMMEDIATE_ASK",
        "CAP": 5,
        "SHARES": 100,
        "SESSION": "AM",
        "FLATTEN": [11, 29],
        "NOT_V5_V2": True,
        "NOT_THRESHOLD_RESCUE": True,
        "STABLE_FEATURES": [u["feature"] for u in stable],
        "FEATURE_FAMILY_MAX": list(FEATURE_IDS),
    }
    return {
        "MARKET_MECHANISM": market,
        "STABLE_FEATURE_SENTENCES": story_lines,
        "EXISTING_CLOSED_STRATEGY_DUPLICATE": False,
        "ENTRY_CAUSAL_AT_SIGNAL": True,
        "EXIT_FROM_SAME_THESIS": True,
        "COMPLETE_STRATEGY_PRECOMMITTED": True,
        "NEXT_STRATEGY": spec,
        "TREE_RULE": tree_rule,
        "VERDICT_IF_USED": CASE_FOUND,
        "LABELS": {"success": LABEL_SUSTAINED, "fail": LABEL_FAILED},
    }

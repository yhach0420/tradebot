"""Fixed Day-Trade Universe candidates. Liquidity/structure only. No outcome."""
from __future__ import annotations

from typing import Any

from research.fixed_universe_historical_foundation_v1 import (
    CLAIM_RETROSPECTIVE_UNIVERSE_SELECTABILITY,
    PANEL_CONDITIONED,
    RECOMMENDED_STOCK_N,
    TARGET_UNIVERSE_MAX,
    TARGET_UNIVERSE_MIN,
    UNIVERSE_FROZEN,
    UNIVERSE_REVIEW_CADENCE,
)

# Selection uses structure/liquidity class labels known as of 2026-09.
# Not computed from historical PnL, future return, paper win rate, or strategy score.
# Not a point-in-time 2025 universe. PANEL-CONDITIONED HISTORICAL RESEARCH.
FORBIDDEN_SELECTION_INPUTS = (
    "historical_pnl",
    "future_return",
    "paper_win_rate",
    "past_strategy_score",
)
ALLOWED_SELECTION_INPUTS = (
    "liquidity",
    "trading_value",
    "trade_frequency",
    "spread_if_available",
    "price_range",
    "data_completeness",
    "listing_continuity",
    "sector_coverage",
)

REQUIRED_SECTOR_BUCKETS = (
    "semiconductor_electronics",
    "auto",
    "machinery",
    "banks",
    "trading_companies",
    "energy_resources",
    "transportation",
    "chemicals",
    "real_estate",
    "retail",
    "pharma",
    "telecom",
    "other_major",
)

# symbol, name_en, name_ja, sector_bucket, tse33, liquidity_class, price_range_class
# liquidity_class is a qualitative TSE high-turnover / Core30-adjacent label as of 2026-09,
# not a downloaded ranking. Freeze step must verify with official daily trading value.
CANDIDATES: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("6857", "Advantest", "アドバンテスト", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "HIGH"),
    ("8035", "Tokyo Electron", "東京エレクトロン", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "HIGH"),
    ("6920", "Lasertec", "レーザーテック", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "HIGH"),
    ("6723", "Renesas", "ルネサスエレクトロニクス", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "STANDARD"),
    ("6981", "Murata", "村田製作所", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "STANDARD"),
    ("6758", "Sony Group", "ソニーグループ", "semiconductor_electronics", "電気機器", "CORE_LIQUID", "STANDARD"),
    ("7203", "Toyota", "トヨタ自動車", "auto", "輸送用機器", "CORE_LIQUID", "STANDARD"),
    ("7267", "Honda", "本田技研工業", "auto", "輸送用機器", "CORE_LIQUID", "STANDARD"),
    ("7269", "Suzuki", "スズキ", "auto", "輸送用機器", "CORE_LIQUID", "STANDARD"),
    ("6902", "Denso", "デンソー", "auto", "輸送用機器", "CORE_LIQUID", "STANDARD"),
    ("7011", "Mitsubishi Heavy", "三菱重工業", "machinery", "機械", "CORE_LIQUID", "STANDARD"),
    ("6301", "Komatsu", "小松製作所", "machinery", "機械", "CORE_LIQUID", "STANDARD"),
    ("6367", "Daikin", "ダイキン工業", "machinery", "機械", "CORE_LIQUID", "HIGH"),
    ("8306", "MUFG", "三菱UFJフィナンシャル・グループ", "banks", "銀行業", "CORE_LIQUID", "STANDARD"),
    ("8316", "SMFG", "三井住友フィナンシャルグループ", "banks", "銀行業", "CORE_LIQUID", "STANDARD"),
    ("8411", "Mizuho", "みずほフィナンシャルグループ", "banks", "銀行業", "CORE_LIQUID", "STANDARD"),
    ("8058", "Mitsubishi Corp", "三菱商事", "trading_companies", "卸売業", "CORE_LIQUID", "STANDARD"),
    ("8001", "Itochu", "伊藤忠商事", "trading_companies", "卸売業", "CORE_LIQUID", "STANDARD"),
    ("8031", "Mitsui", "三井物産", "trading_companies", "卸売業", "CORE_LIQUID", "STANDARD"),
    ("8002", "Marubeni", "丸紅", "trading_companies", "卸売業", "CORE_LIQUID", "STANDARD"),
    ("1605", "INPEX", "INPEX", "energy_resources", "鉱業", "CORE_LIQUID", "STANDARD"),
    ("5020", "ENEOS", "ENEOSホールディングス", "energy_resources", "石油・石炭製品", "CORE_LIQUID", "STANDARD"),
    ("5401", "Nippon Steel", "日本製鉄", "energy_resources", "鉄鋼", "CORE_LIQUID", "STANDARD"),
    ("9020", "JR East", "東日本旅客鉄道", "transportation", "陸運業", "CORE_LIQUID", "STANDARD"),
    ("9104", "MOL", "商船三井", "transportation", "海運業", "CORE_LIQUID", "STANDARD"),
    ("9101", "NYK", "日本郵船", "transportation", "海運業", "CORE_LIQUID", "STANDARD"),
    ("4063", "Shin-Etsu Chemical", "信越化学工業", "chemicals", "化学", "CORE_LIQUID", "HIGH"),
    ("4188", "Mitsubishi Chemical", "三菱ケミカルグループ", "chemicals", "化学", "CORE_LIQUID", "STANDARD"),
    ("4901", "Fujifilm", "富士フイルムホールディングス", "chemicals", "化学", "CORE_LIQUID", "STANDARD"),
    ("8801", "Mitsui Fudosan", "三井不動産", "real_estate", "不動産業", "CORE_LIQUID", "STANDARD"),
    ("8802", "Mitsubishi Estate", "三菱地所", "real_estate", "不動産業", "CORE_LIQUID", "STANDARD"),
    ("8830", "Sumitomo Realty", "住友不動産", "real_estate", "不動産業", "CORE_LIQUID", "STANDARD"),
    ("3382", "Seven & i", "セブン&アイ・ホールディングス", "retail", "小売業", "CORE_LIQUID", "STANDARD"),
    ("8267", "Aeon", "イオン", "retail", "小売業", "CORE_LIQUID", "STANDARD"),
    ("9843", "Nitori", "ニトリホールディングス", "retail", "小売業", "CORE_LIQUID", "STANDARD"),
    ("4502", "Takeda", "武田薬品工業", "pharma", "医薬品", "CORE_LIQUID", "STANDARD"),
    ("4568", "Daiichi Sankyo", "第一三共", "pharma", "医薬品", "CORE_LIQUID", "STANDARD"),
    ("4519", "Chugai", "中外製薬", "pharma", "医薬品", "CORE_LIQUID", "HIGH"),
    ("9432", "NTT", "日本電信電話", "telecom", "情報・通信業", "CORE_LIQUID", "STANDARD"),
    ("9433", "KDDI", "KDDI", "telecom", "情報・通信業", "CORE_LIQUID", "STANDARD"),
    ("9434", "SoftBank Corp", "ソフトバンク", "telecom", "情報・通信業", "CORE_LIQUID", "STANDARD"),
    ("7974", "Nintendo", "任天堂", "other_major", "その他製品", "CORE_LIQUID", "HIGH"),
    ("6098", "Recruit", "リクルートホールディングス", "other_major", "サービス業", "CORE_LIQUID", "STANDARD"),
    ("2914", "JT", "日本たばこ産業", "other_major", "食料品", "CORE_LIQUID", "STANDARD"),
    ("9984", "SoftBank Group", "ソフトバンクグループ", "other_major", "情報・通信業", "CORE_LIQUID", "STANDARD"),
)

EXCLUDED_LOT_SIZE_BLOCKERS = (
    ("9983", "Fast Retailing", "lot_notional_typically_too_large_for_100_share_daytrade"),
    ("6861", "Keyence", "lot_notional_typically_too_large_for_100_share_daytrade"),
)

SECTOR_PROXIES_NOT_IN_UNIVERSE = (
    ("1306", "TOPIX ETF", "index_proxy"),
    ("1321", "Nikkei 225 ETF", "index_proxy"),
    ("1570", "Nikkei 225 leveraged ETF", "optional_index_proxy_not_default"),
)

MID_UNIVERSE_REMOVAL_ALLOWED = (
    "delisting",
    "structural_liquidity_collapse",
    "long_suspension",
    "corporate_reorganization",
)
MID_UNIVERSE_REMOVAL_FORBIDDEN = (
    "research_loss",
    "negative_paper_pnl",
    "low_strategy_score",
)


def candidate_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, (code, en, ja, bucket, tse33, liq, px) in enumerate(CANDIDATES, start=1):
        rows.append(
            {
                "rank_in_candidate_list": i,
                "symbol": code,
                "symbol_yahoo": f"{code}.T",
                "name_en": en,
                "name_ja": ja,
                "sector_bucket": bucket,
                "tse33": tse33,
                "liquidity_class": liq,
                "price_range_class": px,
                "listing_continuity_pre_202309": True,
                "etf": False,
                "common_stock": True,
                "outcome_used": False,
                "historical_pnl_used": False,
                "future_return_used": False,
                "paper_win_rate_used": False,
                "strategy_score_used": False,
                "panel_conditioned": True,
                "frozen": False,
                "selection_reason": (
                    "qualitative_high_tse_trading_value_and_listing_continuity;"
                    "sector_coverage_without_breaking_liquidity;"
                    "price_range_flag_only"
                ),
            }
        )
    return rows


def sector_coverage() -> list[dict[str, Any]]:
    rows = candidate_rows()
    out: list[dict[str, Any]] = []
    for bucket in REQUIRED_SECTOR_BUCKETS:
        names = [r["symbol"] for r in rows if r["sector_bucket"] == bucket]
        out.append(
            {
                "sector_bucket": bucket,
                "n": len(names),
                "symbols": ",".join(names),
                "required": True,
                "equal_weight_required": False,
                "liquidity_broken_for_balance": False,
                "covered": len(names) > 0,
            }
        )
    return out


def methodology() -> dict[str, Any]:
    n = len(CANDIDATES)
    buckets = {r[3] for r in CANDIDATES}
    return {
        "APPROACH_VIABLE": True,
        "RECOMMENDED_STOCK_N": int(RECOMMENDED_STOCK_N),
        "CANDIDATE_N": n,
        "TARGET_MIN": int(TARGET_UNIVERSE_MIN),
        "TARGET_MAX": int(TARGET_UNIVERSE_MAX),
        "FROZEN": bool(UNIVERSE_FROZEN),
        "DYNAMIC_UNIVERSE_NOT_PREMISE": True,
        "PANEL_CONDITIONED_HISTORICAL_RESEARCH": bool(PANEL_CONDITIONED),
        "CLAIM_ONE_YEAR_AGO_SAME_UNIVERSE": bool(CLAIM_RETROSPECTIVE_UNIVERSE_SELECTABILITY),
        "ALLOWED_INPUTS": list(ALLOWED_SELECTION_INPUTS),
        "FORBIDDEN_INPUTS": list(FORBIDDEN_SELECTION_INPUTS),
        "SECTOR_BUCKETS_COVERED": sorted(buckets),
        "REQUIRED_SECTORS_ALL_COVERED": set(REQUIRED_SECTOR_BUCKETS) <= buckets,
        "EQUAL_SECTOR_WEIGHT_REQUIRED": False,
        "LIQUIDITY_NOT_BROKEN_FOR_SECTOR_BALANCE": True,
        "EXCLUDED_LOT_SIZE_BLOCKERS": [x[0] for x in EXCLUDED_LOT_SIZE_BLOCKERS],
        "SECTOR_PROXIES_EXCLUDED_FROM_STOCK_N": [x[0] for x in SECTOR_PROXIES_NOT_IN_UNIVERSE],
        "REVIEW_CADENCE": list(UNIVERSE_REVIEW_CADENCE),
        "MID_REVIEW_REMOVAL_ALLOWED": list(MID_UNIVERSE_REMOVAL_ALLOWED),
        "MID_REVIEW_REMOVAL_FORBIDDEN": list(MID_UNIVERSE_REMOVAL_FORBIDDEN),
        "FREEZE_STEP_MUST_VERIFY": (
            "J-Quants daily Va/Vo over trailing ~60 sessions; "
            "spread if available from live/paper board; "
            "listing continuity and minute completeness; "
            "do not use PnL"
        ),
        "INTERPRETATION": (
            "A 40-50 name Fixed Day-Trade Universe is viable as the first operational "
            "candidate. This candidate list is PANEL-CONDITIONED as of 2026-09. It is "
            "usable for prospective mechanism discovery after freeze. It does not claim "
            "the same 45 names would have been selected in 2025."
        ),
    }


assert len(CANDIDATES) == int(RECOMMENDED_STOCK_N)
assert len({c[0] for c in CANDIDATES}) == len(CANDIDATES)
assert set(REQUIRED_SECTOR_BUCKETS) <= {c[3] for c in CANDIDATES}
assert UNIVERSE_FROZEN is False
assert all(c[0] not in {x[0] for x in EXCLUDED_LOT_SIZE_BLOCKERS} for c in CANDIDATES)

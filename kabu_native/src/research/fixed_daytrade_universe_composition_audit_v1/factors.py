"""A priori external-driver hypothesis map. Not estimated from future returns or PnL."""
from __future__ import annotations

from typing import Any

# Role assignment is economic/structural as of 2026-09 listing + business mix.
# It is not a regression on USDJPY/ES/Oil (those series are not in this audit).
# Dual-role names are allowed; confounding is recorded.

FACTORS = (
    "USDJPY",
    "US_TECH_NQ",
    "GLOBAL_RISK_ES",
    "OIL",
    "JAPAN_RATES",
    "US_RATES",
    "CHINA_HK",
    "KOREA_TECH",
    "DOMESTIC_DEFENSIVE",
    "DOMESTIC_CONSUMPTION",
)

# symbol -> {factor: "pos"|"neg"|"mixed"}
ROLES: dict[str, dict[str, str]] = {
    "1605": {"OIL": "pos", "USDJPY": "mixed", "GLOBAL_RISK_ES": "pos", "CHINA_HK": "mixed"},
    "2914": {"DOMESTIC_DEFENSIVE": "pos", "USDJPY": "neg", "GLOBAL_RISK_ES": "neg", "US_TECH_NQ": "neg"},
    "3382": {"DOMESTIC_CONSUMPTION": "pos", "USDJPY": "neg", "CHINA_HK": "neg"},
    "4063": {"US_TECH_NQ": "pos", "CHINA_HK": "pos", "USDJPY": "pos", "OIL": "neg"},
    "4188": {"OIL": "neg", "CHINA_HK": "pos", "USDJPY": "mixed"},
    "4502": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "GLOBAL_RISK_ES": "neg"},
    "4519": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "GLOBAL_RISK_ES": "neg"},
    "4568": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "GLOBAL_RISK_ES": "neg"},
    "4901": {"CHINA_HK": "mixed", "USDJPY": "mixed", "DOMESTIC_CONSUMPTION": "mixed"},
    "5020": {"OIL": "mixed", "USDJPY": "neg", "DOMESTIC_CONSUMPTION": "mixed"},
    "5401": {"CHINA_HK": "pos", "OIL": "mixed", "GLOBAL_RISK_ES": "pos", "USDJPY": "pos"},
    "6098": {"DOMESTIC_CONSUMPTION": "pos", "CHINA_HK": "neg", "US_RATES": "neg"},
    "6301": {"CHINA_HK": "pos", "USDJPY": "pos", "GLOBAL_RISK_ES": "pos"},
    "6367": {"USDJPY": "pos", "CHINA_HK": "pos", "US_RATES": "neg"},
    "6723": {"US_TECH_NQ": "pos", "KOREA_TECH": "pos", "USDJPY": "pos", "US_RATES": "neg"},
    "6758": {"US_TECH_NQ": "pos", "USDJPY": "pos", "DOMESTIC_CONSUMPTION": "mixed", "US_RATES": "neg"},
    "6857": {"US_TECH_NQ": "pos", "KOREA_TECH": "pos", "US_RATES": "neg", "GLOBAL_RISK_ES": "pos"},
    "6902": {"USDJPY": "pos", "CHINA_HK": "pos", "OIL": "neg", "GLOBAL_RISK_ES": "pos"},
    "6920": {"US_TECH_NQ": "pos", "KOREA_TECH": "pos", "US_RATES": "neg"},
    "6981": {"US_TECH_NQ": "pos", "KOREA_TECH": "pos", "USDJPY": "pos", "US_RATES": "neg"},
    "7011": {"CHINA_HK": "pos", "USDJPY": "pos", "GLOBAL_RISK_ES": "pos"},
    "7203": {"USDJPY": "pos", "GLOBAL_RISK_ES": "pos", "CHINA_HK": "mixed", "US_RATES": "mixed"},
    "7267": {"USDJPY": "pos", "GLOBAL_RISK_ES": "pos", "CHINA_HK": "mixed"},
    "7269": {"USDJPY": "pos", "GLOBAL_RISK_ES": "pos", "CHINA_HK": "mixed"},
    "7974": {"USDJPY": "pos", "DOMESTIC_CONSUMPTION": "pos", "US_TECH_NQ": "mixed", "US_RATES": "neg"},
    "8001": {"CHINA_HK": "pos", "OIL": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "mixed"},
    "8002": {"CHINA_HK": "pos", "OIL": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "mixed"},
    "8031": {"CHINA_HK": "pos", "OIL": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "mixed"},
    "8035": {"US_TECH_NQ": "pos", "KOREA_TECH": "pos", "US_RATES": "neg", "GLOBAL_RISK_ES": "pos"},
    "8058": {"CHINA_HK": "pos", "OIL": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "mixed"},
    "8267": {"DOMESTIC_CONSUMPTION": "pos", "USDJPY": "neg", "CHINA_HK": "neg"},
    "8306": {"JAPAN_RATES": "pos", "US_RATES": "pos", "GLOBAL_RISK_ES": "mixed"},
    "8316": {"JAPAN_RATES": "pos", "US_RATES": "pos", "GLOBAL_RISK_ES": "mixed"},
    "8411": {"JAPAN_RATES": "pos", "US_RATES": "pos", "GLOBAL_RISK_ES": "mixed"},
    "8801": {"JAPAN_RATES": "neg", "US_RATES": "neg", "DOMESTIC_CONSUMPTION": "mixed"},
    "8802": {"JAPAN_RATES": "neg", "US_RATES": "neg", "DOMESTIC_CONSUMPTION": "mixed"},
    "8830": {"JAPAN_RATES": "neg", "US_RATES": "neg", "DOMESTIC_CONSUMPTION": "mixed"},
    "9020": {"OIL": "neg", "DOMESTIC_DEFENSIVE": "mixed", "USDJPY": "neg"},
    "9101": {"OIL": "neg", "CHINA_HK": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "pos"},
    "9104": {"OIL": "neg", "CHINA_HK": "pos", "GLOBAL_RISK_ES": "pos", "USDJPY": "pos"},
    "9432": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "JAPAN_RATES": "mixed"},
    "9433": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "JAPAN_RATES": "mixed"},
    "9434": {"DOMESTIC_DEFENSIVE": "pos", "US_TECH_NQ": "neg", "JAPAN_RATES": "mixed"},
    "9843": {"DOMESTIC_CONSUMPTION": "pos", "USDJPY": "neg", "CHINA_HK": "neg"},
    "9984": {"US_TECH_NQ": "pos", "GLOBAL_RISK_ES": "pos", "US_RATES": "neg", "USDJPY": "mixed"},
}

# Missing TSE33 roles: necessary = needed to identify a driver vs idiosyncratic cluster.
MISSING_ROLE_SPECS = (
    {
        "role": "insurance",
        "tse33": "保険業",
        "tse33_code": "7150",
        "factor": "JAPAN_RATES",
        "side": "pos",
        "necessary_for_driver_id": True,
        "why": "Three megabanks are one credit/NIM cluster. Insurance duration/investment income is a different rates channel.",
        "precommitted_candidates": ("8766",),
        "alternate_candidates": ("8750", "8725", "8630"),
    },
    {
        "role": "securities_nonbank",
        "tse33": "証券、商品先物取引業",
        "tse33_code": "7100",
        "factor": "GLOBAL_RISK_ES",
        "side": "pos",
        "necessary_for_driver_id": True,
        "why": "Separates equity-risk / brokerage flow from bank NIM. Banks alone confound rates vs risk-on.",
        "precommitted_candidates": ("8604",),
        "alternate_candidates": ("8601",),
    },
    {
        "role": "electric_power_gas",
        "tse33": "電気・ガス業",
        "tse33_code": "4050",
        "factor": "USDJPY",
        "side": "neg",
        "necessary_for_driver_id": True,
        "why": "Cleaner JPY/LNG fuel-cost sink than retailers or a refiner. Also a domestic defensive rates-sensitive utility.",
        "precommitted_candidates": ("9503", "9531"),
        "alternate_candidates": ("9501", "9502"),
    },
    {
        "role": "air_transport",
        "tse33": "空運業",
        "tse33_code": "5150",
        "factor": "OIL",
        "side": "neg",
        "necessary_for_driver_id": False,
        "why": "Shipping is oil-neg but also China/trade-pos. Air is a less China-confounded oil-cost sink. Useful, not strictly required.",
        "precommitted_candidates": ("9202",),
        "alternate_candidates": ("9201",),
    },
    {
        "role": "construction_materials",
        "tse33": "建設業",
        "tse33_code": "2050",
        "factor": "JAPAN_RATES",
        "side": "neg",
        "necessary_for_driver_id": False,
        "why": "RE developers already cover rates-neg housing duration. Construction adds public-works/China mix. Not required for rates identification.",
        "precommitted_candidates": ("1925",),
        "alternate_candidates": ("1802", "1801"),
    },
    {
        "role": "nonferrous_materials",
        "tse33": "非鉄金属",
        "tse33_code": "3500",
        "factor": "CHINA_HK",
        "side": "pos",
        "necessary_for_driver_id": False,
        "why": "Steel 5401 is a partial China/materials proxy. Copper/Ni is a cleaner China+EV materials sleeve. Useful, not required.",
        "precommitted_candidates": ("5713",),
        "alternate_candidates": ("5711", "5802"),
    },
)


def side_coverage(symbols: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for factor in FACTORS:
        pos = [s for s in symbols if ROLES.get(s, {}).get(factor) == "pos"]
        neg = [s for s in symbols if ROLES.get(s, {}).get(factor) == "neg"]
        mixed = [s for s in symbols if ROLES.get(s, {}).get(factor) == "mixed"]
        pos_n, neg_n = len(pos), len(neg)
        if pos_n >= 2 and neg_n >= 2:
            status = "BOTH_SIDES_ROBUST"
        elif pos_n >= 1 and neg_n >= 1:
            status = "BOTH_SIDES_THIN"
        elif pos_n >= 1 and neg_n == 0:
            status = "MISSING_NEG"
        elif neg_n >= 1 and pos_n == 0:
            status = "MISSING_POS"
        else:
            status = "UNCOVERED"
        out.append(
            {
                "factor": factor,
                "pos_n": pos_n,
                "neg_n": neg_n,
                "mixed_n": len(mixed),
                "pos_symbols": pos,
                "neg_symbols": neg,
                "mixed_symbols": mixed,
                "status": status,
                "both_sides": pos_n >= 1 and neg_n >= 1,
            }
        )
    return out


CORE_BOTH_SIDES_REQUIRED = ("USDJPY", "US_TECH_NQ", "GLOBAL_RISK_ES", "OIL", "JAPAN_RATES", "CHINA_HK")


assert "8306" in ROLES and ROLES["8306"]["JAPAN_RATES"] == "pos"
assert ROLES["8801"]["JAPAN_RATES"] == "neg"
assert ROLES["1605"]["OIL"] == "pos"
assert ROLES["9101"]["OIL"] == "neg"
assert ROLES["6857"]["US_TECH_NQ"] == "pos"
assert ROLES["2914"]["US_TECH_NQ"] == "neg"
assert len(MISSING_ROLE_SPECS) == 6

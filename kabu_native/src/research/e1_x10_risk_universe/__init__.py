"""E1_X10 Fixed 100-Share Risk Universe Audit — diagnostic only; no ENTRY/EXIT."""

ANALYSIS_ID = "E1_X10_FIXED_100SHARE_RISK_UNIVERSE_AUDIT"
DOCUMENT_ID = "E1_X10_AUTOMATION_RISK_UNIVERSE"
SOURCE_CLOSURE = "e1x7x9_closure_20260805_011523_A"

LOT = 100
GRID_SEC = 5.0
EXEC_HORIZONS_SEC = (1.0, 5.0, 10.0, 30.0)
FRESHNESS_MAX_SEC = 3.0  # production entry_max_price_age_sec / entry_max_board_age_sec
MIN_SPREAD_OBS = 30
MIN_SPREAD_DAYS = 3
TARGET_SYMBOL = "285A"

NOTIONAL_BANDS = (
    ("LE_300K", 0.0, 300_000.0),
    ("300K_500K", 300_000.0, 500_000.0),
    ("500K_1M", 500_000.0, 1_000_000.0),
    ("GT_1M", 1_000_000.0, float("inf")),
)

FORBIDDEN_ALPHA_COLUMNS = (
    "pnl",
    "profit",
    "win",
    "mfe",
    "mae",
    "first" + "-touch",
    "first" + "_touch",
    "candidate",
    "pfq",
    "exit_reason",
    "profit" + "_factor",
)

"""
Phase 79: Rolling OOS symbol cooloff from prior sessions only (never current session).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REJECT_SYMBOL_COOLDOWN = "symbol_cooloff"
RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5 = "prior_avg_pnl_negative_trades_ge_5"


@dataclass
class SymbolCooloffConfig:
    enabled: bool = False
    rule: str = RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5
    min_trades: int = 5
    metric: str = "avg_pnl"
    threshold: float = 0.0
    lookback_sessions: str = "all_available"
    apply_mode: str = "reject_entry"


@dataclass
class SymbolPriorStats:
    symbol: str
    trades: int = 0
    total_pnl_pct: float = 0.0
    pnls: list[float] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)

    @property
    def avg_pnl_pct(self) -> float:
        if self.trades <= 0:
            return 0.0
        return self.total_pnl_pct / self.trades

    @property
    def win_rate(self) -> float:
        if self.trades <= 0:
            return 0.0
        return sum(1 for p in self.pnls if p > 0) / self.trades

    @property
    def profit_factor(self) -> Optional[float]:
        return _profit_factor(self.pnls)


@dataclass
class SymbolCooloffCheck:
    blocked: bool
    symbol: str
    reason: str = ""
    prior_avg_pnl: Optional[float] = None
    prior_trades: int = 0
    prior_total_pnl: Optional[float] = None


@dataclass
class SymbolCooloffState:
    config: SymbolCooloffConfig
    run_session_key: str
    source_sessions: list[str] = field(default_factory=list)
    cooloff_symbols: set[str] = field(default_factory=set)
    prior_stats: dict[str, SymbolPriorStats] = field(default_factory=dict)

    def check(self, symbol: str) -> SymbolCooloffCheck:
        sym = str(symbol or "").strip()
        if not self.config.enabled or not sym:
            return SymbolCooloffCheck(False, sym)
        st = self.prior_stats.get(sym)
        if sym not in self.cooloff_symbols or st is None:
            return SymbolCooloffCheck(False, sym, prior_trades=st.trades if st else 0)
        return SymbolCooloffCheck(
            True,
            sym,
            reason=REJECT_SYMBOL_COOLDOWN,
            prior_avg_pnl=round(st.avg_pnl_pct, 6),
            prior_trades=st.trades,
            prior_total_pnl=round(st.total_pnl_pct, 4),
        )

    def summary_fields(self) -> dict[str, Any]:
        return {
            "symbol_cooloff_enabled": self.config.enabled,
            "symbol_cooloff_rule": self.config.rule,
            "symbol_cooloff_list": sorted(self.cooloff_symbols),
            "symbol_cooloff_count": len(self.cooloff_symbols),
            "symbol_cooloff_source_sessions": list(self.source_sessions),
            "symbol_cooloff_min_trades": self.config.min_trades,
            "symbol_cooloff_threshold": self.config.threshold,
            "symbol_cooloff_run_session_key": self.run_session_key,
        }


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def load_structural_trades(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {
                "symbol": r["symbol"],
                "realized_pnl_pct": float(r.get("realized_pnl_pct") or 0),
            }
            for r in csv.DictReader(f)
        ]


def discover_sessions_with_trades(
    small_paper_base: Path,
    *,
    before_session_key: str,
) -> list[tuple[str, Path]]:
    """Return (session_key, structural_trades.csv path) strictly before before_session_key."""
    found: list[tuple[str, Path]] = []
    if not small_paper_base.is_dir():
        return found
    for day_dir in sorted(small_paper_base.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit():
            continue
        for sub in sorted(day_dir.iterdir()):
            if not sub.is_dir():
                continue
            key = f"{day_dir.name}/{sub.name}"
            if key >= before_session_key:
                continue
            csv_path = sub / "structural_trades.csv"
            if csv_path.is_file():
                found.append((key, csv_path))
    found.sort(key=lambda x: x[0])
    return found


def aggregate_prior_stats(
    source_sessions: Sequence[tuple[str, Path]],
) -> dict[str, SymbolPriorStats]:
    out: dict[str, SymbolPriorStats] = {}
    for session_id, csv_path in source_sessions:
        for row in load_structural_trades(csv_path):
            sym = str(row["symbol"])
            st = out.setdefault(sym, SymbolPriorStats(symbol=sym))
            pnl = float(row["realized_pnl_pct"])
            st.trades += 1
            st.total_pnl_pct += pnl
            st.pnls.append(pnl)
            if session_id not in st.session_ids:
                st.session_ids.append(session_id)
    return out


def apply_cooloff_rule(
    prior: Mapping[str, SymbolPriorStats],
    *,
    rule: str,
    min_trades: int,
    threshold: float,
) -> set[str]:
    cooloff: set[str] = set()
    if rule == RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5:
        for sym, st in prior.items():
            if st.trades >= min_trades and st.avg_pnl_pct < threshold:
                cooloff.add(sym)
        return cooloff
    return cooloff


def build_symbol_cooloff_state(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> Optional[SymbolCooloffState]:
    """Build cooloff list using only sessions chronologically before run_session_key."""
    enabled = bool(getattr(pilot_config, "symbol_cooloff_enabled", False))
    if not enabled:
        return None

    cfg = SymbolCooloffConfig(
        enabled=True,
        rule=str(getattr(pilot_config, "symbol_cooloff_rule", RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5)),
        min_trades=int(getattr(pilot_config, "symbol_cooloff_min_trades", 5)),
        metric=str(getattr(pilot_config, "symbol_cooloff_metric", "avg_pnl")),
        threshold=float(getattr(pilot_config, "symbol_cooloff_threshold", 0.0)),
        lookback_sessions=str(
            getattr(pilot_config, "symbol_cooloff_lookback_sessions", "all_available")
        ),
        apply_mode=str(getattr(pilot_config, "symbol_cooloff_apply_mode", "reject_entry")),
    )

    base = repo_root / "kabu_native" / "results" / "small_paper"
    sources = discover_sessions_with_trades(base, before_session_key=run_session_key)
    if cfg.lookback_sessions != "all_available":
        try:
            n = int(cfg.lookback_sessions)
            sources = sources[-n:]
        except ValueError:
            pass

    prior = aggregate_prior_stats(sources)
    cooloff = apply_cooloff_rule(
        prior,
        rule=cfg.rule,
        min_trades=cfg.min_trades,
        threshold=cfg.threshold,
    )

    return SymbolCooloffState(
        config=cfg,
        run_session_key=run_session_key,
        source_sessions=[s[0] for s in sources],
        cooloff_symbols=cooloff,
        prior_stats=prior,
    )


def cooloff_config_from_pilot(pilot_config: Any) -> SymbolCooloffConfig:
    return SymbolCooloffConfig(
        enabled=bool(getattr(pilot_config, "symbol_cooloff_enabled", False)),
        rule=str(getattr(pilot_config, "symbol_cooloff_rule", RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5)),
        min_trades=int(getattr(pilot_config, "symbol_cooloff_min_trades", 5)),
        metric=str(getattr(pilot_config, "symbol_cooloff_metric", "avg_pnl")),
        threshold=float(getattr(pilot_config, "symbol_cooloff_threshold", 0.0)),
        lookback_sessions=str(
            getattr(pilot_config, "symbol_cooloff_lookback_sessions", "all_available")
        ),
        apply_mode=str(getattr(pilot_config, "symbol_cooloff_apply_mode", "reject_entry")),
    )


def session_key_from_output_dir(output_dir: Path, repo_root: Path) -> str:
    base = (repo_root / "kabu_native" / "results" / "small_paper").resolve()
    return str(output_dir.resolve().relative_to(base)).replace("\\", "/")


def prior_stats_rows(prior: Mapping[str, SymbolPriorStats]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym, st in sorted(prior.items()):
        pf = st.profit_factor
        rows.append(
            {
                "symbol": sym,
                "trades": st.trades,
                "total_pnl_pct": round(st.total_pnl_pct, 4),
                "avg_pnl_pct": round(st.avg_pnl_pct, 6),
                "win_rate": round(st.win_rate, 4),
                "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "sessions": "|".join(st.session_ids),
            }
        )
    rows.sort(key=lambda r: r["avg_pnl_pct"])
    return rows


def validate_prior_only_sources(
    state: SymbolCooloffState,
    *,
    run_session_key: str,
) -> list[str]:
    """Return error messages if any source session is not strictly before run_session_key."""
    errs: list[str] = []
    for sid in state.source_sessions:
        if sid >= run_session_key:
            errs.append(f"source session {sid} must be before {run_session_key}")
    return errs

"""
Phase 84: Rolling OOS daytrade suitability gate (prior sessions only for threshold).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from small_paper.accepted_liquidity_metrics import (
    load_push_tick_series,
    lookup_metrics_at_entry,
    metrics_from_payload,
)
from small_paper.daytrade_suitability import (
    QUALITY_GATE,
    enrich_daytrade_metrics,
    percentile_value,
    volatility_liquidity_score,
)
from small_paper.symbol_cooloff import load_structural_trades

REJECT_DAYTRADE_SUITABILITY = "daytrade_suitability"
RULE_VOLATILITY_LIQUIDITY_TOP50 = "volatility_liquidity_top50"
LOOKBACK_PRIOR_ONLY = "prior_only"


@dataclass
class DaytradeSuitabilityConfig:
    enabled: bool = False
    rule: str = RULE_VOLATILITY_LIQUIDITY_TOP50
    lookback_sessions: str = LOOKBACK_PRIOR_ONLY
    apply_mode: str = "reject_entry"


@dataclass
class DaytradeSuitabilityCheck:
    blocked: bool
    score: Optional[float] = None
    threshold: Optional[float] = None
    atr_pct: Optional[float] = None
    intraday_range_pct: Optional[float] = None
    trading_value: Optional[float] = None
    turnover_proxy: Optional[float] = None
    reason: str = ""


@dataclass
class DaytradeSuitabilityState:
    config: DaytradeSuitabilityConfig
    run_session_key: str
    source_sessions: list[str] = field(default_factory=list)
    vol_liq_threshold: Optional[float] = None
    prior_quality_trade_count: int = 0

    def summary_fields(self) -> dict[str, Any]:
        return {
            "daytrade_suitability_enabled": self.config.enabled,
            "daytrade_suitability_rule": self.config.rule,
            "daytrade_suitability_threshold": self.vol_liq_threshold,
            "daytrade_suitability_source_sessions": list(self.source_sessions),
            "daytrade_suitability_prior_quality_trades": self.prior_quality_trade_count,
            "daytrade_suitability_run_session_key": self.run_session_key,
        }

    def check(self, trade: Mapping[str, Any]) -> DaytradeSuitabilityCheck:
        if not self.config.enabled:
            return DaytradeSuitabilityCheck(False, reason="disabled")

        metrics = entry_metrics_from_trade(trade)
        score = metrics.get("volatility_liquidity_score")
        th = self.vol_liq_threshold

        if th is None:
            return DaytradeSuitabilityCheck(
                blocked=False,
                score=score,
                threshold=None,
                atr_pct=metrics.get("atr_pct"),
                intraday_range_pct=metrics.get("intraday_range_pct"),
                trading_value=metrics.get("trading_value"),
                turnover_proxy=metrics.get("turnover_proxy"),
                reason="no_prior_threshold",
            )

        if score is None:
            return DaytradeSuitabilityCheck(
                blocked=True,
                score=None,
                threshold=th,
                atr_pct=metrics.get("atr_pct"),
                intraday_range_pct=metrics.get("intraday_range_pct"),
                trading_value=metrics.get("trading_value"),
                turnover_proxy=metrics.get("turnover_proxy"),
                reason="missing_vol_liq_score",
            )

        blocked = float(score) < float(th)
        return DaytradeSuitabilityCheck(
            blocked=blocked,
            score=score,
            threshold=th,
            atr_pct=metrics.get("atr_pct"),
            intraday_range_pct=metrics.get("intraday_range_pct"),
            trading_value=metrics.get("trading_value"),
            turnover_proxy=metrics.get("turnover_proxy"),
            reason=REJECT_DAYTRADE_SUITABILITY if blocked else "",
        )


def entry_metrics_from_trade(trade: Mapping[str, Any]) -> dict[str, Optional[float]]:
    tv = trade.get("trading_value")
    if tv is None:
        tv = trade.get("trading_value_jpy")
    atr = _float(trade.get("atr_pct"))
    vol_liq = trade.get("volatility_liquidity_score")
    if vol_liq is None and atr is not None and tv is not None:
        vol_liq = volatility_liquidity_score(atr, _float(tv))
    return {
        "atr_pct": atr,
        "intraday_range_pct": _float(trade.get("intraday_range_pct")),
        "trading_value": _float(tv),
        "turnover_proxy": _float(trade.get("turnover_proxy")),
        "volatility_liquidity_score": _float(vol_liq) if vol_liq is not None else None,
    }


def entry_metrics_from_payload(payload: Mapping[str, Any]) -> dict[str, Optional[float]]:
    from small_paper.accepted_liquidity_metrics import _float as af

    px = af(payload.get("CurrentPrice")) or af(payload.get("CalcPrice")) or 0.0
    base = metrics_from_payload(payload, entry_price=float(px or 0))
    enriched = enrich_daytrade_metrics(base, payload)
    tv = enriched.get("trading_value_jpy")
    return {
        "atr_pct": enriched.get("atr_pct"),
        "intraday_range_pct": enriched.get("intraday_range_pct"),
        "trading_value": tv,
        "turnover_proxy": enriched.get("turnover_proxy"),
        "volatility_liquidity_score": enriched.get("volatility_liquidity_score"),
    }


def attach_entry_metrics_to_trade(trade: dict[str, Any], payload: Mapping[str, Any]) -> None:
    m = entry_metrics_from_payload(payload)
    trade["atr_pct"] = m.get("atr_pct")
    trade["intraday_range_pct"] = m.get("intraday_range_pct")
    trade["trading_value"] = m.get("trading_value")
    trade["trading_value_jpy"] = m.get("trading_value")
    trade["turnover_proxy"] = m.get("turnover_proxy")
    trade["volatility_liquidity_score"] = m.get("volatility_liquidity_score")


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_ts(iso: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def push_dir_for_session_key(session_key: str, repo_root: Path) -> Optional[Path]:
    day = session_key.split("/")[0]
    if len(day) == 8 and day.isdigit():
        return (
            repo_root
            / "kabu_native"
            / "data"
            / "push_jsonl"
            / f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        )
    return None


def discover_sessions_for_suitability_prior(
    small_paper_base: Path,
    *,
    before_session_key: str,
) -> list[tuple[str, Path]]:
    """Sessions strictly before run key (structural csv and/or replay/live events)."""
    found: list[tuple[str, Path]] = []
    if not small_paper_base.is_dir():
        return found
    for day_dir in sorted(small_paper_base.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8 or not day_dir.name.isdigit():
            continue
        for sub in sorted(day_dir.iterdir()):
            if not sub.is_dir():
                continue
            key = f"{day_dir.name}/{sub.name}"
            if key >= before_session_key:
                continue
            has_csv = (sub / "structural_trades.csv").is_file()
            has_events = (sub / "small_paper_events.jsonl").is_file()
            if not has_csv and not has_events:
                continue
            if (
                has_csv
                or sub.name.startswith("push_replay_")
                or sub.name.startswith("live_full_session_")
            ):
                found.append((key, sub))
    found.sort(key=lambda x: x[0])
    return found


def load_session_trades_csv_or_replay(session_dir: Path, *, repo_root: Path) -> list[dict[str, Any]]:
    csv_path = session_dir / "structural_trades.csv"
    if csv_path.is_file():
        return load_structural_trades(csv_path)
    if not (session_dir / "small_paper_events.jsonl").is_file():
        return []
    return _trades_from_accepted_events(session_dir)


def _trades_from_accepted_events(session_dir: Path) -> list[dict[str, Any]]:
    """Prior calibration: accepted events with quality (no structural exit replay)."""
    import json

    path = session_dir / "small_paper_events.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(ev.get("event_type") or "") != "accepted":
                continue
            q = _float(ev.get("continuation_quality_score")) or 0.0
            if q < QUALITY_GATE:
                continue
            sym = str(ev.get("symbol") or "")
            ent = str(ev.get("entry_time") or "")
            if sym and ent:
                out.append(
                    {
                        "symbol": sym,
                        "entry_time": ent,
                        "continuation_quality_score": q,
                    }
                )
    return out


def prior_vol_liq_scores(
    source_sessions: Sequence[tuple[str, Path]],
    *,
    repo_root: Path,
) -> tuple[list[float], list[str]]:
    scores: list[float] = []
    used_sessions: list[str] = []
    for session_id, session_dir in source_sessions:
        push_dir = push_dir_for_session_key(session_id, repo_root)
        if push_dir is None or not push_dir.is_dir():
            continue
        trades = load_session_trades_csv_or_replay(session_dir, repo_root=repo_root)
        symbols = {str(t.get("symbol") or "") for t in trades}
        series = load_push_tick_series(push_dir, symbols)
        session_scores: list[float] = []
        for t in trades:
            q = _float(t.get("continuation_quality_score")) or 0.0
            if q < QUALITY_GATE:
                continue
            sym = str(t.get("symbol") or "")
            ent_ts = _parse_ts(str(t.get("entry_time") or ""))
            m = lookup_metrics_at_entry(series.get(sym, []), ent_ts)
            vol = volatility_liquidity_score(
                _float(m.get("atr_pct")),
                _float(m.get("trading_value_jpy")),
            )
            if vol is not None:
                session_scores.append(vol)
        if session_scores:
            scores.extend(session_scores)
            used_sessions.append(session_id)
    return scores, used_sessions


def build_vol_liq_threshold(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> Optional[DaytradeSuitabilityState]:
    if not bool(getattr(pilot_config, "daytrade_suitability_enabled", False)):
        return None

    from small_paper.vol_liq_startup_cache import build_vol_liq_threshold_with_startup_cache

    return build_vol_liq_threshold_with_startup_cache(
        pilot_config,
        repo_root=repo_root,
        run_session_key=run_session_key,
    )


def validate_prior_only_sources(
    state: DaytradeSuitabilityState,
    *,
    run_session_key: str,
) -> list[str]:
    errors: list[str] = []
    for sid in state.source_sessions:
        if sid >= run_session_key:
            errors.append(f"prior session {sid} not before run {run_session_key}")
    return errors


def suitability_config_from_pilot(pilot_config: Any) -> DaytradeSuitabilityConfig:
    return DaytradeSuitabilityConfig(
        enabled=bool(getattr(pilot_config, "daytrade_suitability_enabled", False)),
        rule=str(
            getattr(pilot_config, "daytrade_suitability_rule", RULE_VOLATILITY_LIQUIDITY_TOP50)
        ),
        lookback_sessions=str(
            getattr(pilot_config, "daytrade_suitability_lookback_sessions", LOOKBACK_PRIOR_ONLY)
        ),
        apply_mode=str(getattr(pilot_config, "daytrade_suitability_apply_mode", "reject_entry")),
    )

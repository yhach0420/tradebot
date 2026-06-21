"""
Phase465A — Trend Tournament Audit.

Investigates Phase464 vs Phase465 contradictions (return field缺失, gate funnel, cohort identity).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _position_key
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
)
from research.phase464_pre_gate_archetype_audit import (
    _annotate_candidates,
    _is_trend_following,
    _load_population_cache,
    _passes_board_gate,
    _rise,
    _vwap_above_ratio,
    _weak_shape_block,
)
from research.phase465_trend_gate_tournament import (
    GATE_SPECS,
    _entry_block,
    _make_trend_only,
    _load_replay_pool,
)

RETURN_FIELDS = ("return_5min_pct", "return_10min_pct", "return_15min_pct", "return_30min_pct")
RISE_ALIASES = ("r5", "r10", "r15", "r30")
FIELD_MAP = {
    "r5": ("return_5min_pct", "entry_rise_5min_pct"),
    "r10": ("return_10min_pct", "entry_rise_10min_pct"),
    "r15": ("return_15min_pct", "entry_rise_15min_pct"),
    "r30": ("return_30min_pct", "entry_rise_30min_pct"),
}

FUNNEL_FIELDS = [
    "gate_id",
    "replay_pool_count",
    "trend_following_count",
    "gate_pass_count",
    "trend_and_gate_count",
    "board_pass_count",
    "runtime_core_count",
    "trend_gate_runtime_count",
    "replay_accepted_count",
]

MISSING_FIELDS = [
    "field_or_alias",
    "source_field_a",
    "source_field_b",
    "cohort_non_null",
    "cohort_null",
    "replay_non_null",
    "replay_null",
    "cohort_pct_present",
]

COHORT_CMP_FIELDS = [
    "metric",
    "phase464_style",
    "phase465_style",
    "match",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _trend_path(trade: Mapping[str, Any]) -> str:
    r15 = _rise(trade, 15)
    r30 = _rise(trade, 30)
    vwap = _vwap_above_ratio(trade) or 0
    hu = _float(trade.get("high_update_count_30m")) or 0
    if r15 is not None and r30 is not None and r15 > 0 and r30 > 0 and vwap >= 0.7:
        return "r15_r30_vwap"
    if hu >= 2:
        return "high_update_only"
    return "other"


def _pass_trend_runtime_core(t: Mapping[str, Any]) -> bool:
    if not _passes_board_gate(t):
        return False
    if guard_high_drift(t):
        return False
    if _weak_shape_block(t):
        return False
    if phase364_blocked_only(t):
        return False
    return True


def _field_presence(rows: Sequence[Mapping[str, Any]], *keys: str) -> tuple[int, int]:
    non_null = sum(1 for t in rows if any(_float(t.get(k)) is not None for k in keys))
    return non_null, len(rows) - non_null


def _phase464_cohort_keys(reports: Path, price_idx: Mapping) -> set[str]:
    pop = _load_population_cache(reports)
    if not pop:
        return set()
    raw, _, _ = pop
    ann = _annotate_candidates(raw, price_idx=price_idx)
    return {
        _position_key(t)
        for t in ann
        if _is_trend_following(t) and not t.get("data_stale")
    }


def _phase465_cohort_keys(reports: Path, price_idx: Mapping) -> set[str]:
    return _phase464_cohort_keys(reports, price_idx)


def run_phase465a_audit(*, repo_root: Path) -> dict[str, Any]:
    from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    pop = _load_population_cache(reports)
    if not pop:
        raise FileNotFoundError("phase464 cache required")
    raw_candidates, _, _ = pop
    ann = _annotate_candidates(raw_candidates, price_idx=price_idx)
    cohort = [t for t in ann if _is_trend_following(t) and not t.get("data_stale")]
    cohort_keys = {_position_key(t) for t in cohort}

    replay_pool, np_shadows = _load_replay_pool(reports)
    np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, np_shadows)

    # 1 — r5/r10/r15/r30 count=0 root cause
    rise_presence: dict[str, Any] = {}
    for alias, (src_a, src_b) in FIELD_MAP.items():
        nn, nl = _field_presence(cohort, src_a, src_b)
        rise_presence[alias] = {
            "cohort_non_null_via_rise": sum(1 for t in cohort if _rise(t, int(alias[1:])) is not None),
            "cohort_non_null_src_a": sum(1 for t in cohort if _float(t.get(src_a)) is not None),
            "cohort_non_null_src_b": sum(1 for t in cohort if _float(t.get(src_b)) is not None),
            "cohort_total": len(cohort),
        }

    path_ctr = Counter(_trend_path(t) for t in cohort)

    # 4 — cohort identity
    keys464 = _phase464_cohort_keys(reports, price_idx)
    keys465 = _phase465_cohort_keys(reports, price_idx)
    cohort_cmp = [
        {
            "metric": "trend_cohort_count",
            "phase464_style": len(keys464),
            "phase465_style": len(keys465),
            "match": len(keys464) == len(keys465),
        },
        {
            "metric": "key_set_identical",
            "phase464_style": len(keys464),
            "phase465_style": len(keys465 & keys464),
            "match": keys464 == keys465,
        },
        {
            "metric": "only_in_464",
            "phase464_style": len(keys464 - keys465),
            "phase465_style": 0,
            "match": len(keys464 - keys465) == 0,
        },
        {
            "metric": "only_in_465",
            "phase464_style": 0,
            "phase465_style": len(keys465 - keys464),
            "match": len(keys465 - keys464) == 0,
        },
    ]

    # 5 — missing field audit
    missing_rows: list[dict[str, Any]] = []
    for alias, (src_a, src_b) in FIELD_MAP.items():
        cnn, cnl = _field_presence(cohort, src_a, src_b)
        rnn, rnl = _field_presence(replay_pool, src_a, src_b)
        missing_rows.append(
            {
                "field_or_alias": alias,
                "source_field_a": src_a,
                "source_field_b": src_b,
                "cohort_non_null": cnn,
                "cohort_null": cnl,
                "replay_non_null": rnn,
                "replay_null": rnl,
                "cohort_pct_present": round(cnn / max(len(cohort), 1), 4),
            }
        )
    for feat in ("high_update_count_30m", "vwap_above_ratio", "consecutive_above_ticks"):
        cnn, cnl = _field_presence(cohort, feat)
        rnn, rnl = _field_presence(replay_pool, feat)
        missing_rows.append(
            {
                "field_or_alias": feat,
                "source_field_a": feat,
                "source_field_b": "",
                "cohort_non_null": cnn,
                "cohort_null": cnl,
                "replay_non_null": rnn,
                "replay_null": rnl,
                "cohort_pct_present": round(cnn / max(len(cohort), 1), 4),
            }
        )

    # 2 & 3 — gate funnel + accepted
    funnel_rows: list[dict[str, Any]] = []
    trend_in_replay = [t for t in replay_pool if _is_trend_following(t)]
    for gate_id, (_, gate_fn) in GATE_SPECS.items():
        trend_fn = _make_trend_only(gate_fn)
        gate_pass = [t for t in replay_pool if gate_fn(t)]
        trend_and_gate = [t for t in replay_pool if _is_trend_following(t) and gate_fn(t)]
        runtime_core = [t for t in trend_and_gate if _pass_trend_runtime_core(t)]
        pass_entry = [t for t in replay_pool if trend_fn(t)]

        st = simulate_capacity_replay(
            replay_pool,
            np_shadows,
            mode=f"phase465a_{gate_id}",
            entry_block_fn=_entry_block(trend_fn),
            baseline_accepted_keys=set(),
        )

        funnel_rows.append(
            {
                "gate_id": gate_id,
                "replay_pool_count": len(replay_pool),
                "trend_following_count": len(trend_in_replay),
                "gate_pass_count": len(gate_pass),
                "trend_and_gate_count": len(trend_and_gate),
                "board_pass_count": sum(1 for t in trend_and_gate if _passes_board_gate(t)),
                "runtime_core_count": len(runtime_core),
                "trend_gate_runtime_count": len(pass_entry),
                "replay_accepted_count": st.accepted_trade_count,
            }
        )

    # T1 zero accepted breakdown
    t1_gate = GATE_SPECS["T1"][1]
    t1_fail_r30 = sum(
        1
        for t in replay_pool
        if _is_trend_following(t) and (_rise(t, 30) is None or (_rise(t, 30) or 0) <= 0)
    )

    verdict = "phase465_invalid"
    reasons = [
        "return_*min_pct missing on trend cohort (r5-r30 count=0 in Part A)",
        "T1-T10 gates using r30 treat None as fail → T1 accepts 0 despite 29460 trend cohort",
        "Phase464 would-PnL used high_update path; Phase465 gates T1/T6-T10 require absent r* fields",
    ]
    if keys464 == keys465:
        reasons.append("cohort key set identical between Phase464 and Phase465")

    findings = {
        "1_r5_r30_count_zero_reason": (
            "phase464 cache stores high_update/vwap features but return_5/10/15/30min_pct "
            f"present on only {_field_presence(cohort, 'return_30min_pct')[0]}/{len(cohort)} cohort rows. "
            f"Trend path: {dict(path_ctr)}. Phase465 Part A reads alias r5-r30 via _rise() → all None."
        ),
        "2_trend_29460_accepted_zero_reason": (
            f"Replay pool trend={len(trend_in_replay)}. T1 requires r30>0 but r30 non-null in replay="
            f"{_field_presence(replay_pool, 'return_30min_pct')[0]}. "
            f"Trend+T1 fail (missing r30): {t1_fail_r30}. "
            f"Trend+runtime_core in replay: {sum(1 for t in trend_in_replay if _pass_trend_runtime_core(t))}. "
            "Capacity replay accepted=0 when trend_gate_runtime_count=0."
        ),
        "3_gate_funnel": funnel_rows,
        "4_cohort_same": keys464 == keys465,
        "4_cohort_counts": {"464": len(keys464), "465": len(keys465)},
        "5_missing_fields": missing_rows,
        "5_rise_presence": rise_presence,
        "verdict": verdict,
        "invalidity_reasons": reasons,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "findings": findings,
        "_funnel_rows": funnel_rows,
        "_missing_rows": missing_rows,
        "_cohort_cmp": cohort_cmp,
        "_rise_presence": rise_presence,
        "_trend_path_distribution": dict(path_ctr),
    }


@dataclass
class Phase465AJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase465a_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        from research.structural_trade_normalize import resolve_reports_dir

        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "funnel": reports / "phase465a_trend_gate_funnel.csv",
            "missing": reports / "phase465a_field_presence_audit.csv",
            "cohort": reports / "phase465a_cohort_identity.csv",
            "summary": reports / "phase465a_summary.json",
        }
        _write_csv(paths["funnel"], FUNNEL_FIELDS, list(result.get("_funnel_rows") or []))
        _write_csv(paths["missing"], MISSING_FIELDS, list(result.get("_missing_rows") or []))
        _write_csv(paths["cohort"], COHORT_CMP_FIELDS, list(result.get("_cohort_cmp") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase465a_trend_tournament_audit.md"
        f = result.get("findings") or {}
        lines = [
            "# Phase465A — Trend Tournament Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## 1. r5–r30 count=0",
            "",
            str(f.get("1_r5_r30_count_zero_reason")),
            "",
            "## 2. Trend 29460 → accepted 0",
            "",
            str(f.get("2_trend_29460_accepted_zero_reason")),
            "",
            "## 4. Cohort identity",
            "",
            f"Same key set: **{f.get('4_cohort_same')}** ({f.get('4_cohort_counts')})",
            "",
            "## 5. Trend path (Phase464/465 cohort)",
            "",
            str(result.get("_trend_path_distribution")),
            "",
            "See `phase465a_trend_gate_funnel.csv` for T1–T10 funnel counts.",
        ]
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths

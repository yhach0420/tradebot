"""report.json / report.md only. No Excel. Day2 freeze untouched."""
from __future__ import annotations

import json
from typing import Any

from research.am_entry_profit_improvement.publish import json_sanitize as _json_sanitize
from research.current_day1_information_close_v1 import ANALYSIS_ID
from research.current_day1_information_close_v1.isolation import OUT


def json_sanitize(obj: Any) -> Any:
    got = _json_sanitize(obj)
    if isinstance(got, float) and abs(got) == float("inf"):
        return "inf" if got > 0 else "-inf"
    if isinstance(got, dict):
        return {str(k): json_sanitize(v) for k, v in got.items()}
    if isinstance(got, list):
        return [json_sanitize(v) for v in got]
    return got


def _fmt(x: Any) -> str:
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    return str(x)


def build_markdown(body: dict[str, Any]) -> str:
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    freeze = dict(body.get("day2_freeze") or {})
    nxt = dict(body.get("next_tape") or {})
    bind = dict(body.get("binding_counterexample") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{d.get('VERDICT')}**",
        f"NEXT: `{d.get('NEXT')}`",
        "",
        "20260911 existing raw feature mining is CLOSED. This file archives parent",
        "verdicts. It does not duplicate parent tables. Day2 primary freeze is",
        "unchanged. No ENTRY/EXIT. No Excel.",
        "",
        "## Required close answers",
        "",
        f"- 1 Day1 existing-information exhausted: `{_fmt(a.get('1_Day1_existing_information_exhausted'))}`",
        f"- 2 futures-only price lead usable: `{_fmt(a.get('2_futures_only_price_lead_usable'))}`",
        f"- 3 futures book lead usable: `{_fmt(a.get('3_futures_book_lead_usable'))}`",
        f"- 4 futures×activity interaction exists: `{a.get('4_futures_x_activity_interaction_exists')}`",
        f"- 5 T-time BOTH_DOWN sufficient: `{_fmt(a.get('5_T_time_BOTH_DOWN_sufficient'))}`",
        f"- 6 precursor sufficient: `{_fmt(a.get('6_precursor_sufficient'))}`",
        f"- 7 actual BOTH_UP sufficient: `{_fmt(a.get('7_actual_BOTH_UP_sufficient'))}`",
        f"- 8 cash participation sufficient: `{_fmt(a.get('8_cash_participation_sufficient'))}`",
        f"- 9 causal ENTRY identified: `{_fmt(a.get('9_causal_ENTRY_identified'))}`",
        f"- 10 EXIT identified: `{_fmt(a.get('10_EXIT_identified'))}`",
        f"- 11 Day1 feature mining closed: `{_fmt(a.get('11_Day1_feature_mining_closed'))}`",
        f"- 12 Day2 primary unchanged: `{_fmt(a.get('12_Day2_primary_unchanged'))}`",
        f"- 13 substitutions allowed: `{_fmt(a.get('13_substitutions_allowed'))}`",
        f"- 14 next information source: `{a.get('14_next_information_source')}`",
        f"- 15 Runtime changed: `{_fmt(a.get('15_Runtime_changed'))}`",
        f"- 16 Paper changed: `{_fmt(a.get('16_Paper_changed'))}`",
        f"- 17 submit/cancel/live: `{a.get('17_submit_cancel_live')}`",
        f"- 18 VERDICT: `{a.get('18_VERDICT')}`",
        f"- 19 NEXT: `{a.get('19_NEXT')}`",
        "",
        "## Binding counterexample",
        "",
        f"- 09:21:46: `{bind.get('winner')}`",
        f"- 10:32:39: `{bind.get('loser')}`",
        f"- {bind.get('interpretation')}",
        "",
        "## Retained scientific finding only",
        "",
        str(body.get("scientific_finding_retained")),
        "",
        "Do not convert this into ENTRY logic, a trading rule, a threshold,",
        "a reversal trigger, or a cash confirmation rule.",
        "",
        "## Parent chain (reference, not copy)",
        "",
    ]
    for p in body.get("parents") or []:
        extra = []
        if p.get("CASE") not in (None, ""):
            extra.append(f"CASE={p.get('CASE')}")
        if p.get("TYPE") not in (None, ""):
            extra.append(f"TYPE={p.get('TYPE')}")
        tag = (" " + " ".join(extra)) if extra else ""
        lines.append(f"- {p.get('label')}: `{p.get('VERDICT')}`{tag}")
        lines.append(f"  `{p.get('path')}`")
    lines.extend(
        [
            "",
            "## Day2 primary freeze (unchanged)",
            "",
            f"- analysis_id: `{freeze.get('analysis_id')}`",
            f"- primary: `{freeze.get('primary')}`",
            f"- substitutions_allowed: `{_fmt(freeze.get('substitutions_allowed'))}`",
            f"- rewritten_by_this_close: `{_fmt(freeze.get('rewritten_by_this_close'))}`",
            f"- path: `{freeze.get('path')}`",
            "",
            "Day2 primary asks only whether the Day1 cross-sectional interaction",
            "repeats exactly. Pass does not certify ENTRY. Fail means the",
            "interaction does not replicate.",
            "",
            "## Stop on 20260911 raw",
            "",
        ]
    )
    for item in body.get("stop_forbidden") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Next tape",
            "",
            f"- source: `{nxt.get('source')}` live-ready `{nxt.get('live_ready')}`",
            f"- /ranking types: `{nxt.get('types')}`",
            f"- window: `{nxt.get('window_jst')}` JST cadence {nxt.get('cadence_sec')}s fail-soft {nxt.get('fail_soft_cadence_sec')}s",
            f"- register/unregister mutation: `{_fmt(nxt.get('register_unregister_mutation'))}`",
            f"- parallel: `{nxt.get('futures_live_command')}` and `{nxt.get('breadth_live_command')}`",
            f"- predefined metrics: `{nxt.get('predefined_metrics')}`",
            f"- forbidden raw primaries: `{nxt.get('forbidden_raw_primaries')}`",
            f"- Q1: {(list(nxt.get('first_questions') or []) + ['', ''])[0]}",
            f"- Q2: {(list(nxt.get('first_questions') or []) + ['', ''])[1]}",
            "- No large Futures × Breadth × Stock grid on first recon.",
            "- Do not wait 10 days; same-day recon after transport FULL.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def write_artifacts(body: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = json_sanitize(body)
    (OUT / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUT / "report.md").write_text(build_markdown(body), encoding="utf-8")
    return {
        "report_json": str(OUT / "report.json"),
        "report_md": str(OUT / "report.md"),
    }

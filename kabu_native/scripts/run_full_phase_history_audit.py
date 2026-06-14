#!/usr/bin/env python3
"""
Full Phase History Audit generator (research-only output).

Outputs:
  kabu_native/docs/audits/full_phase_history_audit.csv
  kabu_native/results/reports/full_phase_history_report.md  (temporary audit snapshot)
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "kabu_native" / "scripts"
REPORTS = REPO / "kabu_native" / "results" / "reports"
AUDITS = REPO / "kabu_native" / "docs" / "audits"
SRC = REPO / "kabu_native" / "src"

PHASE_SCRIPT_RE = re.compile(r"run_phase(\d+[a-z]?|343p)_", re.I)
SUMMARY_JSON_RE = re.compile(r"phase(\d+[a-z]?|343p)_", re.I)

CSV_FIELDS = [
    "phase",
    "date",
    "category",
    "sub_category",
    "title",
    "purpose",
    "runtime_status",
    "shadow_status",
    "research_status",
    "current_status",
    "adoption_status",
    "removed_or_disabled",
    "related_phases",
    "related_reports",
    "summary",
    "verdict",
]

# Manual overrides keyed by (phase, script_stem_suffix) or phase alone
RUNTIME_ADOPTED: dict[str, dict[str, Any]] = {
    "55": {"category": "Monitoring", "title": "Small paper observer runtime", "related": "148,332"},
    "113": {"category": "Universe", "title": "Daytrade suitability top50 rule"},
    "117": {"category": "Universe", "title": "Volatility liquidity universe"},
    "148": {"category": "Monitoring", "title": "AM/PM daily runner orchestration"},
    "153b": {"category": "Entry", "title": "Entry price risk guard (min price / tick ratio)"},
    "267": {"category": "Entry", "title": "entry_score_v2 gate (min=3); quality reject off"},
    "314": {"category": "Entry", "title": "Entry score v2 simplification (Momentum+Board only)"},
    "332": {"category": "Exit", "title": "Board-dynamic trailing-MFE production EXIT"},
    "333": {"category": "Discord", "title": "Canonical 100-share yen summary"},
    "355": {"category": "Entry", "title": "Pullback misread Dynamic40 guard"},
    "364": {"category": "Entry", "title": "Near day-high + low momentum Dynamic40 guard"},
    "281": {"category": "Discord", "title": "Discord channel split (trade vs cap-blocked)"},
}

ACTIVE_FORWARD_SHADOW: dict[str, dict[str, Any]] = {
    "255": {"title": "Sector heat forward shadow logger", "related": "253,254,256"},
    "256": {"title": "Sector heat forward shadow auto hook", "related": "255"},
    "262": {"title": "Risk-aware sizing forward shadow logger", "related": "261"},
    "266": {"title": "Equity dynamic stop forward shadow auto", "related": "263"},
    "273": {"title": "Live config forward shadow (Phase272 configs)", "related": "270,272"},
    "274": {"title": "Live config auto-transition shadow (1.5M→2M band)", "related": "272,273"},
}

REJECTED: dict[str, dict[str, Any]] = {
    "351": {"title": "Limit-up proximity entry guard", "category": "Entry"},
    "359": {"title": "Gap-up fade entry guard", "category": "Entry"},
    "368": {"title": "Symbol reentry cluster guard", "category": "Entry"},
    "370": {"title": "K10 stop-chain A1 guard", "category": "Entry"},
    "371": {"title": "High-MFE stop_hit exit recovery", "category": "Exit"},
    "375": {"title": "Dynamic40 rank quality full replace", "category": "Universe"},
    "166": {"title": "Fade breakdown EXIT", "category": "Exit"},
    "200": {"title": "Entry architecture redesign", "category": "Entry"},
    "189": {"title": "New feature discovery", "category": "Entry"},
}

SUPERSEDED: dict[str, dict[str, Any]] = {
    "174": {"title": "Fixed trailing MFE 0.8%/50%", "superseded_by": "332"},
    "13": {"title": "no_entry_until 09:30 gate", "superseded_by": "148"},
    "114": {"title": "12:25 PM universe regen", "superseded_by": "148 intraday refresh"},
    "271": {"title": "Leverage 1.5 bucket recommendation", "superseded_by": "272"},
    "270": {"title": "Equity bucket recommendation (mixed leverage)", "superseded_by": "272"},
}

NON_PHASE_RUNTIME: list[dict[str, Any]] = [
    {
        "phase": "NP-entry-scan",
        "date": "2026-06-13",
        "category": "Entry",
        "sub_category": "freshness",
        "title": "Entry scan controller (freshness/batch)",
        "purpose": "Stale price/board reject; max entries per scan",
        "runtime_status": "runtime",
        "shadow_status": "none",
        "research_status": "none",
        "current_status": "active",
        "adoption_status": "adopted",
        "removed_or_disabled": "false",
        "related_phases": "348,355",
        "related_reports": "",
        "summary": "entry_scan_controller.py wired in pilot_runner",
        "verdict": "Production ENTRY path guard from kabutrade0612",
    },
    {
        "phase": "NP-canonical-summary",
        "date": "2026-06-13",
        "category": "Discord",
        "sub_category": "reporting",
        "title": "Canonical summary builder",
        "purpose": "Discord/session summary from observer_exit only",
        "runtime_status": "runtime",
        "shadow_status": "none",
        "research_status": "none",
        "current_status": "active",
        "adoption_status": "adopted",
        "removed_or_disabled": "false",
        "related_phases": "333",
        "related_reports": "phase333_summary_100share_yen_pnl_report.md",
        "summary": "100-share yen primary metrics",
        "verdict": "Production reporting",
    },
]

CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("universe", "Universe"),
    ("entry", "Entry"),
    ("exit", "Exit"),
    ("position", "Position"),
    ("cap", "Position"),
    ("concurrent", "Position"),
    ("risk", "Risk"),
    ("sizing", "Sizing"),
    ("equity", "Sizing"),
    ("capital", "Sizing"),
    ("sector_heat", "Data"),
    ("intraday", "Data"),
    ("data", "Data"),
    ("replay", "Replay"),
    ("monitor", "Monitoring"),
    ("production_monitor", "Monitoring"),
    ("discord", "Discord"),
    ("config", "Config"),
    ("yaml", "Config"),
    ("documentation", "Documentation"),
    ("forensic", "Monitoring"),
    ("audit", "Monitoring"),
]


def _git_date(path: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%ai", "--", str(path)],
            cwd=REPO,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            return out.split()[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _infer_category(text: str) -> str:
    t = text.lower()
    for kw, cat in CATEGORY_KEYWORDS:
        if kw in t:
            return cat
    return "Monitoring"


def _parse_script_meta(path: Path) -> dict[str, Any]:
    phase_m = PHASE_SCRIPT_RE.search(path.name)
    phase = phase_m.group(1) if phase_m else path.stem.replace("run_phase", "")
    title = path.stem.replace("run_phase", "Phase ").replace("_", " ")
    purpose = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 2 and lines[0].startswith('"""'):
            doc = []
            for line in lines[1:]:
                if line.strip().endswith('"""'):
                    doc.append(line.replace('"""', ""))
                    break
                doc.append(line)
            doc_text = "\n".join(doc).strip()
            if doc_text:
                purpose = doc_text.split("\n")[0][:200]
                if len(doc_text.split("\n")) > 1:
                    title = doc_text.split("\n")[0][:120]
    except OSError:
        pass
    return {
        "phase": phase,
        "script": path.name,
        "title": title,
        "purpose": purpose or f"Script {path.name}",
        "date": _git_date(path),
        "category": _infer_category(path.stem + " " + purpose),
    }


def _find_summary_json(phase: str) -> Path | None:
    candidates = sorted(REPORTS.glob(f"phase{phase}_*summary.json"))
    if candidates:
        return candidates[0]
    candidates = sorted(REPORTS.glob(f"phase{phase}_*.json"))
    return candidates[0] if candidates else None


def _extract_verdict_from_json(data: dict[str, Any]) -> tuple[str, str, str]:
    """Return (adoption_status, verdict_text, summary_snippet)."""
    for key in ("conclusion", "verdict", "forward_summary", "required_answers"):
        block = data.get(key)
        if isinstance(block, dict):
            for k in (
                "recommendation",
                "adoption_verdict",
                "maintain_production_stack",
                "production_adoption_ok",
                "note",
            ):
                if k in block:
                    val = block[k]
                    if isinstance(val, bool):
                        if val:
                            return "adopted", str(val), str(val)
                        return "rejected", str(val), str(val)
                    if isinstance(val, dict):
                        av = val.get("adoption_verdict") or val.get("verdict")
                        if av:
                            return str(av), str(av), json.dumps(val, ensure_ascii=False)[:200]
                    s = str(val)
                    low = s.lower()
                    if "maintain" in low or "adopt" in low or "production" in low:
                        return "adopted", s, s[:200]
                    if "reject" in low or "do not" in low:
                        return "rejected", s, s[:200]
                    if "observe" in low:
                        return "observe", s, s[:200]
                    return "observe", s, s[:200]
    if data.get("adoptable") is False or data.get("adopt_not_allowed") is True:
        return "observe", "adopt_not_allowed", "Forward sample insufficient or gates not met"
    if data.get("adoptable") is True:
        return "adopted", "adoptable", "Passes adoption gate in report"
    return "observe", "research_complete", "Report generated; no runtime adoption"


def _classify_row(meta: dict[str, Any]) -> dict[str, Any]:
    phase = meta["phase"]
    stem = meta["script"]
    title = meta["title"]
    category = meta["category"]
    sub_category = "script"
    runtime_status = "research"
    shadow_status = "none"
    research_status = "active"
    current_status = "inactive"
    adoption_status = "observe"
    removed_or_disabled = "false"
    related_phases = ""
    related_reports = ""
    summary = meta["purpose"]
    verdict = "Research / review artifact"
    date = meta["date"] or ""

    summary_path = _find_summary_json(phase)
    if summary_path:
        related_reports = str(summary_path.relative_to(REPO)).replace("\\", "/")
        sj = _load_json(summary_path)
        if not date and sj.get("generated_at"):
            date = str(sj["generated_at"])[:10]
        av, vt, sm = _extract_verdict_from_json(sj)
        if adoption_status == "observe":
            adoption_status = av
        verdict = vt
        if sm:
            summary = sm

    if phase in RUNTIME_ADOPTED:
        o = RUNTIME_ADOPTED[phase]
        runtime_status = "runtime"
        research_status = "none"
        current_status = "active"
        adoption_status = "adopted"
        category = o.get("category", category)
        title = o.get("title", title)
        related_phases = o.get("related", "")
        verdict = "Production runtime (Stack C)"
        shadow_status = "counterfactual" if phase == "332" else shadow_status

    if phase in ACTIVE_FORWARD_SHADOW:
        o = ACTIVE_FORWARD_SHADOW[phase]
        runtime_status = "shadow"
        shadow_status = "active"
        research_status = "active"
        current_status = "active"
        adoption_status = "observe"
        title = o.get("title", title)
        related_phases = o.get("related", "")
        verdict = "Forward shadow logging; no runtime trading change"

    if phase in REJECTED:
        o = REJECTED[phase]
        runtime_status = "research"
        current_status = "inactive"
        adoption_status = "rejected"
        category = o.get("category", category)
        title = o.get("title", title)
        verdict = "Evaluated and rejected for production"

    if phase in SUPERSEDED:
        o = SUPERSEDED[phase]
        runtime_status = "removed"
        current_status = "removed"
        adoption_status = "superseded"
        removed_or_disabled = "true"
        title = o.get("title", title)
        related_phases = o.get("superseded_by", "")
        verdict = f"Superseded by Phase {o.get('superseded_by', '')}"

    # Collision handling: older workstreams on same phase number
    if phase == "262" and "slot_occupation" in stem:
        adoption_status = "superseded"
        current_status = "inactive"
        verdict = "Superseded by risk sizing forward shadow (262-Risk-Aware-Sizing)"
    if phase == "263" and "max_concurrent" in stem:
        adoption_status = "superseded"
        current_status = "inactive"
    if phase == "266" and "quality_replacement" in stem:
        adoption_status = "observe"
        verdict = "Review-only quality→score gate study; not applied to runtime"
    if phase == "267" and "v2_gate" in stem:
        adoption_status = "superseded"
        current_status = "inactive"
        verdict = "Superseded by equity curve shadow Phase267 (capital path)"
    if phase == "268" and "daily_runner" in stem:
        adoption_status = "superseded"
    if phase == "270" and "fast_paper" in stem:
        adoption_status = "superseded"
        verdict = "Superseded by equity bucket recommendation Phase270"
    if phase == "273" and "entry_score" in stem:
        adoption_status = "superseded"
    if phase == "255" and "price_floor" in stem:
        adoption_status = "superseded"
        verdict = "Superseded by sector heat forward shadow Phase255"

    # Capital/sizing research band 246-274 (except adopted shadows)
    try:
        n = int(re.sub(r"[a-z]", "", phase) or "0")
    except ValueError:
        n = 0
    if 246 <= n <= 274 and runtime_status not in ("runtime", "shadow"):
        if "forward_shadow" in stem or "equity_curve" in stem or "reconciliation" in stem:
            research_status = "active"
        else:
            research_status = "active"
        if adoption_status == "observe" and n >= 267:
            adoption_status = "observe"

    if 374 <= n <= 389:
        research_status = "active"
        category = category if category != "Monitoring" else "Sizing"
        if n in (388, 389):
            adoption_status = "observe"
            verdict = "Live capital candidate research; runtime CAP still 3"

    if "shadow" in stem and runtime_status == "research":
        shadow_status = "active" if current_status == "active" else "inactive"
        runtime_status = "shadow"

    if not date:
        date = "undated"

    return {
        "phase": phase,
        "date": date,
        "category": category,
        "sub_category": sub_category,
        "title": title,
        "purpose": meta["purpose"],
        "runtime_status": runtime_status,
        "shadow_status": shadow_status,
        "research_status": research_status,
        "current_status": current_status,
        "adoption_status": adoption_status,
        "removed_or_disabled": removed_or_disabled,
        "related_phases": related_phases,
        "related_reports": related_reports,
        "summary": summary[:500],
        "verdict": verdict[:300],
    }


def _collect_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(SCRIPTS.glob("run_phase*.py")):
        meta = _parse_script_meta(path)
        rows.append(_classify_row(meta))
    rows.extend(NON_PHASE_RUNTIME)
    # de-dupe by phase+title keeping first
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in rows:
        key = f"{r['phase']}|{r['title']}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (_phase_sort_key(r["phase"]), r["date"], r["phase"]))
    return unique


def _phase_sort_key(phase: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]?|343p)?", phase, re.I)
    if not m:
        return (99999, phase)
    num = int(m.group(1))
    suffix = m.group(2) or ""
    return (num, suffix)


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)


def _filter(rows: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    out = rows
    for k, v in kwargs.items():
        if isinstance(v, tuple):
            out = [r for r in out if r.get(k) in v]
        else:
            out = [r for r in out if r.get(k) == v]
    return out


def _write_md(rows: list[dict[str, Any]], path: Path) -> None:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    runtime_rows = _filter(rows, runtime_status="runtime", current_status="active", adoption_status="adopted")
    shadow_fwd = [r for r in rows if r["phase"] in ACTIVE_FORWARD_SHADOW and r["current_status"] == "active"]
    rejected = _filter(rows, adoption_status="rejected")
    superseded = _filter(rows, adoption_status="superseded")
    observe_shadow = _filter(rows, adoption_status="observe", runtime_status="shadow", current_status="active")

    lines = [
        "# KabuStation Full Phase History Audit",
        "",
        f"Generated: {now}",
        "",
        "Source: git history, configs, src, scripts, tests, reports (Phase001+).",
        "Constraint: audit-only — no Runtime/Universe/Entry/Exit/YAML changes.",
        "",
        "---",
        "",
        "# Executive Summary",
        "",
        "## 現在の本番スタック (Stack C — paper observer)",
        "",
        "Config: `kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`",
        "",
        "| Layer | Phase | 内容 |",
        "|-------|-------|------|",
        "| Universe | 113/117/269 | volatility_liquidity_top50 + core10-dynamic40-price-risk AM/PM refresh |",
        "| Entry | 267/314 | entry_score_v2_min=3; quality reject off; Momentum+Board score |",
        "| Entry | 153b | entry_price_risk_guard reject |",
        "| Entry | 355 | pullback_misread_dynamic40_guard (Dynamic40 only) |",
        "| Entry | 364 | near_day_high_low_momentum_dynamic40_guard (Dynamic40 only) |",
        "| Entry | NP | entry_scan freshness/batch guard |",
        "| Exit | 332 | board-dynamic trailing-MFE (high 1.0%/60%, low 0.6%/40%) |",
        "| Position | cap3 | max_concurrent_positions=3 (CAP=2 research only) |",
        "| Risk | — | daily_loss -2.5%, risk_cluster block, maintenance ratio sim |",
        "| Discord | 333/281 | canonical 100-share yen summary; cap-blocked channel |",
        "",
        "## 現在の Shadow スタック",
        "",
        "**Post-session forward shadow (pilot_runner auto):** 255/256, 262, 266, 273, 274",
        "",
        "**Inline session shadow (counterfactual / monitoring):** 332 legacy trailing, 355 pullback shadow,",
        "351 limit-up, 335 realtime board exit, 214 board imbalance, 186 vwap, 230 entry expectancy, etc.",
        "",
        "## 現在の Research スタック",
        "",
        "Active capital path: Phase267–274 forward equity curves (observe until day_count≥10).",
        "Capital scaling: Phase374–389 (CAP sensitivity; 388/389 recommend 1.5M start, CAP=2 not runtime).",
        "Sector heat pipeline: Phase246–254 (feeds Phase255 forward shadow).",
        "",
        "---",
        "",
        "# Runtime Stack (latest only)",
        "",
        "## Universe",
        "- core10 + dynamic40 price-risk filter (`--universe-mode core10-dynamic40-price-risk-filter-shadow`)",
        "- daytrade_suitability_rule: volatility_liquidity_top50",
        "- Intraday refresh 10:00 / 14:30 (Phase148); legacy Phase114 12:25 regen **superseded**",
        "",
        "## Entry",
        "- entry_score_v2_min: 3 (Phase267/314)",
        "- reject_below_quality: false",
        "- enable_pullback_misread_dynamic40_guard: true (Phase355)",
        "- enable_near_day_high_low_momentum_dynamic40_guard: true (Phase364)",
        "- entry_price_risk_guard + entry_scan freshness",
        "",
        "## Exit",
        "- structural_exit_policy: combined_structural_exit_v1_trailing_mfe_shadow (Phase332)",
        "- hard_stop 1.2%, overlap_replaced, session_end preserved",
        "- momentum_fade / quality_decay / fixed 0.8%/50% trailing: **removed or shadow-only**",
        "",
        "## Position",
        "- max_concurrent_positions: 3",
        "- CAP=2 (Phase387/388/389): research_candidate only",
        "",
        "## Risk",
        "- daily_loss_guard_pct: -2.5%",
        "- risk_cluster_consecutive_losses: 5",
        "- Phase355/364 Dynamic40 entry guards",
        "",
        "## Discord",
        "- canonical_summary (100-share yen primary)",
        "- trade notify + cap-blocked webhook (Phase281 family)",
        "- Research shadow blocks: SectorHeat, RiskSizing, EquityDynamicStop, LiveConfig, Transition",
        "",
        "## YAML",
        "- Production: `small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`",
        "- shadow_only: true, order_enabled: false, paper_only: true",
        "",
        "---",
        "",
        "# Active Shadow Stack",
        "",
    ]
    for r in shadow_fwd:
        report = r.get("related_reports") or "—"
        lines.append(f"- **Phase{r['phase']}** ({r['date']}): {r['title']} — `{report}`")
    lines.extend(["", "---", "", "# Rejected / Deprecated Logic", ""])
    lines.append("## 不採用 (rejected)")
    for r in sorted(rejected, key=lambda x: _phase_sort_key(x["phase"]))[:40]:
        lines.append(f"- Phase{r['phase']}: {r['title']} — {r['verdict']}")
    lines.extend(["", "## 削除・置換 (superseded/removed)", ""])
    for r in sorted(superseded, key=lambda x: _phase_sort_key(x["phase"]))[:30]:
        lines.append(
            f"- Phase{r['phase']}: {r['title']} → superseded by Phase {r.get('related_phases','')}"
        )
    lines.extend(
        [
            "",
            "Other removed/disabled:",
            "- virtual_hold_expired / live_virtual_hold official exits forbidden",
            "- 300s virtual hold PF (legacy appendix only)",
            "- TAKE as EXIT rejected (Phase54)",
            "- Fade breakdown/hybrid/watch production EXIT paths (Phase166 family — shadow configs only)",
            "",
            "---",
            "",
            "# Phase Timeline (milestones)",
            "",
            "| Era | Phases | Note |",
            "|-----|--------|------|",
            "| 2026-05-06 | first commit | screening / discord foundation |",
            "| 2026-05-18 | 55 | small paper observer runtime |",
            "| 2026-05 | 113–174 | universe, gates, trailing-MFE shadow |",
            "| 2026-06 early | 200–245 | entry score / validation framework |",
            "| 2026-06 mid | 246–274 | sector heat + capital shadow + live config forward |",
            "| 2026-06-13 | 317–373 | 6/12 incident review; Stack C adoption |",
            "| 2026-06-14 | 374–389 | capital scaling research; 1.5M live candidate |",
            "",
            f"Full inventory: {len(rows)} rows in `docs/audits/full_phase_history_audit.csv`.",
            "",
            "---",
            "",
            "# 6/12 Incident Review",
            "",
            "## 採用 (production runtime)",
            "",
            "| Phase | 改善 | 根拠 |",
            "|-------|------|------|",
            "| 332 | Board-dynamic trailing EXIT | phase332 production_adoption_ok |",
            "| 355 | Pullback misread Dynamic40 guard | phase365 maintain Phase355+364 |",
            "| 364 | Near day-high low-momentum guard | phase363 production_candidate; phase365 maintain |",
            "| 333 | Canonical 100-share yen summary | kabutrade0612 |",
            "| NP | Entry scan / freshness guard | kabutrade0612 YAML |",
            "| 281 | CAP-blocked Discord channel | kabutrade0612 |",
            "",
            "## 不採用 (6/12 analysis)",
            "",
            "| Phase | 内容 | 理由 |",
            "|-------|------|------|",
            "| 351/352 | Limit-up proximity guard | shadow/research only |",
            "| 359 | Gap-up fade guard | shadow only |",
            "| 368 | Symbol reentry cluster | do not adopt |",
            "| 370 | K10 stop-chain A1 | rejected |",
            "| 371 | High-MFE stop_hit recovery | shadow only |",
            "| 342–347 | Board failure exit | research/shadow only |",
            "| 337–341 | Exit candidate / VWAP tuning | not production |",
            "| 362-B | C03 all symbols | Phase365 chose Dynamic40-only (364) |",
            "",
            "---",
            "",
            "# Current Production Verdict",
            "",
            "## 現在の本番構成",
            "Stack C: Phase332 EXIT + Phase267/314 ENTRY score + Phase355/364 guards + cap3 + top50 universe.",
            "",
            "## 現在の推奨ライブ構成 (research — not runtime reflected)",
            "- Phase272/273: **eq1500k_lev2p0_cap3_fixed_stop_1p2** (live start)",
            "- Scale at 2M+: **eq2000k_lev2p0_cap5_dynamic_stop_risk_1p0**",
            "- Phase274 forward shadow: auto-transition at equity≥2M (observe, day_count<10)",
            "- Phase389: 150万円運用推奨; CAP=2は research のみ",
            "",
            "## 最大未解決課題 (上位10)",
            "",
            "1. **CAP=2 vs CAP=3** — Phase387/388/389 positive at 1.5M research; runtime still cap3",
            "2. **Forward shadow sample** — Phase273/274 need ≥10 days before adopt_not_allowed clears",
            "3. **lev1.5 non-robust** — Phase271 fixed lev2.0; Phase272 supersedes Phase270 leverage mix",
            "4. **Period A losses** — Phase377/389 regime monitoring ongoing",
            "5. **Low-MFE stop_hit** — Phase358/372/379 forensic; no production fix adopted",
            "6. **Replay fidelity** — Phase381: board/PUSH dependent exits; Yahoo replay gap",
            "7. **Dynamic40 alpha dependency** — Phase374/375: rank_21_40 profit source",
            "8. **6/12 AM loss** — guards reduce loss but AM still negative in isolation",
            "9. **Research PF vs live equity** — Phase268: static PF must not drive adoption",
            "10. **Universe refresh vs alpha** — core10 untouched by guards; dynamic40 carries guard + alpha",
            "",
            "---",
            "",
            "# Future Work",
            "",
            "## 現在進行中 Shadow",
            "- Phase255 SectorHeat forward (accumulating)",
            "- Phase262 Risk-aware sizing forward",
            "- Phase266 Equity dynamic stop forward",
            "- Phase273 Live config bucket forward",
            "- Phase274 Auto-transition 1.5M→2M band forward",
            "- Phase387 CAP=2 shadow monitoring",
            "",
            "## 採用待ち Shadow",
            "- Phase273/274: await day_count≥10 + final_equity gates",
            "- Phase388/389: CAP=2 at 1.5M if live sessions confirm",
            "",
            "## 優先順位",
            "1. Complete forward shadow observation window (273/274/266)",
            "2. Live-session validation of CAP=2 at 1.5M (388)",
            "3. Period A loss monitoring (377/389)",
            "4. Low-MFE stop_hit mitigation research (379) — no runtime until robust",
            "5. Sector heat negative filter adoption decision (254→255 data)",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    rows = _collect_rows()
    csv_path = AUDITS / "full_phase_history_audit.csv"
    md_path = REPORTS / "full_phase_history_report.md"
    _write_csv(rows, csv_path)
    _write_md(rows, md_path)
    print(f"rows={len(rows)}")
    print(f"csv: {csv_path}")
    print(f"md: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

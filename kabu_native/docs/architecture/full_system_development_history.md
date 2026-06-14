# TradeBot System Source of Truth **v5**

**監査可能な System Evolution Source of Truth** — 履歴集ではなく runtime 世代・採用 funnel・依存関係を監査する正本。
**Canonical MD:** `kabu_native/docs/architecture/full_system_development_history.md`
**Canonical CSV:** `kabu_native/docs/audits/full_phase_history_audit.csv`
**Canonical PDF:** `kabu_native/docs/architecture/full_system_development_history.pdf`
**Generated:** 2026-06-14 21:53 JST | **Generator:** `scripts/run_full_system_development_history.py` (Phase391)
**Audit rows:** 330 + genesis | **Runtime generation:** 9 (Current)

**Satellite docs:** `runtime_change_log.md` · `runtime_dependency_graph.md` · `runtime_adoption_funnel.md`

---

# Production Stack Definition

## Runtime (Current)

### Universe

- core10-dynamic40-price-risk-filter-shadow + vol-liq top50 *(Phase 113, 117, 269, 148)*

### Entry

- entry_score_v2_min=3 (Momentum:low + Board:mid); quality reject off *(Phase 314, 267)*
- entry_price_risk_guard *(Phase 153b)*
- pullback_misread_dynamic40_guard (Dynamic40 only) *(Phase 355)*
- near_day_high_low_momentum_dynamic40_guard (Dynamic40 only) *(Phase 364)*
- entry_scan freshness / batch guard *(Phase NP-entry-scan)*

### Exit

- board-dynamic trailing-MFE (high 1.0%/60%, low 0.6%/40%) *(Phase 332)*
- hard_stop 1.2%, overlap_replaced, session_close *(Phase structural v1)*

### Position

- max_concurrent_positions=3 (100-share observer) *(Phase q070_cap3)*
- runtime cap=3; CAP=2 research only *(Phase 388, 389, 387)*

### Risk

- daily_loss -2.5%, risk_cluster block, maintenance ratio sim *(Phase YAML)*

### Discord

- canonical 100-share yen summary + cap-blocked webhook *(Phase 333, 281)*

### Monitoring

- AM/PM daily runner, preflight 317, post-close 376/377/373 *(Phase 148, 317, 376)*

### Shadow

- forward post-session: 255/256, 262, 266, 273, 274 *(Phase 255, 262, 266, 273, 274)*

### Research

- capital path 267–274 forward; scaling 374–389 (runtime 未反映) *(Phase 272, 273, 388)*

### Live Candidate

- eq1500k_lev2p0_cap3_fixed_stop_1p2 → 2M+ CAP5 dynamic (research) *(Phase 272, 274)*

**Config:** `small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`  
**Mode:** paper_only · shadow_only · order_enabled=false

---

# Current Production Truth

**唯一の正** — 本番 runtime（Stack C）。矛盾時は本表を優先。

| Layer | Current Truth | Source Phase |
| --- | --- | --- |
| Universe | core10-dynamic40-price-risk-filter-shadow + vol-liq top50 | 113, 117, 269, 148 |
| Entry | entry_score_v2_min=3 (Momentum:low + Board:mid); quality reject off | 314, 267 |
| Entry | entry_price_risk_guard | 153b |
| Entry | pullback_misread_dynamic40_guard (Dynamic40 only) | 355 |
| Entry | near_day_high_low_momentum_dynamic40_guard (Dynamic40 only) | 364 |
| Entry | entry_scan freshness / batch guard | NP-entry-scan |
| Exit | board-dynamic trailing-MFE (high 1.0%/60%, low 0.6%/40%) | 332 |
| Exit | hard_stop 1.2%, overlap_replaced, session_close | structural v1 |
| Position | max_concurrent_positions=3 (100-share observer) | q070_cap3 |
| CAP | runtime cap=3; CAP=2 research only | 388, 389, 387 |
| Risk | daily_loss -2.5%, risk_cluster block, maintenance ratio sim | YAML |
| Discord | canonical 100-share yen summary + cap-blocked webhook | 333, 281 |
| Monitoring | AM/PM daily runner, preflight 317, post-close 376/377/373 | 148, 317, 376 |
| Shadow | forward post-session: 255/256, 262, 266, 273, 274 | 255, 262, 266, 273, 274 |
| Research | capital path 267–274 forward; scaling 374–389 (runtime 未反映) | 272, 273, 388 |
| Live Candidate | eq1500k_lev2p0_cap3_fixed_stop_1p2 → 2M+ CAP5 dynamic (research) | 272, 274 |

---

# Documentation Governance

**必須:** 採用・不採用・置換・削除が発生したら **両方** を更新する。

| Step | Artifact | Path |
| --- | --- | --- |
| 1 | Audit CSV | `kabu_native/docs/audits/full_phase_history_audit.csv` |
| 2 | Source of Truth MD | `kabu_native/docs/architecture/full_system_development_history.md` |
| 3 | PDF (optional) | `kabu_native/docs/architecture/full_system_development_history.pdf` |

| Event | Required updates |
| --- | --- |
| **採用** | audit overrides → CSV → PRODUCTION_TRUTH → regenerate MD |
| **不採用** | audit REJECTED → CSV → regenerate MD (+ Failure/Misconception if material) |
| **置換** | audit SUPERSEDED → CSV → regenerate MD |
| **削除** | audit removed → CSV → regenerate MD |

**Workflow:**

1. `python scripts/run_full_phase_history_audit.py`
2. Update script constants if runtime changed (PRODUCTION_TRUTH, EVIDENCE_BY_PHASE, …)
3. `python scripts/run_full_system_development_history.py`
4. `python tools/md_to_pdf.py kabu_native/docs/architecture/full_system_development_history.md`

| 区分 | 正本パス | 内容 |
| --- | --- | --- |
| 恒久 | `docs/architecture/` | **本書** (SoT MD/PDF) |
| 恒久 | `docs/audits/` | **full_phase_history_audit.csv** |
| 一時 | `results/reports/` | Phase 検証 snapshot のみ |

---

# Key Milestones

330 Phase を読む前に把握する重要イベント（CSV + curated reasons）。

| Phase | Date | Title | Reason | Current Status |
| --- | --- | --- | --- | --- |
| Phase55 | 2026-05-18 | kabu PUSH paper observer を主 runtime に | kabu PUSH paper observer を主 runtime に | active |
| Phase113 | 2026-05-27 | Daytrade suitability top50 rule | vol-liq top50 universe 採用 | active |
| Phase117 | 2026-05-27 | Volatility liquidity universe | volatility-liquidity universe 基盤 | active |
| Phase148 | 2026-05-27 | AM/PM daily runner orchestration | AM/PM 10:00/14:30 intraday refresh | active |
| Phase153b | 2026-05-27 | Entry price risk guard (min price / tick ratio) | entry price risk guard 本番化 | active |
| Phase174 | 2026-05-30 | Fixed trailing MFE 0.8%/50% | fixed trailing MFE shadow（後に332置換） | removed |
| Phase267 | 2026-06-14 | entry_score_v2 gate (min=3); quality reject off | quality reject off + score_v2 min=3 | active |
| Phase314 | 2026-06-07 | Entry score v2 simplification (Momentum+Board only) | entry score v2 簡素化（2 token） | active |
| Phase332 | 2026-06-13 | Board-dynamic trailing-MFE production EXIT | board-dynamic trailing EXIT 本番化 | active |
| Phase333 | 2026-06-13 | Canonical 100-share yen summary | canonical 100-share yen summary | active |
| Phase355 | 2026-06-13 | Pullback misread Dynamic40 guard | 6/12 Dynamic40 pullback guard | active |
| Phase364 | 2026-06-13 | 6/12 near-day-high low-mom D40 guard | 6/12 near-day-high low-mom D40 guard | active |
| Phase365 | 2026-06-13 | Phase 365 production stack validation | maintain Stack C (355+364) 確定 | observe |
| Phase272 | 2026-06-14 | Phase 272 apply leverage robustness to equity bucket recommendation | lev2.0 fixed; 150万 CAP3 research recommend | observe |
| Phase273 | 2026-06-04 | Live config forward shadow (Phase272 configs) | live config forward shadow（observe） | superseded |
| Phase274 | 2026-06-14 | Live config auto-transition shadow (1.5M→2M band) | auto 1.5M→2M transition shadow（observe） | observe |
| Phase388 | 2026-06-14 | Phase 388 cap1500k live candidate validation | 1.5M live candidate validation | observe |
| Phase389 | 2026-06-14 | Phase 389 full regime live candidate validation | full-regime live candidate; CAP=2 research | observe |

---

# Runtime Diff History

Appendix B とは別。**いつ何が変わったか** を時系列で把握。

| Date | Runtime Name | Universe | Entry | Exit | CAP | Major Change |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-18 | Observer v1 | pre top50 | quality + structural | stop/session/overlap | 3 | Phase55 small paper observer 開始 |
| 2026-05-29 | Core10 + Dynamic40 v1 | 113/117 top50 | quality≥0.70 + price risk | structural + fade trials | 3 | two-layer universe; fade EXIT 試行 |
| 2026-06-04 | Trailing Shadow | core10+d40 + AM/PM(148) | quality + score shadow | Phase174 fixed 0.8%/50% shadow | 3 | trailing-MFE shadow policy 導入 |
| 2026-06-07 | Score v2 Transition | core10+d40 price-risk | Phase314 score_v2≥3 | fixed trailing + structural | 3 | v1 多因子から 2-token score へ |
| 2026-06-09 | Pre-332 Runtime | core10+d40 price-risk | score_v2 + 153b | Phase332 replay OK (YAML pending) | 3 | board-dynamic EXIT 採用判定 OK |
| 2026-06-12 | 6/12 Incident Runtime | core10+d40 price-risk | score_v2 + 153b; 355/364 **off** | 174 legacy or 332 transition | 3 | **6/12 AM incident** — D40 losses |
| 2026-06-13 | Stack C Production | 113/117/269 + refresh | 267/314 + 355 + 364 + freshness | Phase332 board-dynamic | 3 | kabutrade0612 — guards + 333/281 |
| 2026-06-14 | Current Runtime | Stack C unchanged | Stack C unchanged | Stack C unchanged | 3 | forward shadow 273/274; CAP=2 research only |

---

# Runtime Evolution Audit (Phase391)

**Current runtime generation:** 9 (Current)
**Production stack:** Stack C | **CAP=2:** Research only (runtime cap3)

---

# Runtime Change Log

| Date | Runtime Version | Universe | Entry | Exit | CAP | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-06 | Genesis | manual CSV / screening | prototype gate | not integrated | — | first commit; Discord / screening foundation |
| 2026-05-18 | Observer v1 | pre top50 | quality + structural | stop/session/overlap | 3 | Phase55 small paper observer |
| 2026-05-29 | Core10+Dynamic40 v1 | 113/117 top50 | quality≥0.70 + price risk | structural + fade trials | 3 | two-layer universe |
| 2026-06-04 | Trailing Shadow | core10+d40 + AM/PM(148) | quality + score shadow | Phase174 fixed 0.8%/50% shadow | 3 | trailing-MFE shadow policy |
| 2026-06-07 | ScoreV2 Transition | core10+d40 price-risk | Phase314 score_v2≥3 | fixed trailing + structural | 3 | depart v1 multi-factor score |
| 2026-06-09 | Pre-332 | core10+d40 price-risk | score_v2 + 153b | Phase332 replay OK (YAML pending) | 3 | board-dynamic EXIT adoption OK |
| 2026-06-12 | 6/12 Incident Runtime | core10+d40 price-risk | score_v2 + 153b; guards off | 174 legacy or 332 transition | 3 | 6/12 AM Dynamic40 losses |
| 2026-06-13 | Stack C | 113/117/269 + refresh | 267/314 + 355 + 364 + freshness | Phase332 board-dynamic | 3 | kabutrade0612 recovery commit |
| 2026-06-14 | Current | Stack C unchanged | Stack C unchanged | Stack C unchanged | 3 | forward shadows; CAP=2 research only |

---

# Stack Evolution

| Stack | Period | Universe | Entry | Exit | CAP | Adopt Reason | Replace/Retire Reason | Current Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stack A | 20260518–20260612 (research counterfactual) | core10+d40 price-risk (observed trades) | score_v2 + price risk; **no guards** | trailing-MFE + stop 1.2% | 3 | Phase365 baseline for guard delta measurement | superseded by Stack B/C guard analysis | research baseline |
| Stack B | 20260518–20260612 (research counterfactual) | same as A | A + Phase355 pullback D40 guard only | same as A | 3 | Phase355 isolated effect (+100k vs A) | superseded by Stack C (+364 incremental) | research superseded |
| Stack C | 2026-06-13–present (production) | 113/117/269 + AM/PM refresh | 267/314 + 355 + 364 + freshness + 153b | Phase332 board-dynamic + structural | 3 | Phase365 maintain; +483k vs baseline | — (current production) | **active production** |

---

# Runtime Dependency Graph

```
Stack C (production)
├── Universe
│   ├── Phase113
│   ├── Phase117
│   ├── Phase148
│   └── Phase269
├── Entry
│   ├── Phase153b
│   ├── Phase267
│   ├── Phase314
│   ├── Phase355
│   ├── Phase364
│   └── NP-entry-scan
├── Exit
│   ├── Phase332
│   └── structural v1
├── Position
│   └── q070_cap3
├── Risk
│   ├── YAML daily_loss
│   └── risk_cluster
├── Discord
│   ├── Phase281
│   └── Phase333
├── Monitoring
│   ├── Phase55
│   ├── Phase148
│   ├── Phase317
│   ├── Phase376
│   ├── Phase377
│   └── Phase373
├── Shadow
│   ├── Phase255
│   ├── Phase256
│   ├── Phase262
│   ├── Phase266
│   ├── Phase273
│   ├── Phase274
│   └── Phase387
└── Research
    ├── Phase272
    ├── Phase273
    ├── Phase274
    ├── Phase388
    └── Phase389

CAP=2: Phase387/388/389 → Research branch (not production runtime)
```

---

# Adoption Funnel

## Overall Status

| Status | Count |
| --- | --- |
| Adopted | 21 |
| Rejected | 8 |
| Removed | 5 |
| Research | 289 |
| Shadow | 4 |
| Superseded | 4 |
| **Total** | 331 |

## By Category

| Category | Total | Adopted | Rejected | Shadow | Research | Superseded | Removed | Observe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Universe | 21 | 3 | 1 | 0 | 15 | 1 | 1 | 0 |
| Entry | 41 | 5 | 4 | 0 | 31 | 1 | 0 | 0 |
| Exit | 24 | 1 | 2 | 0 | 21 | 0 | 0 | 0 |
| Position | 22 | 1 | 0 | 0 | 20 | 1 | 0 | 0 |
| Risk | 5 | 0 | 0 | 1 | 4 | 0 | 0 | 0 |
| Sizing | 10 | 0 | 0 | 1 | 9 | 0 | 0 | 0 |
| Capital | 2 | 0 | 0 | 0 | 1 | 0 | 1 | 0 |
| Discord | 8 | 3 | 0 | 0 | 5 | 0 | 0 | 0 |
| Monitoring | 165 | 7 | 1 | 0 | 153 | 1 | 3 | 0 |
| Data | 19 | 1 | 0 | 1 | 17 | 0 | 0 | 0 |
| Replay | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 0 |
| Documentation | 4 | 0 | 0 | 1 | 3 | 0 | 0 | 0 |
| **合計** | 331 | 21 | 8 | 4 | 289 | 4 | 5 | 0 |

---

# Adopted Then Removed

| Phase | Adopted Date | Removed Date | Replacement | Reason |
| --- | --- | --- | --- | --- |
| Phase114 | 2026-05-27 | 2026-05-29 | Phase148 | 12:25 PM regen → 10:00/14:30 intraday refresh |
| Phase13 | 2026-05-17 | 2026-05-18 | Phase148 | no_entry_until 09:30 → session window management |
| Phase174 | 2026-06-04 | 2026-06-13 | Phase332 | fixed 0.8%/50% trailing → board-dynamic trailing |
| Phase255 | 2026-06-04 | 2026-06-04 | 253,254,256 | Superseded by sector heat forward shadow Phase255 |
| Phase263 | 2026-06-04 | 2026-06-04 | — | At 5M yen, dynamic_stop_risk_0p5 improves total shadow PnL vs fixed -1.2%. |
| Phase268 | 2026-06-04 | 2026-06-04 | — | research_complete |
| Phase270 | 2026-06-14 | 2026-06-14 | Phase272 | mixed leverage bucket → lev2.0 fixed |
| Phase271 | 2026-06-14 | 2026-06-14 | Phase272 | lev1.5 non-robust on 9-day sample |
| Phase273 | 2026-06-04 | 2026-06-14 | Phase274 | static bucket shadow superseded by auto-transition shadow |

---

# Runtime Delta Timeline

| Date | Added | Removed | Replaced |
| --- | --- | --- | --- |
| 2026-05-18 | Phase55 observer | — | — |
| 2026-05-29 | Phase113/117 top50 | — | — |
| 2026-06-04 | Phase174 trailing shadow | — | — |
| 2026-06-07 | Phase314 score_v2 | quality≥0.70 reject (267 path) | — |
| 2026-06-09 | Phase332 EXIT (replay OK) | — | — |
| 2026-06-12 | — | — | — (incident; guards not yet applied) |
| 2026-06-13 | Phase355, Phase364, Phase333, Phase281, NP-scan | Phase174 production trailing | Phase174 → Phase332 |
| 2026-06-14 | Phase273/274 forward shadow hooks | — | Phase270/271 → Phase272 (research) |

---

# Current Runtime Provenance

| Component | Phase | Adoption Date | Evidence |
| --- | --- | --- | --- |
| Universe top50 | 113, 117 | 2026-05-27 | Production runtime Stack C |
| Core10+Dynamic40 price-risk | 269, 148 | 2026-05-29 | AM/PM refresh + price-risk filter |
| Entry score v2 | 314, 267 | 2026-06-07 | Rule reduction 2-token; quality reject off |
| Price risk guard | 153b | 2026-05-27 | YAML entry_price_risk_guard |
| Pullback guard | 355 | 2026-06-13 | +100,400 yen vs baseline (Phase365) |
| Near day-high guard | 364 | 2026-06-13 | +140,200 yen 6/12 replay (Phase363) |
| Board dynamic exit | 332 | 2026-06-13 | production_adoption_ok=true |
| CAP3 | q070_cap3 | 2026-05-18 | runtime max_concurrent_positions=3 |
| CAP2 | 388, 389, 387 | 2026-06-14 | **Research only** — runtime cap3 maintained |
| Canonical summary | 333 | 2026-06-13 | kabutrade0612 canonical 100-share yen |
| Cap-blocked Discord | 281 | 2026-06-13 | Discord channel split |


---

# Introduction

## システム目的

kabuステーション® API を一次データ源とし、**実発注なし（observer only）** で ENTRY/EXIT/Universe/資金制約を検証する自動売買研究プラットフォーム。旧 Yahoo 非公式 API 系（`market/yahoo/`）とは別系統で、`kabu_native/` に universe・朝スクリーン・リプレイ・small paper observer・shadow 蓄積・日次 runner を集約している。

最終目標は「live 前に capital path まで含めた再現可能な検証」を経て、将来の実運用構成を決めること。現時点では **performance guarantee ではなく runtime verification** に留まる。

## 開発開始日

**2026-05-06 01:55 JST** — `first commit`（screening / Discord 通知の原型）

## 現在の到達点（2026-06-14）

| 層 | 到達点 |
|----|--------|
| **Runtime（Stack C）** | Phase332 board-dynamic trailing EXIT + Phase314/267 entry_score_v2 + Phase355/364 Dynamic40 ENTRY guard + cap3 + core10-dynamic40 universe |
| **Shadow（forward）** | SectorHeat 255, RiskSizing 262, EquityDynamicStop 266, LiveConfig 273, Transition 274 — paper 終了後自動蓄積 |
| **Research（capital）** | Phase267–274 資産曲線 forward + Phase374–389 CAP/元本スケーリング（runtime 未反映） |
| **運用** | AM/PM daily runner（Phase148）、Tomorrow Preflight（317）、引け後 review（376/377） |
| **未完了** | PUSH JSONL 完全 normalized replay、CAP=2 runtime 採用、forward shadow 10日ゲート |

**このシステムが今の形になった理由（一行）:**  
Yahoo 依存の旧系から kabu PUSH ベース observer へ移行した後、**Period A の大敗**と **6/12 AM 事故**を契機に ENTRY を簡素化し EXIT を板連動 trailing に刷新、Dynamic40 限定 guard で損失構造を切り、なお capital path は research shadow で観測中である。

---

# Chronological Timeline

時系列は Git コミット・report `generated_at`・paper session 日付を統合。同一日に複数 Phase が並行する場合、依存関係順に記載。

---

## 2026-05-06 — 創世期（screening / Discord / entry 原型）

### Phase001相当（番号なし — first commit 群）

**目的:** kabu 連携の足場 — 銘柄スクリーニングと Discord 通知。

**背景:** 旧 Yahoo 監視ループとは別に、公式 API 系の新 pipeline を `tradebotfile` に立ち上げる必要があった。

**実装内容:** screening feature、Discord notice、entry 試行（`entry0507` 等の連続コミット）。

**検証結果:** 単体機能は動作。売買ロジック統合前。

**採用判定:** 基盤として **adopted**（後続すべての前提）。

**影響:** `kabu_native/src/screening/`, `src/notify/discord.py` 系の原点。

**後続:** Phase8–11（Logic Lab 前段の quality / replay 試行）。

**現在状態:** **active**（朝スクリーン・通知の土台）

---

## 2026-05-16〜17 — kabustation0516/0517（API・universe 基盤）

### Phase37–43（validation / gate diagnosis）

**目的:** 初期 gate と top-quartile 仮説の OOS 検証。

**背景:** entry quality だけでは足りず、ExposureGate の診断が必要だった。

**実装内容:** `run_phase43_gate_diagnosis.py`, Logic Lab 連携。

**検証結果:** gate 診断 CSV/JSON が `results/reports/` に蓄積開始。

**採用判定:** 診断基盤 **adopted**；当時の gate 閾値自体は後続で多数 **superseded**。

**現在状態:** Phase43 pass 要件（`require_phase43_pass: true`）は YAML に **active** 残存。

---

## 2026-05-18 — Phase55: Small Paper Observer Runtime

### Phase55

**目的:** Yahoo shadow ではなく **kabu PUSH ベースの paper observer** を主検証ランタイムにする。

**背景:** live 判断は PUSH/board に依存するが、旧 replay では再現できない（後に Risk S1 として formalize）。

**実装内容:** `ObserverPositionTracker`, `pilot_runner.py`, structural exit replay。

**検証結果:** Git 上唯一 Phase 番号付きコミットメッセージ（`Phase55 small paper observer runtime`）。

**採用判定:** **adopted** — 現行 Stack C の親世代。

**影響:** `small_paper/` ディレクトリが主戦場に。

**後続:** Phase45–148（observer 拡張）、Phase332（EXIT 刷新）。

**現在状態:** **active**（骨格は存続、中身は Stack C に置換済み）

---

## 2026-05-27 — kabutrade0527（Exit pathology 時代）

### Phase68–88, Phase71 ほか

**目的:** ENTRY/EXIT 変数監査、momentum fade exit、symbol cooloff、daytrade suitability。

**背景:** Period A 以前から stop_hit 偏重・fade exit 試行が乱立。

**実装内容:** `run_phase71_split_momentum_fade_review.py` 等 — 以後 **200 番台 research の共通 replay エンジン** になる。

**検証結果:** fade / momentum exit は PF 改善が不安定。

**採用判定:** fade 系 EXIT は **rejected**（production から排除、shadow config のみ残存）。

**影響:** 「EXIT を増やす」路線の失敗が記録開始。

**現在状態:** Phase71 engine は research 基盤として **active**；fade production path は **removed**

---

## 2026-05-29〜31 — 構造トレード期間の確立

### Phase113/117（daytrade suitability / vol-liq top50）

**目的:** Universe を流動性・ボラティリティで絞る。

**背景:** 全銘柄監視はノイズ過多。

**実装内容:** `daytrade_suitability_rule: volatility_liquidity_top50`。

**採用判定:** **adopted** — Stack C YAML に **active**。

### Phase148（AM/PM daily runner）

**目的:** 朝・昼の universe refresh と paper session 起動を自動化。

**背景:** Phase114 の 12:25 単発 regen では不十分。

**実装内容:** `run_core10_dynamic40_am_pm_daily_runner.py`, intraday refresh 10:00/14:30。

**採用判定:** **adopted**。

**後続:** Phase269 core10-dynamic40-price-risk-filter。

**現在状態:** **active** — Phase114 は **superseded**

---

## 2026-06-01〜04 — Trailing MFE shadow と Sector Heat 研究

### Phase174

**目的:** 固定 trailing MFE（activate 0.8%, giveback 50%）を structural EXIT shadow で検証。

**背景:** momentum_fade/quality_decay が不調のため、MFE ベース利確を試行。

**実装内容:** `combined_structural_exit_v1_trailing_mfe_shadow` policy。

**検証結果:** shadow では改善シグナル。board 情報未使用。

**採用判定:** 一時 **adopted**（shadow policy 名として production に載る）— 後 **Phase332 で置換**。

**現在状態:** **superseded** — legacy 0.8%/50% は counterfactual shadow のみ **active**

### Phase246–254（Sector Heat 研究線）

**目的:** セクター熱・negative filter の forward 検証。

**背景:** Universe/Entry の regime 依存を定量化したい。

**実装内容:** `market_sector_heat.py`, Phase255 forward logger 準備。

**採用判定:** runtime **rejected**；forward shadow **observe**。

**現在状態:** Phase255/256 forward shadow **active**（paper 終了後フック）

---

## 2026-06-07 — kabutrade0607（Entry score 深化）

### Phase213–245（entry score v1/v2 探索）

**目的:** entry_expectancy_score 系の因子分解・リーク監査。

**背景:** quality gate だけでは Period A を説明できない。

**検証結果:** 因子過多・リークリスク・過学習懸念。

**採用判定:** v1 複雑モデルは **superseded** by Phase314。

**現在状態:** research scripts **active**；runtime v1 gate は **removed**

---

## 2026-06-08〜11 — Entry Score v2 簡素化と Board Dynamic 準備

### Phase314

**目的:** ENTRY を **Momentum:low + Board:mid = 3** のみに簡素化。

**背景:** 「Phase 増加 = rule 堆積」という誤認への反証として、**runtime rule を減らす**判断。

**実装内容:** HBRecent/TV/Duration/Price を v2 判定から除外。`entry_score_v2_min: 3`。

**検証結果:** Period B で baseline より改善方向。汎化は監視継続。

**採用判定:** **adopted** — Stack C の ENTRY 中核。

**影響:** Phase267 で quality reject を off にし score gate 一本化。

**現在状態:** **active**

### Phase332（設計・replay 完了 → 6/13 production 反映）

**目的:** trailing MFE を **entry_imbalance_percentile** で board_high/low に分岐。

**背景:** 固定 0.8%/50% は board 強弱を無視。Phase330/331 full replay で優位。

**実装内容:** high: 1.0%/60%, low: 0.6%/40%。`board_dynamic_trailing_shadow.py` で legacy 反事実計測。

**検証結果:** `phase332_board_dynamic_trailing_production_adoption_report.json` → `production_adoption_ok: true`。

**採用判定:** **adopted**（2026-06-09 レポート、2026-06-13 runtime 反映）。

**現在状態:** **active**（本番 EXIT）

---

## 2026-06-12 — 6/12 Paper Trade（インシデント当日）

### 当日 Runtime（Stack 移行途中）

**背景:** 6/12 AM session で Dynamic40 中心に大きな損失。午後・事後分析の起点日。

**観測（Phase355 post-audit）:**
- AM accepted 79（Core10: 11, Dynamic40: 68）
- Dynamic40 reject 28（pullback guard 相当）
- Core10 reject 0 — guard 設計通り Core10 非触

**当日の意味:** 「Universe は当たっているが Dynamic40 の **押し目誤読** と **高値近接低モメ** が損失源」という仮説が data で支持された。

**採用判定:** 当日中の runtime 変更はなし。分析 Phase348–355 が 6/13 に集中。

**現在状態:** セッションデータ `results/small_paper/20260612/` は **active** 参照元

---

## 2026-06-13 — kabutrade0612（6/12 Incident Recovery コミット）

### Phase348/349

**目的:** 6/12 ENTRY 失敗・limit gap 失敗の個別レビュー。

**採用判定:** **research** — 直接 guard 採用には至らず。

### Phase355

**目的:** Dynamic40 限定 **pullback misread** guard 本番化。

**背景:** 5分騰落<0 かつ VWAP 乖離<0 の ENTRY を reject。

**検証結果:** Phase355 rollout AM replay pass。Phase365: B stack +100,400円 vs baseline。

**採用判定:** **adopted** — YAML `enable_pullback_misread_dynamic40_guard: true`。

**現在状態:** **active**

### Phase361/362/363/364

**目的:** near day-high + low momentum guard（C03）の stack 検証。

**背景:** Phase362 は **全銘柄 C03** が PF 最大だが Core10 への副作用懸念。

**検証結果:** Phase363: 6/12 単日 delta +140,200円（C03 all）。Phase365: **Dynamic40 のみ** Phase364 を production stack に確定。

**採用判定:** Phase364 **adopted**；Phase362-B（全銘柄）は **superseded** by Phase365-C。

**現在状態:** Phase364 **active**

### Phase333 / entry_scan / Discord cap-blocked

**目的:** 100株円 canonical summary、ENTRY freshness、CAP 満杯 Discord 分離。

**採用判定:** **adopted**（運用可視性）。

**現在状態:** **active**

### Phase365/373

**目的:** production stack 歴史検証と本番監視パック。

**結論:** `maintain_production_stack: true` — Phase355 + Phase364。

**現在状態:** **active**（引け後監視の baseline）

### Phase376/377

**目的:** 引け後 daily PnL と Period A/B regime 分解。

**検証結果:**
- Period A（20260518–27）: Stack C でも **-841,550円**（7日）
- Period B（20260528–0612）: Stack C **+813,140円**（9日）

**影響:** 「システムは Period B で成立、Period A は別 regime」という narrative が固定化。

**現在状態:** **active** monitoring

---

## 2026-06-14 — Capital Path Research（未コミット作業含む）

### Phase260–274（capital / sizing / live config shadow）

**目的:** Research PF ではなく **final_equity** 主指標で capital path を forward 蓄積。

**背景:** Phase267 で「150万 CAP=2 マイナス vs 過去 CAP=2 プラス」の矛盾（Phase268 で説明）。

#### Phase267

**問題:** 150万/2x/CAP=2/100株 → final_equity **1,437,480**（-4.17%）。

**原因（Phase268）:** reject 1036 trades の counterfactual PnL が正 — capital constraint で「見えない利益」が除外。

**採用判定:** **research observe** — runtime 未反映。

#### Phase268

**結論:** Research PF 単独採用禁止。accepted vs rejected 分解。

#### Phase269–272

**目的:** 元本×leverage×CAP×stop grid → Phase270 bucket 推奨 → Phase271 lev1.5 非頑健 → **Phase272 lev2.0 固定**。

**採用判定（research）:** 150万 **CAP=3 fixed**；200万+ **CAP=5 dynamic**。**runtime 未反映**。

#### Phase273/274

**目的:** paper 終了後 forward shadow — 固定 bucket（273）と 1.5M→2M 自動遷移（274）。

**検証結果（9日）:** transition 未達（final 1,650,270 < 2M）。`adopt_not_allowed`（day_count<10）。

**現在状態:** **shadow active**, **observe**

### Phase374–389

**目的:** CAP sensitivity、1.5M live candidate、full regime validation。

**Phase388/389:** 150万円運用推奨。**CAP=2 は research_candidate** — runtime は cap3 のまま。

**現在状態:** **research active**；CAP=2 runtime **rejected until live confirm**

---

# Major Architecture Evolution

## Era 1 — 初期スクリーニング（2026-05-06〜05-17）

**問題:** データ源が Yahoo 中心で、kabu 公式 API との乖離。

**改善:** screening + Discord + universe 構築 CLI。

**生存:** 朝スクリーン、`build_universe.py`, Discord 通知。**旧 Yahoo loop は別系統に隔離**。

---

## Era 2 — Paper Observer（2026-05-18〜）

**問題:** shadow/live の判定経路が不一致。

**改善:** Phase55 small paper observer、`ObserverPositionTracker`, structural_trades.csv。

**生存:** 現 Stack C の runtime 骨格。**virtual hold PF は Era 2 末期に廃止方針**。

---

## Era 3 — Core10 + Dynamic40（2026-05-29〜）

**問題:** top50 だけでは alpha/source が不透明。

**改善:** core10 固定 + dynamic40 rank、price-risk filter（Phase269）、AM/PM refresh（Phase148）。

**生存:** `--universe-mode core10-dynamic40-price-risk-filter-shadow`。**rank_21_40 依存はリスクとして記録**（Phase374/381）。

---

## Era 4 — Entry Score v2（2026-06-07〜）

**問題:** v1 多因子 score はリーク・過学習・説明可能性に欠ける。

**改善:** Phase314 で **2 token のみ**。Phase267 で quality reject off。

**生存:** Momentum:low + Board:mid。**v1 因子群は shadow log のみ**。

---

## Era 5 — Board Dynamic Trailing（2026-06-04〜13）

**問題:** 固定 trailing（Phase174）と fade exit が board regime を反映しない。

**改善:** Phase332 imbalance percentile 連動 trailing。

**失敗からの教訓:** EXIT 改善は **board/PUSH データが必要** — Yahoo replay のみでは過大評価リスク（Phase381）。

**生存:** production EXIT。**174 legacy は shadow counterfactual**。

---

## Era 6 — 6/12 Incident Recovery（2026-06-12〜13）

**問題:** Dynamic40 で押し目誤読・高値近接低モメ ENTRY が損失。

**改善:** Phase355 + Phase364（Dynamic40 のみ）。Stack C 確定。

**不採用:** limit-up guard(351), gap-up fade(359), board failure exit(342–347), K10(370), reentry cluster(368).

**生存:** 355/364 guards + canonical summary + entry freshness。

---

## Era 7 — Capital Path Research（2026-06-14〜）

**問題:** trade-level PF と capital path PnL の乖離（Phase268）。

**改善:** equity curve simulation、forward shadow 255/262/266/273/274、dual-layer output。

**未決:** CAP=2 runtime 採用、lev 固定 2.0 の live 開始、10日 forward ゲート。

**生存:** research + shadow **observe** — **runtime 未反映**。

---

# Runtime Evolution

## Universe の変遷

| 時期 | 状態 | 理由 |
|------|------|------|
| 初期 | 手動 CSV / 広 universe | 探索期 |
| Phase113/117 | vol-liq top50 | ノイズ削減 |
| Phase148 | intraday refresh 10:00/14:30 | Phase114 12:25 **superseded** |
| Phase269 | core10 + dynamic40 + price-risk | AM/PM runner 既定 |
| Phase375 | rank bucket 分析 | full D40 置換 **rejected** |

**現在:** core10-dynamic40-price-risk-filter-shadow。**active**。

---

## Entry の変遷

| 時期 | 状態 | 結果 |
|------|------|------|
| quality gate | min_continuation_quality reject | Period A で不調 → **reject off** |
| Phase230–245 | 多因子 score v1 | **superseded** |
| Phase314/267 | score v2 min=3 (Momentum+Board) | **active** |
| Phase153b | price risk guard | **active** |
| Phase355/364 | Dynamic40 guards | **active**（6/13 採用） |
| Phase351/359/368/370 | 各種 guard 試行 | **rejected** |

**現在:** score v2 + 2 guards + price risk + freshness scan。**rule 数は Era 4 後に減少**。

---

## Exit の変遷

| 時期 | 状態 | 結果 |
|------|------|------|
| structural v1 | stop/session/overlap | 基盤 **active** |
| fade/momentum/quality_decay | Phase71 系 | production **removed** |
| Phase174 | fixed trailing 0.8%/50% | **superseded** |
| Phase332 | board-dynamic trailing | **active** |
| Phase342–347 | board failure exit | **rejected** |
| Phase371 | high-MFE stop recovery | **rejected** |

**現在:** hard_stop 1.2% + board-dynamic trailing + overlap + session close。

---

## CAP の変遷

| 時期 | CAP | 根拠 |
|------|-----|------|
| 初期 trial | 3 | q070_cap3 由来 |
| Phase245/262 | slot occupation 研究 | research only |
| Phase385–389 | CAP=2 @ 1.5M research positive | **runtime 未反映** |
| Phase267 | 150万 CAP=2 → マイナス | CAP=3 推奨（research） |

**現在 runtime:** **cap=3**。**CAP=2 observe**（Phase387 shadow monitoring）。

---

## Discord の変遷

| 時期 | 変更 |
|------|------|
| 2026-05-06 | 通知原型 |
| Phase281 | trade / cap-blocked チャンネル分離 |
| Phase333 | canonical 100-share yen summary |
| Forward shadow | SectorHeat / RiskSizing / EquityStop / LiveConfig blocks |

**現在:** canonical_summary 必須 + research shadow 追記。**active**。

---

# Major Failures

## 1. Period A 大敗（20260518–27）

**何が起きたか:** Stack C でも 7日 **-841,550円**（Phase377）。

**問題:** regime / universe / overlap / exit の合成要因。単一 guard では説明不可。

**対応:** Period B 分離監視。ENTRY 簡素化（314）と 6/12 guard は **Period B 向け**に有効。

**現在:** **active** リスク — Period A 再発時の drawdown シナリオ未解決。

---

## 2. Phase174 固定 Trailing → Phase332 置換

**問題:** 全銘柄同一 0.8%/50% は board 強弱を無視。弱板で遅すぎ、強板で早すぎ。

**撤回:** production trailing ロジックから固定パラメータ削除。

**教訓:** EXIT は **board 特徴量連動** が必要。legacy は shadow counterfactual のみ。

**現在:** Phase174 **superseded** / shadow **active**

---

## 3. Fade / Momentum Exit 路線（Phase71 系）

**問題:** PF 改善が replay で不安定。live board 依存。

**撤回:** production structural policy から fade 系排除。

**現在:** shadow YAML のみ **inactive for production**

---

## 4. Research PF による誤判断リスク（Phase267/268）

**問題:** unconstrained static PnL は positive に見えるが capital path は negative。

**撤回:** 「PF>1 なら採用」ルールを明文化禁止。

**現在:** dual-layer / final_equity 主指標 **active** 方針

---

## 5. Phase362-B 全銘柄 C03 採用案

**問題:** PF 最大だが Core10 副作用と単日依存。

**撤回:** Phase365 で **Dynamic40 のみ Phase364** に確定。

**現在:** Phase362-B **superseded**

---

## 6. Leverage 1.5 bucket 推奨（Phase270 → Phase271/272）

**問題:** Phase271 — 9日サンプルで lev1.5 優位は非頑健。

**撤回:** lev **2.0 固定**（Phase272）。

**現在:** Phase270/271 **superseded** / Phase272 **active**（research recommendation）

---

## 7. no_entry_until / virtual hold / TAKE-as-EXIT

| 項目 | 結果 |
|------|------|
| Phase13 09:30 gate | **removed** → session window 管理 |
| 300s virtual hold PF | **removed**（legacy 参照のみ） |
| TAKE=EXIT（Phase54） | **rejected** |

---

# Major Successes

## Phase148 — AM/PM Daily Runner

**問題:** 手動 session 起動・universe 更新漏れ。  
**改善:** 10:00/14:30 refresh + orchestration。  
**現在:** **active** — 平日運用の中心。

## Phase314 + Phase267 — Entry Score v2 簡素化

**問題:** 因子過多・quality gate 混乱。  
**改善:** 2 token / min=3 / quality reject off。  
**現在:** **active** — Stack C ENTRY 中核。

## Phase332 — Board Dynamic Trailing EXIT

**問題:** 固定 trailing の board  blindness。  
**改善:** imbalance percentile tier 連動。  
**現在:** **active** — Stack C EXIT。

## Phase355 + Phase364 — Dynamic40 ENTRY Guards

**問題:** 6/12 Dynamic40 損失構造。  
**改善:** pullback misread + near day-high low-mom（D40 only）。  
**検証:** Phase365 +813k vs baseline +160k（Period B 全体）。  
**現在:** **active** — Core10 非触を維持。

## Phase333 — Canonical 100-Share Yen Summary

**問題:** Discord/summary の PnL 定義揺れ。  
**改善:** observer_exit 実約定ベース一本化。  
**現在:** **active**

## Phase268 + dual-layer — Capital Path 認識

**問題:** trade PF と equity curve の乖離。  
**改善:** accepted/rejected 分解、final_equity 採用基準。  
**現在:** **active** 方法論（Phase273+ に継承）

## Phase255/262/266/273/274 — Forward Shadow 基盤

**問題:** 単発 backtest の過学習。  
**改善:** paper 終了後自動蓄積、10日ゲート。  
**現在:** **shadow active**, adoption **observe**

---

# 6/12 Incident Deep Dive

## 発生

**日付:** 20260612（AM session 中心）  
**現象:** Dynamic40 中心の accepted ENTRY で損失。Core10 は guard 対象外のため別分析。

## 分析

| Phase | 発見 |
|-------|------|
| 348 | ENTRY failure 個別要因 |
| 349 | limit gap failure |
| 355 post-audit | D40 28 reject 候補、Core10 0 reject |
| 353/354 | pullback universe split |
| 358/372/379 | low-MFE stop_hit 構造（guard 後も残存） |

## 検証

| Phase | 結果 |
|-------|------|
| 355 rollout | AM replay pass |
| 362 | 355+C03 stack PF 1.27 |
| 363 | 6/12 C03 delta +140,200円 |
| 365 | maintain 355+364 |
| 377 | Period B 黒字化は guard+exit 合成 |

## 採用（production）

- Phase355 pullback guard
- Phase364 near day-high low-mom guard（**D40 only** — 365 確定）
- Phase332 EXIT（事前採用済み、6/13 コミットで YAML 整合）
- Phase333 canonical summary
- entry_scan freshness
- Discord cap-blocked channel

## 不採用

- 351 limit-up / 352 historical
- 359 gap-up fade
- 368 symbol reentry cluster
- 370 K10 stop-chain
- 371 high-MFE recovery exit
- 342–347 board failure exit
- 337–341 exit candidate / VWAP tuning
- 362-B C03 all symbols（→ 364 D40 only に縮小）

## 現在状態

- Stack C **active**
- 6/12 AM 単独 PnL は guard 後もマイナス（377）— **完全回復ではない**
- low-MFE stop_hit **未解決**（research 継続）

---

# Capital Research Era

## Phase260 — Position Exposure Audit

**目的:** CAP/reject/buying power の可視化。  
**現在:** research **active** — Phase268/269 の前提。

## Phase261–262 — Risk-Aware Sizing

**目的:** fixed_100 vs risk_1pct 等。  
**262 forward shadow:** paper 終了後自動。**observe**。

## Phase263/266 — Equity Dynamic Stop

**目的:** equity ベース dynamic stop vs fixed 1.2%。  
**266 forward shadow:** **active**。**observe**（10日未満）。

## Phase267–272 — Equity Curve & Live Config Recommendation

| Phase | 決定 |
|-------|------|
| 267 | 150万 CAP=2 → マイナス（capital path） |
| 268 | reject PnL が主因 — PF 単独禁止 |
| 269 | grid 150 configs |
| 270 | bucket 推奨（lev 混在） |
| 271 | lev1.5 非頑健 |
| 272 | **lev2.0 固定**；150万 CAP=3 fixed；200万+ CAP=5 dynamic |

**runtime 反映:** **なし**（research recommendation only）

## Phase273/274 — Forward Live Config Shadow

| 構成 | 内容 |
|------|------|
| 273 | 3 bucket 固定（1500k / 2000k / 3000k） |
| 274 | 1500k スタート → equity≥2M で CAP5/dynamic 自動遷移 |

**9日結果:** final 1,650,270 — **transition 未発生**。`adopt_not_allowed`。

## Phase374–389 — Capital Scaling

**目的:** CAP sensitivity、realistic credit sizing、1.5M live candidate。

**Phase388/389:** 150万円推奨。CAP=2 research positive。**runtime cap3 維持**。

---

---

# Top Open Risks

| Priority | Risk ID | Title | Description | Current Mitigation | Owner Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Risk S1 | PUSH replay fidelity | Board/PUSH dependent EXIT/ENTRY not fully reproducible offline | Forward shadow; Risk S1 documented (Phase381) | 381 |
| 2 | Risk S2 | Period A recurrence | Stack C Period A still -772k; guards Period B oriented | 377/389 regime monitoring | 377, 389 |
| 3 | Risk A1 | low-MFE stop_hit | stop_hit after minimal MFE persists post-guards | 379 research; no production fix | 379 |
| 4 | Risk A2 | CAP=2 runtime未検証 | 388/389 research positive; runtime cap3 maintained | 387 shadow + live session confirm | 387, 388 |
| 5 | Risk A3 | Forward shadow <10日 | 273/274/266 day_count=9 adopt_not_allowed | Continue forward accumulation | 273, 274 |

---

# What Changed Since 6/12

6/12 Incident Runtime → Current Runtime の差分。

| Layer | 6/12 Runtime | Current Runtime | Phase |
| --- | --- | --- | --- |
| Entry | 355 off | 355 on (D40 pullback) | 355 |
| Entry | 364 off | 364 on (D40 near-high) | 364 |
| Entry | freshness scan partial | entry_scan freshness guard | NP-scan |
| Exit | 174 legacy / transition | 332 board-dynamic | 332 |
| Summary | legacy mixed PnL defs | 333 canonical 100-share yen | 333 |
| Discord | single channel noise | 281 cap-blocked split | 281 |
| Monitoring | ad-hoc review | 376/377/373 production monitor | 376 |
| Universe | 269 partial | 269 price-risk AM/PM stable | 269 |
| CAP | cap3 | cap3 (CAP=2 research observe) | 387/388 |

---

---

# Top Open Risks

| Priority | Risk ID | Title | Description | Current Mitigation | Owner Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Risk S1 | PUSH replay fidelity | Board/PUSH dependent EXIT/ENTRY not fully reproducible offline | Forward shadow; Risk S1 documented (Phase381) | 381 |
| 2 | Risk S2 | Period A recurrence | Stack C Period A still -772k; guards Period B oriented | 377/389 regime monitoring | 377, 389 |
| 3 | Risk A1 | low-MFE stop_hit | stop_hit after minimal MFE persists post-guards | 379 research; no production fix | 379 |
| 4 | Risk A2 | CAP=2 runtime未検証 | 388/389 research positive; runtime cap3 maintained | 387 shadow + live session confirm | 387, 388 |
| 5 | Risk A3 | Forward shadow <10日 | 273/274/266 day_count=9 adopt_not_allowed | Continue forward accumulation | 273, 274 |

---

# What Changed Since 6/12

6/12 Incident Runtime → Current Runtime の差分。

| Layer | 6/12 Runtime | Current Runtime | Phase |
| --- | --- | --- | --- |
| Entry | 355 off | 355 on (D40 pullback) | 355 |
| Entry | 364 off | 364 on (D40 near-high) | 364 |
| Entry | freshness scan partial | entry_scan freshness guard | NP-scan |
| Exit | 174 legacy / transition | 332 board-dynamic | 332 |
| Summary | legacy mixed PnL defs | 333 canonical 100-share yen | 333 |
| Discord | single channel noise | 281 cap-blocked split | 281 |
| Monitoring | ad-hoc review | 376/377/373 production monitor | 376 |
| Universe | 269 partial | 269 price-risk AM/PM stable | 269 |
| CAP | cap3 | cap3 (CAP=2 research observe) | 387/388 |

---

---

# Top Open Risks

| Priority | Risk ID | Title | Description | Current Mitigation | Owner Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Risk S1 | PUSH replay fidelity | Board/PUSH dependent EXIT/ENTRY not fully reproducible offline | Forward shadow; Risk S1 documented (Phase381) | 381 |
| 2 | Risk S2 | Period A recurrence | Stack C Period A still -772k; guards Period B oriented | 377/389 regime monitoring | 377, 389 |
| 3 | Risk A1 | low-MFE stop_hit | stop_hit after minimal MFE persists post-guards | 379 research; no production fix | 379 |
| 4 | Risk A2 | CAP=2 runtime未検証 | 388/389 research positive; runtime cap3 maintained | 387 shadow + live session confirm | 387, 388 |
| 5 | Risk A3 | Forward shadow <10日 | 273/274/266 day_count=9 adopt_not_allowed | Continue forward accumulation | 273, 274 |

---

# What Changed Since 6/12

6/12 Incident Runtime → Current Runtime の差分。

| Layer | 6/12 Runtime | Current Runtime | Phase |
| --- | --- | --- | --- |
| Entry | 355 off | 355 on (D40 pullback) | 355 |
| Entry | 364 off | 364 on (D40 near-high) | 364 |
| Entry | freshness scan partial | entry_scan freshness guard | NP-scan |
| Exit | 174 legacy / transition | 332 board-dynamic | 332 |
| Summary | legacy mixed PnL defs | 333 canonical 100-share yen | 333 |
| Discord | single channel noise | 281 cap-blocked split | 281 |
| Monitoring | ad-hoc review | 376/377/373 production monitor | 376 |
| Universe | 269 partial | 269 price-risk AM/PM stable | 269 |
| CAP | cap3 | cap3 (CAP=2 research observe) | 387/388 |

---

---

# Top Open Risks

| Priority | Risk ID | Title | Description | Current Mitigation | Owner Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Risk S1 | PUSH replay fidelity | Board/PUSH dependent EXIT/ENTRY not fully reproducible offline | Forward shadow; Risk S1 documented (Phase381) | 381 |
| 2 | Risk S2 | Period A recurrence | Stack C Period A still -772k; guards Period B oriented | 377/389 regime monitoring | 377, 389 |
| 3 | Risk A1 | low-MFE stop_hit | stop_hit after minimal MFE persists post-guards | 379 research; no production fix | 379 |
| 4 | Risk A2 | CAP=2 runtime未検証 | 388/389 research positive; runtime cap3 maintained | 387 shadow + live session confirm | 387, 388 |
| 5 | Risk A3 | Forward shadow <10日 | 273/274/266 day_count=9 adopt_not_allowed | Continue forward accumulation | 273, 274 |

---

# What Changed Since 6/12

6/12 Incident Runtime → Current Runtime の差分。

| Layer | 6/12 Runtime | Current Runtime | Phase |
| --- | --- | --- | --- |
| Entry | 355 off | 355 on (D40 pullback) | 355 |
| Entry | 364 off | 364 on (D40 near-high) | 364 |
| Entry | freshness scan partial | entry_scan freshness guard | NP-scan |
| Exit | 174 legacy / transition | 332 board-dynamic | 332 |
| Summary | legacy mixed PnL defs | 333 canonical 100-share yen | 333 |
| Discord | single channel noise | 281 cap-blocked split | 281 |
| Monitoring | ad-hoc review | 376/377/373 production monitor | 376 |
| Universe | 269 partial | 269 price-risk AM/PM stable | 269 |
| CAP | cap3 | cap3 (CAP=2 research observe) | 387/388 |

---

---

# Top Open Risks

| Priority | Risk ID | Title | Description | Current Mitigation | Owner Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Risk S1 | PUSH replay fidelity | Board/PUSH dependent EXIT/ENTRY not fully reproducible offline | Forward shadow; Risk S1 documented (Phase381) | 381 |
| 2 | Risk S2 | Period A recurrence | Stack C Period A still -772k; guards Period B oriented | 377/389 regime monitoring | 377, 389 |
| 3 | Risk A1 | low-MFE stop_hit | stop_hit after minimal MFE persists post-guards | 379 research; no production fix | 379 |
| 4 | Risk A2 | CAP=2 runtime未検証 | 388/389 research positive; runtime cap3 maintained | 387 shadow + live session confirm | 387, 388 |
| 5 | Risk A3 | Forward shadow <10日 | 273/274/266 day_count=9 adopt_not_allowed | Continue forward accumulation | 273, 274 |

---

# What Changed Since 6/12

6/12 Incident Runtime → Current Runtime の差分。

| Layer | 6/12 Runtime | Current Runtime | Phase |
| --- | --- | --- | --- |
| Entry | 355 off | 355 on (D40 pullback) | 355 |
| Entry | 364 off | 364 on (D40 near-high) | 364 |
| Entry | freshness scan partial | entry_scan freshness guard | NP-scan |
| Exit | 174 legacy / transition | 332 board-dynamic | 332 |
| Summary | legacy mixed PnL defs | 333 canonical 100-share yen | 333 |
| Discord | single channel noise | 281 cap-blocked split | 281 |
| Monitoring | ad-hoc review | 376/377/373 production monitor | 376 |
| Universe | 269 partial | 269 price-risk AM/PM stable | 269 |
| CAP | cap3 | cap3 (CAP=2 research observe) | 387/388 |

---

# Current State (Latest)

## 現在の本番 Runtime（Stack C）

```
Config: small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml
Universe: core10-dynamic40-price-risk + vol-liq top50 + AM/PM refresh
Entry:    score_v2 (Momentum+Board≥3) + Phase355 + Phase364 + price risk + freshness
Exit:     Phase332 board-dynamic trailing + stop 1.2% + overlap + session close
Position: max_concurrent_positions=3
Risk:     daily loss -2.5%, risk cluster block, maintenance ratio (sim)
Discord:  canonical 100-share yen + cap-blocked webhook + shadow blocks
Mode:     paper_only, order_enabled=false, shadow_only=true
```

## 現在の Shadow

**Forward（post-session auto）:** 255, 262, 266, 273, 274  
**Inline（session）:** 332 legacy trailing counterfactual, 355 pullback shadow, 335 board exit, 214 imbalance, 186 vwap, 230 score shadow 他  
**Monitoring:** Phase387 CAP=2 shadow

## 現在の Research

- Forward shadow 10日ゲート待ち（273/274/266）
- CAP=2 @ 1.5M live 確認待ち（388/389）
- Period A regime 対策未確立（377/389）
- low-MFE stop_hit（379）
- Sector heat negative filter 採用判断（254→255）
- PUSH JSONL normalized replay（Risk S1）

## 今後の方針

1. **Forward shadow を10営業日以上継続** — final_equity 主指標で adopt 判定  
2. **CAP=2 は live session で再現確認後のみ runtime 検討**  
3. **Period A 再発監視** — guard は Period B 向けに設計されたことを忘れない  
4. **Replay 基盤整備** — board/PUSH 依存 EXIT の offline 検証信頼性向上  
5. **edge attribution** — Universe / Entry / Exit / Overlap / Capital の分離 PF  
6. **live 開始構成（research）:** eq1500k_lev2p0_cap3_fixed → 2M+ で CAP5 dynamic（Phase274 shadow で検証中）

---

---

---

---

---

---

---

# Historical Misconceptions

Curated (25 entries). Audit CSV: `330` phases.

| Date | Misconception | Believed At The Time | Invalidated By | Final Verdict | Current Status |
| --- | --- | --- | --- | --- | --- |
| 2026-05-27 | momentum fade EXIT improves PF | fade/hybrid exit trials | Phase71 | production rejected | removed |
| 2026-06-04 | fixed trailing 0.8%/50% optimal for all symbols | Phase174 shadow gain | Phase332 | board-dynamic replace | superseded |
| 2026-06-06 | PF > 1 implies runtime adoption | unconstrained replay | Phase267/268 | final_equity primary | active |
| 2026-06-06 | CAP=2 optimal at 1.5M | slot efficiency | Phase267 | CAP=3 research recommend | observe |
| 2026-06-06 | rejected trades can be ignored in PF | simplified PF | Phase268 | dual-layer required | active |
| 2026-06-07 | entry score v1 multi-factor explains edge | Phase230–245 | Phase314/266 | v2 two-token only | superseded |
| 2026-06-07 | quality≥0.70 is core ENTRY gate | YAML default | Phase267 | score_v2≥3 | superseded |
| 2026-06-07 | adding rules always improves system | phase count = progress | Phase314 | rule reduction | active |
| 2026-06-08 | offline EXIT replay equals live | structural replay | Phase381 | PUSH/board gap | active |
| 2026-06-12 | limit-up guard fixes 6/12 | Phase351 | Phase351 rollout | rejected | rejected |
| 2026-06-12 | gap-up fade guard fixes AM loss | Phase359 | Phase359 review | rejected | rejected |
| 2026-06-12 | C03 all-symbol guard is production best | Phase362-B | Phase365 | D40-only 364 | superseded |
| 2026-06-13 | Stack C profit is ENTRY-guard-only | post-adoption narrative | Phase381 | EXIT also contributes | active |
| 2026-06-13 | 355+364 fixes Period A | guard universal efficacy | Phase377 | Period B oriented | active |
| 2026-06-13 | 12:25 PM regen sufficient | Phase114 | Phase148 | 10:00/14:30 refresh | superseded |
| 2026-06-13 | TAKE event equals EXIT | Phase54 | Phase54 review | TAKE≠EXIT | removed |
| 2026-06-14 | lev1.5 bucket is live optimal | Phase270 | Phase271/272 | lev2.0 fixed | superseded |
| 2026-06-14 | 9-day forward shadow sufficient for adopt | Phase273 early results | adopt_not_allowed | ≥10-day gate | observe |
| 2026-06-14 | equity≥2M transition fires immediately | Phase274 design | Phase274 9-day run | transition false | observe |
| 2026-06-14 | sector heat filter ready for universe | Phase254/255 delta | Phase255 sample | insufficient_sample | observe |
| 2026-06-14 | risk_2pct sizing beats fixed_100 | Phase262 | low_price_overexpansion | observe | observe |
| 2026-06-14 | CAP=2 research → immediate runtime | Phase388/389 | live confirm pending | cap3 maintained | observe |
| 2026-06-14 | rank_21_40 full replace improves alpha | Phase375 | Phase375 review | rejected | rejected |
| 2026-06-14 | price cap constraint is profit source | unconstrained vs cap confusion | Phase268/267 | capital path matters | active |
| 2026-06-04 | fade exit effective in production | Phase71/166 | Phase166 rejected | shadow only | removed |

---

# Phase Adoption Matrix

Generated from `kabu_native/docs/audits/full_phase_history_audit.csv`.

| Category | Total | Adopted | Rejected | Superseded | Active |
| --- | --- | --- | --- | --- | --- |
| Universe | 21 | 3 | 1 | 2 | 2 |
| Entry | 41 | 5 | 4 | 1 | 6 |
| Exit | 24 | 1 | 2 | 0 | 1 |
| Position | 22 | 1 | 0 | 1 | 0 |
| Risk | 5 | 0 | 0 | 0 | 1 |
| Sizing | 10 | 0 | 0 | 0 | 1 |
| Capital | 2 | 0 | 0 | 1 | 0 |
| Discord | 8 | 3 | 0 | 0 | 3 |
| Monitoring | 164 | 6 | 1 | 4 | 2 |
| Data | 19 | 1 | 0 | 0 | 1 |
| Replay | 10 | 0 | 0 | 0 | 0 |
| Documentation | 4 | 0 | 0 | 0 | 1 |
| **合計** | 330 | 20 | 8 | 9 | 18 |

---

# Major Decisions Table (Adoption Rationale)

CSV adoption_status ∈ {adopted, rejected, superseded} — **37 rows**. Evidence from reports + EVIDENCE_BY_PHASE.

| Phase | Decision | Why Adopted | Why Alternatives Rejected | Evidence | Current Status |
| --- | --- | --- | --- | --- | --- |
| Phase13 | no_entry_until 09:30 gate | Superseded by Phase 148 | related: 148; status: superseded | 09:30 no_entry_until は廃止。session 枠は構造理由のみ (configs/session_control.yaml)。 | removed |
| Phase55 | Small paper observer runtime | Production observer runtime | related: 148,332; status: adopted | Git: Phase55 small paper observer runtime | active |
| Phase113 | Daytrade suitability top50 rule | Production runtime (Stack C) | related: —; status: adopted | Production runtime Stack C universe | active |
| Phase114 | 12:25 PM universe regen | Superseded by Phase 148 intraday refresh | related: 148 intraday refresh; status: superseded | Report generated; no runtime adoption | removed |
| Phase117 | Volatility liquidity universe | Production runtime (Stack C) | related: —; status: adopted | Production runtime Stack C universe | active |
| Phase148 | AM/PM daily runner orchestration | Production runtime (Stack C) | related: —; status: adopted | Phase114 12:25 superseded | active |
| Phase153b | Entry price risk guard (min price / tick ratio) | Production runtime (Stack C) | related: —; status: adopted | YAML entry_price_risk_guard active | active |
| Phase166 | Fade breakdown EXIT | Evaluated and rejected for production | related: —; status: rejected | Evaluated and rejected for production | rejected |
| Phase174 | Fixed trailing MFE 0.8%/50% | Superseded by Phase 332 | related: 332; status: superseded | Superseded by Phase332 board-dynamic | removed |
| Phase187 | Phase 187 vwap adverse cluster analysis | Shadow/review only; no hard reject implemented. | related: —; status: rejected | Shadow/review only; no hard reject implemented. | rejected |
| Phase254 | Phase 254 price floor adoption review | Robustness observation only. Phase253 negative-filter patterns evaluated for day-level ... | related: —; status: adopted | Robustness observation only. Phase253 negative-filter patterns evaluated for day-level stability,... | observe |
| Phase255 | Sector heat forward shadow logger | Superseded by sector heat forward shadow Phase255 | related: 253,254,256; status: superseded | Forward shadow logging only; actual Universe/Entry/Runtime unchanged. Adoption remains blocked un... | superseded |
| Phase257 | Phase 257 core12 dynamic38 pricecap shadow review | Observation only — Core12/Dynamic38 and price-cap variants are not adopted. MIN_CLOSE_P... | related: —; status: adopted | Observation only — Core12/Dynamic38 and price-cap variants are not adopted. MIN_CLOSE_PRICE=300.0... | observe |
| Phase263 | Phase 263 max concurrent time concentration | At 5M yen, dynamic_stop_risk_0p5 improves total shadow PnL vs fixed -1.2%. | related: —; status: superseded | At 5M yen, dynamic_stop_risk_0p5 improves total shadow PnL vs fixed -1.2%. | superseded |
| Phase267 | entry_score_v2 gate (min=3); quality reject off | Production runtime (Stack C) | related: —; status: adopted | score_v2 gate; quality reject off | active |
| Phase268 | Phase 268 daily runner default universe review | research_complete | related: —; status: superseded | Report generated; no runtime adoption | superseded |
| Phase270 | Equity bucket recommendation (mixed leverage) | Superseded by Phase 272 | related: 272; status: superseded | Mixed leverage bucket | removed |
| Phase271 | Leverage 1.5 bucket recommendation | Superseded by Phase 272 | related: 272; status: superseded | lev1.5 non-robust on 9-day sample | removed |
| Phase273 | Live config forward shadow (Phase272 configs) | Forward shadow logging; no runtime trading change | related: 270,272; status: superseded | day_count=9 adopt_not_allowed; final_equity +10.0% | superseded |
| Phase281 | Discord channel split (trade vs cap-blocked) | Production runtime (Stack C) | related: —; status: adopted | Report generated; no runtime adoption | active |
| Phase314 | Entry score v2 simplification (Momentum+Board only) | Production runtime (Stack C) | related: —; status: adopted | Rule reduction: 2-token score only | active |
| Phase332 | Board-dynamic trailing-MFE production EXIT | Production runtime (Stack C) | related: —; status: adopted | production_adoption_ok=true | active |
| Phase333 | Canonical 100-share yen summary | Production runtime (Stack C) | related: —; status: adopted | kabutrade0612 canonical summary | active |
| Phase351 | Limit-up proximity entry guard | Evaluated and rejected for production | related: —; status: rejected | production rejected | rejected |
| Phase354 | Phase 354 pullback universe split validation | Proceed with production shadow pilot using A_all_symbols. | related: —; status: adopted | Proceed with production shadow pilot using A_all_symbols. | observe |
| Phase355 | Pullback misread Dynamic40 guard | Production runtime (Stack C) | related: —; status: adopted | +100,400 yen vs baseline (Phase365); 6/12 D40 28 reject audit | active |
| Phase359 | Gap-up fade entry guard | Evaluated and rejected for production | related: —; status: rejected | shadow only rejected | rejected |
| Phase361 | Phase 361 near day high low mom guard validation | Production shadow pilot only; adoption bar not fully met. | related: —; status: adopted | Production shadow pilot only; adoption bar not fully met. | observe |
| Phase363 | Phase 363 c03 robustness validation | Production candidate: C03 improvement survives 6/12 exclusion. | related: —; status: adopted | Production candidate: C03 improvement survives 6/12 exclusion. | observe |
| Phase364 | Near day-high + low momentum Dynamic40 guard | Production runtime (Stack C) | related: 365,363; status: adopted | +140,200 yen 6/12 replay delta (Phase363); Phase365 maintain | active |
| Phase365 | Phase 365 production stack validation | Maintain Phase355 + Phase364 production stack. | related: —; status: adopted | Stack C +483,110 PF 1.2607 vs baseline +160,510 | observe |
| Phase367 | Phase 367 low mfe residual forensic | Shadow-validate A6_symbol_reentry_cluster before any production guard; counterfactual d... | related: —; status: adopted | Shadow-validate A6_symbol_reentry_cluster before any production guard; counterfactual delta=12100... | observe |
| Phase368 | Symbol reentry cluster guard | Evaluated and rejected for production | related: —; status: rejected | do not adopt | rejected |
| Phase370 | K10 stop-chain A1 guard | Evaluated and rejected for production | related: —; status: rejected | evaluated rejected | rejected |
| Phase371 | High-MFE stop_hit exit recovery | Evaluated and rejected for production | related: —; status: rejected | shadow only rejected | rejected |
| Phase375 | Dynamic40 rank quality full replace | Evaluated and rejected for production | related: —; status: rejected | full D40 replace rejected | rejected |
| PhaseNP-canonical-summary | Canonical summary builder | Production reporting | related: 333; status: adopted | 100-share yen primary metrics | active |
| PhaseNP-entry-scan | Entry scan controller (freshness/batch) | Production ENTRY path guard from kabutrade0612 | related: 348,355; status: adopted | entry_scan_controller.py wired in pilot_runner | active |

---

# Lessons Learned

| Lesson | Evidence Phase | Current Interpretation |
| --- | --- | --- |
| Rule addition ≠ improvement | 314, 377 | Simplify runtime rules; measure by regime |
| Research PF ≠ Final Equity | 267, 268 | Capital path + reject decomposition mandatory |
| Dynamic40 and Core10 are separate problems | 355, 364, 365 | Guards target D40 only |
| EXIT without board data is dangerous | 332, 381 | Board-linked trailing; replay gap Risk S1 |
| Do not push research directly to runtime | 273, 274, 388 | Forward shadow + ≥10-day gate |
| Period regime must be read separately | 377, 389 | Period A losses persist under Stack C |
| Single-day replay delta is insufficient | 363, 365 | Maintain with single-day share monitoring |
| EXIT contributes beyond ENTRY guards | 381 | trailing-MFE is part of Stack C edge |
| CAP change is capital-path not trade-PF | 267, 385–389 | CAP=2 research ≠ runtime cap3 |
| Leverage buckets need robustness check | 271, 272 | Fixed lev2.0 after short-sample failure |
| Observer-only must stay observer-only | all runtime | order_enabled=false maintained |
| Discord PnL needs one canonical definition | 333 | 100-share yen from observer_exit |
| AM session concentrates losses | 355 audit, 377 | AM/PM decomposition in monitoring |
| low-MFE stop_hit survives guards | 379 | Unresolved; research continues |
| Documentation follows adoption events | 390 | Update this SoT on adopt/reject/supersede |

---

# Appendix A — Complete Phase Timeline

Phase001 (genesis) through Phase99999. **331 rows.** CSV-only.

| Date | Phase | Category | Description | Verdict | Current Status |
| --- | --- | --- | --- | --- | --- |
| 2026-05-06 | Phase001 | Monitoring | Genesis — screening / Discord / entry prototype | adopted | active |
| 2026-05-17 | Phase8 | Monitoring | Phase 8 sweep | observe | observe |
| 2026-05-17 | Phase9 | Entry | Phase 9 entry quality | observe | observe |
| 2026-05-17 | Phase10 | Monitoring | Phase 10 combined candidates | observe | observe |
| 2026-05-17 | Phase11 | Replay | Phase 11 screen replay | observe | observe |
| 2026-05-17 | Phase13 | Monitoring | no_entry_until 09:30 gate | superseded | removed |
| 2026-05-18 | Phase37 | Monitoring | Phase 37 validation | observe | observe |
| 2026-05-18 | Phase38 | Monitoring | Phase 38 validation | observe | observe |
| 2026-05-18 | Phase39 | Monitoring | Phase 39 top quartile | observe | observe |
| 2026-05-18 | Phase40 | Monitoring | Phase 40 top quartile oos | observe | observe |
| 2026-05-18 | Phase41 | Data | Phase 41 data oos | observe | observe |
| 2026-05-18 | Phase43 | Monitoring | Phase 43 gate diagnosis | observe | observe |
| 2026-05-27 | Phase68 | Entry | Phase 68 variable audit | observe | observe |
| 2026-05-27 | Phase69 | Monitoring | Phase 69 indicator design audit | observe | observe |
| 2026-05-27 | Phase70 | Monitoring | Phase 70 decomposed indicator review | observe | observe |
| 2026-05-27 | Phase71 | Exit | Phase 71 split momentum fade review | observe | observe |
| 2026-05-27 | Phase72 | Exit | Phase 72 price momentum exit trial review | observe | observe |
| 2026-05-27 | Phase74 | Entry | Phase 74 entry churn overlap review | observe | observe |
| 2026-05-27 | Phase75 | Monitoring | Phase 75 quality gate redesign review | observe | observe |
| 2026-05-27 | Phase76 | Position | Phase 76 overlap position management review | observe | observe |
| 2026-05-27 | Phase77 | Monitoring | Phase 77 worst symbol regime filter review | observe | observe |
| 2026-05-27 | Phase78 | Monitoring | Phase 78 rolling symbol cooloff review | observe | observe |
| 2026-05-27 | Phase79 | Monitoring | Phase 79 symbol cooloff trial review | observe | observe |
| 2026-05-27 | Phase81 | Universe | Phase 81 universe coverage audit | observe | observe |
| 2026-05-27 | Phase82 | Monitoring | Phase 82 daytrade suitability review | observe | observe |
| 2026-05-27 | Phase83 | Monitoring | Phase 83 daytrade suitability oos review | observe | observe |
| 2026-05-27 | Phase84 | Monitoring | Phase 84 vol liq trial review | observe | observe |
| 2026-05-27 | Phase86 | Monitoring | Phase 86 vol liq symbol cooloff review | observe | observe |
| 2026-05-27 | Phase87 | Monitoring | Phase 87 profit source review | observe | observe |
| 2026-05-27 | Phase88 | Exit | Phase 88 exit pathology review | observe | observe |
| 2026-05-27 | Phase89 | Monitoring | Phase 89 mfe giveback min peak whatif review | observe | observe |
| 2026-05-27 | Phase90 | Monitoring | Phase 90 stability review | observe | observe |
| 2026-05-27 | Phase95 | Universe | Phase 95 universe diagnosis | observe | observe |
| 2026-05-27 | Phase96 | Universe | Phase 96 dynamic universe design | observe | observe |
| 2026-05-27 | Phase99 | Universe | Phase 99 shadow universe preflight | observe | observe |
| 2026-05-27 | Phase100 | Monitoring | Phase 100 jpx master setup check | observe | observe |
| 2026-05-27 | Phase101 | Universe | Phase 101 dynamic universe scoring review | observe | observe |
| 2026-05-27 | Phase103 | Monitoring | Phase 103 sampling revision | observe | observe |
| 2026-05-27 | Phase104 | Monitoring | Phase 104 board error rootcause | observe | observe |
| 2026-05-27 | Phase105 | Universe | Phase 105 register limit aware universe | observe | observe |
| 2026-05-27 | Phase106 | Monitoring | Phase 106 shadow live pipeline | observe | observe |
| 2026-05-27 | Phase107 | Monitoring | Phase 107 dynamic selection design | observe | observe |
| 2026-05-27 | Phase108 | Monitoring | Phase 108 opening screen design | observe | observe |
| 2026-05-27 | Phase109 | Universe | Phase 109 opening dynamic50 universe | observe | observe |
| 2026-05-27 | Phase110 | Monitoring | Phase 110 opening dynamic50 backtest review | observe | observe |
| 2026-05-27 | Phase111 | Monitoring | Phase 111 opening50 failure analysis | observe | observe |
| 2026-05-27 | Phase112 | Data | Phase 112 daily data scalability | observe | observe |
| 2026-05-27 | Phase113 | Universe | Daytrade suitability top50 rule | adopted | active |
| 2026-05-27 | Phase114 | Universe | 12:25 PM universe regen | superseded | removed |
| 2026-05-27 | Phase115 | Monitoring | Phase 115 am pm shadow pipeline | observe | observe |
| 2026-05-27 | Phase116 | Monitoring | Phase 116 am pm session policy | observe | observe |
| 2026-05-27 | Phase117 | Universe | Volatility liquidity universe | adopted | active |
| 2026-05-27 | Phase118 | Monitoring | Phase 118 core10 dynamic40 pipeline | observe | observe |
| 2026-05-27 | Phase119 | Monitoring | Phase 119 daily core rotation design | observe | observe |
| 2026-05-27 | Phase120 | Exit | Phase 120 mfe mae exit review | observe | observe |
| 2026-05-27 | Phase121 | Exit | Phase 121 fade exit replay | observe | observe |
| 2026-05-27 | Phase122 | Monitoring | Phase 122 fade extension conditions | observe | observe |
| 2026-05-27 | Phase123 | Monitoring | Phase 123 conditional fade extension review | observe | observe |
| 2026-05-27 | Phase124 | Monitoring | Phase 124 mfe predictor review | observe | observe |
| 2026-05-27 | Phase125 | Monitoring | Phase 125 reacceleration review | observe | observe |
| 2026-05-27 | Phase126 | Exit | Phase 126 state based fade exit review | observe | observe |
| 2026-05-27 | Phase127 | Monitoring | Phase 127 fade watch shadow | observe | observe |
| 2026-05-27 | Phase128 | Monitoring | Phase 128 fade watch trigger review | observe | observe |
| 2026-05-27 | Phase129 | Monitoring | Phase 129 trading logic improvement plan | observe | observe |
| 2026-05-27 | Phase130 | Exit | Phase 130 range hold exit review | observe | observe |
| 2026-05-27 | Phase131 | Monitoring | Phase 131 reacceleration shadow review | observe | observe |
| 2026-05-27 | Phase132 | Position | Phase 132 cap sensitivity review | observe | observe |
| 2026-05-27 | Phase133 | Monitoring | Phase 133 switch old vs new review | observe | observe |
| 2026-05-27 | Phase134 | Monitoring | Phase 134 fade switch policy review | observe | observe |
| 2026-05-27 | Phase135 | Monitoring | Phase 135 fade switch cooldown shadow review | observe | observe |
| 2026-05-27 | Phase136 | Entry | Phase 136 cap3 entry replay review | observe | observe |
| 2026-05-27 | Phase137 | Replay | Phase 137 replay fidelity review | observe | observe |
| 2026-05-27 | Phase138 | Replay | Phase 138 hybrid replay engine review | observe | observe |
| 2026-05-27 | Phase139 | Monitoring | Phase 139 hybrid fade switch policy review | observe | observe |
| 2026-05-27 | Phase140 | Monitoring | Phase 140 fade switch priority analysis | observe | observe |
| 2026-05-27 | Phase141 | Monitoring | Phase 141 fade switch block shadow review | observe | observe |
| 2026-05-27 | Phase142 | Monitoring | Phase 142 fade switch block scope review | observe | observe |
| 2026-05-27 | Phase143 | Monitoring | Phase 143 fade first switch block shadow review | observe | observe |
| 2026-05-27 | Phase144 | Monitoring | Phase 144 fade first switch block refinement review | observe | observe |
| 2026-05-27 | Phase145 | Monitoring | Phase 145 remaining issues review | observe | observe |
| 2026-05-27 | Phase146 | Monitoring | Phase 146 am pm multiday rescreening review | observe | observe |
| 2026-05-27 | Phase147 | Monitoring | Phase 147 shadow pilot readiness review | observe | observe |
| 2026-05-27 | Phase148 | Monitoring | AM/PM daily runner orchestration | adopted | active |
| 2026-05-27 | Phase148b | Monitoring | Phase 148b runner crash recovery | observe | observe |
| 2026-05-27 | Phase148c | Monitoring | Phase 148c session recovery | observe | observe |
| 2026-05-27 | Phase149 | Monitoring | Phase 149 symbol validation update | observe | observe |
| 2026-05-27 | Phase150 | Exit | Phase 150 urgent exit failure review | observe | observe |
| 2026-05-27 | Phase151 | Exit | Phase 151 take exit shadow review | observe | observe |
| 2026-05-27 | Phase152 | Monitoring | Phase 152 stop hit loss review | observe | observe |
| 2026-05-27 | Phase153a | Risk | Phase 153a low price risk review | observe | observe |
| 2026-05-27 | Phase153b | Entry | Entry price risk guard (min price / tick ratio) | adopted | active |
| 2026-05-27 | Phase153c | Universe | Phase 153c universe low price diagnosis | observe | observe |
| 2026-05-27 | Phase153d | Universe | Phase 153d price risk universe filter review | observe | observe |
| 2026-05-27 | Phase154 | Risk | Phase 154 daily runner price risk shadow review | observe | observe |
| 2026-05-27 | Phase154a | Monitoring | Phase 154a trial policy fix review | observe | observe |
| 2026-05-27 | Phase155 | Position | Phase 155 kabu register capacity fix | observe | observe |
| 2026-05-27 | Phase156 | Position | Phase 156 intraday refresh cap5 review | observe | observe |
| 2026-05-27 | Phase157 | Data | Phase 157 intraday refresh runner review | observe | observe |
| 2026-05-27 | Phase158 | Position | Phase 158 cap3 vs cap5 review | observe | observe |
| 2026-05-27 | Phase159 | Monitoring | Phase 159 overlap review | observe | observe |
| 2026-05-27 | Phase160 | Exit | Phase 160 fade exit review | observe | observe |
| 2026-05-27 | Phase161 | Monitoring | Phase 161 fade shadow policy review | observe | observe |
| 2026-05-27 | Phase162 | Monitoring | Phase 162 fade hybrid shadow review | observe | observe |
| 2026-05-27 | Phase163 | Replay | Phase 163 replay mismatch review | observe | observe |
| 2026-05-27 | Phase164 | Monitoring | Phase 164 fade hybrid refinement review | observe | observe |
| 2026-05-27 | Phase165 | Monitoring | Phase 165 overlap close policy review | observe | observe |
| 2026-05-27 | Phase166 | Exit | Fade breakdown EXIT | rejected | rejected |
| 2026-05-27 | Phase167 | Monitoring | Phase 167 4392 retained gate fix review | observe | observe |
| 2026-05-27 | Phase167 | Monitoring | Phase 167 shadow guard audit | observe | observe |
| 2026-05-27 | Phase168 | Entry | Phase 168 entry price risk guard missing price fix | observe | observe |
| 2026-05-27 | Phase168 | Monitoring | Phase 168 post fix verification | observe | observe |
| 2026-05-27 | Phase169 | Data | Phase 169 intraday refresh audit | observe | observe |
| 2026-05-27 | Phase170 | Data | Phase 170 intraday refresh hook fix | observe | observe |
| 2026-05-30 | Phase172 | Exit | Phase 172 exit metric redesign review | observe | observe |
| 2026-05-30 | Phase173 | Exit | Phase 173 exit redesign multisession review | observe | observe |
| 2026-05-30 | Phase174 | Monitoring | Fixed trailing MFE 0.8%/50% | superseded | removed |
| 2026-05-30 | Phase175 | Monitoring | Phase 175 pre live execution verification | observe | observe |
| 2026-05-30 | Phase176 | Data | Phase 176 intraday refresh patch verification | observe | observe |
| 2026-05-30 | Phase176 | Data | Phase 176 intraday refresh stop root cause | observe | observe |
| 2026-05-30 | Phase176b | Data | Phase 176b intraday refresh live shadow audit | observe | observe |
| 2026-05-30 | Phase177 | Monitoring | Phase 177 daytrade suitability accepted symbol review | observe | observe |
| 2026-05-30 | Phase178 | Monitoring | Phase 178 low liquidity filter review | observe | observe |
| 2026-05-30 | Phase179 | Monitoring | Phase 179 low liquidity shadow review | observe | observe |
| 2026-05-30 | Phase179b | Monitoring | Phase 179b low liquidity live shadow observation | observe | observe |
| 2026-05-30 | Phase179c | Config | Phase 179c low liquidity shadow yaml verification | observe | observe |
| 2026-05-30 | Phase179d | Config | Phase 179d daily runner low liquidity shadow config selection | observe | observe |
| 2026-05-30 | Phase180 | Monitoring | Phase 180 logging and symbol diagnostics verification | observe | observe |
| 2026-05-30 | Phase180 | Monitoring | Phase 180 symbol level trade diagnostics | observe | observe |
| 2026-05-30 | Phase181 | Entry | Phase 181 entry expectancy feature review | observe | observe |
| 2026-05-30 | Phase182 | Entry | Phase 182 extended entry analysis | observe | observe |
| 2026-05-30 | Phase183 | Entry | Phase 183 extended entry shadow logging verification | observe | observe |
| 2026-05-30 | Phase184 | Entry | Phase 184 extended entry shadow impact review | observe | observe |
| 2026-05-30 | Phase185 | Monitoring | Phase 185 vwap dev shadow candidate multisession review | observe | observe |
| 2026-05-30 | Phase186 | Monitoring | Phase 186 vwap shadow reject live observation | observe | observe |
| 2026-05-30 | Phase187 | Monitoring | Phase 187 vwap adverse cluster analysis | rejected | rejected |
| 2026-05-30 | Phase188 | Entry | Phase 188 pre adverse entry feature review | observe | observe |
| 2026-05-31 | Phase213c | Monitoring | Phase 213c board imbalance cohort stability review | observe | observe |
| 2026-05-31 | Phase213d | Position | Phase 213d session composition audit | observe | observe |
| 2026-05-31 | Phase214 | Monitoring | Phase 214 board imbalance shadow verification | observe | observe |
| 2026-05-31 | Phase215 | Entry | Phase 215 pm entry timing attribution review | observe | observe |
| 2026-05-31 | Phase216 | Monitoring | Phase 216 high mfe winner attribution review | observe | observe |
| 2026-05-31 | Phase217 | Monitoring | Phase 217 stop hit root cause review | observe | observe |
| 2026-05-31 | Phase218 | Monitoring | Phase 218 guard attribution audit | observe | observe |
| 2026-05-31 | Phase219 | Entry | Phase 219 board entry gate counterfactual review | observe | observe |
| 2026-05-31 | Phase220 | Monitoring | Phase 220 positive expectancy cohort discovery | observe | observe |
| 2026-05-31 | Phase221 | Monitoring | Phase 221 early momentum discovery review | observe | observe |
| 2026-05-31 | Phase222 | Monitoring | Phase 222 high break counterfactual review | observe | observe |
| 2026-05-31 | Phase223 | Monitoring | Phase 223 high break coverage audit | observe | observe |
| 2026-05-31 | Phase224 | Entry | Phase 224 early momentum entry cohort review | observe | observe |
| 2026-05-31 | Phase225 | Entry | Phase 225 entry interaction discovery review | observe | observe |
| 2026-05-31 | Phase226 | Monitoring | Phase 226 stop cluster counterfactual review | observe | observe |
| 2026-05-31 | Phase227 | Entry | Phase 227 positive entry interaction discovery review | observe | observe |
| 2026-05-31 | Phase228 | Entry | Phase 228 entry expectancy discovery | observe | observe |
| 2026-06-04 | Phase229 | Entry | Phase 229 entry score discovery | observe | observe |
| 2026-05-31 | Phase230 | Entry | Phase 230 entry expectancy shadow observation | observe | observe |
| 2026-05-31 | Phase231 | Monitoring | Phase 231 score cohort expectancy discovery | observe | observe |
| 2026-05-31 | Phase232 | Entry | Phase 232 entry feature leak audit | observe | observe |
| 2026-06-04 | Phase233 | Entry | Phase 233 entry expectancy shadow validation | observe | observe |
| 2026-06-04 | Phase234 | Monitoring | Phase 234 paper trade sim harness | observe | observe |
| 2026-06-04 | Phase235 | Entry | Phase 235 entry score attribution | observe | observe |
| 2026-06-04 | Phase236 | Entry | Phase 236 entry score counterfactual repair | observe | observe |
| 2026-06-04 | Phase238 | Entry | Phase 238 entry score v2 full history validation | observe | observe |
| 2026-06-04 | Phase239 | Entry | Phase 239 entry score ge5 gate system comparison | observe | observe |
| 2026-06-04 | Phase241 | Position | Phase 241 max concurrent review | observe | observe |
| 2026-06-04 | Phase242 | Data | Phase 242 intraday refresh root cause | observe | observe |
| 2026-06-04 | Phase242 | Position | Phase 242 max concurrent counterfactual | observe | observe |
| 2026-06-04 | Phase242b | Data | Phase 242b intraday refresh fix report | observe | observe |
| 2026-06-04 | Phase243 | Monitoring | Phase 243 fast validation framework | observe | observe |
| 2026-06-04 | Phase244 | Monitoring | Phase 244 fast validation coverage expansion | observe | observe |
| 2026-06-04 | Phase245 | Position | Phase 245 max concurrent counterfactual | observe | observe |
| 2026-06-04 | Phase246 | Monitoring | Phase 246 v2 priority simulation | observe | observe |
| 2026-06-14 | Phase246 | Data | Phase 246 sector heat observation | observe | observe |
| 2026-06-04 | Phase247 | Monitoring | Phase 247 v2 gap analysis | observe | observe |
| 2026-06-14 | Phase247 | Data | Phase 247 sector heat diagnostics | observe | observe |
| 2026-06-04 | Phase248 | Monitoring | Phase 248 v2 adoption decision | observe | observe |
| 2026-06-14 | Phase248 | Data | Phase 248 sector heat debias validation | observe | observe |
| 2026-06-14 | Phase249 | Universe | Phase 249 sector heat universe shadow simulation | observe | observe |
| 2026-06-14 | Phase250 | Data | Phase 250 sector heat data alignment diagnostics | observe | observe |
| 2026-06-04 | Phase251 | Universe | Phase 251 universe discovery | observe | observe |
| undated | Phase251 | Data | Phase 251 sector heat extend intraday data | observe | observe |
| 2026-06-04 | Phase252 | Universe | Phase 252 universe counterfactual | observe | observe |
| 2026-06-14 | Phase252 | Data | Phase 252 sector heat trade attribution | observe | observe |
| 2026-06-04 | Phase253 | Monitoring | Phase 253 exclusion attribution | observe | observe |
| 2026-06-14 | Phase253 | Data | Phase 253 sector heat negative filter shadow | observe | observe |
| 2026-06-04 | Phase254 | Monitoring | Phase 254 price floor adoption review | adopted | observe |
| 2026-06-14 | Phase254 | Data | Phase 254 sector heat negative filter robustness | adopted | observe |
| 2026-06-04 | Phase255 | Monitoring | Sector heat forward shadow logger | superseded | superseded |
| undated | Phase256 | Data | Sector heat forward shadow auto hook | observe | observe |
| 2026-06-14 | Phase257 | Position | Phase 257 core12 dynamic38 pricecap shadow review | adopted | observe |
| 2026-06-14 | Phase258 | Position | Phase 258 pricecap off attribution | observe | observe |
| 2026-06-14 | Phase259 | Monitoring | Phase 259 price band policy shadow | observe | observe |
| 2026-06-04 | Phase260 | Monitoring | Phase 260 priority analysis | observe | observe |
| 2026-06-14 | Phase260a | Position | Phase 260a position exposure audit | observe | observe |
| 2026-06-14 | Phase260b | Position | Phase 260b equity position sizing shadow | observe | observe |
| 2026-06-14 | Phase261 | Risk | Phase 261 risk aware sizing shadow | observe | observe |
| 2026-06-14 | Phase262 | Risk | Risk-aware sizing forward shadow logger | observe | observe |
| 2026-06-04 | Phase263 | Position | Phase 263 max concurrent time concentration | superseded | superseded |
| 2026-06-14 | Phase263 | Sizing | Phase 263 equity dynamic stop shadow | observe | observe |
| 2026-06-04 | Phase264 | Monitoring | Phase 264 replacement counterfactual | observe | observe |
| 2026-06-04 | Phase265 | Entry | Phase 265 entry quality concentration window | observe | observe |
| 2026-06-14 | Phase265 | Monitoring | Phase 265 structural trades backfill | observe | observe |
| 2026-06-03 | Phase266 | Sizing | Equity dynamic stop forward shadow auto | observe | observe |
| 2026-06-04 | Phase266b | Monitoring | Phase 266b adoption audit | observe | observe |
| 2026-06-14 | Phase267 | Entry | entry_score_v2 gate (min=3); quality reject off | adopted | active |
| 2026-06-04 | Phase268 | Universe | Phase 268 daily runner default universe review | superseded | superseded |
| 2026-06-14 | Phase268 | Position | Phase 268 capital simulation reconciliation | observe | observe |
| 2026-06-04 | Phase269 | Risk | Phase 269 daily runner default price risk implementation | observe | observe |
| 2026-06-14 | Phase269 | Config | Phase 269 portfolio configuration optimization | observe | observe |
| 2026-06-14 | Phase270 | Sizing | Equity bucket recommendation (mixed leverage) | superseded | removed |
| 2026-06-14 | Phase271 | Monitoring | Leverage 1.5 bucket recommendation | superseded | removed |
| 2026-06-14 | Phase272 | Sizing | Phase 272 apply leverage robustness to equity bucket recommendation | observe | observe |
| 2026-06-04 | Phase273 | Entry | Live config forward shadow (Phase272 configs) | superseded | superseded |
| 2026-06-14 | Phase274 | Config | Live config auto-transition shadow (1.5M→2M band) | observe | observe |
| 2026-06-04 | Phase276 | Discord | Phase 276 discord notification ux implementation | observe | observe |
| 2026-06-04 | Phase277 | Discord | Phase 277 discord notification ux completion | observe | observe |
| 2026-06-04 | Phase278 | Discord | Phase 278 discord notification demo send | observe | observe |
| 2026-06-04 | Phase279 | Discord | Phase 279 discord refresh summary polish | observe | observe |
| 2026-06-04 | Phase280 | Universe | Phase 280 universe refresh readability fix | observe | observe |
| 2026-06-04 | Phase281 | Discord | Discord channel split (trade vs cap-blocked) | adopted | active |
| 2026-06-04 | Phase282 | Discord | Phase 282 discord live flow validation | observe | observe |
| 2026-06-04 | Phase283 | Replay | Phase 283 fast realtime replay discord flow | observe | observe |
| 2026-06-04 | Phase284 | Replay | Phase 284 fast replay current system discord consistency | observe | observe |
| 2026-06-04 | Phase285 | Monitoring | Phase 285 symbol concentration analysis | observe | observe |
| 2026-06-05 | Phase287 | Monitoring | Phase 287 auxiliary filter relaxation | observe | observe |
| 2026-06-05 | Phase287 | Universe | Phase 287 initial screening universe notify fix | observe | observe |
| 2026-06-05 | Phase288 | Monitoring | Phase 288 symbolspec subscript crash fix | observe | observe |
| 2026-06-07 | Phase289 | Entry | Phase 289 entry score v2 factor attribution | observe | observe |
| 2026-06-07 | Phase290 | Entry | Phase 290 entry score v2 reweight review | observe | observe |
| 2026-06-07 | Phase291 | Monitoring | Phase 291 score4 profit source audit | observe | observe |
| 2026-06-07 | Phase292 | Monitoring | Phase 292 score generation integrity audit | observe | observe |
| 2026-06-07 | Phase293 | Monitoring | Phase 293 score pregate feature fix review | observe | observe |
| 2026-06-07 | Phase294 | Monitoring | Phase 294 score fix with overtrade guard review | observe | observe |
| 2026-06-07 | Phase295 | Monitoring | Phase 295 hbrecent pregate fix report | observe | observe |
| 2026-06-07 | Phase296 | Monitoring | Phase 296 duration cutoff validation | observe | observe |
| 2026-06-07 | Phase297 | Monitoring | Phase 297 score5 consistency audit | observe | observe |
| 2026-06-07 | Phase299 | Monitoring | Phase 299 board pregate fix report | observe | observe |
| 2026-06-07 | Phase300 | Monitoring | Phase 300 board live payload availability report | observe | observe |
| 2026-06-07 | Phase301 | Monitoring | Phase 301 board live confirmation | observe | observe |
| 2026-06-07 | Phase302 | Monitoring | Phase 302 duration bottleneck audit | observe | observe |
| 2026-06-07 | Phase303 | Monitoring | Phase 303 duration cutoff origin audit | observe | observe |
| 2026-06-07 | Phase304 | Monitoring | Phase 304 duration value review | observe | observe |
| 2026-06-07 | Phase305 | Monitoring | Phase 305 duration weight review | observe | observe |
| 2026-06-07 | Phase306 | Monitoring | Phase 306 token fire rate profit attribution | observe | observe |
| 2026-06-07 | Phase307 | Monitoring | Phase 307 price token removal review | observe | observe |
| 2026-06-07 | Phase308 | Entry | Phase 308 rebuilt entry score review | observe | observe |
| 2026-06-07 | Phase309 | Monitoring | Phase 309 d outlier check | observe | observe |
| 2026-06-07 | Phase310 | Monitoring | Phase 310 remove duration price tokens report | observe | observe |
| 2026-06-07 | Phase311 | Monitoring | Phase 311 repoint score after token removal report | observe | observe |
| 2026-06-07 | Phase312 | Monitoring | Phase 312 min threshold tv review | observe | observe |
| 2026-06-07 | Phase313 | Monitoring | Phase 313 hbrecent necessity review | observe | observe |
| 2026-06-07 | Phase313 | Monitoring | Phase 313 remove tv token report | observe | observe |
| 2026-06-07 | Phase314 | Entry | Entry score v2 simplification (Momentum+Board only) | adopted | active |
| 2026-06-07 | Phase315 | Monitoring | Phase 315 100share yen expectancy report | observe | observe |
| 2026-06-07 | Phase316 | Exit | Phase 316 exit discord 100share yen notification report | observe | observe |
| 2026-06-13 | Phase317 | Monitoring | Phase 317 tomorrow paper trade preflight | observe | observe |
| 2026-06-13 | Phase318 | Replay | Phase 318 current production logic replay | observe | observe |
| 2026-06-13 | Phase319 | Exit | Phase 319 exit reason pnl diagnosis | observe | observe |
| 2026-06-13 | Phase320 | Monitoring | Phase 320 board gate live effectiveness audit | observe | observe |
| 2026-06-13 | Phase321 | Monitoring | Phase 321 stop hit mechanism review | observe | observe |
| 2026-06-13 | Phase322 | Monitoring | Phase 322 trailing activation sensitivity review | observe | observe |
| 2026-06-13 | Phase323 | Monitoring | Phase 323 trailing activation full day review | observe | observe |
| 2026-06-13 | Phase324 | Exit | Phase 324 exit feature separation review | observe | observe |
| 2026-06-13 | Phase325 | Exit | Phase 325 vwap exit discovery | observe | observe |
| 2026-06-13 | Phase326 | Monitoring | Phase 326 vwap threshold distribution review | observe | observe |
| 2026-06-13 | Phase327 | Monitoring | Phase 327 vwap contraction threshold review | observe | observe |
| 2026-06-13 | Phase328 | Replay | Phase 328 vwap contraction full replay review | observe | observe |
| 2026-06-13 | Phase329 | Monitoring | Phase 329 trailing context sensitivity review | observe | observe |
| 2026-06-13 | Phase330 | Monitoring | Phase 330 board dynamic trailing review | observe | observe |
| 2026-06-13 | Phase331 | Replay | Phase 331 board dynamic trailing full replay review | observe | observe |
| 2026-06-13 | Phase332 | Exit | Board-dynamic trailing-MFE production EXIT | adopted | active |
| 2026-06-13 | Phase333 | Discord | Canonical 100-share yen summary | adopted | active |
| 2026-06-13 | Phase336 | Replay | Phase 336 realtime board full replay evaluation | observe | observe |
| 2026-06-13 | Phase337 | Exit | Phase 337 exit candidate shadow evaluation | observe | observe |
| 2026-06-13 | Phase338 | Exit | Phase 338 exit candidate validation | observe | observe |
| 2026-06-13 | Phase339 | Monitoring | Phase 339 vwap assisted loss tuning | observe | observe |
| 2026-06-13 | Phase340 | Monitoring | Phase 340 vwap dev finetune | observe | observe |
| 2026-06-13 | Phase341 | Monitoring | Phase 341 vwap 0p4pct robustness | observe | observe |
| 2026-06-13 | Phase342 | Exit | Phase 342 board failure exit | observe | observe |
| 2026-06-13 | Phase343 | Monitoring | Phase 343 board failure mfe tuning | observe | observe |
| 2026-06-13 | Phase343pre_parallel_eval_benchmark | Monitoring | Phase 343pre parallel eval benchmark | observe | observe |
| 2026-06-13 | Phase344 | Monitoring | Phase 344 board failure mfe0p2 confirm5 robustness | observe | observe |
| 2026-06-13 | Phase345 | Monitoring | Phase 345 board failure forensic | observe | observe |
| 2026-06-13 | Phase346 | Monitoring | Phase 346 board failure false positive guard | observe | observe |
| 2026-06-13 | Phase347 | Monitoring | Phase 347 board failure cooldown finetune | observe | observe |
| 2026-06-13 | Phase348 | Entry | Phase 348 20260612 entry failure review | observe | observe |
| 2026-06-13 | Phase349 | Monitoring | Phase 349 20260612 limit gap failure review | observe | observe |
| 2026-06-13 | Phase350 | Entry | Phase 350 recent3 entry guard validation | observe | observe |
| 2026-06-13 | Phase351 | Entry | Limit-up proximity entry guard | rejected | rejected |
| 2026-06-13 | Phase352 | Monitoring | Phase 352 limit up guard historical validation | observe | observe |
| 2026-06-13 | Phase353 | Monitoring | Phase 353 bad regime detection | observe | observe |
| 2026-06-13 | Phase353 | Monitoring | Phase 353 pullback misread historical validation | observe | observe |
| 2026-06-13 | Phase354 | Universe | Phase 354 pullback universe split validation | adopted | observe |
| 2026-06-13 | Phase355 | Entry | Pullback misread Dynamic40 guard | adopted | active |
| 2026-06-13 | Phase356 | Exit | Phase 356 post phase355 exit rebaseline | observe | observe |
| 2026-06-13 | Phase357 | Exit | Phase 357 actual exit audit | observe | observe |
| 2026-06-13 | Phase358 | Monitoring | Phase 358 low mfe stophit forensic | observe | observe |
| 2026-06-13 | Phase359 | Entry | Gap-up fade entry guard | rejected | rejected |
| 2026-06-13 | Phase360 | Monitoring | Phase 360 eother classification | observe | observe |
| 2026-06-13 | Phase361 | Monitoring | Phase 361 near day high low mom guard validation | adopted | observe |
| 2026-06-13 | Phase362 | Monitoring | Phase 362 stack validation | observe | observe |
| 2026-06-13 | Phase363 | Monitoring | Phase 363 c03 robustness validation | adopted | observe |
| 2026-06-13 | Phase365 | Monitoring | Phase 365 production stack validation | adopted | observe |
| 2026-06-13 | Phase366 | Monitoring | Phase 366 stophit reclassification | observe | observe |
| 2026-06-13 | Phase367 | Monitoring | Phase 367 low mfe residual forensic | adopted | observe |
| 2026-06-13 | Phase368 | Entry | Symbol reentry cluster guard | rejected | rejected |
| 2026-06-13 | Phase369 | Monitoring | Phase 369 a1 deep split | observe | observe |
| 2026-06-13 | Phase370 | Entry | K10 stop-chain A1 guard | rejected | rejected |
| 2026-06-13 | Phase371 | Exit | High-MFE stop_hit exit recovery | rejected | rejected |
| 2026-06-13 | Phase372 | Monitoring | Phase 372 low mfe immediate death forensic | observe | observe |
| 2026-06-13 | Phase373 | Monitoring | Phase 373 production monitoring | observe | observe |
| 2026-06-13 | Phase374 | Universe | Phase 374 dynamic40 universe quality review | observe | observe |
| 2026-06-13 | Phase375 | Universe | Dynamic40 rank quality full replace | rejected | rejected |
| 2026-06-13 | Phase376 | Sizing | Phase 376 production daily pnl review | observe | observe |
| 2026-06-13 | Phase377 | Sizing | Phase 377 daily regime breakdown | observe | observe |
| 2026-06-13 | Phase378 | Sizing | Phase 378 period b loss concentration | observe | observe |
| 2026-06-13 | Phase379 | Sizing | Phase 379 low mfe stophit deep review | observe | observe |
| 2026-06-13 | Phase380 | Entry | Phase 380 board quality entry signal review | observe | observe |
| 2026-06-14 | Phase381 | Sizing | Phase 381 winner profile review | observe | observe |
| 2026-06-13 | Phase382 | Position | Phase 382 capital audit | observe | observe |
| 2026-06-13 | Phase382 | Position | Phase 382 capital constrained backtest | observe | observe |
| 2026-06-13 | Phase382 | Sizing | Phase 382 profit driver preservation monitor | observe | observe |
| 2026-06-13 | Phase383 | Sizing | Phase 383 realistic credit sizing backtest | observe | observe |
| 2026-06-13 | Phase384 | Position | Phase 384 capital scaling study | observe | observe |
| 2026-06-14 | Phase385 | Position | Phase 385 cap sensitivity study | observe | observe |
| 2026-06-14 | Phase386 | Position | Phase 386 third position quality review | observe | observe |
| 2026-06-14 | Phase387 | Position | Phase 387 cap2 shadow validation | observe | observe |
| 2026-06-14 | Phase388 | Position | Phase 388 cap1500k live candidate validation | observe | observe |
| 2026-06-14 | Phase389 | Sizing | Phase 389 full regime live candidate validation | observe | observe |
| undated | Phase390 | Monitoring | Phase 390 system source of truth v3 expansion | observe | observe |
| 2026-06-13 | PhaseNP-canonical-summary | Discord | Canonical summary builder | adopted | active |
| 2026-06-13 | PhaseNP-entry-scan | Entry | Entry scan controller (freshness/batch) | adopted | active |

---

# Appendix B — Runtime Snapshot History

Curated runtime epochs（Appendix B 維持）。6/12 Incident Runtime 確認用。

| Date | Runtime Name | Universe | Entry | Exit | CAP | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-06 | Genesis | manual CSV / screening | prototype gate | not integrated | — | first commit; Discord notice only |
| 2026-05-18 | Observer v1 | pre top50 exploration | quality + structural gates | stop / session / overlap | 3 | Phase55 small paper observer |
| 2026-05-29 | Core10 + Dynamic40 v1 | 113/117 vol-liq top50 | quality≥0.70 + price risk | structural + fade trials | 3 | two-layer universe; fade exit trials |
| 2026-06-04 | Trailing Shadow | core10+d40 + AM/PM refresh (148) | quality + score shadow | Phase174 fixed 0.8%/50% shadow | 3 | trailing-MFE shadow policy name |
| 2026-06-07 | Score v2 Transition | core10+d40 price-risk | Phase314 score_v2≥3 migration | fixed trailing + structural | 3 | departing from v1 multi-factor score |
| 2026-06-09 | Pre-332 Runtime | core10+d40 price-risk | score_v2≥3 + 153b price risk | Phase332 board-dynamic (replay OK, YAML pending) | 3 | production_adoption_ok; YAML sync on 6/13 |
| 2026-06-12 | 6/12 Incident Runtime | core10+d40 price-risk | score_v2≥3 + 153b; **355/364 off** | trailing-MFE (332 or 174 transition) | 3 | **6/12 AM incident** — Dynamic40 losses; guards not applied |
| 2026-06-13 | Stack C Production | 113/117/269 + AM/PM refresh | 267/314 + 355 + 364 + freshness | Phase332 board-dynamic | 3 | kabutrade0612; 333/281 ops |
| 2026-06-14 | Current Runtime | same as Stack C | same as Stack C | same as Stack C | 3 | forward shadows 273/274; CAP=2 research only |

### Stack A / B / C (Phase365 research labels)

| Stack | ENTRY delta | Phase365 PnL | PF | Status |
| --- | --- | --- | --- | --- |
| A baseline | no guards | +160,510 | 1.0526 | research |
| B +355 | pullback D40 | +260,910 | 1.1070 | research |
| C +355+364 | near-day-high D40 | +483,110 | 1.2607 | production |

---

# Appendix C — Shadow Evolution History

| Start Date | Shadow | Purpose | Status | Current Verdict |
| --- | --- | --- | --- | --- |
| 2026-06-04 | Phase255/256 SectorHeat | sector heat negative filter forward accumulation | active | observe |
| 2026-06-14 | Phase262 RiskSizing | risk-aware sizing forward vs fixed_100 | active | observe |
| 2026-06-03 | Phase266 EquityDynamicStop | equity dynamic stop vs fixed 1.2% | active | observe |
| 2026-06-04 | Phase273 LiveConfig | Phase272 live config bucket forward equity curves | active | observe |
| 2026-06-14 | Phase274 AutoTransition | 1.5M start → equity≥2M auto CAP5/dynamic | active | observe |
| 2026-06-14 | Phase387 CAP2 Shadow | CAP=2 vs CAP=3 production monitoring | active | observe |
| 2026-06-09 | Phase332 legacy trailing | fixed 0.8%/50% counterfactual vs board-dynamic | active | superseded |
| 2026-06-13 | Phase355 pullback shadow | pullback guard reject logging | active | adopted |
| 2026-06-12 | Phase351 limit-up | limit-up proximity guard trial | active | rejected |
| 2026-06-04 | Phase335/214/186/230 inline | board exit / imbalance / vwap / score shadow | active | observe |

---

# Appendix D — Failure Archive

| Date | Failure | Root Cause | Resolution | Current State |
| --- | --- | --- | --- | --- |
| 20260518–27 | Period A Loss | regime + universe + overlap + exit composite | Period A/B split monitoring (377); guards target Period B | active risk |
| 20260612 | 6/12 Incident | Dynamic40 pullback misread + near-high low-mom entries | Phase355 + Phase364 adopted (Stack C) | partially mitigated |
| 2026-06-04 | Phase174 Fixed Trailing | board regime blindness (0.8%/50% universal) | Phase332 board-dynamic trailing | superseded |
| 2026-06-06 | Research PF Misinterpretation | unconstrained PF positive but capital path negative | Phase268 dual-layer; final_equity primary | active policy |
| 2026-06-13 | Phase362-B (C03 all symbols) | PF max but Core10 side-effect + single-day dependence | Phase365 → Dynamic40-only Phase364 | superseded |
| 2026-06-14 | Leverage 1.5 Hypothesis | 9-day sample non-robust (271) | Phase272 lev2.0 fixed | superseded |
| 2026-06-14 | CAP2 Misunderstanding | research positive at 1.5M ≠ runtime adopt | cap3 maintained; 387/388 observe | runtime 未採用 |
| 2026-05-27 | Fade / Momentum Exit Path | PF unstable; board-dependent | removed from production EXIT | removed |
| 2026-06-08 | Yahoo Replay Fidelity Gap | board/PUSH dependent logic not reproducible offline | Risk S1 documented; forward shadow required | active risk |
| 2026-06-14 | Low-MFE stop_hit Residual | stop_hit after minimal MFE post-guards | 379 research ongoing; no production fix | unresolved |

---

# Phase391 — Generator Report

## 追加章（v5）

| # | Section | Output |
| --- | --- | --- |
| 1 | Runtime Change Log | main MD + runtime_change_log.md |
| 2 | Stack Evolution | main MD |
| 3 | Runtime Dependency Graph | main MD + runtime_dependency_graph.md |
| 4 | Adoption Funnel | main MD + runtime_adoption_funnel.md |
| 5 | Adopted Then Removed | main MD + runtime_adoption_funnel.md |
| 6 | Runtime Delta Timeline | main MD + runtime_change_log.md |
| 7 | Current Runtime Provenance | main MD + runtime_dependency_graph.md |

## 即答チェックリスト

| Question | Answer |
| --- | --- |
| 今のRuntime世代 | **9** (Current) |
| 6/12前後の変更 | Runtime Delta Timeline / What Changed Since 6/12 |
| Stack C構成Phase | Runtime Dependency Graph |
| 採用後削除・置換 | Adopted Then Removed |
| 330 Phase funnel | Adoption Funnel (331 rows) |
| CAP=2 | **Research** (388/389/387) — runtime **cap3** |

## 変更なし確認

| Layer | Changed |
| --- | --- |
| Runtime | **No** |
| Universe | **No** |
| Entry | **No** |
| Exit | **No** |
| YAML | **No** |

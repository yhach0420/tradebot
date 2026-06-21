# Phase471 — Momentum Component Attribution Audit

**Verdict:** `pullback_v2_candidate`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最も効く成分 | score_cutoff (momentum_continuation_score<=0.2546) |
| 2 | 6976を守る成分 | score_cutoff |
| 3 | 6/18崩壊を防ぐ成分 | score_cutoff |
| 4 | 4062悪化成分 | none_at_score_cutoff; late_chase_helps_4062 |
| 5 | score cutoff単体必要 | True |
| 6 | near_day_high guard単体 | False |
| 7 | pullback_misread guard単体 | False |
| 8 | 明示条件置換可能 | True |
| 9 | 最良variant | PBv2-3 (Board + extracted best + Late Chase + HD + WS) |
| 10 | A baseline上回る | True |
| 11 | Late Chase併用 | Yes — L = score cutoff + Late Chase; delta vs A=45199.92 (Phase469 B equivalent when C≡A) |
| 12 | Runtime候補 | False |
| 13 | Shadow候補 | L / PBv2-3 |

## Tournament

| rank | var | PnL | PF | maxDD | acc | Δ vs A |
|---:|---|---:|---:|---:|---:|---:|
| 1 | PBv2-3 | 402962.82 | 1.9886 | 71000.0 | 256 | 45199.92 |
| 2 | L | 402962.82 | 1.9886 | 71000.0 | 256 | 45199.92 |
| 3 | A | 357762.9 | 1.7638 | 71000.0 | 278 | 0.0 |
| 4 | C | 357762.9 | 1.7638 | 71000.0 | 278 | 0.0 |
| 5 | I | 357762.9 | 1.7638 | 71000.0 | 278 | 0.0 |
| 6 | PBv2-2 | 357762.9 | 1.7638 | 71000.0 | 278 | 0.0 |
| 7 | E | 296364.04 | 2.3888 | 59000.0 | 144 | -61398.86 |
| 8 | B | 109551.99 | 1.1589 | 137000.0 | 325 | -248210.91 |
| 9 | G | 52751.21 | 1.0664 | 218300.0 | 370 | -305011.69 |
| 10 | F | 52751.21 | 1.0664 | 218300.0 | 370 | -305011.69 |
| 11 | K | 52751.21 | 1.0664 | 218300.0 | 370 | -305011.69 |
| 12 | PBv2-1 | 52751.21 | 1.0664 | 218300.0 | 370 | -305011.69 |
| 13 | H | 17951.21 | 1.0208 | 277900.0 | 367 | -339811.69 |
| 14 | D | 0 | None | 0.0 | 0 | -357762.9 |
| 15 | J | -29037.1 | 0.9599 | 278400.0 | 272 | -386800.0 |

## Part A — Code path audit

| file | function | condition | reject_reason | runtime_sequence |
|---|---|---|---|---|
| live_feature_bridge.py | `_momentum_score` | `0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy` | — | tick update → score field |
| entry_expectancy_score_shadow.py | `_feature_token(Momentum)` | score <= p33 (0.2546) → `Momentum:low` | — | ExposureGate pre-check |
| entry_expectancy_score_shadow.py | `momentum_low_required_for_v2` | `"Momentum:low" in tokens` | `momentum_low_required` | ExposureGate step 3 |
| exposure_gate.py | `evaluate_entry` | v2: momentum + board + score>=3 | `momentum_low_required` / `entry_score_v2_below_threshold` | step 4 |
| near_day_high_low_momentum_dynamic40_entry_guard.py | `check` | Dynamic40 + near high + mom<0.30 | `near_day_high_low_momentum_dynamic40_guard` | phase364 guard |
| pullback_misread_dynamic40_entry_guard.py | `check` | Dynamic40 + r5<0 + vwap_dev<0 | `pullback_misread_dynamic40_guard` | phase355 guard |
| phase365 / pass_a0 | `phase364_blocked_only` | near-high-low-mom on Dynamic40 | — | replay baseline block |

## Component decomposition (A vs B extras)

| component | blocked | blocked PnL | protects 6976 | protects 618 | hurts 4062 |
|---|---:|---:|---:|---:|---:|
| score_cutoff | 6109 | -15530.95 | 2000.41 | -0.0 | 0.0 |
| price_mom_low | 6109 | -15530.95 | 2000.41 | -0.0 | 0.0 |
| vwap_part_low | 5543 | -15430.95 | 2000.41 | -0.0 | 0.0 |
| mfe_proxy_low | 0 | 0 | 0 | 0 | 0 |
| near_day_high_guard | 0 | 0 | 0 | 0 | 0 |
| pullback_misread_guard | 0 | 0 | 0 | 0 | 0 |
| momentum_low_token | 6109 | -15530.95 | 2000.41 | -0.0 | 0.0 |

Note: pool-level shadow PnL understates capacity-replay delta (−248k A vs B). Tournament rows are authoritative.

## 解釈

**Momentum:low ≡ score cutoff** — Variant C (explicit `momentum_continuation_score <= 0.2546`) is **bit-identical** to A (278 accepted, +357,763). The tertile token adds no extra filtering beyond the fixed p33 cutoff.

**Guards alone fail** — G/H/K (near_day_high or pullback_misread only, no score filter) lose −305k to −340k vs A. Production phase364 guard is necessary but **not sufficient** without low-momentum score filter.

**Component isolation** — D (price_mom only) accepts 0 trades (logged `pure_price_momentum` sparse). E (vwap_part) over-filters (−61k). F (mfe_proxy) ≡ G (guard-only path).

**Best: PBv2-3 / L** — score cutoff + Late Chase Guard = Phase469 B (+45,200 vs A). 6976 preserved (+221k), 6/18 preserved (+14.6k), 4062 improved (+15k).

**6/18 attribution** — B accepts 6976 on 6/18 (−137k single trade); A/C/L block it via score cutoff.

## 次アクション

1. **Shadow PBv2-3** — explicit score<=0.2546 + Late Chase (Phase469 B 再現)
2. **Runtime変更なし** — Momentum:low token は score cutoff と等価; 急ぎの token 削除不要
3. **Guard単体導入禁止** — G/H 結果が −305k 以下
4. **Pullback Gate v2 設計** — `Board:mid/high + score<=0.2546 + HD + WS + LateChase` を明示条件化候補

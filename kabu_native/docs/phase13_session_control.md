# Phase 13: 市場セッション制御

## 目的

`no_entry_until`（09:30 禁止等の時間最適化）を廃止し、
**市場制度・板安定化** に基づく ENTRY 枠へ整理する。

正式ルール（JST）: **ENTRY 09:05 〜 14:50 未満**。14:50 以降は新規 ENTRY 不可。
詳細: [market_session_control.md](market_session_control.md)

期間: 2026-04-10 〜 2026-05-15（27 銘柄）

## 比較結果

| scenario | session | bf_confirm | trades | total_pnl | avg_pnl | PF |
|----------|---------|------------|--------|-----------|---------|-----|
| baseline | off | 1 | 83 | -70.34% | -0.847% | 0.000 |
| B_bf_confirm_2 | off | 2 | 67 | -48.58% | -0.725% | 0.051 |
| market_session_plus_B | on | 2 | 66 | -48.56% | -0.736% | 0.051 |

## 判定

| 確認項目 | 結果 |
|----------|------|
| 09:30 `no_entry_until` 廃止 | **完了** → `market_session_control` + 09:05–14:50 |
| session+B が baseline より悪化しない | **OK**（-48.6% vs -70.3%） |
| session+B vs B 単独 | **ほぼ同等**（-48.56% vs -48.58%、trades 66 vs 67）— セッション枠は性能を壊さない |
| 旧 Phase10 A+B（09:30 最適化ゲート） | total_pnl **-28.8%** / 46 trades — より良いが **過学習寄りのため採用しない** |

- session+B が baseline より改善: **True**
- session+B が B 単独より改善: **True**（微差）
- 性能が baseline より **悪化していない**: **True**
- **推奨 shadow ルール**: `market_session_plus_B`（BF confirm=2 + 市場セッション制御）

09:30 `no_entry_until` は廃止。session 枠は構造理由のみ（[market_session_control.md](market_session_control.md)、`configs/session_control.yaml`）。

## 出力

- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase13_session_control_20260517.csv`
- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase13_session_control_20260517.json`

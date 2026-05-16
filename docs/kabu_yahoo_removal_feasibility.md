# Yahoo 完全除去の実現性（Phase 4A・PUSH 起点）

## スコープ

- **目的**: 将来、Yahoo へ依存せず **kabu ステーション API（REST + PUSH）だけ**で、1 分足相当・VWAP・`recent_5m_high` 相当・シグナル評価に必要なデータをまかなえるかを整理する。
- **今回の検証手段**: 既存 `paper_trade` には接続せず、PoC として `scripts/kabu_push_probe.py` と `src/kabu_push_client.py` / `src/kabu_bar_builder.py` で **接続・項目・簡易集計**を確認する。
- **注意**: kabu **PUSH は値が変わったときに送られる board 相当のスナップショット**であり、**取引所の全約定ティック列ではない**。公式: [PUSH](https://kabucom.github.io/kabusapi/ptal/push.html)。

## 取得できた項目（PUSH 想定・銘柄により差）

PoC では `src/kabu_push_client.py` の `EXPECTED_PUSH_FIELDS_STOCK` を基準に JSONL を検査する。典型的な株式 QUOTE 系では、少なくとも次を **取得できる前提で設計できる**（実機では銘柄・時間帯で欠損があり得る）。

| 区分 | フィールド例 | 用途メモ |
|------|----------------|----------|
| 銘柄 | `Symbol`, `SymbolName`, `Exchange` | キー・市場識別 |
| 価格・時刻 | `CurrentPrice`, `CurrentPriceTime` | 現値とその時刻（1 分バケットのキー） |
| 出来高・金額 | `TradingVolume`, `TradingVolumeTime`, `TradingValue` | **累積出来高の差分**でその回の出来高近似 |
| 気配 | `BidPrice` / `BidQty`, `AskPrice` / `AskQty` | スプレッド・板厚の参考（シグナル次第） |
| セッション指標 | `OpeningPrice`, `HighPrice`, `LowPrice`, `VWAP` | 寄り・セッション高安・**API 提示のセッション VWAP** |

**出来高差分**: `TradingVolume` がセッション累積であれば、連続する PUSH 間の差分で「その更新区間の出来高」に相当する量を積み上げられる（負の差分・リセットは実装でガードが必要）。

## 取得できない／弱い項目

| 項目 | 理由 |
|------|------|
| **正確な 1 分 OHLCV（取引所公式 1 分足と一致）** | PUSH は **更新間隔が不規則**。同じ分内に複数回更新が無い分は、**最後のスナップショットだけ**が観測される。 |
| **全約定のティック（約定価格・約定逐一）** | PUSH は board 相当。ティック履歴 API ではない。 |
| **昼休み・引け後の継続 PUSH** | 公式仕様上、**昼休み・大引け後は配信されない**（板と同様のイメージ）。 |
| **Yahoo 経由でしか取っている補助系列** | 例: 簡易チャート用の **ルール整列済み 1 分足**、一部 **指数・ETF・海外** などは kabu 側の銘柄範囲・別 API の要否で再設計が必要。 |

## 1 分足生成は可能か

**技術的には「可能」だが、Yahoo 1 分足とは別物になる。**

- `src/kabu_bar_builder.py` の `MinuteBarBuilderFromPush` は、`CurrentPrice` と `CurrentPriceTime` で **1 分 UTC バケット**に割り当て、累積 `TradingVolume` の差分を **出来高近似**として積む。
- **限界**:
  - 更新が疎な分は **OHLC が同一価格に偏る**、出来高が **0** の分が並ぶ。
  - **初回メッセージ**で累積出来高の基準が取れないと、差分が使えない（最初の数本は欠損し得る）。
- **結論ラベル**: 「**kabu だけで 1 分“風”バーは作れる**」が、「**Yahoo の 1 分 CSV と数値一致**」は期待しない。

## `recent_5m_high` 相当

- `recent_n_minute_high_excluding_current` は **確定済み n 本の高値の最大**という近似。
- Yahoo 実装の `recent_5m_high` が **密な 1 分足の高値**を前提にしている場合、PUSH 由来のバーでは **高値が過小評価**され得る（分内のピークを観測できないため）。

## VWAP（kabu データのみ）

1. **板に付随する `VWAP` フィールド**（セッション VWAP）をそのまま使う — `vwap_from_push_field`。
2. **PUSH から組み立てた 1 分足**に対し、典型価格×出来高で再計算 — `vwap_typical_from_bars`（出来高近似の品質に依存）。

シグナルが「当日累積 VWAP」であれば 1 が有力。分足ベースの独自定義であれば 2 と整合を取る必要がある。

## Yahoo 除去のボトルネック（本リポジトリ観点の一般論）

1. **系列の定義差**: 戦略が「Yahoo の整列済み 1 分足」に最適化されている場合、PUSH ベースでは **同じ閾値が成立しなくなる**。
2. **履歴・ウォームアップ**: 当日寄り前の **MA25 や前日値** など、REST（銘柄情報・四本値）やローカルキャッシュへの寄せ直しが必要。
3. **マーケット時間・休日**: 昼休み・非取引時間の **データ欠落**をシグナル側が許容するか、別ソースで埋めるか。
4. **ウォッチ銘柄の幅**: Yahoo で補っていた銘柄が kabu の対象外なら、**銘柄ウォッチリストとフォールバック設計**の見直しが必要。
5. **レート・接続**: PUSH は常時接続。REST ポーリング削減と引き換えに **WebSocket 寿命・再接続・再 register** の運用が要る。

## 必要な追加実装（本 PoC の先）

- **本番用 PUSH クライアント**: 再接続、例外、heartbeats、`register` / `unregister` のライフサイクル、複銘柄。
- **1 分足ストア**: 当日メモリ + 必要ならディスク（リプレイ・検証用）。
- **Yahoo 既存ロジックとの差分テスト**: 同一日を Yahoo 1 分と PUSH 合成 1 分で並走し、`signals_eval` の分岐差を定量化。
- **累積出来高の異常値処理**（暫定取引・リセット・負差分）のルール化。

## PoC の実行方法（接続可否の確認）

```text
# 仕様・URL のみ（土日・夜間でも可）
python scripts/kabu_push_probe.py --spec-only

# 実接続（Kabu 起動・API 有効・.env に KABU_API_PASSWORD）
python scripts/kabu_push_probe.py --symbol 9984 --seconds 120 --recv-poll 15
```

出力: `results/kabu_push_probe/YYYYMMDD/*.jsonl` と `*_summary.json`。

## 結論: Yahoo 完全除去は可能か

- **技術的には「kabu のみで主要な現値・累積出来高・気配・セッション VWAP を扱い、1 分足“風”系列と派生指標を計算する」ことは可能**。
- ただし **PUSH はティックではない**ため、**Yahoo 1 分足・当リポジトリ既存ロジックと数値一致を保ったままの「差し替え」は非現実的**になりやすい。
- **現実的なゴール**は、「**シグナル定義を kabu 系列に合わせて再較正し、検証パスを通す**」ことである。

**総合判断**: *完全除去は「API 範囲内なら可能」だが、「挙動不変のドロップイン置換」は難しく、**シグナルとデータ契約の更新**が前提になる。*

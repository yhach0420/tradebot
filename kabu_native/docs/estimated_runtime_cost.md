# Phase334 — データ取得ランタイムコスト見積もり

**更新:** 2026-06-09  
**関連:** [board_data_inventory.md](board_data_inventory.md) · [realtime_board_exit_feasibility.md](realtime_board_exit_feasibility.md)

---

## 1. 前提（現行 small paper live）

| パラメータ | 値 | 出典 |
|-----------|-----|------|
| PUSH 登録銘柄数 | **50**（上限） | `KABU_PUSH_REGISTER_LIMIT` |
| 同時保有上限 | **3** | `max_concurrent_positions` |
| live poll_interval | **5.0 s**（heartbeat 等） | pilot config `live.poll_interval_sec` |
| セッション時間 | 約 **5.5 h**（09:00–15:30、昼休み除く実効） | 運用設計 |
| push_jsonl 記録 | **ON**（`record_push_jsonl: true`） | pilot config |

---

## 2. PUSH 経路（現行・推奨）

### 2.1 API コスト

| 項目 | 1 セッションあたり | 備考 |
|------|-------------------|------|
| `PUT /register` | 1〜数回 | 朝の universe refresh + リトライ |
| `PUT /unregister/all` | 0〜1回 | レジスト失敗時 |
| WebSocket メッセージ | **イベント駆動・無制限** | kabu API は PUSH 件数課金なし |
| **リアルタイム板 EXIT 追加時** | **+0 API** | 既存 PUSH ペイロードを再利用 |

### 2.2 メッセージ量（実測 2026-06-05〜09）

| 指標 | 値 |
|------|-----|
| 登録銘柄数/日 | 50 |
| 全銘柄合計行数/日 | **1.11M〜1.35M** |
| 1 銘柄平均行数/日 | **約 22,000〜27,000** |
| 1 銘柄平均レート | **約 1.1〜1.4 msg/s**（5.5h 換算） |
| 高流動性例（9984.T） | **〜150,000 行/日**（〜7.6 msg/s） |
| 低流動性例（4429.T） | **〜150 行/日**（更新稀少） |

**保有 3 銘柄に限定した板監視**でも、ユニバース全体への PUSH 登録は変わらないため、**marginal API コストはゼロ**。CPU 側は保有銘柄の tick ごとに `calc_board_imbalance` 1 回（O(10)）— 無視できるオーバーヘッド。

### 2.3 ストレージコスト（push_jsonl）

| 項目 | 見積もり |
|------|----------|
| 1 行サイズ | 約 1.5〜3 KB（深度込み JSON） |
| 1 日あたり（50 銘柄） | **約 1.5〜4 GB**（圧縮なし） |
| 月次（20 営業日） | **約 30〜80 GB** |

板 EXIT shadow で `hold_imbalance` を events CSV に追加しても、列追加分は **push_jsonl の 1% 未満**。

---

## 3. REST ポーリング経路（shadow runner 参考・非推奨）

### 3.1 shadow 既定

| パラメータ | 値 |
|-----------|-----|
| `poll_interval_sec` | **15** |
| watchlist `top_n` | **10** |

**コスト:** 10 銘柄 × (19,800 s / 15 s) ≈ **13,200 GET /board / セッション**

### 3.2 保有銘柄のみ REST ポール（仮想案）

| シナリオ | 銘柄数 | 間隔 | GET / セッション |
|----------|--------|------|----------------|
| 保守的 | 3 | 5 s | 3 × 3,960 = **11,880** |
| 積極的 | 3 | 1 s | 3 × 19,800 = **59,400** |

### 3.3 レート制限リスク

- `dynamic_build.py`: HTTP **429** / kabu code **4001006** を `http_429_rate_limit` として分類
- Phase104: 400 銘柄一括 board は register 上限で失敗
- **結論:** REST 追加ポールは 429・運用負荷の観点で **PUSH より劣る**

---

## 4. シナリオ比較

| シナリオ | API 呼び出し/セッション | 追加 register | 429 リスク | 板深度 | 推奨 |
|----------|------------------------|---------------|-----------|--------|------|
| **A. 現行 PUSH のみ** | ~50 register + WS | 0 | 低 | 実測で深度あり | ✅ 現行 |
| **B. PUSH + 保有 REST 5s** | +11,880 GET | 0 | 中 | フル | ❌ 不要 |
| **C. shadow REST 15s×10** | +13,200 GET | 共有 50 | 中 | フル | 参考のみ |
| **D. PUSH + realtime imb EXIT** | **同 A** | **0** | 低 | 同 A | ✅ **次段階** |

---

## 5. 計算コスト（CPU / レイテンシ）

| 処理 | 頻度（保有 3 銘柄） | コスト |
|------|-------------------|--------|
| `calc_board_imbalance` | 最大 ~7 msg/s × 3 ≈ **21/s**（高流動性時） | 微小 |
| `LiveFeatureBridge.update` | 同程度 | 既存 |
| `observer.on_tick` + EXIT 評価 | 同程度 | 既存 + 数条件分岐 |

ボトルネックは **API ではなくディスク I/O（push_jsonl 追記）** — 既に負担あり、板 EXIT 追加の影響は限定的。

---

## 6. 保有時間と監視ウィンドウ

`small_paper_events.csv` の `hold_sec` 集計（直近セッション、n=1,364）:

| 統計 | 秒 | 分 |
|------|-----|-----|
| **中央値** | 603 | **~10** |
| 平均 | 12,525 | ※長期保有・session_close 含みで右裾が長い |

**解釈:** 日中の板監視は **中央値 ~10 分/トレード × 最大 3 ポジション** が典型。session_close 待ちの長い tail は板シグナルより時間 EXIT が支配的。

---

## 7. コスト結論

| 質問 | 見積もり |
|------|----------|
| リアルタイム板 EXIT の marginal API コスト | **0**（PUSH 再利用） |
| 追加 REST が必要か | **不要**（保有銘柄は登録済み） |
| 主なコスト | push_jsonl **ストレージ**（既存）+ 実装・検証工数 |
| 避けるべき案 | 保有銘柄 REST 5s ポール（+~12k req/日、429 リスク） |

---

## 8. 推奨運用

1. **データ取得:** 現行 PUSH 50 銘柄を維持；板 EXIT はペイロード内計算のみ。
2. **ログ:** shadow 段階で `hold_imbalance` / streak を events に追加；詳細は push_jsonl を正とする。
3. **検証:** push_jsonl リプレイで API コストゼロのバックテスト可能。
4. **監視:** 低流動性銘柄は板更新が稀少 — `stale_tick_sec` と併用して板 EXIT の無効化条件を設ける。

---

## 9. データ出典

| データ | パス / コマンド |
|--------|----------------|
| push_jsonl 行数集計 | `kabu_native/data/push_jsonl/2026-06-{05,08,09}` |
| Phase300 監査 | `python kabu_native/scripts/run_phase300_board_live_payload_availability_report.py` |
| hold_sec 集計 | `kabu_native/results/small_paper/*/small_paper_events.csv` |
| 設定 | `kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` |

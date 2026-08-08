# E1_X6 新ENTRYロジック実装・検証指示書

## 0. 文書情報

| 項目 | 内容 |
|---|---|
| Document ID | `E1_X6_FCRR_IMPLEMENTATION_SPEC` |
| Version | `1.2` |
| 対象計画 | `E1_X6_VALIDATION_PLAN Version 1.2` |
| Candidate family | `E1_X6_FCRR` |
| 名称 | `Flow Confirmed Reclaim & Retention` |
| 日本語名 | 上昇基調・押し目・売り停止・買い再開・上抜け継続確認ENTRY |
| 現在状態 | `DESIGN_SPEC_ONLY / NOT_EXECUTED / NOT_ADOPTED` |
| 運用制約 | Paper研究のみ。`submit/cancel/live = 0/0/0` |
| 本線 | E1_X5、PBv2、production YAML、Live経路を変更しない |

本書は新ロジックの実装仕様であり、利益が出ることや採用を宣言するものではない。  
Version 1.2では、ENTRY候補単体の採用判定を廃止し、各ENTRY候補の値動きシナリオに対応するEXITを設計して、ENTRY×EXITの組み合わせでのみ採否を判定する。  
採用判断は必ず `E1_X6_VALIDATION_PLAN Version 1.2` のGate、Rolling-origin、LODO、7/22除外、集中度、Safetyを通過した後に行う。

---

# 1. 作るロジック

E1_X6はE1_X5やPBv2へ条件を追加する派生ロジックにしない。  
同じWatch50を、canonical評価時点ごとに独立評価する。

```text
Watch50 canonical event
├─ Frozen E1_X5 comparison
└─ E1_X6_FCRR independent state machine
```

狙う値動きは一つに限定する。

```text
上昇する土台がある
↓
正常な押し目が発生する
↓
売り圧力が止まる
↓
買いフローが再開する
↓
micro-highを実際に上抜く
↓
上抜け後も価格と出来高が維持される
↓
ENTRY
```

単純なMomentum、板枚数、合計scoreではENTRYしない。

---

# 2. 過去案から必ず修正する点

| 過去の問題 | E1_X6_FCRRの必須対策 |
|---|---|
| RPFEが実質PRICE条件主体になった | 買いフロー未確認ではENTRY不可 |
| VCIEの5秒維持でfalse breakoutを拾った | 上抜け後10～30秒のRetentionを必須化 |
| NoProgressが約58～62%へ悪化した | breakout level維持、新高値、出来高継続を確認してからENTRY |
| 同一銘柄再ENTRYが276/347件 | 1 episodeにつきENTRYは1回だけ |
| 出来高倍率が379倍等へ異常化 | 比率だけでなく絶対量floorとactive-window数を必須化 |
| 板一点値が見せ玉・取消に弱い | 静的imbalance単独では通さず、価格・約定方向・bid支持を同時確認 |
| 特定日利益への依存 | 日付、銘柄、AM/PMをENTRY条件に使わず、LODOをhard gateにする |

---

# 3. 日依存を禁止する契約

E1_X6_FCRRのENTRY判定には次を使用しない。

- 日付
- 曜日
- 2026-07-22等の特定日フラグ
- 銘柄コード固有条件
- 日ごとに異なる閾値
- その日の最終損益、勝ち日・負け日
- 将来MFE、MAE、STOP、Winner
- AM/PMによるpermit/reject
- 結果を見て後から追加した相場区分

許可するのは、判断時点までにcanonical経路で得られる価格、VWAP、出来高差分、tick方向、bid/ask、spread、履歴、Watch50内相対状態だけとする。

AM/PM、日、銘柄、相場局面は診断・層別に使ってよいが、初期candidateのENTRY条件には入れない。

最終specの全閾値は全日共通で固定する。fold内の学習処理を使う場合も、各foldの構築期間だけでfitし、確認日へそのまま適用する。

---

# 4. データ品質Gate

以下を満たさないeventでは状態を進めず、欠損を0として条件成立させない。

```text
canonical should_evaluate = true
analysis_mask_id対象内
universe登録済み
event freshness PASS
board freshness PASS
price history >= 180秒
volume history >= 120秒
spread計算可能
VWAP計算可能
累積出来高の差分が非負かつ時系列整合
```

出来高・売買方向の品質が不足する場合は、

```text
FLOW_DATA_INCOMPLETE
```

として状態を進めない。価格条件だけで代替ENTRYしない。

板深度の動的差分が取れない場合でも、best bid/ask、spread、mid、tick、出来高差分が完全なら評価可能とする。深い板のimbalance deltaは補助監査値であり、初期candidateの必須データにはしない。

---

# 5. Episode定義

同じ上昇波でENTRYを繰り返さないため、symbolごとにepisodeを持つ。

## 5.1 Episode開始

`CONTEXT_READY`を初めて満たした時点で新しい`episode_id`を発行する。

## 5.2 Episode内ENTRY

1 episodeにつき`ENTRY_EMITTED`は最大1回。

## 5.3 Episode終了

次のいずれかで終了する。

```text
価格がVWAPを明確に下回る
context起点後の安値を更新
spread急拡大
event/board stale
session終了
最大episode時間超過
ENTRY後にpositionがclosedし、再セットアップ条件未達
```

## 5.4 再ENTRY

単純なcooldown時間経過だけでは再ENTRYを許可しない。  
次の全てを満たして新しいepisodeを作り直した場合だけ許可する。

```text
前episode終了済み
新しいcontext highを形成
新しいpullback lowを形成
前episodeのmicro-highとは別のreclaim levelを形成
```

---

# 6. 状態機械

```text
IDLE
↓
CONTEXT_READY
↓
PULLBACK_ACTIVE
↓
SELLING_EXHAUSTED
↓
RECLAIM_CROSSED
↓
RETENTION_CONFIRMED
↓
ENTRY_EMITTED
↓
EPISODE_LOCKED
```

無効化時は、

```text
任意状態
↓
INVALIDATED
↓
IDLE
```

へ戻す。

## 時系列規則

- 1観測で進めてよい状態は最大1段階。
- `RECLAIM_CROSSED`と`ENTRY_EMITTED`を同一eventで成立させない。
- Retentionは上抜け後に実際に経過した時間だけで判定する。
- 状態時刻、使用featureのasof_time、判定理由をdecision ledgerへ保存する。
- 同一入力の二重再生で状態遷移ledger SHAを完全一致させる。

---

# 7. 各状態の条件と閾値生成方式

## 7.0 Version 1.1の修正原則

Version 1.0に記載した次のような具体値は、過去結果から採用可能性が証明された値ではない。

```text
ret_180s > 0
distance_from_session_high <= 1.0 * ATR
pullback_depth_atr = 0.20～1.00
volume impulse >= 1.50
uptick ratio >= 0.60
spread <= 5bps
```

これらを全て必須AND条件として固定すると、RPFEでFLOW confirmedがほぼ成立しなかった問題を再現し、経済評価に必要なsupportへ到達しない可能性が高い。

したがって、以下を分離する。

### 固定するもの

- 状態の順序
- 実際のmicro-high cross
- crossとENTRYを同一eventにしない
- breakout後Retention
- 同一episode 1 ENTRY
- 欠損をPASSにしない
- 日付・銘柄固有条件を使わない
- 未来情報を使わない
- source、cost、CAP、EXIT、合格Gate

### 構築期間内で決めるもの

- 上昇contextの強さ
- 正常なpullbackの深さ・時間
- 売り停止を表すfeature
- 出来高impulseの強さ
- uptick比率
- spread許容値
- Retention中の活動継続値

候補別PnLを見る前に、以下の閾値生成法、feature候補、候補数を`P1_STUDY_PRECOMMIT`へ固定する。  
結果を見て数値を緩めるのではなく、各Rolling-origin foldの構築期間だけで同じ決定手順を実行する。

---

## 7.1 閾値生成契約

### 使用可能な分位点

```text
q30
q50
q70
```

上記以外の細かいgridを追加しない。

### 正方向feature

値が高いほど上昇継続に有利と仮定するfeatureは、

```text
x >= q30
x >= q50
x >= q70
```

を候補とする。

### 負方向feature

値が低いほど有利と仮定するfeatureは、

```text
x <= q70
x <= q50
x <= q30
```

を候補とする。

### band feature

pullback depthやdurationのように適正帯を持つfeatureは、

```text
q20相当の固定外挿は行わず、
q30～q70
q30～q50
q50～q70
```

の3帯だけを使用する。

### 選択方法

各foldの構築期間だけで、計画書の`primary_label_id`を使い次の順序で決める。

1. feature単独の方向性を日単位で確認  
2. support不足の閾値を除外  
3. 方向反転するfeatureを除外  
4. 日balanced primary-label指標が最良の閾値を選ぶ  
5. 最良値と実質同等なら、最も緩くsupportが大きい閾値を選ぶ  
6. PnL、PF、勝率、EXIT損益は閾値決定に使わない  
7. 確認日へ変更せず適用する  

使用した分位点、母集団、support、選択理由をfoldごとに保存する。

---

## 7.2 Support／Reachability Gate

経済結果を計算する前に、候補が単に厳しすぎないかを確認する。

各foldの構築期間で最低限、

```text
CONTEXT_READY >= 300 episodes
PULLBACK_ACTIVE >= 150 episodes
SELLING_EXHAUSTED >= 75 episodes
RECLAIM_CROSSED >= 45 episodes
ENTRY候補 >= 30 episodes
ENTRY発生日 >= 3日
```

を要求する。

これは合格条件ではなく、候補を経済評価できる最低supportである。  
未達候補はPnL不合格ではなく、

```text
CANDIDATE_UNREACHABLE
```

としてCandidate Registryへ残す。

全候補が未達の場合は、同じrunの結果を見ながら条件を変更しない。  
precommitした別profileが残っていなければ、そのcandidate familyは終了する。

---

## 7.3 `CONTEXT_READY`

目的は「強い上昇条件を全て満たすこと」ではなく、下降途中ではない上向き候補を作ることである。

使用候補feature:

```text
vwap_distance_atr
linear_slope_180s
return_180s
distance_from_session_high_atr
price_update_count_60s
active_volume_window_ratio_120s
spread_bps
```

構築規則:

- Price contextから最大2feature
- Tradeability guardから最大1feature
- 3つ以上の価格条件を同時必須にしない
- `mid > VWAP`は候補featureであり、無条件のhard gateにしない
- spreadはRuntime安全上限を超えない範囲でq50またはq70から選ぶ
- feature方向が日によって反転する場合は使用しない

状態成立例はfoldごとに生成されるが、構造は次とする。

```text
CONTEXT_READY =
    price_context_feature_1
AND optional price_context_feature_2
AND tradeability_guard
```

---

## 7.4 `PULLBACK_ACTIVE`

使用候補feature:

```text
pullback_depth_atr
pullback_duration_sec
pullback_low_vs_vwap_atr
down_slope_30s
new_low_count_30s
```

構築規則:

- pullback depthまたはdurationのどちらか1つをprimary setup条件とする
- 追加guardは最大1つ
- depthとdurationとVWAP位置と下落速度を全てANDにしない
- bandは§7.1の3帯から構築期間だけで選ぶ
- pullbackと呼べる実価格低下がないepisodeは除外する

`micro_high`は、pullback low形成後から売り停止成立までの実観測高値として固定する。

---

## 7.5 `SELLING_EXHAUSTED`

使用候補feature:

```text
seconds_since_new_pullback_low
down_tick_volume_deceleration
return_15s_minus_return_30s
down_slope_improvement
best_bid_low_update_stopped
spread_change_from_pullback
```

構築規則:

- 少なくとも1つの「安値更新停止」証拠を必須とする
- 追加する売り速度・bid・spread証拠は最大1つ
- 30秒固定をhard codeせず、観測可能な候補値をq30/q50/q70で決める
- 板深度欠損時に0改善として扱わない
- 売り停止条件だけでENTRYしない

状態成立は、

```text
SELLING_EXHAUSTED =
    low_update_stop_evidence
AND optional secondary_exhaustion_evidence
```

とする。

---

## 7.6 `RECLAIM_CROSSED`

ここだけは価格の実crossをhard conditionとする。

```text
previous_mid <= micro_high
current_mid > micro_high
```

加えて、価格だけのRPFEへ戻らないよう動的活動証拠を要求する。

### Flow profile F1：Minimum dynamic confirmation

次の3証拠のうち2つ以上。

```text
volume_impulse
uptick_volume_ratio_improvement
price_update_acceleration
```

### Flow profile F2：Dual flow confirmation

次を両方必須。

```text
volume_impulse
uptick_volume_ratio_improvement
```

加えて、

```text
spread_not_widening
```

を必須とする。

### 閾値

- volume impulseはactive windowだけを分母にする
- denominator=0は禁止
- active window不足は禁止
- 絶対量floorは構築期間のcross-section q30またはq50から選ぶ
- 倍率、uptick比率、update accelerationはq30/q50/q70から決める
- trade-side品質を保存する
- DIRECTがない場合のQUOTE_INFERRED／TICK_RULE_INFERRED優先順は固定する

---

## 7.7 `RETENTION_CONFIRMED`

Retention時間だけは候補IDで明示的に固定する。

```text
R10 = 10秒
R20 = 20秒
R30 = 30秒
```

Retention中のhard condition:

```text
midがmicro_high - 1tick未満へ崩れない
pullback lowを更新しない
event/board staleにならない
```

Retention中の継続証拠は、次のうち1つ以上とする。

```text
cross後の新高値更新
cross後return > 0
活動window継続
uptick優勢維持
best_bidによるbreakout level支持
```

継続証拠の数値閾値は構築期間内で生成する。  
出来高、uptick、新高値、bid支持を全てANDにしない。

---

## 7.8 `ENTRY_EMITTED`

`RETENTION_CONFIRMED`後の次のcanonical評価eventで1回だけ発行する。

```text
entry_price = canonical best_ask
episode_entry_count = 1
```

CAP blockedの場合も同一episodeから再発行しない。

---

# 8. Candidate Registry

採用判定対象は次の6本に限定する。

| candidate_id | Flow profile | Retention |
|---|---|---:|
| `FCRR_F1_R10` | Minimum dynamic confirmation | 10秒 |
| `FCRR_F1_R20` | Minimum dynamic confirmation | 20秒 |
| `FCRR_F1_R30` | Minimum dynamic confirmation | 30秒 |
| `FCRR_F2_R10` | Dual flow confirmation | 10秒 |
| `FCRR_F2_R20` | Dual flow confirmation | 20秒 |
| `FCRR_F2_R30` | Dual flow confirmation | 30秒 |

各candidateのcontext、pullback、exhaustion、flow数値閾値は、同じ§7.1のfold内構築手順で決める。

候補別経済出力を見た後に、

- featureを追加
- 分位点を追加
- F1の2-of-3を1-of-3へ変更
- F2のANDをORへ変更
- Retention時間を追加
- spread条件を削除
- support基準を引き下げる

ことは禁止する。

## 8.1 非採用Ablation

原因監査として次を出すが、採用候補にはしない。

```text
A0: actual cross only
A1: cross + volume
A2: cross + uptick
A3: cross + F1, retentionなし
A4: cross + F2, retentionなし
A5: episode lockなし
```

Ablationの成績が良くても、同じrunで採用候補へ昇格させない。

---

# 9. ENTRY候補評価は採否ではなくEXIT設計入力とする

Frozen E1_X5 EXITはbenchmarkとして全候補へ適用するが、そのPnL・PFをENTRY候補の最終合否に使用しない。

ENTRY候補ごとに次を作成する。

- ENTRY後event path ledger
- 10/30/60/120/300秒のMFE・MAE
- cost超過時間
- reclaim levelの維持・再割れ・再奪回
- 新高値更新時刻
- volume persistence
- uptick/downtick volume
- update speed
- bid支持、spread悪化
- MFE giveback
- censor状態

ENTRY候補は次の7シナリオへ分類する。

```text
S1_IMMEDIATE_CONTINUATION
S2_RETEST_THEN_CONTINUATION
S3_FALSE_BREAKOUT
S4_NO_PROGRESS
S5_SPIKE_GIVEBACK
S6_LATE_CONTINUATION
S7_CENSORED_OR_OTHER
```

scenario_idは診断labelであり、Runtime EXIT入力に直接使用しない。

ENTRY候補がEXIT設計へ進む条件は、Source、Leakage、Determinism、Reachability、複数日support、EXIT指標の再構成可能性である。  
E1_X5 EXITで赤字でも、ENTRY後にcostを超える正のMFEが複数日に存在すればEXIT設計へ進める。

---

# 10. 候補別EXIT設計

## 10.1 EXITに使用する指標

### Price structure

```text
current_return
MFE_so_far
MAE_so_far
giveback_from_MFE
distance_to_reclaim_level
distance_to_pullback_low
distance_to_VWAP
seconds_since_new_high
new_high_count
short_slope
ATR normalized distance
```

### Volume / trade flow

```text
volume_10s / 30s / 60s
volume_persistence
volume retention vs entry impulse
uptick_volume_ratio
downtick acceleration
price impact efficiency
price update speed
```

### Board / liquidity

```text
best_bid support at reclaim level
best_bid downgrade count
ask replenishment / absorption
imbalance deterioration
spread widening
board freshness
```

### Time / state

```text
elapsed_from_entry
time_to_cost_cover
seconds_since_progress
session_remaining
current exit state
```

未来の最終MFE、最終MAE、scenario_id、最終EXIT理由をRuntime featureにしない。

## 10.2 EXIT state machine

```text
OPEN_INIT
↓
STRUCTURE_HOLD
↓
PROGRESS_CHECK
↓
PROFIT_PROTECTION
↓
TREND_MANAGEMENT
↓
EXIT
```

### `OPEN_INIT`

誤breakoutと執行直後ノイズを分離する。  
reclaim level、初期MAE、spread、bid支持、売り加速を評価する。

### `STRUCTURE_HOLD`

正常retestを許容しつつ、構造破壊を検出する。  
level下抜け幅・継続時間、再奪回、pullback-low距離、volume persistenceを評価する。

### `PROGRESS_CHECK`

NoProgressを判定する。  
cost超過、MFE、新高値、最後のprogress、update速度、volume維持を評価する。

### `PROFIT_PROTECTION`

十分なMFE後のgivebackを抑える。  
break-even、MFE giveback、bid悪化、downtick加速、spread拡大を評価する。

### `TREND_MANAGEMENT`

継続上昇を伸ばし、固定TARGETによる早すぎるEXITを避ける。  
高値更新間隔、構造trailing、ATR、volume persistence、flow悪化、最大保有を評価する。

## 10.3 EXIT Candidate Registry

各ENTRY候補に最大3本を作る。

| exit_candidate_id | 狙い |
|---|---|
| `X_STRUCTURAL` | 正常retestを許容し、reclaim構造破壊でEXIT |
| `X_CONTINUATION` | 早期progressを要求し、false breakout・NoProgressを早くEXIT |
| `X_HYBRID` | 初期は構造許容、利益後はgivebackとflow悪化で保護 |

候補別EXITの指標、方向、分位点、最大要素数、生成手順は、EXIT候補別PnLを見る前に`P2_EXIT_PRECOMMIT`へ保存する。

## 10.4 ENTRY×EXIT全event replay

最終評価は、

```text
entry_candidate_id × exit_candidate_id
```

ごとに行う。

既存ENTRY ledgerへ後付けEXITを当てるだけではなく、ENTRY、OPEN、EXIT、CAP解放、後続ENTRYを全eventから再生する。

Frozen E1_X5 EXITはbenchmarkであり、自動的な最終候補にはしない。

## 10.5 Joint合格Gate

計画書Version 1.3のjoint gateを適用する。

```text
CORE_VALID:
  PnL > 0
  PF >= 1.10
  completed >= 30

ALL_USABLE:
  PnL > 0
  PF >= 1.10

7/22除外:
  PnL > 0
  PF > 1.00
  completed >= 30

Rolling-origin:
  3/5以上プラス
  確認日PnL中央値 > 0

FIXED_SPEC_DAY_DELETION:
  全ケース残存PnL >= 0

Concentration:
  top1 trade / symbol除外後PnL > 0

BASE:
  PF、STOP損失、最大DD改善

Safety:
  submit/cancel/live = 0/0/0
```

ENTRY候補単体の結果で停止しない。  
ENTRY×EXITの全組合せが不合格なら、

```text
E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR
```

とする。

EXIT経路の証拠量不足なら、

```text
E1_X6_INSUFFICIENT_EXIT_EVIDENCE
```

とする。
# 11. 実装先

本線へ接続しないresearch moduleとして作る。

```text
kabu_native/src/research/e1_x6_fcrr/
├── __init__.py
├── config.py
├── features.py
├── episode.py
├── state_machine.py
├── decision.py
├── replay.py
├── metrics.py
├── manifests.py
└── report.py

kabu_native/scripts/run_e1_x6_fcrr.py
kabu_native/tests/test_e1_x6_fcrr.py
```

既存canonical event processor、E1_X5 frozen EXIT、CAP5 simulator、cost計算を再利用する。  
新しい独自canonical変換、別のevent順序、別のコスト計算を作らない。

MAINLINE、production YAML、Runtime registration、Discord、Live注文経路は変更しない。

---

# 12. 必須テスト

最低限、以下に加えてENTRY後path、EXIT状態機械、joint replayを自動テストする。

1. 未来情報がfeatureへ入らない  
2. `asof_time <= decision_time`  
3. 1eventで2状態以上進まない  
4. crossとENTRYが同一eventにならない  
5. Retention時間未達でENTRYしない  
6. micro-high未突破でENTRYしない  
7. 価格だけ上抜けてもvolume不足ならENTRYしない  
8. volume比率のdenominator=0をPASSしない  
9. active-window不足をPASSしない  
10. uptick比率不足でENTRYしない  
11. spread拡大時にINVALIDATE  
12. pullback low更新時にINVALIDATE  
13. 1 episode 1 ENTRY  
14. CAP blocked後に同episode再発行しない  
15. 新しいepisode条件なしで再ENTRYしない  
16. 欠損を0埋めしてPASSしない  
17. 二重再生でdecision/trade ledger SHA一致  
18. 5bpsを1回だけ控除  
19. completed以外を経済集計へ混ぜない  
20. submit/cancel/liveが常に0
21. ENTRY候補単体のPnLをfinal verdictに使わない
22. scenario_idをRuntime EXIT featureへ入れない
23. EXIT featureのasof_time <= exit_decision_time
24. 正常retestとstructure failureを区別できる
25. NoProgress timerがfuture dataを参照しない
26. MFE_so_farだけを使いfinal MFEを参照しない
27. EXIT後のCAP解放と後続ENTRYを全event replayで再現
28. ENTRY×EXIT pairごとにledger SHA一致
29. E1_X5 EXIT benchmarkとjoint candidateを別集計
30. ENTRY_ONLY verdictを生成しない

---

# 13. 実行順序

## Gate 0

計画書どおり、Source Manifest、Parity、決定性、E1_X5 BASEを先に確定する。

Gate 0不合格ならFCRRを実行しない。

## P1 Study Precommit

候補別のPnL、PF、勝率、fold順位を開く前に、本書の内容を`P1_STUDY_PRECOMMIT`へ保存する。

保存項目:

```text
Document ID / Version
candidate family
candidate IDs
全状態条件
threshold
retention variants
candidate count limit
feature schema
label schema
fold
seed
合格条件
precommit_at_jst
precommit SHA
```

既に候補経済出力を見た後なら、

```text
E1_X6_PRECOMMIT_CONTAMINATED
```

を記録し、study revisionを上げて最初から再実行する。

## Final run

7/31を含む最終Manifestで、Gate 0、dataset、F1～F5、LODO、7/22除外を新規final run IDで最初から実行する。

暫定runや旧RPFE/VCIEのledgerを正式値として流用しない。

---

# 14. 成果物

計画書指定の同一directoryへ3ファイルだけ出す。

```text
kabu_native/results/research/e1_x6_redesign_20260721_20260731/
├── report.json
├── report.md
└── audit.xlsx
```

追加の個別CSV、candidate別Excel、重複reportは最終成果物へ残さない。

`audit.xlsx`へ最低限追加する内容:

- `Precommit`
- `FCRR_StateTransitions`
- `FCRR_Episodes`
- `FCRR_Funnel`
- `FCRR_Ablation`
- `Candidates`
- `WalkForward`
- `DayDeletion`
- `NoiseAudit`
- `Trades`
- `Safety`
- `Tests`
- `ChangeLog`

---

# 15. 完了報告の必須形式

```text
1. plan version
2. precommit_at_jst / precommit SHA
3. final run ID
4. Source / Parity / Determinism verdict
5. 使用日・window・品質区分
6. FCRR_F1_R10～FCRR_F2_R30のstate funnelとReachability
7. 各candidateのsignal-level / CAP5結果
8. 日別、AM/PM、7/22除外
9. F1～F5
10. FIXED_SPEC_DAY_DELETION全ケース
11. top1 day/trade/symbol集中
12. X5_KEEP / X5_REMOVED / X6_ADDED / BOTH_REJECT
13. STOP / early STOP / NoProgress
14. episode数 / ENTRY数 / same episode reentry
15. ablation結果
16. failed_tests
17. submit/cancel/live
18. mainline_changed
19. ENTRY×EXIT pairのfinal verdict
20. 成果物3ファイル
```

最終verdictは計画書の定義から選ぶ。

```text
E1_X6_RESEARCH_CANDIDATE_FROZEN
E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR
E1_X6_INSUFFICIENT_EXIT_EVIDENCE
E1_X6_INSUFFICIENT_EVIDENCE
E1_X6_SOURCE_BLOCKED
```

---

# 16. Cursorへ渡す実行指示

この文書と`E1_X6_VALIDATION_PLAN Version 1.2`をSource of Truthとして扱うこと。

最初にrepository root、branch、git status、計画書正本、既存E1_X5 Parity証跡、最終Source Manifestの有無をread-onlyで確認する。

Gate 0を通過する前にE1_X6候補を実行しない。候補別経済出力を開く前に、本書の6候補、閾値生成法、分位点、Support／Reachability Gateを`P1_STUDY_PRECOMMIT`として時刻・SHA付きで固定する。

E1_X6_FCRRをresearch-only moduleとして実装し、Frozen E1_X5と同じcanonical source、window、cost、CAP、EXITで比較する。状態順序のスキップ、価格だけのENTRY、同一episode再ENTRY、出来高倍率の分母0・閑散window誤認、5秒だけのfalse breakoutを禁止する。

最終ManifestでENTRY候補の経路診断、候補別EXIT設計、F1～F5、FIXED_SPEC_DAY_DELETION、7/22除外、集中度、Safetyを実行する。ENTRY×EXIT pairが1本も全Gateを通らない場合は条件を緩めず`E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR`で停止する。

実注文は禁止。MAINLINE、production YAML、Live注文経路を変更しない。`submit/cancel/live = 0/0/0`を維持する。


---

# 17. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-08-03 | FCRR状態機械と固定数値条件を初版として定義。 |
| 1.1 | 2026-08-03 | 過去のRPFE／VCIEが全採用否定だったことを反映。未検証の具体値を全AND固定する方式を廃止し、状態順序・安全条件を固定、数値閾値はfold構築期間内のq30/q50/q70から同一手順で生成する方式へ変更。Reachability Gate、F1/F2×R10/R20/R30の6候補、非採用Ablationを追加。 |
| 1.2 | 2026-08-03 | ENTRY候補単体の採用判定を廃止。ENTRY後path ledger、7シナリオ分類、EXIT指標候補、5段階EXIT状態機械、X_STRUCTURAL/X_CONTINUATION/X_HYBRID、P2_EXIT_PRECOMMIT、ENTRY×EXIT joint gateを追加。 |

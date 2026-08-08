# TradeBot E1_X6 再設計・検証計画書

| 項目 | 内容 |
|---|---|
| Document ID | `E1_X6_VALIDATION_PLAN` |
| Version | `2.1` |
| 制定日 | 2026-07-30 |
| 最終監査日 | 2026-08-02 |
| 状態 | `APPROVED_PLAN / DAY_ROBUST_JOINT_STRATEGY_VALIDATION` |
| 状態基準時点 | 2026-08-02。Plan 2.0 joint EXIT sweepの4戦略が`REJECTED_DAY_CONCENTRATED`となった後。研究目的を「全日無敗」から「利益の日別分散・通常日の期待値」へ正しく修正。負け日は許容する。評価単位は`JointStrategyPackage`（ENTRY＋EXIT一体）。Shadowはユーザー承認後のみ。 |
| 正本配置先（repository） | `kabu_native/kabu_native/docs/e1_x6_validation_plan.md`（repository root=`C:\Users\yhach\Documents\tradebotfile`基準。本書のみを正本とし、別場所へ第二のSoTを作らない） |
| 想定repository root | `C:\Users\yhach\Documents\tradebotfile`。実行前に実在とbranchを確認する |
| 対象 | E1_X5比較BASE、E1_X6 JointStrategy（ENTRY＋EXIT一体）検証。Shadowは承認後。Runtime反映は禁止 |

## 1. 本書の位置づけ

本書をE1_X6再設計・検証のSource of Truthとする。

- チャット上の要約や途中報告より本書を優先する。
- 実施結果は本書を自動的に変更しない。
- データ期間、特徴量、ラベル、コスト、CAP、合格条件を変更する場合は、結果を見てから後付けせず、変更理由と影響範囲を変更履歴へ先に記録する。
- 変更後に既存結果を流用する場合は、影響を受ける検証を無効化して再実行する。
- 本書でいう「採用」はPaper候補としての採用であり、実注文の許可を意味しない。
- 本書の添付や生成だけでは、repositoryへの配置、コード変更、検証実行、Capture完了を証明しない。各完了判定には対応する成果物またはRuntime証跡が必要である。

計画と実績の優先関係は次のとおりとする。

| 情報 | 役割 | 矛盾時の扱い |
|---|---|---|
| 本書の最新版 | 目的、固定条件、工程、合否規約 | 旧版やチャット要約より優先 |
| `report.json` | run、manifest、数値、verdictの機械可読実績 | `audit.xlsx`と一致しなければ停止 |
| `audit.xlsx` | source、dataset、取引台帳、再計算根拠 | `report.json`と一致しなければ停止 |
| `report.md` | 人間向け要約 | 数値は上記2成果物へ照合 |
| チャット本文 | 指示・補足 | 正本や成果物を上書きしない |

### 1.1 引き継ぎ用Status Snapshot

この表は計画規約ではなく、引き継ぎ時の現在地である。証跡を確認した場合だけ更新し、更新日時と根拠を残す。固定条件や合格条件の変更は、この表ではなく変更履歴を伴うVersion更新で行う。

| 項目 | 2026-07-30監査時点（Version 1.2） |
|---|---|
| 現在Phase | `PRE_GATE_0 / PRE_GATE_0_PROVISIONAL_EXECUTION_ALLOWED` |
| 確定済み | E1_X5不採用・比較BASE凍結。7/27 PM canonical Parityは既存証跡上で合格済み。7/30 raw CaptureはCOMPLETE |
| 未確認・未完了 | 7/31 Capture、最終9営業日Source Manifest、最終BASE、E1_X6候補凍結 |
| 次の1工程 | 7/30時点で可能なP0→P1→暫定P2/F1～F4を実行。結果は`PROVISIONAL_NOT_FOR_SELECTION` |
| 既知の停止要因 | source破損・Parity drift・leakage・計画書SoT非一意 |
| 安全条件 | Paperのみ。`submit/cancel/live = 0/0/0` |
| 暫定規約 | 7/31 PM後は新final run IDで7/21～31を最初から再実行。暫定runへF5だけを追加しない |

## 2. 背景と現在地

E1_X5は、特定日への利益集中と通常日の大量ENTRY・STOP偏重が確認されたため、不採用とする。ロジックは比較BASEとして凍結し、閾値調整で延命しない。

既存の7/21～29参考再生は次の状態だった。

| 指標 | 既存参考値 |
|---|---:|
| 完了取引 | 1,014 |
| 100株・5bps控除後PnL | -168,344.715円 |
| PF | 0.921 |
| W/L/D | 372/642/0 |
| 7/22単日PnL | +567,497.800円 |
| 7/22除外PnL | -735,842.515円 |
| 7/28～29 PnL | -498,142.425円 |

ただし、この合計は部分Capture、`NOT_ADOPTED`、`EXCLUDED_LAG_RESYNC`、`RETROSPECTIVE_REFERENCE`を含む研究用参考値であり、正式Forward成績ではない。E1_X6研究開始時に、全日を同一canonical経路・同一集計定義で再構成した値を正式な比較BASEとする。

現在の判断は次のとおり。

- E1_X5：`NOT_ADOPTED`、比較BASEとして凍結
- E1_X5_G1：`NOT_ADOPTED`、E1_X6へ持ち込まない
- E1_X6：未作成、未採用
- 7/21～31：すべてE1_X6の分析・設計データ
- 7/22：設計データに含める。除外感度を別途必須検査する
- 期間内検証：過学習検出用の内部検証
- 真の未使用時系列検証：E1_X6凍結後、最初に到来するsessionからのForward Paper
- 7/30・31：本書制定時点では全日データ未確定。E1_X5を変更せずCaptureを完了させる

## 3. 目的

主目的は、監視50銘柄のENTRY可能時点から「これから上昇する状態」と「不要ENTRYになる状態」を分離し、日・銘柄・相場局面への依存が小さいE1_X6を構築することである。

具体的には以下を実現する。

1. E1_X5が勝った理由と、STOP・no_progress・見逃した上昇の違いを同じ特徴量で説明する。
2. E1_X5がENTRYした取引だけでなく、監視50銘柄の全評価可能時点を母集団にする。
3. **ENTRYと対応EXITを一体の`JointStrategyPackage`として評価する**（ENTRY単体合格後にEXITを直列追加しない）。
4. 7/22の強い上昇局面を学習に残しながら、7/22がなくても成立する候補だけを残す。
5. 期間内のウォークフォワード、日除外、AM/PM、相場局面、銘柄集中度で過学習を検出する。
6. 合格JointStrategyがあれば研究凍結し、**ユーザー明示承認後**にPaper並走Shadowを開始する（自動開始しない）。

### 3.1 Version 2.0 で廃止する規約

- ENTRY単体の合格・採用判定
- ENTRY合格後にEXITを作る直列方式
- `E1_X6_ENTRY_ONLY_CANDIDATE_FROZEN`
- E1_X5 EXITを付けただけで完成戦略とする扱い
- Shadowの自動開始規約

Version 1.xのENTRY結果は `ENTRY_HYPOTHESIS_ONLY / RETROSPECTIVE_REFERENCE` とし、Forward成績や採用実績へ加算しない。

### 3.2 JointStrategyPackage 必須要素

各 Strategy ID に以下を必須化する。

- ENTRY family、特徴量、方向、閾値
- ENTRY仮説と想定継続時間
- 仮説崩れを判定するinvalidation EXIT
- initial STOP
- TARGETまたはTRAILING
- no-progress EXIT
- MAX_HOLD
- session/window終了処理
- 再ENTRY規則
- execution price規約
- 100株、5bps一回、CAP5、pyramiding禁止
- code/config/schema SHA

### 3.3 Version 2.1 で廃止する規約（Version 2.0からの変更）

求めるのは「毎日必ずプラスになる戦略」ではない。負け日は許容する。求めるのは、特定の1日または2日だけの大勝ちに依存せず、通常日にも期待値があるENTRY＋EXIT完成戦略である。したがって以下の条件を廃止する。

- 9/9日すべてプラス
- `worst_day_net_pnl > 0`
- Rolling-origin 5/5日すべてプラス
- RefitLODO 9/9日すべてプラス
- Forward 20/20日すべてプラス

これは条件緩和ではなく、研究目的を「全日無敗」から「利益の日別分散・通常日の期待値」へ正しく直す変更である。Version 2.0は変更せず履歴保存する（SHA `72d692dfd89b98ff50b6ca3fcdcc6ab17c449216c5bf3d619cdc1eb2ccf2c82a`、`kabu_native/kabu_native/docs/history/e1_x6_validation_plan_v2.0_RETIRED_HISTORY.md`）。

Plan 2.0 joint EXIT sweep（run `e1x6_joint_exit_20260802_105508`）の4戦略はすべて
`REJECTED_DAY_CONCENTRATED / FAILURE_ANALYSIS_ONLY`とする。

- X5_FROZEN：7/22・7/31除外後 −279,774円
- X5_TIGHTER_STOP：同 −281,612円
- X5_WIDER_TARGET：同 −281,174円
- 上位2日がgross positive day PnLの98.6～99.9%を占める
- X5_SHORTER_HOLDは全日マイナス

総損益やPFが高くても、これらを候補・最良戦略・Shadow候補とは表現しない。同runの`report.json`はチャット送信エラーで未添付になっただけであり、欠落・監査不備とは扱わない。

### 3.4 日依存を排除する必須ゲート（20260721～20260731、Version 2.1）

5bps控除後、同一`JointStrategyPackage`（全9日へ同一ENTRY＋EXIT spec）について以下を必須とする。

- 全9日合計PnL > 0
- 日別PnLの中央値 > 0
- best 1日除外後PnL > 0
- best 2日除外後PnL > 0
- 最大利益日の寄与率 <= gross positive day PnLの30%
- 上位2利益日の寄与率 <= gross positive day PnLの50%
- 7/22除外後もPnL > 0、PF > 1.00
- top 1取引除外後PnL > 0
- top 1銘柄除外後PnL > 0
- PF >= 1.10
- completed trades合計 >= 30
- 各日completed trades >= 3
- 最大DDとSTOP損失がE1_X5より悪化しない
- INVALID_SOURCE = 0
- A/B determinism完全一致

7/22、7/31などの日付を条件へ直接組み込むことは禁止する。上位1日・2日は毎回機械的に算出する（best 1日・best 2日は日別PnL降順で決定し、tie-breakは日付昇順）。

Rolling-originは、confirm合計PnL > 0、confirm日中央値 > 0、best confirm日除外後もPnL > 0とする。

RefitLODOは、held-out合計PnL > 0、held-out日中央値 > 0、best 1日・best 2日除外後もPnL > 0とする。

### 3.5 探索方法（Version 2.1）

総利益最大化を探索目的にしない。日ごとの重みを均等にし、PUSH数・取引数が多い日が選定を支配しないようにする。

合格戦略間の優先順位：

1. best 2日除外後PnL
2. 日別PnL中央値
3. 日別PnL下位25%値
4. 上位1日・2日の利益集中率（低いほど良い）
5. 最大DD（浅いほど良い）
6. PF
7. 戦略の単純さ（パラメータ数が少ないほど良い）
8. 全期間総PnL

ENTRYを先に選ばない。ENTRY条件、invalidation、STOP、TARGET/TRAILING、no-progress、MAX_HOLDを固定した`JointStrategyPackage`単位で評価する。

まず既存JointRegistry全200戦略を実際にfull canonical replayする（イベント意味論はReplay lifecycle contractに完全準拠。エンジンはE1_X5 session直接replayとの取引台帳完全一致パリティを証明したもののみ使用可）。

200戦略で合格0の場合、総利益最大の不合格案を結果として返さず、ENTRY構造から再設計する。再設計では、利用可能なas-of特徴量を棚卸しし、以下の候補群を経済結果確認前に固定する。

- signal/scoreの継続性・傾き・加速度
- ENTRY前の価格追随・失速
- 出来高・板・spreadによる確認
- volatilityおよび市場状態
- ENTRY根拠ごとのinvalidation EXIT
- no-progress時間とMAX_HOLD
- initial STOPとTARGET/TRAILINGの組合せ

未来値、MFE、MAE、日付、銘柄固有条件、負けた時間帯の後付け除外は禁止する。candidate cap、列挙順、seed、threshold grid、tie-break、code/config/schema SHAをP1へ事前登録してから再生する。

合格なし → `E1_X6_NO_ROBUST_JOINT_STRATEGY`（条件緩和禁止。特定日依存戦略を「最良候補」として提示しない）。
全ゲート合格でも CORE_VALID=0 なら `E1_X6_JOINT_RESEARCH_SPEC_FROZEN_FOR_FORWARD_TEST`（採用済みと表現しない）。

Shadow有効化、Paper runner/Task/production YAML組込み、20営業日カウント開始、Discord、Runtime反映、Forward開始は**ユーザー明示承認まで禁止**。ユーザー承認まで`submit/cancel/live = 0/0/0`を維持する。

## 4. 非目的

本計画では以下を行わない。

- 実注文、Live注文経路の有効化
- E1_X5の閾値だけを微調整した別名ロジックの作成
- G1、旧enriched経路、旧block/delta値の再利用
- E1_X5がENTRYした取引だけを使う選択バイアスのある分析
- 結果を見た後のENTRY/EXITパラメータ同時最適化（事前登録したJointStrategyPackageの評価は必須）
- 銘柄固有、日付固有、7/22固有の条件
- 将来値をENTRY特徴量へ混入させること
- 期間内検証を未使用OOSまたはForward実績と呼ぶこと
- 単一日の大幅利益だけで候補を採用すること
- 合格候補がない場合に条件を緩和して無理にE1_X6を作ること
- 個別CSVの大量生成
- Shadowの自動開始・Runtime反映・実注文

## 5. 固定条件

| 項目 | 固定値・方針 |
|---|---|
| 運用 | Paperのみ |
| 注文安全値 | `submit/cancel/live = 0/0/0` |
| 比較BASE | 凍結したE1_X5 |
| 判断経路 | canonical経路へ一本化 |
| ENTRY評価タイミング | canonical `should_evaluate`に従う5秒ゲート＋`STATE_CHANGE` |
| FE・EXIT処理 | 全イベントを処理し、ENTRYゲートと混同しない |
| 旧経路 | G1、旧enriched、旧block/deltaは不使用 |
| 売買単位 | 100株換算 |
| コスト | 往復5bps。E1_X5と同一実装を1回だけ適用 |
| 保有上限 | 候補ごとに独立CAP5 |
| 再ENTRY | 同一episode内は禁止 |
| pyramiding | 禁止 |
| データ | raw captureは変更しない |
| Universe | 各判断時点でRuntimeが実際に選定・登録していた銘柄だけを使用。後日の成績で銘柄を追加・除外しない |
| 比較mask | E1_X5と全候補へ同一の`analysis_mask_id`を適用し、同じsource・window・評価可能時点で比較 |
| Runtime | 研究合格までMAINLINE、production YAML、Live注文経路を変更しない |
| First PUSH | Paper起動条件にしない。ENTRYだけを鮮度条件でブロックする |
| 成果物 | `report.json / report.md / audit.xlsx`の3ファイルへ統合 |

コスト、CAP、ENTRY評価タイミング、EXIT定義はE1_X5の固定実装から抽出し、研究開始時のmanifestへSHA付きで保存する。研究途中で再解釈しない。

### Replay lifecycle (analysis_mask partition)

Research-only confirm / BASE / final / LODO economics use the locked partition lifecycle below (verbatim). Data period, features, cost 5bps, CAP5, LOT100, candidate cap200, and acceptance numeric gates are unchanged.

```
AM_PM_CARRY = NO (fresh session per analysis_mask partition day×AM|PM).
WINDOW_END_OPEN = WINDOW_CENSORED / WINDOW_END_OPEN_EXCLUDED — orphan exclude from completed PnL; no force-close; no post-window exit grace.
EXIT_EVENT_SCOPE = every canonical board event in partition (FE+EXIT every event); ENTRY = score samples only (5s+STATE_CHANGE).
Partition scope = Source Manifest valid_window [start,end] inclusive for that AM/PM mask.
Events after valid_end are NOT processed for that partition (so EXIT 11:29 when mask ends 11:25 → position becomes WINDOW_CENSORED, not completed MAX_HOLD).
E1 continuous SESSION_CLOSE 11:30/15:30 may only fire if ts is still inside partition; for AM mask ending 11:25 it never fires.
No silent carry to next trading day.
Clock conflict AmPm 11:25 vs E1 11:30 is RESOLVED for research as: partition boundary = analysis_mask valid_window (canonical orphan rule). Document this in Plan 1.3.
```

Confirm / final / RefitLODO economics evaluation_mode must be `FULL_CANONICAL_EVENT_REPLAY` only. `PORTFOLIO_REPLAY_ON_LABELED_SCORE_ROWS` / approximated confirm economics are forbidden. Pipeline A/B uses separate caches under `temp/e1x6_final_<id>/run_A` and `run_B`, run sequentially (A then B) to avoid OOM on ~16GB machines.

## 6. 対象期間とデータの扱い

### 6.1 対象日

対象は2026-07-21～2026-07-31の9営業日とする。

| 日付 | 既存の扱い | E1_X6での扱い |
|---|---|---|
| 7/21～24 | 部分Capture参考 | 有効なCapture windowだけを設計データとして使用 |
| 7/27 AM | 部分AM参考 | 有効範囲を設計データとして使用 |
| 7/27 PM | Parity基準、`NOT_ADOPTED` | 既存Parity証跡の確認と設計データに使用 |
| 7/28 | `EXCLUDED_LAG_RESYNC` | 正式Forwardにはしない。失敗分析・ストレス設計データに使用 |
| 7/29 | `RETROSPECTIVE_REFERENCE` | 設計データに使用。Forwardへ遡及加算しない |
| 7/30～31 | 未確定 | E1_X5無変更で収集し、seal後に分類 |

「7/21～31をすべて使う」とは、品質差を無視して全行を同列に結合することではない。各windowを以下へ分類し、同一候補について層別結果と`ALL_USABLE`結果の両方を出す。

| 区分 | 定義 | 用途 |
|---|---|---|
| `CORE_VALID` | canonical validatorを通過し、時系列再構成に重大な曖昧さがない | 主分析・候補判定 |
| `PARTIAL_VALID_WINDOW` | 日全体ではないが、境界が明確でwindow内が有効 | 設計・感度検査 |
| `STRESS_RECOVERABLE` | lag/resync/gap等の警告があるが、再生が決定的で経済ledgerを構成できる | 失敗分析・ストレス検査 |
| `INVALID_SOURCE` | 順序、時刻、重複、欠損等により判断時点を一意に再構成できない | 経済集計から除外し、理由だけ監査保存 |

品質区分、採用window、期待session範囲、source優先順位、重複排除規則は、戦略PnLを見る前にSource Manifestへ固定する。損益、勝敗、候補の都合を理由に区分やwindowを変更してはならない。変更が必要な場合はmanifest revisionを上げ、影響するBASE・dataset・候補評価をすべて無効化して再実行する。

本書でいう`ALL_USABLE`は、`CORE_VALID + PARTIAL_VALID_WINDOW + STRESS_RECOVERABLE`の和集合であり、`INVALID_SOURCE`を含まない。

- `CORE_VALID`を最終候補の主たる合否判定層に使う。
- `PARTIAL_VALID_WINDOW`は固定されたwindow内に限り、仮説生成、候補構築、内部fold、感度検査に使える。ただし日全体へ外挿、時間補正、PnLの比例拡大を行わない。
- `STRESS_RECOVERABLE`は失敗構造の解析、候補の棄却、ストレス検査に使う。これだけを根拠に特徴量方向や数値閾値を最適化しない。
- すべてのBASE・候補は同じ`analysis_mask_id`上で比較し、候補ごとに都合のよい有効行を選ばない。
- Captureやsessionが重複する場合は、Source Manifestで固定した決定的event keyにより同一eventを1回だけ数える。内容が競合して一意に解決できないeventは`INVALID_SOURCE`とする。
- 期待session範囲は、後から推測せず、pinしたrunner/configの予定windowから決める。各windowについて有効秒数、期待秒数、coverage率を保存する。
- 日別Universeは、ENTRY判断前に確定していた選定時刻・登録銘柄・universe SHAを保存する。後日作った50銘柄の和集合へ置換しない。

データ品質フラグ自体を利益予測の特徴量として使用しない。欠損値を0へ置換してENTRY条件を成立させない。

### 6.2 必須データ監査

日・AM/PM・session・windowごとに以下を保存する。

- capture session ID、push part、seal、開始・終了時刻
- expected window、valid window、coverage率、`analysis_mask_id`
- Universe選定時刻、登録銘柄、universe SHA
- raw件数、正規化件数、銘柄数
- gap、重複、sequence inversion、時刻逆転
- source重複排除件数、競合件数、source優先順位
- resync、`CONSUMER_LAG`、欠損、decode失敗
- quote・board・volume・trade-sideの品質
- `evaluated / no_evaluation`と理由
- ENTRY、completed、open、orphan
- CAP blocked、same-symbol blocked
- Runtime↔Offline不一致
- source manifest revision、source SHA、canonical dataset SHA、ledger SHA

window末尾まで将来ラベルの観測時間を確保できない評価点は`CENSORED`とし、負けや0リターンへ置換しない。AM/PM境界、昼休み、session末尾をまたいで将来ラベルを作らない。

Runtime↔Offlineは同一source・同一window・同一`analysis_mask_id`に揃えた場合だけParity比較する。実session開始時刻やsession分割が異なる比較は`NOT_COMPARABLE_SCOPE`として差を保存し、Parity合格とも不一致0とも表現しない。scope差があるretrospective replayは、決定性と品質区分を満たせば設計・ストレス用途には使えるが、正式Forward実績にはしない。

## 7. Gate 0：Source・Parity・BASE確定

E1_X6の候補探索より先に、入力とE1_X5比較BASEを確定する。

### 7.1 7/27 PM canonical経路Parity

これは戦略成績の採否試験ではなく、次の経路が既存Oracleと同一であることを確認する経路整合性試験である。

```text
Capture
→ canonical変換
→ 評価・ENTRY判定
→ EXIT判定
→ 取引台帳・損益集計
```

2026-07-27 PMは既存検証でParity合格済みであり、証跡の想定repository-relative配置先は`kabu_native/results/research/e1_x5_runtime_offline_parity_20260727/`である。実配置が異なる場合は既存成果物を検索して一度だけ解決し、同名の証跡を新規作成しない。Gate 0では、まずその証跡、source/config/code SHA、下表の値が揃っていることを確認する。

canonical event処理、E1_X5判断経路、入力正規化、依存version、対象source/configのいずれにも影響する変更がなく、既存証跡が完全なら再実行しない。証跡欠損、SHA不明、または影響変更がある場合だけ再実行する。既存合格の再利用または再実行の判断理由を`report.json`と`audit.xlsx`へ残す。

再実行が必要な場合、2026-07-27 PMを同じ入力scopeで再生し、以下と完全一致しなければ以降を中止する。`events`等のscopeは既存Parity監査のfield定義をそのまま使い、raw件数など別scopeの値と比較しない。

| metric | 必須値 |
|---|---:|
| events | 547,817 |
| evaluated / no_evaluation | 17,353 / 308 |
| completed | 70 |
| PnL | +45,023.825円 |
| PF | 1.9226340172410525 |
| W/L/D | 35/35/0 |
| ledger SHA-256 | `b5837b4871273aad64445e76c251a3bc72ff6aa98c41107c04dffaefe04ef2d4` |

Parity合格は7/27 PMの利益やE1_X5を採用する根拠ではない。7/27 PMの扱いは引き続き`NOT_ADOPTED`である。

### 7.2 決定性

同じ入力を最低2回再生し、次を完全一致させる。

- canonical evaluation dataset SHA
- ENTRY decision ledger SHA
- completed trade ledger SHA
- counters
- PnL、PF、W/L/D、EXIT理由

一致しない場合はロジック研究を開始せず、`E1_X6_SOURCE_BLOCKED`とする。

### 7.3 E1_X5比較BASE

7/21～31の各有効windowを凍結E1_X5で再生し、以下を同一母集団で確定する。

- signal-level結果
- 独立CAP5を適用したportfolio-level結果
- AM、PM、日計、全期間
- `CORE_VALID`、`ALL_USABLE`、品質区分別
- PnL、PF、W/L/D、最大DD
- STOP件数、STOP損失、STOP損失/完了取引
- TARGET、TRAILING、MAX_HOLD、no_progress等のEXIT内訳
- top 1日、top 1取引、top 1銘柄を除いたPnL
- ENTRY候補、採用、CAP blocked、same-symbol blocked、open、orphan

既存の7/21～29参考値は照合対象に使うが、新BASEを上書きする正解値としては使わない。

### 7.4 Metric Contract

BASEと候補の集計定義を次で固定する。

- 経済指標は、確定したcompleted tradeだけを対象とする。open、orphan、capacity rejected、skipped、notification-only、shadow-onlyをPnL、PF、W/L/Dへ混ぜない。
- trade PnLは100株換算で、往復5bpsを1回だけ控除した未丸め値を内部集計に使う。表示時の丸め値を再集計しない。
- `PF = 利益tradeのPnL合計 / abs(損失tradeのPnL合計)`とする。損失tradeが0件の場合は数値を捏造せず、JSONでは`null`と`NO_LOSS` statusを保存する。合否上は必要trade supportとPnL条件を満たす`NO_LOSS`をPF gate合格として扱う。
- W/L/Dは5bps控除後のtrade PnLの正・負・0で決める。
- 主比較の最大DDはJSTの確定EXIT順に並べた累積100株PnLから計算し、`realized_trade_sequence_max_dd`として保存する。同時刻eventのtie-breakをmanifestへ固定する。再構成可能な場合は`mark_to_market_max_dd`も別指標で保存し、両者を混同しない。
- partial windowを1日分へ換算せず、実在する同一`analysis_mask_id`の範囲だけを比較する。
- reportの全期間値は、日別丸め値の和ではなくcompleted trade ledgerから直接再集計する。

## 8. 解析用canonical dataset

### 8.1 母集団

E1_X5採用取引だけでなく、監視50銘柄の全canonical ENTRY評価可能時点を対象とする。

各レコードには最低限、以下を持たせる。

- `day / session / AM_PM / window_id`
- `symbol`
- `event_time / decision_time / asof_time`
- `evaluation_id / episode_id`
- 判断時点のuniverse ID、registration状態、`analysis_mask_id`
- `should_evaluate`の理由
- E1_X5 score、accept/reject、reject理由
- ENTRY時点で利用可能だった特徴量
- 特徴量ごとの観測時刻、age、quality
- 将来ラベルとcensor理由
- E1_X5での仮想EXIT結果

PUSH件数や5秒ごとの行数を独立サンプル数とはみなさない。主要な頑健性単位は日とepisodeとする。

同一判断時点が複数sourceに存在する場合は、Source Manifestの重複排除後の1レコードだけを使う。Universe未登録、mask外、必要featureの時刻を一意に確定できない評価点を後から補完して母集団へ追加しない。

row-levelのランダム分割は禁止し、splitは日付境界で行う。非ENTRY例のsubsamplingやclass weightを使う場合は、構築期間内だけで決定し、seed・抽出率・重みをmanifestへ固定する。確認日と経済replayはsubsamplingせず全評価可能時点を使う。高頻度PUSHの銘柄や特定日が行数だけで支配しないよう、診断指標はrow単位に加えて日・symbol・episode単位でも出す。

### 8.2 特徴量

使用候補はENTRY判断時点までにcanonical経路で取得可能なものに限定する。

- Price：短期リターン、加速度、高値更新、VWAP距離、追いかけ距離、pullback
- Volume：出来高、出来高加速、売買代金、tick/update頻度
- Board：bid/ask、imbalance、板改善・悪化、spread、流動性
- Market State：canonical相場局面、全体方向、局面遷移
- Time：AM/PM、寄り後経過、引け前
- Episode：初回ENTRY候補、再ENTRY候補、前回EXIT後の状態変化
- Existing：PBv2、E1_X5 score構成要素

銘柄コード、日付、結果を見て作ったイベント固有フラグを予測条件にしない。相場局面はENTRY時点で確定していた値だけを使用する。

標準化、分位点、欠損処理、カテゴリ境界、feature選択、閾値算出などデータから学習する処理は、各Rolling-origin foldの構築期間だけでfitし、確認日へ変更せず適用する。全9日で計算した統計量をfold確認へ流用しない。固定値でないmarket regime定義も同じ扱いとする。

### 8.3 将来ラベル

ENTRY特徴量と将来結果を分離して、以下を作る。

- 30秒、1分、3分、5分、10分後リターン
- 同区間のMFE、MAE
- E1_X5の凍結TARGET・STOPのどちらへ先に到達したか
- cost控除前・5bps控除後の期待値
- E1_X5上のWinner、STOP、no_progress
- E1_X5が見送った後に上昇した`MISSED_WINNER`
- 上昇せず不要だった`UNNECESSARY_ENTRY`

TARGET・STOP・`MISSED_WINNER`の数値閾値は、Gate 0でE1_X5仕様から抽出し、candidate探索前にstudy manifestへ固定する。複数の将来期間から最も都合のよい結果だけを選ばない。

将来return、MFE、MAE、first-touch、仮想EXIT、Winner/STOP/MISSED等はlabel専用列とし、直接・集約・欠損patternを含めENTRY featureへ入れない。feature tableとlabel tableのschemaを分離し、join後にleakage監査を行う。

候補rankingに使う`primary_label_id`とprimary horizonをcandidate探索前にStudy Manifestへ1つ固定し、他のhorizonはsecondary診断とする。primaryで不合格の候補を、結果のよいsecondary horizonへ切り替えて救済しない。最終採否はlabel精度ではなく、§11の同一window上のportfolio-level経済ゲートで決める。

## 9. Phase 1：ENTRY再構築

### 9.1 原則

- EXITは凍結E1_X5のままとする。
- ENTRY品質とEXIT効果を混ぜないため、将来リターン・MFE/MAE・first-touchと、E1_X5固定EXITの両方で評価する。
- WinnerだけでなくSTOP、no_progress、見逃した上昇、全非ENTRY時点を同じ特徴量で比較する。
- signal-levelと独立CAP5適用後のportfolio-levelを分離する。
- 最終的な経済判定はportfolio-levelを主とし、CAPで隠れた信号品質をsignal-levelで監査する。

### 9.2 候補探索の制約

候補探索の検索空間を結果確認前にmanifestへ保存する。

- まず単独特徴量の方向性とsupportを評価し、その後、意味を説明できる2要素までの相互作用を評価する。
- 3要素以上の複雑な組み合わせは、2要素までで複数日に再現した場合だけ追加候補にできる。
- 銘柄固有・日付固有の閾値は禁止する。
- ある条件またはinteractionの学習supportが`n < 30`なら、記述用に残しても採用根拠にしない。
- 欠損を条件成立として扱わない。
- 候補ID、特徴量、方向、閾値、生成理由、評価順をCandidate Registryへ全件保存する。
- 結果を見て不利な候補だけを削除しない。棄却理由を残す。
- candidate family、閾値grid、最大interaction数、探索順、総候補数上限を経済結果の一括評価前にmanifestへ固定する。
- 各foldの閾値、分位点、前処理は構築期間だけから算出する。確認日や後続日の統計量を使わない。
- 探索開始後に新しいcandidate familyを追加する場合はstudy revisionを上げ、追加前に見たfoldを含めて全foldを再実行する。
- Registryには実評価した総候補数と有効な検索空間を保存し、多数候補から選んだ結果を単独仮説のように扱わない。

ENTRY候補は、単純なPnL最大ではなく、日別頑健性、7/22非依存、STOP抑制、最大DD、複雑度の順で絞る。

## 10. 期間内の内部検証

7/21～31は最終的にすべて設計へ使うため、以下は未使用OOSではない。目的は過学習と特定日依存の検出である。

### 10.1 Rolling-origin 5fold

| Fold | 構築期間 | 変更禁止の確認日 |
|---|---|---|
| F1 | 7/21～24 | 7/27 |
| F2 | 7/21～27 | 7/28 |
| F3 | 7/21～28 | 7/29 |
| F4 | 7/21～29 | 7/30 |
| F5 | 7/21～30 | 7/31 |

各foldでは、特徴量選択と閾値決定を構築期間だけで行い、確認日の結果を見て同foldを再調整しない。5fold全体を見て最終候補を選ぶため、集約結果は内部開発結果として扱う。

Rolling-originが評価するのは、全期間で後付けした1本の固定specではなく、事前登録したcandidate familyと構築手順である。各foldで選ばれたfamily、feature方向、閾値、supportを保存し、構造が一貫しているかを確認する。内部検査を通過した後だけ、同じ構築手順を全9営業日に1回適用して最終specを作り、以後変更せず凍結候補の検査へ進む。

各確認日のsource品質と`analysis_mask_id`はPnLを見る前に固定する。5つの確認日すべてに、判断時点とlabelを決定的に再構成できる評価maskが必要である。確認日が`INVALID_SOURCE`となりfoldを評価できない場合、別の日へ差し替えたり分母を変更したりせず、`E1_X6_INSUFFICIENT_EVIDENCE`とする。`PARTIAL_VALID_WINDOW`や`STRESS_RECOVERABLE`を使ったfoldは品質区分を明示し、`CORE_VALID`結果と混同しない。

### 10.2 日除外検査

LODOを次の2種類に分ける。

1. `FIXED_SPEC_DAY_DELETION`  
   全期間で固定した候補を変更せず、各日を1日ずつ集計から除外する。利益が特定日に依存していないかを見るハードゲートとする。
2. `REFIT_LODO_STABILITY`  
   1日を隠して残り8日で特徴量方向・閾値を再構築し、隠した日に一度だけ適用する。候補構造の安定性を診断する。最終OOSとは呼ばない。

7/22は通常どおり設計に含める。そのうえで、7/22を完全に除外した固定候補のPnLとPFを必ず計算する。7/22のデータ異常が確認されない限り、正式除外はしない。

### 10.3 必須層別

- AM / PM
- 日別
- `CORE_VALID / PARTIAL_VALID_WINDOW / STRESS_RECOVERABLE`
- market regime
- 銘柄
- 時間帯
- 初回ENTRY / 再ENTRY候補
- CAP適用前 / 適用後
- EXIT理由
- top 1日 / top 1取引 / top 1銘柄への利益集中
- worst 10取引と連敗
- STOP件数、STOP損失、MFEを出した後のSTOP

サブグループ`n < 30`は参考表示とし、単独で合否判定や新ルールの根拠にしない。

## 11. ENTRY候補の合格条件

以下はすべて、E1_X5と同一window・同一コスト・独立CAP5で比較する。

### 11.1 必須ゲート

| Gate | 合格条件 |
|---|---|
| Source | 7/27 PM既存Parity証跡を確認済み、または必要時の再実行で合格。重大な未解決データ不整合なし |
| Leakage | 全特徴量の`asof_time <= decision_time`、未来情報混入0 |
| Determinism | 二重再生のdataset・decision・trade ledger SHA完全一致 |
| Safety | `submit/cancel/live = 0/0/0` |
| Fold completeness | F1～F5の確認日すべてに、事前固定した決定的`analysis_mask_id`が存在 |
| Support | 採用根拠となる条件の学習supportが各foldで`n >= 30` |
| Trade support | `CORE_VALID`全期間と7/22除外の双方でcompleted trades `n >= 30` |
| Procedure stability | 同じcandidate familyとfeature方向が5fold中3fold以上で選択され、方向反転なし |
| `ALL_USABLE` | 5bps控除後PnL > 0、PF >= 1.10 |
| `CORE_VALID` | 5bps控除後PnL > 0、PF >= 1.10 |
| 7/22除外 | PnL > 0、PF > 1.00 |
| Rolling-origin | 5fold中3fold以上で確認日PnL > 0、5fold中央値PnL > 0 |
| 日依存 | `FIXED_SPEC_DAY_DELETION`の全ケースで残存期間PnL >= 0 |
| 取引集中 | top 1取引除外PnL > 0、top 1銘柄除外PnL > 0 |
| BASE比較 | PF改善、STOP損失改善、STOP損失/完了取引改善、最大DD改善 |
| 実装 | canonical経路だけで同一判断を再現可能 |
| 複雑度 | 銘柄・日付固有条件なし。最終候補は説明可能な1本 |

STOP損失は、単に取引数を極端に減らしたことで小さくなっていないかを、完了取引当たり・signal-level・見逃した上昇数と合わせて判断する。

### 11.2 候補が複数合格した場合

以下の優先順で1本に絞る。

1. Rolling-originのプラスfold数
2. 確認日PnL中央値
3. worst-day PnL
4. 7/22除外PF
5. 最大DD
6. E1_X5比のSTOP損失改善
7. 条件の単純さ

全期間PnLが最大という理由だけでは選ばない。

### 11.3 合格候補がない場合

条件を緩和せず、`E1_X6_NO_ROBUST_ENTRY_CANDIDATE`で終了する。EXIT最適化には進まない。

Sourceは再構成できても、fold completeness、coverage、日数、supportの不足により候補の良否を判定できない場合は、ロジック不合格と混同せず`E1_X6_INSUFFICIENT_EVIDENCE`で終了する。不足データを0件・0円・負けへ置換しない。追加データを収集して再計画する場合は、新しい対象期間と判定規約を結果を見る前にVersion更新する。

## 12. Phase 2：EXIT再設計

ENTRY候補が全ゲートを通過した場合だけ、そのENTRY仕様とENTRY ledgerを凍結してEXIT再設計へ進む。

### 12.1 対象

- STOP
- no_progress
- TARGET
- TRAILING
- MAX_HOLD
- MFE後の利益吐き出し
- ENTRY直後のMAE
- 時間帯・相場局面別の保有時間

### 12.2 手順

1. ENTRY固定＋E1_X5 EXITをEXIT比較BASEとする。
2. EXIT要素を1種類ずつ変更し、寄与を分離する。
3. 単独で複数日に改善した要素だけ、最終組み合わせ候補にできる。
4. ENTRY、CAP、コスト、再ENTRY条件をEXIT探索中に変更しない。
5. ENTRY候補と同じSource、Leakage、Determinism、7/22除外、Rolling-origin、日除外、集中度ゲートを適用する。

EXIT候補が安定改善しない場合は、無理に変更せずE1_X5 EXITを維持し、ENTRYだけを変更した`E1_X6_ENTRY_ONLY_CANDIDATE`として凍結できる。

## 13. E1_X6凍結

最終候補をForwardへ出す前に以下を固定する。

- ENTRY仕様と閾値
- EXIT仕様と閾値
- feature schema/version
- label schema/version
- canonical code commit SHA
- config SHA
- source manifest SHA
- cost、100株、CAP5、再ENTRY、pyramiding条件
- unit test結果
- 二重再生ledger SHA
- 7/21～31の全監査結果

凍結後のverdictは次のいずれかとする。

- `E1_X6_RESEARCH_CANDIDATE_FROZEN`
- `E1_X6_ENTRY_ONLY_CANDIDATE_FROZEN`
- `E1_X6_NO_ROBUST_ENTRY_CANDIDATE`
- `E1_X6_INSUFFICIENT_EVIDENCE`
- `E1_X6_SOURCE_BLOCKED`

凍結後に仕様、閾値、特徴量、コスト、CAPを変えた場合は別candidate IDとし、Forward日数を0から数え直す。

## 14. 凍結後Forward Paper検証

本書でいう「8月Forward」は8月1日からの暦月集計ではない。E1_X6のcandidate ID、spec、code/config/schema SHA、`frozen_at_jst`を保存した後、最初に到来する市場sessionから開始する。凍結前に取得・閲覧・分析した8月データを遡ってForwardへ加算しない。20有効営業日の完了が9月以降になっても、同一candidateの連続Forwardとして扱う。

### 14.1 実行方式

- Paper限定
- E1_X6と凍結E1_X5を同一feed上で並行比較
- ledgerとCAPは候補ごとに独立
- MAINLINE採用前は他戦略の保有枠へ干渉させない
- `submit/cancel/live = 0/0/0`
- データ品質不合格日は有効営業日数へ数えない
- 日次結果を見ても途中調整しない
- `forward_start_at_jst`、candidate ID、凍結SHA、日別quality判定を保存
- Forward日の採否に使うsource/coverage規則を開始前に固定し、PnLを見て有効日から外さない

### 14.2 判定時点

| 有効営業日 | 判定 |
|---:|---|
| 5日 | Source、Safety、決定性、実装不一致を確認。採用・損益判定はしない |
| 10日 | 中間集計のみ。採用・調整・損益理由の早期終了は行わない |
| 20日 | Paper候補として正式採用または棄却 |

20日未満で停止できるのは、安全違反、source破損、決定性喪失、実装driftが発生した場合だけとする。途中損益を理由に条件変更、別candidateへの差し替え、都合のよい期間打ち切りを行わない。

20有効営業日の正式ゲートは少なくとも以下とする。

- 5bps控除後PnL > 0
- PF >= 1.10
- completed trades `n >= 30`。未満は合格ではなく`INSUFFICIENT_FORWARD_SUPPORT`
- best 1日除外後もPnL > 0
- best 1取引除外後、best 1銘柄除外後もPnL > 0
- E1_X5同期間比でPF、STOP損失、最大DDが改善
- Runtime↔Offlineの経済ledger不一致0
- 二重再生ledger SHA一致
- `submit/cancel/live = 0/0/0`
- 未解決の重大データ品質問題なし

Forward合格はPaper候補への採用であり、Live移行は別計画・別承認とする。

## 15. 成果物

研究成果物は次のディレクトリへ3ファイルだけ生成する。

```text
kabu_native/results/research/e1_x6_redesign_20260721_20260731/
├── report.json
├── report.md
└── audit.xlsx
```

本計画書はrepositoryのdocs正本であり、上記「研究成果物3ファイル」の数には含めない。

Source Manifest、Study Manifest、Freeze Manifest、Candidate Registryは独立ファイルにせず、`report.json`内のversioned objectと`audit.xlsx`内の対応sheetへ格納する。計算途中の一時ファイルは成果物directory外で管理し、最終成果物として残さない。

### 15.1 `report.md`

- 結論とverdict
- 現在地と実行済みPhase
- E1_X5比較BASE
- E1_X6候補
- AM/PM・日別・品質区分別結果
- Rolling-origin、LODO、7/22除外
- ENTRY・EXITの主要改善点
- 未解決事項と次工程

### 15.2 `report.json`

- plan version
- run ID
- source/config/code/schema SHA
- 全metrics
- candidate registryと採否理由
- test結果
- safety counters
- final verdict

### 15.3 `audit.xlsx`

最低限、次のシートへ統合する。

- `Index`
- `Summary`
- `Source`
- `Windows`
- `DataQuality`
- `Dataset`
- `Features`
- `Labels`
- `Baseline`
- `Candidates`
- `WalkForward`
- `DayDeletion`
- `RefitLODO`
- `AM_PM`
- `Regimes`
- `Symbols`
- `Trades`
- `Exits`
- `Counters`
- `Concentration`
- `Parity`
- `Tests`
- `Safety`
- `ChangeLog`

Excelの1シート上限を超える明細は削除・集約で隠さず、同一workbook内で`Dataset_001`、`Dataset_002`、`Labels_001`のように決定的に分割する。`Index`へ各分割sheetのrow範囲、件数、schema、SHAを記録する。

`report.json`の集計値と`audit.xlsx`の台帳再集計は完全一致させる。不一致時は`report.md`へ都合のよい一方を掲載せず、verdictを保留して原因を解消する。個別CSV、候補ごとのExcel、同内容の重複レポートは生成しない。

## 16. 実行順序と停止条件

| 順序 | 工程 | 完了条件 | 停止条件 |
|---:|---|---|---|
| 1 | 7/30・31 Capture完了 | sealとsource inventory確定 | source欠損・破損 |
| 2 | Gate 0 | Parity・決定性・BASE確定 | `E1_X6_SOURCE_BLOCKED` |
| 3 | canonical dataset作成 | 全50銘柄、labels、quality確定 | leakage・時刻不整合 |
| 4 | E1_X5失敗構造解析 | Winner/STOP/no_progress/MISSED比較 | 再現不能 |
| 5 | ENTRY候補探索 | Candidate Registry確定 | 合格候補なし |
| 6 | 内部頑健性検査 | 全必須ゲート完了 | `E1_X6_NO_ROBUST_ENTRY_CANDIDATE` |
| 7 | EXIT再設計 | EXIT採用またはE1_X5 EXIT維持を決定 | 無理な改善は行わない |
| 8 | E1_X6凍結 | code/config/schema/SHA固定 | 未解決不一致 |
| 9 | 凍結後Forward | 5・10・20有効営業日 | 仕様変更時は0日へリセット |

## 17. 直近の実行事項

1. 7/30・31はE1_X5ロジックを変更せず、raw captureとPaper運用を完了する。
2. 7/31 PM終了後、経済結果を見る前に全9営業日のsource inventory、quality classification、`analysis_mask_id`を確定する。
3. 2026-07-27 PMの既存Parity合格証跡とSHAを確認する。影響変更または証跡不足がある場合だけ再実行する。
4. 7/21～31のE1_X5比較BASEを同一canonical経路で再構成する。
5. 全50銘柄・全評価可能時点のdatasetと将来ラベルを作成する。
6. ENTRY再構築を開始する。

7/30・31の結果を見てから、本書の合格条件や候補探索ルールを都合よく変更しない。

## 18. 他チャットへの引き継ぎ

### 18.1 引き継ぎpacket

新しいチャットには次を渡す。

| 必須度 | ファイル・情報 | 注意 |
|---|---|---|
| 必須 | 本書 | filenameの連番ではなくDocument ID=`E1_X6_VALIDATION_PLAN`とVersionで最新版を識別 |
| 生成後は必須 | 最新`report.json` | run ID、manifest、数値、verdictの基準 |
| 生成後は必須 | 同runの`audit.xlsx` | 台帳と監査根拠 |
| 生成後は必須 | 同runの`report.md` | 人間向け要約 |
| 必須 | 引き継ぎ時点のStatus Snapshot | 更新日時、完了証跡、次の1工程、blocker、成果物の有無 |

成果物がまだ生成されていないPhaseでは、旧runを「最新」として添付せず、`NOT_GENERATED`と明記する。異なるrun IDの3成果物を混ぜない。ファイル名に`(1)`等が付いても、内部のrun ID、plan version、SHAで組を確認する。

### 18.2 受領側の確認規約

受領したAIは作業前に次を確認する。

1. 本書のDocument ID、Version、Status Snapshot基準時点
2. 添付成果物の有無、run ID、plan version、source/config/code SHA
3. `report.json`と`audit.xlsx`のverdict・主要数値の一致
4. repositoryへ接続できる場合はroot、branch、`git status`、本書の実配置
5. 現在Phase、完了済み工程、次の1工程、停止条件

ChatGPTへ本書を添付しただけでは、Windows PC上のrepositoryやRuntimeへアクセスできるとは限らない。接続できない場合は実行済みと装わず、Cursor等の実行担当へ渡す具体的指示を作る。接続できる場合も、最初はread-onlyで証跡と差分を確認し、既存の無関係な変更を保持する。

計画書と成果物が矛盾した場合、受領側が推測でどちらかを採用してはならない。規約の矛盾は本書のVersion更新、実績値の矛盾は再集計または再実行で解消する。

### 18.3 引き継ぎ文面

以下の`[...]`を引き継ぎ時点の実値へ更新して送る。

```text
TradeBotのE1_X6再設計をこのチャットで継続します。

添付したDocument ID=E1_X6_VALIDATION_PLAN、Version=[version]を
正式なSource of Truthとして扱ってください。

引き継ぎ時点:
- snapshot_at_jst: [日時]
- current_phase: [Phase]
- last_completed: [工程と証跡]
- next_single_step: [次の1工程]
- blockers: [なし / 内容]
- result_artifacts: [NOT_GENERATED / 3ファイルのrun ID]
- repository_access: [あり / なし / 不明]

最初に計画書と添付成果物を読み、
1. plan versionとsnapshotの鮮度
2. 固定条件
3. 完了済み工程と証拠
4. 未完了工程
5. 次の1工程
6. 停止条件
7. 欠けているファイル・情報
を要約してください。

7/27 PM Parityはcanonical再生経路の整合性確認です。
既存合格証跡とSHAが完全で影響変更がなければ再実行せず、
影響変更または証跡不足がある場合だけ再実行してください。

8月データはE1_X6凍結後の最初のsessionからForwardへ算入し、
凍結前のデータを遡及加算しないでください。

ユーザーの明示承認とVersion更新を伴わない計画書との矛盾変更は禁止です。
本計画の範囲では実注文とLive注文経路の有効化は禁止です。
submit/cancel/liveは常に0/0/0です。矛盾や証跡不足があれば推測で進めず停止してください。
```

## 19. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-07-30 | 初版。7/21～31を全て設計データとし、品質区分を分離。7/22は含め、除外感度を必須化。期間内検証を内部検証、8月を未使用Forwardと定義。ENTRY固定後にEXITを再設計する工程と合否ゲートを確定。 |
| 1.1 | 2026-07-30 | 引き継ぎ監査。Status Snapshotと成果物優先関係を追加。7/27 PM Parityを既存合格済みの経路整合性試験と明確化し、影響変更時だけ再実行する規約へ修正。Source分類・window mask・重複排除をPnL確認前に固定。Universe as-of、metric contract、fold内前処理、候補探索規約、証拠不足verdictを追加。Forward開始を凍結後最初のsessionとし遡及算入を禁止。Excel行上限と引き継ぎpacketを明文化。 |
| 1.2 | 2026-07-30 | 暫定実行許可。status=`APPROVED_PLAN / PRE_GATE_0_PROVISIONAL_EXECUTION_ALLOWED`。7/30時点でP0→P1→暫定P2/F1～F4を許可。全結果は`PROVISIONAL_NOT_FOR_SELECTION`。7/31 PM後は新final run IDで7/21～31を最初から再実行。暫定runへF5だけを追加しない。固定条件・合格条件の数値は変更しない。正本パスを実在一意パスへ記録。 |
| 1.3 | 2026-08-01 | Replay lifecycle contract for analysis_mask partitions. Previous Version 1.2 SHA `f037b770d23f235aa651153ae357a060dddd9fc2fb353161651f0ca4ef0e66fe`. Reason=`replay lifecycle contract for analysis_mask partitions`. Affected run `e1x6_final_20260801_024352_97202b28` → disposition `SUPERSEDED_REPLAY_BOUNDARY_CONTRACT_ERROR`. Data period / features / cost 5bps / CAP5 / LOT100 / candidate cap200 / acceptance numeric gates unchanged. Confirm economics = `FULL_CANONICAL_EVENT_REPLAY` only. |
| 2.0 | 2026-08-02 | JointStrategyPackage evaluation. Previous Version 1.3 SHA `69adeab44a636e953440314968d9e747baec4e2cc97494b1a84cf9929fbea5d8`. Reason=`ENTRY+EXIT joint evaluation; abolish ENTRY-only adoption and serial EXIT-after-ENTRY`. Stage-1 audit final `e1x6_final_20260801_215129_3ef3736e` completed under 1.3; Version 1.x ENTRY results = `ENTRY_HYPOTHESIS_ONLY / RETROSPECTIVE_REFERENCE`. Shadow auto-start abolished (user approval required). Data period / 5bps / CAP5 / LOT100 / analysis_mask lifecycle unchanged. |
| 2.1 | 2026-08-02 | Day-robust joint gates. Previous Version 2.0 SHA `72d692dfd89b98ff50b6ca3fcdcc6ab17c449216c5bf3d619cdc1eb2ccf2c82a`（履歴保存: `docs/history/e1_x6_validation_plan_v2.0_RETIRED_HISTORY.md`）. Reason=`研究目的を「全日無敗」から「利益の日別分散・通常日の期待値」へ修正（条件緩和ではない）`. 廃止: 9/9日全プラス / worst_day>0 / Rolling-origin 5/5 / RefitLODO 9/9 / Forward 20/20. 追加: 日別中央値>0、best1/2日除外後PnL>0、top1日寄与<=30%・top2日寄与<=50%、Rolling-origin(合計>0・中央値>0・best confirm日除外後>0)、RefitLODO(合計>0・中央値>0・best1/2日除外後>0)。探索は日重み均等・優先順位8段（§3.5）。Plan 2.0 sweepの4戦略=`REJECTED_DAY_CONCENTRATED / FAILURE_ANALYSIS_ONLY`。日付固有条件（7/22・7/31等）のゲート組込み禁止、上位日は毎回機械算出。環境事象: 2026-08-02のOS temp/results清掃によりStage-1公開3成果物とtemp台帳がディスクから消失（SHA記録は`constants.STAGE1_FINAL_RUN_20260801`に保持。監査不備ではない）。経済結果はraw captureから再生成する。Data period / 5bps / CAP5 / LOT100 / candidate cap200 / analysis_mask lifecycle unchanged. |

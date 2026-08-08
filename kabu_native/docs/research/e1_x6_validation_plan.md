# TradeBot E1_X6 再設計・検証計画書

| 項目 | 内容 |
|---|---|
| Document ID | `E1_X6_VALIDATION_PLAN` |
| Version | `1.3` |
| 制定日 | 2026-07-30 |
| 最終監査日 | 2026-07-30 |
| 状態 | `REVISION_1_3 / ENTRY_EXIT_JOINT_EVALUATION` |
| 状態基準時点 | 2026-07-30。本書監査時点でRuntimeは未再確認 |
| 正本配置先（repository） | `kabu_native/docs/research/e1_x6_validation_plan.md` |
| 想定repository root | `C:\Users\yhach\Documents\tradebotfile`。実行前に実在とbranchを確認する |
| 対象 | E1_X5の失敗分析、E1_X6のENTRY再構築、EXIT再設計、凍結後Forward Paper検証 |

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

| 項目 | 2026-07-30監査時点 |
|---|---|
| 現在Phase | `PRE_GATE_0 / PROVISIONAL_PREPARATION_ALLOWED` |
| 確定済み | E1_X5不採用・比較BASE凍結。7/27 PM canonical Parityは既存証跡上で合格済み。7/30先行実行規約をVersion 1.2で固定 |
| 未確認・未完了 | 7/30 Captureのseal・品質、7/31 Capture完了、9営業日の最終Source Manifest、7/21～31新比較BASE、E1_X6候補 |
| 次の1工程 | E1_X5を変更せず、7/30 Captureのseal・source品質を証跡で確認する |
| 先行実行の上限 | 事前固定後の暫定F1～F4まで。候補採否、LODO、EXIT再設計、凍結は禁止 |
| 最終判定可能時点 | 7/31 PM seal後に最終Manifestを固定し、全9日・F1～F5を最初から再実行した後 |
| 既知の停止要因 | 現時点では未確定。source破損・Parity drift・leakageがあれば停止 |
| 安全条件 | Paperのみ。`submit/cancel/live = 0/0/0` |

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
- 7/30：日次通知を見ていても、Captureのseal・source品質を証跡確認するまでは未確定。確認後は§7.0の範囲で先行実行できる
- 7/31：本書Version 1.2制定時点では未収集。E1_X5を変更せずCaptureを完了させ、PM seal後に最終工程へ進む

本書で「経済結果を見る前に固定する」とは、E1_X6候補別のreplay PnL、PF、順位、fold成績を開く前に固定することを指す。すでに表示されたE1_X5の日次Summaryは既知情報として監査に残すが、それを理由にsource品質区分、採用window、候補family、閾値grid、合格条件を変更しない。

## 3. 目的

主目的は、監視50銘柄のENTRY可能時点から「これから上昇する状態」と「不要ENTRYになる状態」を分離し、日・銘柄・相場局面への依存が小さいE1_X6を構築することである。

具体的には以下を実現する。

1. E1_X5が勝った理由と、STOP・no_progress・見逃した上昇の違いを同じ特徴量で説明する。
2. E1_X5がENTRYした取引だけでなく、監視50銘柄の全評価可能時点を母集団にする。
3. ENTRY候補を先に生成して値動きシナリオを診断し、各ENTRY候補に対応するEXITを設計したうえで、ENTRY×EXITの組み合わせとして採否を判定する。
4. 7/22の強い上昇局面を学習に残しながら、7/22がなくても成立する候補だけを残す。
5. 期間内のウォークフォワード、日除外、AM/PM、相場局面、銘柄集中度で過学習を検出する。
6. 合格候補を1本に絞って完全凍結し、凍結後に到来する未使用Paperで5・10・20有効営業日を検証する。

## 4. 非目的

本計画では以下を行わない。

- 実注文、Live注文経路の有効化
- E1_X5の閾値だけを微調整した別名ロジックの作成
- G1、旧enriched経路、旧block/delta値の再利用
- E1_X5がENTRYした取引だけを使う選択バイアスのある分析
- ENTRY候補だけの損益を最終採否として扱うこと
- ENTRY条件とEXIT条件を同一探索ループで無制限に同時最適化すること
- 銘柄固有、日付固有、7/22固有の条件
- 将来値をENTRY特徴量へ混入させること
- 期間内検証を未使用OOSまたはForward実績と呼ぶこと
- 単一日の大幅利益だけで候補を採用すること
- 合格候補がない場合に条件を緩和して無理にE1_X6を作ること
- 個別CSVの大量生成

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
| 7/30 | Capture・seal監査未完了 | seal・品質確認後、§7.0の範囲で暫定設計とF1～F4に使用。正式Forwardにはしない |
| 7/31 | 未確定 | E1_X5無変更で収集。PM seal後に最終Manifestへ取り込み、全工程を再実行 |

「7/21～31をすべて使う」とは、品質差を無視して全行を同列に結合することではない。各windowを以下へ分類し、同一候補について層別結果と`ALL_USABLE`結果の両方を出す。

| 区分 | 定義 | 用途 |
|---|---|---|
| `CORE_VALID` | canonical validatorを通過し、時系列再構成に重大な曖昧さがない | 主分析・候補判定 |
| `PARTIAL_VALID_WINDOW` | 日全体ではないが、境界が明確でwindow内が有効 | 設計・感度検査 |
| `STRESS_RECOVERABLE` | lag/resync/gap等の警告があるが、再生が決定的で経済ledgerを構成できる | 失敗分析・ストレス検査 |
| `INVALID_SOURCE` | 順序、時刻、重複、欠損等により判断時点を一意に再構成できない | 経済集計から除外し、理由だけ監査保存 |

品質区分、採用window、期待session範囲、source優先順位、重複排除規則は、戦略PnLを見る前にSource Manifestへ固定する。損益、勝敗、候補の都合を理由に区分やwindowを変更してはならない。変更が必要な場合はmanifest revisionを上げ、影響するBASE・dataset・候補評価をすべて無効化して再実行する。

7/30時点のSource Manifestは`PROVISIONAL`とし、7/31を含む最終Manifestと同一視しない。最終Manifestは、7/31 PM seal後に全9営業日を対象として再生成し、source品質、coverage、gap、重複、時刻整合などの技術監査項目だけで確定する。この確定作業中はE1_X6候補の経済出力を参照しない。すでに既知のE1_X5日次Summaryも、品質区分変更の根拠にしない。

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
- funnelの排他的内訳と保存則。`evaluated`に対する未分類差分、`TICK_BUILD_FAILED`等の理由別件数
- ENTRY、completed、open、orphan
- CAP blocked、same-symbol blocked
- Runtime↔Offline不一致
- source manifest revision、source SHA、canonical dataset SHA、ledger SHA

Discord等の表示上のfunnel差分と、canonical decision ledger自体の欠損を分離する。ledgerとraw counterが整合し表示だけが欠けている場合は`REPORTING_MISMATCH`として修正対象に残すが、直ちにsource破損とはみなさない。判断eventが未分類・欠損している場合はGate 0を停止する。

window末尾まで将来ラベルの観測時間を確保できない評価点は`CENSORED`とし、負けや0リターンへ置換しない。AM/PM境界、昼休み、session末尾をまたいで将来ラベルを作らない。

Runtime↔Offlineは同一source・同一window・同一`analysis_mask_id`に揃えた場合だけParity比較する。実session開始時刻やsession分割が異なる比較は`NOT_COMPARABLE_SCOPE`として差を保存し、Parity合格とも不一致0とも表現しない。scope差があるretrospective replayは、決定性と品質区分を満たせば設計・ストレス用途には使えるが、正式Forward実績にはしない。

## 7. Gate 0：Source・Parity・BASE確定

E1_X6の正式な候補探索・採否より先に、入力とE1_X5比較BASEを確定する。7/30先行実行だけは、以下の規約に従い`PROVISIONAL`の入力・BASEで予備実行できる。

### 7.0 7/30先行実行規約

7/30からの先行着手を許可する。ただし、先行実行は7/31を含む正式検証の代替ではなく、実装・データ・手順を早期に確認するための暫定工程である。先行実行を行わず7/31 PM後から正式工程を開始しても、計画上の不利益はない。

| Stage | 開始条件 | 実施してよいこと | 実施してはいけないこと |
|---|---|---|---|
| `P0_PROVISIONAL_PREP` | 直ちに開始可能。F4には7/30 PM sealが必要 | 7/30 seal・品質確認、7/21～30暫定Source Inventory、7/27 PM既存Parity証跡・SHA確認、既存日の決定性確認、BASE・dataset生成経路の実装確認 | E1_X6候補の採否、最終品質区分、最終BASEの宣言 |
| `P1_STUDY_PRECOMMIT` | E1_X6候補別の経済出力を一度も開いていない | source判定規則、feature/label schema、primary label、candidate family、許容する方向と方向決定手順、閾値生成法・grid、interaction上限、探索順、候補数上限、seed、fold、合格条件を固定し、時刻とSHAを保存 | PnL・PF・勝率・fold順位を見てから固定値を選ぶこと |
| `P2_PROVISIONAL_F1_F4` | `P1`完了。F4は7/30 PM seal・mask確定後 | 暫定E1_X5 BASE、暫定canonical dataset、F1～F4の予備実行、コード・schema・集計不具合の検出 | LODO・7/22除外の正式判定、候補選定、EXIT再設計、候補凍結、Forward開始 |
| `F0_FINAL_9DAY_RERUN` | 7/31 PM seal後、最終Source Manifest・`analysis_mask_id`固定 | 全9日BASE、dataset、labels、F1～F5、LODO、7/22除外、全ゲートを新しいfinal run IDで最初から再実行 | 暫定F1～F4へF5だけを追加すること、暫定集計を正式値へ昇格すること |

先行実行では次を必須とする。

- `P1_STUDY_PRECOMMIT`より前は、E1_X6候補別のPnL、PF、W/L/D、ランキングを生成・表示しない。
- `P2`の全出力へ`PROVISIONAL_NOT_FOR_SELECTION`、plan version、provisional run ID、precommit SHAを付ける。
- 暫定結果を理由にcandidate family、許容する特徴量方向・方向決定手順、閾値grid、interaction上限、探索順、合格条件、source判定規則を変更しない。
- 実装不具合、schema不整合、計算誤りを修正する場合は、変更理由を経済成績と切り離してChangeLogへ記録し、影響する暫定結果をすべて`INVALIDATED`にする。PnL悪化・PF不足・勝率不足は修正理由にできない。
- 7/31 PM後は、最終Manifestと新しいfinal run IDでGate 0から全9日を再生成する。F1～F4も再実行し、暫定ledger、閾値、集計値を正式結果へコピーしない。
- fold内fitは引き続き各構築期間だけで行う。全9日の最終再実行は、F1～F4へ7/31の情報を学習投入する許可ではない。
- 暫定出力を個別CSVや別レポートとして最終成果物へ残さず、run ID、precommit、無効化履歴、主要監査結果を最終3成果物内へ統合する。

`P1`より前にE1_X6候補別の経済出力をすでに開いていたことが判明した場合、そのprecommitは成立しない。`E1_X6_PRECOMMIT_CONTAMINATED`として記録し、見た内容、影響範囲、新しいstudy revisionを明示してから全foldを再実行する。これは期間内検証を正式OOSへ戻すものではなく、汚染を隠さず管理するための規約である。

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
- 日・AM/PMごとの完了取引数とPnL・PF・STOP率の関係
- 60秒・5分window内のENTRY密度、同一銘柄再ENTRY回数、episode当たりENTRY回数
- ENTRYからEXITまでの時間帯別件数、30秒以内・60秒以内・120秒以内STOP件数と損失
- session内の取引順、累積PnL、各追加取引の限界PnL
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

## 9. Phase 1：ENTRY候補生成とEXITシナリオ診断

### 9.1 原則

- ENTRY候補を出した時点では採用・不採用を判定しない。
- 凍結E1_X5 EXITによる損益は比較BASE・問題診断として保存するが、ENTRY候補の最終採否には使わない。
- ENTRY候補ごとに、ENTRY後の値動き経路を再構成し、その候補が想定する上昇シナリオと失敗シナリオを明確化する。
- EXITはENTRY候補のシナリオに対応して設計する。
- 最終採否単位は`entry_candidate_id × exit_candidate_id`の組み合わせとする。
- ENTRY探索とEXIT探索を無制限に同時最適化しない。ENTRY仕様を一度固定して経路診断を行い、その後にEXIT候補群をprecommitして経済評価する。
- signal-levelと独立CAP5適用後のportfolio-levelを分離する。最終経済判定は組み合わせごとのportfolio-levelを主とする。

### 9.2 ENTRY Candidate Registry

ENTRY候補探索の検索空間を、候補別経済出力を見る前に`P1_ENTRY_PRECOMMIT`へ保存する。

- candidate family、特徴量方向、閾値生成法、grid、状態順序、候補数上限を固定する。
- 銘柄固有・日付固有・AM/PM固有の閾値は禁止する。
- 各foldの閾値・分位点・前処理は構築期間だけでfitする。
- support不足候補は`ENTRY_UNREACHABLE`として残し、負け候補と混同しない。
- ENTRY候補の段階でPF、PnL、最大DDによる採用判定を行わない。
- E1_X5 EXITによる結果は、既存EXITとの相性と失敗構造を把握するための診断値とする。
- ENTRY候補を最終候補へ進める条件は、Source、Leakage、Determinism、Safety、状態到達support、複数日での発生、ENTRY後にコストを超える上昇余地が観測されることだけとする。
- ENTRY後の最大可能利益を未来情報で実装へ使わない。MFE・MAE等はEXIT設計用labelと診断に限定する。

### 9.3 ENTRY後の経路台帳

各ENTRY候補について、ENTRY後の全eventを最大300秒またはsession終了まで保存する。

最低限、以下を時間軸で持つ。

- ENTRYからの経過秒
- bid / ask / mid / spread
- ENTRY価格、micro-high、pullback-low、VWAP、session highとの距離
- 現時点までのMFE・MAE
- 現在損益、最高値からのgiveback
- 新高値更新回数、最後の新高値からの経過時間
- breakout levelの維持・再割れ・再奪回
- volume 10/30/60秒、volume persistence
- uptick / downtick volume比率
- tick・price update速度
- bid支持、ask補充、imbalance変化
- event / trade / board freshness
- E1_X5 EXITが発火した時刻と理由
- 30秒、60秒、120秒、300秒のcensor状態

### 9.4 EXIT設計用シナリオ分類

将来情報を用いた分類は、EXIT候補の仮説生成と診断labelにのみ使用する。Runtime条件へ直接入れない。

ENTRY後の経路を次へ排他的に分類する。

| scenario_id | 経路 |
|---|---|
| `S1_IMMEDIATE_CONTINUATION` | ENTRY後早期にコストを超え、新高値を継続する |
| `S2_RETEST_THEN_CONTINUATION` | breakout level付近まで戻すが、構造を維持して再上昇する |
| `S3_FALSE_BREAKOUT` | breakout levelを割り、回復せず下落する |
| `S4_NO_PROGRESS` | 大きく逆行しないが、出来高・更新・上昇が続かない |
| `S5_SPIKE_GIVEBACK` | 一度十分なMFEを出した後、利益を大きく返す |
| `S6_LATE_CONTINUATION` | 初動は遅いが、許容時間内に上昇する |
| `S7_CENSORED_OR_OTHER` | session境界、source不足、上記に一意分類できない |

分類境界は`P1_ENTRY_PRECOMMIT`時点で固定したprimary horizon・first-touch・cost基準と、構築期間内の分位点だけで決める。結果のよい分類へ後から境界を変更しない。

### 9.5 EXITに利用可能な指標候補

EXIT判断に使用できるのは、その時点までの情報だけとする。

#### 価格構造

- ENTRY価格からのreturn
- micro-high / reclaim levelからの距離
- pullback-lowからの距離
- VWAPからの距離
- 現在までのMFE / MAE
- MFEからのgiveback
- 最後の新高値からの経過時間
- 新高値更新回数
- 短期傾き・加速度
- ATR正規化距離

#### 出来高・約定フロー

- volume 10/30/60秒
- volume persistence
- ENTRY前impulseに対する出来高維持率
- uptick / downtick volume比率
- 売り約定加速
- price impact efficiency
- tick / price update速度

#### 板・流動性

- bidによるreclaim level支持
- best bid低下回数
- ask補充・吸収
- imbalance改善・悪化
- spreadとspread拡大
- board freshness

#### 時間・状態

- ENTRY後経過時間
- 最初のcost超過までの時間
- 最後のprogressからの経過時間
- state遷移
- session終了までの残時間

ENTRY後の最終MFE、最終MAE、将来EXIT理由、最終scenario_idをRuntime EXIT featureへ入れない。

### 9.6 二段階Precommit

EXITをENTRY経路に合わせて設計しつつ、後付け最適化を防ぐためprecommitを二段階に分ける。

1. `P1_ENTRY_PRECOMMIT`  
   ENTRY family、閾値生成法、ENTRY候補数、経路台帳schema、scenario分類法を固定する。
2. `P2_EXIT_PRECOMMIT`  
   構築期間の経路診断を完了した後、候補別EXIT scenario、使用指標、方向、閾値生成法、EXIT候補数上限、組合せ方法を固定する。EXIT候補別PnL・PF・順位を見る前に時刻とSHAを保存する。

`P2_EXIT_PRECOMMIT`後にEXIT指標、scenario、閾値gridを追加する場合はstudy revisionを上げ、全foldを最初から再実行する。

### 9.7 X5→X6の判断差分監査

凍結E1_X5と各ENTRY候補の判断差分は、ENTRY採用判定ではなく、どの経路を追加・削除したかの監査として使用する。

- `X5_KEEP`
- `X5_REMOVED`
- `X6_ADDED`
- `BOTH_REJECT`

各群についてscenario分布、MFE/MAE、cost超過率、早期逆行、NoProgress、givebackを出す。  
PnL比較はE1_X5 EXITを用いた診断値と、最終的なENTRY×EXIT組合せ値を明確に分離する。
## 10. 期間内の内部検証

7/21～31は最終的にすべて設計へ使うため、以下は未使用OOSではない。目的は過学習と特定日依存の検出である。

### 10.0 暫定実行と正式実行の境界

7/30 PM seal後はF1～F4を予備実行できるが、これは`PROVISIONAL_NOT_FOR_SELECTION`である。7/31 PM後の正式実行では、最終Source Manifest、最終`analysis_mask_id`、同じprecommit済み構築手順を使い、F1～F5をすべて新規計算する。

次は禁止する。

- 暫定F1～F4を確定値として保持し、F5だけを追加して5fold結果を作ること
- 暫定foldで選ばれた閾値やfamilyをfinal runへ直接コピーすること
- 暫定成績の悪いfoldを除外、品質再分類、window短縮で救済すること
- 7/31を見た後にF1～F4の構築期間へ7/31由来の統計量を流入させること

暫定F1～F4は、実行経路、schema、fold境界、集計、監査列が正しく機能するかを早期に検出するために使う。候補の合否・順位・凍結判断には、最終runのF1～F5だけを使う。

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
- 日別取引数とPnL・PF・STOP率
- ENTRY burst、同一銘柄再ENTRY、30/60/120秒以内STOP
- session内取引順ごとの限界PnL
- `X5_KEEP / X5_REMOVED / X6_ADDED / BOTH_REJECT`

サブグループ`n < 30`は参考表示とし、単独で合否判定や新ルールの根拠にしない。

## 11. ENTRY候補のEXIT設計進行条件

ENTRY候補の段階では経済採用Gateを適用しない。以下はEXIT設計へ進めるための技術・証拠量Gateである。

| Gate | 進行条件 |
|---|---|
| Source | 重大な未解決データ不整合なし |
| Leakage | ENTRY featureの`asof_time <= decision_time`、未来情報混入0 |
| Determinism | ENTRY decision ledgerと経路台帳の二重再生SHA一致 |
| Safety | `submit/cancel/live = 0/0/0` |
| ENTRY precommit | `P1_ENTRY_PRECOMMIT`時刻・SHAが候補出力前に存在 |
| Fold completeness | F1～F5で同一手順を再現可能 |
| Reachability | ENTRY候補が事前登録supportを満たす |
| Day support | ENTRYが複数日に発生し、単一日だけの候補でない |
| Exit observability | EXIT設計に必要な価格・時間・出来高・板指標をENTRY後に再構成可能 |
| Economic possibility | cost控除後の正のMFEを持つepisodeが複数日に存在し、どのEXITでも利益化不能な候補でない |
| Complexity | 日付・銘柄固有条件なし |

ここではPF、PnL、最大DD、7/22除外PnLを理由にENTRY候補を採用・棄却しない。  
明らかに上昇余地がなく、理論上どの実行可能EXITでもコストを超えられない候補だけを`ENTRY_NO_EXITABLE_EDGE`として停止できる。

全ENTRY候補がreachabilityまたはobservability不足なら`E1_X6_INSUFFICIENT_EVIDENCE`とする。  
十分な経路がある候補は、E1_X5 EXITで赤字でもEXIT設計へ進める。

## 12. Phase 2：候補別EXITシナリオ設計

### 12.1 採用単位

最終採否単位は次とする。

```text
strategy_pair_id =
    entry_candidate_id
    ×
    exit_candidate_id
```

ENTRY単体、EXIT単体、凍結E1_X5 EXITによる仮結果を最終採用しない。

### 12.2 共通EXIT状態機械

各ENTRY候補に対するEXITは、以下の状態機械を基本とする。

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

どの状態からでも、異常価格・source stale・session終了時は安全EXITへ進める。

#### `OPEN_INIT`

ENTRY直後の誤判定と執行直後ノイズを扱う。  
使用候補指標:

- entry_priceからのMAE
- reclaim level / micro-highの維持
- spread急拡大
- bid支持
- 売り約定加速
- elapsed time

#### `STRUCTURE_HOLD`

S2の正常retestとS3のfalse breakoutを分離する。  
使用候補指標:

- reclaim level下抜け幅と継続時間
- pullback-lowまでの距離
- level再奪回
- bid支持回復
- volume persistence
- uptick回復

#### `PROGRESS_CHECK`

S1/S6とS4を分離する。  
使用候補指標:

- cost超過の有無
- MFE
- 新高値更新
- 最後のprogressからの時間
- price update速度
- volume維持
- entry impulseに対する活動比率

#### `PROFIT_PROTECTION`

S5の利益吐き出しを抑える。  
使用候補指標:

- 現時点MFE
- MFEからのgiveback
- break-even位置
- reclaim level
- bid悪化
- downtick加速
- spread拡大

#### `TREND_MANAGEMENT`

継続上昇を固定TARGETだけで早く切らず、崩れを検知する。  
使用候補指標:

- 高値更新間隔
- ATRまたは構造的trailing level
- volume persistence
- uptick優勢
- board支持
- 最大保有時間
- session残時間

### 12.3 EXIT candidate family

各ENTRY候補について、最大3つのEXIT scenarioを`P2_EXIT_PRECOMMIT`へ登録する。

1. `X_STRUCTURAL`  
   reclaim levelとpullback構造の破壊を重視する。正常retestを許容し、構造破壊で早く切る。
2. `X_CONTINUATION`  
   ENTRY後早期のprogressを期待する。NoProgressとfalse breakoutを早めに終了する。
3. `X_HYBRID`  
   初期は構造を許容し、cost超過後はMFE givebackとflow悪化で利益を保護する。

全ENTRY候補へ同じ固定秒・固定bpsを機械的に当てるのではなく、構築期間の経路分布から同じ生成手順で閾値を決める。  
ただし、候補ごとに使用できる指標、方向、分位点、候補数は`P2_EXIT_PRECOMMIT`で固定する。

### 12.4 EXIT閾値生成

- 数値閾値は各foldの構築期間だけで決める。
- 候補gridは事前登録した分位点またはATR正規化値に限定する。
- 確認日のPnLを見て閾値を調整しない。
- STOP、no-progress、break-even、trailing、max-holdを一度に無制限探索しない。
- まず各要素を単独で全episodeへ適用し、scenario別の誤退出・損失回避を診断する。
- 最終組合せは最大3要素までとする。
- 同じ役割の条件を重複して積み上げない。
- E1_X5 EXITはbenchmarkとして保存するが、候補別EXITが成立しない場合の自動採用先にしない。

### 12.5 Joint Rolling-origin

各foldで次を行う。

```text
構築期間:
ENTRY閾値fit
→ ENTRY後経路作成
→ scenario分布診断
→ EXIT候補と閾値fit

確認日:
ENTRY仕様変更禁止
EXIT仕様変更禁止
→ 全eventを最初から再生
→ CAP5を含むjoint portfolio評価
```

ENTRY ledgerを後付け削除し、既存tradeへEXITだけ当てる簡易評価を最終判定に使わない。  
ENTRY、保有、CAP、EXIT、後続ENTRYの変化を全event replayで再現する。

### 12.6 ENTRY×EXIT組合せの最終合格条件

以下は`strategy_pair_id`ごとに適用する。

| Gate | 合格条件 |
|---|---|
| Source / Leakage / Determinism / Safety | 全てPASS |
| Precommit | ENTRY・EXITの双方が候補別経済出力前に固定 |
| Trade support | `CORE_VALID`全期間と7/22除外の双方でcompleted trades `n >= 30` |
| `ALL_USABLE` | 5bps控除後PnL > 0、PF >= 1.10 |
| `CORE_VALID` | 5bps控除後PnL > 0、PF >= 1.10 |
| 7/22除外 | PnL > 0、PF > 1.00 |
| Rolling-origin | 5fold中3fold以上で確認日PnL > 0、中央値PnL > 0 |
| 日依存 | `FIXED_SPEC_DAY_DELETION`全ケースで残存期間PnL >= 0 |
| 集中 | top 1取引・top 1銘柄除外後PnL > 0 |
| BASE比較 | 凍結E1_X5よりPF、STOP損失、最大DDが改善 |
| Scenario整合 | S3/S4損失を抑え、S1/S2/S6の利益機会を過度に失わない |
| EXIT妥当性 | EXIT理由と使用指標がRuntime時点で再現可能 |
| 複雑度 | ENTRY＋EXIT全体が説明可能な1本 |

### 12.7 合格候補がない場合

ENTRY候補だけが良好でも、対応するEXITを含むjoint gateを通過しなければ採用しない。

```text
E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR
```

で終了する。

EXITの証拠量が不足している場合は、

```text
E1_X6_INSUFFICIENT_EXIT_EVIDENCE
```

とする。

条件を緩和してENTRY単体候補やE1_X5 EXITとの暫定組合せをForwardへ出さない。
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
- `E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR`
- `E1_X6_INSUFFICIENT_EXIT_EVIDENCE`
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

先行実行を行った場合も成果物数を増やさない。最終3成果物には、`P1_STUDY_PRECOMMIT`の時刻・SHA、provisional run ID、final run ID、暫定結果のstatus、無効化理由を格納し、暫定値と正式値を別field・別sheetで識別する。暫定値を正式fieldへ上書きまたは昇格しない。

### 15.1 `report.md`

- 結論とverdict
- 現在地と実行済みPhase
- precommit、provisional run、final runの識別
- E1_X5比較BASE
- E1_X6候補
- AM/PM・日別・品質区分別結果
- Rolling-origin、LODO、7/22除外
- ENTRY・EXITの主要改善点
- 未解決事項と次工程

### 15.2 `report.json`

- plan version
- run ID
- execution stage、precommit SHA、provisional/final run対応
- source/config/code/schema SHA
- 全metrics
- candidate registryと採否理由
- test結果
- safety counters
- final verdict

### 15.3 `audit.xlsx`

最低限、次のシートへ統合する。

- `Index`
- `RunIndex`
- `Precommit`
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
- `NoiseAudit`
- `Parity`
- `Tests`
- `Safety`
- `ChangeLog`

Excelの1シート上限を超える明細は削除・集約で隠さず、同一workbook内で`Dataset_001`、`Dataset_002`、`Labels_001`のように決定的に分割する。`Index`へ各分割sheetのrow範囲、件数、schema、SHAを記録する。

`report.json`の集計値と`audit.xlsx`の台帳再集計は完全一致させる。不一致時は`report.md`へ都合のよい一方を掲載せず、verdictを保留して原因を解消する。個別CSV、候補ごとのExcel、同内容の重複レポートは生成しない。

## 16. 実行順序と停止条件

| 順序 | 工程 | 完了条件 | 停止条件 |
|---:|---|---|---|
| 1 | `P0` 7/30 Capture監査 | seal、window、source品質を証跡確認 | 未seal・破損時はF4を開始しない |
| 2 | `P0` 暫定準備 | 7/21～30暫定inventory、Parity再利用判断、既存日の決定性、生成経路確認 | Parity drift・非決定性なら候補実行を停止 |
| 3 | `P1` Study Precommit | 最初のE1_X6候補別経済出力より前の時刻・SHA固定 | 先行閲覧済みなら`E1_X6_PRECOMMIT_CONTAMINATED` |
| 4 | `P2` 暫定F1～F4（任意） | 全出力が`PROVISIONAL_NOT_FOR_SELECTION` | 不具合時は影響runを無効化。採否へ進まない |
| 5 | 7/31 Capture・最終Manifest | PM seal後、全9日のSource Manifestと`analysis_mask_id`確定 | 必須foldを再構成不能なら`E1_X6_INSUFFICIENT_EVIDENCE` |
| 6 | `F0` 最終Gate 0・全9日再実行 | 新規final runでParity・決定性・BASE確定。F1～F4暫定値流用0 | `E1_X6_SOURCE_BLOCKED` |
| 7 | canonical dataset作成 | 全50銘柄、labels、quality確定 | leakage・時刻不整合 |
| 8 | E1_X5失敗構造解析 | Winner/STOP/no_progress/MISSED比較 | 再現不能 |
| 9 | ENTRY候補生成・経路診断 | ENTRY support、scenario台帳、EXIT observability確定 | `ENTRY_UNREACHABLE`または証拠不足 |
| 10 | 候補別EXIT設計・joint検証 | ENTRY×EXITでF1～F5、LODO、7/22除外、全joint gate完了 | `E1_X6_NO_ROBUST_ENTRY_EXIT_PAIR` |
| 11 | E1_X6凍結 | code/config/schema/SHA固定 | 未解決不一致 |
| 12 | 凍結後Forward | 5・10・20有効営業日 | 仕様変更時は0日へリセット |

## 17. 直近の実行事項

1. E1_X5、production YAML、Live注文経路を変更せず、7/30 Captureのseal、window、source品質を証跡で確認する。
2. 7/21～30の暫定Source Inventoryを作り、各windowを技術監査項目だけで暫定分類する。
3. 2026-07-27 PMの既存Parity合格証跡とSHAを確認する。影響変更または証跡不足がある場合だけ再実行する。
4. 既存日で決定性を確認し、暫定E1_X5 BASEとcanonical datasetの生成経路を検証する。
5. E1_X6候補別の経済出力を開く前に、§7.0の`P1_STUDY_PRECOMMIT`を固定する。
6. 時間があればF1～F4を予備実行し、すべて`PROVISIONAL_NOT_FOR_SELECTION`として扱う。F4は7/30 PM seal確認後だけ実行する。
7. 7/31はE1_X5を変更せず、raw captureとPaper運用を完了する。
8. 7/31 PM seal後、E1_X6候補の経済出力を見ずに全9営業日の最終Source Manifest、quality classification、`analysis_mask_id`を確定する。
9. 新しいfinal run IDでGate 0、全9日BASE、dataset、labels、F1～F5を最初から再実行する。暫定F1～F4へF5だけを追加しない。
10. final runだけを使ってENTRY候補の経路診断を行い、候補別EXITをprecommitした後、ENTRY×EXITの組み合わせでLODO、7/22除外、最終採否を行う。

7/30先行実行の目的は待ち時間短縮と経路不具合の早期検出であり、7/31を含む証拠量を前倒しで作ることではない。暫定結果を見て本書の合格条件、source規則、候補探索ルールを都合よく変更しない。

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
| 先行実行後は必須 | execution stage、precommit SHA、provisional run IDとstatus | 暫定値を正式値として渡さない |

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
- execution_stage: [P0 / P1 / P2 / F0以降]
- study_precommit: [NOT_CREATED / SHAと時刻]
- provisional_runs: [NONE / run IDとVALID・INVALIDATED]
- final_run: [NOT_STARTED / run ID]
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

7/30時点のF1～F4は`PROVISIONAL_NOT_FOR_SELECTION`です。
7/31 PM後は最終Manifestと新しいfinal run IDで、
全9日・F1～F5を最初から再実行してください。
暫定F1～F4へF5だけを追加した集計は禁止です。

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
| 1.2 | 2026-07-30 | 7/30先行着手を許可。`P0/P1/P2/F0`境界、候補経済出力前のStudy Precommit、暫定F1～F4の非採用、7/31 PM後の全9日・F1～F5完全再実行を追加。暫定結果による探索・品質・合格規約変更を禁止し、実装不具合時の無効化、final run integrity、引き継ぎ項目を明文化。funnel保存則、取引密度・短時間STOP・再ENTRY・取引順の限界PnL、`X5_KEEP/X5_REMOVED/X6_ADDED/BOTH_REJECT`によるノイズ削減監査を追加し、単純な日次取引上限を既定解にしない方針を固定。 |
| 1.3 | 2026-08-03 | ENTRY候補単体の経済採否を廃止。ENTRY後の経路を7シナリオへ分類し、候補別EXIT指標・状態機械・二段階Precommitを追加。最終採否単位をENTRY×EXIT pairへ変更し、ENTRY_ONLY凍結を廃止。 |

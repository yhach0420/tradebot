# Phase571A — Entry Wait Definition Audit

**Verdict:** `phase571a_entry_wait_definition_audit_done`
**Breakdown trades audited:** 3297
**Samples:** 40 (universe_wait×20 + board_wait×20)

## Key finding

Phase571 `universe_wait` **does not mean Universe未登録**.
It measures `[policy screening_end → first per-symbol entry_symbol_eval)`.
This includes runner late start (e.g. 12:33 policy → 12:56 actual session) and replay gaps.

## Mandatory answers

1. universe_wait meaning: screening_end(09:03/12:33)から当該銘柄の初回entry_symbol_evalまで。Universe未登録ではなく「銘柄別初回評価前」+ runner実開始遅延を含む
2. board_wait meaning: push/momentum/volume通過後、entry_score_v2/shape guard reject の occupancy。Board未評価ではなく評価済みreject
3. push_wait meaning: data_stale_price/board reject の occupancy。初回PUSH前区間はuniverse_wait側
4. universe/push overlap: False — occupancy model は排他的。ただしラベル意味は混同しうる
5. universe registration delay: False — 代表20件中 session_actual_start > screening の件数=0（runner遅延）
6. websocket registration delay: False — WS登録=live_session_config.generated_at。policy screeningより遅い日あり
7. first push delay: True — 銘柄別fresh PUSHは初回evalと同時または直後が多い。stale reject が push_wait
8. entry eval delay: True — events_fallback 40/40 samples — replayはacceptedのみで巨大universe_wait
9. 5471 actual wait: **push_wait** @ 2026-06-25T11:14:41+09:00
10. Phase571 classification correct: **partial** — occupancy数学は正しいがラベルがRuntime pipelineと不一致
11. rename labels: **True** — {'universe_wait': 'pre_first_eval_wait', 'push_wait': 'push_stale_wait', 'board_wait': 'board_guard_wait'}
12. runtime anomaly: **False** — runner実開始がpolicyより遅い（例 12:33→12:56）は運用遅延でありgate異常ではない
13. runtime fix needed: **False**
14. next phase: **phase572_entry_wait_shadow_monitor**

## 5471 target trade

- entry: 2026-06-25T11:14:41+09:00
- session: C:\Users\yhach\Documents\tradebotfile\kabu_native\results\small_paper\20260625\live_session_080340
- Phase571 waits: universe=1840.0 push=3968.0 board=148.0 cap=0.0


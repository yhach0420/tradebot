# E1_X5 Forward Shadow — Paper Default ON

## Summary

Paper Trade では `E1_X5_FORWARD_SHADOW` を設定しなくても E1_X5 Forward Shadow が有効になる。

| 文脈 | 未設定 / 空 | `1` / `true` / `yes` / `on` | `0` / `false` / `no` / `off` | 不正値 |
|------|-------------|-----------------------------|------------------------------|--------|
| Paper (`KABU_PAPER_RUNTIME=1`) | ON (`PAPER_DEFAULT_ON`) | ON (`PAPER_ENV_ON`) | OFF (`PAPER_ENV_OFF`) | OFF + 警告 (`INVALID_ENV_FORCED_OFF`) |
| 非 Paper / Live | OFF (`NON_PAPER_FORCED_OFF`) | OFF（強制） | OFF | OFF |

## 運用ポイント

- Paper では E1_X5 Shadow は **デフォルト ON**
- 停止方法は `E1_X5_FORWARD_SHADOW=0`
- Live では環境変数に関係なく **強制 OFF**
- PBv2 とは **独立 CAP5**
- **注文 API を使用しない**（observe-only）

## 実装

- 判定集約: `small_paper.e1_x5_forward_shadow.resolve_e1_x5_forward_shadow_enabled`
- Paper 判定: `small_paper.forward_observer_defaults.is_paper_runtime`（`KABU_PAPER_RUNTIME`）
- Live 強制 OFF: `is_live_or_real_order_context`

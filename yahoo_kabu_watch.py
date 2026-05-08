"""
Yahoo Finance（非公式API）で日本株を監視・スクリーニングするツール（発注機能なし）
=================================================================

このスクリプトは、Yahoo Finance の「非公式API」からデータを取得して、
WATCHリストに入れた複数銘柄を 1 秒ごとに監視し、条件に合う銘柄だけ表示します。

Issue #1 の要件（スクリーニング条件）:
- WATCHリストで複数銘柄指定（例: 7203.T）
- 前日比 +1%以上
- 当日高値の 98%以上
- 出来高あり（0より大きい）

追加（Discord通知）:
- 条件に一致した銘柄を `DISCORD_WEBHOOK_URL` の Discord Webhook に通知します。
- 「同じ銘柄を連続通知しない」ため、直前ループで条件一致していた銘柄は通知しません。

注意:
- 非公式APIなので、仕様変更・アクセス制限で動かなくなる可能性があります。
- 取引判断や損益については自己責任でお願いします（本ツールは発注しません）。

動作確認の目安:
  Python 3.10+（3.9でもたぶん動きます）
  pip install requests
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
import math
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

def _default_replay_configs_dicts() -> dict[str, dict[str, Any]]:
    """
    Replay比較用configの初期値（要件どおり）。
    キーはファイル名（configs/ 配下）。
    """
    return {
        "replay_default.json": {
            "name": "replay_default",
            "early_exit": False,
            "strict_afternoon": False,
            "topix_weak_block": False,
        },
        "replay_safe.json": {
            "name": "replay_safe_v1",
            "early_exit": True,
            "vwap_break_exit": True,
            "recent_5m_low_break_exit": True,
            "strict_afternoon": True,
            "topix_weak_block": True,
            "afternoon_strict": {
                "volume_spike_ratio_min": 2.0,
                "vwap_dist_pct_max": 1.0,
                "rebreak_mult": 1.0015,
            },
        },
        "replay_aggressive.json": {
            "name": "replay_aggressive",
            "early_exit": False,
            "strict_afternoon": False,
            "topix_weak_block": False,
            "afternoon_strict": {
                "volume_spike_ratio_min": 1.2,
                "vwap_dist_pct_max": 3.0,
                "rebreak_mult": 1.0005,
            },
        },
        "replay_balanced.json": {
            "name": "replay_balanced",
            "early_exit": True,
            "vwap_break_exit": True,
            "recent_5m_low_break_exit": False,
            "strict_afternoon": True,
            "topix_weak_block": False,
            "afternoon_strict": {
                "volume_spike_ratio_min": 1.5,
                "vwap_dist_pct_max": 1.8,
                "rebreak_mult": 1.0010,
            },
        },
        "replay_morning_only.json": {
            "name": "replay_morning_only",
            "early_exit": True,
            "vwap_break_exit": True,
            "recent_5m_low_break_exit": True,
            "strict_afternoon": True,
            "topix_weak_block": True,
            "disable_afternoon_entry": True,
        },
    }


def _ensure_replay_configs_exist() -> str:
    """
    要件:
    - configs/ フォルダを自動作成
    - 起動時に存在しない config は自動生成（replay実行時に呼ぶ）
    - config未指定時のデフォルトは configs/replay_default.json
    戻り値: デフォルトconfigの絶対パス（configs/replay_default.json）
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_dir = os.path.join(script_dir, "configs")
    os.makedirs(cfg_dir, exist_ok=True)
    defaults = _default_replay_configs_dicts()
    for fn, payload in defaults.items():
        p = os.path.join(cfg_dir, fn)
        if os.path.exists(p):
            continue
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[{now_str()}] configの自動生成に失敗: {p} ({type(e).__name__}: {e})")
    return os.path.join(cfg_dir, "replay_default.json")


def _load_replay_config(path: str) -> dict[str, Any]:
    """
    Replay戦略条件のconfig JSONを読み込みます。
    - 失敗したら {} を返す（CLIだけでも動くように）
    """
    p = str(path or "").strip()
    if not p:
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data["_path"] = p
            return data
        return {}
    except Exception as e:
        # now_str はこの時点では未定義の可能性があるため、単純にprintする
        print(f"Replay configの読み込みに失敗: {p} ({type(e).__name__}: {e})")
        return {}


def _apply_replay_config_to_flags(*, cfg: dict[str, Any]) -> dict[str, Any]:
    """
    config JSON の値を run_replay のフラグ/閾値へマッピングします。
    ここに寄せることで、戦略条件をコードのあちこちに散らさないようにします。
    """
    if not isinstance(cfg, dict):
        cfg = {}
    early_exit = bool(cfg.get("early_exit", False))
    strict_afternoon = bool(cfg.get("strict_afternoon", False))
    topix_weak_block = bool(cfg.get("topix_weak_block", True))
    vwap_break_exit = bool(cfg.get("vwap_break_exit", True))
    recent_low_break_exit = bool(cfg.get("recent_5m_low_break_exit", True))
    disable_afternoon_entry = bool(cfg.get("disable_afternoon_entry", False))

    # 後場厳格化の閾値（任意override）
    aft = cfg.get("afternoon_strict") if isinstance(cfg.get("afternoon_strict"), dict) else {}
    volx_min = float(aft.get("volume_spike_ratio_min", AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN)) if isinstance(aft, dict) else float(AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN)
    vwap_dist_max = float(aft.get("vwap_dist_pct_max", AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX)) if isinstance(aft, dict) else float(AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX)
    rebreak_mult = float(aft.get("rebreak_mult", AFTERNOON_ENTRY_STRICT_REBREAK_MULT)) if isinstance(aft, dict) else float(AFTERNOON_ENTRY_STRICT_REBREAK_MULT)

    return {
        "replay_config_path": str(cfg.get("_path") or ""),
        "replay_config_name": str(cfg.get("name") or ""),
        "replay_early_exit_before_stop": bool(early_exit),
        "replay_early_exit_vwap": bool(early_exit and vwap_break_exit),
        "replay_early_exit_recent_low": bool(early_exit and recent_low_break_exit),
        "replay_strict_afternoon_entry": bool(strict_afternoon),
        "replay_disable_afternoon_entry": bool(disable_afternoon_entry),
        "replay_afternoon_topix_weak_block": bool(topix_weak_block),
        "aft_volume_spike_ratio_min": float(volx_min),
        "aft_vwap_dist_pct_max": float(vwap_dist_max),
        "aft_rebreak_mult": float(rebreak_mult),
    }


def _signal_time_bucket_jst(dt: Optional[Any]) -> str:
    """
    Replayの集計/デバッグ用の時間帯バケット（JST）。
    - run_replay 内部で参照されるため、モジュールスコープに置いて未定義参照を防ぎます。
    """
    try:
        if dt is None:
            return "N/A"
        t = dt
        if isinstance(t, str):
            return "N/A"
        if not isinstance(t, datetime):
            return "N/A"
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        jst = t.astimezone(JST)
        hm = jst.hour * 60 + jst.minute
        if (9 * 60) <= hm < (9 * 60 + 30):
            return "前場寄り直後(09:00-09:30)"
        if (9 * 60 + 30) <= hm < (11 * 60 + 30):
            return "前場(09:30-11:30)"
        if (12 * 60 + 30) <= hm < (14 * 60):
            return "後場前半(12:30-14:00)"
        if (14 * 60) <= hm < (15 * 60 + 30):
            return "後場後半(14:00-15:30)"
        return "時間外"
    except Exception:
        return "UNKNOWN"

# requests は標準ライブラリではありません。無い場合は分かりやすく案内します。
try:
    import requests
except ImportError:  # pragma: no cover
    print("このツールは 'requests' が必要です。次を実行して入れてください:")
    print("  pip install requests")
    raise


# ----------------------------
# Yahoo Finance 非公式APIのURL
# ----------------------------
# 例:
# https://query1.finance.yahoo.com/v7/finance/quote?symbols=7203.T
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

# quote が弾かれる環境向けの代替エンドポイント（こちらの方が通ることがあります）
# 例:
# https://query1.finance.yahoo.com/v8/finance/chart/7203.T?interval=1m&range=1d
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# ----------------------------
# 過去データの仮想リプレイ（テスト用）
# ----------------------------
# 目的:
# - 相場時間外でも「過去の1分足」を1秒ごとに再生し、
#   いつもの判定ロジック（条件一致/条件外れ/候補価格変更）とDiscord通知を確認するための機能です。
#
# 重要:
# - TEST_REPLAY_MODE = False のときは、既存のリアルタイム監視（現在値の取得）を一切変えません。
# - コマンドラインで `python yahoo_kabu_watch.py --replay` を付けた時だけ True になります。
TEST_REPLAY_MODE = False

# ----------------------------
# 監視したい銘柄（ここを編集）
# ----------------------------
# 銘柄コードは「7203.T」の形式です。
# 例: トヨタ 7203.T / ソフトバンクG 9984.T
WATCH: list[str] = [
    "7203.T",
    "9984.T",
    "6232.T",
    "8053.T"
]

# ----------------------------
# スクリーニング条件（ここを編集）
# ----------------------------
MIN_CHANGE_PCT = 1.0          # 前日比（%）がこの値以上
MAX_CHANGE_PCT = 8.0          # 前日比（%）がこの値以上なら「急騰しすぎ」として除外（未満だけ通す）
MIN_RATIO_TO_DAY_HIGH = 0.98  # 現在値が当日高値の何%以上か（0.98 = 98%）
MIN_VOLUME = 300_000          # 最低出来高（この値以上だけ通す）

# ----------------------------
# エントリータイミング検知（追加条件）
# ----------------------------
# 目的:
# - 「強い銘柄っぽい」ではなく「エントリーに近い瞬間」だけ通知したい。
#
# 追加する必須条件:
# - 直近5分高値ブレイク（price > recent_5m_high）
# - 上昇傾向（price > price_5min_ago）
# - VWAP乖離（price が VWAP より 0.2%以上 上）
#
# 注意:
# - これらは 1分足系列が必要です（リアルタイムでは chart の 1m データを参照します）
VWAP_DISTANCE_PCT = 0.5  # (price - vwap) / vwap * 100 >= 0.5

# Entry候補（= entry）への接近条件:
# - 「ブレイクした！」だけで通知が出ると、entry候補から遠い場面でも通知が増えがちです。
# - そこで「entry候補の99.5%以上まで近づいたとき」だけ条件一致にします。
ENTRY_NEAR_RATIO = 0.996

# Entryの算出方法（新仕様）:
# - 以前: entry = 当日高値(day_high)
# - 変更: entry = 直近5分高値(recent_5m_high) * ENTRY_BREAKOUT_BUFFER
#
# ねらい:
# - 当日高値は「すでに遠い」ことが多く、強い上昇でも Entry未成立 になりやすい
# - 直近5分高値ベースにすると、ブレイクの“今この瞬間”に寄せやすい
ENTRY_BREAKOUT_BUFFER = 1.001  # 0.1%上抜け（例: 5000円 → 5005円）

# ----------------------------
# 出来高急増（5日平均出来高との比較）
# ----------------------------
MIN_VOLUME_SPIKE_RATIO = 2.0  # 現在出来高 >= 5日平均出来高 * この倍率

# 5日平均出来高は chart から計算します（毎秒取得は重いのでキャッシュ）。
VOL_AVG5_CACHE_TTL_SEC = 60 * 10  # 10分

# VWAP は日中に変わるので、比較的短めにキャッシュします
VWAP_CACHE_TTL_SEC = 60 * 5  # 5分

# 1分足系列（直近シグナル計算用）のキャッシュ:
# - 毎秒 chart を取りに行くと重いので、短いTTLでキャッシュします。
INTRADAY_SERIES_CACHE_TTL_SEC = 20

# ----------------------------
# 時価総額フィルタ
# ----------------------------
MIN_MARKET_CAP = 30_000_000_000     # 300億円以上
MAX_MARKET_CAP = 500_000_000_000   # 5000億円以下

# ----------------------------
# Discord 通知（Issue #1 追加要件）
# ----------------------------
# - Webhook URL は環境変数 `DISCORD_WEBHOOK_URL` から読みます。
# - 条件一致した銘柄のうち「前回ループでは候補に入っていなかった銘柄」だけ通知します
#   （= 同じ銘柄を連続通知しないための仕組みです）。
# - 初心者向けに、エントリー/損切り/利確候補は“分かりやすい簡易ルール”で計算します。
#
# 簡易ルール（必要なら調整してください）:
# - エントリー候補: 当日高値（break想定で「高値更新」を狙うイメージ）
# - 損切り候補: エントリーの -2% 下
# - 利確候補: エントリーの +4% 上（リスクリワードを 1:2 くらいの形に）
STOP_LOSS_PCT_FROM_ENTRY = 0.02
TAKE_PROFIT_PCT_FROM_ENTRY = 0.04

# ----------------------------
# 候補価格の大幅変更通知（Discord再通知）
# ----------------------------
# 条件一致中の銘柄でも、エントリー/損切り/利確の候補価格が大きく動いたら再通知します。
# 判定は「%」と「円」の両方で見ます（どちらかに引っかかれば通知）。
LEVEL_CHANGE_PCT = 1.0  # 1%以上変わったら通知
LEVEL_CHANGE_YEN = 10   # 10円以上変わったら通知

# ----------------------------
# 条件外れ通知の安定化（連続不一致で確定）
# ----------------------------
# 目的:
# - 一瞬だけ条件を割った（特にVWAP付近のチョン）で「条件外れ通知」が出てしまうのを防ぎたい。
#
# 仕様:
# - 2〜3分連続で条件不一致になった場合のみ条件外れ通知する
EXIT_CONFIRM_COUNT = 3

# ----------------------------
# Entry上抜け（breakout）状態のリセット条件
# ----------------------------
# 目的:
# - breakout_state が「昔のentry突破」を引きずると、新しいentry候補で再度ブレイクしても
#   🚀通知が出なくなります。
#
# 仕様:
# - entry が前回「突破済みになった時のentry」から 0.3%以上変わったら breakout_state を False に戻す
BREAKOUT_ENTRY_RESET_PCT = 0.3

# ----------------------------
# 25日移動平均（MA25）取得
# ----------------------------
# 25日移動平均は「日足の終値」を25本ぶん集めて平均します。
# 毎秒APIを叩くと重くなるので、一定時間キャッシュして使い回します。
MA25_CACHE_TTL_SEC = 60 * 10  # 10分に1回だけ取り直す（必要なら調整）


def _browser_headers(referer: Optional[str] = None) -> dict[str, str]:
    """
    Yahoo側に「普通のブラウザ」っぽく見せるためのヘッダ。
    401/403 を避ける目的で Referer/Accept も入れます。
    """
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    return h


def warmup_session(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> None:
    """
    セッションにクッキー等を載せるための「肩慣らし」アクセス。
    環境によっては、先にWebページを見た扱いにすると API が通ることがあります。
    """
    # 公式ではないので、どのURLが効くかは環境次第です。失敗しても監視は続けます。
    url = f"https://finance.yahoo.com/quote/{symbol}"
    try:
        session.get(url, headers=_browser_headers(referer="https://finance.yahoo.com/"), timeout=timeout_sec)
    except Exception:
        pass


def now_str() -> str:
    """ログ表示用の現在時刻（ローカル時刻・見やすい形式）"""
    return datetime.now().strftime("%H:%M:%S")


@dataclass(frozen=True)
class Quote:
    """取得した株価の最小セット（初心者向けに構造をはっきりさせる）"""

    symbol: str
    price: float
    currency: str
    previous_close: Optional[float]  # 前日終値（previousClose）
    change_percent: Optional[float]  # 前日比（%）
    day_high: Optional[float]        # 当日高値
    # 当日安値（朝スクリーニング用に追加）
    # - Yahooのレスポンスによっては欠けることがあるため Optional にします。
    day_low: Optional[float]
    volume: Optional[float]          # 出来高（整数が多いが float で受ける）
    market_time_utc: Optional[datetime]
    market_cap: Optional[float]  # 時価総額（Yahoo Financeの値）


@dataclass(frozen=True)
class ReplayBar:
    """
    リプレイで使う「1分足1本」の最小セットです。
    - timestamp_utc: ローソク足の時刻（UTC）
    - open/high/low/close: OHLC
    - volume: その1分の出来高（バー出来高）
    """

    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class IntradaySignals:
    """
    「エントリータイミング検知」に必要な、直近の1分足由来シグナル。

    - recent_5m_high:
        直近5分の高値（※現在の足は含めない想定）
    - price_5min_ago:
        5分前の価格（1分足 close を基準）
    - vwap:
        日中VWAP（概算）
    - vwap_distance_pct:
        VWAP乖離率(%) = (price - vwap) / vwap * 100
    - vol_3m_gt_prev_3m:
        直近3分出来高合計 > その前の3分出来高合計（加点用）
    """

    recent_5m_high: Optional[float]
    price_5min_ago: Optional[float]
    vwap: Optional[float]
    vwap_distance_pct: Optional[float]
    vol_3m_gt_prev_3m: Optional[bool]


@dataclass
class ReplaySignalEval:
    """
    Replayモード専用:
    🚀 Entry上抜け（breakout）が出た瞬間を「signal」として記録し、
    その後の価格推移から期待値（伸びたか/耐えたか）を簡易検証します。

    注意:
    - 通常監視モードには影響させない（Replayモード内だけで使う）
    - Discord通知は不要（ターミナル表示のみ）
    """

    symbol: str

    # signal時点の情報
    signal_time_utc: datetime
    signal_price: float
    entry_price: float
    stop_price: float
    take_price: float

    # signal後の推移（signal時点から更新していく）
    max_price_after: float
    min_price_after: float
    last_price_after: float

    # 到達判定（固定take/stopは参考情報として残す）
    take_hit: bool = False
    stop_hit: bool = False

    # 新しい利確ロジック:
    # 1) signal_price から +1.0% 到達したら partial_take_hit=True
    partial_take_hit: bool = False
    partial_take_time_utc: Optional[datetime] = None

    # 2) partial_take_hit 後のトレーリング決済
    trailing_exit_price: Optional[float] = None
    trailing_exit_time_utc: Optional[datetime] = None
    trailing_exit_reason: Optional[str] = None  # "VWAP" / "recent_5m_low"

    # 最終損益（entry基準）
    final_profit_pct: Optional[float] = None

    resolved: bool = False
    result: str = "HOLD"  # WIN / LOSE / HOLD

    # ポジション種別（期待値検証の見分け用）
    # - "BASE": 通常のEntry上抜け（最初のポジション）
    # - "ADD1": 追加ポジション1
    # - "ADD2": 追加ポジション2
    position_kind: str = "BASE"

    # 決済スタイル:
    # - "trailing": 既存の「partial_take(+1%) → トレーリング決済」ロジック
    # - "fixed":    take_price 到達で WIN、stop_price 到達で LOSE（追加ポジション用）
    exit_style: str = "trailing"

    # =========================
    # 期待値検証の「除外フラグ」（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - signal は検出されたとしても「期待値検証の集計対象に含めない」ことがあります。
    # - 例えば「同一銘柄は1日に1回まで採用」という制限モードでは、2回目以降を除外します。
    excluded_from_eval: bool = False
    excluded_reason: str = ""

    # =========================
    # デバッグ/整合性チェック用（追加仕様）
    # =========================
    signal_id: str = ""

    def update_with_price(
        self,
        *,
        time_utc: datetime,
        price: float,
        vwap: Optional[float],
        recent_5m_low: Optional[float],
        early_exit_before_partial_take: bool = False,
        early_exit_vwap: bool = True,
        early_exit_recent_low: bool = True,
    ) -> None:
        """
        価格更新（新しい利確ロジック版）

        - max/min を更新
        - （任意）Entry直後からの早期撤退:
            - price < VWAP で即EXIT（VWAP_BREAK_EARLY）
            - price < recent_5m_low で即EXIT（RECENT_5M_LOW_BREAK_EARLY）
          ※ STOPは最後の保険として扱うため、早期撤退が有効な場合は STOP より先に評価します。
        - 固定take_price は参考として take_hit だけ記録（結果判定には使わない）
        - 新しい利確:
            1) signal_price から +1% 到達で partial_take_hit
            2) partial_take_hit 後は (price < VWAP) または (price < recent_5m_low) で EXIT
        """
        p = float(price)
        self.last_price_after = p
        if p > self.max_price_after:
            self.max_price_after = p
        if p < self.min_price_after:
            self.min_price_after = p

        if self.resolved:
            return

        # -----------------------------
        # 早期撤退（ユーザー要望）
        # - STOP は最終防衛ラインにする
        # - partial_take(+1%) 前でも exit 条件を優先できるようにする
        # -----------------------------
        if bool(early_exit_before_partial_take) and str(self.exit_style) != "fixed":
            try:
                if bool(early_exit_vwap) and isinstance(vwap, (int, float)) and p < float(vwap):
                    self.trailing_exit_price = p
                    self.trailing_exit_time_utc = time_utc
                    self.trailing_exit_reason = "VWAP_EARLY"
                    self.resolved = True
                    setattr(self, "exit_reason", "VWAP_BREAK_EARLY")
                    setattr(self, "exit_price", float(p))
                    setattr(self, "exit_time_utc", time_utc)
                elif bool(early_exit_recent_low) and isinstance(recent_5m_low, (int, float)) and p < float(recent_5m_low):
                    self.trailing_exit_price = p
                    self.trailing_exit_time_utc = time_utc
                    self.trailing_exit_reason = "recent_5m_low_early"
                    self.resolved = True
                    setattr(self, "exit_reason", "RECENT_5M_LOW_BREAK_EARLY")
                    setattr(self, "exit_price", float(p))
                    setattr(self, "exit_time_utc", time_utc)

                if self.resolved:
                    if self.entry_price > 0:
                        self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                    # 早期撤退は損切りが主目的だが、利益で終わった場合は WIN 扱いにする
                    if self.final_profit_pct is not None and float(self.final_profit_pct) > 0:
                        self.result = "WIN"
                    else:
                        self.result = "LOSE"
                    return
            except Exception:
                # 早期撤退の評価で落ちても、通常ロジックへフォールバック
                pass

        # -----------------------------
        # 固定決済（追加ポジション向け）
        # -----------------------------
        # 初心者向けポイント:
        # - 追加ポジションは「浅い利確」を明確に検証したいので、
        #   take_price 到達でWIN、stop_price 到達でLOSEとして解決します。
        if str(self.exit_style) == "fixed":
            # stop は最優先（仕様）
            if p <= float(self.stop_price):
                self.stop_hit = True
                self.resolved = True
                self.result = "LOSE"
                setattr(self, "exit_reason", "STOP")
                setattr(self, "exit_price", float(p))
                setattr(self, "exit_time_utc", time_utc)
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                return

            if p >= float(self.take_price):
                self.take_hit = True
                self.resolved = True
                self.result = "WIN"
                setattr(self, "exit_reason", "TAKE")
                setattr(self, "exit_price", float(p))
                setattr(self, "exit_time_utc", time_utc)
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                return

        # STOP（最終防衛ライン）
        if p <= float(self.stop_price):
            self.stop_hit = True
            self.resolved = True
            self.result = "LOSE"
            setattr(self, "exit_reason", "STOP")
            setattr(self, "exit_price", float(p))
            setattr(self, "exit_time_utc", time_utc)
            if self.entry_price > 0:
                self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
            return

        # partial take（signal_price +1% 到達）
        if not self.partial_take_hit:
            target = float(self.signal_price) * 1.01
            if p >= target:
                self.partial_take_hit = True
                self.partial_take_time_utc = time_utc
                # partial_take_hit=True なら最低 HOLD 以上（仕様）
                self.result = "HOLD"
            return

        # partial_take_hit 後のトレーリング決済
        if self.partial_take_hit:
            if isinstance(vwap, (int, float)) and p < float(vwap):
                self.trailing_exit_price = p
                self.trailing_exit_time_utc = time_utc
                self.trailing_exit_reason = "VWAP"
                self.resolved = True
                setattr(self, "exit_reason", "VWAP_BREAK")
                setattr(self, "exit_price", float(p))
                setattr(self, "exit_time_utc", time_utc)
            elif isinstance(recent_5m_low, (int, float)) and p < float(recent_5m_low):
                self.trailing_exit_price = p
                self.trailing_exit_time_utc = time_utc
                self.trailing_exit_reason = "recent_5m_low"
                self.resolved = True
                setattr(self, "exit_reason", "RECENT_5M_LOW_BREAK")
                setattr(self, "exit_price", float(p))
                setattr(self, "exit_time_utc", time_utc)

            if self.resolved:
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                # partial_take_hit=True なら最低HOLD。利益が残っていればWIN。
                if self.final_profit_pct is not None and self.final_profit_pct > 0:
                    self.result = "WIN"
                else:
                    self.result = "HOLD"
                return

    def max_profit_pct(self) -> float:
        """
        最大利益率（%）:
        - entry を基準にします（エントリー基準の期待値を見るため）
        """
        if self.entry_price <= 0:
            return 0.0
        return ((self.max_price_after - self.entry_price) / self.entry_price) * 100.0

    def max_drawdown_pct(self) -> float:
        """
        最大ドローダウン（%）:
        - entry を基準に、最悪（min）を見ます（負の値になります）。
        """
        if self.entry_price <= 0:
            return 0.0
        return ((self.min_price_after - self.entry_price) / self.entry_price) * 100.0


@dataclass(frozen=True)
class MorningScreenResult:
    """
    朝スクリーニングの集計結果（1銘柄ぶん）。

    初心者向けポイント:
    - まず「必要な値をまとめた箱」を作っておくと、後の整形/Discord出力が楽になります。
    - Quote（現在値など）だけでは足りない指標（VWAP/MA25など）をここで追加保持します。
    """

    symbol: str
    score: int
    quote: Quote
    vwap: Optional[float]
    ma25: Optional[float]
    avg_vol5: Optional[float]
    vol_spike_ratio: Optional[float]
    day_range_pct: Optional[float]
    reasons: list[str]


def calc_entry_from_signals(sig: Optional[IntradaySignals]) -> Optional[float]:
    """
    Entry候補価格を「直近5分高値ベース」で計算します（新仕様）。

    entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER

    - recent_5m_high が取れない場合は None（= entry も作れない）
    """
    if sig is None:
        return None
    if sig.recent_5m_high is None:
        return None
    base = float(sig.recent_5m_high)
    if base <= 0:
        return None
    return base * float(ENTRY_BREAKOUT_BUFFER)


# 最新の「直近シグナル」を、通知/再計算のために保持します。
# 重要:
# - Quote には recent_5m_high 等の情報が入っていないため、
#   entry の計算には「そのループで計算した IntradaySignals」が必要です。
# - そこで、各ループで計算した IntradaySignals を symbol ごとにここへ保存し、
#   calculate_entry(q) はそれを参照して entry を統一します。
_LATEST_INTRADAY_SIGNALS: dict[str, IntradaySignals] = {}

# =========================
# 地合いフィルタ（追加仕様・Replay中心）
# =========================
# 初心者向けポイント:
# - “弱い地合いの日”は、ブレイク順張りが負けやすいことがあります。
# - そこで「指数/全体の強さ/直近の負け具合」から ENTRY を止めます。
MARKET_RISING_RATIO_MIN = 0.40          # 上昇銘柄割合がこれ未満なら弱い
MARKET_ENTRY_FAIL_RATE_30M_MAX = 0.60   # 直近30分のENTRY失敗率がこれ超なら弱い
# high_update_low の閾値（緩和）
# - 以前は厳しすぎて常に弱判定になりやすいため、下げます。
MARKET_HIGH_UPDATE_RATIO_MIN = 0.07     # 高値付近（≒高値更新率の代用）がこれ未満なら弱い
MARKET_VWAP_BELOW_RATIO_MAX = 0.60      # VWAP下銘柄がこれより多いなら弱い（後場追加条件等で使用）

AFTERNOON_FILTER_START_MIN = 12 * 60 + 30  # 12:30
AFTERNOON_FILTER_END_MIN = 14 * 60         # 14:00
AFTERNOON_BREAK_MORNING_HIGH_RATIO_MIN = 0.10  # 前場高値更新がこれ未満なら後場弱い

# WEAK地合い時の追加厳格化（Entry）
WEAK_ENTRY_VWAP_DIST_PCT_MAX = 1.5      # VWAP乖離率の上限（高値掴み抑制）
WEAK_VOLUME_SPIKE_RATIO_MIN = 1.5       # 出来高倍率（5日平均比）の下限
WEAK_REBREAK_MULT = 1.002               # 直近5分高値更新をより厳格化（例: *1.002）

# =========================
# 後場Entry 厳格化（Replay比較用）
# =========================
# ユーザー要望:
# - 後場（12:30以降）は「全面禁止」ではなく条件を強化して大損を減らす
AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN = 2.0   # 出来高倍率を強化（例: 2.0x以上）
AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX = 1.0        # VWAP乖離上限を強化（高値掴み抑制）
AFTERNOON_ENTRY_STRICT_REBREAK_MULT = 1.0015          # 5分高値更新（より強い上抜け）必須

# CRASH地合い（ENTRY禁止）
# - 目的: 「市場全体で止める」よりも「危険signalだけ除外」へ寄せる
# - 仕様変更（ユーザー要望）:
#   - TOPIX <= -1.5% のみ CRASH
#   - -0.5%〜-1.5% は WEAK 扱い（CRASHではない）
CRASH_TOPIX_CHG_PCT_MAX = -1.5          # TOPIX(代用ETF)がこれ以下ならCRASH扱い
WEAK_TOPIX_CHG_PCT_MAX = -0.5           # TOPIXがこれ以下ならWEAK理由として採用（-0.5%未満）
CRASH_RISING_RATIO_MAX = 0.25           # 上昇銘柄割合がこれ以下ならCRASH扱い
CRASH_HIGH_RATIO_MAX = 0.03             # 高値付近割合がこれ以下ならCRASH扱い

# =========================
# signal品質フィルタ（Replay中心）
# =========================
SIGNAL_FILTER_RSI_BLOCK_GT = 82.0        # RSI > 82 は危険（Entry禁止）
SIGNAL_FILTER_ATR_PCT_BLOCK_GT = 4.0     # ATR% > 4% は危険（Entry禁止）
SIGNAL_FILTER_RS_BLOCK_LT = 0.0          # Relative Strength（TOPIX比） < 0 は危険（Entry禁止）

# WEAK時の厳格化（ユーザー要望: WEAK時はRSI/ATR厳格化 + 後場制限のみ）
WEAK_SIGNAL_FILTER_RSI_BLOCK_GT = 78.0
WEAK_SIGNAL_FILTER_ATR_PCT_BLOCK_GT = 3.5

# 指数（代用ETF）:
# - Nikkei: 1321.T（NF日経225）
# - TOPIX : 1306.T（TOPIX連動）
INDEX_NIKKEI_ETF = "1321.T"
INDEX_TOPIX_ETF = "1306.T"

# =========================
# Replay銘柄スコア（実運用フィルタ用）
# =========================
_SYMBOL_SCORING_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def _load_symbol_scoring_latest() -> dict[str, Any]:
    """
    直近のReplay銘柄スコアを読み込みます。
    - 保存先: results/symbol_scores_latest.json（Replay実行時に自動生成）
    - 無ければ空dict
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(script_dir, "results", "symbol_scores_latest.json")
        if not os.path.exists(p):
            return {}
        mtime = os.path.getmtime(p)
        if _SYMBOL_SCORING_CACHE.get("mtime") == mtime and isinstance(_SYMBOL_SCORING_CACHE.get("data"), dict):
            return dict(_SYMBOL_SCORING_CACHE.get("data") or {})
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        _SYMBOL_SCORING_CACHE["mtime"] = mtime
        _SYMBOL_SCORING_CACHE["data"] = dict(data)
        return dict(data)
    except Exception:
        return {}


def _symbol_blacklist_and_priority_sets() -> tuple[set[str], set[str], set[str]]:
    data = _load_symbol_scoring_latest()
    bl = set([str(s) for s in (data.get("blacklist_symbols") or []) if str(s).strip()])
    pr = set([str(s) for s in (data.get("priority_symbols") or []) if str(s).strip()])
    qf = set([str(s) for s in (data.get("quality_blocked_symbols") or []) if str(s).strip()])
    # 強銘柄候補（優先監視・加点）
    pr.update({"9984.T", "7012.T", "9412.T"})
    return bl, pr, qf



def calculate_entry(q: Quote) -> Optional[float]:
    """
    entry 計算の共通関数（仕様: すべてここを通す）。

    新仕様:
    - entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER

    注意:
    - recent_5m_high は 1分足系列から計算するため、Quote単体では分かりません。
    - そのため、この関数は `_LATEST_INTRADAY_SIGNALS[q.symbol]` を参照します。
    - day_high は「参考表示」だけにし、entry計算には使いません。
    """
    sig = _LATEST_INTRADAY_SIGNALS.get(q.symbol)
    return calc_entry_from_signals(sig)


def _calc_change_percent(*, price: float, previous_close: Optional[float]) -> Optional[float]:
    """
    前日比（%）を previousClose から計算します。

    要件:
    - previousClose が存在する場合:
        change_percent = ((price - previousClose) / previousClose) * 100
    - previousClose が無い / 0 以下などで計算できない場合:
        None を返す（= 見送り理由「前日終値取得失敗」につながる）
    """
    if previous_close is None:
        return None
    pc = float(previous_close)
    if pc <= 0:
        return None
    return ((float(price) - pc) / pc) * 100.0


def fetch_ma25(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    25日移動平均（MA25）を取得します。

    ポイント（初心者向け）:
    - Yahooの quote API では移動平均が取れないことがあるため、chart（日足）から計算します。
    - 「過去25日分の終値」を集めて平均 = MA25（とてもベーシックなSMAです）
    - データが足りない/取れない場合は None を返します（= 見送り理由になる）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1d", "range": "6mo"}  # 25本以上欲しいので6ヶ月ぶん取る
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}
    closes = (q0 or {}).get("close") or []

    # close 配列には None が混ざることがあります（取引が無い日/欠損など）。
    # 後ろ（最新側）から 25 本ぶんの「数値」を集めます。
    last_25: list[float] = []
    for v in reversed(closes):
        if isinstance(v, (int, float)):
            last_25.append(float(v))
            if len(last_25) >= 25:
                break

    if len(last_25) < 25:
        return None
    return sum(last_25) / 25.0


def fetch_avg_volume_5(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    5日平均出来高（SMA5）を取得します。

    Yahoo Finance の chart（日足）から出来高配列を取り、
    「直近の5営業日分の出来高」を平均します。
    - データ不足なら None（= 表示/Discordでは N/A。現状は必須条件ではありません）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1d", "range": "3mo"}  # 5営業日分を確実に確保（閑散期/祝日対策）
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}
    volumes = (q0 or {}).get("volume") or []

    last_5: list[float] = []
    for v in reversed(volumes):
        if isinstance(v, (int, float)):
            vv = float(v)
            if vv > 0:
                last_5.append(vv)
                if len(last_5) >= 5:
                    break

    if len(last_5) < 5:
        return None
    return sum(last_5) / 5.0


def fetch_vwap(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    VWAP（出来高加重平均価格）を取得/推定します。

    Yahoo chart API が VWAP を直接返さない場合があるため、
    ここでは「1分足の典型価格（(高値+安値+終値)/3）×出来高」を累積して概算 VWAP を計算します。

    取得できない場合は None を返します（要件どおり、取得失敗でも条件からは除外しません）。
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": "1d"}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    # 1) もし vwap 配列がそのまま返ってくるなら、それを使います
    if isinstance((q0 or {}).get("vwap"), list):
        vwap_arr = q0.get("vwap") or []
        for v in reversed(vwap_arr):
            if isinstance(v, (int, float)):
                vvp = float(v)
                if vvp > 0:
                    return vvp

    # 2) 返ってこない場合は概算 VWAP
    high_arr = (q0 or {}).get("high") or []
    low_arr = (q0 or {}).get("low") or []
    close_arr = (q0 or {}).get("close") or []
    vol_arr = (q0 or {}).get("volume") or []

    n = min(len(high_arr), len(low_arr), len(close_arr), len(vol_arr))
    if n <= 0:
        return None

    total_pv = 0.0
    total_v = 0.0
    # 最新側がいいが、順序はどちらでも計算できるので簡単に走査します。
    for i in range(n):
        h = high_arr[i]
        l = low_arr[i]
        c = close_arr[i]
        v = vol_arr[i]
        if not isinstance(h, (int, float)) or not isinstance(l, (int, float)) or not isinstance(c, (int, float)):
            continue
        if not isinstance(v, (int, float)):
            continue
        vv = float(v)
        if vv <= 0:
            continue
        # 典型価格（typical price）
        tp = (float(h) + float(l) + float(c)) / 3.0
        total_pv += tp * vv
        total_v += vv

    if total_v <= 0:
        return None
    return total_pv / total_v


def _fetch_quote_v7(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    Yahoo Finance（非公式API v7/finance/quote）から現在値を取得します。

    返ってくるJSONはざっくりこんな形です（必要なところだけ）:
      {
        "quoteResponse": {
          "result": [
            {
              "symbol": "7203.T",
              "regularMarketPrice": 3000.0,
              "currency": "JPY",
              "regularMarketTime": 1710000000
            }
          ],
          "error": null
        }
      }

    - regularMarketPrice: 現在値（市場が開いていれば「今」の値に近い）
    - regularMarketTime: 価格の時刻（Unix秒, UTC）
    """

    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)
    params = {"symbols": symbol}

    r = session.get(YAHOO_QUOTE_URL, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    quote_resp = data.get("quoteResponse") or {}
    results = quote_resp.get("result") or []
    if not results:
        # symbol が間違っている / データが無い / API側仕様変更 などで起きます
        raise RuntimeError(f"価格データが見つかりませんでした。symbol={symbol} レスポンス={quote_resp!r}")

    q0 = results[0] or {}
    price = q0.get("regularMarketPrice")
    if price is None:
        # 取引時間外や、該当フィールドが欠けたケース
        raise RuntimeError(f"regularMarketPrice が取得できませんでした。symbol={symbol} データ={q0!r}")

    currency = q0.get("currency") or ""
    # previousClose（前日終値）: これが取れれば「前日比%」を自前で計算できます。
    # Yahoo側のキー名は環境/銘柄で揺れることがあるので複数候補を見ます。
    previous_close = q0.get("regularMarketPreviousClose")
    if previous_close is None:
        previous_close = q0.get("previousClose")
    if previous_close is None:
        previous_close = q0.get("chartPreviousClose")

    # 前日比（%）:
    # - Yahooが regularMarketChangePercent を返すこともありますが、環境/銘柄によって欠けることがあるため
    #   previousClose から計算した値を“正”として採用します（要件）。
    change_percent = _calc_change_percent(price=float(price), previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None)
    day_high = q0.get("regularMarketDayHigh")
    day_low = q0.get("regularMarketDayLow")
    volume = q0.get("regularMarketVolume")

    # 時価総額（marketCap）
    # Yahooのレスポンスキーは環境で揺れることがあるので複数候補を試します。
    market_cap_raw = q0.get("marketCap")
    if market_cap_raw is None:
        market_cap_raw = q0.get("marketCapFloat")
    market_cap: Optional[float]
    if isinstance(market_cap_raw, (int, float)):
        market_cap = float(market_cap_raw)
    elif isinstance(market_cap_raw, str):
        try:
            market_cap = float(market_cap_raw.replace(",", ""))
        except Exception:
            market_cap = None
    else:
        market_cap = None

    market_time = q0.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=str(currency),
        previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None,
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        day_low=float(day_low) if isinstance(day_low, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
        market_cap=market_cap,
    )


def _fetch_quote_v8_chart(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    Yahoo Finance（非公式API v8/finance/chart）から現在値を取得します（fallback用）。

    chart は「ローソク足系列」も返しますが、ここでは以下のどちらかで現在値を取ります:
    - meta.regularMarketPrice（あれば最優先）
    - indicators.quote[0].close の末尾（最後の終値/現在値に近い値）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    # interval/range は最小限でOK。細かい足が欲しいわけではなく「今の値」を取りたいだけです。
    params = {"interval": "1m", "range": "1d"}

    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        raise RuntimeError(f"chart error: {error!r}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"chart result が空です。symbol={symbol}")

    r0 = results[0] or {}
    meta = r0.get("meta") or {}

    currency = str(meta.get("currency") or "")
    # chart の meta は previousClose のキー名が環境で揺れることがあります。
    # よくある候補を順に試します。
    previous_close = (
        meta.get("previousClose")
        if isinstance(meta.get("previousClose"), (int, float))
        else meta.get("chartPreviousClose")
        if isinstance(meta.get("chartPreviousClose"), (int, float))
        else None
    )
    day_high = meta.get("regularMarketDayHigh")
    day_low = meta.get("regularMarketDayLow")
    volume = meta.get("regularMarketVolume")
    market_cap_raw = meta.get("marketCap")
    if market_cap_raw is None:
        market_cap_raw = meta.get("marketCapFloat")
    market_cap: Optional[float]
    if isinstance(market_cap_raw, (int, float)):
        market_cap = float(market_cap_raw)
    elif isinstance(market_cap_raw, str):
        try:
            market_cap = float(market_cap_raw.replace(",", ""))
        except Exception:
            market_cap = None
    else:
        market_cap = None

    # 1) meta.regularMarketPrice があればそれを使う（「現在値」として最もそれっぽい）
    price = meta.get("regularMarketPrice")

    # 2) 無ければ close 配列の最後の非nullを探す
    if price is None:
        indicators = r0.get("indicators") or {}
        quotes = indicators.get("quote") or []
        q0 = quotes[0] if quotes else {}
        closes = (q0 or {}).get("close") or []
        for v in reversed(closes):
            if v is not None:
                price = v
                break

    if price is None:
        raise RuntimeError(f"現在値が見つかりませんでした（chart）。symbol={symbol}")

    # 前日比（%）は previousClose から計算します（要件）。
    change_percent = _calc_change_percent(price=float(price), previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None)

    market_time = meta.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=currency,
        previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None,
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        day_low=float(day_low) if isinstance(day_low, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
        market_cap=market_cap,
    )


def fetch_quote(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    現在値取得の「本体」。
    まず v7/quote を試し、401/403 などで弾かれたら warmup → 再試行 → だめなら chart にフォールバックします。
    """
    try:
        return _fetch_quote_v7(session, symbol, timeout_sec=timeout_sec)
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        # 401/403 は環境依存で出ることがあります（今回の検証環境もこれ）。
        if status in (401, 403):
            warmup_session(session, symbol, timeout_sec=timeout_sec)
            # warmup 後にもう一度 quote
            try:
                return _fetch_quote_v7(session, symbol, timeout_sec=timeout_sec)
            except requests.HTTPError as e2:
                status2 = getattr(e2.response, "status_code", None)
                if status2 in (401, 403):
                    # quote がダメなら chart を試す
                    return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)
                raise
            except Exception:
                # quote の再試行で別のエラーになったら chart を試す
                return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)
        raise
    except Exception:
        # quote のパース失敗などは chart を試す（非公式なのでフィールド欠損があり得ます）
        return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)


def fetch_history_1m(
    session: requests.Session,
    symbol: str,
    *,
    range_str: str,
    timeout_sec: float = 12.0,
) -> tuple[list[ReplayBar], dict]:
    """
    Yahoo Finance の chart API から「過去1分足」を取得します（リプレイ用）。

    仕様:
    - interval=1m
    - range は "1d" / "5d" / "10d" / "20d" / "60d"
      - ただし Yahoo Finance 側の仕様都合で、そのまま通らないことがあります。
      - その場合は、内部で「近いYahooのrange」へマップして取得します（検証用途向け）。

    戻り値:
    - bars: 1分足の配列（欠損データは除外）
    - meta: chart.result[0].meta（currency/previousClose等が入ることがあります）
    """
    # Yahoo Finance の chart API は、range に "1d","5d","1mo","3mo"... のような値を要求します。
    # 一方でユーザー要件として「10d/20d/60d」を指定したいので、近い値へマップして互換を持たせます。
    range_map = {
        "1d": "1d",
        "5d": "5d",
        # 10d/20d は概ね 1ヶ月(1mo) に含まれる想定
        "10d": "1mo",
        "20d": "1mo",
        # 60d は「60d」を優先（1mでは 3mo が弾かれる環境があるため）
        "60d": "60d",
    }
    if range_str not in range_map:
        raise ValueError("range_str は '1d','5d','10d','20d','60d' を指定してください")

    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": range_map[range_str]}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        raise RuntimeError(f"chart error: {error!r}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"chart result が空です。symbol={symbol}")

    r0 = results[0] or {}
    meta = r0.get("meta") or {}
    ts_arr = r0.get("timestamp") or []
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    opens = (q0 or {}).get("open") or []
    highs = (q0 or {}).get("high") or []
    lows = (q0 or {}).get("low") or []
    closes = (q0 or {}).get("close") or []
    vols = (q0 or {}).get("volume") or []

    n = min(len(ts_arr), len(opens), len(highs), len(lows), len(closes), len(vols))
    bars: list[ReplayBar] = []

    # 欠損（None）が混ざりやすいので、数値が揃っている行だけ採用します。
    for i in range(n):
        ts = ts_arr[i]
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = vols[i]
        if not isinstance(ts, (int, float)):
            continue
        if not all(isinstance(x, (int, float)) for x in (o, h, l, c, v)):
            continue
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        bars.append(
            ReplayBar(
                timestamp_utc=dt,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )

    return bars, meta


def fetch_history_1m_by_period(
    session: requests.Session,
    symbol: str,
    *,
    start_utc: datetime,
    end_utc: datetime,
    timeout_sec: float = 20.0,
) -> tuple[list[ReplayBar], dict]:
    """
    Yahoo Finance の chart API から「指定期間の1分足」を取得します（Replayランダム日付抽出用）。

    目的:
    - interval=1m は range=60d/3mo などが弾かれる環境があるため、
      「1日ぶん（period1/period2）」を複数回取得して3か月分の検証を可能にします。
    """
    t1 = start_utc
    t2 = end_utc
    if t1.tzinfo is None:
        t1 = t1.replace(tzinfo=timezone.utc)
    if t2.tzinfo is None:
        t2 = t2.replace(tzinfo=timezone.utc)
    if t2 <= t1:
        raise ValueError("end_utc は start_utc より後である必要があります")

    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {
        "interval": "1m",
        "period1": int(t1.timestamp()),
        "period2": int(t2.timestamp()),
        "includePrePost": "false",
    }
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        raise RuntimeError(f"chart error: {error!r}")

    results = chart.get("result") or []
    if not results:
        return [], {}

    r0 = results[0] or {}
    meta = r0.get("meta") or {}
    ts_arr = r0.get("timestamp") or []
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    opens = (q0 or {}).get("open") or []
    highs = (q0 or {}).get("high") or []
    lows = (q0 or {}).get("low") or []
    closes = (q0 or {}).get("close") or []
    vols = (q0 or {}).get("volume") or []

    n = min(len(ts_arr), len(opens), len(highs), len(lows), len(closes), len(vols))
    bars: list[ReplayBar] = []
    for i in range(n):
        ts = ts_arr[i]
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = vols[i]
        if not isinstance(ts, (int, float)):
            continue
        if not all(isinstance(x, (int, float)) for x in (o, h, l, c, v)):
            continue
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        bars.append(
            ReplayBar(
                timestamp_utc=dt,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )
    return bars, meta


def fetch_intraday_1m_series(
    session: requests.Session,
    symbol: str,
    *,
    timeout_sec: float = 20.0,
) -> tuple[list[float], list[float], list[float]]:
    """
    リアルタイム監視用に「直近の1分足系列」を取ります。

    返すもの:
    - closes: close配列（Noneは除外しないでそのまま入る可能性があるので呼び出し側で注意）
    - highs:  high配列
    - vols:   volume配列（1分ごとの出来高）

    なぜ必要？
    - recent_5m_high（直近5分高値）
    - price_5min_ago（5分前の価格）
    - 直近3分出来高 vs その前3分出来高（加点）
    を出すためです。
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": "1d"}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    if chart.get("error"):
        raise RuntimeError(f"chart error: {chart.get('error')!r}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("chart result が空です")

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    closes = (q0 or {}).get("close") or []
    highs = (q0 or {}).get("high") or []
    vols = (q0 or {}).get("volume") or []

    # 型をそろえる（Noneが混ざり得るので list[float] ではなく list を返したいが、
    # 既存コードの型と合わせるため、数値だけをfloatに寄せつつ、Noneはそのまま残します）
    def _as_float_or_none(x):
        return float(x) if isinstance(x, (int, float)) else None

    closes2 = [_as_float_or_none(x) for x in closes]
    highs2 = [_as_float_or_none(x) for x in highs]
    vols2 = [_as_float_or_none(x) for x in vols]
    return closes2, highs2, vols2


def calc_intraday_signals_from_series(
    *,
    price: float,
    closes: list[Optional[float]],
    highs: list[Optional[float]],
    vols: list[Optional[float]],
    vwap: Optional[float],
) -> IntradaySignals:
    """
    1分足の配列から、エントリー判定に必要なシグナルを計算します。

    仕様対応:
    - recent_5m_high: 直近5分の高値（最新足は除外して計算）
    - price_5min_ago: 5分前の価格（close）
    - VWAP乖離率
    - 出来高増加（直近3分合計 > その前3分合計）
    """
    # Noneを除いた「末尾の有効データ列」を作ります（欠損があるときの耐性）
    highs_valid = [x for x in highs if isinstance(x, (int, float))]
    closes_valid = [x for x in closes if isinstance(x, (int, float))]
    vols_valid = [x for x in vols if isinstance(x, (int, float))]

    recent_5m_high: Optional[float] = None
    price_5min_ago: Optional[float] = None
    vol_inc: Optional[bool] = None

    # recent_5m_high:
    # - 「直近5分」= 直近5本の1分足
    # - 「上抜け」判定に使うので、現在の足（最新1本）は除外して max を取ります
    if len(highs_valid) >= 6:
        window = highs_valid[-6:-1]  # 5本
        if window:
            recent_5m_high = float(max(window))

    # price_5min_ago:
    # - close の 5本前（最新を含めた時系列の -6 番目）
    if len(closes_valid) >= 6:
        price_5min_ago = float(closes_valid[-6])

    # 出来高増加（加点用）:
    # - 直近3分合計 vs その前3分合計
    if len(vols_valid) >= 6:
        last3 = sum(float(x) for x in vols_valid[-3:])
        prev3 = sum(float(x) for x in vols_valid[-6:-3])
        vol_inc = last3 > prev3

    # VWAP乖離率
    vwap_distance_pct: Optional[float] = None
    if isinstance(vwap, (int, float)) and float(vwap) > 0:
        vwap_distance_pct = ((float(price) - float(vwap)) / float(vwap)) * 100.0

    return IntradaySignals(
        recent_5m_high=recent_5m_high,
        price_5min_ago=price_5min_ago,
        vwap=(float(vwap) if isinstance(vwap, (int, float)) else None),
        vwap_distance_pct=vwap_distance_pct,
        vol_3m_gt_prev_3m=vol_inc,
    )

def parse_args(argv: list[str]) -> argparse.Namespace:
    """
    コマンドライン引数の解析。

    初心者向けポイント:
    - コマンドで指定できるようにすると、毎回ファイルを編集しなくて良くなります。
    """
    p = argparse.ArgumentParser(
        prog="yahoo_kabu_watch.py",
        description="Yahoo Finance（非公式API）で日本株の現在値を1秒ごとに監視し、上抜けを表示します（発注なし）。",
    )
    p.add_argument(
        "--morning-screen",
        action="store_true",
        help=(
            "朝スクリーニング機能を実行します。"
            " デイトレ開始前に『その日触るべき監視候補』を自動選定して、ターミナルとDiscordに出力します。"
            " 通常監視やReplayには影響しません。"
        ),
    )
    p.add_argument(
        "--replay",
        action="store_true",
        help=(
            "過去データの仮想リプレイ（テスト）を有効化します。"
            " Yahoo Finance の過去1分足を取得し、1秒ごとに1分ずつ再生して通常の判定/Discord通知を動かします。"
        ),
    )
    p.add_argument(
        "--replay-mode",
        type=str,
        default="normal",
        choices=["normal", "fast"],
        help=(
            "Replayの実行モード。"
            " normal=従来(待機あり) / fast=待機なし・出力最小・結果集計優先。"
        ),
    )
    p.add_argument(
        "--replay-fast-discord",
        action="store_true",
        help="fastモードでもDiscord通知を有効にします（デフォルトはOFF）。",
    )
    p.add_argument(
        "--replay-fast-verbose",
        action="store_true",
        help="fastモードでも進捗ログを多めに出します（デフォルトは最小表示）。",
    )
    p.add_argument(
        "--replay-fast-print-signal-details",
        action="store_true",
        help="fastモードでも終了時のsignal詳細ログを表示します（デフォルトはOFF・結果ファイルには保存されます）。",
    )
    p.add_argument(
        "--replay-market-debug",
        action="store_true",
        help="Replayで、signal候補（crossed）発生時に地合い判定のTrue/Falseをデバッグ表示します。",
    )
    p.add_argument(
        "--replay-range",
        type=str,
        default="1d",
        choices=["1d", "5d", "10d", "20d", "60d", "random_5d"],
        help=(
            "リプレイで取得する期間。1d/5d/10d/20d/60d。"
            " random_5d を指定すると『過去3か月からランダムに5営業日抽出』になります。"
            " デフォルト 1d"
        ),
    )
    p.add_argument(
        "--replay-repeat",
        type=int,
        default=1,
        help="Replayを連続実行する回数（例: --replay-repeat 10）。デフォルト 1",
    )
    p.add_argument(
        "--replay-random-days",
        type=int,
        default=0,
        help=(
            "Replayの日付をランダム抽出します（営業日）。"
            " 0なら無効。例: --replay-random-days 5"
        ),
    )
    p.add_argument(
        "--replay-random-months",
        type=int,
        default=3,
        help="ランダム抽出の対象期間（月）。デフォルト 3（過去3か月）",
    )
    p.add_argument(
        "--replay-seed",
        type=int,
        default=None,
        help=(
            "Replayランダム抽出の乱数seed（再現用）。"
            " 指定しない場合は実行ごとに変わります。"
        ),
    )
    p.add_argument(
        "--replay-morning-screen",
        type=str,
        default="",
        help=(
            "Replay中に、指定したJST時刻（HH:MM）で『Morning Screen → 監視銘柄へ自動追加 → その後のsignal検証』を行います。"
            " 例: --replay-morning-screen 09:07"
        ),
    )
    p.add_argument(
        "--replay-early-exit",
        action="store_true",
        help="Replayで、STOP前の早期撤退（VWAP割れ/直近5分安値割れ）を有効化します。",
    )
    p.add_argument(
        "--replay-disable-afternoon-entry",
        action="store_true",
        help="Replayで、後場（12:30以降）の新規Entryを禁止します。",
    )
    p.add_argument(
        "--replay-strict-afternoon-entry",
        action="store_true",
        help="Replayで、後場（12:30以降）のEntry条件を厳格化します（全面禁止ではなく絞り込み）。",
    )
    p.add_argument(
        "--replay-afternoon-compare",
        action="store_true",
        help="Replayで『通常/後場禁止/後場厳格化』の3パターンを同一バッチで比較します（seed固定推奨）。",
    )
    p.add_argument(
        "--replay-config",
        type=str,
        default="",
        help="Replayの戦略条件をまとめたconfig JSONパス（例: configs/replay_safe.json）。",
    )
    p.add_argument(
        "--one-trade-per-symbol-per-day",
        action="store_true",
        help=(
            "Replay期待値検証で『同一銘柄は1日に最大1回まで』Entry signal を採用します（JST日付基準）。"
            " このモードでは、同じJST日付で同じsymbolの2回目以降のsignalは、検出ログは出してもよいが集計対象外にします。"
        ),
    )
    # =========================
    # ADD（追加ポジション）制御
    # =========================
    # 要件:
    # - ADDをデフォルトOFF
    # - --disable-add 指定時は ADD1/ADD2 を生成しない（BASEのみ運用）
    # 互換のため、明示的にONにしたい場合は --enable-add を用意します。
    g_add = p.add_mutually_exclusive_group()
    g_add.add_argument(
        "--enable-add",
        action="store_true",
        help="ADD（追加ポジション: ADD1/ADD2）を有効化します（デフォルトはOFF）。",
    )
    g_add.add_argument(
        "--disable-add",
        action="store_true",
        help="ADD（追加ポジション: ADD1/ADD2）を無効化します（デフォルトOFF・明示用）。",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="取得間隔（秒）。デフォルト 1.0",
    )
    p.add_argument(
        "--watch",
        type=str,
        default="",
        help="監視銘柄をカンマ区切りで指定（例: 7203.T,9984.T）。指定するとファイル上部の WATCH を上書きします。",
    )
    p.add_argument(
        "--watch-file",
        type=str,
        default="",
        help="監視銘柄を1行1銘柄で書いたファイルパス。空行と # から始まる行は無視します。--watch より優先。",
    )
    p.add_argument(
        "--print-all",
        action="store_true",
        help="条件に合わない銘柄も含めて毎回ログを出します（デバッグ用）。",
    )
    p.add_argument(
        "--only-changes",
        action="store_true",
        help="候補リストが変わった時だけ表示します（出力を減らしたい場合）。",
    )
    return p.parse_args(argv)


def _fmt_price(x: Optional[float]) -> str:
    """
    価格を見やすく表示するための整形です。
    - 四捨五入して整数っぽく見える場合は整数で返します
    - 少数が必要なら小数2桁まで出します
    """
    if x is None:
        return "N/A"
    xf = float(x)
    if abs(xf - round(xf)) < 1e-9:
        return str(int(round(xf)))
    return f"{xf:.2f}"


def _fmt_volume(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    # 出来高は整数が多いので、表示は整数に寄せます。
    return str(int(round(float(v))))


# =========================
# Discord通知の表示（Embed用の整形）
# =========================
# Discord Embed は「title / color / fields」などを持てるので、
# 長い文章よりも「重要情報を上に」「補足は下に」まとめやすくなります。


def _fmt_yen(x: Optional[float]) -> str:
    """価格は「小数なし・円表示」に統一します。"""
    if x is None:
        return "N/A"
    return f"{int(round(float(x)))}円"


def _fmt_pct(x: Optional[float]) -> str:
    """前日比%は小数2桁、符号付きで表示します。"""
    if x is None:
        return "N/A"
    xf = float(x)
    sign = "+" if xf >= 0 else ""
    return f"{sign}{xf:.2f}%"


def _fmt_ratio_pct(numer: Optional[float], denom: Optional[float]) -> str:
    """例: 高値接近率 = price/day_high * 100（小数1桁）"""
    if numer is None or denom is None:
        return "N/A"
    d = float(denom)
    if d <= 0:
        return "N/A"
    return f"{(float(numer) / d) * 100.0:.1f}%"


def _fmt_volume_man(v: Optional[float]) -> str:
    """
    出来高は「万株」表示にします。
    例: 8,940,000 → 894万株
    """
    if v is None:
        return "N/A"
    man = int(round(float(v) / 10_000.0))
    return f"{man}万株"


def _embed_field(name: str, value: str, *, inline: bool = False) -> dict[str, Any]:
    """Embed fields の最小構造。"""
    return {"name": name, "value": value, "inline": bool(inline)}


JST = timezone(timedelta(hours=9))


def _fmt_dt_jst(dt_utc: Optional[datetime]) -> str:
    """
    UTCの datetime を JST 表示にします（リプレイの視認性改善用）。
    例: 2026-05-01 15:23:00
    """
    if dt_utc is None:
        return "N/A"
    # dt_utc は tz=UTC の想定。念のため tz が無い場合も UTC として扱います。
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dt_jst_short(dt_utc: Optional[datetime]) -> str:
    """Embed内用の短いJST表示。例: 2026-05-01 15:23 JST"""
    if dt_utc is None:
        return "N/A"
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def build_embed_match(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    vwap: Optional[float],
    ma25: float,
    replay_time_jst: Optional[str] = None,
    recent_5m_high: Optional[float] = None,
    price_5min_ago: Optional[float] = None,
    vwap_distance_pct: Optional[float] = None,
    vol_increase: Optional[bool] = None,
    entry_near_ratio: Optional[float] = None,
    entry_crossed: Optional[bool] = None,
    cross_target_entry: Optional[float] = None,
    prev_entry_snapshot: Optional[float] = None,
    breakout_state: Optional[bool] = None,
) -> dict[str, Any]:
    """
    【条件一致】Embed
    - 重要情報を上にまとめ、補足は下に置きます。
    """
    fields: list[dict[str, Any]] = []
    # リプレイ時は「この通知がどの時刻の再生か」を最上段に出します。
    # 通常モードでは None で渡せば表示されません。
    if replay_time_jst:
        fields.append(_embed_field("Replay時刻", replay_time_jst, inline=False))
    fields.append(_embed_field("現在値", _fmt_yen(q.price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(q.change_percent), inline=True))
    fields.append(_embed_field("出来高", _fmt_volume_man(q.volume), inline=True))
    fields.append(_embed_field("高値接近率", _fmt_ratio_pct(q.price, q.day_high), inline=True))

    # 追加情報（エントリー判断に必要なもの）
    fields.append(_embed_field("直近5分高値", _fmt_yen(recent_5m_high), inline=True))
    fields.append(_embed_field("5分前価格", _fmt_yen(price_5min_ago), inline=True))
    if vwap_distance_pct is None:
        vwap_dist_s = "N/A"
    else:
        vwap_dist_s = f"{vwap_distance_pct:.2f}%"
    fields.append(_embed_field("VWAP乖離率", vwap_dist_s, inline=True))
    if vol_increase is None:
        vol_inc_s = "N/A"
    else:
        vol_inc_s = "あり" if vol_increase else "なし"
    fields.append(_embed_field("出来高増加", vol_inc_s, inline=True))

    # Entry算出元（新仕様）:
    # - Entryは「直近5分高値 × バッファ」で計算しています。
    # - 当日高値(day_high)は参考情報として残します。
    fields.append(_embed_field("Entry算出元", f"直近5分高値 × {ENTRY_BREAKOUT_BUFFER:.3f}", inline=False))
    fields.append(_embed_field("当日高値(参考)", _fmt_yen(q.day_high), inline=True))

    # Entry接近率 / Entry上抜け（クロス）:
    # - 実戦で「今エントリー判断しやすいか」を最短で見られるように追加します。
    _ = entry_near_ratio  # 引数は「しきい値」の表示など将来拡張用（現状は entry から計算）
    if entry > 0:
        fields.append(_embed_field("Entry接近率", f"{(float(q.price)/float(entry))*100.0:.2f}%", inline=True))
    else:
        fields.append(_embed_field("Entry接近率", "N/A", inline=True))
    if entry_crossed is None:
        crossed_s = "N/A"
    else:
        crossed_s = "成立" if entry_crossed else "未成立"
    fields.append(_embed_field("Entry上抜け", crossed_s, inline=True))

    # Entry上抜け（クロス）の判定ターゲット:
    # - 今回の仕様では「前ループ時点の entry」をターゲットにします。
    # - そのため、通知で「どのentryを上抜け判定に使ったか」を明示します。
    if cross_target_entry is not None:
        fields.append(_embed_field("cross_target_entry", _fmt_yen(cross_target_entry), inline=True))
    if prev_entry_snapshot is not None:
        fields.append(_embed_field("prev_entry_snapshot", _fmt_yen(prev_entry_snapshot), inline=True))

    # breakout_state（突破済み/未突破）:
    # - 「なぜ🚀が出ないのか？」を見分けやすくするために表示します。
    if breakout_state is None:
        st_s = "N/A"
    else:
        st_s = "突破済み" if breakout_state else "未突破"
    fields.append(_embed_field("breakout_state", st_s, inline=True))

    fields.append(
        _embed_field(
            "売買候補",
            "\n".join(
                [
                    f"Entry: {_fmt_yen(entry)}",
                    f"Stop: {_fmt_yen(stop)}",
                    f"Take: {_fmt_yen(take)}",
                ]
            ),
            inline=False,
        )
    )
    fields.append(
        _embed_field(
            "補足",
            "\n".join([f"VWAP: {_fmt_yen(vwap)}", f"25MA: {_fmt_yen(ma25)}"]),
            inline=False,
        )
    )

    return {
        "title": f"🟢 条件一致: {q.symbol}",
        "color": 0x2ECC71,  # green
        "fields": fields,
    }


def build_embed_entry_cross(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    vwap: Optional[float],
    ma25: float,
    replay_time_jst: Optional[str],
    recent_5m_high: Optional[float],
    price_5min_ago: Optional[float],
    vwap_distance_pct: Optional[float],
    vol_increase: Optional[bool],
    entry_crossed: bool,
    cross_target_entry: Optional[float] = None,
    prev_entry_snapshot: Optional[float] = None,
) -> dict[str, Any]:
    """
    🚀 Entry上抜け（クロス）を強調するEmbed。
    - 条件一致Embedと同じ情報を持ちつつ、title と color を変えて目立たせます。
    """
    embed = build_embed_match(
        q,
        entry=entry,
        stop=stop,
        take=take,
        vwap=vwap,
        ma25=ma25,
        replay_time_jst=replay_time_jst,
        recent_5m_high=recent_5m_high,
        price_5min_ago=price_5min_ago,
        vwap_distance_pct=vwap_distance_pct,
        vol_increase=vol_increase,
        entry_near_ratio=float(ENTRY_NEAR_RATIO),
        entry_crossed=bool(entry_crossed),
        cross_target_entry=cross_target_entry,
        prev_entry_snapshot=prev_entry_snapshot,
        breakout_state=True,
    )
    embed["title"] = f"🚀 Entry上抜け: {q.symbol}"
    embed["color"] = 0x3498DB  # blue
    return embed


def build_embed_levels_change(
    *,
    symbol: str,
    price: float,
    change_percent: Optional[float],
    old_entry: float,
    new_entry: float,
    old_stop: float,
    new_stop: float,
    old_take: float,
    new_take: float,
) -> dict[str, Any]:
    """【候補価格変更】Embed"""
    fields: list[dict[str, Any]] = []
    fields.append(_embed_field("現在値", _fmt_yen(price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(change_percent), inline=True))
    fields.append(
        _embed_field(
            "候補価格",
            "\n".join(
                [
                    f"Entry: {_fmt_yen(old_entry)} → {_fmt_yen(new_entry)}",
                    f"Stop: {_fmt_yen(old_stop)} → {_fmt_yen(new_stop)}",
                    f"Take: {_fmt_yen(old_take)} → {_fmt_yen(new_take)}",
                ]
            ),
            inline=False,
        )
    )
    return {
        "title": f"🟡 候補価格変更: {symbol}",
        "color": 0xF1C40F,  # yellow
        "fields": fields,
    }


def build_embed_out(
    *,
    symbol: str,
    price: Optional[float],
    change_percent: Optional[float],
    reasons: list[str],
) -> dict[str, Any]:
    """【条件外れ】Embed"""
    reason_text = " / ".join(reasons) if reasons else "条件から外れました"
    fields: list[dict[str, Any]] = []
    fields.append(_embed_field("現在値", _fmt_yen(price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(change_percent), inline=True))
    fields.append(_embed_field("外れた理由", reason_text, inline=False))
    return {
        "title": f"🔴 条件外れ: {symbol}",
        "color": 0xE74C3C,  # red
        "fields": fields,
    }


def _build_discord_message(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    ma25: float,
    vol_avg5: Optional[float],
    vol_spike_ratio: Optional[float],
    vwap: Optional[float],
    market_cap: Optional[float],
) -> dict[str, Any]:
    # 互換のため関数名は残しますが、通常通知では使わない情報を省略した Embed に置き換えます。
    # - market_cap / 出来高急増倍率の詳細 / 長い説明文 は通常通知から外します（要件）。
    _ = (vol_avg5, vol_spike_ratio, market_cap)  # 引数互換維持（未使用）
    # 通常モードでは Replay時刻は付けません（リプレイ側は build_embed_match を直接呼んで付けます）。
    embed = build_embed_match(q, entry=entry, stop=stop, take=take, vwap=vwap, ma25=ma25, replay_time_jst=None)
    return {"embeds": [embed]}


def _discord_post(webhook_url: str, content: str) -> None:
    """
    requests.post で Discord Webhookへ送信します。
    """
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    r.raise_for_status()


def _discord_post_webhook_payload(webhook_url: str, payload: dict[str, Any]) -> None:
    """Webhookへ Embed payload を送ります。"""
    r = requests.post(webhook_url, json=payload, timeout=20)
    r.raise_for_status()


def _parse_channel_id(raw: str) -> Optional[int]:
    """
    環境変数で渡される Discord のチャンネルID（文字列）を int に変換します。
    - 未設定（空文字）の場合は None
    - 数字以外が混ざっていた場合も None（= チャンネル送信を使わずフォールバック）
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _discord_send_to_channel(
    *,
    bot_token: str,
    channel_id: int,
    content: str,
) -> None:
    """
    Discord Bot Token を使って、指定チャンネルへメッセージ送信します（WebhookではなくBot送信）。

    重要:
    - これは「Webhookの送信先」ではなく、channel_id で指定したチャンネルに送ります。
    - Bot がそのチャンネルを閲覧/送信できる権限が必要です。
    """
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json={"content": content}, timeout=20)
    r.raise_for_status()


def _discord_send_to_channel_payload(
    *,
    bot_token: str,
    channel_id: int,
    payload: dict[str, Any],
) -> None:
    """Bot送信（Embed payload 対応版）。"""
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    r.raise_for_status()


def discord_notify(
    payload: dict[str, Any],
    *,
    webhook_url: str,
    alert_channel_id: Optional[int],
    bot_token: str,
) -> None:
    """
    通知を「最終的にどこへ送るか」を1か所にまとめた関数です。

    優先順位（方針）:
    1) ALERT_CHANNEL_ID + DISCORD_BOT_TOKEN があるなら、Bot送信に統一（推奨）
    2) それが無理なら、従来どおり Webhook へ送信（互換）

    運用メモ（初心者向け）:
    - 当面いちばん簡単なのは「通知用チャンネルで作ったWebhook URL」を DISCORD_WEBHOOK_URL に入れる方法です。
      そうすると Webhook でも通知ログを分離できます。
    """
    if alert_channel_id is not None and bot_token.strip():
        _discord_send_to_channel_payload(
            bot_token=bot_token.strip(),
            channel_id=alert_channel_id,
            payload=payload,
        )
        return
    if webhook_url.strip():
        _discord_post_webhook_payload(webhook_url.strip(), payload)
        return
    # どちらも無ければ「通知なし」（discord_enabled 側で制御する想定）


def _get_discord_token_with_compat_warning() -> str:
    """
    DiscordのBotトークンを環境変数から取得します（環境変数名の整理版）。

    仕様:
    - 正: DISCORD_TOKEN
    - 旧: DISCORD_BOT_TOKEN（廃止予定）
      - もし残っていたら警告を表示しつつ、その値で動作を継続します（互換）。
    """
    tok = os.getenv("DISCORD_TOKEN", "").strip()
    old = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if old:
        print(f"[{now_str()}] 警告: DISCORD_BOT_TOKEN は廃止予定です。DISCORD_TOKEN に移行してください。")
        if not tok:
            tok = old
    return tok


def _level_changed(*, old: float, new: float) -> bool:
    """
    候補価格が「大幅に変わったか」を判定します。

    判定ルール（仕様）:
    - 価格差が 1%以上変わったら通知
    - または 値幅が 10円以上変わったら通知
    """
    diff = abs(float(new) - float(old))
    if diff >= float(LEVEL_CHANGE_YEN):
        return True

    # %判定は old を基準にします（old=0 などの異常値は安全に弾く）
    if float(old) <= 0:
        return False
    pct = (diff / float(old)) * 100.0
    return pct >= float(LEVEL_CHANGE_PCT)


def _build_levels_change_message(
    *,
    symbol: str,
    price: float,
    currency: str,
    change_percent: Optional[float],
    old_entry: float,
    new_entry: float,
    old_stop: float,
    new_stop: float,
    old_take: float,
    new_take: float,
) -> dict[str, Any]:
    _ = currency  # 引数互換維持（Embedは円固定表示）
    embed = build_embed_levels_change(
        symbol=symbol,
        price=price,
        change_percent=change_percent,
        old_entry=old_entry,
        new_entry=new_entry,
        old_stop=old_stop,
        new_stop=new_stop,
        old_take=old_take,
        new_take=new_take,
    )
    return {"embeds": [embed]}


def _build_discord_out_message(symbol: str, *, price: Optional[float], currency: str) -> dict[str, Any]:
    """
    条件外れ通知（Discord用）。
    - 仕様: 条件一致していた銘柄が条件から外れたら通知する
    """
    _ = currency  # 引数互換維持（Embedは円固定表示）
    embed = build_embed_out(symbol=symbol, price=price, change_percent=None, reasons=[])
    return {"embeds": [embed]}


def _load_watch_from_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        out: list[str] = []
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
        return out


def _parse_watch_csv(s: str) -> list[str]:
    # "7203.T, 9984.T" のような入力を想定
    items = [x.strip() for x in s.split(",")]
    return [x for x in items if x]


# =========================
# 監視銘柄の読み込み（段階的拡張）
# =========================
# 優先順位:
# 1) watchlist.json
# 2) symbols.csv（列: symbol,name）
# 3) このファイル上部の WATCH（既存の動作）
WATCHLIST_JSON_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")
SYMBOLS_CSV_PATH = os.path.join(os.path.dirname(__file__), "symbols.csv")


def _load_watchlist_json(path: str) -> tuple[list[str], Optional[str]]:
    """
    watchlist.json を読み込みます。
    - 想定: JSON 配列 ["7203.T", ...]
    - dict で来る場合も {"symbols": [...]} などを軽く吸収します。
    """
    if not os.path.exists(path):
        return ([], None)
    try:
        raw = json.loads(open(path, "r", encoding="utf-8").read())
        if isinstance(raw, list):
            return ([str(s).strip() for s in raw if str(s).strip()], None)
        if isinstance(raw, dict):
            maybe = raw.get("symbols") or raw.get("watchlist") or []
            if isinstance(maybe, list):
                return ([str(s).strip() for s in maybe if str(s).strip()], None)
    except Exception as e:
        # JSON が壊れている等の理由を返します（呼び出し側でログ出しして前回のリストを維持する）
        return ([], str(e))
    return ([], "watchlist.json の形式が想定外です（JSON配列を期待しています）")


def _load_symbols_csv(path: str) -> list[str]:
    """
    symbols.csv を読み込みます。
    - 期待する列: symbol,name
    - symbol 列だけを使います
    """
    if not os.path.exists(path):
        return []
    try:
        out: list[str] = []
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = (row.get("symbol") or row.get("Symbol") or "").strip()
                if sym:
                    out.append(sym)
        # 重複なしにします
        return sorted({s for s in out if s})
    except Exception:
        return []


def _load_morning_screen_symbols() -> list[str]:
    """
    朝スクリーニング用の「対象銘柄」を決めます。

    仕様（ユーザー要件）:
    - symbols.csv があれば symbols.csv の銘柄を対象
    - なければ watchlist.json の銘柄を対象
    - どちらも無ければ 既存WATCH を対象

    初心者向けポイント:
    - 通常監視の優先順位（watchlist.json優先）とは “別仕様” なので、
      朝スクリーニングでは専用の関数を作って分離します（既存挙動を壊さないため）。
    """
    if os.path.exists(SYMBOLS_CSV_PATH):
        syms = _load_symbols_csv(SYMBOLS_CSV_PATH)
        if syms:
            return syms

    if os.path.exists(WATCHLIST_JSON_PATH):
        watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
        if err:
            # 壊れていても朝スクリーニング自体は止めず、WATCHへフォールバックします。
            print(f"[{now_str()}] watchlist.json 読み込みエラー（朝スクリーニングはWATCHへフォールバック）: {err}")
        if watch_loaded:
            return watch_loaded

    return list(WATCH)


def _calc_day_range_pct(*, day_high: Optional[float], day_low: Optional[float], previous_close: Optional[float], price: float) -> Optional[float]:
    """
    当日値幅（%）を計算します。

    仕様（ユーザー要件）:
    - 「当日値幅が 1%以上」なら加点

    初心者向けポイント:
    - 「%」にするには、何かの基準値で割る必要があります。
    - ここでは、前日終値が取れるなら前日終値を基準（より自然）、
      取れないなら現在値を基準にします（安全なフォールバック）。
    """
    if not isinstance(day_high, (int, float)) or not isinstance(day_low, (int, float)):
        return None
    hi = float(day_high)
    lo = float(day_low)
    if hi <= 0 or lo <= 0:
        return None
    if hi < lo:
        # 変なデータが来た時の安全策
        hi, lo = lo, hi

    base: float
    if isinstance(previous_close, (int, float)) and float(previous_close) > 0:
        base = float(previous_close)
    else:
        base = float(price) if float(price) > 0 else 0.0
    if base <= 0:
        return None

    return ((hi - lo) / base) * 100.0


def _morning_screen_score(
    *,
    q: Quote,
    vwap: Optional[float],
    ma25: Optional[float],
    avg_vol5: Optional[float],
    day_range_pct: Optional[float],
) -> tuple[int, list[str], Optional[float]]:
    """
    朝スクリーニング用のスコアリングを行います。

    仕様（ユーザー要件）:
    - 前日比 +1%以上 +5%未満：+2点
    - 前日比 +5%以上 +8%未満：+1点
    - 前日比 +8%以上：-2点
    - 出来高30万株以上：+1点
    - 出来高が5日平均出来高の1.5倍以上：+2点
    - 現在値 > VWAP：+2点
    - 現在値 > 25日移動平均：+1点
    - 当日高値の98%以上：+2点
    - 当日値幅が1%以上：+1点

    戻り値:
    - score: 合計点
    - reasons: 表示用の理由（主に“加点に効いた要素”）
    - vol_spike_ratio: 出来高 / 5日平均出来高（表示用）
    """
    score = 0
    reasons: list[str] = []

    chg = q.change_percent
    if isinstance(chg, (int, float)):
        chg_f = float(chg)
        if 1.0 <= chg_f < 5.0:
            score += 2
            reasons.append("前日比+1-5%")
        elif 5.0 <= chg_f < 8.0:
            score += 1
            reasons.append("前日比+5-8%")
        elif chg_f >= 8.0:
            score -= 2
            reasons.append("急騰(8%+)")  # マイナスも「注意点」として理由に残す

    vol = q.volume
    if isinstance(vol, (int, float)):
        vol_f = float(vol)
        if vol_f >= 300_000.0:
            score += 1
            reasons.append("出来高30万+")

    vol_spike_ratio: Optional[float] = None
    if isinstance(vol, (int, float)) and isinstance(avg_vol5, (int, float)) and float(avg_vol5) > 0:
        vol_spike_ratio = float(vol) / float(avg_vol5)
        if vol_spike_ratio >= 1.5:
            score += 2
            reasons.append("出来高急増")

    if isinstance(vwap, (int, float)) and float(vwap) > 0 and float(q.price) > float(vwap):
        score += 2
        reasons.append("VWAP上")

    if isinstance(ma25, (int, float)) and float(ma25) > 0 and float(q.price) > float(ma25):
        score += 1
        reasons.append("MA25上")

    if isinstance(q.day_high, (int, float)) and float(q.day_high) > 0:
        if float(q.price) >= float(q.day_high) * 0.98:
            score += 2
            reasons.append("高値付近")

    if isinstance(day_range_pct, (int, float)):
        if float(day_range_pct) >= 1.0:
            score += 1
            reasons.append("値幅1%+")

    return score, reasons, vol_spike_ratio


def _format_morning_screen_message(results: list[MorningScreenResult]) -> str:
    """
    Discord/ターミナルに出す「朝スクリーニング結果」テキストを作ります。

    仕様（ユーザー要件）:
    - 上位10銘柄まで表示
    - 指定のフォーマットに近い形で（順位/スコア/現在値/前日比/出来高/VWAP/理由）
    """
    lines: list[str] = []
    lines.append("📊 朝スクリーニング結果")
    lines.append("")

    if not results:
        lines.append("該当なし（除外条件により0件でした）")
        return "\n".join(lines)

    top = results[:10]
    for i, r in enumerate(top, start=1):
        q = r.quote
        reason_text = " / ".join(r.reasons) if r.reasons else "—"
        lines.append(f"{i}位 {r.symbol} スコア: {r.score}")
        lines.append(f"現在値: {_fmt_yen(q.price)}")
        lines.append(f"前日比: {_fmt_pct(q.change_percent)}")
        lines.append(f"出来高: {_fmt_volume_man(q.volume)}")
        lines.append(f"VWAP: {_fmt_yen(r.vwap)}")
        lines.append(f"理由: {reason_text}")
        lines.append("")

    return "\n".join(lines).rstrip()


def run_morning_screen() -> int:
    """
    朝スクリーニングを実行します（通常監視とは完全に別ルート）。

    除外条件（ユーザー要件）:
    - 出来高が10万株未満は除外
    - 前日比がマイナスは除外
    - 価格が取得できない銘柄は除外
    """
    symbols = _load_morning_screen_symbols()
    if not symbols:
        print(f"[{now_str()}] 朝スクリーニング: 対象銘柄が空です。symbols.csv / watchlist.json / WATCH を確認してください。")
        return 2

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    bot_token = _get_discord_token_with_compat_warning()
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)

    # 朝は1回実行が多いので、ここで「何を対象にしたか」をターミナルに出します。
    print("=== 朝スクリーニング ===")
    print(f"- 対象銘柄数: {len(symbols)}")
    if os.path.exists(SYMBOLS_CSV_PATH):
        print("- ソース: symbols.csv（最優先）")
    elif os.path.exists(WATCHLIST_JSON_PATH):
        print("- ソース: watchlist.json（symbols.csvが無い場合の次点）")
    else:
        print("- ソース: WATCH（フォールバック）")
    print("")

    results: list[MorningScreenResult] = []

    with requests.Session() as session:
        for sym in symbols:
            try:
                q = fetch_quote(session, sym)
            except Exception as e:
                # 仕様: 価格が取得できない銘柄は除外（落とさず続行）
                print(f"[{now_str()}] {sym} 価格取得失敗（除外）: {e}")
                continue

            # 除外条件: 出来高 < 10万
            if not isinstance(q.volume, (int, float)) or float(q.volume) < 100_000.0:
                continue

            # 除外条件: 前日比がマイナス
            if not isinstance(q.change_percent, (int, float)) or float(q.change_percent) < 0.0:
                continue

            # 指標取得（可能な限り）:
            # - 失敗しても「その指標はN/A」でスコア計算を続行します。
            vwap: Optional[float]
            ma25: Optional[float]
            avg5: Optional[float]
            try:
                vwap = fetch_vwap(session, sym)
            except Exception:
                vwap = None
            try:
                ma25 = fetch_ma25(session, sym)
            except Exception:
                ma25 = None
            try:
                avg5 = fetch_avg_volume_5(session, sym)
            except Exception:
                avg5 = None

            day_range_pct = _calc_day_range_pct(
                day_high=q.day_high,
                day_low=q.day_low,
                previous_close=q.previous_close,
                price=float(q.price),
            )

            score, reasons, vol_spike_ratio = _morning_screen_score(
                q=q,
                vwap=vwap,
                ma25=ma25,
                avg_vol5=avg5,
                day_range_pct=day_range_pct,
            )

            results.append(
                MorningScreenResult(
                    symbol=sym,
                    score=int(score),
                    quote=q,
                    vwap=vwap,
                    ma25=ma25,
                    avg_vol5=avg5,
                    vol_spike_ratio=vol_spike_ratio,
                    day_range_pct=day_range_pct,
                    reasons=reasons,
                )
            )

    # 並び順: スコア降順 → 前日比降順 → 出来高降順（同点のときの見やすさ用）
    def _sort_key(r: MorningScreenResult) -> tuple:
        chg = float(r.quote.change_percent) if isinstance(r.quote.change_percent, (int, float)) else 0.0
        vol = float(r.quote.volume) if isinstance(r.quote.volume, (int, float)) else 0.0
        return (int(r.score), chg, vol)

    results_sorted = sorted(results, key=_sort_key, reverse=True)

    msg = _format_morning_screen_message(results_sorted)
    print(msg)

    if discord_enabled:
        try:
            discord_notify(
                {"content": msg},
                webhook_url=webhook_url,
                alert_channel_id=alert_channel_id,
                bot_token=bot_token,
            )
        except Exception as e:
            print(f"[{now_str()}] Discord送信失敗（朝スクリーニング）: {e}")

    return 0


def run_replay(
    *,
    interval_sec: float,
    only_changes: bool,
    fixed_watch: Optional[list[str]],
    replay_range: str,
    replay_random_days: int = 0,
    replay_random_months: int = 3,
    replay_seed: Optional[int] = None,
    replay_mode: str = "normal",
    replay_fast_discord: bool = False,
    replay_fast_verbose: bool = False,
    replay_fast_print_signal_details: bool = False,
    replay_market_debug: bool = False,
    replay_repeat_run_no: int = 0,
    replay_repeat_total: int = 0,
    replay_output_subdir: str = "",
    replay_batch_stamp: str = "",
    replay_morning_screen_hhmm: str = "",
    one_trade_per_symbol_per_day: bool = False,
    enable_add: bool = False,
    replay_early_exit_before_stop: bool = False,
    replay_early_exit_vwap: bool = True,
    replay_early_exit_recent_low: bool = True,
    replay_disable_afternoon_entry: bool = False,
    replay_strict_afternoon_entry: bool = False,
    replay_afternoon_topix_weak_block: bool = True,
    replay_config_name: str = "",
    replay_config_path: str = "",
    aft_volume_spike_ratio_min: float = AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN,
    aft_vwap_dist_pct_max: float = AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX,
    aft_rebreak_mult: float = AFTERNOON_ENTRY_STRICT_REBREAK_MULT,
    replay_settings: Optional[dict[str, Any]] = None,
) -> int:
    """
    過去データの仮想リプレイ（テストモード）。

    仕様（ユーザー要件）:
    - `python yahoo_kabu_watch.py --replay` の時だけ有効（TEST_REPLAY_MODE=True）
    - Yahoo Finance から過去の1分足データを取得（range: 1d/5d）
    - 対象銘柄は watchlist.json（通常と同じ監視銘柄決定ルール）
    - 1分足データを 1 秒ごとに仮想的に再生する
    - 各時点の price/high/low/volume を使って通常の判定ロジックを動かす
    - 条件一致通知 / 条件外れ通知 / 候補価格変更通知 を Discord に送る
    """
    # replay_range は parse_args の choices で制限されますが、保険でここでもチェックします。
    if replay_range not in ("1d", "5d", "10d", "20d", "60d", "random_5d"):
        print("--replay-range は 1d/5d/10d/20d/60d を指定してください。")
        return 2

    # repeatロットの識別子（重要: run_replay 全体で必ず定義しておく）
    # - main側で replay_batch_stamp が渡される想定だが、単体実行でも落ちないようにフォールバックする
    batch_stamp = str(replay_batch_stamp or "").strip() or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    safe_batch_stamp = str(batch_stamp or "").strip() or "replay"

    # =========================
    # Replay設定（表示/保存用）
    # - main側で値+sourceを作って渡す（run_replay単体実行でも落ちないようにfallback）
    # =========================
    if not isinstance(replay_settings, dict):
        replay_settings = {
            "config_name": str(replay_config_name or ""),
            "config_path": str(replay_config_path or ""),
            "replay_range": str(replay_range),
            "replay_repeat": int(replay_repeat_total or 0),
            "replay_mode": str(replay_mode),
        }

    def _settings_lines(st: dict[str, Any]) -> list[str]:
        def _get_flag(k: str) -> tuple[str, str]:
            v = st.get(k)
            if isinstance(v, dict):
                return (str(v.get("value")), str(v.get("source")))
            return (str(v), "")

        out: list[str] = []
        out.append("【Replay設定】")
        out.append("")
        out.append(f"config_name: {str(st.get('config_name') or '')}")
        out.append(f"config_path: {str(st.get('config_path') or '')}")
        out.append("")
        out.append(f"replay_range: {str(st.get('replay_range') or '')} (cli)")
        out.append(f"replay_repeat: {int(st.get('replay_repeat') or 0)} (cli)")
        out.append(f"replay_mode: {str(st.get('replay_mode') or '')} (cli)")
        out.append("")
        v, s = _get_flag("early_exit")
        out.append(f"early_exit={v} ({s})")
        v, s = _get_flag("vwap_break_exit")
        out.append(f"vwap_break_exit={v} ({s})")
        v, s = _get_flag("recent_5m_low_break_exit")
        out.append(f"recent_5m_low_break_exit={v} ({s})")
        out.append("")
        v, s = _get_flag("strict_afternoon")
        out.append(f"strict_afternoon={v} ({s})")
        v, s = _get_flag("disable_afternoon_entry")
        out.append(f"disable_afternoon_entry={v} ({s})")
        v, s = _get_flag("topix_weak_block")
        out.append(f"topix_weak_block={v} ({s})")
        out.append("")
        aft = st.get("afternoon") if isinstance(st.get("afternoon"), dict) else {}
        if isinstance(aft, dict):
            for key, label in [
                ("volume_spike_ratio_min", "afternoon.volume_spike_ratio_min"),
                ("vwap_dist_pct_max", "afternoon.vwap_dist_pct_max"),
                ("rebreak_mult", "afternoon.rebreak_mult"),
            ]:
                vv = aft.get(key)
                if isinstance(vv, dict):
                    out.append(f"{label}={vv.get('value')} ({vv.get('source')})")
        out.append("")
        return out

    # ターミナル出力（要件）
    try:
        print("\n".join(_settings_lines(replay_settings)))
    except Exception:
        pass

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    # Bot送信用トークンは DISCORD_TOKEN に統一します（旧: DISCORD_BOT_TOKEN は互換で吸収）
    bot_token = _get_discord_token_with_compat_warning()
    fast_mode = (str(replay_mode) == "fast")
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)
    if fast_mode and not bool(replay_fast_discord):
        # fastモードは高速化優先のため、デフォルトでDiscord通知を無効化します。
        discord_enabled = False

    if webhook_url:
        print(f"[{now_str()}] Discord通知: DISCORD_WEBHOOK_URL が設定されています（Webhookの送信先チャンネルに送られます）")
    if alert_channel_id is not None and bot_token:
        print(f"[{now_str()}] Discord通知: ALERT_CHANNEL_ID={alert_channel_id} へ Bot送信します（推奨）")
    last_discord_candidate_symbols: set[str] = set()
    # 条件外れ通知で「最後に見た価格」を出すために覚えておきます。
    last_quote_by_symbol: dict[str, Quote] = {}
    last_notified_levels: dict[str, tuple[float, float, float]] = {}
    # 条件外れ通知の安定化（連続不一致をカウント）
    exit_miss_count: dict[str, int] = {}
    # Entry突破状態（最終仕様）:
    # - False: まだentry未突破
    # - True:  entry突破済み（突破中は再通知しない）
    breakout_state_by_symbol: dict[str, bool] = {}
    # breakout_state を True にした時点の entry（基準）を覚えます。
    last_breakout_entry_by_symbol: dict[str, Optional[float]] = {}
    # Entry上抜け（クロス）判定用に「前回価格」を覚えておきます。
    prev_price_by_symbol: dict[str, Optional[float]] = {}
    # （注）以前の型の残骸があるとバグるので、prev_price_by_symbol は上の Optional 版だけに統一します。

    # =========================
    # Replay期待値検証（ターミナル表示のみ）
    # =========================
    # - 🚀 Entry上抜けが出た瞬間を signal として記録
    # - signal後の価格推移で take/stop 到達や最大利益/最大逆行を集計
    replay_signals: list[ReplaySignalEval] = []
    # symbol -> active signal indices（同一銘柄で複数signalが出た場合も追えるようにする）
    active_signal_indices_by_symbol: dict[str, list[int]] = {}
    # 地合いフィルタで「禁止されたENTRY」も、後で“回避効果”を見るために影として追跡します。
    blocked_signal_indices_by_symbol: dict[str, list[int]] = {}
    blocked_entry_count = 0
    signal_candidate_count = 0
    signal_seq = 0
    blocked_reason_counts: dict[str, int] = {}
    # Replay中に「候補が reject された理由」を累積集計します（ユーザー要望）
    reject_reason_counts: dict[str, int] = {}

    # 表示抑制用
    last_candidates: set[str] = set()

    # =========================
    # MARKET_DEBUG（ユーザー要望）
    # =========================
    # signal生成の有無に関係なく、Replay中の地合い判定直後の値を保存します。
    # 出力が肥大化しすぎないよう、一定件数で打ち切ります（先頭から優先的に保持）。
    MARKET_DEBUG_MAX_ROWS = 20000
    market_debug_rows: list[dict[str, Any]] = []

    # crossed デバッグ（ユーザー要望）
    CROSSED_DEBUG_MAX_ROWS = 20000
    CROSSED_FALSE_STREAK_TO_COUNT = 20
    crossed_debug_rows: list[dict[str, Any]] = []
    crossed_false_streak_by_symbol: dict[str, int] = {}

    # パイプライン段階別デバッグ（ユーザー要望）
    pipeline_debug: dict[str, int] = {
        "market_debug_count": 0,
        "candidate_loop_entered": 0,
        "to_notify_count": 0,
        "entry_calc_ok": 0,
        "entry_calc_none": 0,
        "ma25_ok": 0,
        "ma25_none": 0,
        "intraday_signal_ready": 0,
        "intraday_signal_none": 0,
        "crossed_check_entered": 0,
        "crossed_true": 0,
        "crossed_false": 0,
        "signal_generated": 0,
        "replay_signals_append_count": 0,
        "pre_signal_object_count": 0,
        "post_signal_object_count": 0,
    }
    continue_reason_counts: dict[str, int] = {}

    # signal append 直前デバッグ（ユーザー要望）
    APPEND_SIGNAL_DEBUG_MAX_ROWS = 500
    append_signal_debug_rows: list[dict[str, Any]] = []

    # ReplaySignalEval 生成前後デバッグ（ユーザー要望）
    PRE_SIGNAL_OBJECT_DEBUG_MAX_ROWS = 500
    POST_SIGNAL_OBJECT_DEBUG_MAX_ROWS = 500
    pre_signal_object_debug_rows: list[dict[str, Any]] = []
    post_signal_object_debug_rows: list[dict[str, Any]] = []
    continue_before_append_rows: list[dict[str, Any]] = []

    # 例外スタックトレース（ユーザー要望: EXCEPTION_BEFORE_APPEND 先頭10件）
    exception_before_append_traces: list[dict[str, Any]] = []
    EXCEPTION_TRACE_MAX = 10
    exception_before_append_trace_texts: list[str] = []
    trace_capture_failed_count = 0

    # 通常モード同様、MA25/出来高5日平均はキャッシュします（毎秒取り直すと重い）
    ma25_cache: dict[str, tuple[float, float]] = {}
    avg5_cache: dict[str, tuple[float, float]] = {}

    # リプレイで「当日高値」「当日出来高（累積）」「VWAP（概算）」を作るための状態
    running_day_high: dict[str, float] = {}
    running_day_volume: dict[str, float] = {}
    running_vwap_pv: dict[str, float] = {}  # (typical price * volume) 累積
    running_vwap_v: dict[str, float] = {}   # volume 累積

    # 日ごとの previous close を切り替えるための状態（簡易実装）
    current_day_key: dict[str, str] = {}
    prev_close_by_day: dict[str, Optional[float]] = {}

    # 前場高値（後場弱地合いフィルタ用）:
    # key: (JST日付, symbol)
    morning_high_by_day_symbol: dict[tuple[str, str], float] = {}

    # watchlist.json の読み込み失敗時の保険（通常と同様）
    last_watch: list[str] = []

    # =========================
    # Replay Morning Screen（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - 「未来データ」を使うと検証にならないので、指定時刻までの1分足だけでスクリーニングします。
    # - 指定時刻になったら、その日だけ監視対象にTOP10を追加して、以降のsignal/期待値を検証します。

    def _parse_hhmm(hhmm: str) -> Optional[tuple[int, int]]:
        s = (hhmm or "").strip()
        if not s:
            return None
        if ":" not in s:
            return None
        a, b = s.split(":", 1)
        try:
            hh = int(a)
            mm = int(b)
        except Exception:
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return (hh, mm)

    ms_time = _parse_hhmm(replay_morning_screen_hhmm)
    if replay_morning_screen_hhmm.strip() and ms_time is None:
        print("--replay-morning-screen は HH:MM 形式で指定してください（例: 09:07）")
        return 2

    # スクリーニング対象のユニバース（仕様: symbols.csv > watchlist.json > WATCH）
    ms_universe: list[str] = _load_morning_screen_symbols() if ms_time is not None else []

    # 1分足は「必要になった銘柄だけ」取ってキャッシュします（全部取ると重いので）
    ms_bars_cache: dict[str, list[ReplayBar]] = {}
    ms_meta_cache: dict[str, dict] = {}

    # 日別のMorning Screen結果（Replay終了時にまとめて表示するため）
    # key は JST日付 "YYYY-MM-DD"
    ms_daily: dict[str, dict[str, Any]] = {}

    # 日付が変わったときのリセット用（JST基準）
    ms_current_day_jst: Optional[str] = None

    # =========================
    # 前日継続監視（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - 前日に強かった銘柄が「翌日も続伸」するかを検証したい場合、
    #   前日のReplay結果から“継続候補”を作り、翌日の監視開始時点で追加します。
    #
    # - 「継続候補」は Morning Screen TOP10 とは別枠で管理し、
    #   Summary出力でも「当日選出」と「前日継続」を分けて見られるようにします。
    ms_carryover_by_day: dict[str, list[str]] = {}  # day_jst -> carryover symbols

    def _pnl_yen_100_shares_replay(s: ReplaySignalEval) -> float:
        """
        100株あたり損益（円）を計算します（Replay共通）。

        NOTE:
        - Morning Screen Replay Summary と同じルールに揃えます。
        """
        if isinstance(s.final_profit_pct, (int, float)):
            return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
        if s.result == "WIN":
            return (float(s.take_price) - float(s.entry_price)) * 100.0
        if s.result == "LOSE":
            return (float(s.stop_price) - float(s.entry_price)) * 100.0
        return 0.0

    def _day_jst_str(dt_utc: datetime) -> str:
        """UTC datetime を JST日付（YYYY-MM-DD）に変換します。"""
        t = dt_utc
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(JST).strftime("%Y-%m-%d")

    # =========================
    # 事故分析用指標（RSI/ATR/Relative Strength）
    # =========================
    def _calc_rsi14(closes: list[float]) -> Optional[float]:
        """
        RSI(14)（Wilder方式）。
        - 15本以上のcloseが必要
        """
        try:
            xs = [float(x) for x in closes if isinstance(x, (int, float))]
            if len(xs) < 15:
                return None
            # 直近15本から14差分
            window = xs[-15:]
            diffs = [window[i] - window[i - 1] for i in range(1, len(window))]
            gains = [d if d > 0 else 0.0 for d in diffs]
            losses = [(-d) if d < 0 else 0.0 for d in diffs]
            avg_gain = sum(gains) / 14.0
            avg_loss = sum(losses) / 14.0
            if avg_loss <= 0:
                return 100.0
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
            return float(rsi)
        except Exception:
            return None

    def _calc_atr14(highs: list[float], lows: list[float], closes: list[float]) -> Optional[float]:
        """
        ATR(14)（単純平均版）。
        - 15本以上必要（TRはprev closeを使うため）
        """
        try:
            hs = [float(x) for x in highs if isinstance(x, (int, float))]
            ls = [float(x) for x in lows if isinstance(x, (int, float))]
            cs = [float(x) for x in closes if isinstance(x, (int, float))]
            n = min(len(hs), len(ls), len(cs))
            if n < 15:
                return None
            hs = hs[-15:]
            ls = ls[-15:]
            cs = cs[-15:]
            trs: list[float] = []
            for i in range(1, 15):
                h = float(hs[i])
                l = float(ls[i])
                pc = float(cs[i - 1])
                tr = max(h - l, abs(h - pc), abs(l - pc))
                trs.append(float(tr))
            if len(trs) != 14:
                return None
            return float(sum(trs)) / 14.0
        except Exception:
            return None

    # 同一銘柄の重複エントリー制限（追加仕様）:
    # - key: JST日付（YYYY-MM-DD）
    # - value: その日に「採用済み」の銘柄セット
    accepted_entry_symbols_by_day: dict[str, set[str]] = {}

    # 追加ポジション（買い増し）用の状態:
    # - key: (JST日付, symbol)
    # - value: 追加回数（0,1,2）
    add_count_by_day_symbol: dict[tuple[str, str], int] = {}
    # - key: (JST日付, symbol)
    # - value: 直近のEntry価格（BASE/ADD含む“最後に建てたポジション”の価格）
    last_entry_price_by_day_symbol: dict[tuple[str, str], float] = {}
    # - value: 前回ADDの時刻（UTC datetime）。最低5分間隔を作るために使います。
    last_add_time_by_day_symbol: dict[tuple[str, str], datetime] = {}
    # - value: 出来高増加が「継続」しているかを見るための前回値
    prev_vol_inc_by_day_symbol: dict[tuple[str, str], bool] = {}

    # 当日停止（追加仕様）:
    # - 同一銘柄で当日累計損失（=損益%の合計）が -3% を超えたら、その日その銘柄の新規ENTRY/ADDを停止
    #   ※期待値検証のための簡易ルール。勝ちで相殺される設計にしています（累計損益%）。
    daily_cum_profit_pct_by_day_symbol: dict[tuple[str, str], float] = {}
    trading_stopped_by_day_symbol: dict[tuple[str, str], bool] = {}
    # 解決済みsignalの損益を二重計上しないためのフラグ
    resolved_counted_signal_indices: set[int] = set()
    # 停止ログのスパム防止
    stop_logged_by_day_symbol: set[tuple[str, str]] = set()

    with requests.Session() as session:
        # -----------------------------
        # 監視銘柄の決定（通常と同じ）
        # -----------------------------
        watch: list[str] = []
        if fixed_watch is not None:
            watch = list(fixed_watch)
        else:
            if os.path.exists(WATCHLIST_JSON_PATH):
                watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
                if err:
                    print(f"[{now_str()}] watchlist.json 読み込みエラー（前回の監視リストを維持）: {err}")
                    watch = list(last_watch)
                else:
                    watch = watch_loaded
                    last_watch = list(watch)
            else:
                loaded_symbols = _load_symbols_csv(SYMBOLS_CSV_PATH)
                watch = loaded_symbols if loaded_symbols else list(WATCH)

        if not watch:
            print(f"[{now_str()}] 監視銘柄なし。watchlist.json を用意してください。")
            return 2

        # 地合い判定用の指数（代用ETF）を、Replayデータ取得にだけ追加します。
        # - 監視銘柄候補の評価/通知/ENTRY記録の対象にはしません（地合い判定専用）。
        index_syms = [INDEX_NIKKEI_ETF, INDEX_TOPIX_ETF]
        fetch_syms = list(watch) + [s for s in index_syms if s not in watch]

        print("=== TEST REPLAY MODE ===")
        # 表示用の replay_range（通常の 1d/5d には影響させない）
        replay_range_label = str(replay_range)
        # --replay-range random_5d をショートカットとして扱う
        if replay_range_label == "random_5d" and int(replay_random_days or 0) <= 0:
            replay_random_days = 5
            replay_random_months = 3
        if int(replay_random_days or 0) > 0:
            replay_range_label = f"random_{int(replay_random_days)}d"
        print(f"- replay_range: {replay_range_label}")
        # リプレイ速度の見せ方を「直感的」にします。
        # interval_sec=1.0 なら「1秒 = 1分」
        # interval_sec=0.5 なら「0.5秒 = 1分」など。
        if abs(interval_sec - 1.0) < 1e-9:
            speed_s = "1秒 = 1分"
        else:
            speed_s = f"{interval_sec:.2f}秒 = 1分"
        print(f"- replay_speed: {speed_s}")
        if fast_mode:
            print("- replay_mode: fast（sleep無し・出力最小・結果集計優先）")
        print(f"- watch: {', '.join(watch)}\n")

        # -----------------------------
        # fetch_range（API取得用レンジ）
        # -----------------------------
        # random_5d は Replay制御用ラベルであり、Yahooのrange_strには渡さない
        # - 1分足取得は 60d を使い、その中からランダム営業日を抽出する
        fetch_range = str(replay_range)
        if str(replay_range) == "random_5d" or int(replay_random_days or 0) > 0:
            fetch_range = "60d"

        # -----------------------------
        # 過去1分足の取得（最初にまとめて取る）
        # -----------------------------
        bars_by_symbol: dict[str, list[ReplayBar]] = {}
        meta_by_symbol: dict[str, dict] = {}

        # Replay日付のランダム抽出（追加仕様）
        replay_dates_jst: list[str] = []
        if int(replay_random_days or 0) > 0:
            months = int(replay_random_months or 3)
            if months <= 0:
                months = 3
            k = int(replay_random_days)
            if k <= 0:
                k = 5

            # 候補日（平日）を作る（祝日はAPIでデータが無いので後で除外される）
            now_jst = datetime.now(JST)
            start_jst = (now_jst - timedelta(days=months * 31)).date()
            end_jst = now_jst.date()
            candidates: list[str] = []
            d = start_jst
            while d <= end_jst:
                if d.weekday() < 5:
                    candidates.append(d.strftime("%Y-%m-%d"))
                d = d + timedelta(days=1)

            rng = random.Random(int(replay_seed)) if replay_seed is not None else random.Random()
            rng.shuffle(candidates)

            # 祝日などを除外するため、先頭銘柄で「データが取れる日」だけ採用
            probe_sym = watch[0]
            picked: list[str] = []
            for day_s in candidates:
                if len(picked) >= k:
                    break
                try:
                    y, m, dd = (int(x) for x in day_s.split("-"))
                    day0 = datetime(y, m, dd, 0, 0, 0, tzinfo=JST)
                    day1 = day0 + timedelta(days=1)
                    bs, _m = fetch_history_1m_by_period(
                        session,
                        probe_sym,
                        start_utc=day0.astimezone(timezone.utc),
                        end_utc=day1.astimezone(timezone.utc),
                    )
                    if bs:
                        picked.append(day_s)
                except Exception:
                    continue

            replay_dates_jst = sorted(picked)
            if len(replay_dates_jst) < k:
                print(
                    f"[{now_str()}] ランダム抽出の候補日が不足しました: "
                    f"picked={len(replay_dates_jst)}/{k}（祝日が多い/データ取得制限など）"
                )
            if replay_dates_jst:
                print("=== Replayランダム抽出日（JST） ===")
                print("\n".join([f"- {d}" for d in replay_dates_jst]))
                print(f"- seed: {replay_seed if replay_seed is not None else '(random)'}")
                print()

        for sym in fetch_syms:
            try:
                if replay_dates_jst:
                    all_bars_sym: list[ReplayBar] = []
                    meta_last: dict = {}
                    for day_s in replay_dates_jst:
                        y, m, dd = (int(x) for x in day_s.split("-"))
                        day0 = datetime(y, m, dd, 0, 0, 0, tzinfo=JST)
                        day1 = day0 + timedelta(days=1)
                        bs, mt = [], {}
                        last_err: Optional[Exception] = None
                        for attempt in range(3):
                            try:
                                bs, mt = fetch_history_1m_by_period(
                                    session,
                                    sym,
                                    start_utc=day0.astimezone(timezone.utc),
                                    end_utc=day1.astimezone(timezone.utc),
                                )
                                last_err = None
                                break
                            except Exception as e:
                                if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                                    last_err = e
                                    break
                                last_err = e
                                time.sleep(1.0 * (attempt + 1))
                        if last_err is not None and (not bs) and (not mt):
                            raise last_err
                        if bs:
                            all_bars_sym.extend(bs)
                        if mt:
                            meta_last = mt
                    bars, meta = all_bars_sym, meta_last
                else:
                    bars, meta = [], {}
                    last_err2: Optional[Exception] = None
                    for attempt in range(3):
                        try:
                            bars, meta = fetch_history_1m(session, sym, range_str=fetch_range)
                            last_err2 = None
                            break
                        except Exception as e:
                            # DNS解決失敗は待っても直らないことが多いので、即中断して次へ
                            if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                                last_err2 = e
                                break
                            last_err2 = e
                            time.sleep(1.0 * (attempt + 1))
                    if last_err2 is not None and not bars:
                        raise last_err2
                if not bars:
                    print(f"[{now_str()}] {sym} リプレイ用の1分足が空でした（スキップ）")
                    continue
                bars_by_symbol[sym] = bars
                meta_by_symbol[sym] = meta
            except Exception as e:
                print(f"[{now_str()}] {sym} 過去1分足の取得に失敗（スキップ）: {e}")

        if not bars_by_symbol:
            print(f"[{now_str()}] リプレイ対象のデータが1つも取得できませんでした。")
            return 2

        # NOTE:
        # - replay_dates_jst は「randomモード」で上の取得前に確定します（祝日はprobeで除外）

        # -----------------------------
        # リプレイ対象の「開始/終了時刻」を表示（JST）
        # -----------------------------
        all_bars = [b for bars in bars_by_symbol.values() for b in bars]
        start_utc = min((b.timestamp_utc for b in all_bars), default=None)
        end_utc = max((b.timestamp_utc for b in all_bars), default=None)
        print("=== Replay対象 ===")
        print(f"- 対象日(目安): {_fmt_dt_jst(start_utc)[:10]} ～ {_fmt_dt_jst(end_utc)[:10]}")
        print(f"- 開始時刻(JST): {_fmt_dt_jst(start_utc)}")
        print(f"- 終了時刻(JST): {_fmt_dt_jst(end_utc)}")
        print(f"- 対象銘柄: {', '.join(sorted(bars_by_symbol.keys()))}\n")

        # -----------------------------
        # 前日終値（previous close）を「日単位で固定」するためのテーブルを作ります。
        # - 期待仕様: change% は常に (Current - PreviousClose) / PreviousClose * 100
        # - previousClose は「前日終値」であり、日中に更新されてはいけません。
        # -----------------------------
        prev_close_fixed_by_symbol_day: dict[tuple[str, str], Optional[float]] = {}
        for sym, bars in bars_by_symbol.items():
            if not bars:
                continue
            # day_key(UTC日付) -> その日の最終close
            last_close_by_day: dict[str, float] = {}
            for b in bars:
                dk = b.timestamp_utc.strftime("%Y-%m-%d")
                last_close_by_day[dk] = float(b.close)
            days_sorted = sorted(list(last_close_by_day.keys()))
            for idx_d, dk in enumerate(days_sorted):
                if idx_d <= 0:
                    prev_close_fixed_by_symbol_day[(sym, dk)] = None
                else:
                    prev_dk = days_sorted[idx_d - 1]
                    prev_close_fixed_by_symbol_day[(sym, dk)] = float(last_close_by_day.get(prev_dk)) if prev_dk in last_close_by_day else None

        # 銘柄ごとの再生ポインタ
        idx_by_symbol: dict[str, int] = {sym: 0 for sym in bars_by_symbol.keys()}

        # base_watch: 元々のReplay監視銘柄（毎日これがベース）
        # active_watch: 当日の監視銘柄（Morning Screenで追加される可能性あり）
        # NOTE: 指数（代用ETF）は base_watch に含めない（地合い判定専用）
        base_watch: set[str] = set([s for s in bars_by_symbol.keys() if s not in set(index_syms)])
        active_watch: set[str] = set(base_watch)
        # 指数（地合い判定用）は常に進める
        for s in index_syms:
            if s in bars_by_symbol:
                active_watch.add(s)

        # 進行率用:
        # - 全銘柄・全バーの総数に対して、何本再生したかで%を出します。
        total_bars = sum(len(bars) for bars in bars_by_symbol.values())
        progressed_bars = 0

        # 初回 previousClose（取れれば使う）
        for sym, meta in meta_by_symbol.items():
            pc = meta.get("previousClose")
            prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

        try:
            # 地合いレジーム（前回値）:
            # - ADD禁止（WEAK/CRASH）に使います（当分の簡易実装: 1分遅れで適用）
            market_regime_last = "NORMAL"
            while True:
                loop_started = time.perf_counter()

                # -----------------------------
                # 1秒ぶん進める（各銘柄の1分足を1本進める）
                # -----------------------------
                quotes: list[Quote] = []
                # 重要: “当日の監視対象”だけ進めます（Morning Screenで追加された銘柄もここに入ります）
                for sym in sorted(active_watch):
                    bars = bars_by_symbol.get(sym) or []
                    i = idx_by_symbol.get(sym, 0)
                    if i >= len(bars):
                        continue
                    bar = bars[i]
                    idx_by_symbol[sym] = i + 1
                    progressed_bars += 1

                    # 日付キー（UTC日付で管理する簡易版）
                    day_key = bar.timestamp_utc.strftime("%Y-%m-%d")
                    if current_day_key.get(sym) != day_key:
                        # 日が変わったら日内状態をリセット
                        current_day_key[sym] = day_key
                        running_day_high[sym] = float("-inf")
                        running_day_volume[sym] = 0.0
                        running_vwap_pv[sym] = 0.0
                        running_vwap_v[sym] = 0.0

                        # 前日終値（previous close）を切り替え（重要: 日中に更新しない）
                        # - 期待仕様: 前日終値は「前日最終close」で固定
                        # - 取れない最初日だけは「当日開始価格」で代用し、change% を 0% から始める
                        pc_fixed = prev_close_fixed_by_symbol_day.get((sym, day_key))
                        if isinstance(pc_fixed, (int, float)) and float(pc_fixed) > 0:
                            prev_close_by_day[f"{sym}::{day_key}"] = float(pc_fixed)
                        else:
                            prev_close_by_day[f"{sym}::{day_key}"] = float(bar.close)

                    running_day_high[sym] = max(running_day_high.get(sym, float("-inf")), float(bar.high))
                    running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bar.volume)

                    # VWAP（概算）: typical price * volume の累積
                    tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
                    running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bar.volume)
                    running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bar.volume)

                    meta = meta_by_symbol.get(sym) or {}
                    currency = str(meta.get("currency") or "JPY")
                    prev_close = prev_close_by_day.get(f"{sym}::{day_key}")
                    chg = _calc_change_percent(price=float(bar.close), previous_close=prev_close)

                    # Entry上抜け（クロス）判定用に「前回価格」を保持します（Replayでも同じ判定にする）。
                    prev_price_by_symbol.setdefault(sym, None)

                    quotes.append(
                        Quote(
                            symbol=sym,
                            price=float(bar.close),
                            currency=currency,
                            previous_close=float(prev_close) if isinstance(prev_close, (int, float)) else None,
                            change_percent=float(chg) if isinstance(chg, (int, float)) else None,
                            day_high=float(running_day_high[sym]),
                            # Replayは最小限の再現でOKなので day_low は概算（running min）ではなく None にします。
                            # 朝スクリーニングで必要な値ですが、Replay要件には入っていません。
                            day_low=None,
                            volume=float(running_day_volume[sym]),  # 日内累積出来高として扱う
                            market_time_utc=bar.timestamp_utc,
                            market_cap=None,
                        )
                    )

                    # 今回の価格を保存（次ループで prev として使う）
                    prev_price_by_symbol[sym] = float(bar.close)

                # 全銘柄が再生し終えたら終了
                if not quotes:
                    # Replay終了時に「期待値検証結果」をまとめて表示します。
                    # （Discordは不要、ターミナルだけでOK）
                    if (not fast_mode) or bool(replay_fast_print_signal_details):
                        print("\n=== Replay期待値検証（signals summary） ===")
                        if not replay_signals:
                            # Windows(cp932) で絵文字が出力できず落ちることがあるため、絵文字は使いません。
                            print("- signal は0件でした（Entry上抜けが発生していません）")
                        else:
                            for s in replay_signals:
                                t_jst = _fmt_dt_jst_short(s.signal_time_utc)
                                pt_t = _fmt_dt_jst_short(s.partial_take_time_utc) if s.partial_take_time_utc else "N/A"
                                te_p = _fmt_yen(s.trailing_exit_price) if s.trailing_exit_price is not None else "N/A"
                                te_r = s.trailing_exit_reason or "N/A"
                                # final_profit_pct が未確定（HOLDで終了）なら、最後に見た価格で暫定計算します。
                                if s.final_profit_pct is None and s.entry_price > 0:
                                    fp = ((float(s.last_price_after) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                                else:
                                    fp = float(s.final_profit_pct or 0.0)
                                excluded_s = "EXCLUDED" if bool(getattr(s, "excluded_from_eval", False)) else "OK"
                                print(
                                    f"- {s.symbol} | "
                                    f"pos={getattr(s, 'position_kind', 'BASE')} | "
                                    f"signal_time_jst={t_jst} | "
                                    f"signal_price={_fmt_yen(s.signal_price)} | "
                                    f"entry_price={_fmt_yen(s.entry_price)} | "
                                    f"max_price_after={_fmt_yen(s.max_price_after)} | "
                                    f"min_price_after={_fmt_yen(s.min_price_after)} | "
                                    f"max_profit_pct={s.max_profit_pct():.2f}% | "
                                    f"max_drawdown_pct={s.max_drawdown_pct():.2f}% | "
                                    f"partial_take_hit={'Y' if s.partial_take_hit else 'N'} | "
                                    f"partial_take_time={pt_t} | "
                                    f"trailing_exit_price={te_p} | "
                                    f"trailing_exit_reason={te_r} | "
                                    f"final_profit_pct={fp:.2f}% | "
                                    f"take_hit={'Y' if s.take_hit else 'N'} | "
                                    f"stop_hit={'Y' if s.stop_hit else 'N'} | "
                                    f"result={s.result} | "
                                    f"eval={excluded_s}"
                                )
                        print()

                    # =========================
                    # 期待値検証サマリー（追加仕様）
                    # =========================
                    # 初心者向けポイント:
                    # - 「各signalの詳細」だけだと全体の傾向が掴みにくいので、
                    #   最後に集計（勝率・平均値・損益）をまとめて見られるようにします。

                    def _safe_avg(xs: list[float]) -> float:
                        """空リストでも落ちない平均（空なら0）。"""
                        return (sum(xs) / len(xs)) if xs else 0.0

                    def _signal_time_bucket_jst(t_utc: datetime) -> str:
                        """
                        時間帯分類（JST）:
                        - 前場寄り直後: 09:00〜09:30
                        - 前場:       09:30〜11:30
                        - 後場前半:   12:30〜14:00
                        - 後場後半:   14:00〜15:30
                        """
                        t = t_utc
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        jst = t.astimezone(JST)
                        hm = jst.hour * 60 + jst.minute
                        if (9 * 60) <= hm < (9 * 60 + 30):
                            return "前場寄り直後(09:00-09:30)"
                        if (9 * 60 + 30) <= hm < (11 * 60 + 30):
                            return "前場(09:30-11:30)"
                        if (12 * 60 + 30) <= hm < (14 * 60):
                            return "後場前半(12:30-14:00)"
                        if (14 * 60) <= hm < (15 * 60 + 30):
                            return "後場後半(14:00-15:30)"
                        return "時間外"

                    def _profit_pct_for_summary(s: ReplaySignalEval) -> float:
                        """
                        サマリー用の“最終損益%”を返します。

                        仕様（ユーザー要件）:
                        - final_profit_pct があればそれを採用
                        - 無い場合:
                          - WIN: take_price到達時の利益（entry基準）
                          - LOSE: stop_price到達時の損失（entry基準）
                          - HOLD: 取れなければ 0%
                        """
                        if isinstance(s.final_profit_pct, (int, float)):
                            return float(s.final_profit_pct)
                        if s.entry_price <= 0:
                            return 0.0
                        if s.result == "WIN":
                            return ((float(s.take_price) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                        if s.result == "LOSE":
                            return ((float(s.stop_price) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                        return 0.0

                    def _pnl_yen_100_shares(s: ReplaySignalEval) -> float:
                        """
                        100株あたり損益（円）を計算します。

                        仕様（ユーザー要件）:
                        - final_profit_pct がある場合:
                          pnl_yen = signal_price * 100 * final_profit_pct / 100
                        - final_profit_pct がない場合:
                          - WIN: take_price到達時の利益（円）
                          - LOSE: stop_price到達時の損失（円）
                          - HOLD: 取れなければ 0円
                        """
                        if isinstance(s.final_profit_pct, (int, float)):
                            return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
                        if s.result == "WIN":
                            return (float(s.take_price) - float(s.entry_price)) * 100.0
                        if s.result == "LOSE":
                            return (float(s.stop_price) - float(s.entry_price)) * 100.0
                        return 0.0

                    # =========================
                    # 銘柄スコアリング（Replay期待値ベース）
                    # =========================
                    # ENTRY禁止条件（ユーザー要件）:
                    # - expectancy < -5000円
                    # - 勝率 < 35%
                    # - signal数 >= 3
                    #
                    # 優先銘柄（ユーザー要件）:
                    # - expectancy > +5000円
                    # - 勝率 > 60%
                    SYMBOL_BLACKLIST_EXPECTANCY_YEN_MAX = -5000.0
                    # 実運用の禁止条件（強化）:
                    # - expectancy < -5000円
                    # - signal数 >= 2
                    SYMBOL_BLACKLIST_SIGNALS_MIN = 2
                    SYMBOL_PRIORITY_EXPECTANCY_YEN_MIN = 5000.0
                    SYMBOL_PRIORITY_WIN_RATE_PCT_MIN = 60.0

                    # 集計対象（重複エントリー制限などで除外されたsignalは除く）
                    # 未解決signalは「時間切れ決済」として扱い、exit_reason/exit_priceを確定させます。
                    # - 事故分析/Exit分析で「NO_EXIT」が混ざると比較が難しいため、Replay終端で統一します。
                    for s in replay_signals:
                        try:
                            if bool(getattr(s, "excluded_from_eval", False)):
                                continue
                            if bool(getattr(s, "resolved", False)):
                                continue
                            # 終端時点の価格で仮決済
                            setattr(s, "exit_reason", "TIME_EXIT")
                            setattr(s, "exit_price", float(getattr(s, "last_price_after", 0.0)))
                            setattr(s, "exit_time_utc", datetime.now(tz=timezone.utc))
                        except Exception:
                            continue

                    eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                    excluded_n = len(replay_signals) - len(eval_signals)

                    # EVAL_FILTER_DEBUG（ユーザー要望）
                    # - signal生成後に「どこで0になるか」を切り分けるため、除外フィルタの前後を保存
                    eval_filter_debug: dict[str, Any] = {
                        "before_count": int(len(replay_signals)),
                        "after_count": int(len(eval_signals)),
                        "excluded": [],
                    }
                    try:
                        ex_list: list[dict[str, Any]] = []
                        for ss in replay_signals:
                            if bool(getattr(ss, "excluded_from_eval", False)):
                                ex_list.append(
                                    {
                                        "signal_id": str(getattr(ss, "signal_id", "") or ""),
                                        "excluded_reason": str(getattr(ss, "excluded_reason", "") or ""),
                                    }
                                )
                            if len(ex_list) >= 200:
                                break
                        eval_filter_debug["excluded"] = ex_list
                    except Exception:
                        pass

                    total = len(eval_signals)
                    win = sum(1 for s in eval_signals if s.result == "WIN")
                    lose = sum(1 for s in eval_signals if s.result == "LOSE")
                    hold = sum(1 for s in eval_signals if s.result == "HOLD")
                    win_rate = (win / total * 100.0) if total > 0 else 0.0

                    # 利益率/損失率の平均（WIN/LOSEに分けて集計）
                    profit_pcts = [_profit_pct_for_summary(s) for s in eval_signals if s.result == "WIN"]
                    loss_pcts = [_profit_pct_for_summary(s) for s in eval_signals if s.result == "LOSE"]
                    avg_profit_pct = _safe_avg(profit_pcts)
                    avg_loss_pct = _safe_avg(loss_pcts)

                    # 最大利益率 / 最大下落率の平均（全signal対象）
                    max_profit_pcts = [float(s.max_profit_pct()) for s in eval_signals]
                    max_drawdown_pcts = [float(s.max_drawdown_pct()) for s in eval_signals]
                    avg_max_profit_pct = _safe_avg(max_profit_pcts)
                    avg_max_drawdown_pct = _safe_avg(max_drawdown_pcts)

                    pnls = [_pnl_yen_100_shares(s) for s in eval_signals]
                    total_pnl_yen = sum(pnls)
                    avg_pnl_yen = (total_pnl_yen / total) if total > 0 else 0.0
                    avg_profit_pct_all = _safe_avg([_profit_pct_for_summary(s) for s in eval_signals]) if total > 0 else 0.0

                    def _expectancy_yen_100_shares(xs: list[ReplaySignalEval]) -> float:
                        """
                        Expectancy（100株あたり期待値, 円）:
                        - ここでは「1signalあたり平均損益（円）」を期待値として扱います。
                        """
                        if not xs:
                            return 0.0
                        pn = [_pnl_yen_100_shares(s) for s in xs]
                        return float(sum(pn)) / float(len(pn))

                    # =========================
                    # BASE / ADD1 / ADD2 別サマリー（追加仕様）
                    # =========================
                    by_pos: dict[str, list[ReplaySignalEval]] = {}
                    for s in eval_signals:
                        pk = str(getattr(s, "position_kind", "BASE") or "BASE")
                        by_pos.setdefault(pk, []).append(s)

                    print("【期待値検証サマリー】")
                    print(f"総signal数: {total}")
                    if excluded_n > 0:
                        print(f"除外signal数: {excluded_n}（例: 同一銘柄1日1回モードなど）")
                    print(f"WIN数: {win}")
                    print(f"LOSE数: {lose}")
                    print(f"HOLD数: {hold}")
                    print(f"勝率: {win_rate:.1f}%")
                    print(f"平均利益率: {avg_profit_pct:.2f}%")
                    print(f"平均損失率: {avg_loss_pct:.2f}%")
                    print(f"平均最大利益率: {avg_max_profit_pct:.2f}%")
                    print(f"平均最大下落率: {avg_max_drawdown_pct:.2f}%")
                    print(f"100株あたり合計損益: {total_pnl_yen:+,.0f}円")
                    print(f"100株あたり平均損益: {avg_pnl_yen:+,.0f}円")
                    print(f"expectancy（100株/1signal）: {_expectancy_yen_100_shares(eval_signals):+,.0f}円")
                    print()

                    # =========================
                    # 銘柄別サマリー（追加仕様）
                    # =========================
                    by_symbol: dict[str, list[ReplaySignalEval]] = {}
                    for s in eval_signals:
                        by_symbol.setdefault(s.symbol, []).append(s)

                    print("【銘柄別サマリー】")
                    for sym in sorted(by_symbol.keys()):
                        xs = by_symbol[sym]
                        t = len(xs)
                        w = sum(1 for s in xs if s.result == "WIN")
                        l = sum(1 for s in xs if s.result == "LOSE")
                        h = sum(1 for s in xs if s.result == "HOLD")
                        wr = (w / t * 100.0) if t > 0 else 0.0
                        pnl = sum(_pnl_yen_100_shares(s) for s in xs)
                        print(sym)
                        print(f"signal数: {t}")
                        print(f"WIN: {w}")
                        print(f"LOSE: {l}")
                        print(f"HOLD: {h}")
                        print(f"勝率: {wr:.1f}%")
                        print(f"100株損益: {pnl:+,.0f}円")
                        print()

                    # =========================
                    # 時間帯別サマリー（追加仕様）
                    # =========================
                    by_bucket: dict[str, list[ReplaySignalEval]] = {}
                    for s in eval_signals:
                        by_bucket.setdefault(_signal_time_bucket_jst(s.signal_time_utc), []).append(s)

                    # 表示順を固定（時間外は最後）
                    bucket_order = [
                        "前場寄り直後(09:00-09:30)",
                        "前場(09:30-11:30)",
                        "後場前半(12:30-14:00)",
                        "後場後半(14:00-15:30)",
                        "時間外",
                    ]

                    print("【時間帯別サマリー】")
                    for b in bucket_order:
                        xs = by_bucket.get(b) or []
                        if not xs:
                            continue
                        t = len(xs)
                        w = sum(1 for s in xs if s.result == "WIN")
                        l = sum(1 for s in xs if s.result == "LOSE")
                        wr = (w / t * 100.0) if t > 0 else 0.0
                        pnl = sum(_pnl_yen_100_shares(s) for s in xs)
                        print(b)
                        print(f"signal数: {t}")
                        print(f"WIN: {w}")
                        print(f"LOSE: {l}")
                        print(f"勝率: {wr:.1f}%")
                        print(f"100株損益: {pnl:+,.0f}円")
                        print()

                    # =========================
                    # BASE / ADD1 / ADD2 別サマリー（追加仕様）
                    # =========================
                    print("【BASE/ADD別サマリー】")
                    for pk in ["BASE", "ADD1", "ADD2"]:
                        xs = by_pos.get(pk) or []
                        if not xs:
                            continue
                        t = len(xs)
                        w = sum(1 for s in xs if s.result == "WIN")
                        l = sum(1 for s in xs if s.result == "LOSE")
                        h = sum(1 for s in xs if s.result == "HOLD")
                        wr = (w / t * 100.0) if t > 0 else 0.0
                        pnl = sum(_pnl_yen_100_shares(s) for s in xs)
                        exp_y = _expectancy_yen_100_shares(xs)
                        print(pk)
                        print(f"signal数: {t}")
                        print(f"WIN/LOSE/HOLD: {w}/{l}/{h}")
                        print(f"勝率: {wr:.1f}%")
                        print(f"100株損益: {pnl:+,.0f}円")
                        print(f"expectancy（100株/1signal）: {exp_y:+,.0f}円")
                        print()

                    # =========================
                    # Morning Screen Replay Summary（追加仕様）
                    # =========================
                    # ターミナル表示のみでOK（Discordは不要）
                    if ms_time is not None:
                        print("【Morning Screen Replay Summary】")
                        if not ms_daily:
                            print("- Morning Screen は1度も実行されませんでした（指定時刻が再生範囲に無い等）")
                            print()
                        else:
                            def _pnl_yen_100_shares_ms(s: ReplaySignalEval) -> float:
                                if isinstance(s.final_profit_pct, (int, float)):
                                    return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
                                if s.result == "WIN":
                                    return (float(s.take_price) - float(s.entry_price)) * 100.0
                                if s.result == "LOSE":
                                    return (float(s.stop_price) - float(s.entry_price)) * 100.0
                                return 0.0

                            by_day_stats: dict[str, dict[str, Any]] = {
                                day: {
                                    "selected": {"signals": 0, "win": 0, "lose": 0, "hold": 0, "pnl": 0.0},
                                    "carryover": {"signals": 0, "win": 0, "lose": 0, "hold": 0, "pnl": 0.0},
                                }
                                for day in ms_daily.keys()
                            }

                            by_symbol_ms: dict[str, dict[str, Any]] = {}
                            for day, info in ms_daily.items():
                                for sym in (info.get("symbols") or []):
                                    agg = by_symbol_ms.setdefault(
                                        sym,
                                        {
                                            "picked": 0,
                                            "signals": 0,
                                            "win": 0,
                                            "lose": 0,
                                            "hold": 0,
                                            "pnl": 0.0,
                                            # 継続監視（追加仕様）
                                            "carry_picked": 0,
                                            "carry_signals": 0,
                                            "carry_win": 0,
                                            "carry_lose": 0,
                                            "carry_hold": 0,
                                            "carry_pnl": 0.0,
                                        },
                                    )
                                    agg["picked"] += 1
                                for sym in (info.get("carryover_symbols") or []):
                                    agg = by_symbol_ms.setdefault(
                                        sym,
                                        {
                                            "picked": 0,
                                            "signals": 0,
                                            "win": 0,
                                            "lose": 0,
                                            "hold": 0,
                                            "pnl": 0.0,
                                            "carry_picked": 0,
                                            "carry_signals": 0,
                                            "carry_win": 0,
                                            "carry_lose": 0,
                                            "carry_hold": 0,
                                            "carry_pnl": 0.0,
                                        },
                                    )
                                    agg["carry_picked"] += 1

                            # Morning Screen Summary も「期待値検証の集計対象」に合わせます
                            # （= 同一銘柄1日1回モード等で excluded_from_eval な signal は除外）
                            ms_eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                            for s in ms_eval_signals:
                                t = s.signal_time_utc
                                if t.tzinfo is None:
                                    t = t.replace(tzinfo=timezone.utc)
                                day_jst = t.astimezone(JST).strftime("%Y-%m-%d")
                                info = ms_daily.get(day_jst)
                                if not info:
                                    continue
                                selected_set = set(info.get("symbols") or [])
                                carry_set = set(info.get("carryover_symbols") or [])

                                # 分類ルール（初心者向けにシンプルに）:
                                # - carryover に入っている銘柄は carryover 側へ寄せる（重複カウント防止）
                                # - selected は “screen_time以降” のsignalだけを対象にする
                                bucket: Optional[str] = None
                                if s.symbol in carry_set:
                                    bucket = "carryover"
                                elif s.symbol in selected_set:
                                    st_utc = info.get("screen_time_utc")
                                    if isinstance(st_utc, datetime):
                                        st2 = st_utc
                                        if st2.tzinfo is None:
                                            st2 = st2.replace(tzinfo=timezone.utc)
                                        if t < st2:
                                            continue
                                    bucket = "selected"
                                else:
                                    continue

                                dd = by_day_stats.get(day_jst)
                                if dd is not None and bucket is not None:
                                    ddb = dd.get(bucket) or {}
                                    ddb["signals"] = int(ddb.get("signals") or 0) + 1
                                    ddb["pnl"] = float(ddb.get("pnl") or 0.0) + float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        ddb["win"] = int(ddb.get("win") or 0) + 1
                                    elif s.result == "LOSE":
                                        ddb["lose"] = int(ddb.get("lose") or 0) + 1
                                    else:
                                        ddb["hold"] = int(ddb.get("hold") or 0) + 1
                                    dd[bucket] = ddb

                                agg = by_symbol_ms.setdefault(
                                    s.symbol,
                                    {
                                        "picked": 0,
                                        "signals": 0,
                                        "win": 0,
                                        "lose": 0,
                                        "hold": 0,
                                        "pnl": 0.0,
                                        "carry_picked": 0,
                                        "carry_signals": 0,
                                        "carry_win": 0,
                                        "carry_lose": 0,
                                        "carry_hold": 0,
                                        "carry_pnl": 0.0,
                                    },
                                )
                                if bucket == "carryover":
                                    agg["carry_signals"] += 1
                                    agg["carry_pnl"] += float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        agg["carry_win"] += 1
                                    elif s.result == "LOSE":
                                        agg["carry_lose"] += 1
                                    else:
                                        agg["carry_hold"] += 1
                                else:
                                    agg["signals"] += 1
                                    agg["pnl"] += float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        agg["win"] += 1
                                    elif s.result == "LOSE":
                                        agg["lose"] += 1
                                    else:
                                        agg["hold"] += 1

                            for day in sorted(ms_daily.keys()):
                                info = ms_daily[day]
                                hhmm = str(info.get("hhmm") or "")
                                syms = list(info.get("symbols") or [])
                                carry_syms = list(info.get("carryover_symbols") or [])
                                scores = dict(info.get("scores") or {})
                                stt = by_day_stats.get(day) or {}
                                sel = stt.get("selected") or {}
                                car = stt.get("carryover") or {}
                                picked = ", ".join([f"{s}({int(scores.get(s, 0))})" for s in syms]) if syms else "(none)"
                                carry_picked = ", ".join(carry_syms) if carry_syms else "(none)"
                                print(f"- 日付: {day}")
                                print(f"  指定時刻: {hhmm} JST")
                                print(f"  当日選出(TOP10): {picked}")
                                print(f"  前日継続: {carry_picked}")
                                print(
                                    "  [当日選出] "
                                    f"signal数: {int(sel.get('signals') or 0)}  "
                                    f"WIN/LOSE/HOLD: {int(sel.get('win') or 0)}/{int(sel.get('lose') or 0)}/{int(sel.get('hold') or 0)}  "
                                    f"100株損益: {float(sel.get('pnl') or 0.0):+,.0f}円"
                                )
                                print(
                                    "  [前日継続] "
                                    f"signal数: {int(car.get('signals') or 0)}  "
                                    f"WIN/LOSE/HOLD: {int(car.get('win') or 0)}/{int(car.get('lose') or 0)}/{int(car.get('hold') or 0)}  "
                                    f"100株損益: {float(car.get('pnl') or 0.0):+,.0f}円"
                                )
                                print()

                            print("【Morning Screen 銘柄別サマリー】")
                            for sym in sorted(by_symbol_ms.keys()):
                                a = by_symbol_ms[sym]
                                picked_n = int(a.get("picked") or 0)
                                sig_n = int(a.get("signals") or 0)
                                w = int(a.get("win") or 0)
                                pnl = float(a.get("pnl") or 0.0)
                                wr = (w / sig_n * 100.0) if sig_n > 0 else 0.0
                                carry_picked_n = int(a.get("carry_picked") or 0)
                                carry_sig_n = int(a.get("carry_signals") or 0)
                                carry_w = int(a.get("carry_win") or 0)
                                carry_pnl = float(a.get("carry_pnl") or 0.0)
                                carry_wr = (carry_w / carry_sig_n * 100.0) if carry_sig_n > 0 else 0.0
                                print(sym)
                                print(f"選出回数: {picked_n}")
                                print(f"signal数: {sig_n}")
                                print(f"勝率: {wr:.1f}%")
                                print(f"100株損益: {pnl:+,.0f}円")
                                print(f"継続監視回数: {carry_picked_n}")
                                print(f"継続監視時signal数: {carry_sig_n}")
                                print(f"継続監視時勝率: {carry_wr:.1f}%")
                                print(f"継続監視時100株損益: {carry_pnl:+,.0f}円")
                                print()

                    # =========================
                    # Replay結果の自動保存（追加仕様）
                    # =========================
                    # - results/ に txt と json を保存します。
                    # - txt は「ターミナル表示に近い読み物」
                    # - json は「後処理・分析用の機械可読」
                    try:
                        script_dir = os.path.dirname(os.path.abspath(__file__))
                        # =========================
                        # results 保存先フォルダ（repeat時に整理）
                        # =========================
                        # 初心者向けポイント:
                        # - --replay-repeat を使うとファイルが増えるので、
                        #   「同じロット（同じrepeat実行）」の結果は1つのフォルダにまとめます。
                        # - repeat時は必ず専用フォルダ results/replay_<range>_<batch_stamp>/ にまとめます（ユーザー要件）。
                        #   ※repeatフォルダの生成は main(TEST_REPLAY_MODE) 側で行い、ここでは二重にネストしない。
                        results_dir = os.path.join(script_dir, "results")
                        if str(replay_output_subdir or "").strip():
                            results_dir = os.path.join(results_dir, str(replay_output_subdir).strip())

                        # ファイル名（JST）
                        saved_at_jst = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
                        # repeatロットの中では時刻を固定して「同じフォルダ/同じ接頭辞」に揃えます
                        # NOTE: batch_stamp は run_replay 冒頭で必ず定義済み
                        start_jst = _fmt_dt_jst(start_utc) if start_utc else "NA"
                        end_jst = _fmt_dt_jst(end_utc) if end_utc else "NA"
                        # 通常の 1d/5d Replay 表示には影響させない（ランダム時だけ切替）
                        replay_range_label = str(replay_range)
                        if int(replay_random_days or 0) > 0:
                            replay_range_label = f"random_{int(replay_random_days)}d"
                        if replay_range_label.startswith("random_"):
                            # 例: replay_summary_random_5d_20260507_232500_run01
                            if int(replay_repeat_total or 0) > 1:
                                run_s = f"run{int(replay_repeat_run_no):02d}"
                                name_base = f"replay_summary_{replay_range_label}_{batch_stamp}_{run_s}"
                            else:
                                name_base = f"replay_summary_{replay_range_label}_{saved_at_jst}"
                        else:
                            name_base = f"replay_{saved_at_jst}_range-{replay_range_label}"

                        os.makedirs(results_dir, exist_ok=True)

                        def _dt_iso(dt: Optional[Any]) -> Optional[str]:
                            if dt is None:
                                return None
                            # 旧データ互換: exit_time_utc が str で入っている場合
                            if isinstance(dt, str):
                                return dt
                            t = dt
                            if t.tzinfo is None:
                                t = t.replace(tzinfo=timezone.utc)
                            return t.isoformat()

                        def _signal_to_dict(s: ReplaySignalEval) -> dict[str, Any]:
                            bucket = _signal_time_bucket_jst(s.signal_time_utc)
                            day_jst = _day_jst_str(s.signal_time_utc)
                            # HOLDでfinalが無い場合は、ターミナル表示と同じ暫定計算に寄せる
                            if s.final_profit_pct is None and s.entry_price > 0:
                                fp = ((float(s.last_price_after) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                            else:
                                fp = float(s.final_profit_pct) if isinstance(s.final_profit_pct, (int, float)) else None
                            return {
                                "signal_id": str(getattr(s, "signal_id", "") or ""),
                                "symbol": s.symbol,
                                "position_kind": str(getattr(s, "position_kind", "BASE") or "BASE"),
                                "exit_style": str(getattr(s, "exit_style", "") or ""),
                                "market_regime": str(getattr(s, "market_regime", "") or ""),
                                "market_state": str(getattr(s, "market_state", "") or str(getattr(s, "market_regime", "") or "")),
                                "market_reasons": str(getattr(s, "market_reasons", "") or ""),
                                "crash_blocked": bool(getattr(s, "crash_blocked", False)),
                                # TOPIX debug (signal候補時点)
                                "topix_fetch_ok": bool(getattr(s, "topix_fetch_ok", False)),
                                # topix_raw は「価格レベル」（例: 2783.52）
                                "topix_raw": (
                                    float(getattr(s, "topix_raw", 0.0))
                                    if isinstance(getattr(s, "topix_raw", None), (int, float))
                                    else None
                                ),
                                "topix_prev_close": (
                                    float(getattr(s, "topix_prev_close", 0.0))
                                    if isinstance(getattr(s, "topix_prev_close", None), (int, float))
                                    else None
                                ),
                                # topix_pct は「CRASH比較に使う%値」
                                "topix_pct": (
                                    float(getattr(s, "topix_pct", 0.0))
                                    if isinstance(getattr(s, "topix_pct", None), (int, float))
                                    else None
                                ),
                                "topix_chg_pct_raw": (
                                    float(getattr(s, "topix_chg_pct_raw", 0.0))
                                    if isinstance(getattr(s, "topix_chg_pct_raw", None), (int, float))
                                    else None
                                ),
                                "topix_chg_pct": (
                                    float(getattr(s, "topix_chg_pct", 0.0))
                                    if isinstance(getattr(s, "topix_chg_pct", None), (int, float))
                                    else None
                                ),
                                "topix_chg_ok": bool(getattr(s, "topix_chg_ok", True)),
                                "topix_crash_threshold": (
                                    float(getattr(s, "topix_crash_threshold", 0.0))
                                    if isinstance(getattr(s, "topix_crash_threshold", None), (int, float))
                                    else float(CRASH_TOPIX_CHG_PCT_MAX)
                                ),
                                "topix_weak_threshold": (
                                    float(getattr(s, "topix_weak_threshold", 0.0))
                                    if isinstance(getattr(s, "topix_weak_threshold", None), (int, float))
                                    else float(WEAK_TOPIX_CHG_PCT_MAX)
                                ),
                                "market_blocked": bool(getattr(s, "market_blocked", False)),
                                "blocked_reason": str(getattr(s, "blocked_reason", "") or ""),
                                "entry_allowed_by_market": bool(getattr(s, "entry_allowed_by_market", True)),
                                "entry_allowed": bool(getattr(s, "entry_allowed", True)),
                                "signal_time_utc": _dt_iso(s.signal_time_utc),
                                "signal_time_jst": _fmt_dt_jst_short(s.signal_time_utc),
                                "day_jst": day_jst,
                                "time_bucket_jst": bucket,
                                "signal_price": float(s.signal_price),
                                "entry_price": float(s.entry_price),
                                "stop_price": float(s.stop_price),
                                "take_price": float(s.take_price),
                                "max_price_after": float(s.max_price_after),
                                "min_price_after": float(s.min_price_after),
                                "last_price_after": float(s.last_price_after),
                                "max_profit_pct": float(s.max_profit_pct()),
                                "max_drawdown_pct": float(s.max_drawdown_pct()),
                                "take_hit": bool(s.take_hit),
                                "stop_hit": bool(s.stop_hit),
                                "partial_take_hit": bool(s.partial_take_hit),
                                "partial_take_time_utc": _dt_iso(s.partial_take_time_utc),
                                "trailing_exit_price": float(s.trailing_exit_price) if s.trailing_exit_price is not None else None,
                                "trailing_exit_time_utc": _dt_iso(s.trailing_exit_time_utc),
                                "trailing_exit_reason": str(s.trailing_exit_reason or ""),
                                "exit_reason": str(getattr(s, "exit_reason", "") or ""),
                                "exit_price": (float(getattr(s, "exit_price", 0.0)) if isinstance(getattr(s, "exit_price", None), (int, float)) else None),
                                "exit_time_utc": _dt_iso(getattr(s, "exit_time_utc", None)),
                                "final_profit_pct": fp,
                                "result": str(s.result),
                                "excluded_from_eval": bool(getattr(s, "excluded_from_eval", False)),
                                "excluded_reason": str(getattr(s, "excluded_reason", "") or ""),
                                "pnl_yen_100_shares": float(_pnl_yen_100_shares(s)),
                                # 事故分析用
                                "rsi14": (float(getattr(s, "rsi14", 0.0)) if isinstance(getattr(s, "rsi14", None), (int, float)) else None),
                                "atr14": (float(getattr(s, "atr14", 0.0)) if isinstance(getattr(s, "atr14", None), (int, float)) else None),
                                "atr_pct": (float(getattr(s, "atr_pct", 0.0)) if isinstance(getattr(s, "atr_pct", None), (int, float)) else None),
                                "vwap_distance_pct": (
                                    float(getattr(s, "vwap_distance_pct", 0.0))
                                    if isinstance(getattr(s, "vwap_distance_pct", None), (int, float))
                                    else None
                                ),
                                "relative_strength_vs_topix_pct": (
                                    float(getattr(s, "relative_strength_vs_topix_pct", 0.0))
                                    if isinstance(getattr(s, "relative_strength_vs_topix_pct", None), (int, float))
                                    else None
                                ),
                                "vol_spike_ratio": (
                                    float(getattr(s, "vol_spike_ratio", 0.0))
                                    if isinstance(getattr(s, "vol_spike_ratio", None), (int, float))
                                    else None
                                ),
                                "suggested_block_reasons": str(getattr(s, "suggested_block_reasons", "") or ""),
                            }

                        def _agg_stats(xs: list[ReplaySignalEval]) -> dict[str, Any]:
                            t = len(xs)
                            w = sum(1 for s in xs if s.result == "WIN")
                            l = sum(1 for s in xs if s.result == "LOSE")
                            h = sum(1 for s in xs if s.result == "HOLD")
                            wr = (w / t * 100.0) if t > 0 else 0.0
                            pnl = float(sum(_pnl_yen_100_shares(s) for s in xs))
                            exp_y = _expectancy_yen_100_shares(xs)
                            avg_fp = _safe_avg([_profit_pct_for_summary(s) for s in xs]) if t > 0 else 0.0
                            dd_pcts = [float(s.max_drawdown_pct()) for s in xs] if xs else []
                            worst_dd_pct = float(min(dd_pcts)) if dd_pcts else 0.0
                            worst_dd_yen_100 = 0.0
                            if xs:
                                # 近似: signal_price × DD%
                                worst_dd_yen_100 = float(xs[0].signal_price) * 100.0 * (float(worst_dd_pct) / 100.0)
                            return {
                                "signals": int(t),
                                "win": int(w),
                                "lose": int(l),
                                "hold": int(h),
                                "win_rate_pct": float(wr),
                                "pnl_yen_100_shares": float(pnl),
                                "avg_pnl_yen_100_shares_per_signal": float((pnl / float(t)) if t > 0 else 0.0),
                                "expectancy_yen_100_shares_per_signal": float(exp_y),
                                "expectancy_profit_pct_per_signal": float(avg_fp),
                                "max_drawdown_pct_worst": float(worst_dd_pct),
                                "max_drawdown_yen_100_shares_worst_est": float(worst_dd_yen_100),
                            }

                        # =========================
                        # ADD分析用の集計（追加仕様）
                        # =========================
                        # 初心者向けポイント:
                        # - 「ADDした方が良いのか？」を比較できるように、
                        #   “銘柄×日” 単位で ADD回数(0/1/2) と損益を集計します。
                        by_day_symbol_all: dict[tuple[str, str], list[ReplaySignalEval]] = {}
                        for s in eval_signals:
                            d = _day_jst_str(s.signal_time_utc)
                            by_day_symbol_all.setdefault((d, s.symbol), []).append(s)

                        add_count_by_day_symbol_from_signals: dict[tuple[str, str], int] = {}
                        for k, xs in by_day_symbol_all.items():
                            c = sum(1 for s in xs if str(getattr(s, "position_kind", "BASE") or "BASE").startswith("ADD"))
                            add_count_by_day_symbol_from_signals[k] = int(c)

                        # ADD回数別（0/1/2）で、銘柄日単位の損益を集計
                        add_bucket_stats: dict[str, dict[str, Any]] = {}
                        for k, xs in by_day_symbol_all.items():
                            add_n = int(add_count_by_day_symbol_from_signals.get(k, 0))
                            bucket = str(add_n)
                            agg = add_bucket_stats.setdefault(
                                bucket,
                                {"daysymbols": 0, "signals": 0, "win": 0, "lose": 0, "hold": 0, "pnl": 0.0},
                            )
                            agg["daysymbols"] += 1
                            agg["signals"] += len(xs)
                            agg["pnl"] += float(sum(_pnl_yen_100_shares(s) for s in xs))
                            for s in xs:
                                if s.result == "WIN":
                                    agg["win"] += 1
                                elif s.result == "LOSE":
                                    agg["lose"] += 1
                                else:
                                    agg["hold"] += 1

                        # ADDあり vs なし（signal単位）の比較
                        add_yes_signals: list[ReplaySignalEval] = []
                        add_no_signals: list[ReplaySignalEval] = []
                        for s in eval_signals:
                            d = _day_jst_str(s.signal_time_utc)
                            if int(add_count_by_day_symbol_from_signals.get((d, s.symbol), 0)) > 0:
                                add_yes_signals.append(s)
                            else:
                                add_no_signals.append(s)

                        # ADD失敗銘柄ランキング（ADDポジションの損益が悪い順）
                        add_pnl_by_symbol: dict[str, float] = {}
                        add_lose_by_symbol: dict[str, int] = {}
                        add_sig_by_symbol: dict[str, int] = {}
                        for s in eval_signals:
                            pk = str(getattr(s, "position_kind", "BASE") or "BASE")
                            if not pk.startswith("ADD"):
                                continue
                            add_sig_by_symbol[s.symbol] = int(add_sig_by_symbol.get(s.symbol, 0)) + 1
                            add_pnl_by_symbol[s.symbol] = float(add_pnl_by_symbol.get(s.symbol, 0.0)) + float(_pnl_yen_100_shares(s))
                            if s.result == "LOSE":
                                add_lose_by_symbol[s.symbol] = int(add_lose_by_symbol.get(s.symbol, 0)) + 1

                        add_fail_rank = sorted(
                            add_pnl_by_symbol.items(),
                            key=lambda kv: float(kv[1]),
                        )[:10]

                        # =========================
                        # 地合いフィルタの効果（追加仕様）
                        # =========================
                        blocked_signals = [
                            s
                            for s in replay_signals
                            if bool(getattr(s, "excluded_from_eval", False))
                            and str(getattr(s, "excluded_reason", "") or "").startswith("MARKET_CRASH")
                            and str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE"
                        ]
                        weak_reject_signals = [
                            s
                            for s in replay_signals
                            if bool(getattr(s, "excluded_from_eval", False))
                            and str(getattr(s, "excluded_reason", "") or "").startswith("WEAK_REJECT")
                            and str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE"
                        ]
                        off_signals = list(eval_signals) + list(blocked_signals)
                        blocked_pnl = float(sum(_pnl_yen_100_shares(s) for s in blocked_signals))
                        avoided_loss_yen = float(max(0.0, -blocked_pnl))

                        def _by_regime(xs: list[ReplaySignalEval]) -> dict[str, list[ReplaySignalEval]]:
                            d: dict[str, list[ReplaySignalEval]] = {"NORMAL": [], "WEAK": [], "CRASH": []}
                            for s in xs:
                                r = str(getattr(s, "market_regime", "") or "")
                                if r not in d:
                                    r = "NORMAL"
                                d[r].append(s)
                            return d

                        regime_on = _by_regime(eval_signals)
                        regime_all = _by_regime([s for s in replay_signals if str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE"])

                        # json
                        report: dict[str, Any] = {
                            "meta": {
                                "saved_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                                "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                                "replay_range": replay_range_label,
                                "replay_speed": speed_s,
                                "watch_symbols": sorted(list(base_watch)),
                                "start_time_jst": start_jst,
                                "end_time_jst": end_jst,
                                "replay_dates": list(replay_dates_jst),
                                "replay_random_days": int(replay_random_days or 0),
                                "replay_random_months": int(replay_random_months or 0),
                                "replay_seed": int(replay_seed) if replay_seed is not None else None,
                                "repeat_run_no": int(replay_repeat_run_no or 0),
                                "repeat_total": int(replay_repeat_total or 0),
                                "batch_stamp": str(batch_stamp),
                                "morning_screen_hhmm_jst": (replay_morning_screen_hhmm or "").strip(),
                                "one_trade_per_symbol_per_day": bool(one_trade_per_symbol_per_day),
                            },
                            # market_state 判定の生ログ（signal生成とは独立）
                            "market_debug": {
                                "rows_total": int(len(market_debug_rows)),
                                "truncated": bool(len(market_debug_rows) >= int(MARKET_DEBUG_MAX_ROWS)),
                                # all_runs.txt に埋め込むため、先頭だけ保存（詳細は runXX.txt 側にも残る）
                                "rows": (market_debug_rows[:500] if isinstance(market_debug_rows, list) else []),
                            },
                            "crossed_debug": {
                                "rows_total": int(len(crossed_debug_rows)),
                                "truncated": bool(len(crossed_debug_rows) >= int(CROSSED_DEBUG_MAX_ROWS)),
                                "rows": (crossed_debug_rows[:500] if isinstance(crossed_debug_rows, list) else []),
                            },
                            "pipeline_debug": dict(pipeline_debug),
                            "continue_reason_counts": dict(continue_reason_counts),
                            "append_signal_debug": {
                                "rows_total": int(len(append_signal_debug_rows)),
                                "rows": (append_signal_debug_rows[:500] if isinstance(append_signal_debug_rows, list) else []),
                            },
                            "pre_signal_object_debug": {
                                "rows_total": int(len(pre_signal_object_debug_rows)),
                                "rows": (pre_signal_object_debug_rows[:500] if isinstance(pre_signal_object_debug_rows, list) else []),
                            },
                            "post_signal_object_debug": {
                                "rows_total": int(len(post_signal_object_debug_rows)),
                                "rows": (post_signal_object_debug_rows[:500] if isinstance(post_signal_object_debug_rows, list) else []),
                            },
                            "continue_before_append": {
                                "rows_total": int(len(continue_before_append_rows)),
                                "rows": (continue_before_append_rows[:500] if isinstance(continue_before_append_rows, list) else []),
                            },
                            "exception_before_append_trace": {
                                "rows_total": int(len(exception_before_append_traces)),
                                "rows": (exception_before_append_traces[:10] if isinstance(exception_before_append_traces, list) else []),
                            },
                            "exception_before_append_trace_texts": (exception_before_append_trace_texts[:10] if isinstance(exception_before_append_trace_texts, list) else []),
                            "trace_capture_failed_count": int(trace_capture_failed_count),
                            "eval_filter_debug": dict(eval_filter_debug),
                            "overall_summary": {
                                "all_signals_detected": int(len(replay_signals)),
                                "signals_in_eval": int(len(eval_signals)),
                                "signals_excluded": int(excluded_n),
                                "stats": _agg_stats(eval_signals),
                                "avg_profit_pct_win_only": float(avg_profit_pct),
                                "avg_loss_pct_lose_only": float(avg_loss_pct),
                                "avg_max_profit_pct": float(avg_max_profit_pct),
                                "avg_max_drawdown_pct": float(avg_max_drawdown_pct),
                            },
                            "by_symbol_summary": {sym: _agg_stats(xs) for sym, xs in by_symbol.items()},
                            "by_time_bucket_summary": {b: _agg_stats(by_bucket.get(b) or []) for b in bucket_order if (by_bucket.get(b) or [])},
                            "by_position_kind_summary": {
                                pk: _agg_stats(by_pos.get(pk) or []) for pk in ["BASE", "ADD1", "ADD2"] if (by_pos.get(pk) or [])
                            },
                            "add_analysis": {
                                "add_count_bucket_by_day_symbol": {
                                    f"{k[0]}::{k[1]}": int(v) for k, v in add_count_by_day_symbol_from_signals.items()
                                },
                                "by_add_count_bucket": add_bucket_stats,
                                "compare_add_yes_vs_no": {
                                    "add_yes": _agg_stats(add_yes_signals),
                                    "add_no": _agg_stats(add_no_signals),
                                },
                                "add_fail_symbol_ranking": [
                                    {
                                        "symbol": sym,
                                        "add_signals": int(add_sig_by_symbol.get(sym, 0)),
                                        "add_lose": int(add_lose_by_symbol.get(sym, 0)),
                                        "add_pnl_yen_100_shares": float(pnl),
                                    }
                                    for sym, pnl in add_fail_rank
                                ],
                            },
                            "market_filter": {
                                "enabled": True,
                                "conditions": {
                                    "CRASH_TOPIX_CHG_PCT_MAX": float(CRASH_TOPIX_CHG_PCT_MAX),
                                    "CRASH_RISING_RATIO_MAX": float(CRASH_RISING_RATIO_MAX),
                                    "CRASH_HIGH_RATIO_MAX": float(CRASH_HIGH_RATIO_MAX),
                                    "MARKET_RISING_RATIO_MIN": float(MARKET_RISING_RATIO_MIN),
                                    "MARKET_ENTRY_FAIL_RATE_30M_MAX": float(MARKET_ENTRY_FAIL_RATE_30M_MAX),
                                    "MARKET_HIGH_UPDATE_RATIO_MIN": float(MARKET_HIGH_UPDATE_RATIO_MIN),
                                    "MARKET_VWAP_BELOW_RATIO_MAX": float(MARKET_VWAP_BELOW_RATIO_MAX),
                                    "note": "CRASHはTOPIX<=閾値 または (上昇銘柄割合/高値付近割合) の複合で判定。TOPIX<0%はWEAK理由。",
                                },
                                "signal_candidate_count": int(signal_candidate_count),
                                "blocked_entry_count": int(blocked_entry_count),
                                "blocked_reason_ranking": sorted(
                                    [{"reason": k, "count": int(v)} for k, v in blocked_reason_counts.items()],
                                    key=lambda x: int(x.get("count") or 0),
                                    reverse=True,
                                ),
                                "weak_reject_count": int(len(weak_reject_signals)),
                                "blocked_signals_stats": _agg_stats(blocked_signals),
                                "performance_filter_on": _agg_stats(eval_signals),
                                "performance_filter_off": _agg_stats(off_signals),
                                "blocked_pnl_yen_100_shares": float(blocked_pnl),
                                "avoided_loss_yen_100_shares": float(avoided_loss_yen),
                                "by_regime_on": {k: _agg_stats(v) for k, v in regime_on.items() if v},
                                "by_regime_all_base": {k: _agg_stats(v) for k, v in regime_all.items() if v},
                            },
                            "reject_reason_ranking": sorted(
                                [{"reason": k, "count": int(v)} for k, v in reject_reason_counts.items()],
                                key=lambda x: int(x.get("count") or 0),
                                reverse=True,
                            ),
                            "signals": [_signal_to_dict(s) for s in replay_signals],
                        }

                        # =========================
                        # ADD ON/OFF 比較（OFFはBASEのみ参考集計）
                        # =========================
                        pnl_add_on = float(sum(_pnl_yen_100_shares(s) for s in eval_signals))
                        pnl_add_off_ref = float(
                            sum(_pnl_yen_100_shares(s) for s in eval_signals if str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE")
                        )
                        report["add_comparison"] = {
                            "enable_add": bool(enable_add),
                            "pnl_add_on_yen_100_shares": float(pnl_add_on),
                            "pnl_add_off_ref_yen_100_shares": float(pnl_add_off_ref),
                            "note": "ADD OFFはBASEのみで再集計した参考値（同一シグナルからADDを除外）",
                        }

                        # =========================
                        # 事故分析（負け上位 / WIN vs LOSE）
                        # =========================
                        def _avg_or_none(vals: list[Optional[float]]) -> Optional[float]:
                            xs = [float(x) for x in vals if isinstance(x, (int, float))]
                            if not xs:
                                return None
                            return float(sum(xs)) / float(len(xs))

                        base_eval = [s for s in eval_signals if str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE"]
                        lose_signals = [s for s in base_eval if s.result == "LOSE"]
                        lose_sorted = sorted(lose_signals, key=lambda s: float(_pnl_yen_100_shares(s)))[:10]

                        def _metrics(xs: list[ReplaySignalEval]) -> dict[str, Any]:
                            return {
                                "count": int(len(xs)),
                                "avg_rsi14": _avg_or_none([getattr(s, "rsi14", None) for s in xs]),
                                "avg_atr_pct": _avg_or_none([getattr(s, "atr_pct", None) for s in xs]),
                                "avg_vwap_distance_pct": _avg_or_none([getattr(s, "vwap_distance_pct", None) for s in xs]),
                                "avg_relative_strength_vs_topix_pct": _avg_or_none([getattr(s, "relative_strength_vs_topix_pct", None) for s in xs]),
                            }

                        win_base = [s for s in base_eval if s.result == "WIN"]
                        lose_base = [s for s in base_eval if s.result == "LOSE"]

                        report["accident_analysis"] = {
                            "lose_top_n": 10,
                            "lose_top_metrics": _metrics(lose_sorted),
                            "lose_worst10": [
                                {
                                    "symbol": s.symbol,
                                    "signal_time_jst": _fmt_dt_jst_short(s.signal_time_utc),
                                    "time_bucket_jst": str(getattr(s, "time_bucket_jst", "") or _signal_time_bucket_jst(s.signal_time_utc)),
                                    "market_regime": str(getattr(s, "market_regime", "") or ""),
                                    "entry_price": float(s.entry_price),
                                    "exit_price": (
                                        float(getattr(s, "exit_price", 0.0))
                                        if isinstance(getattr(s, "exit_price", None), (int, float))
                                        else float(getattr(s, "trailing_exit_price", s.last_price_after) or s.last_price_after)
                                    ),
                                    "final_profit_pct": float(s.final_profit_pct) if isinstance(s.final_profit_pct, (int, float)) else None,
                                    "pnl_yen_100_shares": float(_pnl_yen_100_shares(s)),
                                    "rsi14": getattr(s, "rsi14", None),
                                    "atr_pct": getattr(s, "atr_pct", None),
                                    "vwap_distance_pct": getattr(s, "vwap_distance_pct", None),
                                    "relative_strength_vs_topix_pct": getattr(s, "relative_strength_vs_topix_pct", None),
                                    "vol_spike_ratio": getattr(s, "vol_spike_ratio", None),
                                    "exit_reason": str(getattr(s, "exit_reason", "") or ""),
                                    "not_blocked_reason": str(getattr(s, "not_blocked_reason", "") or ""),
                                }
                                for s in lose_sorted
                            ],
                            "win_avg_metrics": _metrics(win_base),
                            "lose_avg_metrics": _metrics(lose_base),
                        }

                        # Exit分析（集計）
                        by_exit: dict[str, list[ReplaySignalEval]] = {}
                        for s in base_eval:
                            er = str(getattr(s, "exit_reason", "") or "NO_EXIT")
                            by_exit.setdefault(er, []).append(s)

                        exit_stats = {}
                        for er, xs in by_exit.items():
                            t = len(xs)
                            w = sum(1 for s in xs if s.result == "WIN")
                            pnl = float(sum(_pnl_yen_100_shares(s) for s in xs))
                            exit_stats[er] = {
                                "signals": int(t),
                                "win_rate_pct": float((w / t * 100.0) if t > 0 else 0.0),
                                "pnl_yen_100_shares": float(pnl),
                                "expectancy_yen_100_shares_per_signal": float((pnl / t) if t > 0 else 0.0),
                            }

                        lose_exit_rank = {}
                        for s in lose_sorted:
                            er = str(getattr(s, "exit_reason", "") or "NO_EXIT")
                            lose_exit_rank[er] = int(lose_exit_rank.get(er, 0)) + 1

                        report["exit_analysis"] = {
                            "by_exit_reason": exit_stats,
                            "lose_worst10_exit_reason_ranking": sorted(
                                [{"exit_reason": k, "count": int(v)} for k, v in lose_exit_rank.items()],
                                key=lambda x: int(x.get("count") or 0),
                                reverse=True,
                            ),
                        }

                        # Replay設定（比較用）
                        report["replay_config"] = {
                            "early_exit_before_stop": bool(replay_early_exit_before_stop),
                            "disable_afternoon_entry": bool(replay_disable_afternoon_entry),
                            "strict_afternoon_entry": bool(replay_strict_afternoon_entry),
                            "early_exit_vwap": bool(replay_early_exit_vwap),
                            "early_exit_recent_5m_low": bool(replay_early_exit_recent_low),
                            "afternoon_topix_weak_block": bool(replay_afternoon_topix_weak_block),
                            "afternoon_volume_spike_ratio_min": float(aft_volume_spike_ratio_min),
                            "afternoon_vwap_dist_pct_max": float(aft_vwap_dist_pct_max),
                            "afternoon_rebreak_mult": float(aft_rebreak_mult),
                            "config_name": str(replay_config_name or ""),
                            "config_path": str(replay_config_path or ""),
                        }
                        report["replay_settings"] = dict(replay_settings or {})

                        # 銘柄別事故分析（BASEのみ）
                        accident_threshold = -20000.0  # 円/100株
                        by_sym_lose: dict[str, list[ReplaySignalEval]] = {}
                        for s in lose_base:
                            by_sym_lose.setdefault(s.symbol, []).append(s)

                        sym_acc = {}
                        for sym, xs in by_sym_lose.items():
                            pnls = [float(_pnl_yen_100_shares(s)) for s in xs]
                            acc_n = sum(1 for p in pnls if p <= accident_threshold)
                            mx = min(xs, key=lambda s: float(_pnl_yen_100_shares(s)))
                            sym_acc[sym] = {
                                "lose_signals": int(len(xs)),
                                "lose_rate_pct": float((len(xs) / len([s for s in base_eval if s.symbol == sym]) * 100.0))
                                if len([s for s in base_eval if s.symbol == sym]) > 0
                                else 0.0,
                                "avg_lose_pnl_yen_100_shares": float(sum(pnls) / len(pnls)) if pnls else 0.0,
                                "accident_count": int(acc_n),
                                "accident_threshold_yen_100_shares": float(accident_threshold),
                                "accident_avg_metrics": _metrics([s for s in xs if float(_pnl_yen_100_shares(s)) <= accident_threshold]),
                                "max_loss_signal": {
                                    "signal_time_jst": _fmt_dt_jst_short(mx.signal_time_utc),
                                    "pnl_yen_100_shares": float(_pnl_yen_100_shares(mx)),
                                    "exit_reason": str(getattr(mx, "exit_reason", "") or ""),
                                },
                            }

                        focus_syms = ["6501.T", "7013.T", "8058.T", "8306.T", "8473.T"]
                        report["symbol_accident_analysis"] = {
                            "focus_symbols": focus_syms,
                            "by_symbol": sym_acc,
                        }

                        # 事故回避シミュレーション（BASEのみ）
                        def _sim(name: str, pred) -> dict[str, Any]:
                            xs = list(base_eval)
                            kept = [s for s in xs if not bool(pred(s))]
                            pnl_before = float(sum(_pnl_yen_100_shares(s) for s in xs))
                            pnl_after = float(sum(_pnl_yen_100_shares(s) for s in kept))
                            return {
                                "name": name,
                                "signals_before": int(len(xs)),
                                "signals_after": int(len(kept)),
                                "excluded_signals": int(len(xs) - len(kept)),
                                "pnl_before_yen_100_shares": float(pnl_before),
                                "pnl_after_yen_100_shares": float(pnl_after),
                                "improvement_yen_100_shares": float(pnl_after - pnl_before),
                            }

                        def _is_afternoon(s: ReplaySignalEval) -> bool:
                            t = s.signal_time_utc
                            if t.tzinfo is None:
                                t = t.replace(tzinfo=timezone.utc)
                            hm = t.astimezone(JST).hour * 60 + t.astimezone(JST).minute
                            return hm >= (11 * 60 + 30)

                        sims = [
                            _sim("RSI>82 exclude", lambda s: (getattr(s, "rsi14", None) is not None and float(getattr(s, "rsi14")) > 82.0)),
                            _sim("ATR%>4 exclude", lambda s: (getattr(s, "atr_pct", None) is not None and float(getattr(s, "atr_pct")) > 4.0)),
                            _sim("RS<0 exclude", lambda s: (getattr(s, "relative_strength_vs_topix_pct", None) is not None and float(getattr(s, "relative_strength_vs_topix_pct")) < 0.0)),
                            _sim("VWAP dist>3% exclude", lambda s: (getattr(s, "vwap_distance_pct", None) is not None and float(getattr(s, "vwap_distance_pct")) > 3.0)),
                            _sim("Afternoon exclude", lambda s: _is_afternoon(s)),
                            _sim("Focus symbols exclude", lambda s: str(s.symbol) in set(focus_syms)),
                        ]
                        report["accident_avoidance_simulation"] = {
                            "base_only": True,
                            "simulations": sims,
                        }

                        # =========================
                        # フィルタ比較（signal数/期待値/WEAK勝率/個別フィルタONOFF）
                        # =========================
                        # フィルタ比較は「仮想除外」専用（集計対象signalは除外しない）
                        # - suggested_block_reasons に記録された条件をもとに、仮想的に落とした場合を比較します。
                        def _suggested_keys(s: ReplaySignalEval) -> set[str]:
                            raw = str(getattr(s, "suggested_block_reasons", "") or "")
                            return set([x.strip() for x in raw.split(",") if x.strip()])

                        base_all = list(eval_signals)
                        # フィルタON = suggested_block_reasons が空のものだけ残す（仮想）
                        virt_on = [s for s in base_all if not _suggested_keys(s)]
                        virt_off = list(base_all)

                        # RSI/ATR “単独” で落ちるものを戻す比較（仮想）
                        rsi_key1 = f"rsi>{int(SIGNAL_FILTER_RSI_BLOCK_GT)}"
                        rsi_key2 = f"rsi>{int(WEAK_SIGNAL_FILTER_RSI_BLOCK_GT)}"
                        atr_key1 = f"atr_pct>{int(SIGNAL_FILTER_ATR_PCT_BLOCK_GT)}"
                        atr_key2 = f"atr_pct>{WEAK_SIGNAL_FILTER_ATR_PCT_BLOCK_GT}"

                        def _only_one_of(s: ReplaySignalEval, keys: set[str]) -> bool:
                            k = _suggested_keys(s)
                            return (len(k) == 1) and (next(iter(k)) in keys)

                        rsi_only = [s for s in virt_off if _only_one_of(s, {rsi_key1, rsi_key2})]
                        atr_only = [s for s in virt_off if _only_one_of(s, {atr_key1, atr_key2})]

                        # WEAK時勝率（BASE・採用分）
                        weak_eval = [s for s in base_eval if str(getattr(s, "market_regime", "") or "") == "WEAK"]
                        weak_win = sum(1 for s in weak_eval if s.result == "WIN")
                        weak_wr = (weak_win / len(weak_eval) * 100.0) if weak_eval else 0.0

                        report["filter_comparison"] = {
                            "signal_delta": {
                                "signals_filter_on": int(len(virt_on)),
                                "signals_filter_off": int(len(virt_off)),
                                "delta": int(len(virt_off) - len(virt_on)),
                            },
                            "expectancy_delta_yen_100_shares_per_signal": float(
                                (float(sum(_pnl_yen_100_shares(s) for s in virt_off)) / float(len(virt_off))) if virt_off else 0.0
                            )
                            - float((float(sum(_pnl_yen_100_shares(s) for s in virt_on)) / float(len(virt_on))) if virt_on else 0.0),
                            "weak_win_rate_pct_on": float(weak_wr),
                            "rsi_filter_off_stats": _agg_stats([s for s in virt_off if (not _only_one_of(s, {rsi_key1, rsi_key2}))] + rsi_only),
                            "atr_filter_off_stats": _agg_stats([s for s in virt_off if (not _only_one_of(s, {atr_key1, atr_key2}))] + atr_only),
                            "note": "フィルタ比較は仮想（suggested_block_reasonsベース）。実際のeval_signalsは除外しない。",
                        }

                        # =========================
                        # Replay銘柄スコア（ブラックリスト/優先/改善額）
                        # =========================
                        scores_by_symbol: dict[str, dict[str, Any]] = {}
                        blacklist_symbols: list[str] = []
                        priority_symbols: list[str] = []
                        for sym, xs in by_symbol.items():
                            st = _agg_stats(xs)
                            sigs = int(st.get("signals") or 0)
                            wr = float(st.get("win_rate_pct") or 0.0)
                            exp_y = float(st.get("expectancy_yen_100_shares_per_signal") or 0.0)
                            is_black = (sigs >= int(SYMBOL_BLACKLIST_SIGNALS_MIN)) and (exp_y < float(SYMBOL_BLACKLIST_EXPECTANCY_YEN_MAX))
                            is_pri = (exp_y > float(SYMBOL_PRIORITY_EXPECTANCY_YEN_MIN)) and (wr > float(SYMBOL_PRIORITY_WIN_RATE_PCT_MIN))
                            if is_black:
                                blacklist_symbols.append(sym)
                            if is_pri:
                                priority_symbols.append(sym)
                            scores_by_symbol[sym] = {
                                "symbol": sym,
                                "signals": int(sigs),
                                "win_rate_pct": float(wr),
                                "expectancy_yen_100_shares_per_signal": float(exp_y),
                                "avg_pnl_yen_100_shares_per_signal": float(st.get("avg_pnl_yen_100_shares_per_signal") or 0.0),
                                "max_drawdown_pct_worst": float(st.get("max_drawdown_pct_worst") or 0.0),
                                "max_drawdown_yen_100_shares_worst_est": float(st.get("max_drawdown_yen_100_shares_worst_est") or 0.0),
                                "blacklisted": bool(is_black),
                                "priority": bool(is_pri),
                            }

                        # =========================
                        # symbol quality filter（Replayベース）
                        # =========================
                        # 目的:
                        # - 市場全体ではなく「期待値の高い銘柄だけ通す」へ移行
                        #
                        # 初期ルール（調整しやすいようにJSONへ明示）:
                        # - signals >= 2 かつ expectancy <= 0 なら禁止
                        QUALITY_SIGNALS_MIN = 2
                        QUALITY_EXPECTANCY_MAX = 0.0
                        QUALITY_WIN_RATE_MIN = 50.0
                        # 参考ルール（代替案）:
                        # - (win_rate < 50% and expectancy < 0) の場合のみ禁止
                        quality_alt_blocked: list[str] = []
                        quality_blocked: list[str] = []
                        quality_allowed: list[str] = []
                        quality_reason_by_symbol: dict[str, str] = {}
                        for sym, st in scores_by_symbol.items():
                            sigs = int(st.get("signals") or 0)
                            wr = float(st.get("win_rate_pct") or 0.0)
                            exp_y = float(st.get("expectancy_yen_100_shares_per_signal") or 0.0)
                            alt = (sigs >= QUALITY_SIGNALS_MIN) and (wr < QUALITY_WIN_RATE_MIN) and (exp_y < 0.0)
                            if alt:
                                quality_alt_blocked.append(sym)
                            # 採用する品質フィルタ（本命）
                            block = (sigs >= QUALITY_SIGNALS_MIN) and (exp_y <= QUALITY_EXPECTANCY_MAX)
                            if block:
                                quality_blocked.append(sym)
                                quality_reason_by_symbol[sym] = f"signals>={QUALITY_SIGNALS_MIN} and expectancy<={QUALITY_EXPECTANCY_MAX}"
                            else:
                                quality_allowed.append(sym)

                        # quality filter ON/OFF 比較（仮想）
                        quality_on_signals = [s for s in eval_signals if s.symbol not in set(quality_blocked)]
                        quality_off_signals = list(eval_signals)
                        quality_on_pnl = float(sum(_pnl_yen_100_shares(s) for s in quality_on_signals))
                        quality_off_pnl = float(sum(_pnl_yen_100_shares(s) for s in quality_off_signals))
                        quality_on_exp = (quality_on_pnl / float(len(quality_on_signals))) if quality_on_signals else 0.0
                        quality_off_exp = (quality_off_pnl / float(len(quality_off_signals))) if quality_off_signals else 0.0

                        # 銘柄品質ランキング（signals>=2 を優先、expectancy降順）
                        quality_rank = sorted(
                            [
                                {
                                    "symbol": sym,
                                    "signals": int(st.get("signals") or 0),
                                    "win_rate_pct": float(st.get("win_rate_pct") or 0.0),
                                    "expectancy_yen_100_shares_per_signal": float(st.get("expectancy_yen_100_shares_per_signal") or 0.0),
                                    "allowed": bool(sym in set(quality_allowed)),
                                }
                                for sym, st in scores_by_symbol.items()
                            ],
                            key=lambda x: (
                                int(x.get("signals") or 0) >= QUALITY_SIGNALS_MIN,
                                float(x.get("expectancy_yen_100_shares_per_signal") or 0.0),
                            ),
                            reverse=True,
                        )[:50]

                        # 除外による改善額（= ブラックリスト銘柄を全除外した場合の差分）
                        eval_pnl_all = float(sum(_pnl_yen_100_shares(s) for s in eval_signals))
                        eval_pnl_excl = float(sum(_pnl_yen_100_shares(s) for s in eval_signals if s.symbol not in set(blacklist_symbols)))
                        improvement = float(eval_pnl_excl - eval_pnl_all)

                        # 9984.T は優先銘柄として強調（条件未達でもリストには載せる）
                        if "9984.T" in scores_by_symbol and "9984.T" not in set(priority_symbols):
                            priority_symbols.append("9984.T")
                            try:
                                scores_by_symbol["9984.T"]["priority"] = True
                            except Exception:
                                pass

                        report["symbol_scoring"] = {
                            "entry_blacklist_rules": {
                                "expectancy_lt_yen_100_shares": float(SYMBOL_BLACKLIST_EXPECTANCY_YEN_MAX),
                                "signals_gte": int(SYMBOL_BLACKLIST_SIGNALS_MIN),
                            },
                            "priority_rules": {
                                "expectancy_gt_yen_100_shares": float(SYMBOL_PRIORITY_EXPECTANCY_YEN_MIN),
                                "win_rate_gt_pct": float(SYMBOL_PRIORITY_WIN_RATE_PCT_MIN),
                            },
                            "blacklist_symbols": sorted(list(set(blacklist_symbols))),
                            "priority_symbols": sorted(list(set(priority_symbols))),
                            "provisional_blacklist_candidates": ["8058.T", "6890.T", "8473.T"],
                            "priority_emphasis_symbols": ["9984.T"],
                            # symbol quality filter（実運用でENTRY禁止に使う）
                            "quality_filter_rules": {
                                "mode": "expectancy_le_0",
                                "signals_gte": int(QUALITY_SIGNALS_MIN),
                                "expectancy_lte_yen_100_shares": float(QUALITY_EXPECTANCY_MAX),
                                "alt_mode": "win_rate_lt_50_and_expectancy_lt_0",
                                "win_rate_lt_pct": float(QUALITY_WIN_RATE_MIN),
                            },
                            "quality_blocked_symbols": sorted(list(set(quality_blocked))),
                            "quality_allowed_symbols": sorted(list(set(quality_allowed))),
                            "quality_alt_blocked_symbols": sorted(list(set(quality_alt_blocked))),
                            "quality_reason_by_symbol": quality_reason_by_symbol,
                            "scores_by_symbol": scores_by_symbol,
                            "pnl_all_yen_100_shares": float(eval_pnl_all),
                            "pnl_after_excluding_blacklist_yen_100_shares": float(eval_pnl_excl),
                            "improvement_yen_100_shares": float(improvement),
                            "quality_filter_comparison": {
                                "symbols_blocked": int(len(set(quality_blocked))),
                                "signals_off": int(len(quality_off_signals)),
                                "signals_on": int(len(quality_on_signals)),
                                "signal_decrease_rate_pct": float(
                                    ((len(quality_off_signals) - len(quality_on_signals)) / float(len(quality_off_signals)) * 100.0)
                                )
                                if quality_off_signals
                                else 0.0,
                                "pnl_off_yen_100_shares": float(quality_off_pnl),
                                "pnl_on_yen_100_shares": float(quality_on_pnl),
                                "improvement_yen_100_shares": float(quality_on_pnl - quality_off_pnl),
                                "expectancy_off_yen_100_shares_per_signal": float(quality_off_exp),
                                "expectancy_on_yen_100_shares_per_signal": float(quality_on_exp),
                                "expectancy_delta_yen_100_shares_per_signal": float(quality_on_exp - quality_off_exp),
                            },
                            "quality_ranking_top50": quality_rank,
                        }

                        # =========================
                        # 除外シミュレーション（主要負け銘柄）
                        # =========================
                        # 例: 「7003/8058除外なら損益 +○円」
                        scenario_sets: list[list[str]] = [
                            ["7003.T"],
                            ["8058.T"],
                            ["8473.T"],
                            ["6890.T"],
                            ["7003.T", "8058.T"],
                            ["7003.T", "8058.T", "8473.T", "6890.T"],
                        ]
                        scenarios: list[dict[str, Any]] = []
                        for ex_syms in scenario_sets:
                            ex_set = set(ex_syms)
                            pnl_after = float(sum(_pnl_yen_100_shares(s) for s in eval_signals if s.symbol not in ex_set))
                            scenarios.append(
                                {
                                    "exclude_symbols": list(ex_syms),
                                    "pnl_after_yen_100_shares": float(pnl_after),
                                    "improvement_yen_100_shares": float(pnl_after - eval_pnl_all),
                                }
                            )
                        report["symbol_scoring"]["exclusion_scenarios"] = scenarios

                        # =========================
                        # 各signalの詳細CSV（整合性チェック用）
                        # =========================
                        signals_csv_path = os.path.join(results_dir, f"{name_base}_signals.csv")

                        def _as_iso_any(dt: Any) -> str:
                            if dt is None:
                                return ""
                            if isinstance(dt, str):
                                return dt
                            if isinstance(dt, datetime):
                                t = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
                                return t.isoformat()
                            return str(dt)

                        def _signal_closed(s: ReplaySignalEval) -> bool:
                            if bool(getattr(s, "resolved", False)):
                                return True
                            r = str(getattr(s, "exit_reason", "") or "")
                            return bool(r)

                        with open(signals_csv_path, "w", encoding="utf-8", newline="") as fcsv:
                            wcsv = csv.writer(fcsv)
                            wcsv.writerow(
                                [
                                    "signal_id",
                                    "symbol",
                                    "entry_time_utc",
                                    "topix_chg_pct_raw",
                                    "topix_chg_pct",
                                    "market_state",
                                    "blocked_reason",
                                    "entry_allowed",
                                    "exit_time_utc",
                                    "entry_price",
                                    "exit_price",
                                    "profit_pct",
                                    "pnl_yen_100_shares",
                                    "exit_reason",
                                    "position_closed",
                                    "excluded_from_eval",
                                    "excluded_reason",
                                ]
                            )
                            csv_rows_written = 0
                            for i, s in enumerate(replay_signals):
                                sid = str(getattr(s, "signal_id", "") or f"{safe_batch_stamp}_idx{i:05d}")
                                profit_pct = float(_profit_pct_for_summary(s))
                                pnl100 = float(_pnl_yen_100_shares(s))
                                wcsv.writerow(
                                    [
                                        sid,
                                        str(s.symbol),
                                        _as_iso_any(getattr(s, "signal_time_utc", None)),
                                        (
                                            float(getattr(s, "topix_chg_pct_raw", 0.0))
                                            if isinstance(getattr(s, "topix_chg_pct_raw", None), (int, float))
                                            else ""
                                        ),
                                        (
                                            float(getattr(s, "topix_chg_pct", 0.0))
                                            if isinstance(getattr(s, "topix_chg_pct", None), (int, float))
                                            else ""
                                        ),
                                        str(getattr(s, "market_state", "") or getattr(s, "market_regime", "") or ""),
                                        str(getattr(s, "blocked_reason", "") or ""),
                                        bool(getattr(s, "entry_allowed", True)),
                                        _as_iso_any(getattr(s, "exit_time_utc", None)),
                                        float(getattr(s, "entry_price", 0.0) or 0.0),
                                        (float(getattr(s, "exit_price", 0.0)) if isinstance(getattr(s, "exit_price", None), (int, float)) else ""),
                                        profit_pct,
                                        pnl100,
                                        str(getattr(s, "exit_reason", "") or ""),
                                        bool(_signal_closed(s)),
                                        bool(getattr(s, "excluded_from_eval", False)),
                                        str(getattr(s, "excluded_reason", "") or ""),
                                    ]
                                )
                                csv_rows_written += 1

                        # 整合性チェック（差異があればWARNING）
                        unique_ids = [str(getattr(s, "signal_id", "") or "") for s in replay_signals]
                        unique_ids_nonempty = [x for x in unique_ids if x]
                        uniq_id_n = len(set(unique_ids_nonempty)) if unique_ids_nonempty else 0
                        closed_n = sum(1 for s in replay_signals if _signal_closed(s))
                        eval_n = len(eval_signals)
                        warn_msgs: list[str] = []
                        if int(csv_rows_written) != int(len(replay_signals)):
                            warn_msgs.append("signals.csv 行数 != signal総数")
                        if int(csv_rows_written) == 0:
                            warn_msgs.append("signals.csv がヘッダーのみ（0行）")
                        if uniq_id_n and uniq_id_n != len(unique_ids_nonempty):
                            warn_msgs.append("signal_id が重複しています（unique != total）")
                        if eval_n != int(report["overall_summary"]["signals_in_eval"]):
                            warn_msgs.append("len(eval_signals) != overall_summary.signals_in_eval")

                        report["integrity_check"] = {
                            "signals_total": int(len(replay_signals)),
                            "signals_csv_rows": int(csv_rows_written),
                            "positions_closed": int(closed_n),
                            "signals_in_eval": int(eval_n),
                            "unique_signal_id_count": int(uniq_id_n),
                            "warnings": warn_msgs,
                            "signals_csv_path": str(signals_csv_path),
                        }
                        if warn_msgs:
                            print(f"[{now_str()}][WARNING] Replay整合性チェック: " + "; ".join(warn_msgs))

                        # signal 0件の明記（ユーザー向けに「出力ミス」と区別しやすくする）
                        if int(len(replay_signals)) == 0:
                            report["integrity_check"]["note"] = "このrunは signal 0件のため signals.csv はヘッダーのみです"

                        json_path = os.path.join(results_dir, f"{name_base}.json")
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(report, f, ensure_ascii=False, indent=2)

                        # txt
                        lines: list[str] = []
                        lines.append("=== Replay結果（自動保存） ===")
                        # 要件: 最上部に「今回どの設定で回したか」を必ず表示
                        try:
                            rs = report.get("replay_settings") if isinstance(report.get("replay_settings"), dict) else None
                            if isinstance(rs, dict):
                                lines.extend(_settings_lines(rs))
                        except Exception:
                            pass
                        lines.append(f"- saved_at_jst: {report['meta']['saved_at_jst']}")
                        lines.append(f"- replay_range: {replay_range_label}")
                        lines.append(f"- replay_speed: {speed_s}")
                        lines.append(f"- start_time_jst: {start_jst}")
                        lines.append(f"- end_time_jst: {end_jst}")
                        lines.append(f"- watch_symbols: {', '.join(sorted(list(base_watch)))}")
                        if replay_dates_jst:
                            lines.append("- replay_dates:")
                            for d in replay_dates_jst:
                                lines.append(f"  - {d}")
                            lines.append(f"- replay_seed: {replay_seed if replay_seed is not None else '(random)'}")
                        if (replay_morning_screen_hhmm or '').strip():
                            lines.append(f"- replay_morning_screen_hhmm_jst: {(replay_morning_screen_hhmm or '').strip()}")
                        if one_trade_per_symbol_per_day:
                            lines.append("- one_trade_per_symbol_per_day: True")
                        lines.append(f"- enable_add: {bool(enable_add)}")
                        lines.append("")

                        lines.append("【全体サマリー】")
                        lines.append(f"総signal数(検出): {len(replay_signals)}")
                        lines.append(f"総signal数(集計対象): {total}")
                        if excluded_n > 0:
                            lines.append(f"除外signal数: {excluded_n}")
                        if isinstance(report.get("integrity_check"), dict):
                            ic = report.get("integrity_check") or {}
                            lines.append("")
                            lines.append("【Replay整合性チェック】")
                            lines.append(f"- signal数: {int(ic.get('signals_total') or 0)}")
                            lines.append(f"- closed_position数: {int(ic.get('positions_closed') or 0)}")
                            lines.append(f"- 集計signal数: {int(ic.get('signals_in_eval') or 0)}")
                            lines.append(f"- ユニークsignal_id数: {int(ic.get('unique_signal_id_count') or 0)}")
                            if "signals_csv_rows" in ic:
                                lines.append(f"- signals.csv行数: {int(ic.get('signals_csv_rows') or 0)}")
                            if ic.get("warnings"):
                                lines.append(f"- WARNING: {'; '.join([str(x) for x in (ic.get('warnings') or [])])}")
                            if ic.get("signals_csv_path"):
                                lines.append(f"- signals_csv: {str(ic.get('signals_csv_path'))}")
                            if int(ic.get("signals_total") or 0) == 0:
                                lines.append("  ※このrunは signal 0件のため signals.csv はヘッダーのみです")
                        lines.append(f"WIN/LOSE/HOLD: {win}/{lose}/{hold}")
                        lines.append(f"勝率: {win_rate:.1f}%")
                        lines.append(f"平均利益率(WIN): {avg_profit_pct:.2f}%")
                        lines.append(f"平均損失率(LOSE): {avg_loss_pct:.2f}%")
                        lines.append(f"平均最大利益率: {avg_max_profit_pct:.2f}%")
                        lines.append(f"平均最大下落率: {avg_max_drawdown_pct:.2f}%")
                        lines.append(f"100株損益(合計): {total_pnl_yen:+,.0f}円")
                        lines.append(f"100株損益(平均): {avg_pnl_yen:+,.0f}円")
                        lines.append(f"expectancy（100株/1signal）: {_expectancy_yen_100_shares(eval_signals):+,.0f}円")
                        lines.append(f"expectancy（profit%/1signal）: {avg_profit_pct_all:+.2f}%")
                        lines.append("")

                        lines.append("【銘柄別サマリー】")
                        for sym in sorted(by_symbol.keys()):
                            st = by_symbol[sym]
                            t = len(st)
                            w = sum(1 for s in st if s.result == "WIN")
                            l = sum(1 for s in st if s.result == "LOSE")
                            h = sum(1 for s in st if s.result == "HOLD")
                            wr = (w / t * 100.0) if t > 0 else 0.0
                            pnl = sum(_pnl_yen_100_shares(s) for s in st)
                            exp_y = _expectancy_yen_100_shares(st)
                            avg_pnl = (float(pnl) / float(t)) if t > 0 else 0.0
                            worst_dd_pct = min([float(s.max_drawdown_pct()) for s in st]) if st else 0.0
                            lines.append(
                                f"- {sym}: signals={t}  WIN/LOSE/HOLD={w}/{l}/{h}  勝率={wr:.1f}%  "
                                f"expectancy={exp_y:+,.0f}円  平均損益={avg_pnl:+,.0f}円  最大DD={worst_dd_pct:.2f}%  100株損益={pnl:+,.0f}円"
                            )
                        lines.append("")

                        # 銘柄スコアリング（追加）
                        ss = report.get("symbol_scoring") or {}
                        bl = list(ss.get("blacklist_symbols") or [])
                        pr = list(ss.get("priority_symbols") or [])
                        prov = list(ss.get("provisional_blacklist_candidates") or [])
                        emph = list(ss.get("priority_emphasis_symbols") or [])
                        lines.append("【銘柄スコアリング（Replay期待値ベース）】")
                        lines.append(f"- ブラックリスト銘柄: {', '.join(bl) if bl else '(none)'}")
                        lines.append(f"- 優先銘柄: {', '.join(pr) if pr else '(none)'}")
                        lines.append(f"- 暫定ブラックリスト候補: {', '.join(prov) if prov else '(none)'}")
                        lines.append(f"- 優先強調: {', '.join(emph) if emph else '(none)'}")
                        lines.append(f"- 除外後損益(100株): {float(ss.get('pnl_after_excluding_blacklist_yen_100_shares') or 0.0):+,.0f}円")
                        lines.append(f"- 除外による改善額(100株): {float(ss.get('improvement_yen_100_shares') or 0.0):+,.0f}円")
                        lines.append("")

                        # symbol quality filter（追加）
                        qb = list(ss.get("quality_blocked_symbols") or [])
                        qc = ss.get("quality_filter_comparison") or {}
                        if qb or qc:
                            lines.append("【銘柄品質フィルタ（Replayベース）】")
                            lines.append(f"- 禁止銘柄数: {int(qc.get('symbols_blocked') or len(qb))}")
                            lines.append(f"- 禁止銘柄: {', '.join(qb) if qb else '(none)'}")
                            lines.append(
                                f"- quality ON/OFF: signals {int(qc.get('signals_on') or 0)}/{int(qc.get('signals_off') or 0)}  "
                                f"減少率 {float(qc.get('signal_decrease_rate_pct') or 0.0):.1f}%"
                            )
                            lines.append(
                                f"- quality ON/OFF: expectancy {float(qc.get('expectancy_on_yen_100_shares_per_signal') or 0.0):+,.0f}円/"
                                f"{float(qc.get('expectancy_off_yen_100_shares_per_signal') or 0.0):+,.0f}円  "
                                f"Δ={float(qc.get('expectancy_delta_yen_100_shares_per_signal') or 0.0):+,.0f}円"
                            )
                            lines.append(
                                f"- 除外後仮想損益(quality ON,100株): {float(qc.get('pnl_on_yen_100_shares') or 0.0):+,.0f}円  "
                                f"改善 {float(qc.get('improvement_yen_100_shares') or 0.0):+,.0f}円"
                            )
                            lines.append("")

                        # 除外シミュレーション（追加）
                        exs = list(ss.get("exclusion_scenarios") or [])
                        if exs:
                            lines.append("【除外シミュレーション（仮想損益）】")
                            for it in exs:
                                syms = "/".join(list(it.get("exclude_symbols") or []))
                                pnl2 = float(it.get("pnl_after_yen_100_shares") or 0.0)
                                imp2 = float(it.get("improvement_yen_100_shares") or 0.0)
                                lines.append(f"- {syms}除外なら: 損益 {pnl2:+,.0f}円（改善 {imp2:+,.0f}円）/100株")
                            lines.append("")

                        lines.append("【時間帯別サマリー】")
                        for b in bucket_order:
                            st = by_bucket.get(b) or []
                            if not st:
                                continue
                            t = len(st)
                            w = sum(1 for s in st if s.result == "WIN")
                            l = sum(1 for s in st if s.result == "LOSE")
                            h = sum(1 for s in st if s.result == "HOLD")
                            wr = (w / t * 100.0) if t > 0 else 0.0
                            pnl = sum(_pnl_yen_100_shares(s) for s in st)
                            lines.append(f"- {b}: signals={t}  WIN/LOSE/HOLD={w}/{l}/{h}  勝率={wr:.1f}%  100株損益={pnl:+,.0f}円")
                        lines.append("")

                        lines.append("【BASE/ADD別サマリー】")
                        for pk in ["BASE", "ADD1", "ADD2"]:
                            st = by_pos.get(pk) or []
                            if not st:
                                continue
                            t = len(st)
                            w = sum(1 for s in st if s.result == "WIN")
                            l = sum(1 for s in st if s.result == "LOSE")
                            h = sum(1 for s in st if s.result == "HOLD")
                            wr = (w / t * 100.0) if t > 0 else 0.0
                            pnl = sum(_pnl_yen_100_shares(s) for s in st)
                            exp_y = _expectancy_yen_100_shares(st)
                            lines.append(f"- {pk}: signals={t}  WIN/LOSE/HOLD={w}/{l}/{h}  勝率={wr:.1f}%  100株損益={pnl:+,.0f}円  expectancy={exp_y:+,.0f}円")
                        lines.append("")

                        # ADD回数別・比較・ランキング（追加仕様）
                        lines.append("【ADD回数別サマリー（銘柄×日）】")
                        for bucket in ["0", "1", "2"]:
                            a = add_bucket_stats.get(bucket)
                            if not a:
                                continue
                            ds = int(a.get("daysymbols") or 0)
                            sigs = int(a.get("signals") or 0)
                            w = int(a.get("win") or 0)
                            l = int(a.get("lose") or 0)
                            h = int(a.get("hold") or 0)
                            pnl = float(a.get("pnl") or 0.0)
                            wr = (w / sigs * 100.0) if sigs > 0 else 0.0
                            lines.append(f"- ADD回数={bucket}: 銘柄日={ds}  signals={sigs}  WIN/LOSE/HOLD={w}/{l}/{h}  勝率={wr:.1f}%  100株損益={pnl:+,.0f}円")
                        lines.append("")

                        lines.append("【ADDあり vs ADDなし（signal単位）】")
                        ay = _agg_stats(add_yes_signals)
                        an = _agg_stats(add_no_signals)
                        lines.append(f"- ADDあり: signals={ay['signals']}  勝率={ay['win_rate_pct']:.1f}%  100株損益={ay['pnl_yen_100_shares']:+,.0f}円  expectancy={ay['expectancy_yen_100_shares_per_signal']:+,.0f}円")
                        lines.append(f"- ADDなし: signals={an['signals']}  勝率={an['win_rate_pct']:.1f}%  100株損益={an['pnl_yen_100_shares']:+,.0f}円  expectancy={an['expectancy_yen_100_shares_per_signal']:+,.0f}円")
                        lines.append("")

                        lines.append("【ADD失敗銘柄ランキング（ADDポジション損益ワースト）】")
                        if not add_fail_rank:
                            lines.append("- ADDが発生していないためランキングはありません。")
                        else:
                            for sym, pnl in add_fail_rank:
                                lines.append(f"- {sym}: ADD signals={int(add_sig_by_symbol.get(sym, 0))}  LOSE={int(add_lose_by_symbol.get(sym, 0))}  ADD損益(100株)={float(pnl):+,.0f}円")
                        lines.append("")

                        # 地合いフィルタ（追加仕様）
                        lines.append("【地合いフィルタ（ON/OFF別）】")
                        lines.append(f"- signal候補数: {int(signal_candidate_count)}")
                        lines.append(f"- ENTRY禁止回数: {int(blocked_entry_count)}")
                        lines.append(f"- WEAK追加条件で除外: {int(len(weak_reject_signals))}")
                        if blocked_reason_counts:
                            lines.append("- BLOCK理由ランキング:")
                            for rr, cc in sorted(blocked_reason_counts.items(), key=lambda kv: int(kv[1]), reverse=True)[:10]:
                                lines.append(f"  - {rr}: {int(cc)}")
                        st_on = _agg_stats(eval_signals)
                        st_off = _agg_stats(off_signals)
                        st_blk = _agg_stats(blocked_signals)
                        lines.append(f"- [ON] signals={st_on['signals']} 勝率={st_on['win_rate_pct']:.1f}% 100株損益={st_on['pnl_yen_100_shares']:+,.0f}円")
                        lines.append(f"- [OFF] signals={st_off['signals']} 勝率={st_off['win_rate_pct']:.1f}% 100株損益={st_off['pnl_yen_100_shares']:+,.0f}円")
                        lines.append(f"- [BLOCKED] signals={st_blk['signals']} 勝率={st_blk['win_rate_pct']:.1f}% 100株損益={st_blk['pnl_yen_100_shares']:+,.0f}円")
                        lines.append(f"- 地合い回避による損失回避額(推定): {avoided_loss_yen:+,.0f}円/100株")
                        lines.append("")

                        # ADD ON/OFF 比較（追加）
                        ac = report.get("add_comparison") or {}
                        lines.append("【ADD ON/OFF 比較】")
                        lines.append(f"- ADD ON時損益(100株): {float(ac.get('pnl_add_on_yen_100_shares') or 0.0):+,.0f}円")
                        lines.append(f"- ADD OFF時損益(参考/BASEのみ,100株): {float(ac.get('pnl_add_off_ref_yen_100_shares') or 0.0):+,.0f}円")
                        lines.append("")

                        # Replay比較スイッチ（ユーザー要望）
                        cfg = report.get("replay_config") or {}
                        if isinstance(cfg, dict) and (("early_exit_before_stop" in cfg) or ("disable_afternoon_entry" in cfg) or ("strict_afternoon_entry" in cfg)):
                            lines.append("【Replay比較スイッチ】")
                            lines.append(f"- STOP前早期Exit: {bool(cfg.get('early_exit_before_stop', False))}")
                            lines.append(f"- 後場Entry禁止: {bool(cfg.get('disable_afternoon_entry', False))}")
                            lines.append(f"- 後場Entry厳格化: {bool(cfg.get('strict_afternoon_entry', False))}")
                            if "config_name" in cfg or "config_path" in cfg:
                                lines.append(f"- config_name: {str(cfg.get('config_name') or '')}")
                                lines.append(f"- config_path: {str(cfg.get('config_path') or '')}")
                            lines.append("")

                        # 事故分析（追加）
                        aa = report.get("accident_analysis") or {}
                        lt = aa.get("lose_top_metrics") or {}
                        wa = aa.get("win_avg_metrics") or {}
                        la = aa.get("lose_avg_metrics") or {}
                        lines.append("【事故分析（BASEのみ）】")
                        def _fmt_opt(v: Any, fmt: str) -> str:
                            try:
                                if v is None:
                                    return "N/A"
                                return format(float(v), fmt)
                            except Exception:
                                return "N/A"
                        lines.append(
                            "- 負け上位(LOSE worst10) 平均: "
                            f"RSI={_fmt_opt(lt.get('avg_rsi14'), '.2f')}  "
                            f"ATR%={_fmt_opt(lt.get('avg_atr_pct'), '.2f')}%  "
                            f"VWAP乖離={_fmt_opt(lt.get('avg_vwap_distance_pct'), '.2f')}%  "
                            f"RS(TOPIX比)={_fmt_opt(lt.get('avg_relative_strength_vs_topix_pct'), '.2f')}%"
                        )
                        lines.append(
                            "- WIN平均RSI: "
                            f"{_fmt_opt(wa.get('avg_rsi14'), '.2f')}"
                            f" / LOSE平均RSI: {_fmt_opt(la.get('avg_rsi14'), '.2f')}"
                        )
                        lines.append(
                            "- WIN平均ATR%: "
                            f"{_fmt_opt(wa.get('avg_atr_pct'), '.2f')}%"
                            f" / LOSE平均ATR%: {_fmt_opt(la.get('avg_atr_pct'), '.2f')}%"
                        )
                        lines.append("")

                        # LOSE worst10 詳細（追加）
                        lw = (aa.get("lose_worst10") or [])
                        if lw:
                            lines.append("【LOSE worst10（事故詳細）】")
                            for it in lw:
                                fpct_s = "N/A"
                                try:
                                    if it.get("final_profit_pct") is not None:
                                        fpct_s = f"{float(it.get('final_profit_pct')):.2f}%"
                                except Exception:
                                    fpct_s = "N/A"
                                rsi_s = "N/A" if it.get("rsi14") is None else f"{float(it.get('rsi14')):.2f}"
                                atr_s = "N/A" if it.get("atr_pct") is None else f"{float(it.get('atr_pct')):.2f}%"
                                vwap_s = "N/A" if it.get("vwap_distance_pct") is None else f"{float(it.get('vwap_distance_pct')):.2f}%"
                                rs_s = "N/A" if it.get("relative_strength_vs_topix_pct") is None else f"{float(it.get('relative_strength_vs_topix_pct')):.2f}%"
                                volx_s = "N/A" if it.get("vol_spike_ratio") is None else f"{float(it.get('vol_spike_ratio')):.2f}x"
                                lines.append(
                                    "- "
                                    f"{it.get('symbol')} {it.get('signal_time_jst')} "
                                    f"bucket={it.get('time_bucket_jst')} regime={it.get('market_regime')} "
                                    f"entry={_fmt_yen(float(it.get('entry_price') or 0.0))} "
                                    f"exit={_fmt_yen(float(it.get('exit_price') or 0.0))} "
                                    f"fpct={fpct_s} "
                                    f"pnl={float(it.get('pnl_yen_100_shares') or 0.0):+,.0f}円 "
                                    f"RSI={rsi_s} "
                                    f"ATR%={atr_s} "
                                    f"VWAP={vwap_s} "
                                    f"RS={rs_s} "
                                    f"volx={volx_s} "
                                    f"exit_reason={it.get('exit_reason') or 'N/A'} "
                                    f"not_blocked={it.get('not_blocked_reason') or ''}"
                                )
                            lines.append("")

                        # Exit分析（追加）
                        ea = report.get("exit_analysis") or {}
                        ber = ea.get("by_exit_reason") or {}
                        if ber:
                            lines.append("【Exit分析（exit_reason別）】")
                            for er, st in sorted(ber.items(), key=lambda kv: float((kv[1] or {}).get("pnl_yen_100_shares") or 0.0)):
                                lines.append(
                                    f"- {er}: signals={int(st.get('signals') or 0)} "
                                    f"勝率={float(st.get('win_rate_pct') or 0.0):.1f}% "
                                    f"損益={float(st.get('pnl_yen_100_shares') or 0.0):+,.0f}円 "
                                    f"expectancy={float(st.get('expectancy_yen_100_shares_per_signal') or 0.0):+,.0f}円"
                                )
                            lr = ea.get("lose_worst10_exit_reason_ranking") or []
                            if lr:
                                lines.append("- 大損(LOSE worst10) exit_reasonランキング:")
                                for it in lr:
                                    lines.append(f"  - {it.get('exit_reason')}: {int(it.get('count') or 0)}")
                            lines.append("")

                        # 事故回避シミュレーション（追加）
                        av = report.get("accident_avoidance_simulation") or {}
                        sims = av.get("simulations") or []
                        if sims:
                            lines.append("【事故回避シミュレーション（BASEのみ）】")
                            for it in sims:
                                lines.append(
                                    f"- {it.get('name')}: "
                                    f"損益 {float(it.get('pnl_after_yen_100_shares') or 0.0):+,.0f}円（改善 {float(it.get('improvement_yen_100_shares') or 0.0):+,.0f}円） "
                                    f"/ 除外 {int(it.get('excluded_signals') or 0)}件"
                                )
                            lines.append("")

                        # =========================
                        # REJECT理由ランキング（ユーザー要望）
                        # - Replayで「候補が reject された理由」を可視化します
                        # =========================
                        rej_rank = report.get("reject_reason_ranking") or []
                        if rej_rank:
                            lines.append("[REJECT_REASON_RANKING]")
                            lines.append("")
                            for it in rej_rank[:30]:
                                try:
                                    k = str(it.get("reason") or "")
                                    v = int(it.get("count") or 0)
                                    if k:
                                        lines.append(f"{k}: {v}")
                                except Exception:
                                    continue
                            lines.append("")

                        # =========================
                        # PIPELINE_DEBUG（ユーザー要望）
                        # =========================
                        pd = report.get("pipeline_debug") or {}
                        cr = report.get("continue_reason_counts") or {}
                        if isinstance(pd, dict) or isinstance(cr, dict):
                            lines.append("[PIPELINE_DEBUG]")
                            lines.append("")
                            if isinstance(pd, dict):
                                for k in [
                                    "market_debug_count",
                                    "candidate_loop_entered",
                                    "to_notify_count",
                                    "entry_calc_ok",
                                    "entry_calc_none",
                                    "ma25_ok",
                                    "ma25_none",
                                    "intraday_signal_ready",
                                    "intraday_signal_none",
                                    "crossed_check_entered",
                                    "crossed_true",
                                    "crossed_false",
                                    "signal_generated",
                                    "replay_signals_append_count",
                                ]:
                                    if k in pd:
                                        try:
                                            lines.append(f"{k}={int(pd.get(k) or 0)}")
                                        except Exception:
                                            continue
                            lines.append("")
                            if isinstance(cr, dict) and cr:
                                lines.append("continue_reason_counts:")
                                for k, v in sorted(cr.items(), key=lambda kv: int(kv[1]), reverse=True)[:30]:
                                    try:
                                        lines.append(f"{str(k)}: {int(v)}")
                                    except Exception:
                                        continue
                            lines.append("")

                        # =========================
                        # APPEND_SIGNAL_DEBUG（ユーザー要望）
                        # - replay_signals.append 直前の状態を先頭だけ保存
                        # =========================
                        asd = report.get("append_signal_debug") or {}
                        rows = asd.get("rows") if isinstance(asd, dict) else None
                        if isinstance(rows, list) and rows:
                            lines.append("APPEND_SIGNAL_DEBUG:")
                            for it in rows[:50]:
                                if not isinstance(it, dict):
                                    continue
                                lines.append(
                                    f"- {it.get('signal_id')} {it.get('symbol')} entry_time={it.get('entry_time_jst')} "
                                    f"excluded_from_eval={bool(it.get('excluded_from_eval', False))} excluded_reason={it.get('excluded_reason')}"
                                )
                            lines.append("")

                        # PRE/POST/CONTINUE_BEFORE_APPEND（ユーザー要望）
                        pre = report.get("pre_signal_object_debug") or {}
                        pre_rows = pre.get("rows") if isinstance(pre, dict) else None
                        if isinstance(pre_rows, list) and pre_rows:
                            lines.append("PRE_SIGNAL_OBJECT_DEBUG:")
                            for it in pre_rows[:50]:
                                if not isinstance(it, dict):
                                    continue
                                lines.append(
                                    f"- symbol={it.get('symbol')} time={it.get('time_jst')} entry_price={it.get('entry_price')} market_state={it.get('market_state')}"
                                )
                            lines.append("")

                        post = report.get("post_signal_object_debug") or {}
                        post_rows = post.get("rows") if isinstance(post, dict) else None
                        if isinstance(post_rows, list) and post_rows:
                            lines.append("POST_SIGNAL_OBJECT_DEBUG:")
                            for it in post_rows[:50]:
                                if not isinstance(it, dict):
                                    continue
                                lines.append(f"- signal_id={it.get('signal_id')} symbol={it.get('symbol')}")
                            lines.append("")

                        cb = report.get("continue_before_append") or {}
                        cb_rows = cb.get("rows") if isinstance(cb, dict) else None
                        if isinstance(cb_rows, list) and cb_rows:
                            lines.append("CONTINUE_BEFORE_APPEND:")
                            for it in cb_rows[:50]:
                                if not isinstance(it, dict):
                                    continue
                                lines.append(f"- reason={it.get('reason')} symbol={it.get('symbol')}")
                            lines.append("")

                        # EXCEPTION_BEFORE_APPEND_TRACE（ユーザー要望）
                        # - traceが無くても必ずセクションは出す
                        # - traceが0件なのに EXCEPTION_BEFORE_APPEND>0 の場合は TRACE_CAPTURE_FAILED を出す
                        exbt_txts = report.get("exception_before_append_trace_texts") or []
                        lines.append("[EXCEPTION_BEFORE_APPEND_TRACE]")
                        lines.append("")
                        if isinstance(exbt_txts, list) and exbt_txts:
                            for tx in exbt_txts[:10]:
                                lines.append(str(tx or ""))
                                lines.append("")
                        else:
                            lines.append("(no traces captured)")
                            try:
                                ccc = report.get("continue_reason_counts") or {}
                                if isinstance(ccc, dict) and int(ccc.get("EXCEPTION_BEFORE_APPEND") or 0) > 0:
                                    lines.append("TRACE_CAPTURE_FAILED")
                                    lines.append(f"trace_capture_failed_count={int(report.get('trace_capture_failed_count') or 0)}")
                            except Exception:
                                pass
                            lines.append("")

                        # =========================
                        # EVAL_FILTER_DEBUG（ユーザー要望）
                        # =========================
                        efd = report.get("eval_filter_debug") or {}
                        if isinstance(efd, dict):
                            lines.append("EVAL_FILTER_DEBUG:")
                            lines.append(f"- before_count: {int(efd.get('before_count') or 0)}")
                            lines.append(f"- after_count: {int(efd.get('after_count') or 0)}")
                            ex2 = efd.get("excluded") or []
                            if isinstance(ex2, list) and ex2:
                                lines.append("- excluded (top):")
                                for it in ex2[:50]:
                                    if not isinstance(it, dict):
                                        continue
                                    lines.append(f"  - {it.get('signal_id')}: {it.get('excluded_reason')}")
                            lines.append("")

                        # フィルタ比較（追加）
                        fc = report.get("filter_comparison") or {}
                        sd = fc.get("signal_delta") or {}
                        lines.append("【フィルタ比較】")
                        lines.append(
                            f"- signal数(仮想): ON={int(sd.get('signals_filter_on') or 0)} / OFF={int(sd.get('signals_filter_off') or 0)} / Δ={int(sd.get('delta') or 0)}"
                        )
                        lines.append(f"- 期待値変化(OFF-ON,円/100株/signal): {float(fc.get('expectancy_delta_yen_100_shares_per_signal') or 0.0):+,.0f}円")
                        lines.append(f"- WEAK時勝率(ON,BASE): {float(fc.get('weak_win_rate_pct_on') or 0.0):.1f}%")
                        rso = (fc.get("rsi_filter_off_stats") or {})
                        ato = (fc.get("atr_filter_off_stats") or {})
                        lines.append(
                            f"- RSIフィルタOFF(参考): signals={int(rso.get('signals') or 0)} expectancy={float(rso.get('expectancy_yen_100_shares_per_signal') or 0.0):+,.0f}円"
                        )
                        lines.append(
                            f"- ATRフィルタOFF(参考): signals={int(ato.get('signals') or 0)} expectancy={float(ato.get('expectancy_yen_100_shares_per_signal') or 0.0):+,.0f}円"
                        )
                        lines.append("")

                        # NORMAL/WEAK/CRASH別（追加仕様）
                        lines.append("【地合いレジーム別（ON=採用分）】")
                        for rg in ["NORMAL", "WEAK", "CRASH"]:
                            xs = regime_on.get(rg) or []
                            if not xs:
                                continue
                            a = _agg_stats(xs)
                            lines.append(f"- {rg}: signals={a['signals']} 勝率={a['win_rate_pct']:.1f}% 100株損益={a['pnl_yen_100_shares']:+,.0f}円 expectancy={a['expectancy_yen_100_shares_per_signal']:+,.0f}円")
                        lines.append("")

                        lines.append("【signal詳細】")
                        for s in replay_signals:
                            t_jst = _fmt_dt_jst_short(s.signal_time_utc)
                            excluded_s = "EXCLUDED" if bool(getattr(s, "excluded_from_eval", False)) else "OK"
                            pk = str(getattr(s, "position_kind", "BASE") or "BASE")
                            bucket = _signal_time_bucket_jst(s.signal_time_utc)
                            pnl = _pnl_yen_100_shares(s)
                            topix_fetch_ok = bool(getattr(s, "topix_fetch_ok", False))
                            fallback_used = bool(getattr(s, "fallback_used", False))
                            topix_raw = getattr(s, "topix_raw", None)
                            topix_pct = getattr(s, "topix_pct", getattr(s, "topix_chg_pct", None))
                            ms = str(getattr(s, "market_state", "") or getattr(s, "market_regime", "") or "")
                            ea = bool(getattr(s, "entry_allowed", True))
                            if s.final_profit_pct is None and s.entry_price > 0:
                                fp2 = ((float(s.last_price_after) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                            else:
                                fp2 = float(s.final_profit_pct or 0.0)
                            lines.append(
                                f"- {s.symbol} | pos={pk} | bucket={bucket} | time_jst={t_jst} | "
                                f"signal={_fmt_yen(s.signal_price)} | entry={_fmt_yen(s.entry_price)} | "
                                f"result={s.result} | final_profit_pct={fp2:.2f}% | 100株損益={pnl:+,.0f}円 | eval={excluded_s}"
                            )
                            # 追加ログ（ユーザー要望）: signal候補時のTOPIX生値/%値/market_state/ENTRY可否
                            lines.append(f"  topix_fetch_ok={topix_fetch_ok}")
                            if isinstance(topix_raw, (int, float)):
                                # TOPIX価格（例: 1987.32）
                                lines.append(f"  topix_raw={float(topix_raw):.2f}")
                            else:
                                lines.append("  topix_raw=N/A")
                            if isinstance(topix_pct, (int, float)):
                                lines.append(f"  topix_pct={float(topix_pct):+.2f}")
                            else:
                                lines.append("  topix_pct=N/A")
                            lines.append(f"  market_state={ms}")
                            lines.append(f"  entry_allowed={ea}")
                            if not topix_fetch_ok or fallback_used:
                                lines.append(f"  fallback_used={fallback_used}")

                        txt_path = os.path.join(results_dir, f"{name_base}.txt")
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + "\n")

                        # 銘柄スコアの保存（後で実運用のENTRYフィルタに使う）
                        try:
                            score_path = os.path.join(results_dir, f"{name_base}_symbol_scores.json")
                            with open(score_path, "w", encoding="utf-8") as f:
                                json.dump(report.get("symbol_scoring") or {}, f, ensure_ascii=False, indent=2)
                            latest_path = os.path.join(script_dir, "results", "symbol_scores_latest.json")
                            with open(latest_path, "w", encoding="utf-8") as f:
                                json.dump(report.get("symbol_scoring") or {}, f, ensure_ascii=False, indent=2)
                        except Exception:
                            pass

                        print(f"[{now_str()}] Replay結果を保存しました: {txt_path}")
                        print(f"[{now_str()}] Replay結果を保存しました: {json_path}")
                    except Exception as e:
                        print(f"[{now_str()}] Replay結果の保存に失敗しました: {e}")
                    print(f"\n[{now_str()}] リプレイ完了（全銘柄のデータを再生し終えました）")
                    return 0

                # -----------------------------
                # 毎ループ表示する「リプレイ時刻(JST)・進行率」
                # -----------------------------
                replay_t = max((q.market_time_utc for q in quotes if q.market_time_utc), default=None)
                replay_t_jst = _fmt_dt_jst(replay_t)

                # =========================
                # Replay Morning Screen（指定時刻）を実行
                # =========================
                # 実行条件:
                # - --replay-morning-screen が指定されている
                # - 現在のReplay時刻（JST）が指定時刻に到達した
                # - その日（JST日付）ではまだ実行していない
                if ms_time is not None and replay_t is not None:
                    rt = replay_t
                    if rt.tzinfo is None:
                        rt = rt.replace(tzinfo=timezone.utc)
                    jst_now = rt.astimezone(JST)
                    day_jst = jst_now.strftime("%Y-%m-%d")
                    hh, mm = ms_time

                    # 日付が変わったら当日の追加監視をリセット（ベースwatchに戻す）
                    if ms_current_day_jst != day_jst:
                        prev_day_jst = ms_current_day_jst
                        ms_current_day_jst = day_jst
                        active_watch = set(base_watch)

                        # -----------------------------
                        # 前日継続監視銘柄の抽出 → 当日監視へ追加
                        # -----------------------------
                        # 条件（ユーザー要件）:
                        # - WIN（= 前日にWINが存在）
                        # - 100株損益 > 0（銘柄合計）
                        # - signal数 >= 2（銘柄合計）
                        # - max_profit_pct >= 1.0（銘柄内の最大）
                        if ms_time is not None and prev_day_jst:
                            # 前日シグナルを銘柄ごとに集計
                            prev_syms: dict[str, list[ReplaySignalEval]] = {}
                            # 継続候補抽出も「期待値検証の集計対象」に合わせます
                            # （= excluded_from_eval な signal は除外）
                            carry_eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                            for ss in carry_eval_signals:
                                if _day_jst_str(ss.signal_time_utc) != prev_day_jst:
                                    continue
                                prev_syms.setdefault(ss.symbol, []).append(ss)

                            carry: list[str] = []
                            for sym, xs in prev_syms.items():
                                sig_n = len(xs)
                                if sig_n < 2:
                                    continue
                                win_n = sum(1 for x in xs if x.result == "WIN")
                                if win_n <= 0:
                                    continue
                                pnl = sum(float(_pnl_yen_100_shares_replay(x)) for x in xs)
                                if pnl <= 0:
                                    continue
                                max_profit = max(float(x.max_profit_pct()) for x in xs) if xs else 0.0
                                if max_profit < 1.0:
                                    continue
                                carry.append(sym)

                            # 翌営業日（当日）に適用するcarryoverリストとして保存
                            ms_carryover_by_day[day_jst] = sorted(set(carry))

                            # 当日の監視対象へ追加（Morning Screen TOP10 とは別枠）
                            carry_today = ms_carryover_by_day.get(day_jst) or []
                            if carry_today:
                                for sym in carry_today:
                                    if sym in active_watch:
                                        continue
                                    hist = _ensure_ms_history(sym)
                                    if hist is None:
                                        continue
                                    b, m = hist
                                    bars_by_symbol.setdefault(sym, b)
                                    meta_by_symbol.setdefault(sym, m)

                                    # idx を “今の時刻” に合わせる（timestamp>=rt の最初）
                                    seek = 0
                                    for i2, bb in enumerate(b):
                                        if bb.timestamp_utc >= rt:
                                            seek = i2
                                            break
                                    idx_by_symbol[sym] = seek

                                    # 前日終値INIT（metaにあれば）
                                    pc = m.get("previousClose")
                                    prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

                                    # 当日状態の初期化（簡易）
                                    running_day_high[sym] = float("-inf")
                                    running_day_volume[sym] = 0.0
                                    running_vwap_pv[sym] = 0.0
                                    running_vwap_v[sym] = 0.0
                                    day_key = rt.strftime("%Y-%m-%d")
                                    current_day_key[sym] = day_key
                                    for bb in b[:seek]:
                                        if bb.timestamp_utc.strftime("%Y-%m-%d") != day_key:
                                            continue
                                        running_day_high[sym] = max(float(running_day_high.get(sym, float("-inf"))), float(bb.high))
                                        running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bb.volume)
                                        tp = (float(bb.high) + float(bb.low) + float(bb.close)) / 3.0
                                        running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bb.volume)
                                        running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bb.volume)

                                    if seek > 0:
                                        prev_price_by_symbol[sym] = float(b[seek - 1].close)
                                    else:
                                        prev_price_by_symbol.setdefault(sym, None)

                                    active_watch.add(sym)

                                print(
                                    f"[{now_str()}][Replay Carryover] {day_jst} 継続監視追加: "
                                    f"{', '.join(carry_today)}"
                                )

                    if (jst_now.hour, jst_now.minute) >= (hh, mm) and day_jst not in ms_daily:
                        # -----------------------------
                        # 1) Morning Screen（Replay版）
                        # -----------------------------
                        # 初心者向けポイント:
                        # - 「その時点の市場状況」を再現するには、rt 以前のバーだけを使います。
                        # - 以降のバー（未来）は絶対に使いません。

                        def _ensure_ms_history(sym: str) -> Optional[tuple[list[ReplayBar], dict]]:
                            if sym in ms_bars_cache:
                                return (ms_bars_cache[sym], ms_meta_cache.get(sym) or {})
                            try:
                                b, m = fetch_history_1m(session, sym, range_str=fetch_range)
                                if not b:
                                    return None
                                ms_bars_cache[sym] = b
                                ms_meta_cache[sym] = m
                                return (b, m)
                            except Exception:
                                return None

                        def _bars_until_time_jst_day(
                            bars: list[ReplayBar],
                            *,
                            t_utc: datetime,
                        ) -> tuple[list[ReplayBar], Optional[float]]:
                            """
                            指定時刻までの「その日（JST日付）」のバーを返します。
                            - future（t_utcより後）は含めません
                            - prev_close は「当日最初のバーの直前のclose」を返します（取れれば）
                            """
                            if t_utc.tzinfo is None:
                                t_utc = t_utc.replace(tzinfo=timezone.utc)
                            target_day = t_utc.astimezone(JST).date()

                            day_bars: list[ReplayBar] = []
                            first_t: Optional[datetime] = None
                            for b in bars:
                                bt = b.timestamp_utc
                                if bt.tzinfo is None:
                                    bt = bt.replace(tzinfo=timezone.utc)
                                if bt.astimezone(JST).date() != target_day:
                                    continue
                                if first_t is None:
                                    first_t = bt
                                if bt <= t_utc:
                                    day_bars.append(b)
                            if not day_bars or first_t is None:
                                return ([], None)

                            prev_close: Optional[float] = None
                            prev_bar: Optional[ReplayBar] = None
                            for b in bars:
                                if b.timestamp_utc < first_t:
                                    prev_bar = b
                                else:
                                    break
                            if prev_bar is not None:
                                prev_close = float(prev_bar.close)
                            return (day_bars, prev_close)

                        ms_results: list[MorningScreenResult] = []
                        for sym in ms_universe:
                            hist = _ensure_ms_history(sym)
                            if hist is None:
                                continue
                            b, meta = hist
                            day_bars, prev_close = _bars_until_time_jst_day(b, t_utc=rt)
                            if not day_bars:
                                continue

                            last_bar = day_bars[-1]
                            price = float(last_bar.close)
                            day_high = max(float(x.high) for x in day_bars)
                            day_low = min(float(x.low) for x in day_bars)
                            vol = sum(float(x.volume) for x in day_bars)

                            # 除外条件（朝スクリーニング仕様）
                            if vol < 100_000.0:
                                continue

                            chg = _calc_change_percent(price=price, previous_close=prev_close)
                            if not isinstance(chg, (int, float)) or float(chg) < 0.0:
                                continue

                            # VWAP（概算）
                            pv = 0.0
                            vv = 0.0
                            for x in day_bars:
                                v = float(x.volume)
                                if v <= 0:
                                    continue
                                tp = (float(x.high) + float(x.low) + float(x.close)) / 3.0
                                pv += tp * v
                                vv += v
                            vwap = (pv / vv) if vv > 0 else None

                            # MA25 / 5日平均出来高（通常と同じ関数を再利用）
                            ma25: Optional[float] = None
                            avg5: Optional[float] = None
                            try:
                                cached = ma25_cache.get(sym)
                                if cached:
                                    cached_ma25, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                        ma25 = cached_ma25
                                if ma25 is None:
                                    fetched = fetch_ma25(session, sym)
                                    if fetched is not None:
                                        ma25_cache[sym] = (float(fetched), time.perf_counter())
                                        ma25 = float(fetched)
                            except Exception:
                                ma25 = None
                            try:
                                cached = avg5_cache.get(sym)
                                if cached:
                                    cached_avg5, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                        avg5 = cached_avg5
                                if avg5 is None:
                                    fetched = fetch_avg_volume_5(session, sym)
                                    if fetched is not None:
                                        avg5_cache[sym] = (float(fetched), time.perf_counter())
                                        avg5 = float(fetched)
                            except Exception:
                                avg5 = None

                            q_ms = Quote(
                                symbol=sym,
                                price=price,
                                currency=str(meta.get("currency") or "JPY"),
                                previous_close=float(prev_close) if isinstance(prev_close, (int, float)) else None,
                                change_percent=float(chg) if isinstance(chg, (int, float)) else None,
                                day_high=float(day_high),
                                day_low=float(day_low),
                                volume=float(vol),
                                market_time_utc=rt,
                                market_cap=None,
                            )

                            day_range_pct = _calc_day_range_pct(
                                day_high=q_ms.day_high,
                                day_low=q_ms.day_low,
                                previous_close=q_ms.previous_close,
                                price=float(q_ms.price),
                            )
                            score, reasons, vol_spike_ratio = _morning_screen_score(
                                q=q_ms,
                                vwap=vwap,
                                ma25=ma25,
                                avg_vol5=avg5,
                                day_range_pct=day_range_pct,
                            )
                            ms_results.append(
                                MorningScreenResult(
                                    symbol=sym,
                                    score=int(score),
                                    quote=q_ms,
                                    vwap=vwap,
                                    ma25=ma25,
                                    avg_vol5=avg5,
                                    vol_spike_ratio=vol_spike_ratio,
                                    day_range_pct=day_range_pct,
                                    reasons=reasons,
                                )
                            )

                        ms_sorted = sorted(
                            ms_results,
                            key=lambda r: (r.score, float(r.quote.change_percent or 0.0), float(r.quote.volume or 0.0)),
                            reverse=True,
                        )
                        top10 = ms_sorted[:10]
                        selected_syms = [r.symbol for r in top10]
                        scores_by_symbol = {r.symbol: int(r.score) for r in top10}

                        ms_daily[day_jst] = {
                            "date": day_jst,
                            "hhmm": f"{hh:02d}:{mm:02d}",
                            "screen_time_utc": rt,
                            "symbols": selected_syms,
                            "scores": scores_by_symbol,
                            # 前日継続監視（別枠）
                            "carryover_symbols": list(ms_carryover_by_day.get(day_jst) or []),
                        }

                        # -----------------------------
                        # 2) 監視銘柄へ自動追加（当日ぶん）
                        # -----------------------------
                        for sym in selected_syms:
                            if sym in active_watch:
                                continue
                            hist = _ensure_ms_history(sym)
                            if hist is None:
                                continue
                            b, m = hist
                            bars_by_symbol.setdefault(sym, b)
                            meta_by_symbol.setdefault(sym, m)

                            # idx を “今の時刻” に合わせる（timestamp>=rt の最初）
                            seek = 0
                            for i2, bb in enumerate(b):
                                if bb.timestamp_utc >= rt:
                                    seek = i2
                                    break
                            idx_by_symbol[sym] = seek

                            # 前日終値INIT（metaにあれば）
                            pc = m.get("previousClose")
                            prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

                            # 当日状態の初期化（簡易）
                            running_day_high[sym] = float("-inf")
                            running_day_volume[sym] = 0.0
                            running_vwap_pv[sym] = 0.0
                            running_vwap_v[sym] = 0.0
                            day_key = rt.strftime("%Y-%m-%d")
                            current_day_key[sym] = day_key
                            for bb in b[:seek]:
                                if bb.timestamp_utc.strftime("%Y-%m-%d") != day_key:
                                    continue
                                running_day_high[sym] = max(float(running_day_high.get(sym, float("-inf"))), float(bb.high))
                                running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bb.volume)
                                tp = (float(bb.high) + float(bb.low) + float(bb.close)) / 3.0
                                running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bb.volume)
                                running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bb.volume)

                            if seek > 0:
                                prev_price_by_symbol[sym] = float(b[seek - 1].close)
                            else:
                                prev_price_by_symbol.setdefault(sym, None)

                            active_watch.add(sym)

                        picked = ", ".join([f"{s}({scores_by_symbol.get(s, 0)})" for s in selected_syms]) if selected_syms else "(none)"
                        print(f"[{now_str()}][Replay MorningScreen] {day_jst} {hh:02d}:{mm:02d} JST picked: {picked}")

                pct = 0
                if total_bars > 0:
                    pct = int((progressed_bars / total_bars) * 100)
                    if pct > 100:
                        pct = 100
                # 例: [15:23:00][Replay 72%] replay_time_jst=2026-05-01 15:23:00
                if (not fast_mode) or bool(replay_fast_verbose) or (pct % 10 == 0):
                    print(f"[{now_str()}][Replay {pct}%] replay_time_jst={replay_t_jst}")

                # -----------------------------
                # signal後の価格推移を更新（期待値検証）
                # -----------------------------
                # このループの「現在値」で、未解決signalの max/min と take/stop 到達を更新します。
                for q in quotes:
                    idxs = active_signal_indices_by_symbol.get(q.symbol) or []
                    bidxs = blocked_signal_indices_by_symbol.get(q.symbol) or []
                    if not idxs and not bidxs:
                        continue
                    # この時点では「その日の累積VWAP」は running_vwap_* から計算できます。
                    vv = float(running_vwap_v.get(q.symbol, 0.0))
                    vwap_now = (float(running_vwap_pv.get(q.symbol, 0.0)) / vv) if vv > 0 else None

                    # recent_5m_low は「再生済みバー」から作ります（直近5分・最新足は除外）
                    played = idx_by_symbol.get(q.symbol, 0)
                    bars_played = bars_by_symbol.get(q.symbol, [])[:played]
                    recent_5m_low = None
                    if len(bars_played) >= 6:
                        lows_window = [float(b.low) for b in bars_played[-6:-1]]
                        if lows_window:
                            recent_5m_low = float(min(lows_window))
                    for idx in list(idxs):
                        s = replay_signals[idx]
                        s.update_with_price(
                            time_utc=(q.market_time_utc or datetime.now(tz=timezone.utc)),
                            price=float(q.price),
                            vwap=vwap_now,
                            recent_5m_low=recent_5m_low,
                            early_exit_before_partial_take=bool(replay_early_exit_before_stop),
                            early_exit_vwap=bool(replay_early_exit_vwap),
                            early_exit_recent_low=bool(replay_early_exit_recent_low),
                        )
                        if s.resolved:
                            # 解決済みは active から外す（次ループ以降の更新を省略）
                            try:
                                idxs.remove(idx)
                            except Exception:
                                pass

                            # -----------------------------
                            # 当日累計損益%の更新（当日停止条件）
                            # -----------------------------
                            # 初心者向けポイント:
                            # - 「その日の同じ銘柄で負けが続く」状況を止めるための安全装置です。
                            # - ここでは “解決した時点” の final_profit_pct を足し込みます。
                            # - 1つのsignalを何度も数えると壊れるので、indexで一度だけ計上します。
                            if idx not in resolved_counted_signal_indices:
                                resolved_counted_signal_indices.add(idx)
                                day_jst = _day_jst_str(s.signal_time_utc)
                                k = (day_jst, s.symbol)
                                fp = float(s.final_profit_pct) if isinstance(s.final_profit_pct, (int, float)) else 0.0
                                cur = float(daily_cum_profit_pct_by_day_symbol.get(k, 0.0))
                                new_v = cur + fp
                                daily_cum_profit_pct_by_day_symbol[k] = new_v
                                if new_v <= -3.0:
                                    trading_stopped_by_day_symbol[k] = True
                    if idxs:
                        active_signal_indices_by_symbol[q.symbol] = idxs
                    else:
                        active_signal_indices_by_symbol.pop(q.symbol, None)

                    # 地合いで禁止された“影signal”も更新（ADD判定等には使わない）
                    for idx in list(bidxs):
                        s = replay_signals[idx]
                        s.update_with_price(
                            time_utc=(q.market_time_utc or datetime.now(tz=timezone.utc)),
                            price=float(q.price),
                            vwap=vwap_now,
                            recent_5m_low=recent_5m_low,
                            early_exit_before_partial_take=bool(replay_early_exit_before_stop),
                            early_exit_vwap=bool(replay_early_exit_vwap),
                            early_exit_recent_low=bool(replay_early_exit_recent_low),
                        )
                        if s.resolved:
                            try:
                                bidxs.remove(idx)
                            except Exception:
                                pass
                    if bidxs:
                        blocked_signal_indices_by_symbol[q.symbol] = bidxs
                    else:
                        blocked_signal_indices_by_symbol.pop(q.symbol, None)

                # -----------------------------
                # ここから下は「通常の判定ロジック」と同じ流れ
                # （データ供給源が realtime(quote) か replay(1m) かの違いだけ）
                # -----------------------------
                candidates: list[Quote] = []
                skip_reasons_by_symbol: dict[str, list[str]] = {}
                ma25_by_symbol: dict[str, float] = {}
                avg5_by_symbol: dict[str, float] = {}
                vol_spike_ratio_by_symbol: dict[str, Optional[float]] = {}
                vwap_by_symbol: dict[str, Optional[float]] = {}
                intraday_by_symbol: dict[str, IntradaySignals] = {}
                entry_cross_by_symbol: dict[str, bool] = {}
                prev_recent_5m_low_by_symbol: dict[str, Optional[float]] = {}
                prev_recent_5m_high_by_symbol: dict[str, Optional[float]] = {}

                for q in quotes:
                    # 指数（代用ETF）は候補評価の対象外（地合い判定専用）
                    if q.symbol in set(index_syms):
                        continue
                    reasons: list[str] = []

                    if q.change_percent is None:
                        reasons.append("前日終値取得失敗")
                    if q.day_high is None:
                        reasons.append("当日高値が取得できない")
                    if q.volume is None:
                        reasons.append("出来高が取得できない")

                    if q.change_percent is not None:
                        if q.change_percent < MIN_CHANGE_PCT:
                            reasons.append("前日比不足")
                        if q.change_percent >= MAX_CHANGE_PCT:
                            reasons.append("急騰しすぎ")

                    if q.day_high is not None:
                        if q.price < (MIN_RATIO_TO_DAY_HIGH * q.day_high):
                            reasons.append("高値付近ではない")

                    if q.volume is not None:
                        if q.volume < float(MIN_VOLUME):
                            reasons.append("出来高不足")

                    # 5日平均出来高（通常と同じく API 取得 + キャッシュ）
                    avg5: Optional[float] = None
                    if q.volume is not None:
                        try:
                            cached = avg5_cache.get(q.symbol)
                            if cached:
                                cached_avg5, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                    avg5 = cached_avg5
                            if avg5 is None:
                                fetched = fetch_avg_volume_5(session, q.symbol)
                                if fetched is not None:
                                    avg5_cache[q.symbol] = (float(fetched), time.perf_counter())
                                    avg5 = float(fetched)
                        except Exception:
                            avg5 = None

                    if avg5 is not None:
                        avg5_by_symbol[q.symbol] = avg5
                        ratio = q.volume / avg5 if avg5 > 0 else 0.0
                        vol_spike_ratio_by_symbol[q.symbol] = ratio
                    else:
                        vol_spike_ratio_by_symbol[q.symbol] = None

                    # MA25（通常と同じく API 取得 + キャッシュ）
                    ma25: Optional[float] = None
                    try:
                        cached = ma25_cache.get(q.symbol)
                        if cached:
                            cached_ma25, fetched_at = cached
                            if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                ma25 = cached_ma25
                        if ma25 is None:
                            fetched = fetch_ma25(session, q.symbol)
                            if fetched is not None:
                                ma25_cache[q.symbol] = (float(fetched), time.perf_counter())
                                ma25 = float(fetched)
                    except Exception:
                        ma25 = None

                    if ma25 is None:
                        reasons.append("25日線が取得できない")
                    else:
                        ma25_by_symbol[q.symbol] = ma25
                        if q.price <= ma25:
                            reasons.append("25日線以下")

                    # VWAP（リプレイデータから概算）
                    vv = running_vwap_v.get(q.symbol, 0.0)
                    vwap = (running_vwap_pv.get(q.symbol, 0.0) / vv) if vv > 0 else None
                    vwap_by_symbol[q.symbol] = vwap
                    # ----------------------------------------
                    # 追加条件（エントリータイミング検知）
                    # ----------------------------------------
                    # リプレイでは「今までに再生したバー」を使って直近シグナルを計算します。
                    # - ここでは簡単に、bars_by_symbol の先頭から「今のindexまで」を渡して計算します。
                    #   （銘柄数が多い場合は最適化余地がありますが、まずは分かりやすさ優先）
                    played = idx_by_symbol.get(q.symbol, 0)
                    bars_played = bars_by_symbol.get(q.symbol, [])[:played]
                    sig = calc_intraday_signals_from_series(
                        price=float(q.price),
                        closes=[b.close for b in bars_played],
                        highs=[b.high for b in bars_played],
                        vols=[b.volume for b in bars_played],
                        vwap=vwap,
                    )
                    intraday_by_symbol[q.symbol] = sig
                    _LATEST_INTRADAY_SIGNALS[q.symbol] = sig

                    # 前場高値を更新（後場弱地合い判定用）
                    try:
                        ttmp = q.market_time_utc or datetime.now(tz=timezone.utc)
                        if ttmp.tzinfo is None:
                            ttmp = ttmp.replace(tzinfo=timezone.utc)
                        jst_tmp = ttmp.astimezone(JST)
                        day_jst_tmp = jst_tmp.strftime("%Y-%m-%d")
                        hm_tmp = jst_tmp.hour * 60 + jst_tmp.minute
                        if hm_tmp < (11 * 60 + 30):  # 前場終わりまで
                            k2 = (day_jst_tmp, q.symbol)
                            cur_h = float(q.day_high) if isinstance(q.day_high, (int, float)) else float(q.price)
                            morning_high_by_day_symbol[k2] = max(float(morning_high_by_day_symbol.get(k2, float("-inf"))), cur_h)
                    except Exception:
                        pass

                    # =========================
                    # 追加ポジション（ADD）判定（改善版 / Replay期待値検証に反映）
                    # =========================
                    # 目的:
                    # - 弱い銘柄や失速局面でのADDを抑制し、損失拡大を防ぎたい。
                    #
                    # 追加条件（ユーザー要件）:
                    # - current_price > average_entry_price
                    # - current_price > vwap
                    # - current_price >= recent_5m_high * 1.001
                    # - current_price > recent_5m_low
                    # - current_volume > recent_average_volume（直近1分出来高 > 直近5分平均）
                    # - 出来高増加継続（前回も今回も増加=True）
                    # - 最大ADD回数 = 2
                    # - 前回ADDから最低5分経過
                    # - VWAP乖離率 > 3.0% の場合ADD禁止
                    # - 直近5分上昇率 > 2.0% の場合ADD禁止（急騰直後ADD防止）
                    #
                    # ADD発生時ログ:
                    # - ADD理由 / 平均取得単価 / 現在保有数 / ADD回数 / VWAP乖離率 / 含み損益率
                    if bool(enable_add):
                        try:
                            # “保有中”判定: active に未解決ポジションがある
                            active_idxs = active_signal_indices_by_symbol.get(q.symbol) or []
                            holding = bool(active_idxs)

                            # 時刻（JST）
                            tcur = q.market_time_utc
                            if tcur is None:
                                tcur = datetime.now(tz=timezone.utc)
                            if tcur.tzinfo is None:
                                tcur = tcur.replace(tzinfo=timezone.utc)
                            jst_cur = tcur.astimezone(JST)

                            # 14:30 以降は禁止
                            after_1430 = (jst_cur.hour * 60 + jst_cur.minute) >= (14 * 60 + 30)

                            day_jst = _day_jst_str(tcur)
                            key = (day_jst, q.symbol)

                            add_count = int(add_count_by_day_symbol.get(key, 0))
                            # 前回ADDからの経過
                            last_add_t = last_add_time_by_day_symbol.get(key)
                            min_5min_passed = True
                            if isinstance(last_add_t, datetime):
                                dt_sec = (tcur - last_add_t).total_seconds()
                                min_5min_passed = dt_sec >= 5 * 60

                            # 平均取得単価（未解決ポジションを「保有」とみなし、各ポジション=100株として平均）
                            total_qty = 0
                            total_cost = 0.0
                            for idx in active_idxs:
                                try:
                                    ps = replay_signals[int(idx)]
                                except Exception:
                                    continue
                                qty = 100
                                total_qty += qty
                                total_cost += float(ps.entry_price) * float(qty)
                            avg_entry: Optional[float] = None
                            if total_qty > 0:
                                avg_entry = total_cost / float(total_qty)

                            # 当日停止チェック（-3%超）
                            stopped_today = bool(trading_stopped_by_day_symbol.get(key, False))
                            # 地合いWEAK/CRASHのときはADD禁止（仕様）
                            # - 前ループの判定（market_regime_last）を使います（1分遅れで適用）
                            add_allowed_by_regime = (str(market_regime_last) == "NORMAL")

                            # 出来高増加「継続」
                            vol_inc_now = (sig.vol_3m_gt_prev_3m is True)
                            vol_inc_prev = bool(prev_vol_inc_by_day_symbol.get(key, False))
                            vol_inc_cont = bool(vol_inc_prev and vol_inc_now)
                            prev_vol_inc_by_day_symbol[key] = bool(vol_inc_now)  # 次ループ用に更新

                            # VWAP乖離率（禁止条件）
                            vwap_dist = sig.vwap_distance_pct if isinstance(sig.vwap_distance_pct, (int, float)) else None
                            vwap_dist_block = (vwap_dist is not None) and (float(vwap_dist) > 3.0)

                            # recent_5m_high の上に「少しだけ上抜け」しているか（1.001倍）
                            rebreak_strong = False
                            if isinstance(sig.recent_5m_high, (int, float)):
                                rebreak_strong = float(q.price) >= float(sig.recent_5m_high) * 1.001

                            # recent_5m_low（直近5分安値・最新足除外）:
                            # - 押し目崩れ（直近安値割れ）の局面でADDしない
                            played2 = idx_by_symbol.get(q.symbol, 0)
                            bars_played2 = bars_by_symbol.get(q.symbol, [])[:played2]
                            recent_5m_low2: Optional[float] = None
                            if len(bars_played2) >= 6:
                                lows_window2 = [float(b.low) for b in bars_played2[-6:-1]]
                                if lows_window2:
                                    recent_5m_low2 = float(min(lows_window2))
                            low_not_broken = True
                            if isinstance(recent_5m_low2, (int, float)):
                                low_not_broken = float(q.price) > float(recent_5m_low2)

                            # 出来高条件（勢い確認）:
                            # - 直近1分出来高 > 直近5分平均出来高（最新足は「現在」として使う）
                            cur_bar_vol: Optional[float] = None
                            avg5_bar_vol: Optional[float] = None
                            vol_strong = False
                            if bars_played2:
                                cur_bar_vol = float(bars_played2[-1].volume)
                            if len(bars_played2) >= 6:
                                vv5 = [float(b.volume) for b in bars_played2[-6:-1]]
                                if vv5:
                                    avg5_bar_vol = float(sum(vv5)) / float(len(vv5))
                            if cur_bar_vol is not None and avg5_bar_vol is not None:
                                vol_strong = float(cur_bar_vol) > float(avg5_bar_vol)

                            # 急騰直後ブロック（直近5分上昇率 > 2%）:
                            rise_5m_pct: Optional[float] = None
                            rise_block = False
                            if len(bars_played2) >= 6:
                                base_close = float(bars_played2[-6].close)
                                if base_close > 0:
                                    rise_5m_pct = ((float(q.price) - base_close) / base_close) * 100.0
                                    rise_block = float(rise_5m_pct) > 2.0

                            # 追加条件チェック
                            conds_ok = True
                            conds_ok = conds_ok and holding
                            conds_ok = conds_ok and (not after_1430)
                            conds_ok = conds_ok and (not stopped_today)
                            conds_ok = conds_ok and bool(add_allowed_by_regime)
                            conds_ok = conds_ok and (add_count < 2)
                            conds_ok = conds_ok and min_5min_passed
                            conds_ok = conds_ok and (avg_entry is not None and float(q.price) > float(avg_entry))
                            conds_ok = conds_ok and (isinstance(vwap, (int, float)) and float(q.price) > float(vwap))
                            conds_ok = conds_ok and bool(rebreak_strong)
                            conds_ok = conds_ok and bool(low_not_broken)
                            conds_ok = conds_ok and bool(vol_strong)
                            conds_ok = conds_ok and bool(vol_inc_cont)
                            conds_ok = conds_ok and (not vwap_dist_block)
                            conds_ok = conds_ok and (not rise_block)

                            if conds_ok:
                                next_add = add_count + 1
                                # 追加ポジションの利確幅（前の仕様のまま）
                                if next_add == 1:
                                    tp_pct = 0.025
                                    kind = "ADD1"
                                else:
                                    tp_pct = 0.015
                                    kind = "ADD2"

                                entry2 = float(q.price)
                                stop2 = entry2 * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                                take2 = entry2 * (1.0 + tp_pct)

                                s2 = ReplaySignalEval(
                                    symbol=q.symbol,
                                    signal_time_utc=tcur,
                                    signal_price=float(q.price),
                                    entry_price=float(entry2),
                                    stop_price=float(stop2),
                                    take_price=float(take2),
                                    max_price_after=float(q.price),
                                    min_price_after=float(q.price),
                                    last_price_after=float(q.price),
                                    position_kind=str(kind),
                                    exit_style="fixed",
                                )
                                # 事故分析指標（ADD）
                                rsi14 = _calc_rsi14([b.close for b in bars_played2])
                                atr14 = _calc_atr14([b.high for b in bars_played2], [b.low for b in bars_played2], [b.close for b in bars_played2])
                                atr_pct = (float(atr14) / float(q.price) * 100.0) if (atr14 is not None and float(q.price) > 0) else None
                                rs_vs_topix = None
                                if q.change_percent is not None and topix_chg is not None:
                                    rs_vs_topix = float(q.change_percent) - float(topix_chg)
                                setattr(s2, "rsi14", rsi14)
                                setattr(s2, "atr14", atr14)
                                setattr(s2, "atr_pct", atr_pct)
                                setattr(s2, "vwap_distance_pct", (sig.vwap_distance_pct if sig else None))
                                setattr(s2, "relative_strength_vs_topix_pct", rs_vs_topix)
                                # APPEND_SIGNAL_DEBUG（ユーザー要望）: ADDも同じリストに入れます
                                try:
                                    pipeline_debug["replay_signals_append_count"] = int(pipeline_debug.get("replay_signals_append_count", 0)) + 1
                                    pipeline_debug["signal_generated"] = int(pipeline_debug.get("signal_generated", 0)) + 1
                                    if len(append_signal_debug_rows) < int(APPEND_SIGNAL_DEBUG_MAX_ROWS):
                                        append_signal_debug_rows.append(
                                            {
                                                "signal_id": str(getattr(s2, "signal_id", "") or ""),
                                                "symbol": str(getattr(s2, "symbol", "") or ""),
                                                "entry_time_jst": str(_fmt_dt_jst_short(getattr(s2, "signal_time_utc", None))),
                                                "excluded_from_eval": bool(getattr(s2, "excluded_from_eval", False)),
                                                "excluded_reason": str(getattr(s2, "excluded_reason", "") or ""),
                                            }
                                        )
                                except Exception:
                                    pass
                                replay_signals.append(s2)
                                idx2 = len(replay_signals) - 1
                                active_signal_indices_by_symbol.setdefault(q.symbol, []).append(idx2)

                                # 状態更新
                                add_count_by_day_symbol[key] = next_add
                                last_add_time_by_day_symbol[key] = tcur

                                # 含み損益率（平均取得単価ベース）
                                upnl_pct = 0.0
                                if avg_entry is not None and float(avg_entry) > 0:
                                    upnl_pct = ((float(q.price) - float(avg_entry)) / float(avg_entry)) * 100.0

                                # ADD理由ログ（要求項目）
                                reason_parts: list[str] = []
                                reason_parts.append("price>avg_entry")
                                reason_parts.append("price>VWAP")
                                reason_parts.append(">=recent5m_high*1.001")
                                reason_parts.append("price>recent5m_low")
                                reason_parts.append("vol_1m>avg_5m")
                                reason_parts.append("vol_inc_cont")
                                reason_parts.append("cooldown>=5m")
                                reason_parts.append("vwap_dist<=3.0")
                                reason_parts.append("rise_5m<=2.0")
                                reason_text = " / ".join(reason_parts)

                                vd_s = "N/A" if vwap_dist is None else f"{float(vwap_dist):.2f}%"
                                rv_s = "N/A" if rise_5m_pct is None else f"{float(rise_5m_pct):.2f}%"
                                curv_s = "N/A" if cur_bar_vol is None else f"{float(cur_bar_vol):,.0f}"
                                avgv_s = "N/A" if avg5_bar_vol is None else f"{float(avg5_bar_vol):,.0f}"
                                print(f"[{now_str()}][ADD] {day_jst} {q.symbol} {kind}")
                                print(f"  理由: {reason_text}")
                                print(f"  平均取得単価: {_fmt_yen(avg_entry)}")
                                print(f"  現在保有数: {int(total_qty)}株")
                                print(f"  ADD回数: {next_add}/2")
                                print(f"  VWAP乖離率: {vd_s}")
                                print(f"  含み損益率: {upnl_pct:+.2f}%")
                                print(f"  直近5分上昇率: {rv_s}  （>2%はADD禁止）")
                                print(f"  出来高(直近1分): {curv_s} / (直近5分平均): {avgv_s}")
                        except Exception:
                            # 追加ポジションは“検証補助”なので、失敗しても通常の判定は継続します。
                            pass

                    # 1) VWAP乖離（必須）
                    if sig.vwap_distance_pct is None:
                        reasons.append("VWAP取得不可")
                    else:
                        if sig.vwap_distance_pct < float(VWAP_DISTANCE_PCT):
                            reasons.append("VWAP乖離不足")

                    # 2) 直近5分高値ブレイク（必須）
                    if sig.recent_5m_high is None:
                        reasons.append("直近5分高値が取れない")
                    else:
                        if float(q.price) <= float(sig.recent_5m_high):
                            reasons.append("5分高値ブレイク未成立")

                    # 3) 上昇傾向（必須）
                    if sig.price_5min_ago is None:
                        reasons.append("5分前価格が取れない")
                    else:
                        if float(q.price) <= float(sig.price_5min_ago):
                            reasons.append("上昇傾向なし")

                    if not reasons:
                        candidates.append(q)
                    else:
                        skip_reasons_by_symbol[q.symbol] = reasons

                candidate_symbols = {q.symbol for q in candidates}
                should_print = (not only_changes) or (candidate_symbols != last_candidates)
                if should_print:
                    # ここは「候補が何件か」だけを短く表示（時刻/進行率は上で毎回表示済み）
                    print(f"[{now_str()}] 条件一致: {len(candidates)} 銘柄")
                last_candidates = candidate_symbols

                # デバッグ: 候補が0の原因（スキップ理由ランキング）
                # - signal候補（crossed）が一度も出ない時の切り分け用
                if bool(replay_market_debug):
                    # fastモードは出力を抑えるため、10%ごとにだけ出します
                    if (not fast_mode) or bool(replay_fast_verbose) or (pct % 10 == 0):
                        rc: dict[str, int] = {}
                        for rs in skip_reasons_by_symbol.values():
                            for r in (rs or []):
                                rc[r] = int(rc.get(r, 0)) + 1
                        if rc:
                            top = sorted(rc.items(), key=lambda kv: int(kv[1]), reverse=True)[:10]
                            msg = " / ".join([f"{k}={v}" for k, v in top])
                            print(f"[{now_str()}][DEBUG] skip理由TOP: {msg}")

                # -----------------------------
                # 地合い判定（このループの全体に対して1回だけ）
                # -----------------------------
                # 条件（いずれかでENTRY禁止）:
                # ① 日経(代用ETF)がVWAP下
                # ② TOPIX(代用ETF)が前日比マイナス
                # ③ 上昇銘柄割合 < 40%
                # ④ 直近30分 ENTRY失敗率 > 60%
                # ⑤ 高値更新率が低い（ここでは“高値付近の銘柄割合”で代用）
                # 地合いレジーム:
                # - NORMAL: 現在条件そのまま
                # - WEAK:   ENTRY条件を厳格化（禁止しない）
                # - CRASH:  初めてENTRY禁止
                market_regime = "NORMAL"  # "NORMAL" / "WEAK" / "CRASH"
                market_reasons: list[str] = []
                # 候補処理（signal生成）側でも参照されるため、先にデフォルトを用意します。
                # これにより「地合い判定try内で例外→未定義のまま参照」の事故を防ぎます。
                topix_fetch_ok = False
                fallback_used = True
                topix_price_raw = None
                topix_prev_close = None
                topix_chg_raw = None
                topix_chg = None
                topix_chg_ok = False
                try:
                    # 時刻（JST）
                    rt = replay_t
                    if rt is None:
                        rt = datetime.now(tz=timezone.utc)
                    if rt.tzinfo is None:
                        rt = rt.replace(tzinfo=timezone.utc)
                    jst_now = rt.astimezone(JST)
                    hm_now = jst_now.hour * 60 + jst_now.minute
                    day_jst_now = jst_now.strftime("%Y-%m-%d")

                    # ①/②: 指数ETF
                    q_nk = next((qq for qq in quotes if qq.symbol == INDEX_NIKKEI_ETF), None)
                    q_tx = next((qq for qq in quotes if qq.symbol == INDEX_TOPIX_ETF), None)
                    vwap_nk = vwap_by_symbol.get(INDEX_NIKKEI_ETF)
                    nikkei_below_vwap = False
                    if q_nk is not None and isinstance(vwap_nk, (int, float)) and float(q_nk.price) < float(vwap_nk):
                        nikkei_below_vwap = True
                        market_reasons.append("NIKKEI<VWAP")

                    def _normalize_pct_maybe(raw: Optional[float]) -> Optional[float]:
                        """
                        Yahoo/Replayの値が「%」ではなく「比率（例: -0.0082）」で混ざるケースを吸収します。
                        - raw=-0.82   -> -0.82
                        - raw=-0.0082 -> -0.82
                        """
                        if raw is None:
                            return None
                        try:
                            v = float(raw)
                        except Exception:
                            return None
                        # 典型: -0.0082 のような「比率」→ % に変換
                        if abs(v) <= 1.0 and abs(v * 100.0) <= 20.0:
                            return v * 100.0
                        return v

                    topix_fetch_ok = False
                    fallback_used = False
                    topix_price_raw = None
                    topix_prev_close = None
                    # 観測された生のchange%（比較には使わない。単位崩れ確認用）
                    topix_chg_raw = None

                    if q_tx is not None and isinstance(getattr(q_tx, "price", None), (int, float)) and math.isfinite(float(q_tx.price)):
                        topix_fetch_ok = True
                        topix_price_raw = float(q_tx.price)
                        if isinstance(getattr(q_tx, "previous_close", None), (int, float)) and math.isfinite(float(q_tx.previous_close)):
                            topix_prev_close = float(q_tx.previous_close)
                        if isinstance(getattr(q_tx, "change_percent", None), (int, float)) and math.isfinite(float(q_tx.change_percent)):
                            topix_chg_raw = float(q_tx.change_percent)

                    # %（比較直前の値）は「必ず前日終値基準で再計算」する
                    # 重要: _calc_change_percent は % を返します（*100済み）なので、ここでは二重に *100 しない。
                    topix_chg = (
                        _calc_change_percent(price=float(topix_price_raw), previous_close=float(topix_prev_close))
                        if (
                            isinstance(topix_price_raw, (int, float))
                            and isinstance(topix_prev_close, (int, float))
                            and float(topix_prev_close) > 0
                        )
                        else None
                    )

                    # 取得失敗/NaN/欠損は fallback 扱い（= CRASH判定に使わない）
                    if (not topix_fetch_ok) or (topix_prev_close is None) or (topix_chg is None) or (not math.isfinite(float(topix_chg))):
                        fallback_used = True

                    # ③: 上昇銘柄割合
                    up = 0
                    tot = 0
                    for qq in quotes:
                        if qq.symbol in set(index_syms):
                            continue
                        if qq.change_percent is None:
                            continue
                        tot += 1
                        if float(qq.change_percent) > 0:
                            up += 1
                    rising_ratio = (up / tot) if tot > 0 else 0.0
                    if tot > 0 and rising_ratio < float(MARKET_RISING_RATIO_MIN):
                        market_reasons.append(f"rising<{int(MARKET_RISING_RATIO_MIN*100)}%")

                    # ④: 直近30分のENTRY失敗率（ReplaySignalEvalの結果から）
                    win30 = 0
                    lose30 = 0
                    resolved30 = 0
                    t_from = rt - timedelta(minutes=30)
                    for s in replay_signals:
                        if str(getattr(s, "position_kind", "BASE") or "BASE") != "BASE":
                            continue
                        st = s.signal_time_utc
                        if st.tzinfo is None:
                            st = st.replace(tzinfo=timezone.utc)
                        if st < t_from:
                            continue
                        if not bool(getattr(s, "resolved", False)):
                            continue
                        resolved30 += 1
                        if s.result == "WIN":
                            win30 += 1
                        elif s.result == "LOSE":
                            lose30 += 1
                    fail_rate30 = (lose30 / resolved30) if resolved30 > 0 else 0.0
                    if resolved30 >= 3 and fail_rate30 > float(MARKET_ENTRY_FAIL_RATE_30M_MAX):
                        market_reasons.append("fail30m>60%")

                    # ⑤: 高値更新率が低い（代用: 高値付近の銘柄割合）
                    near_high = 0
                    tot2 = 0
                    for qq in quotes:
                        if qq.symbol in set(index_syms):
                            continue
                        if qq.day_high is None:
                            continue
                        tot2 += 1
                        if float(qq.price) >= float(qq.day_high) * 0.999:
                            near_high += 1
                    high_ratio = (near_high / tot2) if tot2 > 0 else 0.0
                    if tot2 > 0 and high_ratio < float(MARKET_HIGH_UPDATE_RATIO_MIN):
                        market_reasons.append("high_update_low")

                    # 後場弱地合いフィルタ（12:30-14:00）
                    if AFTERNOON_FILTER_START_MIN <= hm_now < AFTERNOON_FILTER_END_MIN:
                        # 前場高値更新率
                        brk = 0
                        tot3 = 0
                        for qq in quotes:
                            if qq.symbol in set(index_syms):
                                continue
                            k3 = (day_jst_now, qq.symbol)
                            mh = morning_high_by_day_symbol.get(k3)
                            if mh is None or not isinstance(mh, (int, float)) or float(mh) <= 0:
                                continue
                            tot3 += 1
                            if float(qq.price) > float(mh) * 1.000:
                                brk += 1
                        brk_ratio = (brk / tot3) if tot3 > 0 else 0.0

                        # VWAP下銘柄割合（watchのみ）
                        below = 0
                        tot4 = 0
                        for qq in quotes:
                            if qq.symbol in set(index_syms):
                                continue
                            vw = vwap_by_symbol.get(qq.symbol)
                            if not isinstance(vw, (int, float)):
                                continue
                            tot4 += 1
                            if float(qq.price) < float(vw):
                                below += 1
                        below_ratio = (below / tot4) if tot4 > 0 else 0.0

                        # 指数弱い（①/②のどちらか）
                        idx_weak = ("NIKKEI<VWAP" in market_reasons) or ("TOPIX_WEAK" in market_reasons)

                        if (tot3 > 0 and brk_ratio < float(AFTERNOON_BREAK_MORNING_HIGH_RATIO_MIN)) and (
                            below_ratio > float(MARKET_VWAP_BELOW_RATIO_MAX) or idx_weak
                        ):
                            market_reasons.append("afternoon_weak")

                    # CRASH判定（ここだけはENTRY禁止）
                    # 方針:
                    # - CRASHは「TOPIX急落」のみに絞る（signal数ゼロrun多発の原因になりやすい）
                    # - breadth（上昇銘柄割合/高値付近割合）は WEAK 理由へ寄せる
                    crash = False
                    # 安全装置:
                    # - Replayの前日終値が欠損/切替失敗すると change% が異常値になることがあるため、
                    #   非現実的な変動（例: -50% など）は CRASH 判定に使わない
                    topix_chg_ok = (topix_chg is not None) and (abs(float(topix_chg)) <= 20.0)
                    if topix_chg_ok and float(topix_chg) <= float(CRASH_TOPIX_CHG_PCT_MAX):
                        # CRASH: TOPIX <= -1.5%
                        crash = True
                        market_reasons.append("TOPIX_CRASH")
                    elif topix_chg_ok and (float(CRASH_TOPIX_CHG_PCT_MAX) < float(topix_chg) <= float(WEAK_TOPIX_CHG_PCT_MAX)):
                        # WEAK: -1.5% < TOPIX <= -0.5%
                        market_reasons.append("TOPIX_WEAK")
                    if (tot > 0 and rising_ratio <= float(CRASH_RISING_RATIO_MAX)) and (tot2 > 0 and high_ratio <= float(CRASH_HIGH_RATIO_MAX)):
                        market_reasons.append("BREADTH_WEAK")

                    if crash:
                        market_regime = "CRASH"
                    elif market_reasons:
                        market_regime = "WEAK"
                    else:
                        market_regime = "NORMAL"
                except Exception:
                    market_regime = "NORMAL"
                    market_reasons = []
                market_regime_last = str(market_regime)

                # -----------------------------
                # MARKET_DEBUG（ユーザー要望）
                # - signal生成の有無に関係なく「地合い判定直後」の実値を保存
                # - 各銘柄走査タイミング: このループで候補になった銘柄（candidates）ごとに1行
                # -----------------------------
                try:
                    if len(market_debug_rows) < int(MARKET_DEBUG_MAX_ROWS):
                        t_dbg = replay_t
                        if t_dbg is None:
                            t_dbg = datetime.now(tz=timezone.utc)
                        if t_dbg.tzinfo is None:
                            t_dbg = t_dbg.replace(tzinfo=timezone.utc)
                        ts_jst = t_dbg.astimezone(JST).strftime("%Y-%m-%d %H:%M")

                        entry_allowed_by_market = bool(str(market_regime) != "CRASH")
                        blocked_reason_dbg = (market_reasons or []) if str(market_regime) == "CRASH" else []
                        for q_dbg in (candidates or []):
                            if len(market_debug_rows) >= int(MARKET_DEBUG_MAX_ROWS):
                                break
                            pipeline_debug["market_debug_count"] = int(pipeline_debug.get("market_debug_count", 0)) + 1
                            market_debug_rows.append(
                                {
                                    "timestamp_jst": ts_jst,
                                    "symbol": str(getattr(q_dbg, "symbol", "") or ""),
                                    "topix_fetch_ok": bool(topix_fetch_ok),
                                    "topix_raw": (float(topix_price_raw) if isinstance(topix_price_raw, (int, float)) else None),
                                    "topix_prev_close": (float(topix_prev_close) if isinstance(topix_prev_close, (int, float)) else None),
                                    "topix_pct": (float(topix_chg) if isinstance(topix_chg, (int, float)) else None),
                                    "market_state": str(market_regime),
                                    "entry_allowed": bool(entry_allowed_by_market),
                                    "blocked_reason": list([str(x) for x in blocked_reason_dbg]),
                                }
                            )
                except Exception:
                    pass

                # -----------------------------
                # 候補（新規）処理:
                # - signal記録は Discord の有無に依存させない（fastでも必ず期待値検証できるように）
                # -----------------------------
                to_notify = [q for q in candidates if q.symbol not in last_discord_candidate_symbols]
                pipeline_debug["to_notify_count"] = int(pipeline_debug.get("to_notify_count", 0)) + int(len(to_notify))
                for q in to_notify:
                    pipeline_debug["candidate_loop_entered"] = int(pipeline_debug.get("candidate_loop_entered", 0)) + 1
                    entry_calc = calculate_entry(q)
                    if entry_calc is None:
                        pipeline_debug["entry_calc_none"] = int(pipeline_debug.get("entry_calc_none", 0)) + 1
                        continue_reason_counts["NO_ENTRY_PRICE"] = int(continue_reason_counts.get("NO_ENTRY_PRICE", 0)) + 1
                        continue
                    pipeline_debug["entry_calc_ok"] = int(pipeline_debug.get("entry_calc_ok", 0)) + 1
                    entry = float(entry_calc)
                    stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                    take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                    ma25 = ma25_by_symbol.get(q.symbol)
                    if ma25 is None:
                        pipeline_debug["ma25_none"] = int(pipeline_debug.get("ma25_none", 0)) + 1
                        continue_reason_counts["NO_MA25"] = int(continue_reason_counts.get("NO_MA25", 0)) + 1
                        continue
                    pipeline_debug["ma25_ok"] = int(pipeline_debug.get("ma25_ok", 0)) + 1
                    try:
                        sig = intraday_by_symbol.get(q.symbol)
                        if sig is None:
                            pipeline_debug["intraday_signal_none"] = int(pipeline_debug.get("intraday_signal_none", 0)) + 1
                            continue_reason_counts["NO_INTRADAY_SIGNAL"] = int(continue_reason_counts.get("NO_INTRADAY_SIGNAL", 0)) + 1
                            continue
                        pipeline_debug["intraday_signal_ready"] = int(pipeline_debug.get("intraday_signal_ready", 0)) + 1

                        # Entry上抜け（最終仕様・シンプル版）
                        st_prev = bool(breakout_state_by_symbol.get(q.symbol, False))
                        st = st_prev
                        crossed = False
                        pipeline_debug["crossed_check_entered"] = int(pipeline_debug.get("crossed_check_entered", 0)) + 1
                        if entry > 0:
                            last_entry = last_breakout_entry_by_symbol.get(q.symbol)
                            if st and last_entry is not None and float(last_entry) > 0:
                                diff_pct = (abs(float(entry) - float(last_entry)) / float(last_entry)) * 100.0
                                if diff_pct >= float(BREAKOUT_ENTRY_RESET_PCT):
                                    breakout_state_by_symbol[q.symbol] = False
                                    st = False
                                    last_breakout_entry_by_symbol.pop(q.symbol, None)

                            if float(q.price) >= float(entry) and st is False:
                                crossed = True
                                breakout_state_by_symbol[q.symbol] = True
                                last_breakout_entry_by_symbol[q.symbol] = float(entry)
                            if float(q.price) < float(entry):
                                breakout_state_by_symbol[q.symbol] = False
                                last_breakout_entry_by_symbol.pop(q.symbol, None)

                        if bool(crossed):
                            pipeline_debug["crossed_true"] = int(pipeline_debug.get("crossed_true", 0)) + 1
                        else:
                            pipeline_debug["crossed_false"] = int(pipeline_debug.get("crossed_false", 0)) + 1

                        # -----------------------------
                        # CROSSED_DEBUG（ユーザー要望）
                        # - crossed 計算が壊れていないか/高値更新が追従し続けていないかを切り分ける
                        # -----------------------------
                        try:
                            if len(crossed_debug_rows) < int(CROSSED_DEBUG_MAX_ROWS):
                                t2 = q.market_time_utc or datetime.now(tz=timezone.utc)
                                if t2.tzinfo is None:
                                    t2 = t2.replace(tzinfo=timezone.utc)
                                j2 = t2.astimezone(JST).strftime("%Y-%m-%d %H:%M")
                                hi5 = (sig.recent_5m_high if sig else None)
                                diff_pct2 = None
                                if isinstance(entry, (int, float)) and float(entry) > 0:
                                    diff_pct2 = ((float(q.price) - float(entry)) / float(entry)) * 100.0
                                crossed_debug_rows.append(
                                    {
                                        "symbol": str(q.symbol),
                                        "time_jst": j2,
                                        "price": float(q.price),
                                        "high_5m": (float(hi5) if isinstance(hi5, (int, float)) else None),
                                        "entry": float(entry),
                                        "crossed": bool(crossed),
                                        "diff_pct": (float(diff_pct2) if isinstance(diff_pct2, (int, float)) else None),
                                    }
                                )
                        except Exception:
                            pass

                        # crossed=False が一定回数以上続く場合は「NO_5M_BREAKOUT」を理由集計へ加算
                        try:
                            if bool(crossed):
                                crossed_false_streak_by_symbol[q.symbol] = 0
                            else:
                                nst = int(crossed_false_streak_by_symbol.get(q.symbol, 0)) + 1
                                crossed_false_streak_by_symbol[q.symbol] = nst
                                if int(nst) == int(CROSSED_FALSE_STREAK_TO_COUNT):
                                    reject_reason_counts["NO_5M_BREAKOUT"] = int(reject_reason_counts.get("NO_5M_BREAKOUT", 0)) + 1
                        except Exception:
                            pass

                        # デバッグ: 候補が出たタイミングの状態
                        if bool(replay_market_debug):
                            t_dbg = q.market_time_utc or datetime.now(tz=timezone.utc)
                            if t_dbg.tzinfo is None:
                                t_dbg = t_dbg.replace(tzinfo=timezone.utc)
                            j_dbg = t_dbg.astimezone(JST)
                            nikkei_above_vwap = not ("NIKKEI<VWAP" in market_reasons)
                            topix_positive = not ("TOPIX<0%" in market_reasons)
                            market_breadth_ok = not any(r.startswith("rising<") for r in market_reasons)
                            recent_fail_ok = not ("fail30m>60%" in market_reasons)
                            high_update_ok = not ("high_update_low" in market_reasons)
                            afternoon_ok = not ("afternoon_weak" in market_reasons)
                            print(
                                f"[{now_str()}][CANDIDATE] {q.symbol} {j_dbg.strftime('%Y-%m-%d %H:%M')} JST "
                                f"price={float(q.price):.2f} entry={float(entry):.2f} crossed={bool(crossed)} st_prev={bool(st_prev)} "
                                f"market_regime={market_regime}"
                            )
                            print("  market_filter:")
                            print(f"    nikkei_above_vwap = {bool(nikkei_above_vwap)}")
                            print(f"    topix_positive = {bool(topix_positive)}")
                            print(f"    market_breadth_ok = {bool(market_breadth_ok)}")
                            print(f"    recent_fail_ok = {bool(recent_fail_ok)}")
                            print(f"    high_update_ok = {bool(high_update_ok)}")
                            print(f"    afternoon_ok = {bool(afternoon_ok)}")
                            if market_reasons:
                                print(f"    reasons = {', '.join(market_reasons)}")

                        # Discord通知（有効時のみ）
                        if discord_enabled:
                            if crossed:
                                embed = build_embed_entry_cross(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    vwap=(sig.vwap if sig else vwap_by_symbol.get(q.symbol)),
                                    ma25=float(ma25),
                                    replay_time_jst=_fmt_dt_jst_short(q.market_time_utc),
                                    recent_5m_high=(sig.recent_5m_high if sig else None),
                                    price_5min_ago=(sig.price_5min_ago if sig else None),
                                    vwap_distance_pct=(sig.vwap_distance_pct if sig else None),
                                    vol_increase=(sig.vol_3m_gt_prev_3m if sig else None),
                                    entry_crossed=True,
                                )
                            else:
                                embed = build_embed_match(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    vwap=vwap_by_symbol.get(q.symbol),
                                    ma25=float(ma25),
                                    replay_time_jst=_fmt_dt_jst_short(q.market_time_utc),
                                    recent_5m_high=(sig.recent_5m_high if sig else None),
                                    price_5min_ago=(sig.price_5min_ago if sig else None),
                                    vwap_distance_pct=(sig.vwap_distance_pct if sig else None),
                                    vol_increase=(sig.vol_3m_gt_prev_3m if sig else None),
                                    entry_near_ratio=float(ENTRY_NEAR_RATIO),
                                    entry_crossed=False,
                                    breakout_state=bool(breakout_state_by_symbol.get(q.symbol, False)),
                                )
                            discord_notify(
                                {"embeds": [embed]},
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(entry), float(stop), float(take))

                        # signal記録（期待値検証用）
                        # - Discord通知の有無に依存させない（fastでも必ず期待値検証できるように）
                        if crossed:
                            # 後場Entry禁止（ユーザー要望）
                            if bool(replay_disable_afternoon_entry):
                                try:
                                    t_af = q.market_time_utc or datetime.now(tz=timezone.utc)
                                    if t_af.tzinfo is None:
                                        t_af = t_af.replace(tzinfo=timezone.utc)
                                    hm_af = t_af.astimezone(JST).hour * 60 + t_af.astimezone(JST).minute
                                    # 後場 = 12:30 以降
                                    if hm_af >= (12 * 60 + 30):
                                        continue_reason_counts["AFTERNOON_ENTRY_DISABLED"] = int(
                                            continue_reason_counts.get("AFTERNOON_ENTRY_DISABLED", 0)
                                        ) + 1
                                        try:
                                            if len(continue_before_append_rows) < int(CONTINUE_BEFORE_APPEND_MAX_ROWS):
                                                continue_before_append_rows.append(
                                                    {
                                                        "source_location": "after_crossed_before_object",
                                                        "reason_detail": "AFTERNOON_ENTRY_DISABLED",
                                                        "symbol": str(q.symbol),
                                                        "time_jst": _fmt_dt_jst_short(t_af),
                                                    }
                                                )
                                        except Exception:
                                            pass
                                        continue
                                except Exception:
                                    # 時刻計算に失敗した場合は通常通り継続
                                    pass

                            # 後場Entry厳格化（ユーザー要望）
                            if bool(replay_strict_afternoon_entry):
                                try:
                                    t_af = q.market_time_utc or datetime.now(tz=timezone.utc)
                                    if t_af.tzinfo is None:
                                        t_af = t_af.replace(tzinfo=timezone.utc)
                                    hm_af = t_af.astimezone(JST).hour * 60 + t_af.astimezone(JST).minute
                                    is_afternoon = hm_af >= (12 * 60 + 30)
                                except Exception:
                                    is_afternoon = False
                                    t_af = None

                                if bool(is_afternoon):
                                    # 1) TOPIX_WEAK時はEntry禁止（後場のみ）
                                    if bool(replay_afternoon_topix_weak_block) and isinstance(market_reasons, list) and ("TOPIX_WEAK" in market_reasons):
                                        continue_reason_counts["AFTERNOON_TOPIX_WEAK_BLOCK"] = int(
                                            continue_reason_counts.get("AFTERNOON_TOPIX_WEAK_BLOCK", 0)
                                        ) + 1
                                        continue

                                    # 2) 出来高倍率条件を強化
                                    volx = vol_spike_ratio_by_symbol.get(q.symbol)
                                    if not (isinstance(volx, (int, float)) and float(volx) >= float(aft_volume_spike_ratio_min)):
                                        continue_reason_counts["AFTERNOON_VOL_SPIKE_WEAK"] = int(
                                            continue_reason_counts.get("AFTERNOON_VOL_SPIKE_WEAK", 0)
                                        ) + 1
                                        continue

                                    # 3) VWAP上乖離条件を強化（高値掴み抑制）
                                    vdist = (sig.vwap_distance_pct if sig else None)
                                    if isinstance(vdist, (int, float)) and float(vdist) > float(aft_vwap_dist_pct_max):
                                        continue_reason_counts["AFTERNOON_VWAP_DIST_TOO_HIGH"] = int(
                                            continue_reason_counts.get("AFTERNOON_VWAP_DIST_TOO_HIGH", 0)
                                        ) + 1
                                        continue

                                    # 4) 5分高値更新を必須化（強い上抜け）
                                    if sig is None or sig.recent_5m_high is None:
                                        continue_reason_counts["AFTERNOON_NO_5M_HIGH"] = int(
                                            continue_reason_counts.get("AFTERNOON_NO_5M_HIGH", 0)
                                        ) + 1
                                        continue
                                    if not (float(q.price) >= float(sig.recent_5m_high) * float(aft_rebreak_mult)):
                                        continue_reason_counts["AFTERNOON_REBREAK_WEAK"] = int(
                                            continue_reason_counts.get("AFTERNOON_REBREAK_WEAK", 0)
                                        ) + 1
                                        continue

                                    # 5) recent_5m_low が切り上がっている場合のみ許可
                                    played2 = idx_by_symbol.get(q.symbol, 0)
                                    bars2 = bars_by_symbol.get(q.symbol, [])[:played2]
                                    recent_5m_low_now = None
                                    if len(bars2) >= 6:
                                        lows_window = [float(b.low) for b in bars2[-6:-1]]
                                        if lows_window:
                                            recent_5m_low_now = float(min(lows_window))
                                    prev_low = prev_recent_5m_low_by_symbol.get(q.symbol)
                                    if not (
                                        isinstance(prev_low, (int, float))
                                        and isinstance(recent_5m_low_now, (int, float))
                                        and float(recent_5m_low_now) > float(prev_low)
                                    ):
                                        continue_reason_counts["AFTERNOON_5M_LOW_NOT_RISING"] = int(
                                            continue_reason_counts.get("AFTERNOON_5M_LOW_NOT_RISING", 0)
                                        ) + 1
                                        prev_recent_5m_low_by_symbol[q.symbol] = recent_5m_low_now
                                        continue
                                    prev_recent_5m_low_by_symbol[q.symbol] = recent_5m_low_now

                            signal_candidate_count += 1
                            signal_seq += 1

                            # -----------------------------
                            # signal品質フィルタ（RSI/ATR%/RS）
                            # -----------------------------
                            played_i = idx_by_symbol.get(q.symbol, 0)
                            bars_i = bars_by_symbol.get(q.symbol, [])[:played_i]
                            rsi14 = _calc_rsi14([b.close for b in bars_i])
                            atr14 = _calc_atr14([b.high for b in bars_i], [b.low for b in bars_i], [b.close for b in bars_i])
                            atr_pct = (float(atr14) / float(q.price) * 100.0) if (atr14 is not None and float(q.price) > 0) else None
                            rs_vs_topix = None
                            if q.change_percent is not None and topix_chg is not None:
                                rs_vs_topix = float(q.change_percent) - float(topix_chg)
                            vwap_dist_pct = (sig.vwap_distance_pct if sig else None)

                            def _quality_rejects(regime: str) -> list[str]:
                                rej: list[str] = []
                                # RSは全レジーム共通
                                if rs_vs_topix is not None and float(rs_vs_topix) < float(SIGNAL_FILTER_RS_BLOCK_LT):
                                    rej.append("rs<0")

                                if str(regime) == "WEAK":
                                    # 後場制限（WEAK時のみ）
                                    ttmp2 = q.market_time_utc or datetime.now(tz=timezone.utc)
                                    if ttmp2.tzinfo is None:
                                        ttmp2 = ttmp2.replace(tzinfo=timezone.utc)
                                    hm2 = ttmp2.astimezone(JST).hour * 60 + ttmp2.astimezone(JST).minute
                                    if hm2 >= (11 * 60 + 30):
                                        rej.append("weak_not_morning")

                                    if rsi14 is not None and float(rsi14) > float(WEAK_SIGNAL_FILTER_RSI_BLOCK_GT):
                                        rej.append(f"rsi>{int(WEAK_SIGNAL_FILTER_RSI_BLOCK_GT)}")
                                    if atr_pct is not None and float(atr_pct) > float(WEAK_SIGNAL_FILTER_ATR_PCT_BLOCK_GT):
                                        rej.append(f"atr_pct>{WEAK_SIGNAL_FILTER_ATR_PCT_BLOCK_GT}")
                                else:
                                    if rsi14 is not None and float(rsi14) > float(SIGNAL_FILTER_RSI_BLOCK_GT):
                                        rej.append(f"rsi>{int(SIGNAL_FILTER_RSI_BLOCK_GT)}")
                                    if atr_pct is not None and float(atr_pct) > float(SIGNAL_FILTER_ATR_PCT_BLOCK_GT):
                                        rej.append(f"atr_pct>{int(SIGNAL_FILTER_ATR_PCT_BLOCK_GT)}")
                                return rej

                            # CRASH地合いの扱い（方針転換）:
                            # - 市場全体で止めず「危険signalだけ除外」へ寄せるため、
                            #   CRASHでも signal は通常どおり集計対象に入れる。
                            # - ただし「CRASHだった回数/理由」は別途カウントして分析に使う。
                            crash_blocked = False
                            if str(market_regime) == "CRASH":
                                crash_blocked = True
                                blocked_entry_count += 1
                                for rr in (market_reasons or []):
                                    if str(rr) == "TOPIX<0%":
                                        continue
                                    blocked_reason_counts[rr] = int(blocked_reason_counts.get(rr, 0)) + 1

                            # 分析用: signal品質フィルタは「仮想除外」専用（実際には除外しない）
                            qrej = _quality_rejects(str(market_regime))

                            sig_time = (q.market_time_utc or datetime.now(tz=timezone.utc))
                            day_jst = _day_jst_str(sig_time)
                            key_day_sym = (day_jst, q.symbol)

                            # 当日停止（-3%）
                            daily_stop = bool(trading_stopped_by_day_symbol.get(key_day_sym, False))
                            if daily_stop and key_day_sym not in stop_logged_by_day_symbol:
                                stop_logged_by_day_symbol.add(key_day_sym)
                                v = float(daily_cum_profit_pct_by_day_symbol.get(key_day_sym, 0.0))
                                print(
                                    f"[{now_str()}][STOP] {day_jst} {q.symbol} 当日累計損益が {v:.2f}% になったため、"
                                    "この銘柄の新規ENTRY/ADDを停止します。"
                                )

                            # 重複エントリー制限
                            exclude = False
                            exclude_reason = ""
                            if one_trade_per_symbol_per_day:
                                seen = accepted_entry_symbols_by_day.setdefault(day_jst, set())
                                if q.symbol in seen:
                                    exclude = True
                                    exclude_reason = "同一銘柄は1日1回まで（2回目以降は除外）"
                                else:
                                    seen.add(q.symbol)

                            # 当日停止は「signal候補としては記録するが集計対象外」
                            if daily_stop:
                                exclude = True
                                exclude_reason = "当日停止（-3%）により新規ENTRY/ADD停止"

                            # PRE_SIGNAL_OBJECT_DEBUG（ユーザー要望）
                            try:
                                pipeline_debug["pre_signal_object_count"] = int(pipeline_debug.get("pre_signal_object_count", 0)) + 1
                                if len(pre_signal_object_debug_rows) < int(PRE_SIGNAL_OBJECT_DEBUG_MAX_ROWS):
                                    tpre = sig_time
                                    if tpre.tzinfo is None:
                                        tpre = tpre.replace(tzinfo=timezone.utc)
                                    pre_signal_object_debug_rows.append(
                                        {
                                            "symbol": str(q.symbol),
                                            "time_jst": tpre.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                                            "entry_price": float(entry),
                                            "market_state": str(market_regime),
                                        }
                                    )
                            except Exception:
                                pass

                            # ReplaySignalEval 生成は例外を握り潰さず、必ずスタックトレースを残します
                            try:
                                # 例外切り分け用: 渡す引数を事前に固めてダンプできるようにする
                                _signal_object_args: dict[str, Any] = {
                                    "signal_id": f"{safe_batch_stamp}_run{int(replay_repeat_run_no or 0):02d}_{int(signal_seq):05d}",
                                    "symbol": str(q.symbol),
                                    "signal_time_utc": sig_time,
                                    "signal_price": float(q.price),
                                    "entry_price": float(entry),
                                    "stop_price": float(stop),
                                    "take_price": float(take),
                                    "max_price_after": float(q.price),
                                    "min_price_after": float(q.price),
                                    "last_price_after": float(q.price),
                                    "position_kind": "BASE",
                                    "exit_style": "trailing",
                                    "excluded_from_eval": bool(exclude),
                                    "excluded_reason": str(exclude_reason),
                                }
                                s = ReplaySignalEval(
                                    **_signal_object_args,
                                )
                            except Exception as e:
                                # IMPORTANT: except: pass 禁止。必ずスタックトレースを出す
                                logger.exception("ReplaySignalEval creation failed (symbol=%s)", str(q.symbol))
                                tr_txt = traceback.format_exc()
                                print(f"[{now_str()}] ReplaySignalEval生成に失敗: {q.symbol} ({type(e).__name__}: {e})")
                                # まずターミナルに確実に出す（ユーザー要望）
                                print(tr_txt)

                                # 先頭10件だけ、runXX.txt に全文保存できるように保持（極力シンプルに）
                                try:
                                    if len(exception_before_append_traces) < int(EXCEPTION_TRACE_MAX):
                                        tpre2 = sig_time
                                        if tpre2.tzinfo is None:
                                            tpre2 = tpre2.replace(tzinfo=timezone.utc)
                                        exception_before_append_traces.append(
                                            {
                                                "symbol": str(q.symbol),
                                                "time_jst": tpre2.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                                                "traceback": str(tr_txt),
                                                "signal_object_args": dict(_signal_object_args),
                                            }
                                        )
                                except Exception:
                                    trace_capture_failed_count = int(trace_capture_failed_count) + 1

                                # さらに保険として、テキストだけは必ず残す（辞書化が失敗してもTXT出力できる）
                                if len(exception_before_append_trace_texts) < int(EXCEPTION_TRACE_MAX):
                                    try:
                                        tpre2 = sig_time
                                        if tpre2.tzinfo is None:
                                            tpre2 = tpre2.replace(tzinfo=timezone.utc)
                                        args_lines2 = []
                                        for kk, vv in (_signal_object_args or {}).items():
                                            try:
                                                args_lines2.append(f"{kk}={repr(vv)} type={type(vv).__name__}")
                                            except Exception:
                                                args_lines2.append(f"{kk}=(unrepr) type={type(vv).__name__}")
                                        exception_before_append_trace_texts.append(
                                            "\n".join(
                                                [
                                                    "[EXCEPTION_BEFORE_APPEND_TRACE]",
                                                    f"symbol={str(q.symbol)}",
                                                    f"time={tpre2.astimezone(JST).strftime('%Y-%m-%d %H:%M')}",
                                                    "",
                                                    "SIGNAL_OBJECT_ARGS:",
                                                    *args_lines2,
                                                    "",
                                                    str(tr_txt),
                                                    "",
                                                ]
                                            )
                                        )
                                    except Exception:
                                        trace_capture_failed_count = int(trace_capture_failed_count) + 1

                                continue_reason_counts["EXCEPTION_BEFORE_APPEND"] = int(continue_reason_counts.get("EXCEPTION_BEFORE_APPEND", 0)) + 1
                                if len(continue_before_append_rows) < 500:
                                    continue_before_append_rows.append({"reason": "EXCEPTION_BEFORE_APPEND", "symbol": str(q.symbol)})
                                continue

                            # POST_SIGNAL_OBJECT_DEBUG（ユーザー要望）
                            try:
                                pipeline_debug["post_signal_object_count"] = int(pipeline_debug.get("post_signal_object_count", 0)) + 1
                                if len(post_signal_object_debug_rows) < int(POST_SIGNAL_OBJECT_DEBUG_MAX_ROWS):
                                    post_signal_object_debug_rows.append({"signal_id": str(getattr(s, "signal_id", "") or ""), "symbol": str(q.symbol)})
                            except Exception:
                                pass
                            # 重要: 補助属性の付与で落ちても append は止めない（ユーザー要望）
                            # - ReplaySignalEval を作れたら、まず append を優先する
                            replay_signals.append(s)
                            if not exclude:
                                idx = len(replay_signals) - 1
                                active_signal_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                day_jst2 = _day_jst_str(sig_time)
                                last_entry_price_by_day_symbol[(day_jst2, q.symbol)] = float(entry)

                            # 補助属性（失敗しても継続）
                            try:
                                setattr(s, "market_regime", str(market_regime))
                                # Replayログ用（地合いデバッグ）
                                setattr(s, "topix_fetch_ok", bool(topix_fetch_ok))
                                setattr(s, "fallback_used", bool(fallback_used))
                                setattr(s, "topix_raw", float(topix_price_raw) if isinstance(topix_price_raw, (int, float)) else None)
                                setattr(s, "topix_prev_close", float(topix_prev_close) if isinstance(topix_prev_close, (int, float)) else None)
                                setattr(s, "topix_pct", float(topix_chg) if isinstance(topix_chg, (int, float)) else None)
                                setattr(s, "topix_chg_raw", float(topix_chg_raw) if isinstance(topix_chg_raw, (int, float)) else None)
                                setattr(s, "topix_chg_pct_raw", float(topix_chg_raw) if isinstance(topix_chg_raw, (int, float)) else None)
                                setattr(s, "topix_chg_pct", float(topix_chg) if isinstance(topix_chg, (int, float)) else None)
                                setattr(s, "topix_chg_ok", bool(topix_chg_ok))
                                setattr(s, "topix_crash_threshold", float(CRASH_TOPIX_CHG_PCT_MAX))
                                setattr(s, "topix_weak_threshold", float(WEAK_TOPIX_CHG_PCT_MAX))
                                setattr(s, "market_state", str(market_regime))
                                setattr(s, "crash_blocked", bool(crash_blocked))
                                setattr(s, "market_reasons", ",".join([str(x) for x in (market_reasons or [])]))
                                setattr(s, "market_blocked", bool(crash_blocked))
                                setattr(s, "blocked_reason", (",".join([str(x) for x in (market_reasons or [])]) if crash_blocked else ""))
                                setattr(s, "entry_allowed_by_market", bool(not crash_blocked))
                                setattr(s, "entry_allowed", bool((not crash_blocked) and (not exclude)))
                                setattr(s, "rsi14", rsi14)
                                setattr(s, "atr14", atr14)
                                setattr(s, "atr_pct", atr_pct)
                                setattr(s, "vwap_distance_pct", vwap_dist_pct)
                                setattr(s, "relative_strength_vs_topix_pct", rs_vs_topix)
                                setattr(s, "vol_spike_ratio", vol_spike_ratio_by_symbol.get(q.symbol))
                                # time_bucket は fallback 付き
                                try:
                                    tb = _signal_time_bucket_jst(sig_time)
                                except Exception:
                                    tb = "UNKNOWN"
                                setattr(s, "time_bucket_jst", str(tb))
                                setattr(s, "suggested_block_reasons", ",".join(qrej))
                                passed: list[str] = []
                                if rsi14 is None or float(rsi14) <= float(SIGNAL_FILTER_RSI_BLOCK_GT):
                                    passed.append("rsi_ok")
                                if atr_pct is None or float(atr_pct) <= float(SIGNAL_FILTER_ATR_PCT_BLOCK_GT):
                                    passed.append("atr_ok")
                                if rs_vs_topix is None or float(rs_vs_topix) >= 0.0:
                                    passed.append("rs_ok")
                                if vwap_dist_pct is None or float(vwap_dist_pct) <= 3.0:
                                    passed.append("vwap_ok")
                                setattr(s, "not_blocked_reason", ",".join(passed))
                            except Exception as e:
                                logger.exception("Optional setattr failed after append (symbol=%s)", str(getattr(s, "symbol", "")))
                                print(f"[{now_str()}] 補助属性の付与に失敗(append後): {q.symbol} ({type(e).__name__}: {e})")

                            # APPEND_SIGNAL_DEBUG（ユーザー要望）
                            try:
                                pipeline_debug["replay_signals_append_count"] = int(pipeline_debug.get("replay_signals_append_count", 0)) + 1
                                pipeline_debug["signal_generated"] = int(pipeline_debug.get("signal_generated", 0)) + 1
                                if len(append_signal_debug_rows) < int(APPEND_SIGNAL_DEBUG_MAX_ROWS):
                                    append_signal_debug_rows.append(
                                        {
                                            "signal_id": str(getattr(s, "signal_id", "") or ""),
                                            "symbol": str(getattr(s, "symbol", "") or ""),
                                            "entry_time_jst": str(_fmt_dt_jst_short(getattr(s, "signal_time_utc", None))),
                                            "excluded_from_eval": bool(getattr(s, "excluded_from_eval", False)),
                                            "excluded_reason": str(getattr(s, "excluded_reason", "") or ""),
                                        }
                                    )
                            except Exception as e:
                                logger.exception("APPEND_SIGNAL_DEBUG failed (symbol=%s)", str(getattr(s, "symbol", "")))
                                print(f"[{now_str()}] APPEND_SIGNAL_DEBUGで例外: {q.symbol} ({type(e).__name__}: {e})")

                    except Exception as e:
                        # IMPORTANT: except: pass 禁止。必ずスタックトレースを出す
                        logger.exception("Replay candidate handling failed (symbol=%s)", str(getattr(q, "symbol", "")))
                        print(
                            f"[{now_str()}] Replay候補処理で例外: {q.symbol} ({type(e).__name__}: {e})"
                        )
                        # runXX.txt へ必ず残すため、先頭10件だけtraceback全文を保存
                        tr_txt3 = traceback.format_exc()
                        # まずターミナルに確実に出す（ユーザー要望）
                        print(tr_txt3)
                        if len(exception_before_append_trace_texts) < int(EXCEPTION_TRACE_MAX):
                            try:
                                tpre3 = getattr(q, "market_time_utc", None) or datetime.now(tz=timezone.utc)
                                if isinstance(tpre3, datetime) and tpre3.tzinfo is None:
                                    tpre3 = tpre3.replace(tzinfo=timezone.utc)
                                # その時点で分かる範囲の引数をダンプ（失敗してもtracebackだけは残す）
                                args_lines3: list[str] = []
                                try:
                                    args_lines3.append(f"symbol={repr(getattr(q, 'symbol', ''))} type=str")
                                    args_lines3.append(
                                        f"price={repr(getattr(q, 'price', None))} type={type(getattr(q, 'price', None)).__name__}"
                                    )
                                    args_lines3.append(f"entry={repr(locals().get('entry', None))} type={type(locals().get('entry', None)).__name__}")
                                    args_lines3.append(f"stop={repr(locals().get('stop', None))} type={type(locals().get('stop', None)).__name__}")
                                    args_lines3.append(f"take={repr(locals().get('take', None))} type={type(locals().get('take', None)).__name__}")
                                    args_lines3.append(
                                        f"market_state={repr(locals().get('market_regime', None))} type={type(locals().get('market_regime', None)).__name__}"
                                    )
                                except Exception:
                                    args_lines3 = []
                                exception_before_append_trace_texts.append(
                                    "\n".join(
                                        [
                                            "[EXCEPTION_BEFORE_APPEND_TRACE]",
                                            f"symbol={str(getattr(q, 'symbol', '') or '')}",
                                            f"time={tpre3.astimezone(JST).strftime('%Y-%m-%d %H:%M') if isinstance(tpre3, datetime) else ''}",
                                            "",
                                            "SIGNAL_OBJECT_ARGS:",
                                            *args_lines3,
                                            "",
                                            str(tr_txt3),
                                            "",
                                        ]
                                    )
                                )
                            except Exception:
                                trace_capture_failed_count = int(trace_capture_failed_count) + 1
                        continue_reason_counts["EXCEPTION_BEFORE_APPEND"] = int(continue_reason_counts.get("EXCEPTION_BEFORE_APPEND", 0)) + 1
                        if len(continue_before_append_rows) < 500:
                            continue_before_append_rows.append({"reason": "EXCEPTION_BEFORE_APPEND", "symbol": str(q.symbol)})

                # -----------------------------
                # Discord通知（3種類）
                # -----------------------------
                if discord_enabled:
                    # 条件外れ（安定化: 連続不一致が EXIT_CONFIRM_COUNT に達した時だけ通知）
                    out_symbols = sorted(last_discord_candidate_symbols - candidate_symbols)
                    for sym in out_symbols:
                        last_q = next((qq for qq in quotes if qq.symbol == sym), None)
                        try:
                            exit_miss_count[sym] = int(exit_miss_count.get(sym, 0)) + 1
                            if exit_miss_count[sym] < int(EXIT_CONFIRM_COUNT):
                                continue

                            embed_out = build_embed_out(
                                symbol=sym,
                                price=(last_q.price if last_q else None),
                                change_percent=(last_q.change_percent if last_q else None),
                                reasons=skip_reasons_by_symbol.get(sym, []),
                            )
                            msg_out = {"embeds": [embed_out]}
                            discord_notify(
                                msg_out,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            # 通知したらカウントをリセットし、候補集合からも外します（スパム防止）
                            exit_miss_count.pop(sym, None)
                            last_discord_candidate_symbols.discard(sym)
                            # 条件外れが確定したら breakout_state もリセットします（仕様）。
                            breakout_state_by_symbol[sym] = False
                            last_breakout_entry_by_symbol.pop(sym, None)
                        except Exception as e:
                            print(f"[{now_str()}] Discord条件外れ通知失敗(replay): {sym} ({e})")

                    # 候補に戻った銘柄はカウントをリセット
                    for sym in candidate_symbols:
                        exit_miss_count.pop(sym, None)

                    # 候補価格変更（条件一致中のみ）
                    candidates_by_symbol = {q.symbol: q for q in candidates}
                    new_notified_symbols = {q.symbol for q in to_notify}
                    for sym, (old_entry, old_stop, old_take) in list(last_notified_levels.items()):
                        q = candidates_by_symbol.get(sym)
                        if q is None or sym in new_notified_symbols:
                            continue
                        new_entry_calc = calculate_entry(q)
                        if new_entry_calc is None:
                            continue
                        new_entry = float(new_entry_calc)
                        new_stop = new_entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        new_take = new_entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        if not (
                            _level_changed(old=old_entry, new=new_entry)
                            or _level_changed(old=old_stop, new=new_stop)
                            or _level_changed(old=old_take, new=new_take)
                        ):
                            continue
                        try:
                            msg2 = _build_levels_change_message(
                                symbol=q.symbol,
                                price=float(q.price),
                                currency=str(q.currency),
                                change_percent=q.change_percent,
                                old_entry=float(old_entry),
                                new_entry=float(new_entry),
                                old_stop=float(old_stop),
                                new_stop=float(new_stop),
                                old_take=float(old_take),
                                new_take=float(new_take),
                            )
                            discord_notify(
                                msg2,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(new_entry), float(new_stop), float(new_take))
                        except Exception as e:
                            print(f"[{now_str()}] Discord再通知失敗(replay): {q.symbol} ({e})")

                # Discordの有無に関わらず「前回候補集合」は更新し、次ループのto_notifyを安定化させます。
                last_discord_candidate_symbols = candidate_symbols

                # 1秒ごとの再生
                elapsed = time.perf_counter() - loop_started
                if not fast_mode:
                    sleep_sec = interval_sec - elapsed
                    if sleep_sec > 0:
                        time.sleep(sleep_sec)

        except KeyboardInterrupt:
            print("\nCtrl+C を検知しました。終了します。")
            return 0


def main(argv: list[str]) -> int:
    # shorthand:
    #   python yahoo_kabu_watch.py replay <range> <repeat> <mode> <config.json>
    # を、既存の argparse へ変換して後方互換を保ちます。
    argv2 = list(argv or [])
    if argv2 and str(argv2[0]).lower() == "replay":
        rr = str(argv2[1]) if len(argv2) >= 2 else "1d"
        rep = str(argv2[2]) if len(argv2) >= 3 else "1"
        md = str(argv2[3]) if len(argv2) >= 4 else "normal"
        cfgp = str(argv2[4]) if len(argv2) >= 5 else ""
        argv2 = ["--replay", "--replay-range", rr, "--replay-repeat", rep, "--replay-mode", md]
        if cfgp.strip():
            argv2 += ["--replay-config", cfgp]

    args = parse_args(argv2)

    # -----------------------------
    # 朝スクリーニング（新機能）
    # -----------------------------
    # 重要:
    # - 通常監視やReplayに影響させないため、ここで早期returnします。
    # - これにより「監視ループ」や「Replayの挙動」を一切変更せずに機能追加できます。
    if bool(getattr(args, "morning_screen", False)):
        return run_morning_screen()

    # --replay が指定されたときだけ TEST_REPLAY_MODE を有効化します。
    # （指定しなければ False のまま = いつものリアルタイム監視に戻ります）
    global TEST_REPLAY_MODE
    TEST_REPLAY_MODE = bool(getattr(args, "replay", False))
    replay_range: str = str(getattr(args, "replay_range", "1d"))
    replay_random_days: int = int(getattr(args, "replay_random_days", 0) or 0)
    replay_random_months: int = int(getattr(args, "replay_random_months", 3) or 3)
    replay_seed: Optional[int] = getattr(args, "replay_seed", None)
    replay_mode: str = str(getattr(args, "replay_mode", "normal") or "normal")
    replay_fast_discord: bool = bool(getattr(args, "replay_fast_discord", False))
    replay_fast_verbose: bool = bool(getattr(args, "replay_fast_verbose", False))
    replay_fast_print_signal_details: bool = bool(getattr(args, "replay_fast_print_signal_details", False))
    replay_market_debug: bool = bool(getattr(args, "replay_market_debug", False))
    replay_repeat: int = int(getattr(args, "replay_repeat", 1) or 1)
    replay_morning_screen_hhmm: str = str(getattr(args, "replay_morning_screen", "") or "")
    one_trade_per_symbol_per_day: bool = bool(getattr(args, "one_trade_per_symbol_per_day", False))
    enable_add: bool = bool(getattr(args, "enable_add", False))
    replay_early_exit_before_stop: bool = bool(getattr(args, "replay_early_exit", False))
    replay_disable_afternoon_entry: bool = bool(getattr(args, "replay_disable_afternoon_entry", False))
    replay_strict_afternoon_entry: bool = bool(getattr(args, "replay_strict_afternoon_entry", False))
    replay_afternoon_compare: bool = bool(getattr(args, "replay_afternoon_compare", False))
    replay_config_path: str = str(getattr(args, "replay_config", "") or "").strip()
    # 要件: replay実行時に config パス未指定なら configs/replay_safe.json をデフォルトで読む（自動生成も行う）
    if bool(TEST_REPLAY_MODE) and (not replay_config_path):
        replay_config_path = _ensure_replay_configs_exist()
    elif bool(TEST_REPLAY_MODE):
        # configs/ が無いケースでも落ちないようにする（ユーザーが明示パス指定した場合）
        try:
            parent = os.path.dirname(os.path.abspath(replay_config_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        except Exception:
            pass

    cfg_raw = _load_replay_config(replay_config_path)
    cfg_flags = _apply_replay_config_to_flags(cfg=cfg_raw)

    # configがある場合、戦略条件は基本的にconfig側を優先（CLIは毎回変えるものだけに寄せる）
    if replay_config_path:
        replay_early_exit_before_stop = bool(cfg_flags.get("replay_early_exit_before_stop", replay_early_exit_before_stop))
        replay_strict_afternoon_entry = bool(cfg_flags.get("replay_strict_afternoon_entry", replay_strict_afternoon_entry))
        replay_disable_afternoon_entry = bool(cfg_flags.get("replay_disable_afternoon_entry", replay_disable_afternoon_entry))
        replay_early_exit_vwap = bool(cfg_flags.get("replay_early_exit_vwap", True))
        replay_early_exit_recent_low = bool(cfg_flags.get("replay_early_exit_recent_low", True))
        replay_afternoon_topix_weak_block = bool(cfg_flags.get("replay_afternoon_topix_weak_block", True))
        aft_volume_spike_ratio_min = float(cfg_flags.get("aft_volume_spike_ratio_min", AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN))
        aft_vwap_dist_pct_max = float(cfg_flags.get("aft_vwap_dist_pct_max", AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX))
        aft_rebreak_mult = float(cfg_flags.get("aft_rebreak_mult", AFTERNOON_ENTRY_STRICT_REBREAK_MULT))
        replay_config_name = str(cfg_flags.get("replay_config_name") or str(cfg_raw.get("name") or ""))
    else:
        replay_early_exit_vwap = True
        replay_early_exit_recent_low = True
        replay_afternoon_topix_weak_block = True
        aft_volume_spike_ratio_min = float(AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN)
        aft_vwap_dist_pct_max = float(AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX)
        aft_rebreak_mult = float(AFTERNOON_ENTRY_STRICT_REBREAK_MULT)
        replay_config_name = ""

    # 要件: replay開始時に必ず読み込んだconfigをprint（反映確認）
    if bool(TEST_REPLAY_MODE):
        print("Loaded replay config:")
        print(f"config_name={str(replay_config_name or '')}")
        print(f"config_path={str(replay_config_path or '')}")
        print(f"early_exit_before_stop={bool(replay_early_exit_before_stop)}")
        print(f"strict_afternoon={bool(replay_strict_afternoon_entry)}")
        print(f"topix_weak_block={bool(replay_afternoon_topix_weak_block)}")
        print(f"disable_afternoon_entry={bool(replay_disable_afternoon_entry)}")

    # =========================
    # Replay設定（値 + source）を作って run_replay へ渡す
    # =========================
    def _src(key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict) and key in cfg_raw:
                return "config"
        except Exception:
            pass
        return "default"

    def _src_aft(child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                a = cfg_raw.get("afternoon_strict")
                if isinstance(a, dict) and child_key in a:
                    return "config"
        except Exception:
            pass
        return "default"

    replay_settings = {
        "config_name": str(replay_config_name or ""),
        "config_path": str(replay_config_path or ""),
        "replay_range": str(replay_range),
        "replay_repeat": int(replay_repeat),
        "replay_mode": str(replay_mode),
        "early_exit": {"value": bool(replay_early_exit_before_stop), "source": _src("early_exit")},
        "vwap_break_exit": {"value": bool(replay_early_exit_vwap), "source": _src("vwap_break_exit")},
        "recent_5m_low_break_exit": {"value": bool(replay_early_exit_recent_low), "source": _src("recent_5m_low_break_exit")},
        "strict_afternoon": {"value": bool(replay_strict_afternoon_entry), "source": _src("strict_afternoon")},
        "disable_afternoon_entry": {"value": bool(replay_disable_afternoon_entry), "source": _src("disable_afternoon_entry")},
        "topix_weak_block": {"value": bool(replay_afternoon_topix_weak_block), "source": _src("topix_weak_block")},
        "afternoon": {
            "volume_spike_ratio_min": {"value": float(aft_volume_spike_ratio_min), "source": _src_aft("volume_spike_ratio_min")},
            "vwap_dist_pct_max": {"value": float(aft_vwap_dist_pct_max), "source": _src_aft("vwap_dist_pct_max")},
            "rebreak_mult": {"value": float(aft_rebreak_mult), "source": _src_aft("rebreak_mult")},
        },
    }

    # 事故防止: 後場禁止と後場厳格化は同時ONにしない（厳格化が無意味になるため）
    if bool(replay_disable_afternoon_entry) and bool(replay_strict_afternoon_entry):
        print(f"[{now_str()}] --replay-disable-afternoon-entry と --replay-strict-afternoon-entry は同時指定できません。")
        return 2

    # 事故防止: config側で後場厳格化をONにしている場合も同様
    if bool(replay_disable_afternoon_entry) and bool(replay_config_path) and bool(replay_strict_afternoon_entry):
        print(f"[{now_str()}] 後場禁止と後場厳格化が同時に有効です。config/CLIの指定を見直してください。")
        return 2

    interval_sec: float = float(args.interval)
    print_all: bool = bool(args.print_all)
    only_changes: bool = bool(args.only_changes)
    watch_csv: str = str(args.watch or "")
    watch_file: str = str(args.watch_file or "")

    # 監視銘柄の決め方（初心者向けに整理）
    #
    # 1) --watch-file / --watch が指定された場合は「固定リスト」として扱います。
    #    → Discord の !watch add/remove とは独立です（コマンドライン指定が最優先）。
    #
    # 2) コマンドライン指定が無い場合:
    #    → watchlist.json が存在するなら、それを「正」として毎ループ読み直します（今回の仕様）。
    #    → watchlist.json が無い場合だけ、symbols.csv → WATCH にフォールバックします。
    fixed_watch: Optional[list[str]] = None
    if watch_file:
        try:
            fixed_watch = _load_watch_from_file(watch_file)
        except Exception as e:
            print(f"--watch-file の読み込みに失敗しました: {watch_file} ({e})")
            return 2
    elif watch_csv:
        fixed_watch = _parse_watch_csv(watch_csv)

    if interval_sec <= 0:
        print("--interval は 0 より大きい値にしてください。")
        return 2

    # -----------------------------
    # テスト用リプレイモード
    # -----------------------------
    # 注意:
    # - 相場時間外でも、過去の1分足を「1秒ごとに1分」進めて判定/Discord通知を確認できます。
    # - 通常モード（TEST_REPLAY_MODE=False）側は、既存コードをできるだけ触らない方針です。
    if TEST_REPLAY_MODE:
        # --replay-range random_5d をショートカットとして扱う
        if str(replay_range) == "random_5d" and int(replay_random_days or 0) <= 0:
            replay_random_days = 5
            replay_random_months = 3

        n = int(replay_repeat)
        if n <= 0:
            n = 1

        # 後場比較モード（ユーザー要望）
        # - 通常 / 後場禁止 / 後場厳格化 を同一バッチで比較する
        afternoon_compare_modes: list[dict[str, Any]] = []
        if bool(replay_afternoon_compare):
            afternoon_compare_modes = [
                {"mode": "NORMAL", "disable": False, "strict": False},
                {"mode": "AFTERNOON_DISABLED", "disable": True, "strict": False},
                {"mode": "AFTERNOON_STRICT", "disable": False, "strict": True},
            ]

        # repeatの合算
        run_summaries: list[dict[str, Any]] = []
        repeat_label = f"random_{int(replay_random_days)}d" if int(replay_random_days or 0) > 0 else str(replay_range)

        # repeatロットごとのフォルダ（results配下にまとめる）
        batch_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        output_subdir = ""
        if n > 1 or bool(afternoon_compare_modes):
            # 例: results/replay_random_5d_20260507_232500/
            output_subdir = f"replay_{repeat_label}_{batch_stamp}"

        run_no = 0
        for i in range(1, n + 1):
            # seed指定時は runごとにずらして「同じセットを再現しつつ、別サンプル」を作れるようにします
            seed_run = None
            if replay_seed is not None:
                seed_run = int(replay_seed) + int(i) - 1

            # compare指定時は同一seed/dayセットで 3パターンを連続実行
            variants = afternoon_compare_modes if afternoon_compare_modes else [{"mode": "SINGLE", "disable": bool(replay_disable_afternoon_entry), "strict": bool(replay_strict_afternoon_entry)}]
            for v in variants:
                run_no += 1
                code = run_replay(
                    interval_sec=interval_sec,
                    only_changes=only_changes,
                    fixed_watch=fixed_watch,
                    replay_range=replay_range,
                    replay_random_days=replay_random_days,
                    replay_random_months=replay_random_months,
                    replay_seed=seed_run,
                    replay_mode=replay_mode,
                    replay_fast_discord=replay_fast_discord,
                    replay_fast_verbose=replay_fast_verbose,
                    replay_fast_print_signal_details=replay_fast_print_signal_details,
                    replay_market_debug=replay_market_debug,
                    replay_repeat_run_no=run_no,
                    replay_repeat_total=(int(n) * int(len(variants))),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm=replay_morning_screen_hhmm,
                    one_trade_per_symbol_per_day=one_trade_per_symbol_per_day,
                    enable_add=enable_add,
                    replay_early_exit_before_stop=replay_early_exit_before_stop,
                    replay_early_exit_vwap=bool(replay_early_exit_vwap),
                    replay_early_exit_recent_low=bool(replay_early_exit_recent_low),
                    replay_disable_afternoon_entry=bool(v.get("disable", False)),
                    replay_strict_afternoon_entry=bool(v.get("strict", False)),
                    replay_afternoon_topix_weak_block=bool(replay_afternoon_topix_weak_block),
                    replay_config_name=str(replay_config_name or ""),
                    replay_config_path=str(replay_config_path or ""),
                    aft_volume_spike_ratio_min=float(aft_volume_spike_ratio_min),
                    aft_vwap_dist_pct_max=float(aft_vwap_dist_pct_max),
                    aft_rebreak_mult=float(aft_rebreak_mult),
                    replay_settings=replay_settings,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] Replay repeat run{run_no:02d} が失敗しました（exit_code={int(code)}）")
                    return int(code)

            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                results_dir = os.path.join(script_dir, "results")
                if output_subdir:
                    results_dir = os.path.join(results_dir, output_subdir)
                run_tag = f"run{i:02d}"
                # jsonだけ探す（repeatフォルダ内）
                candidates = [fn for fn in os.listdir(results_dir) if fn.endswith(".json") and fn.endswith(f"{run_tag}.json")]
                candidates_sorted = sorted(
                    candidates,
                    key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                    reverse=True,
                )
                if candidates_sorted:
                    p = os.path.join(results_dir, candidates_sorted[0])
                    with open(p, "r", encoding="utf-8") as f:
                        rep = json.load(f)
                    run_summaries.append({"run_no": i, "json_path": p, "report": rep})
            except Exception:
                pass

        # 合算サマリー（保存 + 表示）
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            results_dir = os.path.join(script_dir, "results")
            if output_subdir:
                results_dir = os.path.join(results_dir, output_subdir)
            os.makedirs(results_dir, exist_ok=True)
            # all_runs は repeatロットの時刻に揃える
            name_base = f"replay_summary_{repeat_label}_{batch_stamp}_all_runs"

            # 指標の集計
            total_runs = len(run_summaries)
            if total_runs <= 0:
                print(f"[{now_str()}] 合算対象が0件でした。")
                return 0

            run_stats: list[dict[str, Any]] = []
            by_symbol_pnl: dict[str, float] = {}
            by_symbol_signals: dict[str, int] = {}
            pos_pnl: dict[str, float] = {"BASE": 0.0, "ADD1": 0.0, "ADD2": 0.0}
            pos_signals: dict[str, int] = {"BASE": 0, "ADD1": 0, "ADD2": 0}
            block_total = 0
            block_reason_counts: dict[str, int] = {}

            for rr in run_summaries:
                rep = rr.get("report") or {}
                stats = (((rep.get("overall_summary") or {}).get("stats")) or {})
                pnl = float(stats.get("pnl_yen_100_shares") or 0.0)
                sigs = int(stats.get("signals") or 0)
                wr = float(stats.get("win_rate_pct") or 0.0)
                exp = float(stats.get("expectancy_yen_100_shares_per_signal") or 0.0)
                run_stats.append({"signals": sigs, "win_rate_pct": wr, "pnl": pnl, "exp": exp})

            # ADD ON/OFF（参考）も合算
            add_on_total = 0.0
            add_off_ref_total = 0.0
            for rr in run_summaries:
                rep = rr.get("report") or {}
                ac = rep.get("add_comparison") or {}
                add_on_total += float(ac.get("pnl_add_on_yen_100_shares") or 0.0)
                add_off_ref_total += float(ac.get("pnl_add_off_ref_yen_100_shares") or 0.0)

                # BLOCK合計
                mf = rep.get("market_filter") or {}
                block_total += int(mf.get("blocked_entry_count") or 0)
                for it in (mf.get("blocked_reason_ranking") or []):
                    try:
                        r = str(it.get("reason") or "")
                        c = int(it.get("count") or 0)
                        if r:
                            block_reason_counts[r] = int(block_reason_counts.get(r, 0)) + c
                    except Exception:
                        continue

                # BASE/ADD別（合算）
                by_pos = rep.get("by_position_kind_summary") or {}
                for pk in ["BASE", "ADD1", "ADD2"]:
                    s = by_pos.get(pk) or {}
                    pos_pnl[pk] = float(pos_pnl.get(pk, 0.0)) + float(s.get("pnl_yen_100_shares") or 0.0)
                    pos_signals[pk] = int(pos_signals.get(pk, 0)) + int(s.get("signals") or 0)

                # 銘柄別合算（期待値ランキング用）
                by_sym = rep.get("by_symbol_summary") or {}
                for sym, s in by_sym.items():
                    try:
                        by_symbol_pnl[sym] = float(by_symbol_pnl.get(sym, 0.0)) + float((s or {}).get("pnl_yen_100_shares") or 0.0)
                        by_symbol_signals[sym] = int(by_symbol_signals.get(sym, 0)) + int((s or {}).get("signals") or 0)
                    except Exception:
                        continue

            total_signals = sum(int(x.get("signals") or 0) for x in run_stats)
            total_pnl = sum(float(x.get("pnl") or 0.0) for x in run_stats)
            avg_wr = sum(float(x.get("win_rate_pct") or 0.0) for x in run_stats) / float(total_runs) if total_runs > 0 else 0.0
            avg_pnl = total_pnl / float(total_runs) if total_runs > 0 else 0.0
            avg_exp = sum(float(x.get("exp") or 0.0) for x in run_stats) / float(total_runs) if total_runs > 0 else 0.0
            plus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) > 0)
            minus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) < 0)
            max_win_run = max(run_stats, key=lambda x: float(x.get("pnl") or 0.0))
            max_lose_run = min(run_stats, key=lambda x: float(x.get("pnl") or 0.0))

            # 銘柄別期待値ランキング（合算）
            sym_rank = []
            for sym, pnl in by_symbol_pnl.items():
                n_sig = int(by_symbol_signals.get(sym, 0))
                exp2 = (float(pnl) / float(n_sig)) if n_sig > 0 else 0.0
                sym_rank.append({"symbol": sym, "signals": n_sig, "pnl_yen_100_shares": float(pnl), "expectancy_yen_100_shares": float(exp2)})
            sym_rank_sorted = sorted(sym_rank, key=lambda x: float(x.get("expectancy_yen_100_shares") or 0.0), reverse=True)[:30]

            block_rank = sorted(
                [{"reason": k, "count": int(v)} for k, v in block_reason_counts.items()],
                key=lambda x: int(x.get("count") or 0),
                reverse=True,
            )

            agg = {
                "meta": {
                    "saved_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                    "repeat_label": repeat_label,
                    "repeat_runs": int(total_runs),
                    "output_folder": str(output_subdir),
                },
                "summary": {
                    "runs": int(total_runs),
                    "total_signals": int(total_signals),
                    "avg_win_rate_pct": float(avg_wr),
                    "avg_pnl_yen_100_shares": float(avg_pnl),
                    "total_pnl_yen_100_shares": float(total_pnl),
                    "avg_expectancy_yen_100_shares": float(avg_exp),
                    "add_on_total_pnl_yen_100_shares": float(add_on_total),
                    "add_off_ref_total_pnl_yen_100_shares": float(add_off_ref_total),
                    "plus_runs": int(plus_runs),
                    "minus_runs": int(minus_runs),
                    "max_win_run_pnl_yen_100_shares": float(max_win_run.get("pnl") or 0.0),
                    "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
                },
                "by_position_kind": {
                    pk: {
                        "signals": int(pos_signals.get(pk, 0)),
                        "pnl_yen_100_shares": float(pos_pnl.get(pk, 0.0)),
                        "expectancy_yen_100_shares": (float(pos_pnl.get(pk, 0.0)) / float(pos_signals.get(pk, 1)))
                        if int(pos_signals.get(pk, 0)) > 0
                        else 0.0,
                    }
                    for pk in ["BASE", "ADD1", "ADD2"]
                },
                "market_filter": {
                    "blocked_entry_total": int(block_total),
                    "blocked_reason_ranking": block_rank[:30],
                },
                "by_symbol_expectancy_ranking": sym_rank_sorted,
                "runs": [
                    {
                        "run_no": int(x.get("run_no") or 0),
                        "json_path": str(x.get("json_path") or ""),
                        "replay_dates": list(((x.get("report") or {}).get("meta") or {}).get("replay_dates") or []),
                        "replay_seed": ((x.get("report") or {}).get("meta") or {}).get("replay_seed"),
                        "signals": int((((x.get("report") or {}).get("overall_summary") or {}).get("stats") or {}).get("signals") or 0),
                        "win_rate_pct": float((((x.get("report") or {}).get("overall_summary") or {}).get("stats") or {}).get("win_rate_pct") or 0.0),
                        "pnl_yen_100_shares": float((((x.get("report") or {}).get("overall_summary") or {}).get("stats") or {}).get("pnl_yen_100_shares") or 0.0),
                    }
                    for x in run_summaries
                ],
            }

            json_path = os.path.join(results_dir, f"{name_base}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(agg, f, ensure_ascii=False, indent=2)

            # all_runs のCSV（run単位の一覧 + 合計/平均の目安）
            csv_path = os.path.join(results_dir, f"{name_base}.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as fcsv:
                wcsv = csv.writer(fcsv)
                wcsv.writerow(["run_no", "replay_seed", "signals", "win_rate_pct", "pnl_yen_100_shares"])
                for r in agg.get("runs") or []:
                    wcsv.writerow(
                        [
                            int(r.get("run_no") or 0),
                            r.get("replay_seed"),
                            int(r.get("signals") or 0),
                            float(r.get("win_rate_pct") or 0.0),
                            float(r.get("pnl_yen_100_shares") or 0.0),
                        ]
                    )
                wcsv.writerow([])
                s = agg.get("summary") or {}
                wcsv.writerow(["TOTAL", "", int(s.get("total_signals") or 0), "", float(s.get("total_pnl_yen_100_shares") or 0.0)])
                wcsv.writerow(["AVG", "", "", float(s.get("avg_win_rate_pct") or 0.0), float(s.get("avg_pnl_yen_100_shares") or 0.0)])

            lines: list[str] = []
            lines.append("=== Replay 合算サマリー ===")
            # 要件: 「今回どの設定で回したか」を最上部に必ず表示（=== の直下）
            try:
                cfg_names = set()
                cfg_paths = set()
                ee_vals = set()
                sa_vals = set()
                da_vals = set()
                tw_vals = set()
                for rr in run_summaries:
                    rep = rr.get("report") or {}
                    rs = rep.get("replay_settings") if isinstance(rep.get("replay_settings"), dict) else None
                    if not isinstance(rs, dict):
                        continue
                    cfg_names.add(str(rs.get("config_name") or ""))
                    cfg_paths.add(str(rs.get("config_path") or ""))
                    try:
                        ee_vals.add(bool((rs.get("early_exit") or {}).get("value")))
                    except Exception:
                        pass
                    try:
                        sa_vals.add(bool((rs.get("strict_afternoon") or {}).get("value")))
                    except Exception:
                        pass
                    try:
                        da_vals.add(bool((rs.get("disable_afternoon_entry") or {}).get("value")))
                    except Exception:
                        pass
                    try:
                        tw_vals.add(bool((rs.get("topix_weak_block") or {}).get("value")))
                    except Exception:
                        pass
                lines.append("【Replay設定】")
                lines.append("")
                lines.append(f"config_name: {list(cfg_names)[0] if len(cfg_names)==1 else 'MIXED'}")
                lines.append(f"config_path: {list(cfg_paths)[0] if len(cfg_paths)==1 else 'MIXED'}")
                lines.append("")
                lines.append(f"replay_range: {repeat_label} (cli)")
                lines.append(f"replay_repeat: {int(total_runs)} (cli)")
                lines.append(f"replay_mode: {str('fast' if any('fast' in str((rr.get('report') or {}).get('meta',{}).get('replay_mode','')) for rr in run_summaries) else '')} (cli)")
                lines.append("")
                if ee_vals:
                    lines.append(f"early_exit: {list(ee_vals)[0] if len(ee_vals)==1 else 'MIXED'}")
                if sa_vals:
                    lines.append(f"strict_afternoon: {list(sa_vals)[0] if len(sa_vals)==1 else 'MIXED'}")
                if da_vals:
                    lines.append(f"disable_afternoon_entry: {list(da_vals)[0] if len(da_vals)==1 else 'MIXED'}")
                if tw_vals:
                    lines.append(f"topix_weak_block: {list(tw_vals)[0] if len(tw_vals)==1 else 'MIXED'}")
                lines.append("")
            except Exception:
                pass
            lines.append(f"- saved_at_jst: {agg['meta']['saved_at_jst']}")
            lines.append(f"- repeat_label: {repeat_label}")
            if output_subdir:
                lines.append(f"- output_folder: results/{output_subdir}/")
            lines.append(f"- 実行回数: {total_runs}")
            s = agg["summary"]
            lines.append(f"- 合計signal数: {s['total_signals']}")
            lines.append(f"- 平均勝率: {s['avg_win_rate_pct']:.1f}%")
            lines.append(f"- 平均100株損益: {s['avg_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- 合計100株損益: {s['total_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- 平均expectancy: {s['avg_expectancy_yen_100_shares']:+,.0f}円")
            lines.append(f"- ADD ON時損益(合算,100株): {float(s.get('add_on_total_pnl_yen_100_shares') or 0.0):+,.0f}円")
            lines.append(f"- ADD OFF時損益(参考/BASEのみ合算,100株): {float(s.get('add_off_ref_total_pnl_yen_100_shares') or 0.0):+,.0f}円")
            lines.append(f"- プラスrun数/マイナスrun数: {s['plus_runs']}/{s['minus_runs']}")
            lines.append(f"- 最大勝ちrun: {s['max_win_run_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- 最大負けrun: {s['max_lose_run_pnl_yen_100_shares']:+,.0f}円")
            lines.append("")

            lines.append("【各runの概要】")
            baseline_worst10_sum: Optional[float] = None
            global_max_loss: Optional[dict[str, Any]] = None  # {"run_no": int, "pnl": float, "symbol": str, "time": str, "exit_reason": str}
            for r in agg["runs"]:
                dts = ", ".join(list(r.get("replay_dates") or []))
                lines.append(
                    f"- run{int(r['run_no']):02d}: seed={r.get('replay_seed')}  "
                    f"signals={int(r['signals'])}  勝率={float(r['win_rate_pct']):.1f}%  "
                    f"100株損益={float(r['pnl_yen_100_shares']):+,.0f}円"
                )
                lines.append(f"  replay_dates: {dts}")

                # 追加: Replay比較（ユーザー要望）
                try:
                    rep = {}
                    for rr in run_summaries:
                        if int(rr.get("run_no") or 0) == int(r.get("run_no") or 0):
                            rep = rr.get("report") or {}
                            break
                    cfg = rep.get("replay_config") or {}
                    if isinstance(cfg, dict):
                        lines.append(f"  config_name: {str(cfg.get('config_name') or '')}")
                        lines.append(f"  config_path: {str(cfg.get('config_path') or '')}")
                        lines.append(f"  early_exit: {bool(cfg.get('early_exit_before_stop', False))}")
                        lines.append(f"  strict_afternoon: {bool(cfg.get('strict_afternoon_entry', False))}")
                        lines.append(f"  disable_afternoon_entry: {bool(cfg.get('disable_afternoon_entry', False))}")
                        lines.append(f"  topix_weak_block: {bool(cfg.get('afternoon_topix_weak_block', False))}")
                    aa = rep.get("accident_analysis") or {}
                    lw = aa.get("lose_worst10") or []
                    worst10_sum = None
                    if isinstance(lw, list) and lw:
                        pnls = []
                        for it in lw:
                            try:
                                pnls.append(float(it.get("pnl_yen_100_shares") or 0.0))
                            except Exception:
                                continue
                        worst10_sum = float(sum(pnls)) if pnls else None
                        if baseline_worst10_sum is None:
                            baseline_worst10_sum = float(worst10_sum) if isinstance(worst10_sum, (int, float)) else None
                        if isinstance(worst10_sum, (int, float)):
                            if isinstance(baseline_worst10_sum, (int, float)) and int(r.get("run_no") or 0) != int(agg["runs"][0].get("run_no") or 1):
                                lines.append(
                                    f"  lose_worst10_sum={float(worst10_sum):+,.0f}円 "
                                    f"(vs baseline {float(worst10_sum - baseline_worst10_sum):+,.0f}円)"
                                )
                            else:
                                lines.append(f"  lose_worst10_sum={float(worst10_sum):+,.0f}円")

                        # 最大損失（そのrun内）= worst10の先頭（最小pnl）
                        try:
                            mx = min(lw, key=lambda x: float(x.get("pnl_yen_100_shares") or 0.0))
                            pnl_mx = float(mx.get("pnl_yen_100_shares") or 0.0)
                            cur = {
                                "run_no": int(r.get("run_no") or 0),
                                "pnl": pnl_mx,
                                "symbol": str(mx.get("symbol") or ""),
                                "time": str(mx.get("signal_time_jst") or ""),
                                "exit_reason": str(mx.get("exit_reason") or ""),
                            }
                            if (global_max_loss is None) or (float(cur["pnl"]) < float(global_max_loss.get("pnl") or 0.0)):
                                global_max_loss = cur
                        except Exception:
                            pass
                except Exception:
                    pass
            lines.append("")

            # 最大損失run（ユーザー要望）
            if isinstance(global_max_loss, dict):
                lines.append("【最大損失run】")
                lines.append(
                    f"- run{int(global_max_loss.get('run_no') or 0):02d}: "
                    f"{float(global_max_loss.get('pnl') or 0.0):+,.0f}円 "
                    f"{str(global_max_loss.get('symbol') or '')} {str(global_max_loss.get('time') or '')} "
                    f"exit_reason={str(global_max_loss.get('exit_reason') or '')}"
                )
                lines.append("")

            # 比較: 後場損益（12:30以降）
            try:
                by_mode: dict[str, dict[str, Any]] = {}
                for rr in run_summaries:
                    rep = rr.get("report") or {}
                    cfg = rep.get("replay_config") or {}
                    key = "NORMAL"
                    if isinstance(cfg, dict):
                        if bool(cfg.get("disable_afternoon_entry", False)):
                            key = "AFTERNOON_DISABLED"
                        elif bool(cfg.get("strict_afternoon_entry", False)):
                            key = "AFTERNOON_STRICT"
                    sigs = rep.get("signals") or []
                    aft = []
                    for s2 in (sigs if isinstance(sigs, list) else []):
                        try:
                            t = str(s2.get("signal_time_jst") or "")
                            # signal_time_jst は "YYYY-MM-DD HH:MM" を想定
                            if len(t) >= 16:
                                hhmm = t[11:16]
                                hh = int(hhmm.split(":")[0])
                                mm = int(hhmm.split(":")[1])
                                hm = hh * 60 + mm
                                if hm >= (12 * 60 + 30):
                                    aft.append(s2)
                        except Exception:
                            continue
                    pnl = 0.0
                    for s2 in aft:
                        try:
                            pnl += float(s2.get("pnl_yen_100_shares") or 0.0)
                        except Exception:
                            continue
                    st = by_mode.get(key) or {"signals": 0, "pnl": 0.0}
                    st["signals"] = int(st.get("signals") or 0) + int(len(aft))
                    st["pnl"] = float(st.get("pnl") or 0.0) + float(pnl)
                    by_mode[key] = st
                if by_mode:
                    lines.append("【後場損益（12:30以降, mode別合算）】")
                    for k in ["NORMAL", "AFTERNOON_DISABLED", "AFTERNOON_STRICT"]:
                        if k in by_mode:
                            st = by_mode[k]
                            sig_n = int(st.get("signals") or 0)
                            pnl2 = float(st.get("pnl") or 0.0)
                            exp2 = (pnl2 / sig_n) if sig_n > 0 else 0.0
                            lines.append(f"- {k}: signals={sig_n}  100株損益={pnl2:+,.0f}円  expectancy={exp2:+,.0f}円")
                    lines.append("")
            except Exception:
                pass

            lines.append("【BASE/ADD別（合算）】")
            for pk in ["BASE", "ADD1", "ADD2"]:
                a = agg["by_position_kind"][pk]
                lines.append(
                    f"- {pk}: signals={int(a['signals'])}  100株損益={float(a['pnl_yen_100_shares']):+,.0f}円  "
                    f"expectancy={float(a['expectancy_yen_100_shares']):+,.0f}円"
                )
            lines.append("")

            lines.append("【地合いフィルタ】")
            lines.append(f"- BLOCK数合計: {int(agg['market_filter']['blocked_entry_total'])}")
            br = agg["market_filter"].get("blocked_reason_ranking") or []
            if br:
                lines.append("- BLOCK理由ランキング:")
                for it in br[:10]:
                    lines.append(f"  - {it['reason']}: {int(it['count'])}")
            lines.append("")

            # =========================
            # REJECT理由ランキング（ユーザー要望）
            # - Replayで「候補が reject された理由」を合算して可視化します
            # =========================
            rej_total: dict[str, int] = {}
            for rr in run_summaries:
                rep = rr.get("report") or {}
                for it in (rep.get("reject_reason_ranking") or []):
                    try:
                        k = str(it.get("reason") or "").strip()
                        v = int(it.get("count") or 0)
                        if k:
                            rej_total[k] = int(rej_total.get(k, 0)) + v
                    except Exception:
                        continue
            rej_rank = sorted(
                [{"reason": k, "count": int(v)} for k, v in rej_total.items()],
                key=lambda x: int(x.get("count") or 0),
                reverse=True,
            )
            lines.append("[REJECT_REASON_RANKING]")
            lines.append("")
            for it in rej_rank[:30]:
                lines.append(f"{it['reason']}: {int(it['count'])}")
            lines.append("")

            # =========================
            # PIPELINE_DEBUG（ユーザー要望）
            # - Replayパイプラインのどの段階で落ちているかを可視化
            # =========================
            pd_tot: dict[str, int] = {}
            cr_tot: dict[str, int] = {}
            for rr in run_summaries:
                rep = rr.get("report") or {}
                pd = rep.get("pipeline_debug") or {}
                if isinstance(pd, dict):
                    for k, v in pd.items():
                        try:
                            pd_tot[str(k)] = int(pd_tot.get(str(k), 0)) + int(v)
                        except Exception:
                            continue
                cr = rep.get("continue_reason_counts") or {}
                if isinstance(cr, dict):
                    for k, v in cr.items():
                        try:
                            kk = str(k).strip()
                            if kk:
                                cr_tot[kk] = int(cr_tot.get(kk, 0)) + int(v)
                        except Exception:
                            continue
            lines.append("[PIPELINE_DEBUG]")
            lines.append("")
            for k in [
                "market_debug_count",
                "candidate_loop_entered",
                "to_notify_count",
                "entry_calc_ok",
                "entry_calc_none",
                "ma25_ok",
                "ma25_none",
                "intraday_signal_ready",
                "intraday_signal_none",
                "crossed_check_entered",
                "crossed_true",
                "crossed_false",
                "signal_generated",
                "replay_signals_append_count",
                "pre_signal_object_count",
                "post_signal_object_count",
            ]:
                if k in pd_tot:
                    lines.append(f"{k}={int(pd_tot.get(k) or 0)}")
            lines.append("")
            lines.append("continue_reason_counts:")
            for it in sorted(cr_tot.items(), key=lambda kv: int(kv[1]), reverse=True)[:30]:
                lines.append(f"{it[0]}: {int(it[1])}")
            lines.append("")

            # generated/replay_signals/eval_signals の段階別合算（ユーザー要望）
            gen_total = 0
            rep_total = 0
            eval_total = 0
            for rr in run_summaries:
                rep = rr.get("report") or {}
                pd = rep.get("pipeline_debug") or {}
                if isinstance(pd, dict):
                    gen_total += int(pd.get("signal_generated") or 0)
                efd = rep.get("eval_filter_debug") or {}
                if isinstance(efd, dict):
                    rep_total += int(efd.get("before_count") or 0)
                    eval_total += int(efd.get("after_count") or 0)
            lines.append("generated_signal_count=" + str(int(gen_total)))
            lines.append("replay_signals_count=" + str(int(rep_total)))
            lines.append("eval_signals_count=" + str(int(eval_total)))
            lines.append("")

            lines.append("【銘柄別 期待値ランキング（合算）】")
            for it in agg["by_symbol_expectancy_ranking"][:10]:
                lines.append(
                    f"- {it['symbol']}: signals={int(it['signals'])}  expectancy={float(it['expectancy_yen_100_shares']):+,.0f}円  "
                    f"100株損益={float(it['pnl_yen_100_shares']):+,.0f}円"
                )
            lines.append("")

            # =========================
            # MARKET_DEBUG（ユーザー要望）
            # - Replay summaryではなく all_runs.txt に「signal候補時」の地合いデバッグを必ず残す
            # - 各runの report["signals"]（検出されたsignal候補）から抽出
            # =========================
            lines.append("[MARKET_DEBUG]")
            lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                md = rep.get("market_debug") or {}
                rows = md.get("rows") or []
                rows_total = int(md.get("rows_total") or 0)
                truncated = bool(md.get("truncated", False))
                if not isinstance(rows, list) or not rows:
                    lines.append(f"run{run_no:02d}: (no market_debug rows)")
                    lines.append("")
                    continue
                lines.append(f"run{run_no:02d}: market_debug_rows={rows_total}{' (truncated)' if truncated else ''}")
                lines.append("")
                for r2 in rows:
                    if not isinstance(r2, dict):
                        continue
                    lines.append(str(r2.get("timestamp_jst") or ""))
                    lines.append(f"symbol={str(r2.get('symbol') or '')}")
                    lines.append("")
                    lines.append(f"topix_fetch_ok={bool(r2.get('topix_fetch_ok', False))}")
                    tr = r2.get("topix_raw")
                    if isinstance(tr, (int, float)):
                        lines.append(f"topix_raw={float(tr):.2f}")
                    else:
                        lines.append("topix_raw=N/A")
                    pc2 = r2.get("topix_prev_close")
                    if isinstance(pc2, (int, float)):
                        lines.append(f"topix_prev_close={float(pc2):.2f}")
                    else:
                        lines.append("topix_prev_close=N/A")
                    tp = r2.get("topix_pct")
                    if isinstance(tp, (int, float)):
                        lines.append(f"topix_pct={float(tp):+.2f}")
                    else:
                        lines.append("topix_pct=N/A")
                    lines.append(f"market_state={str(r2.get('market_state') or '')}")
                    lines.append(f"entry_allowed={bool(r2.get('entry_allowed', True))}")
                    br = r2.get("blocked_reason") or []
                    if isinstance(br, list):
                        br_s = ",".join([str(x) for x in br if str(x).strip()])
                    else:
                        br_s = str(br)
                    lines.append(f"blocked_reason=[{br_s}]")
                    lines.append("")
                lines.append("")
                continue

            # =========================
            # CROSSED_DEBUG（ユーザー要望）
            # - candidates到達後に crossed が成立しない原因を切り分けます
            # =========================
            lines.append("[CROSSED_DEBUG]")
            lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                cd = rep.get("crossed_debug") or {}
                rows = cd.get("rows") or []
                rows_total = int(cd.get("rows_total") or 0)
                truncated = bool(cd.get("truncated", False))
                if not isinstance(rows, list) or not rows:
                    lines.append(f"run{run_no:02d}: (no crossed_debug rows)")
                    lines.append("")
                    continue
                lines.append(f"run{run_no:02d}: crossed_debug_rows={rows_total}{' (truncated)' if truncated else ''}")
                lines.append("")
                for r2 in rows:
                    if not isinstance(r2, dict):
                        continue
                    lines.append(f"symbol={str(r2.get('symbol') or '')}")
                    lines.append(f"time={str(r2.get('time_jst') or '')}")
                    p = r2.get("price")
                    h5 = r2.get("high_5m")
                    cr = bool(r2.get("crossed", False))
                    df = r2.get("diff_pct")
                    if isinstance(p, (int, float)):
                        lines.append(f"price={float(p):.2f}")
                    else:
                        lines.append("price=N/A")
                    if isinstance(h5, (int, float)):
                        lines.append(f"high_5m={float(h5):.2f}")
                    else:
                        lines.append("high_5m=N/A")
                    lines.append(f"crossed={cr}")
                    if isinstance(df, (int, float)):
                        lines.append(f"diff={float(df):+.2f}%")
                    else:
                        lines.append("diff=N/A")
                    lines.append("")
                lines.append("")

                # (legacy) signals[] ベースの出力（互換用。ここには通常到達しません）
                sigs = rep.get("signals") or []
                if not isinstance(sigs, list) or not sigs:
                    lines.append(f"run{run_no:02d}: (no signals)")
                    lines.append("")
                    continue
                for s2 in sigs:
                    if not isinstance(s2, dict):
                        continue
                    lines.append(f"run{run_no:02d} {str(s2.get('symbol') or '')} {str(s2.get('signal_time_jst') or '')}")
                    lines.append(f"topix_fetch_ok={bool(s2.get('topix_fetch_ok', False))}")
                    # topix_raw は TOPIX価格レベル（例: 2783.52）
                    tr = s2.get("topix_raw")
                    if isinstance(tr, (int, float)):
                        lines.append(f"topix_raw={float(tr):.2f}")
                    else:
                        lines.append("topix_raw=N/A")
                    tp = s2.get("topix_pct")
                    if isinstance(tp, (int, float)):
                        lines.append(f"topix_pct={float(tp):+.2f}")
                    else:
                        lines.append("topix_pct=N/A")
                    lines.append(f"market_state={str(s2.get('market_state') or s2.get('market_regime') or '')}")
                    lines.append(f"entry_allowed={bool(s2.get('entry_allowed', True))}")
                    br2 = str(s2.get('blocked_reason') or "")
                    lines.append(f"blocked_reason=[{br2}]")
                    lines.append("")

            txt_path = os.path.join(results_dir, f"{name_base}.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            print("\n".join(lines))
            print(f"[{now_str()}] 合算サマリーを保存しました: {txt_path}")
            print(f"[{now_str()}] 合算サマリーを保存しました: {json_path}")
        except Exception as e:
            print(f"[{now_str()}] 合算サマリーの作成に失敗しました: {e}")

        return 0

    print("=== Yahoo Finance 日本株 スクリーニング（発注なし） ===")
    if fixed_watch is not None:
        print(f"- watch(固定): {', '.join(fixed_watch) if fixed_watch else '(empty)'}")
    else:
        print(f"- watch: (watchlist.json があれば毎回読み直します)")
    print(f"- interval: {interval_sec} sec")
    print(
        f"- 条件: 前日比 {MIN_CHANGE_PCT}%以上 {MAX_CHANGE_PCT}%未満 / "
        f"当日高値の {MIN_RATIO_TO_DAY_HIGH*100:.0f}%以上 / "
        f"出来高 {MIN_VOLUME:,}以上 / "
        f"現在値がMA25より上"
    )
    print("- Ctrl+C で終了します。\n")

    # only_changes 用。直前に出した候補セットを覚えておきます。
    last_candidates: set[str] = set()

    # Discord通知用:
    # - これまでは DISCORD_WEBHOOK_URL（Webhook）だけでしたが、
    #   今回から ALERT_CHANNEL_ID を指定すると「そのチャンネルへ送る」ことができます。
    #
    # 使い分け（初心者向け）:
    # - まず簡単に分離したい場合:
    #     通知用チャンネルでWebhookを作り、そのURLを DISCORD_WEBHOOK_URL に設定してください。
    #     → これだけで通知ログが分離できます（Bot権限などの難しい話が不要）。
    # - Bot送信に統一したい場合:
    #     ALERT_CHANNEL_ID と DISCORD_BOT_TOKEN を設定してください。
    #     → Webhookに依存せず、指定チャンネルへ直接送れます。
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    # Bot送信用トークンは DISCORD_TOKEN に統一します（旧: DISCORD_BOT_TOKEN は互換で吸収）
    bot_token = _get_discord_token_with_compat_warning()

    # 通知が有効かどうか:
    # - Bot送信（ALERT_CHANNEL_ID + DISCORD_BOT_TOKEN）か、
    # - Webhook（DISCORD_WEBHOOK_URL）
    # のどちらかが使えれば True です。
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)

    # ログ（仕様）:
    # - Webhook URL が設定されている場合は「Webhookの送信先に送られる」ことを表示します。
    if webhook_url:
        print(f"[{now_str()}] Discord通知: DISCORD_WEBHOOK_URL が設定されています（Webhookの送信先チャンネルに送られます）")
    if alert_channel_id is not None and bot_token:
        print(f"[{now_str()}] Discord通知: ALERT_CHANNEL_ID={alert_channel_id} へ Bot送信します（推奨）")
    last_discord_candidate_symbols: set[str] = set()

    # MA25 のキャッシュ:
    # - symbol ごとに (ma25, fetched_at_monotonic) を持ちます
    ma25_cache: dict[str, tuple[float, float]] = {}

    # 出来高5日平均（VOL_AVG5）のキャッシュ:
    avg5_cache: dict[str, tuple[float, float]] = {}

    # VWAP のキャッシュ:
    # - None もキャッシュして、短い時間での再試行を減らします。
    vwap_cache: dict[str, tuple[Optional[float], float]] = {}

    # 1分足系列（直近シグナル計算用）のキャッシュ:
    # - symbol ごとに (closes, highs, vols, fetched_at)
    intraday_series_cache: dict[str, tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]], float]] = {}

    # watchlist.json のリアルタイム反映用:
    # - 前回の監視銘柄リスト（壊れたJSONを読んだ場合に「前回のリストを維持」するため）
    last_watch: list[str] = []

    # 候補価格の変更通知用:
    # - 銘柄ごとに「前回Discordへ通知した entry/stop/take」を覚えておきます。
    # - 条件一致中でも、この値が大きく変化したら再通知します。
    last_notified_levels: dict[str, tuple[float, float, float]] = {}
    # 条件外れ通知の安定化（連続不一致をカウント）
    exit_miss_count: dict[str, int] = {}

    # requests.Session を使うと、接続の再利用ができて少し効率が良くなります。
    with requests.Session() as session:
        try:
            while True:
                loop_started = time.perf_counter()

                # =========================
                # 監視銘柄の決定（毎ループ）
                # =========================
                watch: list[str] = []

                if fixed_watch is not None:
                    # コマンドライン指定がある場合は固定です（リアルタイム反映しません）。
                    watch = list(fixed_watch)
                else:
                    # watchlist.json が存在する場合は「常にそれを正」として読み直します。
                    if os.path.exists(WATCHLIST_JSON_PATH):
                        watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
                        if err:
                            # 壊れていた場合はエラーログを出して前回のリストを維持します。
                            print(f"[{now_str()}] watchlist.json 読み込みエラー（前回の監視リストを維持）: {err}")
                            watch = list(last_watch)
                        else:
                            watch = watch_loaded
                            last_watch = list(watch)

                        # 空配列 [] は「監視銘柄なし」という明示状態。
                        # フォールバックはせず、メッセージを出して待機します。
                        if not watch:
                            print(f"[{now_str()}] 監視銘柄なし。Discordで !watch add <symbol> してください。")
                            time.sleep(interval_sec)
                            continue
                    else:
                        # watchlist.json が無い場合だけ symbols.csv → WATCH にフォールバック
                        loaded_symbols = _load_symbols_csv(SYMBOLS_CSV_PATH)
                        watch = loaded_symbols if loaded_symbols else list(WATCH)

                # 念のため: 完全に空なら待機（固定watchで empty 指定など）
                if not watch:
                    print(f"[{now_str()}] 監視銘柄なし。Discordで !watch add <symbol> してください。")
                    time.sleep(interval_sec)
                    continue

                # Entry上抜け（クロス）判定用:
                # - このループで「前回価格」を参照できるように、読み取り用のスナップショットを作ります。
                prev_price_snapshot: dict[str, Optional[float]] = dict(prev_price_by_symbol)

                quotes: list[Quote] = []
                for sym in watch:
                    try:
                        q = fetch_quote(session, sym)
                        quotes.append(q)
                        last_quote_by_symbol[sym] = q
                        # 取得後に「今回の価格」を保存（次ループの prev になります）
                        prev_price_by_symbol[sym] = float(q.price)
                    except requests.HTTPError as e:
                        # 429（Too Many Requests）などが起きたら、間隔を伸ばす等が必要かもしれません。
                        print(f"[{now_str()}] {sym} HTTPエラー: {e}")
                    except Exception as e:
                        print(f"[{now_str()}] {sym} 取得エラー: {e}")

                # 条件判定して「候補だけ」残す（見送り理由も同時に作る）
                candidates: list[Quote] = []
                skip_reasons_by_symbol: dict[str, list[str]] = {}
                ma25_by_symbol: dict[str, float] = {}
                avg5_by_symbol: dict[str, float] = {}
                vol_spike_ratio_by_symbol: dict[str, Optional[float]] = {}
                vwap_by_symbol: dict[str, Optional[float]] = {}
                score_by_symbol: dict[str, int] = {}
                intraday_by_symbol: dict[str, IntradaySignals] = {}
                # 「このループで計算した entry」（= 直近5分高値ベース）を保存しておき、
                # 通知や状態管理（breakout_state）で同じ値を使います。
                current_entry_by_symbol: dict[str, Optional[float]] = {}
                # Entry上抜け（breakout）判定結果（このループ内で通知表示に使う）
                entry_cross_by_symbol: dict[str, bool] = {}

                # Replay銘柄スコア（ブラックリスト/優先）を取り込み（ファイルが無ければ空）
                symbol_blacklist, symbol_priority, quality_blocked = _symbol_blacklist_and_priority_sets()

                for q in quotes:
                    reasons: list[str] = []

                    # Replay期待値ベースのブラックリスト銘柄はENTRY対象外
                    if q.symbol in symbol_blacklist:
                        reasons.append("ブラックリスト(Replay)")
                    # Replay銘柄品質フィルタ（期待値の低い銘柄を禁止）
                    if q.symbol in quality_blocked:
                        reasons.append("品質フィルタ(Replay)")

                    # 1) 必須項目チェック（欠けていると判定できないので見送り）
                    if q.change_percent is None:
                        # 要件: previousClose が取れず前日比が計算できない場合は、この理由を追加します。
                        reasons.append("前日終値取得失敗")
                    if q.day_high is None:
                        reasons.append("当日高値が取得できない")
                    if q.volume is None:
                        reasons.append("出来高が取得できない")
                    # 時価総額（marketCap）は Yahoo 側で取れない銘柄があるため、
                    # 取得できないこと自体では見送りにしません（仕様変更）。

                    # 2) 前日比: MIN <= chg < MAX
                    if q.change_percent is not None:
                        if q.change_percent < MIN_CHANGE_PCT:
                            reasons.append("前日比不足")
                        if q.change_percent >= MAX_CHANGE_PCT:
                            reasons.append("急騰しすぎ")

                    # 3) 高値付近: 現在値 >= 0.98 * 当日高値
                    if q.day_high is not None:
                        if q.price < (MIN_RATIO_TO_DAY_HIGH * q.day_high):
                            reasons.append("高値付近ではない")

                    # 4) 出来高: volume >= MIN_VOLUME
                    if q.volume is not None:
                        if q.volume < float(MIN_VOLUME):
                            reasons.append("出来高不足")

                    # 5) 出来高急増（スコア加点）:
                    # 以前は「必須条件」でしたが、仕様変更で「加点条件」にしました。
                    # - 出来高30万以上（MIN_VOLUME）は必須のまま（別条件で判定）
                    # - 出来高急増は必須ではない
                    # - 5日平均出来高が取れた場合だけ倍率を計算して、
                    #   倍率が MIN_VOLUME_SPIKE_RATIO 以上なら +1点します。
                    #
                    # 5日平均出来高が取れない場合:
                    # - 見送りにはしません（必須条件ではないため）
                    # - 表示/Discordでは N/A 扱いになります
                    avg5: Optional[float] = None
                    if q.volume is not None:
                        try:
                            cached = avg5_cache.get(q.symbol)
                            if cached:
                                cached_avg5, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                    avg5 = cached_avg5

                            if avg5 is None:
                                fetched = fetch_avg_volume_5(session, q.symbol)
                                if fetched is not None:
                                    avg5_cache[q.symbol] = (float(fetched), time.perf_counter())
                                    avg5 = float(fetched)
                        except Exception:
                            avg5 = None

                    # スコアの初期値（今は出来高急増だけですが、将来増やせます）
                    score = 0

                    if avg5 is not None:
                        avg5_by_symbol[q.symbol] = avg5
                        ratio = q.volume / avg5 if avg5 > 0 else 0.0
                        vol_spike_ratio_by_symbol[q.symbol] = ratio
                        if ratio >= MIN_VOLUME_SPIKE_RATIO:
                            score += 1
                    else:
                        # 取れない場合は N/A 表示になるようにしておく
                        vol_spike_ratio_by_symbol[q.symbol] = None

                    # 優先銘柄（Replay期待値ベース）はスコア加点
                    if q.symbol in symbol_priority:
                        score += 2

                    score_by_symbol[q.symbol] = score

                    # 6) MA25: 取れないなら見送り、取れたら price > ma25 だけ通す
                    ma25: Optional[float] = None
                    try:
                        cached = ma25_cache.get(q.symbol)
                        if cached:
                            cached_ma25, fetched_at = cached
                            if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                ma25 = cached_ma25

                        if ma25 is None:
                            fetched = fetch_ma25(session, q.symbol)
                            if fetched is not None:
                                ma25_cache[q.symbol] = (float(fetched), time.perf_counter())
                                ma25 = float(fetched)
                    except Exception:
                        ma25 = None

                    if ma25 is None:
                        reasons.append("25日線が取得できない")
                    else:
                        ma25_by_symbol[q.symbol] = ma25
                        if q.price <= ma25:
                            reasons.append("25日線以下")

                    # 7) 時価総額レンジ: MIN_MARKET_CAP <= marketCap <= MAX_MARKET_CAP
                    # marketCap が取得できた場合だけレンジ判定します。
                    if q.market_cap is not None:
                        if q.market_cap < MIN_MARKET_CAP or q.market_cap > MAX_MARKET_CAP:
                            reasons.append("時価総額レンジ外")

                    # 8) VWAP/1分足由来の「エントリータイミング」条件
                    #
                    # ここが今回の改善ポイントです。
                    # - 以前: "price > VWAP"（= 強い銘柄っぽい）だけで候補になることがあり、レンジ往復でも通知が出やすい
                    # - 変更後: 「直近5分高値ブレイク」「5分前より上」「VWAPより0.2%以上上」を満たすときだけ候補にする
                    #
                    # これらを計算するために「1分足系列（close/high/volume）」も一緒に参照します。
                    vwap: Optional[float] = None
                    used_cache = False
                    if not reasons:
                        # ここまでの条件で「ほぼ通る」銘柄だけ VWAP を取りにいきます（負荷を下げるため）
                        try:
                            cached = vwap_cache.get(q.symbol)
                            used_cache = False
                            if cached:
                                cached_vwap, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VWAP_CACHE_TTL_SEC:
                                    # 期限内ならキャッシュをそのまま使います（None でもOK）
                                    vwap = cached_vwap
                                    used_cache = True

                            # 期限切れ or キャッシュ無しなら取り直し
                            if not used_cache:
                                vwap_fetched = fetch_vwap(session, q.symbol)
                                vwap_cache[q.symbol] = (vwap_fetched, time.perf_counter())
                                vwap = vwap_fetched
                        except Exception:
                            vwap = None

                        if vwap is None and not used_cache:
                            # 今回からVWAP乖離が必須条件になるので、取得できない場合は候補になりません。
                            print(f"[{now_str()}] {q.symbol} VWAP取得不可（この銘柄は候補になりません）")
                        else:
                            vwap_by_symbol[q.symbol] = vwap
                            # 1) VWAP乖離条件（必須）
                            sig_tmp = calc_intraday_signals_from_series(
                                price=float(q.price),
                                closes=[],
                                highs=[],
                                vols=[],
                                vwap=vwap,
                            )
                            intraday_by_symbol[q.symbol] = sig_tmp
                            if sig_tmp.vwap_distance_pct is None:
                                reasons.append("VWAP取得不可")
                            else:
                                if sig_tmp.vwap_distance_pct < float(VWAP_DISTANCE_PCT):
                                    reasons.append("VWAP乖離不足")

                            # 2) 1分足系列を取って recent_5m_high / price_5min_ago / 出来高増加 を計算
                            # - 毎秒叩くと重いので短いTTLキャッシュを使います
                            closes_1m: list[Optional[float]] = []
                            highs_1m: list[Optional[float]] = []
                            vols_1m: list[Optional[float]] = []
                            try:
                                cached = intraday_series_cache.get(q.symbol)
                                if cached:
                                    c_closes, c_highs, c_vols, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < INTRADAY_SERIES_CACHE_TTL_SEC:
                                        closes_1m, highs_1m, vols_1m = c_closes, c_highs, c_vols
                                if not closes_1m:
                                    c2, h2, v2 = fetch_intraday_1m_series(session, q.symbol)
                                    closes_1m, highs_1m, vols_1m = c2, h2, v2
                                    intraday_series_cache[q.symbol] = (closes_1m, highs_1m, vols_1m, time.perf_counter())
                            except Exception:
                                closes_1m, highs_1m, vols_1m = [], [], []

                            sig = calc_intraday_signals_from_series(
                                price=float(q.price),
                                closes=closes_1m,
                                highs=highs_1m,
                                vols=vols_1m,
                                vwap=vwap,
                            )
                            intraday_by_symbol[q.symbol] = sig
                            _LATEST_INTRADAY_SIGNALS[q.symbol] = sig
                            # このループの entry（新仕様）を確定して保存
                            current_entry_by_symbol[q.symbol] = calculate_entry(q)

                            # 3) 直近5分高値ブレイク（必須）
                            if sig.recent_5m_high is None:
                                reasons.append("直近5分高値が取れない")
                            else:
                                if float(q.price) <= float(sig.recent_5m_high):
                                    reasons.append("5分高値ブレイク未成立")

                            # 4) 上昇傾向（必須）
                            if sig.price_5min_ago is None:
                                reasons.append("5分前価格が取れない")
                            else:
                                if float(q.price) <= float(sig.price_5min_ago):
                                    reasons.append("上昇傾向なし")

                            # 5) 出来高増加（加点）
                            # 仕様変更: 出来高増加を必須化
                            if sig.vol_3m_gt_prev_3m is not True:
                                reasons.append("出来高増加なし")
                            else:
                                # 必須を満たした上で、スコアとしても +1（将来の並び替え等に使える）
                                score_by_symbol[q.symbol] = score_by_symbol.get(q.symbol, 0) + 1

                            # 追加条件: Entry候補（新仕様: 直近5分高値ベース）への接近
                            # entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER
                            entry_calc = calc_entry_from_signals(sig)
                            if entry_calc is None:
                                reasons.append("Entry計算不可")
                            else:
                                if float(q.price) < (float(entry_calc) * float(ENTRY_NEAR_RATIO)):
                                    reasons.append("Entry候補から遠い")

                            # Entry上抜け（breakout）判定（最終仕様・シンプル版）:
                            # - まずは「price >= entry」で突破とします。
                            # - ただし、同じ銘柄で連続通知しないために breakout_state を使います。
                            #
                            # 状態:
                            # - breakout_state=False: まだentry未突破
                            # - breakout_state=True : entry突破済み
                            entry_now = current_entry_by_symbol.get(q.symbol)
                            if entry_now is None:
                                entry_cross_by_symbol[q.symbol] = False
                            else:
                                st = bool(breakout_state_by_symbol.get(q.symbol, False))
                                # entry が大きく変わったら state をリセット（新しいentry候補に追従する）
                                last_entry = last_breakout_entry_by_symbol.get(q.symbol)
                                if st and last_entry is not None and float(last_entry) > 0:
                                    diff_pct = (abs(float(entry_now) - float(last_entry)) / float(last_entry)) * 100.0
                                    if diff_pct >= float(BREAKOUT_ENTRY_RESET_PCT):
                                        breakout_state_by_symbol[q.symbol] = False
                                        st = False
                                        last_breakout_entry_by_symbol.pop(q.symbol, None)
                                if float(q.price) >= float(entry_now) and st is False:
                                    # 初回突破 → 🚀通知対象
                                    entry_cross_by_symbol[q.symbol] = True
                                    breakout_state_by_symbol[q.symbol] = True
                                    last_breakout_entry_by_symbol[q.symbol] = float(entry_now)
                                else:
                                    entry_cross_by_symbol[q.symbol] = False
                                # entry を下回ったら未突破へ戻す（次の突破でまた通知できる）
                                if float(q.price) < float(entry_now):
                                    breakout_state_by_symbol[q.symbol] = False
                                    last_breakout_entry_by_symbol.pop(q.symbol, None)
                    # vwap が None のままでも理由は追加しない（通過扱い）
                    if vwap is None:
                        vwap_by_symbol[q.symbol] = None

                    # 最終判定: reasons が空なら条件一致
                    if not reasons:
                        candidates.append(q)
                    else:
                        skip_reasons_by_symbol[q.symbol] = reasons

                # -----------------------------
                # REJECT理由の累積集計（ユーザー要望）
                # - signal生成(=crossed)の有無に関係なく、候補評価で落ちた理由をカウントします
                # -----------------------------
                try:
                    for rs in (skip_reasons_by_symbol.values() or []):
                        for r in (rs or []):
                            rr = str(r or "").strip()
                            if not rr:
                                continue
                            reject_reason_counts[rr] = int(reject_reason_counts.get(rr, 0)) + 1
                except Exception:
                    pass

                # 出力: 条件に合う銘柄だけ表示
                candidate_symbols = {q.symbol for q in candidates}
                should_print = (not only_changes) or (candidate_symbols != last_candidates)
                if should_print:
                    if candidates:
                        print(f"\n[{now_str()}] 条件一致: {len(candidates)} 銘柄")
                        # 見やすいように、前日比%が大きい順に並べます
                        for q in sorted(candidates, key=lambda x: x.change_percent or -999, reverse=True):
                            v = "N/A" if q.volume is None else str(int(q.volume))
                            ratio = (q.price / q.day_high) if q.day_high else 0.0
                            mt = q.market_time_utc.isoformat() if q.market_time_utc else "N/A"
                            ma25 = ma25_by_symbol.get(q.symbol)
                            ma25_s = _fmt_price(ma25) if ma25 is not None else "N/A"
                            avg5 = avg5_by_symbol.get(q.symbol)
                            avg5_s = _fmt_volume(avg5) if avg5 is not None else "N/A"
                            vol_spike_ratio = vol_spike_ratio_by_symbol.get(q.symbol)
                            vol_spike_ratio_s = "N/A" if vol_spike_ratio is None else f"{vol_spike_ratio:.2f}x"
                            vwap = vwap_by_symbol.get(q.symbol)
                            vwap_s = _fmt_price(vwap) if vwap is not None else "N/A"
                            mcap_s = "取得不可" if q.market_cap is None else f"{int(round(q.market_cap)):,}"
                            score = score_by_symbol.get(q.symbol, 0)
                            prev_s = _fmt_price(q.previous_close)
                            chg_s = "N/A" if q.change_percent is None else f"{q.change_percent:.2f}%"
                            print(
                                f"  - {q.symbol}: "
                                f"price={_fmt_price(q.price)} {q.currency}, "
                                f"prevClose={prev_s}, "
                                f"chg%={chg_s}, "
                                f"day_high={q.day_high} (ratio={ratio*100:.2f}%), "
                                f"vol={v}, vol_avg5={avg5_s}, spike={vol_spike_ratio_s}, "
                                f"score={score}, "
                                f"vwap={vwap_s}, mcap={mcap_s}, ma25={ma25_s}, time_utc={mt}"
                            )
                        print()
                    else:
                        print(f"[{now_str()}] 条件一致: 0 銘柄")

                    # 見送り理由も表示（指定された理由を出す）
                    # 既存の --print-all の概念は残しつつ、
                    # 追加要件に合わせて「見送り理由」を表示します。
                    for q in sorted(quotes, key=lambda x: x.symbol):
                        reasons = skip_reasons_by_symbol.get(q.symbol)
                        if not reasons:
                            continue
                        prev_s = _fmt_price(q.previous_close)
                        chg_s = "N/A" if q.change_percent is None else f"{q.change_percent:.2f}%"
                        print(
                            f"  - {q.symbol}: 見送り（{' / '.join(reasons)}）"
                            f" / price={_fmt_price(q.price)} prevClose={prev_s} chg%={chg_s}"
                        )

                last_candidates = candidate_symbols

                # ※ prev_entry_snapshot は一旦使わない方針なので、prev_entry_by_symbol の更新も停止します。

                # Discord通知:
                # candidates の中から「前回ループで候補に入っていなかった銘柄」だけ送ります。
                # これにより、同じ銘柄が条件一致し続けても毎秒スパム通知されません。
                if discord_enabled:
                    # candidates は list[Quote] なので、symbol 重複が無い前提で扱います（WATCH は通常ユニーク）。
                    to_notify = [q for q in candidates if q.symbol not in last_discord_candidate_symbols]

                    # -----------------------------
                    # 1) 条件一致通知（新規だけ）
                    # -----------------------------
                    if to_notify:
                        # 見やすさのため、前日比が大きい順に送ります。
                        to_notify_sorted = sorted(to_notify, key=lambda x: x.change_percent or -999, reverse=True)
                        for q in to_notify_sorted:
                            # Entry候補は「直近5分高値ベース」で計算します（新仕様）。
                            sig = intraday_by_symbol.get(q.symbol)
                            entry_calc = calc_entry_from_signals(sig)
                            if entry_calc is None:
                                # ここまで来る時点で recent_5m_high はあるはずですが、念のためガード
                                continue
                            entry = float(entry_calc)
                            stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                            take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                            try:
                                ma25 = ma25_by_symbol.get(q.symbol)
                                if ma25 is None:
                                    continue
                                vol_avg5 = avg5_by_symbol.get(q.symbol)
                                vol_spike_ratio = vol_spike_ratio_by_symbol.get(q.symbol)
                                vwap = vwap_by_symbol.get(q.symbol)
                                market_cap = q.market_cap

                                msg = _build_discord_message(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    ma25=float(ma25),
                                    vol_avg5=vol_avg5,
                                    vol_spike_ratio=vol_spike_ratio,
                                    vwap=vwap,
                                    market_cap=(float(market_cap) if market_cap is not None else None),
                                )
                                # 通常モードの通知にも、今回追加した「エントリー判断に必要な情報」を載せます。
                                crossed = bool(entry_cross_by_symbol.get(q.symbol, False))
                                if sig:
                                    # 通常の条件一致Embed（🟢）か、Entry上抜けEmbed（🚀）かを切り替えます。
                                    if crossed:
                                        embed = build_embed_entry_cross(
                                            q,
                                            entry=entry,
                                            stop=stop,
                                            take=take,
                                            vwap=sig.vwap,
                                            ma25=float(ma25),
                                            replay_time_jst=None,
                                            recent_5m_high=sig.recent_5m_high,
                                            price_5min_ago=sig.price_5min_ago,
                                            vwap_distance_pct=sig.vwap_distance_pct,
                                            vol_increase=sig.vol_3m_gt_prev_3m,
                                            entry_crossed=True,
                                        )
                                        msg = {"embeds": [embed]}
                                    else:
                                        msg["embeds"][0]["fields"] = [
                                            _embed_field("現在値", _fmt_yen(q.price), inline=True),
                                            _embed_field("前日比", _fmt_pct(q.change_percent), inline=True),
                                            _embed_field("出来高", _fmt_volume_man(q.volume), inline=True),
                                            _embed_field("高値接近率", _fmt_ratio_pct(q.price, q.day_high), inline=True),
                                            _embed_field("直近5分高値", _fmt_yen(sig.recent_5m_high), inline=True),
                                            _embed_field("5分前価格", _fmt_yen(sig.price_5min_ago), inline=True),
                                            _embed_field("VWAP乖離率", ("N/A" if sig.vwap_distance_pct is None else f"{sig.vwap_distance_pct:.2f}%"), inline=True),
                                            _embed_field("出来高増加", ("N/A" if sig.vol_3m_gt_prev_3m is None else ("あり" if sig.vol_3m_gt_prev_3m else "なし")), inline=True),
                                            _embed_field("Entry接近率", f"{(float(q.price)/float(entry))*100.0:.2f}%", inline=True),
                                            _embed_field("Entry上抜け", ("成立" if crossed else "未成立"), inline=True),
                                            _embed_field("売買候補", "\n".join([f"Entry: {_fmt_yen(entry)}", f"Stop: {_fmt_yen(stop)}", f"Take: {_fmt_yen(take)}"]), inline=False),
                                            _embed_field("補足", "\n".join([f"VWAP: {_fmt_yen(sig.vwap)}", f"25MA: {_fmt_yen(float(ma25))}"]), inline=False),
                                        ]
                                discord_notify(
                                    msg,
                                    webhook_url=webhook_url,
                                    alert_channel_id=alert_channel_id,
                                    bot_token=bot_token,
                                )

                                # 新規条件一致通知を出した時点の候補価格を覚えておきます。
                                last_notified_levels[q.symbol] = (float(entry), float(stop), float(take))
                            except Exception as e:
                                print(f"[{now_str()}] Discord通知失敗: {q.symbol} ({e})")

                    # -----------------------------
                    # 2) 条件外れ通知
                    # -----------------------------
                    # 前回は候補だったが、今回は候補ではない銘柄に対して通知します。
                    out_symbols = sorted(last_discord_candidate_symbols - candidate_symbols)
                    for sym in out_symbols:
                        try:
                            exit_miss_count[sym] = int(exit_miss_count.get(sym, 0)) + 1
                            if exit_miss_count[sym] < int(EXIT_CONFIRM_COUNT):
                                continue
                            last_q = last_quote_by_symbol.get(sym)
                            embed_out = build_embed_out(
                                symbol=sym,
                                price=(last_q.price if last_q else None),
                                change_percent=(last_q.change_percent if last_q else None),
                                reasons=skip_reasons_by_symbol.get(sym, []),
                            )
                            msg_out = {"embeds": [embed_out]}
                            discord_notify(
                                msg_out,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            exit_miss_count.pop(sym, None)
                            last_discord_candidate_symbols.discard(sym)
                            # 条件外れが確定したら breakout_state もリセットします（仕様）。
                            breakout_state_by_symbol[sym] = False
                            last_breakout_entry_by_symbol.pop(sym, None)
                        except Exception as e:
                            print(f"[{now_str()}] Discord条件外れ通知失敗: {sym} ({e})")

                    # 候補に戻った銘柄はカウントをリセット
                    for sym in candidate_symbols:
                        exit_miss_count.pop(sym, None)

                    # -----------------------------
                    # 3) 候補価格の大幅変更通知（再通知）
                    # -----------------------------
                    # 条件一致している銘柄だけ対象（candidates の中だけ）
                    candidates_by_symbol = {q.symbol: q for q in candidates}
                    new_notified_symbols = {qq.symbol for qq in to_notify}
                    for sym, (old_entry, old_stop, old_take) in list(last_notified_levels.items()):
                        q = candidates_by_symbol.get(sym)
                        if q is None:
                            continue
                        if sym in new_notified_symbols:
                            continue
                        new_entry_calc = calculate_entry(q)
                        if new_entry_calc is None:
                            continue
                        new_entry = float(new_entry_calc)
                        new_stop = new_entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        new_take = new_entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        changed = (
                            _level_changed(old=old_entry, new=new_entry)
                            or _level_changed(old=old_stop, new=new_stop)
                            or _level_changed(old=old_take, new=new_take)
                        )
                        if not changed:
                            continue
                        try:
                            msg2 = _build_levels_change_message(
                                symbol=q.symbol,
                                price=float(q.price),
                                currency=str(q.currency),
                                change_percent=q.change_percent,
                                old_entry=float(old_entry),
                                new_entry=float(new_entry),
                                old_stop=float(old_stop),
                                new_stop=float(new_stop),
                                old_take=float(old_take),
                                new_take=float(new_take),
                            )
                            discord_notify(
                                msg2,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(new_entry), float(new_stop), float(new_take))
                        except Exception as e:
                            print(f"[{now_str()}] Discord再通知失敗: {q.symbol} ({e})")

                # どの銘柄を「前回候補」とみなすかを更新します。
                # これで「同じ銘柄を連続通知しない」を満たします。
                if discord_enabled:
                    last_discord_candidate_symbols = candidate_symbols

                # 「1秒ごと」に近づけるために、処理時間を差し引いて sleep します。
                elapsed = time.perf_counter() - loop_started
                sleep_sec = interval_sec - elapsed
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
                else:
                    # APIが遅い / ネットが遅いと、処理が1秒を超えることがあります。
                    # その場合は待たずに次のループへ進みます（間隔はズレます）。
                    pass

        except KeyboardInterrupt:
            print("\nCtrl+C を検知しました。終了します。")
            return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


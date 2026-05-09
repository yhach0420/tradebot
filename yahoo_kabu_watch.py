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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 固定プールのランダムReplay（平日カレンダーを候補→probeで実データがある日だけ採用）
FIXED_REPLAY_RANDOM_POOLS: dict[str, tuple[date, date]] = {
    "random_60d": (date(2026, 2, 1), date(2026, 4, 30)),
    "random_feb": (date(2026, 2, 1), date(2026, 2, 28)),
    "random_mar": (date(2026, 3, 1), date(2026, 3, 31)),
    # cache_only: Yahoo 1m の保持期間外でも「キャッシュがある日だけ」で検証できるようにする
    "random_mar_cache_only": (date(2026, 3, 1), date(2026, 3, 31)),
    "random_apr": (date(2026, 4, 1), date(2026, 4, 30)),
}
FIXED_RANDOM_REPLAY_LABELS: frozenset[str] = frozenset(FIXED_REPLAY_RANDOM_POOLS.keys())

# AB / sweep: 同一データセット内でフィルタ差分比較を優先するため `random_apr` のみ使用。
# `random_60d` のプール（例: 2/1〜4/30）は 4 月と重複が大きく、Apr と並べても独立検証になりにくい。
SWEEP_REPLAY_RANGES: tuple[str, ...] = ("random_apr",)

# Paper trade 候補の Replay config（暫定・検証済みルールのみ。フィルタ追加は当面しない）
PAPER_TRADE_REPLAY_CONFIG_FILENAMES: tuple[str, ...] = (
    "replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json",
)


def _normalize_regime_control_profiles_from_cfg(
    regime_controls: Optional[dict[str, Any]],
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """config の regime_controls.enabled と各 STATE 別パラメータを正規化。"""
    if not isinstance(regime_controls, dict) or not bool(regime_controls.get("enabled", False)):
        return False, {}
    out: dict[str, dict[str, Any]] = {}
    for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
        raw = regime_controls.get(rk)
        if not isinstance(raw, dict):
            continue
        max_gap = raw.get("max_gap_pct")
        max_vdw = raw.get("max_vwap_distance_pct")
        xm = raw.get("exit_mode", "normal")
        exit_mode = str(xm).strip().lower() if xm is not None else "normal"
        if exit_mode not in ("normal", "fast"):
            exit_mode = "normal"
        out[rk] = {
            "entry_enabled": bool(raw.get("entry_enabled", True)),
            "max_gap_pct": float(max_gap) if isinstance(max_gap, (int, float)) else None,
            "max_vwap_distance_pct": float(max_vdw) if isinstance(max_vdw, (int, float)) else None,
            "exit_mode": str(exit_mode),
        }
    return True, out


def _regime_control_profile_for(
    regime_control_profiles: dict[str, dict[str, Any]], market_regime: str
) -> dict[str, Any]:
    rk = str(market_regime or "").strip().upper()
    if not rk:
        rk = "NORMAL"
    p = regime_control_profiles.get(rk)
    if isinstance(p, dict):
        return dict(p)
    return {
        "entry_enabled": True,
        "max_gap_pct": None,
        "max_vwap_distance_pct": None,
        "exit_mode": "normal",
    }


def _replay_signal_early_exit_kw(
    s: Any,
    *,
    replay_early_exit_before_stop: bool,
    replay_early_exit_vwap: bool,
    replay_early_exit_recent_low: bool,
) -> tuple[bool, bool, bool]:
    """regime_controls 由来の per-signal early exit 上書き（無ければ run 全体の既定）。"""
    ebp = bool(replay_early_exit_before_stop)
    ev = getattr(s, "regime_early_exit_vwap", None)
    er = getattr(s, "regime_early_exit_recent_low", None)
    return (
        ebp,
        bool(replay_early_exit_vwap) if ev is None else bool(ev),
        bool(replay_early_exit_recent_low) if er is None else bool(er),
    )


def _replay_fixed_random_pool_dates(replay_range: str) -> Optional[tuple[date, date]]:
    return FIXED_REPLAY_RANDOM_POOLS.get(str(replay_range).strip())


def _replay_fixed_random_weekday_candidate_count(replay_range: str) -> int:
    p = _replay_fixed_random_pool_dates(replay_range)
    if not p:
        return 0
    return len(_weekday_date_strings_between(p[0], p[1]))


def _replay_fixed_random_meta_extra(replay_range: str) -> dict[str, Any]:
    """random_60d / random_feb 等の meta replay_settings 用・report meta 用フィールド。"""
    p = _replay_fixed_random_pool_dates(replay_range)
    if not p:
        return {}
    return {
        "replay_date_pool_start": p[0].strftime("%Y-%m-%d"),
        "replay_date_pool_end": p[1].strftime("%Y-%m-%d"),
        "replay_candidate_days_count": len(_weekday_date_strings_between(p[0], p[1])),
    }


def _weekday_date_strings_between(start_d: date, end_d: date) -> list[str]:
    """start_d〜end_d の間の月〜金のカレンダー日を YYYY-MM-DD で列挙（祝日は含む・後段probeで除外）。"""
    out: list[str] = []
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d = d + timedelta(days=1)
    return out


# Yahoo Finance chart interval=1m は実質「直近約30日」の intraday のみ返すことが多く、
# それより古い period1/period2（unix）では HTTP 422 や chart.error になる。
YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS = 30


def _yahoo_1m_available_calendar_bounds_jst(today_jst: date, *, history_days: int = YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS) -> tuple[date, date]:
    """1m が取得できるとみなす暦日の範囲 [earliest, latest]（JST・目安）。"""
    hi = today_jst
    lo = today_jst - timedelta(days=max(1, int(history_days)))
    return (lo, hi)


def _replay_morning_only_template() -> dict[str, Any]:
    """replay_morning_only 系プリセットの共通ベース（朝のみ・早期Exit等）。"""
    return {
        "name": "replay_morning_only",
        "early_exit": True,
        "vwap_break_exit": True,
        "recent_5m_low_break_exit": True,
        "strict_afternoon": True,
        "topix_weak_block": True,
        "disable_afternoon_entry": True,
        "entry_filters": {
            "rsi": {"enabled": False, "exclude_above": 75.0},
            "vwap_distance_pct": {"enabled": False, "exclude_above": 2.0},
            "atr_pct": {"enabled": False, "exclude_above": 4.0},
        },
    }


def _default_replay_configs_dicts() -> dict[str, dict[str, Any]]:
    """
    Replay比較用configの初期値（要件どおり）。
    キーはファイル名（configs/ 配下）。
    """
    mo = _replay_morning_only_template()
    vwap2_base = {
        **dict(mo),
        "name": "replay_morning_vwap2",
        "entry_filters": {
            "rsi": {"enabled": False, "exclude_above": 75.0},
            "vwap_distance_pct": {"enabled": True, "exclude_above": 2.0},
            "atr_pct": {"enabled": False, "exclude_above": 4.0},
        },
        # baseline: daily_loss_stop は OFF（比較基準）
        "risk_controls": {
            "daily_loss_stop": {
                "enabled": False,
                "stop_yen_100_shares": 50_000,
            }
        },
    }
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
        "replay_morning_only.json": dict(mo),
        "replay_morning_rsi75.json": {
            **dict(mo),
            "name": "replay_morning_rsi75",
            "entry_filters": {
                "rsi": {"enabled": True, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": False, "exclude_above": 2.0},
                "atr_pct": {"enabled": False, "exclude_above": 4.0},
            },
        },
        "replay_morning_vwap2.json": dict(vwap2_base),
        "replay_morning_vwap2_dd30k.json": {
            **dict(vwap2_base),
            "name": "replay_morning_vwap2_dd30k",
            "risk_controls": {"daily_loss_stop": {"enabled": True, "stop_yen_100_shares": 30_000}},
        },
        "replay_morning_vwap2_dd50k.json": {
            **dict(vwap2_base),
            "name": "replay_morning_vwap2_dd50k",
            "risk_controls": {"daily_loss_stop": {"enabled": True, "stop_yen_100_shares": 50_000}},
        },
        "replay_morning_vwap2_dd70k.json": {
            **dict(vwap2_base),
            "name": "replay_morning_vwap2_dd70k",
            "risk_controls": {"daily_loss_stop": {"enabled": True, "stop_yen_100_shares": 70_000}},
        },
        "replay_morning_vwap15.json": {
            **dict(mo),
            "name": "replay_morning_vwap15",
            "entry_filters": {
                "rsi": {"enabled": False, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": True, "exclude_above": 1.5},
                "atr_pct": {"enabled": False, "exclude_above": 4.0},
            },
        },
        "replay_morning_vwap20.json": {
            **dict(mo),
            "name": "replay_morning_vwap20",
            "entry_filters": {
                "rsi": {"enabled": False, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": True, "exclude_above": 2.0},
                "atr_pct": {"enabled": False, "exclude_above": 4.0},
            },
        },
        "replay_morning_vwap25.json": {
            **dict(mo),
            "name": "replay_morning_vwap25",
            "entry_filters": {
                "rsi": {"enabled": False, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": True, "exclude_above": 2.5},
                "atr_pct": {"enabled": False, "exclude_above": 4.0},
            },
        },
        "replay_morning_vwap30.json": {
            **dict(mo),
            "name": "replay_morning_vwap30",
            "entry_filters": {
                "rsi": {"enabled": False, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": True, "exclude_above": 3.0},
                "atr_pct": {"enabled": False, "exclude_above": 4.0},
            },
        },
        "replay_morning_atr4.json": {
            **dict(mo),
            "name": "replay_morning_atr4",
            "entry_filters": {
                "rsi": {"enabled": False, "exclude_above": 75.0},
                "vwap_distance_pct": {"enabled": False, "exclude_above": 2.0},
                "atr_pct": {"enabled": True, "exclude_above": 4.0},
            },
        },
    }


def _ensure_replay_configs_exist() -> str:
    """
    要件:
    - configs/ フォルダを自動作成
    - 起動時に存在しない config は自動生成（replay実行時に呼ぶ）
    - config未指定時のデフォルトは configs/replay_morning_vwap2.json（baseline候補）
    戻り値: デフォルトconfigの絶対パス（configs/replay_morning_vwap2.json）
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
    return os.path.join(cfg_dir, "replay_morning_vwap2.json")


def _resolve_replay_config_path(path: str) -> str:
    """
    configパスを実ファイルへ解決します。
    - 相対パスは「カレントディレクトリ依存」を避けるため、スクリプト直下を基準にします。
    """
    p = str(path or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(script_dir, p))


def _merge_preset_config_defaults(cfg: dict[str, Any], *, resolved_path: str) -> dict[str, Any]:
    """
    既知プリセット（replay_default.json 等）のデフォルトとマージします。
    - ファイルが欠けているキーは preset で補完（古い/手編集で欠けたキーを救う）
    - ユーザーJSONの明示キーは優先（上書き）
    """
    if not isinstance(cfg, dict):
        cfg = {}
    base = os.path.basename(str(resolved_path or "").replace("\\", "/"))
    presets = _default_replay_configs_dicts()
    tmpl = presets.get(base)
    if not isinstance(tmpl, dict):
        return dict(cfg)
    merged: dict[str, Any] = dict(tmpl)
    for k, v in cfg.items():
        if k == "_path":
            continue
        if k == "afternoon_strict" and isinstance(v, dict) and isinstance(merged.get("afternoon_strict"), dict):
            inner = dict(merged["afternoon_strict"])
            inner.update(v)
            merged["afternoon_strict"] = inner
        elif k == "entry_filters" and isinstance(v, dict) and isinstance(merged.get("entry_filters"), dict):
            inner_merged = dict(merged["entry_filters"])
            for ek, ev in v.items():
                if isinstance(ev, dict) and isinstance(inner_merged.get(ek), dict):
                    sub = dict(inner_merged[ek])
                    sub.update(ev)
                    inner_merged[ek] = sub
                else:
                    inner_merged[ek] = ev
            merged["entry_filters"] = inner_merged
        else:
            merged[k] = v
    merged["_path"] = cfg.get("_path") or resolved_path
    return merged


def _load_replay_config(path: str) -> dict[str, Any]:
    """
    Replay戦略条件のconfig JSONを読み込みます。
    - 失敗したら {} を返す（CLIだけでも動くように）
    """
    p = _resolve_replay_config_path(path)
    if not p:
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data["_path"] = p
            merged = _merge_preset_config_defaults(data, resolved_path=p)
            return merged
        return {}
    except Exception as e:
        # now_str はこの時点では未定義の可能性があるため、単純にprintする
        print(f"Replay configの読み込みに失敗: {p} ({type(e).__name__}: {e})")
        base = os.path.basename(str(p).replace("\\", "/"))
        presets = _default_replay_configs_dicts()
        if base in presets:
            print(
                f"[WARNING] 埋め込みプリセットで代替します: {base} "
                f"(ファイルを修正するか削除して自動生成してください)"
            )
            fb = dict(presets[base])
            fb["_path"] = p
            return fb
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

    ef = cfg.get("entry_filters") if isinstance(cfg.get("entry_filters"), dict) else {}

    def _entry_filter_thr(key: str, default_exclude_above: float) -> tuple[bool, float]:
        sub = ef.get(key)
        if not isinstance(sub, dict):
            return False, float(default_exclude_above)
        en = bool(sub.get("enabled", False))
        thr = float(sub.get("exclude_above", default_exclude_above))
        return en, thr

    rsi_en, rsi_thr = _entry_filter_thr("rsi", 75.0)
    vwap_en, vwap_thr = _entry_filter_thr("vwap_distance_pct", 2.0)
    atr_en, atr_thr = _entry_filter_thr("atr_pct", 4.0)

    # risk controls（任意）
    rc = cfg.get("risk_controls") if isinstance(cfg.get("risk_controls"), dict) else {}
    dls = rc.get("daily_loss_stop") if isinstance(rc, dict) and isinstance(rc.get("daily_loss_stop"), dict) else {}
    daily_loss_stop_enabled = bool(dls.get("enabled", False)) if isinstance(dls, dict) else False
    daily_loss_stop_threshold = float(dls.get("stop_yen_100_shares", 50_000.0)) if isinstance(dls, dict) else 50_000.0

    # regime filters（任意）
    rf = cfg.get("regime_filters") if isinstance(cfg.get("regime_filters"), dict) else {}
    disable_morning_weak = bool(rf.get("disable_morning_weak", False)) if isinstance(rf, dict) else False
    disable_rising_ratio_lt50 = bool(rf.get("disable_rising_ratio_lt50", False)) if isinstance(rf, dict) else False
    disable_topix_weak = bool(rf.get("disable_topix_weak", False)) if isinstance(rf, dict) else False
    topix_weak_threshold_pct = (
        float(rf.get("topix_weak_threshold_pct")) if isinstance(rf, dict) and isinstance(rf.get("topix_weak_threshold_pct"), (int, float)) else None
    )

    # signal filters（任意）
    sf = cfg.get("signal_filters") if isinstance(cfg.get("signal_filters"), dict) else {}
    disable_gap_ge_pct = bool(sf.get("disable_gap_ge_pct", False)) if isinstance(sf, dict) else False
    gap_ge_threshold_pct = float(sf.get("gap_ge_threshold_pct", 3.0)) if isinstance(sf, dict) else 3.0
    disable_vwap_distance_ge_pct = bool(sf.get("disable_vwap_distance_ge_pct", False)) if isinstance(sf, dict) else False
    vwap_distance_ge_threshold_pct = float(sf.get("vwap_distance_ge_threshold_pct", 1.5)) if isinstance(sf, dict) else 1.5
    disable_entry_after_hhmm = bool(sf.get("disable_entry_after_hhmm", False)) if isinstance(sf, dict) else False
    entry_after_hhmm = str(sf.get("entry_after_hhmm", "10:30")) if isinstance(sf, dict) else "10:30"

    # composite signal filters（WEAK時のみ gap / VWAP距離 で除外）
    csf = cfg.get("composite_signal_filters") if isinstance(cfg.get("composite_signal_filters"), dict) else {}
    disable_weak_vwap_ge = bool(csf.get("disable_state_weak_and_vwap_ge_pct", False)) if isinstance(csf, dict) else False
    weak_vwap_ge_thr = float(csf.get("state_weak_vwap_ge_threshold_pct", 1.5)) if isinstance(csf, dict) else 1.5
    disable_weak_gap_ge = bool(csf.get("disable_state_weak_and_gap_ge_pct", False)) if isinstance(csf, dict) else False
    weak_gap_ge_thr = float(csf.get("state_weak_gap_ge_threshold_pct", 3.0)) if isinstance(csf, dict) else 3.0
    weak_risk_filter = ""
    strong_risk_filter = ""
    strong_vwap_ge_thr = 1.5
    sc_enabled = False
    sc_conditions: list[dict[str, Any]] = []
    sc_snap: dict[str, Any] = {}
    if isinstance(csf, dict):
        wrf0 = csf.get("weak_risk_filter")
        if isinstance(wrf0, str):
            s_wrf = wrf0.strip()
            if s_wrf in (
                "weak_vwap_ge_15_only",
                "weak_gap_ge_3_only",
                "weak_vwap_ge_15_and_gap_ge_3",
            ):
                weak_risk_filter = s_wrf
        srf0 = csf.get("strong_risk_filter")
        if isinstance(srf0, str):
            s_srf = srf0.strip()
            if s_srf in ("strong_vwap_ge_15_only", "strong_vwap_ge_12_only", "strong_vwap_ge_10_only"):
                strong_risk_filter = s_srf
        if strong_risk_filter == "strong_vwap_ge_15_only":
            strong_vwap_ge_thr = 1.5
        elif strong_risk_filter == "strong_vwap_ge_12_only":
            strong_vwap_ge_thr = 1.2
        elif strong_risk_filter == "strong_vwap_ge_10_only":
            strong_vwap_ge_thr = 1.0
        if isinstance(csf.get("strong_vwap_ge_threshold_pct"), (int, float)):
            strong_vwap_ge_thr = float(csf.get("strong_vwap_ge_threshold_pct"))
        sc_enabled, sc_conditions, sc_snap = _normalize_strong_combo_filter_from_csf(csf)

    rc_root = cfg.get("regime_controls") if isinstance(cfg.get("regime_controls"), dict) else {}
    regime_control_enabled = False
    regime_control_profiles: dict[str, dict[str, Any]] = {}
    regime_control_snapshot: dict[str, Any] = {}
    if isinstance(rc_root, dict):
        regime_control_snapshot = dict(rc_root)
        regime_control_enabled, regime_control_profiles = _normalize_regime_control_profiles_from_cfg(rc_root)

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
        "entry_filter_rsi_enabled": bool(rsi_en),
        "entry_filter_rsi_exclude_above": float(rsi_thr),
        "entry_filter_vwap_distance_enabled": bool(vwap_en),
        "entry_filter_vwap_distance_exclude_above": float(vwap_thr),
        "entry_filter_atr_pct_enabled": bool(atr_en),
        "entry_filter_atr_pct_exclude_above": float(atr_thr),
        "daily_loss_stop_enabled": bool(daily_loss_stop_enabled),
        "daily_loss_stop_threshold_yen_100_shares": float(daily_loss_stop_threshold),
        "regime_filter_disable_morning_weak": bool(disable_morning_weak),
        "regime_filter_disable_rising_ratio_lt50": bool(disable_rising_ratio_lt50),
        "regime_filter_disable_topix_weak": bool(disable_topix_weak),
        "regime_filter_topix_weak_threshold_pct": topix_weak_threshold_pct,
        "signal_filter_disable_gap_ge_pct": bool(disable_gap_ge_pct),
        "signal_filter_gap_ge_threshold_pct": float(gap_ge_threshold_pct),
        "signal_filter_disable_vwap_distance_ge_pct": bool(disable_vwap_distance_ge_pct),
        "signal_filter_vwap_distance_ge_threshold_pct": float(vwap_distance_ge_threshold_pct),
        "signal_filter_disable_entry_after_hhmm": bool(disable_entry_after_hhmm),
        "signal_filter_entry_after_hhmm": str(entry_after_hhmm),
        "composite_signal_filter_disable_weak_vwap_ge": bool(disable_weak_vwap_ge),
        "composite_signal_filter_weak_vwap_ge_threshold_pct": float(weak_vwap_ge_thr),
        "composite_signal_filter_disable_weak_gap_ge": bool(disable_weak_gap_ge),
        "composite_signal_filter_weak_gap_ge_threshold_pct": float(weak_gap_ge_thr),
        "composite_signal_filter_weak_risk_filter": str(weak_risk_filter),
        "composite_signal_filter_strong_risk_filter": str(strong_risk_filter),
        "composite_signal_filter_strong_vwap_ge_threshold_pct": float(strong_vwap_ge_thr),
        "composite_signal_filter_strong_combo_enabled": bool(sc_enabled),
        "composite_signal_filter_strong_combo_block_conditions": list(sc_conditions),
        "composite_signal_filter_strong_combo_snapshot": dict(sc_snap),
        "regime_control_enabled": bool(regime_control_enabled),
        "regime_control_profiles": dict(regime_control_profiles),
        "regime_control_snapshot": regime_control_snapshot,
    }


def _replay_composite_signal_filter_kwargs_from_flags(cfg_flags: dict[str, Any]) -> dict[str, Any]:
    """run_replay へ渡す composite_signal_filters 系 kwargs。"""
    return {
        "composite_signal_filter_disable_weak_vwap_ge": bool(cfg_flags.get("composite_signal_filter_disable_weak_vwap_ge", False)),
        "composite_signal_filter_weak_vwap_ge_threshold_pct": float(
            cfg_flags.get("composite_signal_filter_weak_vwap_ge_threshold_pct", 1.5)
        ),
        "composite_signal_filter_disable_weak_gap_ge": bool(cfg_flags.get("composite_signal_filter_disable_weak_gap_ge", False)),
        "composite_signal_filter_weak_gap_ge_threshold_pct": float(
            cfg_flags.get("composite_signal_filter_weak_gap_ge_threshold_pct", 3.0)
        ),
        "composite_signal_filter_weak_risk_filter": str(cfg_flags.get("composite_signal_filter_weak_risk_filter") or ""),
        "composite_signal_filter_strong_risk_filter": str(cfg_flags.get("composite_signal_filter_strong_risk_filter") or ""),
        "composite_signal_filter_strong_vwap_ge_threshold_pct": float(
            cfg_flags.get("composite_signal_filter_strong_vwap_ge_threshold_pct", 1.5)
        ),
        "composite_signal_filter_strong_combo_enabled": bool(cfg_flags.get("composite_signal_filter_strong_combo_enabled", False)),
        "composite_signal_filter_strong_combo_block_conditions": list(
            cfg_flags.get("composite_signal_filter_strong_combo_block_conditions") or []
        ),
        "composite_signal_filter_strong_combo_snapshot": dict(cfg_flags.get("composite_signal_filter_strong_combo_snapshot") or {}),
    }


def _composite_weak_virtual_exclude_reason(exclude_reason: str) -> bool:
    """composite_signal_filters（WEAK/STRONG）由来の除外で仮想PnL追跡するか。"""
    if not isinstance(exclude_reason, str):
        return False
    if "COMPOSITE_" in exclude_reason:
        return True
    for tag in ("WEAK_VWAP_GE_15", "WEAK_GAP_GE_3", "WEAK_VWAP_AND_GAP", "STRONG_VWAP_GE"):
        if tag in exclude_reason:
            return True
    return False


def _strong_combo_virtual_exclude_reason(exclude_reason: str, known_reasons: frozenset[str]) -> bool:
    """strong_combo_filter 由来の除外で仮想PnL追跡するか（reason は設定どおり一致）。"""
    if not isinstance(exclude_reason, str) or not known_reasons:
        return False
    for part in exclude_reason.split(" / "):
        p = str(part).strip()
        if p and p in known_reasons:
            return True
    return False


def _normalize_strong_combo_filter_from_csf(csf: Any) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    """
    composite_signal_filters.strong_combo_filter を正規化。
    returns: enabled, block_conditions, snapshot（レポート用）
    """
    scf = csf.get("strong_combo_filter") if isinstance(csf, dict) and isinstance(csf.get("strong_combo_filter"), dict) else {}
    if not scf:
        return False, [], {}
    enabled = bool(scf.get("enabled", False))
    block_conditions: list[dict[str, Any]] = []
    raw_list = scf.get("block_conditions")
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            mr = str(item.get("market_regime") or "").strip().upper()
            hu_eq = item.get("high_update_count_before_entry_eq")
            hu_le = item.get("high_update_count_before_entry_le")
            vwap_ge = item.get("entry_vwap_distance_pct_ge")
            reason = str(item.get("reason") or "").strip() or "STRONG_COMBO"
            if mr not in ("STRONG", "NORMAL", "WEAK", "CRASH"):
                continue
            if not isinstance(vwap_ge, (int, float)):
                continue
            rec: dict[str, Any] = {
                "market_regime": mr,
                "entry_vwap_distance_pct_ge": float(vwap_ge),
                "reason": reason,
            }
            if isinstance(hu_eq, (int, float)):
                rec["high_update_count_before_entry_eq"] = int(hu_eq)
            elif isinstance(hu_le, (int, float)):
                rec["high_update_count_before_entry_le"] = int(hu_le)
            else:
                continue
            block_conditions.append(rec)
    snap = {"enabled": enabled, "block_conditions": list(block_conditions)}
    return enabled, block_conditions, snap


def _build_combo_filter_analysis_report_payload(
    *,
    enabled: bool,
    block_conditions_snapshot: list[dict[str, Any]],
    skipped_total: int,
    skip_reason_counts: dict[str, int],
    virtual_pnl_sum_total: float,
    virtual_count_total: int,
    virtual_pnl_by_reason: dict[str, float],
    virtual_count_by_reason: dict[str, int],
) -> dict[str, Any]:
    """combo_filter_analysis / composite 内 strong_combo 用の JSON。"""
    reasons = sorted(
        set(skip_reason_counts.keys()) | set(virtual_pnl_by_reason.keys()) | set(virtual_count_by_reason.keys())
    )
    by_reason: dict[str, Any] = {}
    for r in reasons:
        rr = str(r)
        sk = int(skip_reason_counts.get(rr, 0))
        vc = int(virtual_count_by_reason.get(rr, 0))
        vp = float(virtual_pnl_by_reason.get(rr, 0.0))
        exp = (float(vp) / float(vc)) if vc > 0 else 0.0
        by_reason[rr] = {
            "skipped_signals_count": int(sk),
            "virtual_resolved_count": int(vc),
            "total_pnl_yen_100_shares": float(vp),
            "avg_expectancy_yen_100_shares_if_skipped": float(exp),
            "prevented_loss_estimate_yen_100_shares": float(-vp),
        }
    return {
        "enabled": bool(enabled),
        "block_conditions": list(block_conditions_snapshot),
        "skipped_signals_count": int(skipped_total),
        "skip_reason_counts": dict(skip_reason_counts),
        "virtual_pnl_analysis": {
            "total_skipped_signals_count": int(skipped_total),
            "total_pnl_yen_100_shares": float(virtual_pnl_sum_total),
            "avg_expectancy_yen_100_shares_if_skipped": (
                float(virtual_pnl_sum_total / float(virtual_count_total)) if int(virtual_count_total) > 0 else 0.0
            ),
            "prevented_loss_estimate_yen_100_shares": float(-float(virtual_pnl_sum_total)),
            "by_reason": dict(by_reason),
        },
    }


def _combo_filter_analysis_dict_from_report(rep: Any) -> dict[str, Any]:
    """
    リプレイJSONの combo_filter_analysis ブロックを返す。
    保存形式は report 直下（正）と overall_summary 内（後方互換）の両方を受け付ける。
    """
    if not isinstance(rep, dict):
        return {}
    cfa = rep.get("combo_filter_analysis")
    if isinstance(cfa, dict):
        return dict(cfa)
    ov = rep.get("overall_summary")
    if isinstance(ov, dict):
        cfa2 = ov.get("combo_filter_analysis")
        if isinstance(cfa2, dict):
            return dict(cfa2)
    return {}


def _aggregate_combo_filter_analysis_from_run_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """複数 run の combo_filter_analysis（strong_combo）を合算。"""
    skipped_grand = 0
    skip_rc: dict[str, int] = {}
    vpn_sum = 0.0
    vcnt_sum = 0
    vpn_by: dict[str, float] = {}
    vcn_by: dict[str, int] = {}
    enabled_any = False
    snap_cond: list[dict[str, Any]] = []
    for rr in run_summaries:
        rep = rr.get("report") or {}
        cf = _combo_filter_analysis_dict_from_report(rep)
        sc = cf.get("strong_combo_filter") if isinstance(cf.get("strong_combo_filter"), dict) else {}
        if not sc:
            continue
        enabled_any = enabled_any or bool(sc.get("enabled", False))
        if isinstance(sc.get("block_conditions"), list) and sc.get("block_conditions"):
            snap_cond = list(sc.get("block_conditions") or [])
        skipped_grand += int(sc.get("skipped_signals_count") or 0)
        for k, v in (sc.get("skip_reason_counts") or {}).items():
            try:
                kk = str(k)
                if kk:
                    skip_rc[kk] = int(skip_rc.get(kk, 0)) + int(v or 0)
            except Exception:
                continue
        vpa = sc.get("virtual_pnl_analysis") if isinstance(sc.get("virtual_pnl_analysis"), dict) else {}
        vpn_sum += float(vpa.get("total_pnl_yen_100_shares") or 0.0)
        br = vpa.get("by_reason") if isinstance(vpa.get("by_reason"), dict) else {}
        for rk, row in br.items():
            if not isinstance(row, dict):
                continue
            ks = str(rk)
            vpn_by[ks] = float(vpn_by.get(ks, 0.0)) + float(row.get("total_pnl_yen_100_shares") or 0.0)
            vcn_by[ks] = int(vcn_by.get(ks, 0)) + int(row.get("virtual_resolved_count") or 0)
        try:
            vcnt_sum += int(
                sum(int(row.get("virtual_resolved_count") or 0) for row in br.values() if isinstance(row, dict))
            )
        except Exception:
            pass
    return {
        "strong_combo_filter": _build_combo_filter_analysis_report_payload(
            enabled=bool(enabled_any),
            block_conditions_snapshot=list(snap_cond),
            skipped_total=int(skipped_grand),
            skip_reason_counts=dict(skip_rc),
            virtual_pnl_sum_total=float(vpn_sum),
            virtual_count_total=int(vcnt_sum),
            virtual_pnl_by_reason=dict(vpn_by),
            virtual_count_by_reason=dict(vcn_by),
        )
    }


def _replay_regime_control_kwargs_from_flags(cfg_flags: dict[str, Any]) -> dict[str, Any]:
    """run_replay へ渡す regime_controls 系 kwargs（各 sweep / main で共通化）。"""
    prof = cfg_flags.get("regime_control_profiles")
    if not isinstance(prof, dict):
        prof = {}
    snap = cfg_flags.get("regime_control_snapshot")
    if not isinstance(snap, dict):
        snap = {}
    return {
        "regime_control_enabled": bool(cfg_flags.get("regime_control_enabled", False)),
        "regime_control_profiles": dict(prof),
        "regime_control_config_snapshot": dict(snap),
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


def _time_bucket_jst_strict(dt: Optional[Any]) -> Optional[str]:
    """
    time bucket expectancy analysis 用（JST・指定バケットのみ）。
    entry_datetime_jst = signal_time_utc(JST) を使う前提。
    """
    try:
        if dt is None or not isinstance(dt, datetime):
            return None
        t = dt
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        jst = t.astimezone(JST)
        hm = jst.hour * 60 + jst.minute
        buckets = [
            ("09:00-09:30", 9 * 60, 9 * 60 + 30),
            ("09:30-10:00", 9 * 60 + 30, 10 * 60),
            ("10:00-10:30", 10 * 60, 10 * 60 + 30),
            ("10:30-11:00", 10 * 60 + 30, 11 * 60),
            ("11:00-11:30", 11 * 60, 11 * 60 + 30),
            ("12:30-13:00", 12 * 60 + 30, 13 * 60),
            ("13:00-14:00", 13 * 60, 14 * 60),
            ("14:00-15:00", 14 * 60, 15 * 60),
        ]
        for label, lo, hi in buckets:
            if lo <= hm < hi:
                return label
        return None
    except Exception:
        return None

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

# Replay 用: Yahoo 1分足のローカルキャッシュ（CSV）。スクリプト直下 data/intraday_1m/<日付>/<銘柄>.csv
INTRADAY_1M_CACHE_ROOT = os.path.join(os.path.dirname(__file__), "data", "intraday_1m")

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
# STRONG: 弱理由が無く TOPIX が十分プラスなら（regime_controls STRONG と整合）
STRONG_TOPIX_CHG_PCT_MIN = 0.30
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
    if not r.ok:
        body_snip = (r.text or "")[:8000]
        try:
            body_snip = json.dumps(r.json(), ensure_ascii=False)[:8000]
        except Exception:
            pass
        print(
            f"[{now_str()}] Yahoo chart HTTP {r.status_code} interval=1m symbol={symbol} "
            f"period1={params.get('period1')} period2={params.get('period2')} "
            f"response_body={body_snip}"
        )
        r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        print(f"[{now_str()}] Yahoo chart JSON error field interval=1m symbol={symbol} error={error!r}")
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


def _intraday_1m_cache_csv_path(symbol: str, day_jst: str) -> str:
    """day_jst: JST の暦日 YYYY-MM-DD（ディレクトリ名・ラベル用）。"""
    return os.path.join(INTRADAY_1M_CACHE_ROOT, day_jst, f"{symbol}.csv")


def _parse_csv_timestamp_utc(s: str) -> datetime:
    t = (s or "").strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_intraday_1m_csv_cache(cache_path: str) -> list[ReplayBar]:
    """キャッシュ CSV を読み込み、有効なバーが1本以上あれば返す。無ければ []。"""
    if not os.path.isfile(cache_path):
        return []
    try:
        bars: list[ReplayBar] = []
        with open(cache_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return []
            for row in reader:
                if not row:
                    continue
                ts_raw = (row.get("timestamp_utc") or "").strip()
                if not ts_raw:
                    continue
                dt = _parse_csv_timestamp_utc(ts_raw)
                o = float(row["open"])
                h = float(row["high"])
                l = float(row["low"])
                c = float(row["close"])
                v = float(row["volume"])
                bars.append(
                    ReplayBar(
                        timestamp_utc=dt,
                        open=o,
                        high=h,
                        low=l,
                        close=c,
                        volume=v,
                    )
                )
        bars.sort(key=lambda b: b.timestamp_utc)
        return bars
    except Exception:
        return []


def _save_intraday_1m_csv_cache(cache_path: str, bars: list[ReplayBar]) -> None:
    """Yahoo 取得成功時に 1日分の 1m 足を保存する。"""
    if not bars:
        return
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp_path = cache_path + ".tmp"
    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_utc", "open", "high", "low", "close", "volume"])
            for b in bars:
                w.writerow(
                    [
                        b.timestamp_utc.astimezone(timezone.utc).isoformat(),
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                    ]
                )
        os.replace(tmp_path, cache_path)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


def validate_intraday_1m_cache_coverage_for_replay_days(
    symbols: list[str],
    replay_days_jst: list[str],
) -> dict[str, Any]:
    """
    Replay前に、replay_days_jst のキャッシュ存在を検証します。

    定義:
    - covered_day: 指定した symbols すべての CSV が存在し、かつバーが1本以上読める日
    - missing_day: 1銘柄でも欠けている日
    """
    syms = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    days = [str(d).strip() for d in (replay_days_jst or []) if str(d).strip()]
    total_days = len(days)
    if not syms or not days:
        return {
            "total_days": int(total_days),
            "covered_days": 0,
            "coverage_ratio": 0.0,
            "missing_days": [],
            "missing_by_day": {},
        }

    missing_by_day: dict[str, list[str]] = {}
    covered_days = 0
    for d in days:
        missing_syms: list[str] = []
        for sym in syms:
            p = _intraday_1m_cache_csv_path(sym, d)
            bars = _load_intraday_1m_csv_cache(p)
            if not bars:
                missing_syms.append(sym)
        if missing_syms:
            missing_by_day[d] = missing_syms
        else:
            covered_days += 1

    ratio = (float(covered_days) / float(total_days)) if total_days > 0 else 0.0
    return {
        "total_days": int(total_days),
        "covered_days": int(covered_days),
        "coverage_ratio": float(ratio),
        "missing_days": sorted(list(missing_by_day.keys())),
        "missing_by_day": {k: list(v) for k, v in sorted(missing_by_day.items(), key=lambda kv: kv[0])},
    }


def _effective_replay_days_count_from_bars(
    bars_by_symbol: dict[str, list[ReplayBar]],
    base_symbols: set[str],
) -> int:
    """各銘柄にバーが存在するJST暦日の共通集合サイズ（=全銘柄で実際に再生可能だった日数の目安）。"""
    syms = [s for s in (base_symbols or set()) if s in bars_by_symbol]
    if not syms:
        return 0
    day_sets: list[set[str]] = []
    for sym in syms:
        ds: set[str] = set()
        for b in (bars_by_symbol.get(sym) or []):
            try:
                ds.add(_day_jst_str(b.timestamp_utc))
            except Exception:
                continue
        day_sets.append(ds)
    if not day_sets:
        return 0
    inter = set(day_sets[0])
    for s in day_sets[1:]:
        inter &= set(s)
    return int(len(inter))


def load_or_fetch_intraday_1m_for_replay_day(
    session: requests.Session,
    symbol: str,
    day_jst: str,
    counters: dict[str, int],
    *,
    timeout_sec: float = 20.0,
) -> tuple[list[ReplayBar], dict]:
    """
    Replay 用 1日分 1m 足: ローカル CSV があれば読む / 無ければ Yahoo（窓外は取得しない）。
    counters は cache_hit / cache_miss / yahoo_fetch / yahoo_1m_window_out を累積する。
    """
    cache_path = _intraday_1m_cache_csv_path(symbol, day_jst)
    cached = _load_intraday_1m_csv_cache(cache_path)
    if cached:
        counters["cache_hit"] += 1
        print(f"[{now_str()}] intraday_1m cache_hit symbol={symbol} day={day_jst} bars={len(cached)}")
        return cached, {}

    counters["cache_miss"] += 1
    print(f"[{now_str()}] intraday_1m cache_miss symbol={symbol} day={day_jst}")

    try:
        y, m, dd = (int(x) for x in day_jst.split("-"))
        day_date = date(y, m, dd)
    except Exception:
        return [], {}

    today_jst = datetime.now(JST).date()
    lo, hi = _yahoo_1m_available_calendar_bounds_jst(today_jst)
    if day_date < lo or day_date > hi:
        counters["yahoo_1m_window_out"] += 1
        print(
            f"[{now_str()}] intraday_1m yahoo_1m_window_out symbol={symbol} day={day_jst} "
            f"(skip Yahoo: outside ~{YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS}d intraday window)"
        )
        return [], {}

    day0 = datetime(y, m, dd, 0, 0, 0, tzinfo=JST)
    day1 = day0 + timedelta(days=1)
    start_u = day0.astimezone(timezone.utc)
    end_u = day1.astimezone(timezone.utc)

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            bs, meta = fetch_history_1m_by_period(
                session,
                symbol,
                start_utc=start_u,
                end_utc=end_u,
                timeout_sec=timeout_sec,
            )
            counters["yahoo_fetch"] += 1
            print(f"[{now_str()}] intraday_1m yahoo_fetch symbol={symbol} day={day_jst} bars={len(bs)}")
            if bs:
                try:
                    _save_intraday_1m_csv_cache(cache_path, bs)
                except Exception as se:
                    print(f"[{now_str()}] intraday_1m cache_save_failed symbol={symbol} day={day_jst}: {se}")
            return bs, meta
        except requests.HTTPError as e:
            sc = int(e.response.status_code) if e.response is not None else -1
            if sc == 422:
                counters["yahoo_1m_window_out"] += 1
                print(
                    f"[{now_str()}] intraday_1m yahoo_1m_window_out symbol={symbol} day={day_jst} "
                    f"(HTTP {sc})"
                )
                return [], {}
            last_err = e
            time.sleep(1.0 * (attempt + 1))
        except RuntimeError:
            counters["yahoo_1m_window_out"] += 1
            print(f"[{now_str()}] intraday_1m yahoo_1m_window_out symbol={symbol} day={day_jst} (chart error)")
            return [], {}
        except Exception as e:
            if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                last_err = e
                break
            last_err = e
            time.sleep(1.0 * (attempt + 1))

    if last_err is not None:
        raise last_err
    return [], {}


def summarize_intraday_1m_cache_coverage(cache_root: str = INTRADAY_1M_CACHE_ROOT) -> dict[str, Any]:
    """
    data/intraday_1m の CSV を走査し、coverage（全日付・銘柄日・月別）を集計します。
    """
    out: dict[str, Any] = {
        "cache_root": cache_root,
        "exists": os.path.isdir(cache_root),
        "unique_calendar_days": 0,
        "total_csv_files": 0,
        "symbols_distinct": 0,
        "date_min": "",
        "date_max": "",
        "by_month": {},
    }
    if not out["exists"]:
        return out

    date_dirs: list[str] = []
    for name in os.listdir(cache_root):
        p = os.path.join(cache_root, name)
        if not os.path.isdir(p):
            continue
        try:
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        date_dirs.append(name)

    date_dirs.sort()
    out["unique_calendar_days"] = len(date_dirs)
    if date_dirs:
        out["date_min"] = date_dirs[0]
        out["date_max"] = date_dirs[-1]

    sym_union: set[str] = set()
    total_files = 0
    by_month: dict[str, dict[str, Any]] = {}

    for d in date_dirs:
        dir_path = os.path.join(cache_root, d)
        ym = d[:7]
        month_bucket = by_month.setdefault(
            ym,
            {"calendar_days_with_any_csv": 0, "symbol_day_files": 0},
        )
        day_files = 0
        try:
            for fn in os.listdir(dir_path):
                if not fn.endswith(".csv"):
                    continue
                fp = os.path.join(dir_path, fn)
                if not os.path.isfile(fp):
                    continue
                sym = fn[:-4]
                sym_union.add(sym)
                total_files += 1
                day_files += 1
        except Exception:
            continue
        if day_files > 0:
            month_bucket["calendar_days_with_any_csv"] += 1
            month_bucket["symbol_day_files"] += day_files

    out["total_csv_files"] = total_files
    out["symbols_distinct"] = len(sym_union)
    out["by_month"] = {
        ym: {
            "calendar_days_with_any_csv": int(v.get("calendar_days_with_any_csv", 0)),
            "symbol_day_files": int(v.get("symbol_day_files", 0)),
        }
        for ym, v in sorted(by_month.items(), key=lambda kv: kv[0])
    }
    return out


def print_intraday_1m_cache_coverage_report(stats: dict[str, Any]) -> None:
    """summarize_intraday_1m_cache_coverage の結果をターミナルへ整形出力する。"""
    print("=== intraday_1m cache coverage ===")
    root = str(stats.get("cache_root") or "")
    print(f"- cache_root: {root}")
    if not stats.get("exists"):
        print("- (キャッシュディレクトリなし)")
        print("")
        return
    print(f"- unique_calendar_days: {int(stats.get('unique_calendar_days') or 0)}")
    print(f"- total_csv_files (symbol-days): {int(stats.get('total_csv_files') or 0)}")
    print(f"- symbols_distinct: {int(stats.get('symbols_distinct') or 0)}")
    dm = str(stats.get("date_min") or "")
    dx = str(stats.get("date_max") or "")
    if dm and dx:
        print(f"- date_range: {dm} .. {dx}")
    print("- by_month:")
    bm = stats.get("by_month") or {}
    if isinstance(bm, dict):
        for ym in sorted(bm.keys()):
            row = bm.get(ym) or {}
            if not isinstance(row, dict):
                continue
            print(
                f"  - {ym}: calendar_days_with_any_csv={int(row.get('calendar_days_with_any_csv') or 0)}, "
                f"symbol_day_files={int(row.get('symbol_day_files') or 0)}"
            )
    print("")


def _resolve_watch_symbols_for_eod(fixed_watch: Optional[list[str]]) -> list[str]:
    """監視ループと同優先度で銘柄リストを解決（--watch-file / --watch → watchlist.json → symbols.csv → WATCH）。"""
    if fixed_watch is not None:
        out = [str(s).strip() for s in fixed_watch if str(s).strip()]
        return list(dict.fromkeys(out))
    if os.path.exists(WATCHLIST_JSON_PATH):
        w, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
        if err:
            print(f"[{now_str()}] watchlist.json 読み込みエラー: {err}")
            return []
        out = [str(x).strip() for x in w if str(x).strip()]
        return list(dict.fromkeys(out))
    loaded = _load_symbols_csv(SYMBOLS_CSV_PATH)
    base = loaded if loaded else list(WATCH)
    out = [str(x).strip() for x in base if str(x).strip()]
    return list(dict.fromkeys(out))


def run_intraday_1m_eod_save_cli(
    symbols: list[str],
    *,
    day_jst: str,
    force_before_close: bool,
    timeout_sec: float = 25.0,
    delay_sec: float = 0.15,
) -> int:
    """
    引け後に当日（または指定日）の 1分足を Yahoo から取得し CSV キャッシュへ保存します。
    ログ語: cache_saved / cache_skip_exists / yahoo_fetch_failed
    """
    syms = list(dict.fromkeys([s for s in symbols if str(s).strip()]))
    if not syms:
        print(f"[{now_str()}] intraday_1m_eod: 監視銘柄が空です。")
        return 2

    now_jst = datetime.now(JST)
    today_jst = now_jst.strftime("%Y-%m-%d")
    if not day_jst.strip():
        day_jst = today_jst
    else:
        day_jst = day_jst.strip()
        try:
            datetime.strptime(day_jst, "%Y-%m-%d")
        except ValueError:
            print(f"[{now_str()}] intraday_1m_eod: 日付が不正です（YYYY-MM-DD）: {day_jst}")
            return 2

    if day_jst == today_jst and not force_before_close:
        if now_jst.hour < 15 or (now_jst.hour == 15 and now_jst.minute < 30):
            print(
                f"[{now_str()}] intraday_1m_eod: JST 15:30 未満のため中止します "
                f"(当日分は引け後に実行してください。テスト時は --force-intraday-1m-eod-time)"
            )
            return 2

    try:
        y, m, dd = (int(x) for x in day_jst.split("-"))
        day0 = datetime(y, m, dd, 0, 0, 0, tzinfo=JST)
    except Exception:
        print(f"[{now_str()}] intraday_1m_eod: 日付の解釈に失敗しました: {day_jst}")
        return 2
    day1 = day0 + timedelta(days=1)
    start_u = day0.astimezone(timezone.utc)
    end_u = day1.astimezone(timezone.utc)

    cnt = {"cache_saved": 0, "cache_skip_exists": 0, "yahoo_fetch_failed": 0}

    print(f"[{now_str()}] intraday_1m_eod: start day_jst={day_jst} symbols={len(syms)} delay_sec={delay_sec}")
    print("")

    with requests.Session() as session:
        for i, sym in enumerate(syms):
            cache_path = _intraday_1m_cache_csv_path(sym, day_jst)
            existing = _load_intraday_1m_csv_cache(cache_path)
            if existing:
                cnt["cache_skip_exists"] += 1
                print(f"[{now_str()}] intraday_1m_eod cache_skip_exists symbol={sym} day={day_jst} bars={len(existing)}")
                if i + 1 < len(syms) and delay_sec > 0:
                    time.sleep(delay_sec)
                continue

            last_err: Optional[Exception] = None
            bs: list[ReplayBar] = []
            meta: dict = {}
            for attempt in range(3):
                try:
                    bs, meta = fetch_history_1m_by_period(
                        session,
                        sym,
                        start_utc=start_u,
                        end_utc=end_u,
                        timeout_sec=timeout_sec,
                    )
                    last_err = None
                    break
                except requests.HTTPError as e:
                    last_err = e
                    sc = int(e.response.status_code) if e.response is not None else -1
                    if sc != 422:
                        time.sleep(1.0 * (attempt + 1))
                    else:
                        break
                except RuntimeError as e:
                    last_err = e
                    break
                except Exception as e:
                    last_err = e
                    if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                        break
                    time.sleep(1.0 * (attempt + 1))

            if last_err is not None:
                cnt["yahoo_fetch_failed"] += 1
                print(
                    f"[{now_str()}] intraday_1m_eod yahoo_fetch_failed symbol={sym} day={day_jst} error={last_err!r}"
                )
            elif not bs:
                cnt["yahoo_fetch_failed"] += 1
                print(
                    f"[{now_str()}] intraday_1m_eod yahoo_fetch_failed symbol={sym} day={day_jst} "
                    f"error=(empty chart / holiday / no session)"
                )
            else:
                try:
                    _save_intraday_1m_csv_cache(cache_path, bs)
                    cnt["cache_saved"] += 1
                    print(
                        f"[{now_str()}] intraday_1m_eod cache_saved symbol={sym} day={day_jst} bars={len(bs)}"
                    )
                except Exception as se:
                    cnt["yahoo_fetch_failed"] += 1
                    print(
                        f"[{now_str()}] intraday_1m_eod yahoo_fetch_failed symbol={sym} day={day_jst} error={se!r}"
                    )

            if i + 1 < len(syms) and delay_sec > 0:
                time.sleep(delay_sec)

    print("")
    print("=== intraday_1m_eod summary ===")
    print(f"- cache_saved: {cnt['cache_saved']}")
    print(f"- cache_skip_exists: {cnt['cache_skip_exists']}")
    print(f"- yahoo_fetch_failed: {cnt['yahoo_fetch_failed']}")
    print("")

    cov = summarize_intraday_1m_cache_coverage()
    print_intraday_1m_cache_coverage_report(cov)
    days_cached = int(cov.get("unique_calendar_days") or 0)
    print(f"=== キャッシュ済みカレンダー日数（全日・全銘柄合算）: {days_cached} 日分 ===")
    print("")
    return 1 if cnt["yahoo_fetch_failed"] > 0 else 0


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
        "--paper-trade",
        action="store_true",
        help=(
            "実注文なしの paper trade（仮想signal/exit/PnLのみ）。"
            " run_replay と同一ロジックで Yahoo Finance 1d 1分足を定期的にスナップショットし、"
            " results/paper_trade/YYYYMMDD/paper_trade_log.csv に追記します。"
        ),
    )
    p.add_argument(
        "--paper-trade-interval",
        type=float,
        default=60.0,
        help="paper_trade のスナップショット間隔（秒）。デフォルト 60",
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
        choices=[
            "1d",
            "5d",
            "10d",
            "20d",
            "60d",
            "random_5d",
            "random_60d",
            "random_feb",
            "random_mar",
            "random_mar_cache_only",
            "random_apr",
        ],
        help=(
            "リプレイで取得する期間。1d/5d/10d/20d/60d。"
            " random_5d は『直近の過去3か月ウィンドウからランダムに5営業日抽出』。"
            " random_60d は『2026-02-01〜2026-04-30 の平日候補からランダムに5営業日抽出』。"
            " random_feb/mar/apr は各月の全日付ウィンドウの平日候補から同様に抽出。"
            " random_mar_cache_only は『random_mar と同じ日付プールだが、キャッシュがある日だけから抽出（Yahoo取得しない）』。"
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
        help=(
            "Replayの戦略条件をまとめたconfig JSONパス（例: configs/replay_safe.json）。"
            " Paper trade 暫定候補: configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json"
        ),
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
    p.add_argument(
        "--save-intraday-1m-eod",
        action="store_true",
        help=(
            "引け後（JST 15:30 以降）向け: 監視銘柄の当日 1分足を Yahoo から取得し "
            "data/intraday_1m/YYYY-MM-DD/<銘柄>.csv に保存（既存ならスキップ）。"
        ),
    )
    p.add_argument(
        "--intraday-1m-eod-date",
        type=str,
        default="",
        help="上書きする対象日（YYYY-MM-DD）。未指定は当日（JST）。過去日の再取得時は時間制約なし。",
    )
    p.add_argument(
        "--force-intraday-1m-eod-time",
        action="store_true",
        help="当日分を保存する際、JST 15:30 前でも実行する（検証用）。",
    )
    p.add_argument(
        "--intraday-1m-eod-delay-sec",
        type=float,
        default=0.15,
        help="銘柄間の待機秒（レート緩和）。デフォルト 0.15",
    )
    p.add_argument(
        "--intraday-1m-cache-report-only",
        action="store_true",
        help="1分足キャッシュの coverage 集計だけ表示し、Yahoo 取得は行いません。",
    )
    p.add_argument(
        "--vwap-distance-sweep",
        action="store_true",
        help=(
            "VWAP distance フィルタ閾値 1.5/2.0/2.5/3.0 で replay-range random_apr（デフォルト10回）を sweep し、"
            " expectancy（平均）順の比較表を results/vwap_sweep_summary_<時刻>.txt に保存します（config は自動生成）。"
        ),
    )
    p.add_argument(
        "--daily-loss-stop-sweep",
        action="store_true",
        help=(
            "daily_loss_stop のON/OFF・閾値(dd30k/dd50k/dd70k)を sweep します。"
            " 対象config: replay_morning_vwap2.json(OFF), replay_morning_vwap2_dd30k/dd50k/dd70k。"
            " 各configについて --replay-range random_apr（--replay-repeat は既定または指定値）を実行し、"
            " results/daily_loss_stop_sweep_<時刻>/sweep_summary.txt に保存します。"
        ),
    )
    p.add_argument(
        "--regime-filter-sweep",
        action="store_true",
        help=(
            "market regime filter（disable_morning_weak / disable_rising_ratio_lt50 / disable_topix_weak）の組み合わせを sweep します。"
            " --replay-range random_apr（sweep はこの範囲のみ）で results/regime_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/regime_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--topix-weak-threshold-sweep",
        action="store_true",
        help=(
            "TOPIX_WEAK 判定の threshold（%%）を sweep します（-0.2/-0.3/-0.5/-0.7）。"
            " 各thresholdで disable_topix_weak を有効化し、--replay-range random_apr で実行、"
            " results/topix_weak_threshold_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/regime_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--signal-filter-sweep",
        action="store_true",
        help=(
            "signal_filters（gap>=1.5/2/2.5/3/4%% + baseline）を sweep します。"
            " --replay-range random_apr×--replay-repeat のみ実行し、results/signal_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/signal_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--composite-filter-sweep",
        action="store_true",
        help=(
            "composite_signal_filters（WEAK時のみ VWAP距離／ギャップしきい値）を sweep します。"
            " --replay-range random_apr×--replay-repeat のみ実行し、"
            " results/composite_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/composite_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--regime-control-sweep",
        action="store_true",
        help=(
            "replay_morning_vwap2_dd30k_rlt50 と full-day無RC と full-day+regime_controls を "
            "--replay-range random_apr のみで比較します（configs/replay_full_day_*.json を使用）。"
            " results/regime_control_sweep_<時刻>/sweep_summary.txt に保存します。"
        ),
    )
    p.add_argument(
        "--weak-risk-filter-sweep",
        action="store_true",
        help=(
            "WEAK地合いで VWAP距離>=1.5 / gap>=3 / 両方 のみ除外する composite_signal_filters.weak_risk_filter を比較します。"
            " morning_baseline / full_day / 上記3モードを random_apr のみ実行し、"
            " results/weak_risk_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
        ),
    )
    p.add_argument(
        "--strong-risk-filter-sweep",
        action="store_true",
        help=(
            "STRONG地合いで entry_vwap_distance_pct がしきい値以上の ENTRY を除外する composite_signal_filters.strong_risk_filter を比較します。"
            " full_day（無フィルタ）と strong_vwap_ge_15/12/10 の4パターンを random_apr のみ実行し、"
            " results/strong_risk_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/strong_risk_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--strong-combo-filter-sweep",
        action="store_true",
        help=(
            "composite_signal_filters.strong_combo_filter（高値更新回数×VWAP距離）を比較します。"
            " baseline / HU2_VWAP15 / HU1or2_VWAP15 を random_apr のみ実行し、"
            " results/strong_combo_filter_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/strong_combo_filter_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--strong-trend-quality-sweep",
        action="store_true",
        help=(
            "STRONG で VWAP乖離≥1.5%% のとき、高値更新回数による介入を比較します。"
            " baseline と hu≤2/≤3 での skip、および HU≥6 のみ許可（それ以外skip）を random_apr のみ実行し、"
            " results/strong_trend_quality_sweep_<時刻>/sweep_summary.txt に保存します。"
            " configs/strong_trend_quality_sweep/ に比較用configを自動生成します。"
        ),
    )
    p.add_argument(
        "--strong-trend-quality-validation-sweep",
        action="store_true",
        help=(
            "baseline と strong_vwap_ge_15_and_hu_le2_skip を random_apr / random_mar / random_60d で検証します。"
            " 既定 replay_repeat=20、run_i の seed は replay_seed+i-1。"
            " results/strong_trend_quality_validation_sweep_<時刻>/sweep_summary.txt に Delta vs baseline を出力します。"
        ),
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
    entry_filter_rsi_enabled: bool = False,
    entry_filter_rsi_exclude_above: float = 75.0,
    entry_filter_vwap_distance_enabled: bool = False,
    entry_filter_vwap_distance_exclude_above: float = 2.0,
    entry_filter_atr_pct_enabled: bool = False,
    entry_filter_atr_pct_exclude_above: float = 4.0,
    daily_loss_stop_enabled: bool = False,
    daily_loss_stop_threshold_yen_100_shares: float = 50_000.0,
    regime_filter_disable_morning_weak: bool = False,
    regime_filter_disable_rising_ratio_lt50: bool = False,
    regime_filter_disable_topix_weak: bool = False,
    regime_filter_topix_weak_threshold_pct: Optional[float] = None,
    signal_filter_disable_gap_ge_pct: bool = False,
    signal_filter_gap_ge_threshold_pct: float = 3.0,
    signal_filter_disable_vwap_distance_ge_pct: bool = False,
    signal_filter_vwap_distance_ge_threshold_pct: float = 1.5,
    signal_filter_disable_entry_after_hhmm: bool = False,
    signal_filter_entry_after_hhmm: str = "10:30",
    composite_signal_filter_disable_weak_vwap_ge: bool = False,
    composite_signal_filter_weak_vwap_ge_threshold_pct: float = 1.5,
    composite_signal_filter_disable_weak_gap_ge: bool = False,
    composite_signal_filter_weak_gap_ge_threshold_pct: float = 3.0,
    composite_signal_filter_weak_risk_filter: str = "",
    composite_signal_filter_strong_risk_filter: str = "",
    composite_signal_filter_strong_vwap_ge_threshold_pct: float = 1.5,
    composite_signal_filter_strong_combo_enabled: bool = False,
    composite_signal_filter_strong_combo_block_conditions: Optional[list[dict[str, Any]]] = None,
    composite_signal_filter_strong_combo_snapshot: Optional[dict[str, Any]] = None,
    regime_control_enabled: bool = False,
    regime_control_profiles: Optional[dict[str, dict[str, Any]]] = None,
    regime_control_config_snapshot: Optional[dict[str, Any]] = None,
    replay_settings: Optional[dict[str, Any]] = None,
    paper_trade_mode: bool = False,
    paper_trade_collect: Optional[dict[str, Any]] = None,
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
    if replay_range not in (
        "1d",
        "5d",
        "10d",
        "20d",
        "60d",
        "random_5d",
        "random_60d",
        "random_feb",
        "random_mar",
        "random_mar_cache_only",
        "random_apr",
    ):
        print(
            "--replay-range は 1d/5d/10d/20d/60d と "
            "random_5d / random_60d / random_feb / random_mar / random_apr を指定してください。"
        )
        return 2

    # repeatロットの識別子（重要: run_replay 全体で必ず定義しておく）
    # - main側で replay_batch_stamp が渡される想定だが、単体実行でも落ちないようにフォールバックする
    batch_stamp = str(replay_batch_stamp or "").strip() or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    safe_batch_stamp = str(batch_stamp or "").strip() or "replay"
    _regime_profiles_rt: dict[str, dict[str, Any]] = (
        dict(regime_control_profiles) if isinstance(regime_control_profiles, dict) else {}
    )
    _rc_snap_report: dict[str, Any] = (
        dict(regime_control_config_snapshot) if isinstance(regime_control_config_snapshot, dict) else {}
    )

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
            "entry_filters": {
                "rsi": {
                    "enabled": {"value": bool(entry_filter_rsi_enabled), "source": "default"},
                    "exclude_above": {"value": float(entry_filter_rsi_exclude_above), "source": "default"},
                },
                "vwap_distance_pct": {
                    "enabled": {"value": bool(entry_filter_vwap_distance_enabled), "source": "default"},
                    "exclude_above": {"value": float(entry_filter_vwap_distance_exclude_above), "source": "default"},
                },
                "atr_pct": {
                    "enabled": {"value": bool(entry_filter_atr_pct_enabled), "source": "default"},
                    "exclude_above": {"value": float(entry_filter_atr_pct_exclude_above), "source": "default"},
                },
            },
            "risk_controls": {
                "daily_loss_stop": {
                    "enabled": {"value": bool(daily_loss_stop_enabled), "source": "default"},
                    "stop_yen_100_shares": {"value": float(daily_loss_stop_threshold_yen_100_shares), "source": "default"},
                }
            },
        }
    _rr_fixed = str(replay_range).strip()
    if _replay_fixed_random_pool_dates(_rr_fixed) and isinstance(replay_settings, dict):
        _meta_x = _replay_fixed_random_meta_extra(_rr_fixed)
        replay_settings.setdefault(
            "replay_date_pool_start",
            {"value": str(_meta_x.get("replay_date_pool_start") or ""), "source": _rr_fixed},
        )
        replay_settings.setdefault(
            "replay_date_pool_end",
            {"value": str(_meta_x.get("replay_date_pool_end") or ""), "source": _rr_fixed},
        )
        replay_settings.setdefault(
            "replay_candidate_days_count",
            {
                "value": int(_replay_fixed_random_weekday_candidate_count(_rr_fixed)),
                "source": _rr_fixed,
            },
        )

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
        for pk in ("replay_date_pool_start", "replay_date_pool_end", "replay_candidate_days_count"):
            if pk not in st:
                continue
            pv = st.get(pk)
            if isinstance(pv, dict):
                out.append(f"{pk}={pv.get('value')} ({pv.get('source')})")
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
        ef = st.get("entry_filters") if isinstance(st.get("entry_filters"), dict) else {}
        if isinstance(ef, dict):
            for fk, title in [
                ("rsi", "RSI filter"),
                ("vwap_distance_pct", "VWAP distance filter"),
                ("atr_pct", "ATR filter"),
            ]:
                sub = ef.get(fk) if isinstance(ef.get(fk), dict) else {}
                en = sub.get("enabled") if isinstance(sub.get("enabled"), dict) else {}
                thr = sub.get("exclude_above") if isinstance(sub.get("exclude_above"), dict) else {}
                if isinstance(en, dict) and isinstance(thr, dict):
                    out.append(
                        f"{title}: enabled={en.get('value')} threshold={thr.get('value')} ({en.get('source')}/{thr.get('source')})"
                    )
        out.append("")
        rc = st.get("risk_controls") if isinstance(st.get("risk_controls"), dict) else {}
        if isinstance(rc, dict):
            dls = rc.get("daily_loss_stop") if isinstance(rc.get("daily_loss_stop"), dict) else {}
            if isinstance(dls, dict):
                en = dls.get("enabled") if isinstance(dls.get("enabled"), dict) else {}
                thr = dls.get("stop_yen_100_shares") if isinstance(dls.get("stop_yen_100_shares"), dict) else {}
                if isinstance(en, dict) and isinstance(thr, dict):
                    out.append(
                        f"daily_loss_stop.enabled={en.get('value')} ({en.get('source')})"
                    )
                    out.append(
                        f"daily_loss_stop.stop_yen_100_shares={thr.get('value')} ({thr.get('source')})"
                    )
                    out.append("")
        rf = st.get("regime_filters") if isinstance(st.get("regime_filters"), dict) else {}
        if isinstance(rf, dict) and rf:
            out.append("【Regime filters】")
            for k in ("disable_morning_weak", "disable_rising_ratio_lt50", "disable_topix_weak", "topix_weak_threshold_pct"):
                v = rf.get(k)
                if isinstance(v, dict):
                    out.append(f"{k}={v.get('value')} ({v.get('source')})")
            out.append("")
        sf = st.get("signal_filters") if isinstance(st.get("signal_filters"), dict) else {}
        if isinstance(sf, dict) and sf:
            out.append("【Signal filters】")
            for k in (
                "disable_gap_ge_pct",
                "gap_ge_threshold_pct",
                "disable_vwap_distance_ge_pct",
                "vwap_distance_ge_threshold_pct",
                "disable_entry_after_hhmm",
                "entry_after_hhmm",
            ):
                v = sf.get(k)
                if isinstance(v, dict):
                    out.append(f"{k}={v.get('value')} ({v.get('source')})")
            out.append("")
        rctl = st.get("regime_controls") if isinstance(st.get("regime_controls"), dict) else {}
        if isinstance(rctl, dict) and rctl:
            out.append("【Regime adaptive controls】")
            v = rctl.get("enabled")
            if isinstance(v, dict):
                out.append(f"enabled={v.get('value')} ({v.get('source')})")
            out.append("")
        csf = st.get("composite_signal_filters") if isinstance(st.get("composite_signal_filters"), dict) else {}
        if isinstance(csf, dict) and csf:
            out.append("【Composite signal filters (WEAKのみ)】")
            for k in (
                "disable_state_weak_and_vwap_ge_pct",
                "state_weak_vwap_ge_threshold_pct",
                "disable_state_weak_and_gap_ge_pct",
                "state_weak_gap_ge_threshold_pct",
            ):
                v = csf.get(k)
                if isinstance(v, dict):
                    out.append(f"{k}={v.get('value')} ({v.get('source')})")
            out.append("")
        scf = st.get("strong_combo_filter") if isinstance(st.get("strong_combo_filter"), dict) else {}
        if isinstance(scf, dict) and scf:
            out.append("【strong_combo_filter】")
            ev = scf.get("enabled")
            if isinstance(ev, dict):
                out.append(f"strong_combo_filter enabled={ev.get('value')} ({ev.get('source')})")
            mr = scf.get("market_regime")
            if isinstance(mr, dict) and str(mr.get("value") or "").strip():
                out.append(f"market_regime={mr.get('value')} ({mr.get('source')})")
            vg = scf.get("entry_vwap_distance_pct_ge")
            if isinstance(vg, dict) and isinstance(vg.get("value"), (int, float)):
                out.append(f"vwap_distance>={vg.get('value')} ({vg.get('source')})")
            hule = scf.get("high_update_count_before_entry_le")
            if isinstance(hule, dict) and hule.get("value") is not None:
                try:
                    out.append(f"high_update_count<={int(hule.get('value'))} ({hule.get('source')})")
                except Exception:
                    out.append(f"high_update_count<={hule.get('value')} ({hule.get('source')})")
            hueq = scf.get("high_update_count_before_entry_eq")
            if isinstance(hueq, dict) and hueq.get("value") is not None:
                try:
                    out.append(f"high_update_count=={int(hueq.get('value'))} ({hueq.get('source')})")
                except Exception:
                    out.append(f"high_update_count=={hueq.get('value')} ({hueq.get('source')})")
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
    if paper_trade_mode:
        # Paper trade: 仮想検証のみ。DiscordのReplay通知・実発注系とも無関係にします。
        discord_enabled = False
        fast_mode = True

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
    regime_filter_skipped_signals_count = 0
    regime_filter_skip_reason_counts: dict[str, int] = {}
    regime_filter_diag_checked_count = 0
    regime_filter_diag_passed_count = 0
    regime_filter_diag_skipped_count = 0
    regime_filter_diag_sample_skipped: list[dict[str, Any]] = []
    REGIME_FILTER_DIAG_SAMPLE_MAX = 30
    # TOPIX_WEAK filter の仮想PnL分析（skipされたsignalを「仮想的に保有」して損益を推定）
    regime_topix_weak_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    regime_topix_weak_virtual_pnl_sum = 0.0
    regime_topix_weak_virtual_win = 0
    regime_topix_weak_virtual_lose = 0
    regime_topix_weak_virtual_count = 0

    # signal_filters virtual pnl
    signal_filters_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    signal_filters_virtual_pnl_sum = 0.0
    signal_filters_virtual_win = 0
    signal_filters_virtual_lose = 0
    signal_filters_virtual_count = 0
    signal_filters_skipped_signals_count = 0
    signal_filters_skip_reason_counts: dict[str, int] = {}
    resolved_counted_signal_filter_virtual_indices: set[int] = set()
    # composite_signal_filters（WEAK×gap/VWAP）仮想PnL
    composite_signal_filter_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    composite_signal_filter_virtual_pnl_sum = 0.0
    composite_signal_filter_virtual_win = 0
    composite_signal_filter_virtual_lose = 0
    composite_signal_filter_virtual_count = 0
    composite_signal_filter_skipped_signals_count = 0
    composite_signal_filter_skip_reason_counts: dict[str, int] = {}
    resolved_counted_composite_signal_filter_virtual_indices: set[int] = set()
    strong_combo_filter_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    strong_combo_filter_virtual_pnl_sum = 0.0
    strong_combo_filter_virtual_count = 0
    strong_combo_filter_virtual_pnl_by_reason: dict[str, float] = {}
    strong_combo_filter_virtual_count_by_reason: dict[str, int] = {}
    strong_combo_filter_skipped_signals_count = 0
    strong_combo_filter_skip_reason_counts: dict[str, int] = {}
    resolved_counted_strong_combo_filter_virtual_indices: set[int] = set()
    _strong_combo_conds_rt: list[dict[str, Any]] = list(composite_signal_filter_strong_combo_block_conditions or [])
    _strong_combo_reasons_frozen = frozenset(
        str(x.get("reason") or "").strip() for x in _strong_combo_conds_rt if str(x.get("reason") or "").strip()
    )
    regime_control_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    regime_control_virtual_pnl_sum = 0.0
    regime_control_virtual_win = 0
    regime_control_virtual_lose = 0
    regime_control_virtual_count = 0
    regime_control_skipped_signals_count = 0
    regime_control_skip_reason_counts: dict[str, int] = {}
    resolved_counted_regime_control_virtual_indices: set[int] = set()
    resolved_counted_regime_topix_virtual_indices: set[int] = set()

    # market_regime / rising_ratio 分布（TODO-02/03 デバッグ用）
    market_regime_counts: dict[str, int] = {}
    rising_ratio_samples = 0
    rising_ratio_lt50_samples = 0
    rising_ratio_lt40_samples = 0
    rising_ratio_ge60_samples = 0
    rising_ratio_sum = 0.0
    rising_ratio_min = None
    rising_ratio_max = None

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

    # 当日停止（risk_controls.daily_loss_stop）:
    # - 当日累積損益(100株円) が -threshold 以下になったら、その日それ以降の新規ENTRY/ADDを停止
    daily_pnl_yen_100_by_day: dict[str, float] = {}
    daily_pnl_min_yen_100_by_day: dict[str, float] = {}
    daily_loss_stop_triggered_by_day: dict[str, bool] = {}
    daily_loss_stop_trigger_dt_jst_by_day: dict[str, datetime] = {}
    daily_loss_stop_pnl_at_trigger_by_day: dict[str, float] = {}
    daily_loss_stop_trigger_count = 0
    daily_loss_stop_triggered_days: list[str] = []
    daily_loss_stop_skipped_entries = 0
    daily_loss_stop_skipped_entries_by_day: dict[str, int] = {}
    daily_loss_stop_virtual_pnl_sum_by_day: dict[str, float] = {}
    daily_loss_stop_virtual_win_by_day: dict[str, int] = {}
    daily_loss_stop_virtual_lose_by_day: dict[str, int] = {}
    daily_loss_stop_virtual_active_indices_by_symbol: dict[str, list[int]] = {}
    resolved_counted_virtual_signal_indices: set[int] = set()
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

        if not paper_trade_mode:
            print("=== TEST REPLAY MODE ===")
        # 表示用の replay_range（通常の 1d/5d には影響させない）
        replay_range_label = str(replay_range)
        # --replay-range random_5d をショートカットとして扱う
        if replay_range_label == "random_5d" and int(replay_random_days or 0) <= 0:
            replay_random_days = 5
            replay_random_months = 3
        # 固定プール（random_60d / random_feb 等）: プールから5営業日（ショートカット）
        if replay_range_label in FIXED_RANDOM_REPLAY_LABELS and int(replay_random_days or 0) <= 0:
            replay_random_days = 5
        if int(replay_random_days or 0) > 0:
            if replay_range_label not in FIXED_RANDOM_REPLAY_LABELS:
                replay_range_label = f"random_{int(replay_random_days)}d"
        if not paper_trade_mode:
            print(f"- replay_range: {replay_range_label}")
        # リプレイ速度の見せ方を「直感的」にします。
        # interval_sec=1.0 なら「1秒 = 1分」
        # interval_sec=0.5 なら「0.5秒 = 1分」など。
        if abs(interval_sec - 1.0) < 1e-9:
            speed_s = "1秒 = 1分"
        else:
            speed_s = f"{interval_sec:.2f}秒 = 1分"
        if not paper_trade_mode:
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
        if str(replay_range) == "random_5d" or str(replay_range) in FIXED_RANDOM_REPLAY_LABELS or int(replay_random_days or 0) > 0:
            fetch_range = "60d"

        # -----------------------------
        # 過去1分足の取得（最初にまとめて取る）
        # -----------------------------
        bars_by_symbol: dict[str, list[ReplayBar]] = {}
        meta_by_symbol: dict[str, dict] = {}
        intraday_1m_counters: dict[str, int] = {
            "cache_hit": 0,
            "cache_miss": 0,
            "yahoo_fetch": 0,
            "yahoo_1m_window_out": 0,
        }

        # Replay日付のランダム抽出（追加仕様）
        replay_dates_jst: list[str] = []
        replay_random_pick_meta: dict[str, Any] = {}
        replay_cache_coverage_validator: dict[str, Any] = {}
        if int(replay_random_days or 0) > 0:
            months = int(replay_random_months or 3)
            if months <= 0:
                months = 3
            k = int(replay_random_days)
            if k <= 0:
                k = 5

            # 候補日（平日カレンダー）を作る（祝日はAPIでデータが無いので後で除外される）
            # cache_only は「キャッシュがある日だけ」で抽出する（Yahoo取得しない）
            cache_only = str(replay_range).strip().endswith("_cache_only")
            _pool_dates = _replay_fixed_random_pool_dates(str(replay_range))
            if _pool_dates:
                candidates = _weekday_date_strings_between(_pool_dates[0], _pool_dates[1])
            else:
                now_jst = datetime.now(JST)
                start_jst = (now_jst - timedelta(days=months * 31)).date()
                end_jst = now_jst.date()
                candidates = []
                d = start_jst
                while d <= end_jst:
                    if d.weekday() < 5:
                        candidates.append(d.strftime("%Y-%m-%d"))
                    d = d + timedelta(days=1)

            _today_ref = datetime.now(JST).date()
            _lo_ref, _hi_ref = _yahoo_1m_available_calendar_bounds_jst(_today_ref)
            n_weekdays_in_pool = len(candidates)

            cache_complete_candidate_days_count = 0
            cache_incomplete_days_count = 0
            cache_complete_ratio = 0.0
            cache_incomplete_missing_symbol_counts_by_day: dict[str, int] = {}
            if cache_only:
                # 候補を「全監視銘柄でキャッシュ完備の日」だけに絞る
                cov_all = validate_intraday_1m_cache_coverage_for_replay_days(
                    symbols=list(watch),
                    replay_days_jst=list(candidates),
                )
                missing_by_day = cov_all.get("missing_by_day") or {}
                complete_days = [d for d in candidates if str(d) not in set(missing_by_day.keys())]
                incomplete_days = sorted(list(set([d for d in candidates if d not in set(complete_days)])))
                cache_complete_candidate_days_count = int(len(complete_days))
                cache_incomplete_days_count = int(len(incomplete_days))
                total_cand = int(len(candidates))
                cache_complete_ratio = (float(cache_complete_candidate_days_count) / float(total_cand)) if total_cand > 0 else 0.0
                # 除外日: missing symbol 数を表示
                try:
                    for d in incomplete_days:
                        ms = missing_by_day.get(d) or []
                        if isinstance(ms, list):
                            cache_incomplete_missing_symbol_counts_by_day[str(d)] = int(len(ms))
                except Exception:
                    pass

                print(
                    f"[{now_str()}] random_cache_only candidates (cache complete): "
                    f"{cache_complete_candidate_days_count}/{total_cand} days (ratio={cache_complete_ratio:.2%})"
                )
                if cache_incomplete_days_count > 0:
                    print(f"[{now_str()}] excluded incomplete cache days: {cache_incomplete_days_count}")
                    for d in incomplete_days:
                        mc = int(cache_incomplete_missing_symbol_counts_by_day.get(d, 0))
                        print(f"- {d}: missing_symbols={mc}")
                    print("")

                candidates = list(complete_days)

            rng = random.Random(int(replay_seed)) if replay_seed is not None else random.Random()
            rng.shuffle(candidates)

            # 祝日などを除外するため、先頭銘柄で「データが取れる日」だけ採用
            probe_sym = watch[0]
            picked: list[str] = []
            probe_empty_bar_days = 0
            probe_fetch_failures = 0
            for day_s in candidates:
                if len(picked) >= k:
                    break
                try:
                    if cache_only:
                        # 候補が cache complete なので、そのまま採用（ダミーで truthy にする）
                        bs = [1]
                    else:
                        bs, _m = load_or_fetch_intraday_1m_for_replay_day(
                            session,
                            probe_sym,
                            day_s,
                            intraday_1m_counters,
                        )
                    if bs:
                        picked.append(day_s)
                    else:
                        probe_empty_bar_days += 1
                except Exception:
                    probe_fetch_failures += 1
                    continue

            replay_dates_jst = sorted(picked)
            # Replay前に cache coverage を検証（watch銘柄のみ・指数は除外）
            replay_cache_coverage_validator = validate_intraday_1m_cache_coverage_for_replay_days(
                symbols=list(watch),
                replay_days_jst=list(replay_dates_jst),
            )
            if replay_dates_jst:
                cov = replay_cache_coverage_validator
                print(
                    f"[{now_str()}] replay cache coverage: {int(cov.get('covered_days') or 0)}/{int(cov.get('total_days') or 0)} days "
                    f"(ratio={float(cov.get('coverage_ratio') or 0.0):.2%})"
                )
                miss = cov.get("missing_days") or []
                if miss:
                    print(f"[{now_str()}] missing cache days:")
                    for d in miss:
                        print(f"- {d}")
                    print("")
            replay_random_pick_meta = {
                "yahoo_1m_history_days_assumed": int(YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS),
                "yahoo_1m_calendar_window_reference_jst": {
                    "start": _lo_ref.strftime("%Y-%m-%d"),
                    "end": _hi_ref.strftime("%Y-%m-%d"),
                },
                "weekday_candidates_in_pool": int(n_weekdays_in_pool),
                "replay_random_requested_days": int(k),
                "replay_random_probe_symbol": str(probe_sym),
                "replay_random_probe_empty_bar_days": int(probe_empty_bar_days),
                "replay_random_probe_fetch_failures": int(probe_fetch_failures),
                "replay_random_picked_count": int(len(replay_dates_jst)),
                "cache_only": bool(cache_only),
                "cache_complete_candidate_days_count": int(cache_complete_candidate_days_count),
                "cache_incomplete_days_count": int(cache_incomplete_days_count),
                "cache_complete_ratio": float(cache_complete_ratio),
                "cache_incomplete_missing_symbol_counts_by_day": dict(cache_incomplete_missing_symbol_counts_by_day),
                "cache_coverage": dict(replay_cache_coverage_validator),
                "intraday_1m_cache_stats_after_probe": {
                    "cache_hit": int(intraday_1m_counters["cache_hit"]),
                    "cache_miss": int(intraday_1m_counters["cache_miss"]),
                    "yahoo_fetch": int(intraday_1m_counters["yahoo_fetch"]),
                    "yahoo_1m_window_out": int(intraday_1m_counters["yahoo_1m_window_out"]),
                },
            }
            if len(replay_dates_jst) < k:
                print(
                    f"[{now_str()}] ランダム抽出不足: picked={len(replay_dates_jst)}/{k} "
                    f"（probe: empty_bar試行={probe_empty_bar_days}, fetch失敗={probe_fetch_failures}; "
                    f"intraday cache_hit={intraday_1m_counters['cache_hit']}, cache_miss={intraday_1m_counters['cache_miss']}, "
                    f"yahoo_fetch={intraday_1m_counters['yahoo_fetch']}, "
                    f"yahoo_1m_window_out={intraday_1m_counters['yahoo_1m_window_out']}）"
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
                        bs, mt = load_or_fetch_intraday_1m_for_replay_day(
                            session,
                            sym,
                            day_s,
                            intraday_1m_counters,
                        )
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

        # paper_trade: Yahoo 1m 読み込み直後 — 未来の足を除外（replay signal 生成より前）
        if paper_trade_mode:
            _now_jst_pt = datetime.now(JST)
            bars_by_symbol, _rm_future, _mx_allow = _paper_trade_filter_future_1m_bars(
                bars_by_symbol,
                now_jst=_now_jst_pt,
            )
            _cj = _now_jst_pt.strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[paper_trade] current_jst={_cj} "
                f"max_allowed_candle={_mx_allow} "
                f"filtered_future_candles={int(_rm_future)}"
            )

        if (not paper_trade_mode) and (
            replay_dates_jst or any(int(intraday_1m_counters[k]) for k in intraday_1m_counters)
        ):
            print("=== Replay 1分足キャッシュ ===")
            print(f"- cache_hit: {intraday_1m_counters['cache_hit']}")
            print(f"- cache_miss: {intraday_1m_counters['cache_miss']}")
            print(f"- yahoo_fetch: {intraday_1m_counters['yahoo_fetch']}")
            print(f"- yahoo_1m_window_out: {intraday_1m_counters['yahoo_1m_window_out']}")
            print("")

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
        if not paper_trade_mode:
            print("=== Replay対象 ===")
            print(f"- 対象日(目安): {_fmt_dt_jst(start_utc)[:10]} ～ {_fmt_dt_jst(end_utc)[:10]}")
            print(f"- 開始時刻(JST): {_fmt_dt_jst(start_utc)}")
            print(f"- 終了時刻(JST): {_fmt_dt_jst(end_utc)}")
            print(f"- 対象銘柄: {', '.join(sorted(bars_by_symbol.keys()))}\n")

        # -----------------------------
        # Replay日付の実効日数/キャッシュcoverage（ユーザー要望）
        # -----------------------------
        effective_replay_days_count = _effective_replay_days_count_from_bars(
            bars_by_symbol=bars_by_symbol,
            base_symbols=set([s for s in bars_by_symbol.keys() if s not in set(index_syms)]),
        )
        cache_cov = replay_cache_coverage_validator if isinstance(replay_cache_coverage_validator, dict) else {}
        cache_coverage_ratio = float(cache_cov.get("coverage_ratio") or 0.0) if cache_cov else 0.0

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
                            if replay_range_label not in FIXED_RANDOM_REPLAY_LABELS:
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
                            # hold_minutes（exitが無い場合は None）
                            hold_minutes = None
                            try:
                                et = getattr(s, "exit_time_utc", None)
                                st = getattr(s, "signal_time_utc", None)
                                if isinstance(et, datetime) and isinstance(st, datetime):
                                    if et.tzinfo is None:
                                        et = et.replace(tzinfo=timezone.utc)
                                    if st.tzinfo is None:
                                        st = st.replace(tzinfo=timezone.utc)
                                    hold_minutes = float((et - st).total_seconds() / 60.0)
                            except Exception:
                                hold_minutes = None
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
                                "entry_time_bucket": str(getattr(s, "time_bucket_jst", "") or bucket),
                                "signal_price": float(s.signal_price),
                                "entry_price": float(s.entry_price),
                                "stop_price": float(s.stop_price),
                                "take_price": float(s.take_price),
                                "max_price_after": float(s.max_price_after),
                                "min_price_after": float(s.min_price_after),
                                "last_price_after": float(s.last_price_after),
                                "max_profit_pct": float(s.max_profit_pct()),
                                "max_drawdown_pct": float(s.max_drawdown_pct()),
                                "max_profit_pct_during_trade": float(s.max_profit_pct()),
                                "max_drawdown_pct_during_trade": float(s.max_drawdown_pct()),
                                "hold_minutes": hold_minutes,
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
                                "entry_vwap_distance_pct": (
                                    float(getattr(s, "vwap_distance_pct", 0.0))
                                    if isinstance(getattr(s, "vwap_distance_pct", None), (int, float))
                                    else None
                                ),
                                "gap_pct": (
                                    float(getattr(s, "gap_pct", 0.0))
                                    if isinstance(getattr(s, "gap_pct", None), (int, float))
                                    else None
                                ),
                                "open_5m_return_pct": (
                                    float(getattr(s, "open_5m_return_pct", 0.0))
                                    if isinstance(getattr(s, "open_5m_return_pct", None), (int, float))
                                    else None
                                ),
                                "first_30m_volume_ratio": (
                                    float(getattr(s, "first_30m_volume_ratio", 0.0))
                                    if isinstance(getattr(s, "first_30m_volume_ratio", None), (int, float))
                                    else None
                                ),
                                "rising_ratio": (
                                    float(getattr(s, "rising_ratio", 0.0))
                                    if isinstance(getattr(s, "rising_ratio", None), (int, float))
                                    else None
                                ),
                                "high_update_count_before_entry": (
                                    int(getattr(s, "high_update_count_before_entry", 0))
                                    if isinstance(getattr(s, "high_update_count_before_entry", None), (int, float))
                                    else None
                                ),
                                "first_30m_volume": (
                                    float(getattr(s, "first_30m_volume", 0.0))
                                    if isinstance(getattr(s, "first_30m_volume", None), (int, float))
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

                        # =========================
                        # TIME_BUCKET_ANALYSIS（entry_datetime_jst=signal_time_utc を元に集計）
                        # =========================
                        tb_labels = [
                            "09:00-09:30",
                            "09:30-10:00",
                            "10:00-10:30",
                            "10:30-11:00",
                            "11:00-11:30",
                            "12:30-13:00",
                            "13:00-14:00",
                            "14:00-15:00",
                        ]
                        by_tb: dict[str, list[ReplaySignalEval]] = {k: [] for k in tb_labels}
                        for s in eval_signals:
                            b = _time_bucket_jst_strict(getattr(s, "signal_time_utc", None))
                            if b and b in by_tb:
                                by_tb[b].append(s)

                        def _exit_time_utc_of_signal(s: ReplaySignalEval) -> Optional[datetime]:
                            t = getattr(s, "exit_time_utc", None)
                            if isinstance(t, datetime):
                                return t
                            tt = getattr(s, "trailing_exit_time_utc", None)
                            if isinstance(tt, datetime):
                                return tt
                            return None

                        time_bucket_analysis: dict[str, Any] = {}
                        for b in tb_labels:
                            xs = by_tb.get(b) or []
                            if not xs:
                                continue
                            t = len(xs)
                            w = sum(1 for s in xs if str(getattr(s, "result", "")) == "WIN")
                            l = sum(1 for s in xs if str(getattr(s, "result", "")) == "LOSE")
                            pnl_sum = float(sum(_pnl_yen_100_shares(s) for s in xs))
                            exp_y = (pnl_sum / float(t)) if t > 0 else 0.0
                            lose_pnls = sorted([float(_pnl_yen_100_shares(s)) for s in xs if str(getattr(s, "result", "")) == "LOSE"])
                            lose_worst10_sum = float(sum(lose_pnls[:10])) if lose_pnls else 0.0
                            hold_mins: list[float] = []
                            for s in xs:
                                et = _exit_time_utc_of_signal(s)
                                st = getattr(s, "signal_time_utc", None)
                                if not isinstance(et, datetime) or not isinstance(st, datetime):
                                    continue
                                st2 = st if st.tzinfo is not None else st.replace(tzinfo=timezone.utc)
                                et2 = et if et.tzinfo is not None else et.replace(tzinfo=timezone.utc)
                                hm = (et2 - st2).total_seconds() / 60.0
                                if hm >= 0:
                                    hold_mins.append(float(hm))
                            avg_hold_minutes = (sum(hold_mins) / float(len(hold_mins))) if hold_mins else 0.0
                            time_bucket_analysis[b] = {
                                "signals": int(t),
                                "winrate_pct": float((float(w) / float(t) * 100.0) if t > 0 else 0.0),
                                "total_pnl_yen_100_shares": float(pnl_sum),
                                "avg_expectancy_yen_100_shares": float(exp_y),
                                "lose_worst10_sum_yen_100_shares": float(lose_worst10_sum),
                                "avg_hold_minutes": float(avg_hold_minutes),
                            }

                        # =========================
                        # MARKET_REGIME_ANALYSIS（signalごと分類）
                        # =========================
                        def _parse_reasons(s: ReplaySignalEval) -> set[str]:
                            r = str(getattr(s, "market_reasons", "") or "")
                            xs = [x.strip() for x in r.split(",") if x.strip()]
                            return set(xs)

                        def _drawdown_yen_100_shares_est(s: ReplaySignalEval) -> float:
                            try:
                                dd_pct = float(s.max_drawdown_pct())
                            except Exception:
                                dd_pct = 0.0
                            try:
                                px = float(getattr(s, "signal_price", 0.0) or 0.0)
                            except Exception:
                                px = 0.0
                            return float(px) * 100.0 * (abs(float(dd_pct)) / 100.0)

                        def _regime_labels_for_signal(s: ReplaySignalEval) -> list[str]:
                            labels: set[str] = set()
                            state = str(getattr(s, "market_regime", "") or getattr(s, "market_state", "") or "")
                            if state:
                                labels.add(f"STATE_{state}")
                            rs = _parse_reasons(s)
                            if "TOPIX_CRASH" in rs:
                                labels.add("TOPIX_CRASH")
                            if "TOPIX_WEAK" in rs:
                                labels.add("TOPIX_WEAK")
                            if "BREADTH_WEAK" in rs:
                                labels.add("BREADTH_WEAK")
                            if "afternoon_weak" in rs:
                                labels.add("AFTERNOON_WEAK")
                            if "NIKKEI<VWAP" in rs:
                                labels.add("NIKKEI_BELOW_VWAP")
                            if "fail30m>60%" in rs:
                                labels.add("FAIL_RATE_30M_HIGH")

                            topix_pct = getattr(s, "topix_pct", None)
                            if isinstance(topix_pct, (int, float)):
                                if float(topix_pct) >= 0.5:
                                    labels.add("TOPIX_STRONG")
                                elif float(topix_pct) <= float(WEAK_TOPIX_CHG_PCT_MAX):
                                    labels.add("TOPIX_WEAK_PCT")

                            rising = getattr(s, "rising_ratio", None)
                            if isinstance(rising, (int, float)):
                                labels.add("RISING_RATIO_GE50" if float(rising) >= 0.5 else "RISING_RATIO_LT50")

                            hm = getattr(s, "hm_now", None)
                            if isinstance(hm, int):
                                if hm < (11 * 60 + 30):
                                    labels.add("MORNING_WEAK" if state in ("WEAK", "CRASH") else "MORNING_STRONG")

                            return sorted(list(labels))

                        market_regime_analysis: dict[str, Any] = {}
                        buckets: dict[str, list[ReplaySignalEval]] = {}
                        for s in eval_signals:
                            for lb in _regime_labels_for_signal(s):
                                buckets.setdefault(lb, []).append(s)

                        for lb, xs in sorted(buckets.items(), key=lambda kv: kv[0]):
                            t = len(xs)
                            if t <= 0:
                                continue
                            w = sum(1 for s in xs if str(getattr(s, "result", "")) == "WIN")
                            pnl_sum = float(sum(_pnl_yen_100_shares(s) for s in xs))
                            exp_y = (pnl_sum / float(t)) if t > 0 else 0.0
                            lose_pnls = sorted([float(_pnl_yen_100_shares(s)) for s in xs if str(getattr(s, "result", "")) == "LOSE"])
                            lose_worst10_sum = float(sum(lose_pnls[:10])) if lose_pnls else 0.0
                            max_dd = float(max((_drawdown_yen_100_shares_est(s) for s in xs), default=0.0))
                            market_regime_analysis[lb] = {
                                "signals": int(t),
                                "winrate_pct": float((float(w) / float(t) * 100.0) if t > 0 else 0.0),
                                "avg_expectancy_yen_100_shares": float(exp_y),
                                "total_pnl_yen_100_shares": float(pnl_sum),
                                "lose_worst10_sum_yen_100_shares": float(lose_worst10_sum),
                                "max_drawdown_yen_100_shares_est": float(max_dd),
                            }

                        rc_eval_by_market_regime: dict[str, Any] = {}
                        for rk_mr in ("STRONG", "NORMAL", "WEAK", "CRASH"):
                            sub_mr = [
                                s
                                for s in eval_signals
                                if str(getattr(s, "position_kind", "BASE") or "BASE") == "BASE"
                                and str(getattr(s, "market_regime", "") or "") == rk_mr
                            ]
                            tm = len(sub_mr)
                            if tm <= 0:
                                rc_eval_by_market_regime[rk_mr] = {
                                    "signals": 0,
                                    "winrate_pct": 0.0,
                                    "avg_expectancy_yen_100_shares": 0.0,
                                    "total_pnl_yen_100_shares": 0.0,
                                    "lose_worst10_sum_yen_100_shares": 0.0,
                                }
                                continue
                            wm = sum(1 for s in sub_mr if str(getattr(s, "result", "")) == "WIN")
                            pnl_m = float(sum(_pnl_yen_100_shares(s) for s in sub_mr))
                            exp_m = (pnl_m / float(tm)) if tm > 0 else 0.0
                            lose_pnls_m = sorted(
                                [float(_pnl_yen_100_shares(s)) for s in sub_mr if str(getattr(s, "result", "")) == "LOSE"]
                            )
                            lw10_m = float(sum(lose_pnls_m[:10])) if lose_pnls_m else 0.0
                            rc_eval_by_market_regime[rk_mr] = {
                                "signals": int(tm),
                                "winrate_pct": float((float(wm) / float(tm) * 100.0) if tm > 0 else 0.0),
                                "avg_expectancy_yen_100_shares": float(exp_m),
                                "total_pnl_yen_100_shares": float(pnl_m),
                                "lose_worst10_sum_yen_100_shares": float(lw10_m),
                            }

                        _sc_snap_bc: list[dict[str, Any]] = []
                        if isinstance(composite_signal_filter_strong_combo_snapshot, dict):
                            _sc_snap_bc = list(composite_signal_filter_strong_combo_snapshot.get("block_conditions") or [])
                        if not _sc_snap_bc:
                            _sc_snap_bc = list(_strong_combo_conds_rt)
                        _combo_filter_payload = _build_combo_filter_analysis_report_payload(
                            enabled=bool(composite_signal_filter_strong_combo_enabled),
                            block_conditions_snapshot=list(_sc_snap_bc),
                            skipped_total=int(strong_combo_filter_skipped_signals_count),
                            skip_reason_counts=dict(strong_combo_filter_skip_reason_counts),
                            virtual_pnl_sum_total=float(strong_combo_filter_virtual_pnl_sum),
                            virtual_count_total=int(strong_combo_filter_virtual_count),
                            virtual_pnl_by_reason=dict(strong_combo_filter_virtual_pnl_by_reason),
                            virtual_count_by_reason=dict(strong_combo_filter_virtual_count_by_reason),
                        )

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
                                **(
                                    _replay_fixed_random_meta_extra(str(replay_range))
                                    if _replay_fixed_random_pool_dates(str(replay_range))
                                    else {}
                                ),
                                "replay_seed": int(replay_seed) if replay_seed is not None else None,
                                **(
                                    {"replay_random_pick_debug": dict(replay_random_pick_meta)}
                                    if replay_random_pick_meta
                                    else {}
                                ),
                                "intraday_1m_cache_stats": dict(intraday_1m_counters),
                                "effective_replay_days_count": int(effective_replay_days_count),
                                "cache_coverage_ratio": float(cache_coverage_ratio),
                                **(
                                    {
                                        "cache_complete_candidate_days_count": int(
                                            (replay_random_pick_meta.get("cache_complete_candidate_days_count") or 0)
                                        ),
                                        "cache_incomplete_days_count": int(
                                            (replay_random_pick_meta.get("cache_incomplete_days_count") or 0)
                                        ),
                                        "cache_complete_ratio": float(
                                            (replay_random_pick_meta.get("cache_complete_ratio") or 0.0)
                                        ),
                                    }
                                    if isinstance(replay_random_pick_meta, dict) and bool(replay_random_pick_meta.get("cache_only"))
                                    else {}
                                ),
                                **(
                                    {"replay_cache_coverage_validator": dict(replay_cache_coverage_validator)}
                                    if isinstance(replay_cache_coverage_validator, dict) and replay_cache_coverage_validator
                                    else {}
                                ),
                                "repeat_run_no": int(replay_repeat_run_no or 0),
                                "repeat_total": int(replay_repeat_total or 0),
                                "batch_stamp": str(batch_stamp),
                                "morning_screen_hhmm_jst": (replay_morning_screen_hhmm or "").strip(),
                                "one_trade_per_symbol_per_day": bool(one_trade_per_symbol_per_day),
                                "market_regime_distribution": dict(market_regime_counts),
                                "rising_ratio_distribution": {
                                    "samples": int(rising_ratio_samples),
                                    "avg": float(rising_ratio_sum / float(rising_ratio_samples)) if rising_ratio_samples > 0 else 0.0,
                                    "min": float(rising_ratio_min) if isinstance(rising_ratio_min, (int, float)) else None,
                                    "max": float(rising_ratio_max) if isinstance(rising_ratio_max, (int, float)) else None,
                                    "lt40_count": int(rising_ratio_lt40_samples),
                                    "lt50_count": int(rising_ratio_lt50_samples),
                                    "ge60_count": int(rising_ratio_ge60_samples),
                                    "lt50_ratio": float(rising_ratio_lt50_samples / float(rising_ratio_samples)) if rising_ratio_samples > 0 else 0.0,
                                },
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
                                "risk_controls": {
                                    "daily_loss_stop_enabled": bool(daily_loss_stop_enabled),
                                    "daily_loss_stop_threshold_yen_100_shares": float(daily_loss_stop_threshold_yen_100_shares),
                                    "daily_loss_stop_trigger_count": int(daily_loss_stop_trigger_count),
                                    "daily_loss_stop_triggered_days": sorted(list(set([str(x) for x in daily_loss_stop_triggered_days if str(x)]))),
                                    "daily_loss_stop_skipped_entries": int(daily_loss_stop_skipped_entries),
                                    "daily_pnl_min_yen_100_shares": float(min(daily_pnl_min_yen_100_by_day.values())) if daily_pnl_min_yen_100_by_day else 0.0,
                                    "max_intraday_drawdown_yen_100_shares": float(
                                        abs(min(daily_pnl_min_yen_100_by_day.values())) if daily_pnl_min_yen_100_by_day else 0.0
                                    ),
                                    "avg_daily_drawdown_yen_100_shares": float(
                                        (
                                            sum(abs(float(v)) for v in daily_pnl_min_yen_100_by_day.values())
                                            / float(len(daily_pnl_min_yen_100_by_day))
                                        )
                                        if daily_pnl_min_yen_100_by_day
                                        else 0.0
                                    ),
                                    "daily_loss_stop_analysis": [
                                        {
                                            "trigger_day_jst": str(day),
                                            "trigger_datetime_jst": (
                                                daily_loss_stop_trigger_dt_jst_by_day.get(day).strftime("%Y-%m-%d %H:%M:%S")
                                                if isinstance(daily_loss_stop_trigger_dt_jst_by_day.get(day), datetime)
                                                else ""
                                            ),
                                            "pnl_before_trigger_yen_100_shares": float(
                                                daily_loss_stop_pnl_at_trigger_by_day.get(day, 0.0)
                                            ),
                                            "skipped_entries_count_after_trigger": int(
                                                daily_loss_stop_skipped_entries_by_day.get(day, 0)
                                            ),
                                            "skipped_entries_virtual_pnl_sum_yen_100_shares": float(
                                                daily_loss_stop_virtual_pnl_sum_by_day.get(day, 0.0)
                                            ),
                                            "skipped_entries_virtual_winrate_pct": float(
                                                (
                                                    float(daily_loss_stop_virtual_win_by_day.get(day, 0))
                                                    / float(
                                                        int(daily_loss_stop_virtual_win_by_day.get(day, 0))
                                                        + int(daily_loss_stop_virtual_lose_by_day.get(day, 0))
                                                    )
                                                    * 100.0
                                                )
                                                if (
                                                    int(daily_loss_stop_virtual_win_by_day.get(day, 0))
                                                    + int(daily_loss_stop_virtual_lose_by_day.get(day, 0))
                                                )
                                                > 0
                                                else 0.0
                                            ),
                                            "prevented_loss_estimate_yen_100_shares": float(
                                                -float(daily_loss_stop_virtual_pnl_sum_by_day.get(day, 0.0))
                                            ),
                                        }
                                        for day in sorted(list(set([str(x) for x in daily_loss_stop_triggered_days if str(x)])))
                                    ],
                                },
                                "regime_filters": {
                                    "disable_morning_weak": bool(regime_filter_disable_morning_weak),
                                    "disable_rising_ratio_lt50": bool(regime_filter_disable_rising_ratio_lt50),
                                    "disable_topix_weak": bool(regime_filter_disable_topix_weak),
                                    "topix_weak_threshold_pct": float(topix_weak_thr_pct),
                                    "skipped_signals_count": int(regime_filter_skipped_signals_count),
                                    "skip_reason_counts": dict(regime_filter_skip_reason_counts),
                                    "diag": {
                                        "filter_name": (
                                            f"mw={int(bool(regime_filter_disable_morning_weak))},"
                                            f"rlt50={int(bool(regime_filter_disable_rising_ratio_lt50))},"
                                            f"tw={int(bool(regime_filter_disable_topix_weak))},"
                                            f"tw_thr={float(topix_weak_thr_pct):g}"
                                        ),
                                        "checked_count": int(regime_filter_diag_checked_count),
                                        "skipped_count": int(regime_filter_diag_skipped_count),
                                        "passed_count": int(regime_filter_diag_passed_count),
                                        "skip_ratio": float(
                                            (float(regime_filter_diag_skipped_count) / float(regime_filter_diag_checked_count))
                                            if int(regime_filter_diag_checked_count) > 0
                                            else 0.0
                                        ),
                                        "sample_skipped": list(regime_filter_diag_sample_skipped),
                                    },
                                    "topix_weak_virtual_analysis": {
                                        "skipped_signals_count": int(regime_topix_weak_virtual_count),
                                        "winrate_pct": float(
                                            (float(regime_topix_weak_virtual_win) / float(regime_topix_weak_virtual_win + regime_topix_weak_virtual_lose) * 100.0)
                                            if (regime_topix_weak_virtual_win + regime_topix_weak_virtual_lose) > 0
                                            else 0.0
                                        ),
                                        "avg_expectancy_yen_100_shares": float(
                                            (float(regime_topix_weak_virtual_pnl_sum) / float(regime_topix_weak_virtual_count))
                                            if int(regime_topix_weak_virtual_count) > 0
                                            else 0.0
                                        ),
                                        "total_pnl_yen_100_shares": float(regime_topix_weak_virtual_pnl_sum),
                                        "prevented_loss_estimate_yen_100_shares": float(-float(regime_topix_weak_virtual_pnl_sum)),
                                        "if_not_skipped_estimate": {
                                            "total_signals": int(int(len(eval_signals)) + int(regime_topix_weak_virtual_count)),
                                            "total_pnl_yen_100_shares": float(
                                                float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                + float(regime_topix_weak_virtual_pnl_sum)
                                            ),
                                            "avg_expectancy_yen_100_shares": float(
                                                (
                                                    float(
                                                        float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                        + float(regime_topix_weak_virtual_pnl_sum)
                                                    )
                                                    / float(int(len(eval_signals)) + int(regime_topix_weak_virtual_count))
                                                )
                                                if (int(len(eval_signals)) + int(regime_topix_weak_virtual_count)) > 0
                                                else 0.0
                                            ),
                                        },
                                    },
                                },
                                "signal_filters": {
                                    "disable_gap_ge_pct": bool(signal_filter_disable_gap_ge_pct),
                                    "gap_ge_threshold_pct": float(signal_filter_gap_ge_threshold_pct),
                                    "disable_vwap_distance_ge_pct": bool(signal_filter_disable_vwap_distance_ge_pct),
                                    "vwap_distance_ge_threshold_pct": float(signal_filter_vwap_distance_ge_threshold_pct),
                                    "disable_entry_after_hhmm": bool(signal_filter_disable_entry_after_hhmm),
                                    "entry_after_hhmm": str(signal_filter_entry_after_hhmm),
                                    "skipped_signals_count": int(signal_filters_skipped_signals_count),
                                    "skip_reason_counts": dict(signal_filters_skip_reason_counts),
                                    "virtual_pnl_analysis": {
                                        "skipped_signals_count": int(
                                            int(signal_filters_virtual_count) + int(composite_signal_filter_virtual_count)
                                        ),
                                        "winrate_pct": float(
                                            (
                                                float(signal_filters_virtual_win + composite_signal_filter_virtual_win)
                                                / float(
                                                    signal_filters_virtual_win
                                                    + signal_filters_virtual_lose
                                                    + composite_signal_filter_virtual_win
                                                    + composite_signal_filter_virtual_lose
                                                )
                                                * 100.0
                                            )
                                            if (
                                                signal_filters_virtual_win
                                                + signal_filters_virtual_lose
                                                + composite_signal_filter_virtual_win
                                                + composite_signal_filter_virtual_lose
                                            )
                                            > 0
                                            else 0.0
                                        ),
                                        "avg_expectancy_yen_100_shares": float(
                                            (
                                                float(signal_filters_virtual_pnl_sum + composite_signal_filter_virtual_pnl_sum)
                                                / float(
                                                    int(signal_filters_virtual_count)
                                                    + int(composite_signal_filter_virtual_count)
                                                )
                                            )
                                            if (
                                                int(signal_filters_virtual_count)
                                                + int(composite_signal_filter_virtual_count)
                                            )
                                            > 0
                                            else 0.0
                                        ),
                                        "total_pnl_yen_100_shares": float(
                                            float(signal_filters_virtual_pnl_sum)
                                            + float(composite_signal_filter_virtual_pnl_sum)
                                        ),
                                        "prevented_loss_estimate_yen_100_shares": float(
                                            -float(signal_filters_virtual_pnl_sum + composite_signal_filter_virtual_pnl_sum)
                                        ),
                                        "if_not_skipped_estimate": {
                                            "total_signals": int(
                                                int(len(eval_signals))
                                                + int(signal_filters_virtual_count)
                                                + int(composite_signal_filter_virtual_count)
                                            ),
                                            "total_pnl_yen_100_shares": float(
                                                float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                + float(signal_filters_virtual_pnl_sum)
                                                + float(composite_signal_filter_virtual_pnl_sum)
                                            ),
                                            "avg_expectancy_yen_100_shares": float(
                                                (
                                                    float(
                                                        float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                        + float(signal_filters_virtual_pnl_sum)
                                                        + float(composite_signal_filter_virtual_pnl_sum)
                                                    )
                                                    / float(
                                                        int(len(eval_signals))
                                                        + int(signal_filters_virtual_count)
                                                        + int(composite_signal_filter_virtual_count)
                                                    )
                                                )
                                                if (
                                                    int(len(eval_signals))
                                                    + int(signal_filters_virtual_count)
                                                    + int(composite_signal_filter_virtual_count)
                                                )
                                                > 0
                                                else 0.0
                                            ),
                                        },
                                    },
                                    "composite_signal_filters": {
                                        "weak_risk_filter": str(composite_signal_filter_weak_risk_filter or ""),
                                        "strong_risk_filter": str(composite_signal_filter_strong_risk_filter or ""),
                                        "strong_vwap_ge_threshold_pct": float(composite_signal_filter_strong_vwap_ge_threshold_pct),
                                        "disable_state_weak_and_vwap_ge_pct": bool(
                                            composite_signal_filter_disable_weak_vwap_ge
                                        ),
                                        "state_weak_vwap_ge_threshold_pct": float(
                                            composite_signal_filter_weak_vwap_ge_threshold_pct
                                        ),
                                        "disable_state_weak_and_gap_ge_pct": bool(composite_signal_filter_disable_weak_gap_ge),
                                        "state_weak_gap_ge_threshold_pct": float(composite_signal_filter_weak_gap_ge_threshold_pct),
                                        "skipped_signals_count": int(composite_signal_filter_skipped_signals_count),
                                        "skip_reason_counts": dict(composite_signal_filter_skip_reason_counts),
                                        "virtual_pnl_analysis": {
                                            "skipped_signals_count": int(composite_signal_filter_virtual_count),
                                            "winrate_pct": float(
                                                (
                                                    float(composite_signal_filter_virtual_win)
                                                    / float(composite_signal_filter_virtual_win + composite_signal_filter_virtual_lose)
                                                    * 100.0
                                                )
                                                if (composite_signal_filter_virtual_win + composite_signal_filter_virtual_lose) > 0
                                                else 0.0
                                            ),
                                            "avg_expectancy_yen_100_shares_if_skipped": float(
                                                (
                                                    float(composite_signal_filter_virtual_pnl_sum)
                                                    / float(composite_signal_filter_virtual_count)
                                                )
                                                if int(composite_signal_filter_virtual_count) > 0
                                                else 0.0
                                            ),
                                            "total_pnl_yen_100_shares": float(composite_signal_filter_virtual_pnl_sum),
                                            "prevented_loss_estimate_yen_100_shares": float(
                                                -float(composite_signal_filter_virtual_pnl_sum)
                                            ),
                                        },
                                        "strong_combo_filter": dict(_combo_filter_payload),
                                    },
                                },
                                "regime_controls": {
                                    "enabled": bool(regime_control_enabled),
                                    "config": dict(_rc_snap_report),
                                    "profiles_runtime": dict(_regime_profiles_rt),
                                    "skipped_signals_count": int(regime_control_skipped_signals_count),
                                    "skip_reason_counts": dict(regime_control_skip_reason_counts),
                                    "virtual_pnl_analysis": {
                                        "skipped_signals_count": int(regime_control_virtual_count),
                                        "winrate_pct": float(
                                            (
                                                float(regime_control_virtual_win)
                                                / float(regime_control_virtual_win + regime_control_virtual_lose)
                                                * 100.0
                                            )
                                            if (regime_control_virtual_win + regime_control_virtual_lose) > 0
                                            else 0.0
                                        ),
                                        "avg_expectancy_yen_100_shares_if_skipped": float(
                                            (float(regime_control_virtual_pnl_sum) / float(regime_control_virtual_count))
                                            if int(regime_control_virtual_count) > 0
                                            else 0.0
                                        ),
                                        "total_pnl_yen_100_shares": float(regime_control_virtual_pnl_sum),
                                        "prevented_loss_estimate_yen_100_shares": float(
                                            -float(regime_control_virtual_pnl_sum)
                                        ),
                                        "if_not_skipped_estimate": {
                                            "total_signals": int(int(len(eval_signals)) + int(regime_control_virtual_count)),
                                            "total_pnl_yen_100_shares": float(
                                                float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                + float(regime_control_virtual_pnl_sum)
                                            ),
                                            "avg_expectancy_yen_100_shares": float(
                                                (
                                                    float(
                                                        float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0)
                                                        + float(regime_control_virtual_pnl_sum)
                                                    )
                                                    / float(int(len(eval_signals)) + int(regime_control_virtual_count))
                                                )
                                                if (int(len(eval_signals)) + int(regime_control_virtual_count)) > 0
                                                else 0.0
                                            ),
                                        },
                                    },
                                    "eval_by_market_regime": dict(rc_eval_by_market_regime),
                                },
                            },
                            "by_symbol_summary": {sym: _agg_stats(xs) for sym, xs in by_symbol.items()},
                            "symbol_contribution_analysis": _build_symbol_contribution_analysis(
                                by_symbol_summary={sym: _agg_stats(xs) for sym, xs in by_symbol.items()},
                                total_pnl_yen_100_shares=float((_agg_stats(eval_signals) or {}).get("pnl_yen_100_shares") or 0.0),
                                total_signals=int(len(eval_signals)),
                                exclude_top_n_symbols_list=(1, 2, 3),
                            ),
                            "signal_feature_analysis": _build_signal_feature_analysis_from_signal_dicts(
                                [_signal_to_dict(x) for x in eval_signals]
                            ),
                            "signal_composite_feature_analysis": _build_composite_signal_feature_analysis_from_signal_dicts(
                                [_signal_to_dict(x) for x in eval_signals]
                            ),
                            "combo_filter_analysis": {"strong_combo_filter": dict(_combo_filter_payload)},
                            "strong_loser_analysis": _build_strong_loser_analysis_from_signal_dicts(
                                [_signal_to_dict(x) for x in eval_signals]
                            ),
                            "signal_state_cross_analysis": _build_signal_state_cross_analysis_from_signal_dicts(
                                [_signal_to_dict(x) for x in eval_signals]
                            ),
                            "by_time_bucket_summary": {b: _agg_stats(by_bucket.get(b) or []) for b in bucket_order if (by_bucket.get(b) or [])},
                            "time_bucket_analysis": dict(time_bucket_analysis),
                            "market_regime_analysis": dict(market_regime_analysis),
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
                            "entry_filter_rsi_enabled": bool(entry_filter_rsi_enabled),
                            "entry_filter_rsi_exclude_above": float(entry_filter_rsi_exclude_above),
                            "entry_filter_vwap_distance_enabled": bool(entry_filter_vwap_distance_enabled),
                            "entry_filter_vwap_distance_exclude_above": float(entry_filter_vwap_distance_exclude_above),
                            "entry_filter_atr_pct_enabled": bool(entry_filter_atr_pct_enabled),
                            "entry_filter_atr_pct_exclude_above": float(entry_filter_atr_pct_exclude_above),
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

                        if not paper_trade_mode:
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
                        else:
                            csv_rows_written = int(len(replay_signals))

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

                        if isinstance(paper_trade_collect, dict):
                            try:
                                paper_trade_collect.clear()
                                paper_trade_collect["report"] = report
                                paper_trade_collect["replay_signals"] = list(replay_signals)
                            except Exception:
                                pass

                        # Paper trade: Replay と同一ロジックの集計（report）は取得済み。
                        # results/replay_* への既定出力は行わず、上位の paper_trade ループ側でCSV/Summaryへ記録します。
                        if paper_trade_mode:
                            return 0

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
                        if isinstance(report.get("meta"), dict):
                            md = report.get("meta") or {}
                            if "effective_replay_days_count" in md:
                                lines.append(f"- effective_replay_days_count: {int(md.get('effective_replay_days_count') or 0)}")
                            if "cache_coverage_ratio" in md:
                                lines.append(f"- cache_coverage_ratio: {float(md.get('cache_coverage_ratio') or 0.0):.2%}")
                            if "cache_complete_candidate_days_count" in md:
                                lines.append(f"- cache_complete_candidate_days_count: {int(md.get('cache_complete_candidate_days_count') or 0)}")
                            if "cache_incomplete_days_count" in md:
                                lines.append(f"- cache_incomplete_days_count: {int(md.get('cache_incomplete_days_count') or 0)}")
                            if "cache_complete_ratio" in md:
                                lines.append(f"- cache_complete_ratio: {float(md.get('cache_complete_ratio') or 0.0):.2%}")
                            v = md.get("replay_cache_coverage_validator") or {}
                            if isinstance(v, dict):
                                miss = v.get("missing_days") or []
                                covd = int(v.get("covered_days") or 0)
                                totd = int(v.get("total_days") or 0)
                                if totd > 0 and covd < totd:
                                    lines.append(f"- WARNING: replay cache coverage {covd}/{totd} days (missing cache exists)")
                                    if miss:
                                        lines.append("  missing_cache_days:")
                                        for d in miss:
                                            lines.append(f"    - {d}")
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
                if (not paper_trade_mode) and (
                    (not fast_mode) or bool(replay_fast_verbose) or (pct % 10 == 0)
                ):
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
                        _eb, _ev, _er = _replay_signal_early_exit_kw(
                            s,
                            replay_early_exit_before_stop=bool(replay_early_exit_before_stop),
                            replay_early_exit_vwap=bool(replay_early_exit_vwap),
                            replay_early_exit_recent_low=bool(replay_early_exit_recent_low),
                        )
                        s.update_with_price(
                            time_utc=(q.market_time_utc or datetime.now(tz=timezone.utc)),
                            price=float(q.price),
                            vwap=vwap_now,
                            recent_5m_low=recent_5m_low,
                            early_exit_before_partial_take=bool(_eb),
                            early_exit_vwap=bool(_ev),
                            early_exit_recent_low=bool(_er),
                        )
                        if s.resolved:
                            # 解決済みは active から外す（次ループ以降の更新を省略）
                            try:
                                idxs.remove(idx)
                            except Exception:
                                pass

                            # -----------------------------
                            # 当日累積PnL(円/100株)の更新（risk_controls.daily_loss_stop）
                            # -----------------------------
                            if idx not in resolved_counted_signal_indices:
                                resolved_counted_signal_indices.add(idx)
                                tcur2 = (q.market_time_utc or datetime.now(tz=timezone.utc))
                                if tcur2.tzinfo is None:
                                    tcur2 = tcur2.replace(tzinfo=timezone.utc)
                                day_jst = _day_jst_str(tcur2)
                                pnl_y = float(_pnl_yen_100_shares_replay(s))
                                cur = float(daily_pnl_yen_100_by_day.get(day_jst, 0.0))
                                new_v = cur + pnl_y
                                daily_pnl_yen_100_by_day[day_jst] = new_v
                                mn = float(daily_pnl_min_yen_100_by_day.get(day_jst, 0.0))
                                if day_jst not in daily_pnl_min_yen_100_by_day:
                                    mn = new_v
                                else:
                                    mn = float(min(mn, new_v))
                                daily_pnl_min_yen_100_by_day[day_jst] = mn

                                if bool(daily_loss_stop_enabled):
                                    thr = float(daily_loss_stop_threshold_yen_100_shares)
                                    if new_v <= -abs(thr):
                                        if not bool(daily_loss_stop_triggered_by_day.get(day_jst, False)):
                                            daily_loss_stop_triggered_by_day[day_jst] = True
                                            daily_loss_stop_trigger_dt_jst_by_day[day_jst] = tcur2.astimezone(JST)
                                            daily_loss_stop_pnl_at_trigger_by_day[day_jst] = float(new_v)
                                            daily_loss_stop_trigger_count += 1
                                            daily_loss_stop_triggered_days.append(day_jst)
                                            print(
                                                f"[{now_str()}][STOP] daily_loss_stop triggered day={day_jst} "
                                                f"pnl_yen_100_shares={new_v:+,.0f} <= -{abs(thr):,.0f}"
                                            )
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

                    # daily_loss_stop で除外された“仮想signal”も更新（集計対象外だが、損益推定に使う）
                    vidxs = daily_loss_stop_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(vidxs):
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
                                vidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_virtual_signal_indices:
                                resolved_counted_virtual_signal_indices.add(idx)
                                d = _day_jst_str(s.signal_time_utc)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                daily_loss_stop_virtual_pnl_sum_by_day[d] = float(
                                    daily_loss_stop_virtual_pnl_sum_by_day.get(d, 0.0)
                                ) + pnl_v
                                if str(s.result) == "WIN":
                                    daily_loss_stop_virtual_win_by_day[d] = int(daily_loss_stop_virtual_win_by_day.get(d, 0)) + 1
                                elif str(s.result) == "LOSE":
                                    daily_loss_stop_virtual_lose_by_day[d] = int(daily_loss_stop_virtual_lose_by_day.get(d, 0)) + 1
                    if vidxs:
                        daily_loss_stop_virtual_active_indices_by_symbol[q.symbol] = vidxs
                    else:
                        daily_loss_stop_virtual_active_indices_by_symbol.pop(q.symbol, None)

                    # regime TOPIX_WEAK で除外された“仮想signal”も更新（損益推定に使う）
                    rvidxs = regime_topix_weak_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(rvidxs):
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
                                rvidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_regime_topix_virtual_indices:
                                resolved_counted_regime_topix_virtual_indices.add(idx)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                regime_topix_weak_virtual_pnl_sum += pnl_v
                                if str(s.result) == "WIN":
                                    regime_topix_weak_virtual_win += 1
                                elif str(s.result) == "LOSE":
                                    regime_topix_weak_virtual_lose += 1
                    if rvidxs:
                        regime_topix_weak_virtual_active_indices_by_symbol[q.symbol] = rvidxs
                    else:
                        regime_topix_weak_virtual_active_indices_by_symbol.pop(q.symbol, None)

                    # signal_filters で除外された“仮想signal”も更新（損益推定に使う）
                    sfvidxs = signal_filters_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(sfvidxs):
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
                                sfvidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_signal_filter_virtual_indices:
                                resolved_counted_signal_filter_virtual_indices.add(idx)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                signal_filters_virtual_pnl_sum += pnl_v
                                if str(s.result) == "WIN":
                                    signal_filters_virtual_win += 1
                                elif str(s.result) == "LOSE":
                                    signal_filters_virtual_lose += 1
                    if sfvidxs:
                        signal_filters_virtual_active_indices_by_symbol[q.symbol] = sfvidxs
                    else:
                        signal_filters_virtual_active_indices_by_symbol.pop(q.symbol, None)

                    # composite_signal_filters で除外された“仮想signal”も更新
                    cfvidxs = composite_signal_filter_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(cfvidxs):
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
                                cfvidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_composite_signal_filter_virtual_indices:
                                resolved_counted_composite_signal_filter_virtual_indices.add(idx)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                composite_signal_filter_virtual_pnl_sum += pnl_v
                                if str(s.result) == "WIN":
                                    composite_signal_filter_virtual_win += 1
                                elif str(s.result) == "LOSE":
                                    composite_signal_filter_virtual_lose += 1
                    if cfvidxs:
                        composite_signal_filter_virtual_active_indices_by_symbol[q.symbol] = cfvidxs
                    else:
                        composite_signal_filter_virtual_active_indices_by_symbol.pop(q.symbol, None)

                    # strong_combo_filter で除外された“仮想signal”
                    scfvidxs = strong_combo_filter_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(scfvidxs):
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
                                scfvidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_strong_combo_filter_virtual_indices:
                                resolved_counted_strong_combo_filter_virtual_indices.add(idx)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                strong_combo_filter_virtual_pnl_sum += pnl_v
                                rk_sc = None
                                er_s = str(getattr(s, "excluded_reason", "") or "")
                                for part in er_s.split(" / "):
                                    ps = str(part).strip()
                                    if ps and ps in _strong_combo_reasons_frozen:
                                        rk_sc = ps
                                        break
                                if rk_sc:
                                    strong_combo_filter_virtual_pnl_by_reason[rk_sc] = float(
                                        strong_combo_filter_virtual_pnl_by_reason.get(rk_sc, 0.0)
                                    ) + float(pnl_v)
                                    strong_combo_filter_virtual_count_by_reason[rk_sc] = int(
                                        strong_combo_filter_virtual_count_by_reason.get(rk_sc, 0)
                                    ) + 1
                    if scfvidxs:
                        strong_combo_filter_virtual_active_indices_by_symbol[q.symbol] = scfvidxs
                    else:
                        strong_combo_filter_virtual_active_indices_by_symbol.pop(q.symbol, None)

                    # regime_controls で除外された“仮想signal”（exit_mode は per-signal に反映済み）
                    rcfvidxs = regime_control_virtual_active_indices_by_symbol.get(q.symbol) or []
                    for idx in list(rcfvidxs):
                        s = replay_signals[idx]
                        _eb_rc, _ev_rc, _er_rc = _replay_signal_early_exit_kw(
                            s,
                            replay_early_exit_before_stop=bool(replay_early_exit_before_stop),
                            replay_early_exit_vwap=bool(replay_early_exit_vwap),
                            replay_early_exit_recent_low=bool(replay_early_exit_recent_low),
                        )
                        s.update_with_price(
                            time_utc=(q.market_time_utc or datetime.now(tz=timezone.utc)),
                            price=float(q.price),
                            vwap=vwap_now,
                            recent_5m_low=recent_5m_low,
                            early_exit_before_partial_take=bool(_eb_rc),
                            early_exit_vwap=bool(_ev_rc),
                            early_exit_recent_low=bool(_er_rc),
                        )
                        if s.resolved:
                            try:
                                rcfvidxs.remove(idx)
                            except Exception:
                                pass
                            if idx not in resolved_counted_regime_control_virtual_indices:
                                resolved_counted_regime_control_virtual_indices.add(idx)
                                pnl_v = float(_pnl_yen_100_shares_replay(s))
                                regime_control_virtual_pnl_sum += pnl_v
                                if str(s.result) == "WIN":
                                    regime_control_virtual_win += 1
                                elif str(s.result) == "LOSE":
                                    regime_control_virtual_lose += 1
                    if rcfvidxs:
                        regime_control_virtual_active_indices_by_symbol[q.symbol] = rcfvidxs
                    else:
                        regime_control_virtual_active_indices_by_symbol.pop(q.symbol, None)

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

                            # 当日停止チェック（risk_controls.daily_loss_stop）
                            stopped_today = bool(daily_loss_stop_enabled) and bool(daily_loss_stop_triggered_by_day.get(day_jst, False))
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
                if should_print and (not paper_trade_mode):
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
                # market features（analysis用）
                rising_ratio = 0.0
                high_ratio = 0.0
                fail_rate30 = 0.0
                brk_ratio = 0.0
                below_ratio = 0.0
                hm_now = 0
                topix_weak_thr_pct = (
                    float(regime_filter_topix_weak_threshold_pct)
                    if isinstance(regime_filter_topix_weak_threshold_pct, (int, float))
                    else float(WEAK_TOPIX_CHG_PCT_MAX)
                )
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
                    elif topix_chg_ok and (float(CRASH_TOPIX_CHG_PCT_MAX) < float(topix_chg) <= float(topix_weak_thr_pct)):
                        # WEAK: -1.5% < TOPIX <= threshold
                        market_reasons.append("TOPIX_WEAK")
                    if (tot > 0 and rising_ratio <= float(CRASH_RISING_RATIO_MAX)) and (tot2 > 0 and high_ratio <= float(CRASH_HIGH_RATIO_MAX)):
                        market_reasons.append("BREADTH_WEAK")

                    if crash:
                        market_regime = "CRASH"
                    elif market_reasons:
                        market_regime = "WEAK"
                    elif (
                        (not bool(fallback_used))
                        and bool(topix_chg_ok)
                        and (topix_chg is not None)
                        and float(topix_chg) >= float(STRONG_TOPIX_CHG_PCT_MIN)
                    ):
                        market_regime = "STRONG"
                    else:
                        market_regime = "NORMAL"
                except Exception:
                    market_regime = "NORMAL"
                    market_reasons = []
                market_regime_last = str(market_regime)
                # 分布カウント（デバッグ用）
                try:
                    market_regime_counts[market_regime_last] = int(market_regime_counts.get(market_regime_last, 0)) + 1
                    rr0 = float(rising_ratio) if isinstance(rising_ratio, (int, float)) else 0.0
                    rising_ratio_samples += 1
                    rising_ratio_sum += rr0
                    rising_ratio_min = rr0 if rising_ratio_min is None else float(min(float(rising_ratio_min), rr0))
                    rising_ratio_max = rr0 if rising_ratio_max is None else float(max(float(rising_ratio_max), rr0))
                    if rr0 < 0.5:
                        rising_ratio_lt50_samples += 1
                    if rr0 < 0.4:
                        rising_ratio_lt40_samples += 1
                    if rr0 >= 0.6:
                        rising_ratio_ge60_samples += 1
                except Exception:
                    pass

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
                                    "market_reasons": list([str(x) for x in (market_reasons or [])]),
                                    "rising_ratio": float(rising_ratio),
                                    "high_ratio": float(high_ratio),
                                    "fail_rate30": float(fail_rate30),
                                    "brk_ratio": float(brk_ratio),
                                    "below_ratio": float(below_ratio),
                                    "hm_now": int(hm_now),
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
                                    # 後場制限（WEAK時のみ）: regime_controls 有効時は時間帯前提を外す
                                    if not bool(regime_control_enabled):
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

                            # 当日停止（risk_controls.daily_loss_stop）
                            daily_stop = bool(daily_loss_stop_enabled) and bool(daily_loss_stop_triggered_by_day.get(day_jst, False))
                            if daily_stop and (day_jst, "__DAY__") not in stop_logged_by_day_symbol:
                                stop_logged_by_day_symbol.add((day_jst, "__DAY__"))
                                v = float(daily_pnl_yen_100_by_day.get(day_jst, 0.0))
                                print(
                                    f"[{now_str()}][STOP] {day_jst} 当日累積PnLが {v:+,.0f}円(100株) になったため、"
                                    "この日の新規ENTRY/ADDを停止します。"
                                )

                            # 重複エントリー制限
                            exclude = False
                            exclude_reason = ""
                            excluded_by_daily_loss_stop = False
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
                                exclude_reason = "当日停止（daily_loss_stop）により新規ENTRY/ADD停止"
                                daily_loss_stop_skipped_entries += 1
                                excluded_by_daily_loss_stop = True
                                daily_loss_stop_skipped_entries_by_day[day_jst] = int(
                                    daily_loss_stop_skipped_entries_by_day.get(day_jst, 0)
                                ) + 1

                            # regime filter AB test（ENTRY前に skip）
                            if not exclude:
                                regime_filter_diag_checked_count += 1
                                rf_reasons: list[str] = []
                                if bool(regime_filter_disable_morning_weak):
                                    # 「前場弱い」を market_regime != NORMAL のように広げすぎない
                                    # - CRASH は無条件で弱い
                                    # - WEAK は「TOPIX/BREADTH/rising 等の明確な弱材料」があるときだけ弱い
                                    rs2 = set([str(x) for x in (market_reasons or [])])
                                    morning_weak_hit = False
                                    if int(hm_now) < (11 * 60 + 30):
                                        if str(market_regime) == "CRASH":
                                            morning_weak_hit = True
                                        elif (
                                            ("TOPIX_CRASH" in rs2)
                                            or ("TOPIX_WEAK" in rs2)
                                            or ("BREADTH_WEAK" in rs2)
                                            or ("NIKKEI<VWAP" in rs2)
                                            or ("fail30m>60%" in rs2)
                                            or any(str(x).startswith("rising<") for x in rs2)
                                        ):
                                            morning_weak_hit = True
                                    if morning_weak_hit:
                                        rf_reasons.append("REGIME_FILTER_MORNING_WEAK")
                                if bool(regime_filter_disable_rising_ratio_lt50):
                                    # rising_ratio は 0..1 を想定。0..100 で来た場合も吸収。
                                    rr = rising_ratio
                                    rr2 = None
                                    if isinstance(rr, (int, float)):
                                        rr2 = float(rr)
                                        if rr2 > 1.0 and rr2 <= 100.0:
                                            rr2 = rr2 / 100.0
                                    if rr2 is not None and rr2 < 0.5:
                                        rf_reasons.append("REGIME_FILTER_RISING_LT50")
                                if bool(regime_filter_disable_topix_weak):
                                    if isinstance(topix_chg, (int, float)) and float(topix_chg) <= float(topix_weak_thr_pct):
                                        rf_reasons.append("REGIME_FILTER_TOPIX_WEAK")
                                    elif isinstance(market_reasons, list) and ("TOPIX_WEAK" in market_reasons):
                                        rf_reasons.append("REGIME_FILTER_TOPIX_WEAK")

                                if rf_reasons:
                                    exclude = True
                                    exclude_reason = " / ".join(rf_reasons)
                                    regime_filter_skipped_signals_count += 1
                                    regime_filter_diag_skipped_count += 1
                                    for rr in rf_reasons:
                                        continue_reason_counts[rr] = int(continue_reason_counts.get(rr, 0)) + 1
                                        regime_filter_skip_reason_counts[rr] = int(regime_filter_skip_reason_counts.get(rr, 0)) + 1
                                    if len(regime_filter_diag_sample_skipped) < int(REGIME_FILTER_DIAG_SAMPLE_MAX):
                                        regime_filter_diag_sample_skipped.append(
                                            {
                                                "symbol": str(q.symbol),
                                                "time_jst": sig_time.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                                                "market_regime": str(market_regime),
                                                "rising_ratio": float(rising_ratio) if isinstance(rising_ratio, (int, float)) else None,
                                                "topix_pct": float(topix_chg) if isinstance(topix_chg, (int, float)) else None,
                                                "reason": str(exclude_reason),
                                            }
                                        )
                                    if len(continue_before_append_rows) < 500:
                                        continue_before_append_rows.append(
                                            {
                                                "reason": "REGIME_FILTER",
                                                "symbol": str(q.symbol),
                                                "day_jst": str(day_jst),
                                                "time_jst": sig_time.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                                                "market_state": str(market_regime),
                                                "market_reasons": ",".join([str(x) for x in (market_reasons or [])]),
                                                "rising_ratio": float(rising_ratio) if isinstance(rising_ratio, (int, float)) else None,
                                                "topix_pct": float(topix_chg) if isinstance(topix_chg, (int, float)) else None,
                                                "exclude_reason": str(exclude_reason),
                                            }
                                        )
                                else:
                                    regime_filter_diag_passed_count += 1

                            # signal feature based filters（ENTRY前に skip）
                            if not exclude:
                                sf_reasons: list[str] = []

                                # entry after HH:MM
                                if bool(signal_filter_disable_entry_after_hhmm):
                                    try:
                                        t0 = sig_time
                                        if t0.tzinfo is None:
                                            t0 = t0.replace(tzinfo=timezone.utc)
                                        j = t0.astimezone(JST)
                                        hhmm = str(signal_filter_entry_after_hhmm or "").strip()
                                        hh = 0
                                        mm = 0
                                        if ":" in hhmm:
                                            a, b = hhmm.split(":", 1)
                                            hh = int(a)
                                            mm = int(b)
                                        else:
                                            hh = int(hhmm[:2])
                                            mm = int(hhmm[2:]) if len(hhmm) >= 4 else 0
                                        if (j.hour * 60 + j.minute) >= (hh * 60 + mm):
                                            sf_reasons.append("SIGNAL_FILTER_ENTRY_AFTER_HHMM")
                                    except Exception:
                                        pass

                                # gap_pct
                                gap_pct0 = None
                                if bool(signal_filter_disable_gap_ge_pct):
                                    try:
                                        sym0 = str(q.symbol)
                                        dk = day_jst
                                        pc0 = prev_close_by_day.get(f"{sym0}::{dk}")
                                        if pc0 is None:
                                            pc0 = prev_close_by_day.get(f"{sym0}::INIT")
                                        bars0 = bars_by_symbol.get(sym0) or []
                                        day_bars0: list[ReplayBar] = []
                                        for bb in bars0:
                                            bt = bb.timestamp_utc
                                            if bt.tzinfo is None:
                                                bt = bt.replace(tzinfo=timezone.utc)
                                            if bt.astimezone(JST).strftime("%Y-%m-%d") != dk:
                                                continue
                                            day_bars0.append(bb)
                                        if day_bars0:
                                            day_bars0 = sorted(day_bars0, key=lambda x: x.timestamp_utc)
                                            day_open = float(day_bars0[0].open)
                                            if isinstance(pc0, (int, float)) and float(pc0) > 0:
                                                gap_pct0 = float((day_open - float(pc0)) / float(pc0) * 100.0)
                                                if float(gap_pct0) >= float(signal_filter_gap_ge_threshold_pct):
                                                    sf_reasons.append("SIGNAL_FILTER_GAP_GE")
                                    except Exception:
                                        pass

                                # vwap distance
                                if bool(signal_filter_disable_vwap_distance_ge_pct):
                                    try:
                                        if isinstance(vwap_dist_pct, (int, float)) and float(vwap_dist_pct) >= float(
                                            signal_filter_vwap_distance_ge_threshold_pct
                                        ):
                                            sf_reasons.append("SIGNAL_FILTER_VWAP_DIST_GE")
                                    except Exception:
                                        pass

                                if sf_reasons:
                                    exclude = True
                                    exclude_reason = " / ".join(sf_reasons)
                                    signal_filters_skipped_signals_count += 1
                                    for rr in sf_reasons:
                                        continue_reason_counts[rr] = int(continue_reason_counts.get(rr, 0)) + 1
                                        signal_filters_skip_reason_counts[rr] = int(signal_filters_skip_reason_counts.get(rr, 0)) + 1

                                # composite signal filters（market_regime==WEAK のときのみ gap / VWAP距離 で除外）
                                if not exclude and str(market_regime) == "WEAK":
                                    csf_reasons: list[str] = []
                                    gap_pct_weak = gap_pct0
                                    if gap_pct_weak is None:
                                        try:
                                            sym_w = str(q.symbol)
                                            dk_w = day_jst
                                            pc_w = prev_close_by_day.get(f"{sym_w}::{dk_w}")
                                            if pc_w is None:
                                                pc_w = prev_close_by_day.get(f"{sym_w}::INIT")
                                            bars_w = bars_by_symbol.get(sym_w) or []
                                            day_bars_w: list[ReplayBar] = []
                                            for bb in bars_w:
                                                bt = bb.timestamp_utc
                                                if bt.tzinfo is None:
                                                    bt = bt.replace(tzinfo=timezone.utc)
                                                if bt.astimezone(JST).strftime("%Y-%m-%d") != dk_w:
                                                    continue
                                                day_bars_w.append(bb)
                                            if day_bars_w:
                                                day_bars_w = sorted(day_bars_w, key=lambda x: x.timestamp_utc)
                                                day_open_w = float(day_bars_w[0].open)
                                                if isinstance(pc_w, (int, float)) and float(pc_w) > 0:
                                                    gap_pct_weak = float((day_open_w - float(pc_w)) / float(pc_w) * 100.0)
                                        except Exception:
                                            pass

                                    wrf_mode = str(composite_signal_filter_weak_risk_filter or "").strip()
                                    if wrf_mode:
                                        thr_v = float(composite_signal_filter_weak_vwap_ge_threshold_pct)
                                        thr_g = float(composite_signal_filter_weak_gap_ge_threshold_pct)
                                        hit_v = isinstance(vwap_dist_pct, (int, float)) and float(vwap_dist_pct) >= thr_v
                                        hit_g = gap_pct_weak is not None and float(gap_pct_weak) >= thr_g
                                        try:
                                            if wrf_mode == "weak_vwap_ge_15_only":
                                                if hit_v:
                                                    csf_reasons.append("WEAK_VWAP_GE_15")
                                            elif wrf_mode == "weak_gap_ge_3_only":
                                                if hit_g:
                                                    csf_reasons.append("WEAK_GAP_GE_3")
                                            elif wrf_mode == "weak_vwap_ge_15_and_gap_ge_3":
                                                if hit_v and hit_g:
                                                    csf_reasons.append("WEAK_VWAP_AND_GAP")
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            if bool(composite_signal_filter_disable_weak_gap_ge):
                                                if gap_pct_weak is not None and float(gap_pct_weak) >= float(
                                                    composite_signal_filter_weak_gap_ge_threshold_pct
                                                ):
                                                    csf_reasons.append("COMPOSITE_WEAK_GAP_GE")
                                        except Exception:
                                            pass

                                        try:
                                            if bool(composite_signal_filter_disable_weak_vwap_ge):
                                                if isinstance(vwap_dist_pct, (int, float)) and float(vwap_dist_pct) >= float(
                                                    composite_signal_filter_weak_vwap_ge_threshold_pct
                                                ):
                                                    csf_reasons.append("COMPOSITE_WEAK_VWAP_GE")
                                        except Exception:
                                            pass

                                    if csf_reasons:
                                        exclude = True
                                        exclude_reason = " / ".join(csf_reasons)
                                        composite_signal_filter_skipped_signals_count += 1
                                        signal_filters_skipped_signals_count += 1
                                        for rr in csf_reasons:
                                            continue_reason_counts[rr] = int(continue_reason_counts.get(rr, 0)) + 1
                                            signal_filters_skip_reason_counts[rr] = int(
                                                signal_filters_skip_reason_counts.get(rr, 0)
                                            ) + 1
                                            composite_signal_filter_skip_reason_counts[rr] = int(
                                                composite_signal_filter_skip_reason_counts.get(rr, 0)
                                            ) + 1

                                # composite signal filters（market_regime==STRONG かつ VWAP距離>=しきい値で除外）
                                if not exclude and str(market_regime) == "STRONG":
                                    srf_mode = str(composite_signal_filter_strong_risk_filter or "").strip()
                                    if srf_mode:
                                        thr_s = float(composite_signal_filter_strong_vwap_ge_threshold_pct)
                                        hit_s = isinstance(vwap_dist_pct, (int, float)) and float(vwap_dist_pct) >= thr_s
                                        csf_reasons_s: list[str] = []
                                        if hit_s:
                                            if srf_mode == "strong_vwap_ge_15_only":
                                                csf_reasons_s.append("STRONG_VWAP_GE_15")
                                            elif srf_mode == "strong_vwap_ge_12_only":
                                                csf_reasons_s.append("STRONG_VWAP_GE_12")
                                            elif srf_mode == "strong_vwap_ge_10_only":
                                                csf_reasons_s.append("STRONG_VWAP_GE_10")
                                        if csf_reasons_s:
                                            exclude = True
                                            exclude_reason = " / ".join(csf_reasons_s)
                                            composite_signal_filter_skipped_signals_count += 1
                                            signal_filters_skipped_signals_count += 1
                                            for rr in csf_reasons_s:
                                                continue_reason_counts[rr] = int(continue_reason_counts.get(rr, 0)) + 1
                                                signal_filters_skip_reason_counts[rr] = int(
                                                    signal_filters_skip_reason_counts.get(rr, 0)
                                                ) + 1
                                                composite_signal_filter_skip_reason_counts[rr] = int(
                                                    composite_signal_filter_skip_reason_counts.get(rr, 0)
                                                ) + 1

                                # composite strong_combo_filter（高値更新回数 × VWAP距離）
                                if (
                                    not exclude
                                    and bool(composite_signal_filter_strong_combo_enabled)
                                    and _strong_combo_conds_rt
                                ):
                                    hu_now = int(near_high) if isinstance(near_high, (int, float)) else None
                                    hit_reason_sc: Optional[str] = None
                                    for cond in _strong_combo_conds_rt:
                                        try:
                                            if str(market_regime) != str(cond.get("market_regime")):
                                                continue
                                            thr_vc = float(cond.get("entry_vwap_distance_pct_ge") or 999.0)
                                            if hu_now is None:
                                                continue
                                            need_eq = cond.get("high_update_count_before_entry_eq")
                                            need_le = cond.get("high_update_count_before_entry_le")
                                            if need_eq is not None:
                                                if int(hu_now) != int(need_eq):
                                                    continue
                                            elif need_le is not None:
                                                if int(hu_now) > int(need_le):
                                                    continue
                                            else:
                                                continue
                                            if not (
                                                isinstance(vwap_dist_pct, (int, float))
                                                and float(vwap_dist_pct) >= float(thr_vc)
                                            ):
                                                continue
                                            hit_reason_sc = str(cond.get("reason") or "").strip() or "STRONG_COMBO"
                                            break
                                        except Exception:
                                            continue
                                    if hit_reason_sc:
                                        exclude = True
                                        exclude_reason = hit_reason_sc
                                        strong_combo_filter_skipped_signals_count += 1
                                        signal_filters_skipped_signals_count += 1
                                        strong_combo_filter_skip_reason_counts[hit_reason_sc] = int(
                                            strong_combo_filter_skip_reason_counts.get(hit_reason_sc, 0)
                                        ) + 1
                                        continue_reason_counts[hit_reason_sc] = int(
                                            continue_reason_counts.get(hit_reason_sc, 0)
                                        ) + 1
                                        signal_filters_skip_reason_counts[hit_reason_sc] = int(
                                            signal_filters_skip_reason_counts.get(hit_reason_sc, 0)
                                        ) + 1

                                # regime_controls（地合い適応 ENTRY: 許可フラグ・gap/VWAP 上限／時間帯禁止に依存しない）
                                if not exclude and bool(regime_control_enabled):
                                    rprof = _regime_control_profile_for(_regime_profiles_rt, str(market_regime))
                                    rc_reasons: list[str] = []
                                    if not bool(rprof.get("entry_enabled", True)):
                                        rc_reasons.append("REGIME_CONTROL_ENTRY_DISABLED")
                                    max_g = rprof.get("max_gap_pct")
                                    if isinstance(max_g, (int, float)):
                                        g_use = gap_pct0
                                        if g_use is None:
                                            try:
                                                sym_r = str(q.symbol)
                                                dk_r = day_jst
                                                pc_r = prev_close_by_day.get(f"{sym_r}::{dk_r}")
                                                if pc_r is None:
                                                    pc_r = prev_close_by_day.get(f"{sym_r}::INIT")
                                                bars_r = bars_by_symbol.get(sym_r) or []
                                                day_bars_r: list[ReplayBar] = []
                                                for bb in bars_r:
                                                    bt = bb.timestamp_utc
                                                    if bt.tzinfo is None:
                                                        bt = bt.replace(tzinfo=timezone.utc)
                                                    if bt.astimezone(JST).strftime("%Y-%m-%d") != dk_r:
                                                        continue
                                                    day_bars_r.append(bb)
                                                if day_bars_r:
                                                    day_bars_r = sorted(day_bars_r, key=lambda x: x.timestamp_utc)
                                                    dor = float(day_bars_r[0].open)
                                                    if isinstance(pc_r, (int, float)) and float(pc_r) > 0:
                                                        g_use = float((dor - float(pc_r)) / float(pc_r) * 100.0)
                                            except Exception:
                                                pass
                                        if g_use is not None and float(g_use) > float(max_g):
                                            rc_reasons.append("REGIME_CONTROL_GAP_GT")
                                    max_v = rprof.get("max_vwap_distance_pct")
                                    if isinstance(max_v, (int, float)):
                                        if isinstance(vwap_dist_pct, (int, float)) and float(vwap_dist_pct) > float(max_v):
                                            rc_reasons.append("REGIME_CONTROL_VWAP_DIST_GT")

                                    if rc_reasons:
                                        exclude = True
                                        exclude_reason = " / ".join(rc_reasons)
                                        regime_control_skipped_signals_count += 1
                                        for rr in rc_reasons:
                                            continue_reason_counts[rr] = int(continue_reason_counts.get(rr, 0)) + 1
                                            regime_control_skip_reason_counts[rr] = int(
                                                regime_control_skip_reason_counts.get(rr, 0)
                                            ) + 1

                            # ENTRY filters（config / 集計対象外）
                            if not exclude:
                                ef_reasons: list[str] = []
                                if bool(entry_filter_rsi_enabled) and rsi14 is not None and float(rsi14) > float(entry_filter_rsi_exclude_above):
                                    ef_reasons.append(f"ENTRY_FILTER_RSI>{float(entry_filter_rsi_exclude_above):g}")
                                    reject_reason_counts["ENTRY_FILTER_RSI"] = int(reject_reason_counts.get("ENTRY_FILTER_RSI", 0)) + 1
                                if bool(entry_filter_vwap_distance_enabled) and vwap_dist_pct is not None:
                                    if abs(float(vwap_dist_pct)) > float(entry_filter_vwap_distance_exclude_above):
                                        ef_reasons.append(
                                            f"ENTRY_FILTER_VWAP_DIST>{float(entry_filter_vwap_distance_exclude_above):g}%"
                                        )
                                        reject_reason_counts["ENTRY_FILTER_VWAP_DIST"] = int(
                                            reject_reason_counts.get("ENTRY_FILTER_VWAP_DIST", 0)
                                        ) + 1
                                if bool(entry_filter_atr_pct_enabled) and atr_pct is not None and float(atr_pct) > float(
                                    entry_filter_atr_pct_exclude_above
                                ):
                                    ef_reasons.append(f"ENTRY_FILTER_ATR>{float(entry_filter_atr_pct_exclude_above):g}%")
                                    reject_reason_counts["ENTRY_FILTER_ATR"] = int(reject_reason_counts.get("ENTRY_FILTER_ATR", 0)) + 1
                                if ef_reasons:
                                    exclude = True
                                    exclude_reason = " / ".join(ef_reasons)

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
                            else:
                                # daily_loss_stop で除外された signal は「仮想PnL」を計算するため、別のactiveとして追跡
                                if bool(excluded_by_daily_loss_stop):
                                    idx = len(replay_signals) - 1
                                    daily_loss_stop_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                # regime TOPIX_WEAK で除外された signal も「仮想PnL」を計算する
                                if isinstance(exclude_reason, str) and ("REGIME_FILTER_TOPIX_WEAK" in exclude_reason):
                                    idx = len(replay_signals) - 1
                                    regime_topix_weak_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    regime_topix_weak_virtual_count += 1
                                # signal filters で除外された signal も「仮想PnL」を計算する
                                if isinstance(exclude_reason, str) and ("SIGNAL_FILTER_" in exclude_reason):
                                    idx = len(replay_signals) - 1
                                    signal_filters_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    signal_filters_virtual_count += 1
                                # WEAK複合フィルタで除外
                                if _composite_weak_virtual_exclude_reason(str(exclude_reason)):
                                    idx = len(replay_signals) - 1
                                    composite_signal_filter_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    composite_signal_filter_virtual_count += 1
                                if isinstance(exclude_reason, str) and ("REGIME_CONTROL_" in exclude_reason):
                                    idx = len(replay_signals) - 1
                                    regime_control_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    regime_control_virtual_count += 1
                                if _strong_combo_virtual_exclude_reason(str(exclude_reason), _strong_combo_reasons_frozen):
                                    idx = len(replay_signals) - 1
                                    strong_combo_filter_virtual_active_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    strong_combo_filter_virtual_count += 1

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
                                setattr(s, "topix_weak_threshold", float(topix_weak_thr_pct))
                                setattr(s, "market_state", str(market_regime))
                                setattr(s, "crash_blocked", bool(crash_blocked))
                                setattr(s, "market_reasons", ",".join([str(x) for x in (market_reasons or [])]))
                                setattr(s, "rising_ratio", float(rising_ratio))
                                setattr(s, "high_ratio", float(high_ratio))
                                setattr(s, "high_update_count_before_entry", int(near_high) if isinstance(near_high, (int, float)) else None)
                                setattr(s, "fail_rate30", float(fail_rate30))
                                setattr(s, "brk_ratio", float(brk_ratio))
                                setattr(s, "below_ratio", float(below_ratio))
                                setattr(s, "hm_now", int(hm_now))
                                setattr(s, "market_blocked", bool(crash_blocked))
                                setattr(s, "blocked_reason", (",".join([str(x) for x in (market_reasons or [])]) if crash_blocked else ""))
                                setattr(s, "entry_allowed_by_market", bool(not crash_blocked))
                                setattr(s, "entry_allowed", bool((not crash_blocked) and (not exclude)))
                                # regime_controls exit_mode → 保有中の early exit を地合いで上書き
                                if bool(regime_control_enabled):
                                    try:
                                        rpx_sc = _regime_control_profile_for(_regime_profiles_rt, str(market_regime))
                                        em_sc = str(rpx_sc.get("exit_mode", "normal")).lower()
                                        if bool(replay_early_exit_before_stop) and em_sc == "fast":
                                            setattr(s, "regime_early_exit_vwap", True)
                                            setattr(s, "regime_early_exit_recent_low", True)
                                        else:
                                            setattr(s, "regime_early_exit_vwap", bool(replay_early_exit_vwap))
                                            setattr(s, "regime_early_exit_recent_low", bool(replay_early_exit_recent_low))
                                    except Exception:
                                        setattr(s, "regime_early_exit_vwap", bool(replay_early_exit_vwap))
                                        setattr(s, "regime_early_exit_recent_low", bool(replay_early_exit_recent_low))
                                setattr(s, "rsi14", rsi14)
                                setattr(s, "atr14", atr14)
                                setattr(s, "atr_pct", atr_pct)
                                setattr(s, "vwap_distance_pct", vwap_dist_pct)
                                setattr(s, "relative_strength_vs_topix_pct", rs_vs_topix)
                                setattr(s, "vol_spike_ratio", vol_spike_ratio_by_symbol.get(q.symbol))
                                # gap/open_5m/first30m（可能な範囲で計算）
                                try:
                                    sym0 = str(getattr(s, "symbol", "") or "")
                                    dt0 = sig_time
                                    if dt0.tzinfo is None:
                                        dt0 = dt0.replace(tzinfo=timezone.utc)
                                    day_key = dt0.astimezone(JST).strftime("%Y-%m-%d")
                                    prev_close0 = prev_close_by_day.get(f"{sym0}::{day_key}")
                                    if prev_close0 is None:
                                        prev_close0 = prev_close_by_day.get(f"{sym0}::INIT")
                                    bars0 = bars_by_symbol.get(sym0) or []
                                    # 当日バー抽出
                                    day_bars0: list[ReplayBar] = []
                                    for bb in bars0:
                                        bt = bb.timestamp_utc
                                        if bt.tzinfo is None:
                                            bt = bt.replace(tzinfo=timezone.utc)
                                        if bt.astimezone(JST).strftime("%Y-%m-%d") != day_key:
                                            continue
                                        day_bars0.append(bb)
                                    if day_bars0:
                                        day_bars0 = sorted(day_bars0, key=lambda x: x.timestamp_utc)
                                        day_open = float(day_bars0[0].open)
                                        if isinstance(prev_close0, (int, float)) and float(prev_close0) > 0:
                                            setattr(s, "gap_pct", float((day_open - float(prev_close0)) / float(prev_close0) * 100.0))
                                        # open_5m_return_pct（最初の5本があれば）
                                        if len(day_bars0) >= 5 and float(day_open) > 0:
                                            c5 = float(day_bars0[4].close)
                                            setattr(s, "open_5m_return_pct", float((c5 - day_open) / day_open * 100.0))
                                        # first 30m volume（最初の30本=30分）
                                        first30 = day_bars0[:30]
                                        v30 = float(sum(float(x.volume) for x in first30))
                                        setattr(s, "first_30m_volume", float(v30))
                                        avg5 = avg5_by_symbol.get(sym0)
                                        if isinstance(avg5, (int, float)) and float(avg5) > 0:
                                            setattr(s, "first_30m_volume_ratio", float(v30 / float(avg5)))
                                except Exception:
                                    pass
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


def _paper_trade_filter_future_1m_bars(
    bars_by_symbol: dict[str, list[ReplayBar]],
    *,
    now_jst: datetime,
) -> tuple[dict[str, list[ReplayBar]], int, str]:
    """
    paper_trade 用: 1分足は [open, open+1m) で完了するものとみなし、
    終了時刻が現在時刻より後の足（未来の足）は除外する。
    Returns:
      - 銘柄ごとのフィルタ後バー
      - 除外した本数の合計
      - max_allowed_candle: 採用した足のうち最も新しい「始値時刻」(JST) を表示用に整形した文字列（無ければ N/A）
    """
    if now_jst.tzinfo is None:
        now_jst = now_jst.replace(tzinfo=JST)
    now_utc = now_jst.astimezone(timezone.utc)
    out: dict[str, list[ReplayBar]] = {}
    removed = 0
    latest_open_jst: Optional[datetime] = None
    for sym, bars in bars_by_symbol.items():
        kept: list[ReplayBar] = []
        for b in bars:
            t = b.timestamp_utc
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            bar_end = t + timedelta(minutes=1)
            if bar_end <= now_utc:
                kept.append(b)
                oj = t.astimezone(JST)
                if latest_open_jst is None or oj > latest_open_jst:
                    latest_open_jst = oj
            else:
                removed += 1
        out[sym] = kept
    max_allowed = latest_open_jst.strftime("%Y-%m-%d %H:%M:%S") if latest_open_jst else "N/A"
    return out, removed, max_allowed


def _paper_trade_signal_event_time_jst(s: ReplaySignalEval) -> str:
    et = getattr(s, "exit_time_utc", None)
    if bool(getattr(s, "resolved", False)) and isinstance(et, datetime):
        t = et if et.tzinfo is not None else et.replace(tzinfo=timezone.utc)
        return t.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    st = getattr(s, "signal_time_utc", None)
    if isinstance(st, datetime):
        t = st if st.tzinfo is not None else st.replace(tzinfo=timezone.utc)
        return t.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def _paper_trade_pnl_yen_100_shares(s: ReplaySignalEval) -> Optional[float]:
    """
    run_replay 内 `_pnl_yen_100_shares` と同一の100株損益（円）。
    未確定・HOLD 等で replay 集計と同様に数値化できない場合は None（CSV は空欄）。
    """
    try:
        if isinstance(getattr(s, "final_profit_pct", None), (int, float)):
            sp = float(getattr(s, "signal_price", 0.0) or 0.0)
            return sp * 100.0 * (float(s.final_profit_pct) / 100.0)
        res = str(getattr(s, "result", "") or "")
        ep = float(getattr(s, "entry_price", 0.0) or 0.0)
        if res == "WIN":
            return (float(getattr(s, "take_price", 0.0) or 0.0) - ep) * 100.0
        if res == "LOSE":
            return (float(getattr(s, "stop_price", 0.0) or 0.0) - ep) * 100.0
        return None
    except Exception:
        return None


def _paper_trade_row_dict(s: ReplaySignalEval) -> dict[str, str]:
    skipped = bool(getattr(s, "excluded_from_eval", False)) or (not bool(getattr(s, "entry_allowed", True)))
    p1 = str(getattr(s, "excluded_reason", "") or "").strip()
    p2 = str(getattr(s, "blocked_reason", "") or "").strip()
    skip_reason = " / ".join([x for x in (p1, p2) if x])
    tp = getattr(s, "topix_pct", None)
    rr = getattr(s, "rising_ratio", None)
    evw = getattr(s, "vwap_distance_pct", None)
    hu = getattr(s, "high_update_count_before_entry", None)
    ep = float(getattr(s, "entry_price", 0.0) or 0.0)
    xp: Optional[float] = None
    raw_xp = getattr(s, "exit_price", None)
    if isinstance(raw_xp, (int, float)):
        xp = float(raw_xp)
    else:
        te = getattr(s, "trailing_exit_price", None)
        if isinstance(te, (int, float)):
            xp = float(te)
        else:
            lp = getattr(s, "last_price_after", None)
            if isinstance(lp, (int, float)):
                xp = float(lp)
    pnl_opt = _paper_trade_pnl_yen_100_shares(s)
    pnl_cell = f"{float(pnl_opt):.2f}" if isinstance(pnl_opt, float) else ""
    return {
        "datetime_jst": _paper_trade_signal_event_time_jst(s),
        "symbol": str(s.symbol),
        "signal_type": str(getattr(s, "position_kind", "BASE") or "BASE"),
        "entry_price": f"{ep:.4f}",
        "exit_price": f"{float(xp):.4f}" if isinstance(xp, float) else "",
        "exit_reason": str(getattr(s, "exit_reason", "") or ""),
        "pnl_yen_100_shares": pnl_cell,
        "market_regime": str(getattr(s, "market_regime", "") or getattr(s, "market_state", "") or ""),
        "rising_ratio": f"{float(rr):.6f}" if isinstance(rr, (int, float)) else "",
        "topix_pct": f"{float(tp):.6f}" if isinstance(tp, (int, float)) else "",
        "entry_vwap_distance_pct": f"{float(evw):.6f}" if isinstance(evw, (int, float)) else "",
        "high_update_count_before_entry": str(int(hu)) if isinstance(hu, (int, float)) else "",
        "skipped": "true" if skipped else "false",
        "skip_reason": skip_reason,
    }


def _paper_trade_merge_skip_reason_counts(rep: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    ov = rep.get("overall_summary") if isinstance(rep.get("overall_summary"), dict) else {}
    for key in ("regime_filters", "signal_filters"):
        sub = ov.get(key)
        if not isinstance(sub, dict):
            continue
        rc = sub.get("skip_reason_counts")
        if isinstance(rc, dict):
            for k, v in rc.items():
                kk = str(k)
                out[kk] = int(out.get(kk, 0)) + int(v or 0)
    combo_root = rep.get("combo_filter_analysis") if isinstance(rep.get("combo_filter_analysis"), dict) else {}
    sc = combo_root.get("strong_combo_filter") if isinstance(combo_root.get("strong_combo_filter"), dict) else {}
    if isinstance(sc, dict):
        rc2 = sc.get("skip_reason_counts")
        if isinstance(rc2, dict):
            for k, v in rc2.items():
                kk = str(k)
                out[kk] = int(out.get(kk, 0)) + int(v or 0)
        br = ((sc.get("virtual_pnl_analysis") or {}) if isinstance(sc.get("virtual_pnl_analysis"), dict) else {}).get("by_reason")
        if isinstance(br, dict):
            for k, v in br.items():
                if isinstance(v, dict) and "skipped_signals_count" in v:
                    kk = str(k)
                    out[kk] = int(out.get(kk, 0)) + int(v.get("skipped_signals_count") or 0)
    return out


def _paper_trade_write_summary_txt(*, path: str, report: dict[str, Any], poll_ts_jst: str) -> None:
    ov = report.get("overall_summary") if isinstance(report.get("overall_summary"), dict) else {}
    stats = ov.get("stats") if isinstance(ov.get("stats"), dict) else {}
    rc_risk = ov.get("risk_controls") if isinstance(ov.get("risk_controls"), dict) else {}
    aa = report.get("accident_analysis") if isinstance(report.get("accident_analysis"), dict) else {}
    lw10 = aa.get("lose_worst10") if isinstance(aa.get("lose_worst10"), list) else []
    lw_sum = 0.0
    for it in lw10:
        if isinstance(it, dict):
            try:
                lw_sum += float(it.get("pnl_yen_100_shares") or 0.0)
            except Exception:
                pass
    rc_ctrl = ov.get("regime_controls") if isinstance(ov.get("regime_controls"), dict) else {}
    ev_mr = rc_ctrl.get("eval_by_market_regime") if isinstance(rc_ctrl.get("eval_by_market_regime"), dict) else {}

    rf_s = ov.get("regime_filters") if isinstance(ov.get("regime_filters"), dict) else {}
    sf_s = ov.get("signal_filters") if isinstance(ov.get("signal_filters"), dict) else {}
    cfa_s = report.get("combo_filter_analysis") if isinstance(report.get("combo_filter_analysis"), dict) else {}
    sc_s = cfa_s.get("strong_combo_filter") if isinstance(cfa_s.get("strong_combo_filter"), dict) else {}

    lines = [
        f"paper_trade_summary (poll={poll_ts_jst})",
        "",
        f"signals_detected: {int(ov.get('all_signals_detected') or 0)}",
        f"signals_in_eval (entries): {int(ov.get('signals_in_eval') or 0)}",
        f"signals_excluded: {int(ov.get('signals_excluded') or 0)}",
        f"skipped_signals_count regime_filters: {int(rf_s.get('skipped_signals_count') or 0)}",
        f"skipped_signals_count signal_filters: {int(sf_s.get('skipped_signals_count') or 0)}",
        f"skipped_signals_count strong_combo_filter: {int(sc_s.get('skipped_signals_count') or 0)}",
        "",
        "skip_reason_counts (merged):",
    ]
    sk = _paper_trade_merge_skip_reason_counts(report)
    if not sk:
        lines.append("  (none)")
    else:
        for k in sorted(sk.keys()):
            lines.append(f"  {k}: {sk[k]}")
    lines.extend(
        [
            "",
            f"virtual_pnl_yen_100_shares: {float(stats.get('pnl_yen_100_shares') or 0.0):+.2f}",
            f"win_rate_pct: {float(stats.get('win_rate_pct') or 0.0):.2f}",
            f"lose_worst10_sum_yen_100_shares: {float(lw_sum):+.2f}",
            f"max_intraday_drawdown_yen_100_shares: {float(rc_risk.get('max_intraday_drawdown_yen_100_shares') or 0.0):.2f}",
            "",
            "regime_controls.eval_by_market_regime (expectancy / n):",
        ]
    )
    if not ev_mr:
        lines.append("  (empty or regime_controls disabled)")
    else:
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = ev_mr.get(rk)
            if not isinstance(row, dict):
                continue
            n = int(row.get("signals") or 0)
            exp = float(row.get("avg_expectancy_yen_100_shares") or 0.0)
            lines.append(f"  {rk}: signals={n} avg_expectancy_yen_100_shares={exp:+.2f}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _paper_trade_should_emit_row(s: ReplaySignalEval) -> bool:
    if bool(getattr(s, "excluded_from_eval", False)):
        return True
    if not bool(getattr(s, "entry_allowed", True)):
        return True
    if bool(getattr(s, "resolved", False)):
        return True
    return False


def _paper_trade_signal_stable_id(s: ReplaySignalEval, *, fallback: str) -> str:
    sid = str(getattr(s, "signal_id", "") or "").strip()
    if sid:
        return sid
    st = getattr(s, "signal_time_utc", None)
    if isinstance(st, datetime):
        iso = st.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{s.symbol}|{iso}|{fallback}"
    return f"{s.symbol}|{fallback}"


def run_paper_trade(*, paper_trade_interval_sec: float, run_replay_kw: dict[str, Any]) -> int:
    """
    Yahoo 1d 1分足を一定間隔で取り直し、run_replay と同一ロジックで仮想signal/exit/PnLのみ記録します。
    実証券API・発注処理には接続しません。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    seen_ids: set[str] = set()
    csv_header = [
        "datetime_jst",
        "symbol",
        "signal_type",
        "entry_price",
        "exit_price",
        "exit_reason",
        "pnl_yen_100_shares",
        "market_regime",
        "rising_ratio",
        "topix_pct",
        "entry_vwap_distance_pct",
        "high_update_count_before_entry",
        "skipped",
        "skip_reason",
    ]
    print(f"[{now_str()}] paper_trade: poll_interval={float(paper_trade_interval_sec):g}s（実注文なし） Ctrl+C で終了")
    # 現物寄り前後の扱い: 09:00 未満は run_replay しない / 15:30 以降は当日1回だけ EOD summary の後は idle
    _HM_OPEN_MIN = 9 * 60
    _HM_CLOSE_MIN = 15 * 60 + 30
    eod_summary_day: Optional[str] = None
    try:
        n_poll = 0
        while True:
            now_loop_jst = datetime.now(JST)
            day_key = now_loop_jst.strftime("%Y%m%d")
            hm = now_loop_jst.hour * 60 + now_loop_jst.minute

            if hm < _HM_OPEN_MIN:
                print(
                    f"[{now_str()}] [paper_trade] market not open — sleeping "
                    f"{float(paper_trade_interval_sec):g}s"
                )
                time.sleep(max(0.5, float(paper_trade_interval_sec)))
                continue

            if hm >= _HM_CLOSE_MIN and eod_summary_day == day_key:
                print(f"[{now_str()}] [paper_trade] market closed — idle (no new signals)")
                time.sleep(max(0.5, float(paper_trade_interval_sec)))
                continue

            n_poll += 1
            is_eod_summary_only = hm >= _HM_CLOSE_MIN and eod_summary_day != day_key

            out_dir = os.path.join(script_dir, "results", "paper_trade", day_key)
            os.makedirs(out_dir, exist_ok=True)
            state_path = os.path.join(out_dir, "paper_trade_seen_ids.json")
            try:
                if os.path.isfile(state_path):
                    with open(state_path, "r", encoding="utf-8") as sf:
                        sj = json.load(sf)
                    xs = sj.get("seen_ids") if isinstance(sj, dict) else None
                    if isinstance(xs, list):
                        seen_ids = set(str(x) for x in xs if str(x))
            except Exception:
                pass

            coll: dict[str, Any] = {}
            kw = dict(run_replay_kw)
            kw["paper_trade_mode"] = True
            kw["paper_trade_collect"] = coll
            code = run_replay(**kw)
            if int(code) != 0:
                print(f"[{now_str()}] paper_trade: run_replay が失敗しました（exit={int(code)}）")
                return int(code)
            rep = coll.get("report")
            rs_list = coll.get("replay_signals")
            if not isinstance(rep, dict) or not isinstance(rs_list, list):
                print(f"[{now_str()}] paper_trade: 内部エラー（report/signals が取得できません）")
                return 2

            if is_eod_summary_only:
                eod_summary_day = day_key

            poll_ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            log_path = os.path.join(out_dir, "paper_trade_log.csv")
            summary_path = os.path.join(out_dir, "paper_trade_summary.txt")

            new_rows: list[dict[str, str]] = []
            if not is_eod_summary_only:
                for i, s in enumerate(rs_list):
                    if not isinstance(s, ReplaySignalEval):
                        continue
                    if not _paper_trade_should_emit_row(s):
                        continue
                    sid = _paper_trade_signal_stable_id(s, fallback=f"idx{i:05d}")
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    new_rows.append(_paper_trade_row_dict(s))

            if new_rows:
                write_header = not os.path.isfile(log_path)
                with open(log_path, "a", encoding="utf-8", newline="") as fcsv:
                    w = csv.DictWriter(fcsv, fieldnames=csv_header)
                    if write_header:
                        w.writeheader()
                    for row in new_rows:
                        w.writerow(row)
                try:
                    with open(state_path, "w", encoding="utf-8") as sf:
                        json.dump({"seen_ids": sorted(seen_ids)}, sf, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            _paper_trade_write_summary_txt(path=summary_path, report=rep, poll_ts_jst=poll_ts)

            try:
                webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
                alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
                bot_token = _get_discord_token_with_compat_warning()
                if (alert_channel_id is not None and bot_token) or webhook_url:
                    ov = rep.get("overall_summary") if isinstance(rep.get("overall_summary"), dict) else {}
                    st = ov.get("stats") if isinstance(ov.get("stats"), dict) else {}
                    n_sig = int(ov.get("all_signals_detected") or 0)
                    n_ev = int(ov.get("signals_in_eval") or 0)
                    pnl = float(st.get("pnl_yen_100_shares") or 0.0)
                    wr = float(st.get("win_rate_pct") or 0.0)
                    tag = "eod_summary" if is_eod_summary_only else "poll"
                    msg = {
                        "content": (
                            f"[paper_trade] {tag} #{n_poll} {poll_ts} JST\n"
                            f"signals={n_sig} eval={n_ev} pnl_100sh={pnl:+.0f}円 win_rate={wr:.1f}%\n"
                            f"+csv rows: {len(new_rows)}"
                        )
                    }
                    discord_notify(
                        msg,
                        webhook_url=webhook_url,
                        alert_channel_id=alert_channel_id,
                        bot_token=bot_token,
                    )
            except Exception:
                pass

            _suffix = " eod_summary" if is_eod_summary_only else ""
            print(
                f"[{now_str()}] paper_trade: poll#{n_poll} OK{_suffix} "
                f"(new_rows={len(new_rows)} signals_eval={int((rep.get('overall_summary') or {}).get('signals_in_eval') or 0)})"
            )
            time.sleep(max(0.5, float(paper_trade_interval_sec)))
    except KeyboardInterrupt:
        print("\nCtrl+C を検知しました。paper_trade を終了します。")
        return 0


def _aggregate_replay_repeat_run_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    repeat 実行で得た runXX.json レポートのリストから、main と同様の集計 summary を返します。
    """
    if not run_summaries:
        return {}
    run_stats: list[dict[str, Any]] = []
    regime_skipped_total = 0
    for rr in run_summaries:
        rep = rr.get("report") or {}
        stats = (((rep.get("overall_summary") or {}).get("stats")) or {})
        pnl = float(stats.get("pnl_yen_100_shares") or 0.0)
        sigs = int(stats.get("signals") or 0)
        wr = float(stats.get("win_rate_pct") or 0.0)
        exp = float(stats.get("expectancy_yen_100_shares_per_signal") or 0.0)
        rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
        rf = ((rep.get("overall_summary") or {}).get("regime_filters")) or {}
        if isinstance(rf, dict):
            regime_skipped_total += int(rf.get("skipped_signals_count") or 0)
        run_stats.append(
            {
                "signals": sigs,
                "win_rate_pct": wr,
                "pnl": pnl,
                "exp": exp,
                "max_intraday_dd": float(rc.get("max_intraday_drawdown_yen_100_shares") or 0.0)
                if isinstance(rc, dict)
                else 0.0,
                "avg_daily_dd": float(rc.get("avg_daily_drawdown_yen_100_shares") or 0.0)
                if isinstance(rc, dict)
                else 0.0,
            }
        )

    total_runs = len(run_stats)
    if total_runs <= 0:
        return {}
    total_signals = sum(int(x.get("signals") or 0) for x in run_stats)
    total_pnl = sum(float(x.get("pnl") or 0.0) for x in run_stats)
    avg_exp = sum(float(x.get("exp") or 0.0) for x in run_stats) / float(total_runs)
    plus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) > 0)
    minus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) < 0)
    max_lose_run = min(run_stats, key=lambda x: float(x.get("pnl") or 0.0))

    sum_lose_worst10_yen = 0.0
    for rr in run_summaries:
        rep = rr.get("report") or {}
        aa = rep.get("accident_analysis") or {}
        lw = aa.get("lose_worst10") or []
        if not isinstance(lw, list):
            continue
        for it in lw:
            try:
                sum_lose_worst10_yen += float(it.get("pnl_yen_100_shares") or 0.0)
            except Exception:
                continue

    return {
        "runs": int(total_runs),
        "total_signals": int(total_signals),
        "avg_expectancy_yen_100_shares": float(avg_exp),
        "total_pnl_yen_100_shares": float(total_pnl),
        "plus_runs": int(plus_runs),
        "minus_runs": int(minus_runs),
        "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
        "sum_lose_worst10_yen_100_shares": float(sum_lose_worst10_yen),
        "regime_filter_skipped_signals_total": int(regime_skipped_total),
    }


def _aggregate_regime_control_sweep_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    base = _aggregate_replay_repeat_run_summaries(run_summaries)
    sk = 0
    sr: dict[str, int] = {}
    vcnt = 0
    vpnl_sum = 0.0
    mr_acc: dict[str, dict[str, float]] = {
        rk: {"signals": 0.0, "pnl_sum": 0.0, "lw10_sum": 0.0} for rk in ("STRONG", "NORMAL", "WEAK", "CRASH")
    }
    for rr in run_summaries:
        rep = rr.get("report") or {}
        ov = rep.get("overall_summary") or {}
        rc = ov.get("regime_controls") if isinstance(ov.get("regime_controls"), dict) else {}
        if not rc:
            continue
        sk += int(rc.get("skipped_signals_count") or 0)
        for k0, v0 in (rc.get("skip_reason_counts") or {}).items():
            try:
                kk = str(k0)
                if kk:
                    sr[kk] = int(sr.get(kk, 0)) + int(v0 or 0)
            except Exception:
                continue
        vpa = rc.get("virtual_pnl_analysis") if isinstance(rc.get("virtual_pnl_analysis"), dict) else {}
        if vpa:
            vcnt += int(vpa.get("skipped_signals_count") or 0)
            vpnl_sum += float(vpa.get("total_pnl_yen_100_shares") or 0.0)
        evmr = rc.get("eval_by_market_regime") if isinstance(rc.get("eval_by_market_regime"), dict) else {}
        if evmr:
            for rk in mr_acc:
                row = evmr.get(rk)
                if not isinstance(row, dict):
                    continue
                mr_acc[rk]["signals"] += float(row.get("signals") or 0)
                mr_acc[rk]["pnl_sum"] += float(row.get("total_pnl_yen_100_shares") or 0.0)
                mr_acc[rk]["lw10_sum"] += float(row.get("lose_worst10_sum_yen_100_shares") or 0.0)

    mr_out: dict[str, dict[str, Any]] = {}
    for rk, acc in mr_acc.items():
        n_sig = int(acc["signals"])
        pnl_tot = float(acc["pnl_sum"])
        mr_out[rk] = {
            "signals": int(n_sig),
            "total_pnl_yen_100_shares": float(pnl_tot),
            "avg_expectancy_yen_100_shares": float(pnl_tot / float(n_sig)) if n_sig > 0 else 0.0,
            "lose_worst10_sum_yen_100_shares": float(acc["lw10_sum"]),
        }

    out = dict(base)
    out["regime_controls_cell_aggregate"] = {
        "skipped_signals_count_total": int(sk),
        "skip_reason_counts": dict(sr),
        "virtual_pnl_aggregate": {
            "skipped_signals_count_total": int(vcnt),
            "total_pnl_yen_100_shares_sum": float(vpnl_sum),
            "avg_expectancy_yen_100_shares_if_skipped": (
                float(vpnl_sum / float(vcnt)) if int(vcnt) > 0 else 0.0
            ),
            "prevented_loss_estimate_yen_100_shares_sum": float(-vpnl_sum),
        },
        "eval_by_market_regime_summed_over_runs": dict(mr_out),
    }
    return out


def _vwap_sweep_thr_slug(threshold: float) -> str:
    return f"thr{int(round(float(threshold) * 10)):03d}"


def _write_vwap_sweep_configs(script_dir: str, thresholds: list[float]) -> dict[float, str]:
    """replay_safe をベースに entry_filters.vwap_distance_pct を上書きした JSON を configs/vwap_sweep/ に保存。"""
    sweep_dir = os.path.join(script_dir, "configs", "vwap_sweep")
    os.makedirs(sweep_dir, exist_ok=True)
    base_rel = "configs/replay_safe.json"
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        presets = _default_replay_configs_dicts()
        fallback = presets.get("replay_safe.json")
        if isinstance(fallback, dict):
            base_cfg = dict(fallback)
    out_paths: dict[float, str] = {}
    for thr in thresholds:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        cfg["name"] = f"replay_safe_vwap_sweep_{thr:g}".replace(".", "_")
        ef = cfg.get("entry_filters")
        if not isinstance(ef, dict):
            ef = {}
        ef["rsi"] = {"enabled": False, "exclude_above": 75.0}
        ef["vwap_distance_pct"] = {"enabled": True, "exclude_above": float(thr)}
        ef["atr_pct"] = {"enabled": False, "exclude_above": 4.0}
        cfg["entry_filters"] = ef
        slug = _vwap_sweep_thr_slug(thr)
        fn = f"replay_safe_vwap_sweep_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        out_paths[float(thr)] = os.path.abspath(path)
    return out_paths


def run_vwap_distance_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    VWAP distance exclude_above を複数値でスイープし、SWEEP_REPLAY_RANGES（random_apr のみ）を各 n_repeat 回実行して比較表を保存します。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    thresholds = [1.5, 2.0, 2.5, 3.0]
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    print(f"[{now_str()}] VWAP distance sweep: thresholds={thresholds} ranges={ranges} repeat={n_repeat}")
    cfg_paths = _write_vwap_sweep_configs(script_dir, thresholds)
    for t, p in cfg_paths.items():
        print(f"[{now_str()}] 生成 config: vwap_exclude={t:g}% -> {p}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []

    for thr in thresholds:
        cfg_path = cfg_paths[thr]
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)

        for rng in ranges:
            replay_random_days = 5
            thr_slug = _vwap_sweep_thr_slug(thr)
            batch_stamp = f"{sweep_stamp}_{thr_slug}_{rng}"
            output_subdir = os.path.join(f"vwap_sweep_{sweep_stamp}", f"{thr_slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: vwap_exclude={thr:g}%  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = [
                        fn
                        for fn in os.listdir(results_dir)
                        if fn.endswith(".json")
                        and ("replay_summary_" in fn)
                        and (not fn.endswith("_symbol_scores.json"))
                        and fn.endswith(f"{run_tag}.json")
                    ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries(run_summaries)
            rows.append(
                {
                    "vwap_exclude_above": float(thr),
                    "replay_range": str(rng),
                    "config_path": str(cfg_path),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== VWAP distance filter sweep ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("生成した config（replay_safe ベース）:")
    for t in thresholds:
        out_lines.append(f"  - {t:g}% -> {cfg_paths[t]}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for p in fps:
                    out_lines.append(f"  - {p}")
        except Exception:
            continue
    out_lines.append("")
    hdr = (
        "rank\tvwap_exclude_%\treplay_range\tavg_expectancy_yen\ttotal_pnl_100_shares\t"
        "max_lose_run_yen\tplus_runs\tminus_runs\tlose_worst10_sum_yen\ttotal_signals\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('vwap_exclude_above')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('total_signals') or 0)}\t"
            f"results/{r.get('output_subdir')}/"
        )

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"vwap_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    out_path = os.path.join(sweep_root, f"vwap_sweep_summary_{sweep_stamp}.txt")
    out_compat_path = os.path.join(results_root, f"vwap_sweep_summary_{sweep_stamp}.txt")
    content = "\n".join(out_lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    # 互換: 旧パスにも同内容を保存（主保存先は sweep フォルダ）
    try:
        with open(out_compat_path, "w", encoding="utf-8") as f2:
            f2.write(content)
    except Exception:
        pass

    print("")
    print(f"[{now_str()}] VWAP sweep summary_path: {out_path}")
    print(f"[{now_str()}] (compat) VWAP sweep summary_path: {out_compat_path}")
    print("\n".join(out_lines))
    return 0


def _aggregate_replay_repeat_run_summaries_for_daily_loss_stop(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    daily_loss_stop sweep 用の集計（repeat runXX.json から必要項目を合算/平均）。
    """
    if not run_summaries:
        return {}

    run_rows: list[dict[str, Any]] = []
    sum_lose_worst10_yen = 0.0
    trigger_count_total = 0
    skipped_entries_total = 0
    max_intraday_dd_worst = 0.0
    avg_daily_dd_sum = 0.0

    for rr in run_summaries:
        rep = rr.get("report") or {}
        stats = (((rep.get("overall_summary") or {}).get("stats")) or {})
        pnl = float(stats.get("pnl_yen_100_shares") or 0.0)
        sigs = int(stats.get("signals") or 0)
        wr = float(stats.get("win_rate_pct") or 0.0)
        exp = float(stats.get("expectancy_yen_100_shares_per_signal") or 0.0)

        rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
        if not isinstance(rc, dict):
            rc = {}
        trigger_count_total += int(rc.get("daily_loss_stop_trigger_count") or 0)
        skipped_entries_total += int(rc.get("daily_loss_stop_skipped_entries") or 0)
        max_intraday_dd_worst = float(max(max_intraday_dd_worst, float(rc.get("max_intraday_drawdown_yen_100_shares") or 0.0)))
        avg_daily_dd_sum += float(rc.get("avg_daily_drawdown_yen_100_shares") or 0.0)

        run_rows.append({"signals": sigs, "win_rate_pct": wr, "pnl": pnl, "exp": exp})

        aa = rep.get("accident_analysis") or {}
        lw = aa.get("lose_worst10") or []
        if isinstance(lw, list):
            for it in lw:
                try:
                    sum_lose_worst10_yen += float(it.get("pnl_yen_100_shares") or 0.0)
                except Exception:
                    continue

    total_runs = len(run_rows)
    if total_runs <= 0:
        return {}

    total_signals = sum(int(x.get("signals") or 0) for x in run_rows)
    total_pnl = sum(float(x.get("pnl") or 0.0) for x in run_rows)
    avg_exp = sum(float(x.get("exp") or 0.0) for x in run_rows) / float(total_runs)
    plus_runs = sum(1 for x in run_rows if float(x.get("pnl") or 0.0) > 0)
    minus_runs = sum(1 for x in run_rows if float(x.get("pnl") or 0.0) < 0)
    max_lose_run = min(run_rows, key=lambda x: float(x.get("pnl") or 0.0))
    avg_daily_dd_avg = (float(avg_daily_dd_sum) / float(total_runs)) if total_runs > 0 else 0.0

    return {
        "runs": int(total_runs),
        "total_signals": int(total_signals),
        "avg_expectancy_yen_100_shares": float(avg_exp),
        "total_pnl_yen_100_shares": float(total_pnl),
        "plus_runs": int(plus_runs),
        "minus_runs": int(minus_runs),
        "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
        "sum_lose_worst10_yen_100_shares": float(sum_lose_worst10_yen),
        "daily_loss_stop_trigger_count": int(trigger_count_total),
        "daily_loss_stop_skipped_entries": int(skipped_entries_total),
        "max_intraday_drawdown_yen_100_shares": float(max_intraday_dd_worst),
        "avg_daily_drawdown_yen_100_shares": float(avg_daily_dd_avg),
    }


def _aggregate_replay_repeat_run_summaries_for_regime_filter(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    repeat 実行で得た runXX.json レポートのリストから、regime_filter sweep用の集計 summary を返します。
    """
    if not run_summaries:
        return {}
    run_rows: list[dict[str, Any]] = []
    sum_lose_worst10_yen = 0.0
    max_intraday_dd_worst = 0.0
    skipped_signals_total = 0

    for rr in run_summaries:
        rep = rr.get("report") or {}
        stats = (((rep.get("overall_summary") or {}).get("stats")) or {})
        pnl = float(stats.get("pnl_yen_100_shares") or 0.0)
        sigs = int(stats.get("signals") or 0)
        exp = float(stats.get("expectancy_yen_100_shares_per_signal") or 0.0)

        rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
        if isinstance(rc, dict):
            try:
                max_intraday_dd_worst = float(
                    max(float(max_intraday_dd_worst), float(rc.get("max_intraday_drawdown_yen_100_shares") or 0.0))
                )
            except Exception:
                pass

        rf = ((rep.get("overall_summary") or {}).get("regime_filters")) or {}
        if isinstance(rf, dict):
            skipped_signals_total += int(rf.get("skipped_signals_count") or 0)

        # lose_worst10
        aa = rep.get("accident_analysis") or {}
        lw = aa.get("lose_worst10") or []
        if isinstance(lw, list):
            for it in lw:
                try:
                    sum_lose_worst10_yen += float((it or {}).get("pnl_yen_100_shares") or 0.0)
                except Exception:
                    continue

        run_rows.append({"signals": sigs, "pnl": pnl, "exp": exp})

    total_runs = len(run_rows)
    if total_runs <= 0:
        return {}

    total_signals = sum(int(x.get("signals") or 0) for x in run_rows)
    total_pnl = sum(float(x.get("pnl") or 0.0) for x in run_rows)
    avg_exp = sum(float(x.get("exp") or 0.0) for x in run_rows) / float(total_runs)
    plus_runs = sum(1 for x in run_rows if float(x.get("pnl") or 0.0) > 0)
    minus_runs = sum(1 for x in run_rows if float(x.get("pnl") or 0.0) < 0)
    max_lose_run = min(run_rows, key=lambda x: float(x.get("pnl") or 0.0))

    return {
        "runs": int(total_runs),
        "total_signals": int(total_signals),
        "passed_signals_count": int(total_signals),
        "avg_expectancy_yen_100_shares": float(avg_exp),
        "total_pnl_yen_100_shares": float(total_pnl),
        "plus_runs": int(plus_runs),
        "minus_runs": int(minus_runs),
        "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
        "sum_lose_worst10_yen_100_shares": float(sum_lose_worst10_yen),
        "max_intraday_drawdown_yen_100_shares": float(max_intraday_dd_worst),
        "skipped_signals_count": int(skipped_signals_total),
        "skip_ratio": float(
            (float(skipped_signals_total) / float(int(total_signals) + int(skipped_signals_total)))
            if (int(total_signals) + int(skipped_signals_total)) > 0
            else 0.0
        ),
    }


def _build_symbol_contribution_analysis(
    *,
    by_symbol_summary: dict[str, Any],
    total_pnl_yen_100_shares: float,
    total_signals: int,
    exclude_top_n_symbols_list: list[int] | tuple[int, ...] = (1, 2, 3),
) -> dict[str, Any]:
    """
    銘柄依存（symbol contribution）分析。
    - by_symbol_summary: runXX.json の "by_symbol_summary" 形式（sym -> {signals, pnl_yen_100_shares, ...}）
    - total_pnl_yen_100_shares / total_signals: 全体の統計（eval対象のみ）
    """
    rows: list[dict[str, Any]] = []
    for sym, s0 in (by_symbol_summary or {}).items():
        if not sym:
            continue
        s = s0 if isinstance(s0, dict) else {}
        pnl = float(s.get("pnl_yen_100_shares") or 0.0)
        sigs = int(s.get("signals") or 0)
        exp = float(s.get("expectancy_yen_100_shares_per_signal") or 0.0)
        ratio_total = float(pnl / float(total_pnl_yen_100_shares)) if float(total_pnl_yen_100_shares) != 0.0 else 0.0
        ratio_abs_total = (
            float(abs(pnl) / float(abs(total_pnl_yen_100_shares))) if float(total_pnl_yen_100_shares) != 0.0 else 0.0
        )
        rows.append(
            {
                "symbol": str(sym),
                "signals": int(sigs),
                "pnl_yen_100_shares": float(pnl),
                "pnl_ratio_of_total": float(ratio_total),
                "pnl_ratio_of_abs_total": float(ratio_abs_total),
                "expectancy_yen_100_shares_per_signal": float(exp),
            }
        )

    rows_sorted = sorted(rows, key=lambda x: float(x.get("pnl_yen_100_shares") or 0.0), reverse=True)
    cum = 0.0
    for r in rows_sorted:
        cum += float(r.get("pnl_yen_100_shares") or 0.0)
        r["cumulative_pnl_yen_100_shares"] = float(cum)
        r["cumulative_pnl_ratio_of_total"] = (
            float(cum / float(total_pnl_yen_100_shares)) if float(total_pnl_yen_100_shares) != 0.0 else 0.0
        )

    # 上位銘柄除外シミュレーション（pnl上位順）
    sims: list[dict[str, Any]] = []
    for n0 in list(exclude_top_n_symbols_list):
        try:
            n = int(n0)
        except Exception:
            continue
        if n <= 0:
            continue
        excluded = rows_sorted[:n]
        excluded_symbols = [str(x.get("symbol") or "") for x in excluded if str(x.get("symbol") or "")]
        excl_pnl = sum(float(x.get("pnl_yen_100_shares") or 0.0) for x in excluded)
        excl_sigs = sum(int(x.get("signals") or 0) for x in excluded)
        pnl_after = float(total_pnl_yen_100_shares) - float(excl_pnl)
        sig_after = int(total_signals) - int(excl_sigs)
        exp_after = float(pnl_after / float(sig_after)) if int(sig_after) > 0 else 0.0
        sims.append(
            {
                "exclude_top_n_symbols": int(n),
                "excluded_symbols": excluded_symbols,
                "total_pnl_before_yen_100_shares": float(total_pnl_yen_100_shares),
                "total_signals_before": int(total_signals),
                "expectancy_before_yen_100_shares_per_signal": float(
                    (float(total_pnl_yen_100_shares) / float(total_signals)) if int(total_signals) > 0 else 0.0
                ),
                "total_pnl_after_yen_100_shares": float(pnl_after),
                "total_signals_after": int(sig_after),
                "expectancy_after_yen_100_shares_per_signal": float(exp_after),
            }
        )

    return {
        "ranking_method": "pnl_desc",
        "total_pnl_yen_100_shares": float(total_pnl_yen_100_shares),
        "total_signals": int(total_signals),
        "by_symbol": rows_sorted,
        "exclude_top_n_simulation": sims,
    }


def _lose_worst10_sum_yen_100_shares_from_pnls(pnls: list[float]) -> float:
    try:
        xs = [float(x) for x in pnls if isinstance(x, (int, float)) and math.isfinite(float(x))]
        if not xs:
            return 0.0
        xs_sorted = sorted(xs)
        return float(sum(xs_sorted[:10]))
    except Exception:
        return 0.0


def _parse_hhmm_to_minutes(hhmm: str) -> Optional[int]:
    try:
        s = str(hhmm or "").strip()
        if not s:
            return None
        if ":" in s:
            a, b = s.split(":", 1)
            hh = int(a)
            mm = int(b)
            return int(hh * 60 + mm)
        # e.g. "1030"
        if len(s) >= 4:
            hh = int(s[:2])
            mm = int(s[2:4])
            return int(hh * 60 + mm)
        return None
    except Exception:
        return None


def _write_signal_filter_sweep_configs(script_dir: str) -> list[str]:
    """
    “signal_filters” ABテスト用configを configs/signal_filter_sweep/ に作成します。
    ベースは replay_morning_vwap2_dd30k_rlt50（無ければ replay_morning_vwap2_dd30k）を優先。
    """
    base_candidates = [
        os.path.join("configs", "replay_morning_vwap2_dd30k_rlt50.json"),
        os.path.join("configs", "replay_morning_vwap2_dd30k.json"),
        os.path.join("configs", "replay_morning_vwap2.json"),
    ]
    base_cfg: dict[str, Any] = {}
    base_path = None
    for rel in base_candidates:
        p = _resolve_replay_config_path(rel)
        if p:
            base_path = p
            base_cfg = _load_replay_config(p) or {}
            if base_cfg:
                break
    if not base_cfg:
        return []

    sweep_dir = os.path.join(script_dir, "configs", "signal_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("baseline_off", {}),
        ("gap_ge_1_5", {"disable_gap_ge_pct": True, "gap_ge_threshold_pct": 1.5}),
        ("gap_ge_2", {"disable_gap_ge_pct": True, "gap_ge_threshold_pct": 2.0}),
        ("gap_ge_2_5", {"disable_gap_ge_pct": True, "gap_ge_threshold_pct": 2.5}),
        ("gap_ge_3", {"disable_gap_ge_pct": True, "gap_ge_threshold_pct": 3.0}),
        ("gap_ge_4", {"disable_gap_ge_pct": True, "gap_ge_threshold_pct": 4.0}),
    ]

    out_paths: list[str] = []
    for slug, flags in cases:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        base_name = str(cfg.get("name") or "replay_cfg")
        cfg["name"] = f"{base_name}_sigf_{slug}"
        if flags:
            cfg["signal_filters"] = dict(flags)
        else:
            cfg.pop("signal_filters", None)
        fn = f"{base_name}_sigf_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        out_paths.append(os.path.abspath(path))
    return out_paths


def _aggregate_replay_repeat_run_summaries_for_signal_filter_sweep(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    # 基本は既存の sweep と同じ指標を抜く
    run_rows: list[dict[str, Any]] = []
    sum_lose_worst10 = 0.0
    max_intraday_dd_worst = 0.0
    skipped_total = 0
    total_signals = 0
    total_pnl = 0.0
    plus_runs = 0
    minus_runs = 0
    max_lose_run = {"pnl": 0.0}
    # gap filter virtual（run合算）
    vf_skipped = 0
    vf_pnl = 0.0
    vf_prevented = 0.0
    vf_wr_weighted_num = 0.0

    for rr in run_summaries:
        rep = rr.get("report") or {}
        stats = (((rep.get("overall_summary") or {}).get("stats")) or {})
        pnl = float(stats.get("pnl_yen_100_shares") or 0.0)
        sigs = int(stats.get("signals") or 0)
        exp = float(stats.get("expectancy_yen_100_shares_per_signal") or 0.0)
        rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
        sf = ((rep.get("overall_summary") or {}).get("signal_filters")) or {}
        skipped_total += int(sf.get("skipped_signals_count") or 0) if isinstance(sf, dict) else 0
        if isinstance(sf, dict):
            vpa = sf.get("virtual_pnl_analysis") or {}
            if isinstance(vpa, dict):
                n = int(vpa.get("skipped_signals_count") or 0)
                vf_skipped += n
                vf_pnl += float(vpa.get("total_pnl_yen_100_shares") or 0.0)
                vf_prevented += float(vpa.get("prevented_loss_estimate_yen_100_shares") or 0.0)
                wrp = float(vpa.get("winrate_pct") or 0.0)
                if n > 0:
                    vf_wr_weighted_num += wrp * float(n)
        max_intraday_dd_worst = max(max_intraday_dd_worst, float(rc.get("max_intraday_drawdown_yen_100_shares") or 0.0)) if isinstance(rc, dict) else max_intraday_dd_worst

        run_rows.append({"pnl": pnl, "signals": sigs, "exp": exp})
        total_signals += sigs
        total_pnl += pnl
        if pnl > 0:
            plus_runs += 1
        if pnl < 0:
            minus_runs += 1
        if (not run_rows) or pnl < float(max_lose_run.get("pnl") or 0.0):
            max_lose_run = {"pnl": pnl}

        aa = rep.get("accident_analysis") or {}
        lw = aa.get("lose_worst10") or []
        if isinstance(lw, list):
            for it in lw:
                try:
                    sum_lose_worst10 += float(it.get("pnl_yen_100_shares") or 0.0)
                except Exception:
                    continue

    runs = len(run_rows)
    avg_exp = (sum(float(x.get("exp") or 0.0) for x in run_rows) / float(runs)) if runs > 0 else 0.0
    vf_wr_pct = float(vf_wr_weighted_num / float(vf_skipped)) if int(vf_skipped) > 0 else 0.0
    vf_avg_exp = float(vf_pnl / float(vf_skipped)) if int(vf_skipped) > 0 else 0.0
    return {
        "runs": int(runs),
        "total_signals": int(total_signals),
        "avg_expectancy_yen_100_shares": float(avg_exp),
        "total_pnl_yen_100_shares": float(total_pnl),
        "plus_runs": int(plus_runs),
        "minus_runs": int(minus_runs),
        "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
        "sum_lose_worst10_yen_100_shares": float(sum_lose_worst10),
        "max_intraday_drawdown_yen_100_shares": float(max_intraday_dd_worst),
        "skipped_signals_count": int(skipped_total),
        "gap_virtual_pnl_analysis_aggregate": {
            "skipped_signals_count_total": int(vf_skipped),
            "total_pnl_yen_100_shares_sum": float(vf_pnl),
            "avg_expectancy_yen_100_shares": float(vf_avg_exp),
            "winrate_pct_weighted": float(vf_wr_pct),
            "prevented_loss_estimate_yen_100_shares_sum": float(vf_prevented),
        },
    }


def run_signal_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    signal_filters の AB test sweep。
    - baseline + gap_ge 閾値 1.5 / 2 / 2.5 / 3 / 4（disable_gap_ge_pct）
    - SWEEP_REPLAY_RANGES（random_apr）×n_repeat のみ（デフォルト n_repeat は main 側で10）
    - 出力: results/signal_filter_sweep_<stamp>/sweep_summary.txt（本指標 + gap virtual 合算）
    - config生成: configs/signal_filter_sweep/
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    cfg_paths = _write_signal_filter_sweep_configs(script_dir)
    if not cfg_paths:
        print(f"[{now_str()}] signal_filter sweep: config生成に失敗しました。")
        return 2

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"signal_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    print(f"[{now_str()}] signal_filter sweep: configs={len(cfg_paths)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] config_root: {os.path.join(script_dir, 'configs', 'signal_filter_sweep')}")
    for p in cfg_paths:
        print(f"[{now_str()}] 生成 config: {p}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []
    for cfg_path in cfg_paths:
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_path))
        cfg_slug = os.path.basename(cfg_path).replace(".json", "")
        # Windowsパス長対策: output_subdir / batch_stamp / json名が長すぎると保存に失敗して「空フォルダ」になる
        cfg_slug_short = "cfg"
        try:
            sfn = os.path.basename(str(cfg_path or "")).replace(".json", "")
            if sfn.endswith("_sigf_baseline_off"):
                cfg_slug_short = "base"
            elif sfn.endswith("_sigf_gap_ge_2_5"):
                cfg_slug_short = "g25"
            elif sfn.endswith("_sigf_gap_ge_1_5"):
                cfg_slug_short = "g15"
            elif sfn.endswith("_sigf_gap_ge_2"):
                cfg_slug_short = "g20"
            elif sfn.endswith("_sigf_gap_ge_3"):
                cfg_slug_short = "g30"
            elif sfn.endswith("_sigf_gap_ge_4"):
                cfg_slug_short = "g40"
            else:
                # 予備: 末尾だけ短く残す
                cfg_slug_short = (sfn[-16:] if len(sfn) > 16 else sfn) or "cfg"
        except Exception:
            cfg_slug_short = "cfg"

        for rng in ranges:
            replay_random_days = 5
            # ここも短縮（path length）
            batch_stamp = f"{sweep_stamp}_{cfg_slug_short}_{rng}"
            output_subdir = os.path.join(f"signal_filter_sweep_{sweep_stamp}", f"{cfg_slug_short}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {cfg_slug_short}  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    if int(n_repeat) <= 1:
                        candidates = [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    else:
                        candidates = [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries_for_signal_filter_sweep(run_summaries)
            rows.append(
                {
                    "config_name": cfg_name,
                    "config_path": str(cfg_path),
                    "config_slug": cfg_slug,
                    "config_slug_short": str(cfg_slug_short),
                    "replay_range": str(rng),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== signal_filter sweep ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("configs:")
    for p in cfg_paths:
        out_lines.append(f"  - {p}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for p in fps:
                    out_lines.append(f"  - {p}")
        except Exception:
            continue
    out_lines.append("")

    hdr = (
        "rank\tconfig_name\treplay_range\tavg_expectancy_yen\ttotal_pnl\tmax_lose_run\tlose_worst10_sum\t"
        "plus_runs\tminus_runs\tskipped_signals_count\ttotal_signals\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(s.get('skipped_signals_count') or 0)}\t"
            f"{int(s.get('total_signals') or 0)}\t"
            f"results/{r.get('output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[GAP_VIRTUAL_PNL_ANALYSIS]  ※skipしたsignalの仮想PnLを全run合算（overall_summary.signal_filters.virtual_pnl_analysis）")
    hdr2 = (
        "rank\tconfig_name\treplay_range\tvirt_skipped_total\tvirt_total_pnl\tvirt_avg_expectancy_yen\t"
        "virt_winrate_pct_weighted\tvirt_prevented_loss_estimate_sum"
    )
    out_lines.append(hdr2)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        gvf = (s.get("gap_virtual_pnl_analysis_aggregate") or {}) if isinstance(s, dict) else {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{int(gvf.get('skipped_signals_count_total') or 0)}\t"
            f"{float(gvf.get('total_pnl_yen_100_shares_sum') or 0.0):+.2f}\t"
            f"{float(gvf.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(gvf.get('winrate_pct_weighted') or 0.0):.2f}\t"
            f"{float(gvf.get('prevented_loss_estimate_yen_100_shares_sum') or 0.0):+.2f}"
        )

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] signal_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _write_composite_filter_sweep_configs(script_dir: str) -> list[str]:
    """
    composite_signal_filters（WEAKのみ）比較用configを configs/composite_filter_sweep/ に作成します。
    ベースは replay_morning_vwap2_dd30k_rlt50 を優先（無ければ dd30k / vwap2）。
    """
    base_candidates = [
        os.path.join("configs", "replay_morning_vwap2_dd30k_rlt50.json"),
        os.path.join("configs", "replay_morning_vwap2_dd30k.json"),
        os.path.join("configs", "replay_morning_vwap2.json"),
    ]
    base_cfg: dict[str, Any] = {}
    for rel in base_candidates:
        p = _resolve_replay_config_path(rel)
        if p:
            base_cfg = _load_replay_config(p) or {}
            if base_cfg:
                break
    if not base_cfg:
        return []

    sweep_dir = os.path.join(script_dir, "configs", "composite_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)

    # baseline: composite セクション無し。その他は WEAK のみ該当条件を明示OFFしつつ1軸だけON
    cases: list[tuple[str, Optional[dict[str, Any]]]] = [
        ("baseline_off", None),
        (
            "weak_vwap_ge_1_5",
            {
                "disable_state_weak_and_vwap_ge_pct": True,
                "state_weak_vwap_ge_threshold_pct": 1.5,
                "disable_state_weak_and_gap_ge_pct": False,
                "state_weak_gap_ge_threshold_pct": 3.0,
            },
        ),
        (
            "weak_vwap_ge_1_0",
            {
                "disable_state_weak_and_vwap_ge_pct": True,
                "state_weak_vwap_ge_threshold_pct": 1.0,
                "disable_state_weak_and_gap_ge_pct": False,
                "state_weak_gap_ge_threshold_pct": 3.0,
            },
        ),
        (
            "weak_gap_ge_3",
            {
                "disable_state_weak_and_vwap_ge_pct": False,
                "state_weak_vwap_ge_threshold_pct": 1.5,
                "disable_state_weak_and_gap_ge_pct": True,
                "state_weak_gap_ge_threshold_pct": 3.0,
            },
        ),
        (
            "weak_gap_ge_2",
            {
                "disable_state_weak_and_vwap_ge_pct": False,
                "state_weak_vwap_ge_threshold_pct": 1.5,
                "disable_state_weak_and_gap_ge_pct": True,
                "state_weak_gap_ge_threshold_pct": 2.0,
            },
        ),
    ]

    out_paths: list[str] = []
    for slug, cflags in cases:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        base_name = str(cfg.get("name") or "replay_cfg")
        cfg["name"] = f"{base_name}_csf_{slug}"
        if cflags is None:
            cfg.pop("composite_signal_filters", None)
        else:
            cfg["composite_signal_filters"] = dict(cflags)
        fn = f"{base_name}_csf_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as fw:
            json.dump(cfg, fw, ensure_ascii=False, indent=2)
        out_paths.append(os.path.abspath(path))
    return out_paths


def _aggregate_replay_repeat_run_summaries_for_composite_filter_sweep(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    base = _aggregate_replay_repeat_run_summaries_for_signal_filter_sweep(run_summaries)
    comp_skipped_entries = 0
    cs_skipped_virt = 0
    cs_pnl = 0.0
    cs_prevented = 0.0
    cs_wr_weighted = 0.0

    for rr in run_summaries:
        rep = rr.get("report") or {}
        sf = ((rep.get("overall_summary") or {}).get("signal_filters")) or {}
        if not isinstance(sf, dict):
            continue
        csf = sf.get("composite_signal_filters") or {}
        if isinstance(csf, dict):
            comp_skipped_entries += int(csf.get("skipped_signals_count") or 0)
            cvpa = csf.get("virtual_pnl_analysis") or {}
            if isinstance(cvpa, dict):
                n2 = int(cvpa.get("skipped_signals_count") or 0)
                cs_skipped_virt += n2
                cs_pnl += float(cvpa.get("total_pnl_yen_100_shares") or 0.0)
                cs_prevented += float(cvpa.get("prevented_loss_estimate_yen_100_shares") or 0.0)
                wr2 = float(cvpa.get("winrate_pct") or 0.0)
                if n2 > 0:
                    cs_wr_weighted += wr2 * float(n2)

    cs_avg_exp = float(cs_pnl / float(cs_skipped_virt)) if int(cs_skipped_virt) > 0 else 0.0
    cs_wr_pct = float(cs_wr_weighted / float(cs_skipped_virt)) if int(cs_skipped_virt) > 0 else 0.0
    out = dict(base)
    out["composite_skipped_entry_signals_count"] = int(comp_skipped_entries)
    out["composite_only_virtual_aggregate"] = {
        "skipped_signals_count_total": int(cs_skipped_virt),
        "total_pnl_yen_100_shares_sum": float(cs_pnl),
        "avg_expectancy_yen_100_shares": float(cs_avg_exp),
        "winrate_pct_weighted": float(cs_wr_pct),
        "prevented_loss_estimate_yen_100_shares_sum": float(cs_prevented),
    }
    return out


def run_composite_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    composite_signal_filters（WEAK×VWAP距離 / WEAK×ギャップ）の sweep。
    - baseline + weak_vwap_ge_1_5 / 1_0 + weak_gap_ge_3 / 2
    - SWEEP_REPLAY_RANGES（random_apr）×n_repeat のみ
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    cfg_paths = _write_composite_filter_sweep_configs(script_dir)
    if not cfg_paths:
        print(f"[{now_str()}] composite_filter sweep: config生成に失敗しました。")
        return 2

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"composite_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    print(f"[{now_str()}] composite_filter sweep: configs={len(cfg_paths)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] config_root: {os.path.join(script_dir, 'configs', 'composite_filter_sweep')}")
    for p in cfg_paths:
        print(f"[{now_str()}] 生成 config: {p}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []
    for cfg_path in cfg_paths:
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_path))
        cfg_slug = os.path.basename(cfg_path).replace(".json", "")
        cfg_slug_short = "cfg"
        try:
            sfn = os.path.basename(str(cfg_path or "")).replace(".json", "")
            if sfn.endswith("_csf_baseline_off"):
                cfg_slug_short = "base"
            elif sfn.endswith("_csf_weak_vwap_ge_1_5"):
                cfg_slug_short = "wv15"
            elif sfn.endswith("_csf_weak_vwap_ge_1_0"):
                cfg_slug_short = "wv10"
            elif sfn.endswith("_csf_weak_gap_ge_3"):
                cfg_slug_short = "wg3"
            elif sfn.endswith("_csf_weak_gap_ge_2"):
                cfg_slug_short = "wg20"
            else:
                cfg_slug_short = (sfn[-16:] if len(sfn) > 16 else sfn) or "cfg"
        except Exception:
            cfg_slug_short = "cfg"

        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{cfg_slug_short}_{rng}"
            output_subdir = os.path.join(f"composite_filter_sweep_{sweep_stamp}", f"{cfg_slug_short}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {cfg_slug_short}  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    if int(n_repeat) <= 1:
                        candidates = [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    else:
                        candidates = [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries_for_composite_filter_sweep(run_summaries)
            rows.append(
                {
                    "config_name": cfg_name,
                    "config_path": str(cfg_path),
                    "config_slug": cfg_slug,
                    "config_slug_short": str(cfg_slug_short),
                    "replay_range": str(rng),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== composite_filter sweep (market_regime==WEAK のみ) ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("configs:")
    for p in cfg_paths:
        out_lines.append(f"  - {p}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for pth in fps:
                    out_lines.append(f"  - {pth}")
        except Exception:
            continue
    out_lines.append("")

    hdr = (
        "rank\tconfig_name\treplay_range\tavg_expectancy_yen\ttotal_pnl\tmax_lose_run\tlose_worst10_sum\t"
        "plus_runs\tminus_runs\tskipped_signals_sigf_any\ttotal_signals\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(s.get('skipped_signals_count') or 0)}\t"
            f"{int(s.get('total_signals') or 0)}\t"
            f"results/{r.get('output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append(
        "[ALL_FILTERS_VIRTUAL_PNL]  ※simple+composite 合算（overall_summary.signal_filters.virtual_pnl_analysis）"
    )
    hdr2 = (
        "rank\tconfig_name\treplay_range\tvirt_skipped_total\tvirt_total_pnl\tvirt_avg_expectancy_yen\t"
        "virt_winrate_pct_weighted\tvirt_prevented_loss_estimate_sum"
    )
    out_lines.append(hdr2)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        gvf = (s.get("gap_virtual_pnl_analysis_aggregate") or {}) if isinstance(s, dict) else {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{int(gvf.get('skipped_signals_count_total') or 0)}\t"
            f"{float(gvf.get('total_pnl_yen_100_shares_sum') or 0.0):+.2f}\t"
            f"{float(gvf.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(gvf.get('winrate_pct_weighted') or 0.0):.2f}\t"
            f"{float(gvf.get('prevented_loss_estimate_yen_100_shares_sum') or 0.0):+.2f}"
        )

    out_lines.append("")
    out_lines.append(
        "[COMPOSITE_WEAK_ONLY_VIRTUAL_PNL]  ※(signal_filters.composite_signal_filters.virtual_pnl_analysis) WEAK複合のみ"
    )
    hdr3 = (
        "rank\tconfig_name\treplay_range\tcomp_skipped_entry\tcomp_virt_skipped\tcomp_virt_total_pnl\t"
        "comp_virt_avg_exp_yen\tcomp_virt_winrate_pct_w\tcomp_prevented_est"
    )
    out_lines.append(hdr3)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        cvf = (s.get("composite_only_virtual_aggregate") or {}) if isinstance(s, dict) else {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{int(s.get('composite_skipped_entry_signals_count') or 0)}\t"
            f"{int(cvf.get('skipped_signals_count_total') or 0)}\t"
            f"{float(cvf.get('total_pnl_yen_100_shares_sum') or 0.0):+.2f}\t"
            f"{float(cvf.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(cvf.get('winrate_pct_weighted') or 0.0):.2f}\t"
            f"{float(cvf.get('prevented_loss_estimate_yen_100_shares_sum') or 0.0):+.2f}"
        )

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] composite_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def run_regime_control_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    比較（random_apr のみ）:
    - morning_baseline: replay_morning_vwap2_dd30k_rlt50
    - full_day_no_regime_control: replay_full_day_vwap2_dd30k_rlt50（時間帯ENTRY禁止を外す）
    - full_day_regime_control: full-day + regime_controls（地合い適応）
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"regime_control_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    cells: list[tuple[str, str, str]] = [
        ("mb", "morning_baseline", os.path.join("configs", "replay_morning_vwap2_dd30k_rlt50.json")),
        ("fd", "full_day_no_regime_control", os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json")),
        ("fdrc", "full_day_regime_control", os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50_regime_controls.json")),
    ]
    resolved: list[tuple[str, str, str]] = []
    for slug, label, rel in cells:
        ap = _resolve_replay_config_path(rel)
        if not ap:
            print(f"[{now_str()}] regime_control sweep: missing config: {rel}")
            return 2
        resolved.append((slug, label, ap))

    print(f"[{now_str()}] regime_control sweep: cells={len(resolved)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    for _s, _lb, _pa in resolved:
        print(f"[{now_str()}]   - {_lb}: {_pa}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []

    for slug, label, cfg_abs in resolved:
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"regime_control_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell": str(label),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_regime_control_sweep_summaries(run_summaries)
            rca = (summ.get("regime_controls_cell_aggregate") or {}) if isinstance(summ, dict) else {}
            vpnl_agg = (
                (((rca.get("virtual_pnl_aggregate") or {}).get("total_pnl_yen_100_shares_sum")))
                if isinstance(rca.get("virtual_pnl_aggregate"), dict)
                else None
            )
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "disable_afternoon": bool(f["replay_disable_afternoon_entry"]),
                    "strict_afternoon": bool(f["replay_strict_afternoon_entry"]),
                    "regime_control_enabled": bool(f.get("regime_control_enabled", False)),
                    "rc_skipped_signals_total": int(rca.get("skipped_signals_count_total") or 0),
                    "rc_virt_pnl_total": float(vpnl_agg) if isinstance(vpnl_agg, (int, float)) else 0.0,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float((((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0)),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== regime_control sweep: morning baseline vs full-day vs full-day+RC ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    for it in collect_debug_rows[:250]:
        out_lines.append(
            f"{it.get('cell')} run_no={int(it.get('run_no') or 0)} found={int(it.get('found_json_count') or 0)} "
            f"loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
        )
    out_lines.append("")
    out_lines.append("ソートキー: summary.avg_expectancy_yen_100_shares（同一random_aprセット内での相対順位）")
    out_lines.append("")
    hdr = (
        "rank\tcell_label\tconfig_name\treplay_range\tavg_expectancy\ttotal_pnl\tmax_lose_run\tlose_w10_sum\tplus_runs\tminus_runs\t"
        "rc_skipped_signals\trc_virt_pnl_sum\tdisable_afternoon_entry\tstrict_afternoon\tregime_control_enabled\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('cell_label')}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(r.get('rc_skipped_signals_total') or 0)}\t{float(r.get('rc_virt_pnl_total') or 0.0):+.2f}\t"
            f"{bool(r.get('disable_afternoon'))}\t{bool(r.get('strict_afternoon'))}\t{bool(r.get('regime_control_enabled'))}\t"
            f"results/{r.get('replay_output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[REGIME_CONTROL / per-cell run合算詳細]")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("regime_controls_cell_aggregate") if isinstance(s.get("regime_controls_cell_aggregate"), dict) else {}
        out_lines.append(f"cell={r.get('cell_label')}")
        if not agg:
            out_lines.append("  (no regime_controls summaries in loaded reports)")
            out_lines.append("")
            continue
        out_lines.append(f"  skipped_signals_count_total={int(agg.get('skipped_signals_count_total') or 0)}")
        src = agg.get("skip_reason_counts") or {}
        if isinstance(src, dict) and src:
            out_lines.append("  skip_reason_counts:")
            for k, v in sorted(src.items(), key=lambda kv: int(kv[1]), reverse=True):
                out_lines.append(f"    - {k}: {int(v)}")
        vap = agg.get("virtual_pnl_aggregate") if isinstance(agg.get("virtual_pnl_aggregate"), dict) else {}
        if vap:
            out_lines.append(f"  virtual.skipped_signals={int(vap.get('skipped_signals_count_total') or 0)}")
            out_lines.append(f"  virtual.total_pnl_yen={float(vap.get('total_pnl_yen_100_shares_sum') or 0.0):+,.2f}")
        emr_sum = agg.get("eval_by_market_regime_summed_over_runs") or {}
        if isinstance(emr_sum, dict):
            out_lines.append("  eval_by_market_regime (BASE採用信号・複数runの値を合算した参考集計）:")
            for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
                rr2 = emr_sum.get(rk)
                if not isinstance(rr2, dict):
                    continue
                sig_n = int(rr2.get("signals") or 0)
                pnl_tt = float(rr2.get("total_pnl_yen_100_shares") or 0.0)
                exp_aa = float(rr2.get("avg_expectancy_yen_100_shares") or 0.0)
                lw10_tt = float(rr2.get("lose_worst10_sum_yen_100_shares") or 0.0)
                out_lines.append(
                    f"    - {rk}: signals={sig_n} expectancy(ref)={exp_aa:+,.2f} total_pnl={pnl_tt:+,.2f} lose_w10_sum={lw10_tt:+,.2f}"
                )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] regime_control sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _aggregate_weak_risk_filter_sweep_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    weak-risk-filter sweep 用: composite（WEAK）skip/virtual と eval_by_market_regime を run 合算。
    """
    base = _aggregate_replay_repeat_run_summaries(run_summaries)
    comp_skip = 0
    comp_virt_skipped = 0
    comp_virt_pnl = 0.0
    mr_acc: dict[str, dict[str, float]] = {
        rk: {"signals": 0.0, "pnl_sum": 0.0, "lw10_sum": 0.0} for rk in ("STRONG", "NORMAL", "WEAK", "CRASH")
    }
    for rr in run_summaries:
        rep = rr.get("report") or {}
        ov = rep.get("overall_summary") or {}
        sf = ov.get("signal_filters") if isinstance(ov.get("signal_filters"), dict) else {}
        csf = sf.get("composite_signal_filters") if isinstance(sf.get("composite_signal_filters"), dict) else {}
        comp_skip += int(csf.get("skipped_signals_count") or 0)
        cvpa = csf.get("virtual_pnl_analysis") if isinstance(csf.get("virtual_pnl_analysis"), dict) else {}
        if cvpa:
            comp_virt_skipped += int(cvpa.get("skipped_signals_count") or 0)
            comp_virt_pnl += float(cvpa.get("total_pnl_yen_100_shares") or 0.0)
        rc = ov.get("regime_controls") if isinstance(ov.get("regime_controls"), dict) else {}
        evmr = rc.get("eval_by_market_regime") if isinstance(rc.get("eval_by_market_regime"), dict) else {}
        if evmr:
            for rk in mr_acc:
                row = evmr.get(rk)
                if not isinstance(row, dict):
                    continue
                mr_acc[rk]["signals"] += float(row.get("signals") or 0)
                mr_acc[rk]["pnl_sum"] += float(row.get("total_pnl_yen_100_shares") or 0.0)
                mr_acc[rk]["lw10_sum"] += float(row.get("lose_worst10_sum_yen_100_shares") or 0.0)

    mr_out: dict[str, dict[str, Any]] = {}
    for rk, acc in mr_acc.items():
        n_sig = int(acc["signals"])
        pnl_tot = float(acc["pnl_sum"])
        mr_out[rk] = {
            "signals": int(n_sig),
            "total_pnl_yen_100_shares": float(pnl_tot),
            "avg_expectancy_yen_100_shares": float(pnl_tot / float(n_sig)) if n_sig > 0 else 0.0,
            "lose_worst10_sum_yen_100_shares": float(acc["lw10_sum"]),
        }

    out = dict(base)
    out["weak_risk_filter_cell_aggregate"] = {
        "composite_skipped_signals_total": int(comp_skip),
        "composite_virtual_skipped_count_total": int(comp_virt_skipped),
        "composite_virtual_pnl_sum": float(comp_virt_pnl),
        "composite_virtual_avg_expectancy_if_skipped": (
            float(comp_virt_pnl / float(comp_virt_skipped)) if int(comp_virt_skipped) > 0 else 0.0
        ),
        "eval_by_market_regime_summed_over_runs": dict(mr_out),
    }
    return out


def _write_weak_risk_filter_sweep_configs(script_dir: str) -> dict[str, str]:
    """
    full_day ベースに weak_risk_filter の3モード用 JSON を configs/weak_risk_filter_sweep/ に生成。
    """
    base_rel = os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        return {}
    sweep_dir = os.path.join(script_dir, "configs", "weak_risk_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)
    modes = (
        "weak_vwap_ge_15_only",
        "weak_gap_ge_3_only",
        "weak_vwap_ge_15_and_gap_ge_3",
    )
    out_paths: dict[str, str] = {}
    for m in modes:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        bn = str(cfg.get("name") or "replay_full_day_vwap2_dd30k_rlt50")
        cfg["name"] = f"{bn}_wrf_{m}"
        cfg["composite_signal_filters"] = {
            "disable_state_weak_and_vwap_ge_pct": False,
            "disable_state_weak_and_gap_ge_pct": False,
            "state_weak_vwap_ge_threshold_pct": 1.5,
            "state_weak_gap_ge_threshold_pct": 3.0,
            "weak_risk_filter": str(m),
        }
        safe_m = m.replace(".", "_")
        fn = f"{bn}_wrf_{safe_m}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as fw:
            json.dump(cfg, fw, ensure_ascii=False, indent=2)
        out_paths[str(m)] = os.path.abspath(path)
    return out_paths


def run_weak_risk_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    WEAK×危険特徴量のみ除外の AB（random_apr のみ）。
    cells: morning_baseline, full_day_no_regime_control, 3× weak_risk_filter モード。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"weak_risk_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    p_morning = _resolve_replay_config_path(os.path.join("configs", "replay_morning_vwap2_dd30k_rlt50.json"))
    p_full = _resolve_replay_config_path(os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json"))
    mode_paths = _write_weak_risk_filter_sweep_configs(script_dir)
    if not p_morning or not p_full or len(mode_paths) < 3:
        print(f"[{now_str()}] weak_risk_filter sweep: 必要なconfigが見つかりません。")
        return 2

    cells: list[tuple[str, str, str]] = [
        ("mb", "morning_baseline", str(p_morning)),
        ("fd", "full_day_no_regime_control", str(p_full)),
    ]
    slug_for_mode = {
        "weak_vwap_ge_15_only": "wv15",
        "weak_gap_ge_3_only": "wg3",
        "weak_vwap_ge_15_and_gap_ge_3": "wand",
    }
    for mk in ("weak_vwap_ge_15_only", "weak_gap_ge_3_only", "weak_vwap_ge_15_and_gap_ge_3"):
        cells.append((slug_for_mode[mk], mk, str(mode_paths[mk])))

    print(f"[{now_str()}] weak_risk_filter sweep: cells={len(cells)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] generated configs: {list(mode_paths.values())}")

    rows: list[dict[str, Any]] = []
    for slug, label, cfg_abs in cells:
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"weak_risk_filter_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        pjson = os.path.join(results_dir, candidates_sorted[0])
                        with open(pjson, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": pjson, "report": rep})
                except Exception:
                    pass

            summ = _aggregate_weak_risk_filter_sweep_summaries(run_summaries)
            wagg = summ.get("weak_risk_filter_cell_aggregate") if isinstance(summ.get("weak_risk_filter_cell_aggregate"), dict) else {}
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "weak_risk_skipped": int(wagg.get("composite_skipped_signals_total") or 0),
                    "weak_risk_virt_pnl": float(wagg.get("composite_virtual_pnl_sum") or 0.0),
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float((((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0)),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== weak_risk_filter sweep (WEAK×危険特徴量のみ除外) ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    hdr = (
        "rank\tcell_label\tavg_expectancy_yen\ttotal_pnl_yen\tlose_worst10_sum\tplus_runs\tminus_runs\t"
        "skipped_signals(composite)\tvirtual_skipped_pnl(composite)\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('cell_label')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.4f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(r.get('weak_risk_skipped') or 0)}\t{float(r.get('weak_risk_virt_pnl') or 0.0):+.2f}\t"
            f"results/{r.get('replay_output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[EVAL_BY_MARKET_REGIME] ※各cell・複数runの eval を単純合算（参考）")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("weak_risk_filter_cell_aggregate") if isinstance(s.get("weak_risk_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        out_lines.append(f"cell={r.get('cell_label')}")
        if not emr:
            out_lines.append("  (empty)")
            out_lines.append("")
            continue
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = emr.get(rk)
            if not isinstance(row, dict):
                continue
            out_lines.append(
                f"  {rk}: signals={int(row.get('signals') or 0)} "
                f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
                f"total_pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                f"lose_w10_sum={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
            )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] weak_risk_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _aggregate_strong_risk_filter_sweep_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    strong-risk-filter sweep 用: composite（STRONG×VWAP）skip/virtual と eval_by_market_regime を run 合算。
    """
    base = _aggregate_replay_repeat_run_summaries(run_summaries)
    comp_skip = 0
    comp_virt_skipped = 0
    comp_virt_pnl = 0.0
    mr_acc: dict[str, dict[str, float]] = {
        rk: {"signals": 0.0, "pnl_sum": 0.0, "lw10_sum": 0.0} for rk in ("STRONG", "NORMAL", "WEAK", "CRASH")
    }
    for rr in run_summaries:
        rep = rr.get("report") or {}
        ov = rep.get("overall_summary") or {}
        sf = ov.get("signal_filters") if isinstance(ov.get("signal_filters"), dict) else {}
        csf = sf.get("composite_signal_filters") if isinstance(sf.get("composite_signal_filters"), dict) else {}
        comp_skip += int(csf.get("skipped_signals_count") or 0)
        cvpa = csf.get("virtual_pnl_analysis") if isinstance(csf.get("virtual_pnl_analysis"), dict) else {}
        if cvpa:
            comp_virt_skipped += int(cvpa.get("skipped_signals_count") or 0)
            comp_virt_pnl += float(cvpa.get("total_pnl_yen_100_shares") or 0.0)
        rc = ov.get("regime_controls") if isinstance(ov.get("regime_controls"), dict) else {}
        evmr = rc.get("eval_by_market_regime") if isinstance(rc.get("eval_by_market_regime"), dict) else {}
        if evmr:
            for rk in mr_acc:
                row = evmr.get(rk)
                if not isinstance(row, dict):
                    continue
                mr_acc[rk]["signals"] += float(row.get("signals") or 0)
                mr_acc[rk]["pnl_sum"] += float(row.get("total_pnl_yen_100_shares") or 0.0)
                mr_acc[rk]["lw10_sum"] += float(row.get("lose_worst10_sum_yen_100_shares") or 0.0)

    mr_out: dict[str, dict[str, Any]] = {}
    for rk, acc in mr_acc.items():
        n_sig = int(acc["signals"])
        pnl_tot = float(acc["pnl_sum"])
        mr_out[rk] = {
            "signals": int(n_sig),
            "total_pnl_yen_100_shares": float(pnl_tot),
            "avg_expectancy_yen_100_shares": float(pnl_tot / float(n_sig)) if n_sig > 0 else 0.0,
            "lose_worst10_sum_yen_100_shares": float(acc["lw10_sum"]),
        }

    out = dict(base)
    out["strong_risk_filter_cell_aggregate"] = {
        "composite_skipped_signals_total": int(comp_skip),
        "composite_virtual_skipped_count_total": int(comp_virt_skipped),
        "composite_virtual_pnl_sum": float(comp_virt_pnl),
        "composite_virtual_avg_expectancy_if_skipped": (
            float(comp_virt_pnl / float(comp_virt_skipped)) if int(comp_virt_skipped) > 0 else 0.0
        ),
        "eval_by_market_regime_summed_over_runs": dict(mr_out),
    }
    return out


def _write_strong_risk_filter_sweep_configs(script_dir: str) -> dict[str, str]:
    """
    full_day ベースに strong_risk_filter 用 JSON を configs/strong_risk_filter_sweep/ に生成。
    """
    base_rel = os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        return {}
    sweep_dir = os.path.join(script_dir, "configs", "strong_risk_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)
    modes_thr: tuple[tuple[str, float], ...] = (
        ("strong_vwap_ge_15_only", 1.5),
        ("strong_vwap_ge_12_only", 1.2),
        ("strong_vwap_ge_10_only", 1.0),
    )
    out_paths: dict[str, str] = {}
    for m, thr in modes_thr:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        bn = str(cfg.get("name") or "replay_full_day_vwap2_dd30k_rlt50")
        cfg["name"] = f"{bn}_srf_{m}"
        cfg["composite_signal_filters"] = {
            "strong_risk_filter": str(m),
            "strong_vwap_ge_threshold_pct": float(thr),
        }
        safe_m = m.replace(".", "_")
        fn = f"{bn}_srf_{safe_m}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as fw:
            json.dump(cfg, fw, ensure_ascii=False, indent=2)
        out_paths[str(m)] = os.path.abspath(path)
    return out_paths


def run_strong_risk_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    STRONG×VWAP距離 の composite_signal_filters.strong_risk_filter を AB（random_apr のみ）。
    cells: full_day_no_regime_control, strong_vwap_ge_15_only / _12_only / _10_only。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges: tuple[str, ...] = ("random_apr",)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"strong_risk_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    p_full = _resolve_replay_config_path(os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json"))
    mode_paths = _write_strong_risk_filter_sweep_configs(script_dir)
    if not p_full or len(mode_paths) < 3:
        print(f"[{now_str()}] strong_risk_filter sweep: 必要なconfigが見つかりません。")
        return 2

    slug_for_mode = {
        "strong_vwap_ge_15_only": "sv15",
        "strong_vwap_ge_12_only": "sv12",
        "strong_vwap_ge_10_only": "sv10",
    }
    cells: list[tuple[str, str, str]] = [
        ("fd", "full_day_no_regime_control", str(p_full)),
    ]
    for mk in ("strong_vwap_ge_15_only", "strong_vwap_ge_12_only", "strong_vwap_ge_10_only"):
        cells.append((slug_for_mode[mk], mk, str(mode_paths[mk])))

    print(f"[{now_str()}] strong_risk_filter sweep: cells={len(cells)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] generated configs: {list(mode_paths.values())}")

    rows: list[dict[str, Any]] = []
    for slug, label, cfg_abs in cells:
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"strong_risk_filter_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        pjson = os.path.join(results_dir, candidates_sorted[0])
                        with open(pjson, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": pjson, "report": rep})
                except Exception:
                    pass

            summ = _aggregate_strong_risk_filter_sweep_summaries(run_summaries)
            sagg = summ.get("strong_risk_filter_cell_aggregate") if isinstance(summ.get("strong_risk_filter_cell_aggregate"), dict) else {}
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "strong_risk_skipped": int(sagg.get("composite_skipped_signals_total") or 0),
                    "strong_risk_virt_pnl": float(sagg.get("composite_virtual_pnl_sum") or 0.0),
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float((((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0)),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== strong_risk_filter sweep (STRONG×VWAP距離>=しきい値でENTRY除外) ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    hdr = (
        "rank\tcell_label\tavg_expectancy_yen\ttotal_pnl_yen\tlose_worst10_sum\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tskipped_signals(composite)\tvirtual_skipped_pnl(composite)\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('cell_label')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.4f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(r.get('strong_risk_skipped') or 0)}\t{float(r.get('strong_risk_virt_pnl') or 0.0):+.2f}\t"
            f"results/{r.get('replay_output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[EVAL_BY_MARKET_REGIME] ※各cell・複数runの eval を単純合算（参考）")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("strong_risk_filter_cell_aggregate") if isinstance(s.get("strong_risk_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        out_lines.append(f"cell={r.get('cell_label')}")
        if not emr:
            out_lines.append("  (empty)")
            out_lines.append("")
            continue
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = emr.get(rk)
            if not isinstance(row, dict):
                continue
            out_lines.append(
                f"  {rk}: signals={int(row.get('signals') or 0)} "
                f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
                f"total_pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                f"lose_w10_sum={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
            )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] strong_risk_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _aggregate_strong_combo_filter_sweep_summaries(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    strong-combo-filter sweep 用: strong_combo skip/virtual と eval_by_market_regime を run 合算。
    """
    base = _aggregate_replay_repeat_run_summaries(run_summaries)
    combo_skip = 0
    combo_virt_pnl = 0.0
    combo_virt_resolved = 0
    mr_acc: dict[str, dict[str, float]] = {
        rk: {"signals": 0.0, "pnl_sum": 0.0, "lw10_sum": 0.0} for rk in ("STRONG", "NORMAL", "WEAK", "CRASH")
    }
    for rr in run_summaries:
        rep = rr.get("report") or {}
        cf = _combo_filter_analysis_dict_from_report(rep)
        sc = cf.get("strong_combo_filter") if isinstance(cf.get("strong_combo_filter"), dict) else {}
        combo_skip += int(sc.get("skipped_signals_count") or 0)
        vpa = sc.get("virtual_pnl_analysis") if isinstance(sc.get("virtual_pnl_analysis"), dict) else {}
        combo_virt_pnl += float(vpa.get("total_pnl_yen_100_shares") or 0.0)
        br = vpa.get("by_reason") if isinstance(vpa.get("by_reason"), dict) else {}
        for row in br.values():
            if isinstance(row, dict):
                combo_virt_resolved += int(row.get("virtual_resolved_count") or 0)
        ov = rep.get("overall_summary") or {}
        rc = ov.get("regime_controls") if isinstance(ov.get("regime_controls"), dict) else {}
        evmr = rc.get("eval_by_market_regime") if isinstance(rc.get("eval_by_market_regime"), dict) else {}
        if evmr:
            for rk in mr_acc:
                row = evmr.get(rk)
                if not isinstance(row, dict):
                    continue
                mr_acc[rk]["signals"] += float(row.get("signals") or 0)
                mr_acc[rk]["pnl_sum"] += float(row.get("total_pnl_yen_100_shares") or 0.0)
                mr_acc[rk]["lw10_sum"] += float(row.get("lose_worst10_sum_yen_100_shares") or 0.0)

    mr_out: dict[str, dict[str, Any]] = {}
    for rk, acc in mr_acc.items():
        n_sig = int(acc["signals"])
        pnl_tot = float(acc["pnl_sum"])
        mr_out[rk] = {
            "signals": int(n_sig),
            "total_pnl_yen_100_shares": float(pnl_tot),
            "avg_expectancy_yen_100_shares": float(pnl_tot / float(n_sig)) if n_sig > 0 else 0.0,
            "lose_worst10_sum_yen_100_shares": float(acc["lw10_sum"]),
        }

    out = dict(base)
    out["strong_combo_filter_cell_aggregate"] = {
        "combo_skipped_signals_total": int(combo_skip),
        "combo_virtual_resolved_total": int(combo_virt_resolved),
        "combo_virtual_pnl_sum": float(combo_virt_pnl),
        "combo_virtual_avg_expectancy_if_skipped": (
            float(combo_virt_pnl / float(combo_virt_resolved)) if int(combo_virt_resolved) > 0 else 0.0
        ),
        "eval_by_market_regime_summed_over_runs": dict(mr_out),
    }
    return out


def _write_strong_combo_filter_sweep_configs(script_dir: str) -> dict[str, str]:
    """
    configs/strong_combo_filter_sweep/ に HU2 / HU1or2 の strong_combo_filter を書き出す。
    """
    base_rel = os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        return {}
    sweep_dir = os.path.join(script_dir, "configs", "strong_combo_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)
    variants: dict[str, dict[str, Any]] = {
        "hu2_vwap15": {
            "enabled": True,
            "block_conditions": [
                {
                    "market_regime": "STRONG",
                    "high_update_count_before_entry_eq": 2,
                    "entry_vwap_distance_pct_ge": 1.5,
                    "reason": "STRONG_HU2_VWAP15",
                }
            ],
        },
        "hu1or2_vwap15": {
            "enabled": True,
            "block_conditions": [
                {
                    "market_regime": "STRONG",
                    "high_update_count_before_entry_eq": 2,
                    "entry_vwap_distance_pct_ge": 1.5,
                    "reason": "STRONG_HU2_VWAP15",
                },
                {
                    "market_regime": "STRONG",
                    "high_update_count_before_entry_eq": 1,
                    "entry_vwap_distance_pct_ge": 1.5,
                    "reason": "STRONG_HU1_VWAP15",
                },
            ],
        },
    }
    out_paths: dict[str, str] = {}
    for slug, sc_body in variants.items():
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        bn = str(cfg.get("name") or "replay_full_day_vwap2_dd30k_rlt50")
        cfg["name"] = f"{bn}_scf_{slug}"
        csf0 = cfg.get("composite_signal_filters") if isinstance(cfg.get("composite_signal_filters"), dict) else {}
        csf_m = dict(csf0)
        csf_m["strong_combo_filter"] = dict(sc_body)
        cfg["composite_signal_filters"] = csf_m
        fn = f"{bn}_scf_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as fw:
            json.dump(cfg, fw, ensure_ascii=False, indent=2)
        out_paths[str(slug)] = os.path.abspath(path)
    return out_paths


def run_strong_combo_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    strong_combo_filter（高値更新×VWAP）の AB（random_apr のみ）。
    cells: baseline, HU2_VWAP15, HU1or2_VWAP15。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges: tuple[str, ...] = ("random_apr",)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"strong_combo_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    p_full = _resolve_replay_config_path(os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json"))
    mode_paths = _write_strong_combo_filter_sweep_configs(script_dir)
    if not p_full or len(mode_paths) < 2:
        print(f"[{now_str()}] strong_combo_filter sweep: 必要なconfigが見つかりません。")
        return 2

    cells: list[tuple[str, str, str]] = [
        ("fd", "baseline", str(p_full)),
        ("hu2", "HU2_VWAP15", str(mode_paths.get("hu2_vwap15") or "")),
        ("hu12", "HU1or2_VWAP15", str(mode_paths.get("hu1or2_vwap15") or "")),
    ]

    print(f"[{now_str()}] strong_combo_filter sweep: cells={len(cells)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] generated configs: {list(mode_paths.values())}")

    rows: list[dict[str, Any]] = []
    for slug, label, cfg_abs in cells:
        if not cfg_abs:
            continue
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"strong_combo_filter_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        pjson = os.path.join(results_dir, candidates_sorted[0])
                        with open(pjson, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": pjson, "report": rep})
                except Exception:
                    pass

            summ = _aggregate_strong_combo_filter_sweep_summaries(run_summaries)
            cagg = summ.get("strong_combo_filter_cell_aggregate") if isinstance(summ.get("strong_combo_filter_cell_aggregate"), dict) else {}
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "combo_skipped": int(cagg.get("combo_skipped_signals_total") or 0),
                    "combo_virt_pnl": float(cagg.get("combo_virtual_pnl_sum") or 0.0),
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float((((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0)),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== strong_combo_filter sweep（STRONG×高値更新回数×VWAP距離） ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    hdr = (
        "rank\tcell_label\tavg_expectancy_yen\ttotal_pnl_yen\tlose_worst10_sum\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tskipped_signals(combo)\tvirtual_skipped_pnl(combo)\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('cell_label')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.4f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(r.get('combo_skipped') or 0)}\t{float(r.get('combo_virt_pnl') or 0.0):+.2f}\t"
            f"results/{r.get('replay_output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[EVAL_BY_MARKET_REGIME] ※各cell・複数runの eval を単純合算（参考）")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("strong_combo_filter_cell_aggregate") if isinstance(s.get("strong_combo_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        out_lines.append(f"cell={r.get('cell_label')}")
        if not emr:
            out_lines.append("  (empty)")
            out_lines.append("")
            continue
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = emr.get(rk)
            if not isinstance(row, dict):
                continue
            out_lines.append(
                f"  {rk}: signals={int(row.get('signals') or 0)} "
                f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
                f"total_pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                f"lose_w10_sum={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
            )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] strong_combo_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _write_strong_trend_quality_sweep_configs(script_dir: str) -> dict[str, str]:
    """
    configs/strong_trend_quality_sweep/ に STRONG×VWAP≥1.5×高値更新（le / ge6のみ許可）用 strong_combo_filter を書き出す。
    """
    base_rel = os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        return {}
    sweep_dir = os.path.join(script_dir, "configs", "strong_trend_quality_sweep")
    os.makedirs(sweep_dir, exist_ok=True)
    variants: dict[str, dict[str, Any]] = {
        "hu_le2": {
            "enabled": True,
            "block_conditions": [
                {
                    "market_regime": "STRONG",
                    "entry_vwap_distance_pct_ge": 1.5,
                    "high_update_count_before_entry_le": 2,
                    "reason": "STRONG_VWAP15_HU_LE2_SKIP",
                }
            ],
        },
        "hu_le3": {
            "enabled": True,
            "block_conditions": [
                {
                    "market_regime": "STRONG",
                    "entry_vwap_distance_pct_ge": 1.5,
                    "high_update_count_before_entry_le": 3,
                    "reason": "STRONG_VWAP15_HU_LE3_SKIP",
                }
            ],
        },
        "hu_ge6_allow": {
            "enabled": True,
            "block_conditions": [
                {
                    "market_regime": "STRONG",
                    "entry_vwap_distance_pct_ge": 1.5,
                    "high_update_count_before_entry_le": 5,
                    "reason": "STRONG_VWAP15_HU_LT6_SKIP_GE6_ONLY_ALLOW",
                }
            ],
        },
    }
    out_paths: dict[str, str] = {}
    for slug, sc_body in variants.items():
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        bn = str(cfg.get("name") or "replay_full_day_vwap2_dd30k_rlt50")
        cfg["name"] = f"{bn}_stq_{slug}"
        csf0 = cfg.get("composite_signal_filters") if isinstance(cfg.get("composite_signal_filters"), dict) else {}
        csf_m = dict(csf0)
        csf_m["strong_combo_filter"] = dict(sc_body)
        cfg["composite_signal_filters"] = csf_m
        fn = f"{bn}_stq_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as fw:
            json.dump(cfg, fw, ensure_ascii=False, indent=2)
        out_paths[str(slug)] = os.path.abspath(path)
    return out_paths


def run_strong_trend_quality_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    strong_combo_filter を使い、STRONG で VWAP 乖離が大きいときの「高値更新の質」を比較する sweep。
    cells: baseline, hu_le2_skip, hu_le3_skip, hu_ge6_only_allow。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges: tuple[str, ...] = ("random_apr",)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"strong_trend_quality_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    p_full = _resolve_replay_config_path(os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json"))
    mode_paths = _write_strong_trend_quality_sweep_configs(script_dir)
    if not p_full or len(mode_paths) < 3:
        print(f"[{now_str()}] strong_trend_quality sweep: 必要なconfigが見つかりません。")
        return 2

    cells: list[tuple[str, str, str]] = [
        ("fd", "baseline", str(p_full)),
        ("le2", "strong_vwap_ge_15_and_hu_le2_skip", str(mode_paths.get("hu_le2") or "")),
        ("le3", "strong_vwap_ge_15_and_hu_le3_skip", str(mode_paths.get("hu_le3") or "")),
        ("ge6", "strong_vwap_ge_15_and_hu_ge6_only_allow", str(mode_paths.get("hu_ge6_allow") or "")),
    ]

    print(f"[{now_str()}] strong_trend_quality sweep: cells={len(cells)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] generated configs: {list(mode_paths.values())}")

    rows: list[dict[str, Any]] = []
    for slug, label, cfg_abs in cells:
        if not cfg_abs:
            continue
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"strong_trend_quality_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        pjson = os.path.join(results_dir, candidates_sorted[0])
                        with open(pjson, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": pjson, "report": rep})
                except Exception:
                    pass

            summ = _aggregate_strong_combo_filter_sweep_summaries(run_summaries)
            cagg = summ.get("strong_combo_filter_cell_aggregate") if isinstance(summ.get("strong_combo_filter_cell_aggregate"), dict) else {}
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "combo_skipped": int(cagg.get("combo_skipped_signals_total") or 0),
                    "combo_virt_pnl": float(cagg.get("combo_virtual_pnl_sum") or 0.0),
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float((((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0)),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== strong_trend_quality sweep（STRONG×VWAP乖離×高値更新の質） ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    hdr = (
        "rank\tcell_label\tavg_expectancy_yen\ttotal_pnl_yen\tlose_worst10_sum\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tskipped_signals(combo)\tvirtual_skipped_pnl(combo)\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('cell_label')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.4f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{int(r.get('combo_skipped') or 0)}\t{float(r.get('combo_virt_pnl') or 0.0):+.2f}\t"
            f"results/{r.get('replay_output_subdir')}/"
        )

    out_lines.append("")
    out_lines.append("[STRONG regime · focus] expectancy / lose_worst10_sum / skipped_signals(combo) / virtual_skipped_pnl(combo)")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("strong_combo_filter_cell_aggregate") if isinstance(s.get("strong_combo_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        sr = emr.get("STRONG") if isinstance(emr.get("STRONG"), dict) else {}
        out_lines.append(
            f"  {r.get('cell_label')}: "
            f"STRONG_exp={float(sr.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
            f"STRONG_lw10_sum={float(sr.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f} "
            f"skipped_combo={int(r.get('combo_skipped') or 0)} "
            f"virt_skipped_pnl_combo={float(r.get('combo_virt_pnl') or 0.0):+.2f}"
        )

    out_lines.append("")
    out_lines.append("[EVAL_BY_MARKET_REGIME] ※各cell・複数runの eval を単純合算（参考）")
    for r in rows_sorted:
        s = r.get("summary") or {}
        agg = s.get("strong_combo_filter_cell_aggregate") if isinstance(s.get("strong_combo_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        out_lines.append(f"cell={r.get('cell_label')}")
        if not emr:
            out_lines.append("  (empty)")
            out_lines.append("")
            continue
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = emr.get(rk)
            if not isinstance(row, dict):
                continue
            out_lines.append(
                f"  {rk}: signals={int(row.get('signals') or 0)} "
                f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
                f"total_pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                f"lose_w10_sum={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
            )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] strong_trend_quality sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def run_strong_trend_quality_validation_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    baseline vs STRONG×VWAP≥1.5×HU≤2 skip を random_apr / random_mar / random_60d で再現性検証する。
    run_i の seed は replay_seed + i - 1（同一 run_index で baseline と variant が同じ抽選になる）。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges: tuple[str, ...] = ("random_apr", "random_mar", "random_60d")
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"strong_trend_quality_validation_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    p_full = _resolve_replay_config_path(os.path.join("configs", "replay_full_day_vwap2_dd30k_rlt50.json"))
    mode_paths = _write_strong_trend_quality_sweep_configs(script_dir)
    p_le2 = str(mode_paths.get("hu_le2") or "")
    if not p_full or not p_le2:
        print(f"[{now_str()}] strong_trend_quality_validation sweep: 必要なconfigが見つかりません。")
        return 2

    cells: list[tuple[str, str, str]] = [
        ("fd", "baseline", str(p_full)),
        ("le2", "strong_vwap_ge_15_and_hu_le2_skip", str(p_le2)),
    ]

    print(f"[{now_str()}] strong_trend_quality_validation sweep: cells={len(cells)} ranges={ranges} repeat={int(n_repeat)}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] replay_seed(base): {replay_seed}  (run_i seed = base + i - 1)")
    print(f"[{now_str()}] hu_le2 config: {p_le2}")

    rows: list[dict[str, Any]] = []
    for slug, label, cfg_abs in cells:
        if not cfg_abs:
            continue
        cfg_raw = _load_replay_config(cfg_abs)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_abs))
        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{slug}_{rng}"
            output_subdir = os.path.join(f"strong_trend_quality_validation_sweep_{sweep_stamp}", f"{slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {label} ({slug})  {rng}  ({int(n_repeat)} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            cr_status = "OK"
            cr_reason = ""
            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_abs),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    signal_filter_disable_gap_ge_pct=bool(f.get("signal_filter_disable_gap_ge_pct", False)),
                    signal_filter_gap_ge_threshold_pct=float(f.get("signal_filter_gap_ge_threshold_pct", 3.0)),
                    signal_filter_disable_vwap_distance_ge_pct=bool(f.get("signal_filter_disable_vwap_distance_ge_pct", False)),
                    signal_filter_vwap_distance_ge_threshold_pct=float(
                        f.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
                    ),
                    signal_filter_disable_entry_after_hhmm=bool(f.get("signal_filter_disable_entry_after_hhmm", False)),
                    signal_filter_entry_after_hhmm=str(f.get("signal_filter_entry_after_hhmm", "10:30")),
                    **_replay_composite_signal_filter_kwargs_from_flags(f),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                ic = int(code)
                if ic != 0:
                    cr_reason = f"run_replay exit={ic} (run={i})"
                    if ic == 2:
                        cr_status = "SKIPPED_NO_DATA"
                        print(
                            f"[{now_str()}] validation sweep SKIP: replay_range={rng} "
                            f"cell_label={label} reason={cr_reason} skipped due to no replay data"
                        )
                    else:
                        cr_status = "ERROR"
                        print(
                            f"[{now_str()}] validation sweep ERROR: replay_range={rng} "
                            f"cell_label={label} reason={cr_reason}"
                        )
                    break

                try:
                    run_tag = f"run{i:02d}"
                    candidates = (
                        [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                            and (f"_{run_tag}.json" in fn)
                        ]
                        if int(n_repeat) > 1
                        else [
                            fn
                            for fn in os.listdir(results_dir)
                            if fn.endswith(".json")
                            and ("replay_summary_" in fn)
                            and (not fn.endswith("_symbol_scores.json"))
                        ]
                    )
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        pjson = os.path.join(results_dir, candidates_sorted[0])
                        with open(pjson, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": pjson, "report": rep})
                except Exception:
                    pass

            if cr_status != "OK":
                rows.append(
                    {
                        "cell_slug": str(slug),
                        "cell_label": str(label),
                        "config_name": str(cfg_name),
                        "replay_range": str(rng),
                        "replay_output_subdir": str(output_subdir),
                        "summary": {},
                        "combo_skipped": None,
                        "combo_virt_pnl": None,
                        "status": str(cr_status),
                        "skip_reason": str(cr_reason),
                    }
                )
                continue

            summ = _aggregate_strong_combo_filter_sweep_summaries(run_summaries)
            cagg = summ.get("strong_combo_filter_cell_aggregate") if isinstance(summ.get("strong_combo_filter_cell_aggregate"), dict) else {}
            rows.append(
                {
                    "cell_slug": str(slug),
                    "cell_label": str(label),
                    "config_name": str(cfg_name),
                    "replay_range": str(rng),
                    "replay_output_subdir": str(output_subdir),
                    "summary": summ,
                    "combo_skipped": int(cagg.get("combo_skipped_signals_total") or 0),
                    "combo_virt_pnl": float(cagg.get("combo_virtual_pnl_sum") or 0.0),
                    "status": "OK",
                    "skip_reason": "",
                }
            )

    _rng_order = {"random_apr": 0, "random_mar": 1, "random_60d": 2}
    _cell_order = {"fd": 0, "le2": 1}
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            _rng_order.get(str(r.get("replay_range")), 99),
            _cell_order.get(str(r.get("cell_slug")), 99),
        ),
    )

    out_lines: list[str] = []
    out_lines.append("=== strong_trend_quality_validation sweep（baseline vs HU≤2×VWAP15・多月検証） ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"ranges: {', '.join(ranges)}")
    out_lines.append(f"repeat_per_cell_range: {int(n_repeat)}")
    out_lines.append(f"replay_seed(base): {replay_seed}")
    out_lines.append(f"run_i_seed: base + i - 1  (i=1..{int(n_repeat)})")
    out_lines.append("")
    hdr = (
        "rank\treplay_range\tcell_label\tstatus\tavg_expectancy_yen\ttotal_pnl_yen\tlose_worst10_sum\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tskipped_signals(combo)\tvirtual_skipped_pnl(combo)\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        st_r = str(r.get("status") or "OK")
        s = r.get("summary") or {}
        if st_r != "OK":
            na = "N/A"
            out_lines.append(
                f"{idx}\t{r.get('replay_range')}\t{r.get('cell_label')}\t{st_r}\t"
                f"{na}\t{na}\t{na}\t{na}\t"
                f"{na}\t{na}\t{na}\t{na}\t"
                f"results/{r.get('replay_output_subdir')}/"
            )
        else:
            out_lines.append(
                f"{idx}\t{r.get('replay_range')}\t{r.get('cell_label')}\t{st_r}\t"
                f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.4f}\t"
                f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
                f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
                f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
                f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
                f"{int(r.get('combo_skipped') or 0)}\t{float(r.get('combo_virt_pnl') or 0.0):+.2f}\t"
                f"results/{r.get('replay_output_subdir')}/"
            )

    out_lines.append("")
    out_lines.append("[DELTA VS BASELINE]")
    out_lines.append(
        "variant=strong_vwap_ge_15_and_hu_le2_skip minus baseline "
        "(same replay_range; run_i uses same seed schedule on both cells). "
        "If either side is not OK, deltas are N/A."
    )
    baseline_by_rng: dict[str, dict[str, Any]] = {}
    for r in rows:
        if str(r.get("cell_slug")) == "fd":
            baseline_by_rng[str(r.get("replay_range"))] = r
    for rng in ranges:
        b_row = baseline_by_rng.get(str(rng))
        v_row = next(
            (x for x in rows if str(x.get("cell_slug")) == "le2" and str(x.get("replay_range")) == str(rng)),
            None,
        )
        out_lines.append("")
        out_lines.append(f"replay_range={rng}")
        if not b_row or not v_row:
            out_lines.append("  (baseline or variant row missing — delta N/A)")
            continue
        st_b = str(b_row.get("status") or "OK")
        st_v = str(v_row.get("status") or "OK")
        if st_b != "OK" or st_v != "OK":
            out_lines.append(f"  baseline_status={st_b} variant_status={st_v}")
            out_lines.append("  delta_total_pnl: N/A")
            out_lines.append("  delta_lose_worst10_sum: N/A")
            out_lines.append("  delta_expectancy: N/A")
            out_lines.append("  delta_max_lose_run: N/A")
            continue
        sb = b_row.get("summary") or {}
        sv = v_row.get("summary") or {}
        d_pnl = float(sv.get("total_pnl_yen_100_shares") or 0.0) - float(sb.get("total_pnl_yen_100_shares") or 0.0)
        d_lw = float(sv.get("sum_lose_worst10_yen_100_shares") or 0.0) - float(sb.get("sum_lose_worst10_yen_100_shares") or 0.0)
        d_exp = float(sv.get("avg_expectancy_yen_100_shares") or 0.0) - float(sb.get("avg_expectancy_yen_100_shares") or 0.0)
        d_mlr = float(sv.get("max_lose_run_pnl_yen_100_shares") or 0.0) - float(sb.get("max_lose_run_pnl_yen_100_shares") or 0.0)
        out_lines.append(f"  delta_total_pnl: {d_pnl:+.2f}")
        out_lines.append(f"  delta_lose_worst10_sum: {d_lw:+.2f}")
        out_lines.append(f"  delta_expectancy: {d_exp:+.4f}")
        out_lines.append(f"  delta_max_lose_run: {d_mlr:+.2f}")

    out_lines.append("")
    out_lines.append("[STRONG regime · focus] expectancy / lose_worst10_sum / skipped_signals(combo) / virtual_skipped_pnl(combo)")
    for r in rows_sorted:
        st_r = str(r.get("status") or "OK")
        if st_r != "OK":
            out_lines.append(
                f"  [{r.get('replay_range')}] {r.get('cell_label')}: status={st_r} "
                f"(STRONG metrics N/A; {r.get('skip_reason') or 'no detail'})"
            )
            continue
        s = r.get("summary") or {}
        agg = s.get("strong_combo_filter_cell_aggregate") if isinstance(s.get("strong_combo_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        sr = emr.get("STRONG") if isinstance(emr.get("STRONG"), dict) else {}
        out_lines.append(
            f"  [{r.get('replay_range')}] {r.get('cell_label')}: "
            f"STRONG_exp={float(sr.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
            f"STRONG_lw10_sum={float(sr.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f} "
            f"skipped_combo={int(r.get('combo_skipped') or 0)} "
            f"virt_skipped_pnl_combo={float(r.get('combo_virt_pnl') or 0.0):+.2f}"
        )

    out_lines.append("")
    out_lines.append("[EVAL_BY_MARKET_REGIME] ※各cell・range・複数runの eval を単純合算（参考）")
    for r in rows_sorted:
        st_r = str(r.get("status") or "OK")
        out_lines.append(f"replay_range={r.get('replay_range')} cell={r.get('cell_label')} status={st_r}")
        if st_r != "OK":
            out_lines.append(f"  (skipped — no aggregate; reason: {r.get('skip_reason') or 'n/a'})")
            out_lines.append("")
            continue
        summ = r.get("summary") or {}
        agg = summ.get("strong_combo_filter_cell_aggregate") if isinstance(summ.get("strong_combo_filter_cell_aggregate"), dict) else {}
        emr = agg.get("eval_by_market_regime_summed_over_runs") if isinstance(agg.get("eval_by_market_regime_summed_over_runs"), dict) else {}
        if not emr:
            out_lines.append("  (empty)")
            out_lines.append("")
            continue
        for rk in ("STRONG", "NORMAL", "WEAK", "CRASH"):
            row = emr.get(rk)
            if not isinstance(row, dict):
                continue
            out_lines.append(
                f"  {rk}: signals={int(row.get('signals') or 0)} "
                f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+.4f} "
                f"total_pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                f"lose_w10_sum={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
            )
        out_lines.append("")

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as fw:
        fw.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] strong_trend_quality_validation sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _bucket_label_by_edges(x: Optional[float], edges: list[float], labels: list[str]) -> str:
    """
    edges: 境界値（昇順）. len(labels) = len(edges)+1
    """
    if x is None or (not isinstance(x, (int, float))) or (not math.isfinite(float(x))):
        return "N/A"
    v = float(x)
    for i, e in enumerate(edges):
        if v <= float(e):
            return str(labels[i])
    return str(labels[-1])


def _build_signal_feature_analysis_from_signal_dicts(signal_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    all_runs 用（signals dict から集計）。
    """
    # feature -> bucket -> accum
    acc: dict[str, dict[str, dict[str, Any]]] = {}

    def _add(feature: str, bucket: str, pnl: float, is_win: bool, is_lose: bool) -> None:
        acc.setdefault(feature, {})
        a = acc[feature].setdefault(bucket, {"signals": 0, "win": 0, "lose": 0, "pnl_sum": 0.0, "pnls": []})
        a["signals"] = int(a.get("signals", 0)) + 1
        if bool(is_win):
            a["win"] = int(a.get("win", 0)) + 1
        if bool(is_lose):
            a["lose"] = int(a.get("lose", 0)) + 1
        a["pnl_sum"] = float(a.get("pnl_sum", 0.0)) + float(pnl)
        try:
            a["pnls"].append(float(pnl))
        except Exception:
            pass

    # bucket definitions（ユーザー要望）
    gap_edges = [-3.0, -1.0, 1.0, 3.0]
    gap_labels = ["<=-3", "-3~-1", "-1~1", "1~3", ">=3"]

    vdist_edges = [0.5, 1.0, 1.5, 2.0]
    vdist_labels = ["<=0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", ">=2.0"]

    vol30_edges = [1.0, 2.0, 3.0, 5.0]
    vol30_labels = ["<1", "1~2", "2~3", "3~5", ">=5"]

    atr_edges = [1.0, 2.0, 3.0]
    atr_labels = ["<1", "1~2", "2~3", ">=3"]

    for s in signal_dicts:
        if not isinstance(s, dict):
            continue
        # eval対象のみ
        if bool(s.get("excluded_from_eval", False)):
            continue
        pnl = float(s.get("pnl_yen_100_shares") or 0.0)
        res = str(s.get("result") or "")
        is_win = (res == "WIN")
        is_lose = (res == "LOSE")

        gap = s.get("gap_pct")
        vdist = s.get("entry_vwap_distance_pct")
        vol30 = s.get("first_30m_volume_ratio")
        atrp = s.get("atr_pct")

        _add("gap_pct", _bucket_label_by_edges(gap, gap_edges, gap_labels), pnl, is_win, is_lose)
        _add("entry_vwap_distance_pct", _bucket_label_by_edges(vdist, vdist_edges, vdist_labels), pnl, is_win, is_lose)
        _add("first_30m_volume_ratio", _bucket_label_by_edges(vol30, vol30_edges, vol30_labels), pnl, is_win, is_lose)
        _add("atr_pct", _bucket_label_by_edges(atrp, atr_edges, atr_labels), pnl, is_win, is_lose)

    out: dict[str, Any] = {}
    for feat, buckets in acc.items():
        rows: list[dict[str, Any]] = []
        for b, a in buckets.items():
            sigs = int(a.get("signals", 0))
            win = int(a.get("win", 0))
            lose = int(a.get("lose", 0))
            pnl_sum = float(a.get("pnl_sum", 0.0))
            winrate = (float(win) / float(sigs) * 100.0) if sigs > 0 else 0.0
            exp = (float(pnl_sum) / float(sigs)) if sigs > 0 else 0.0
            pnls = a.get("pnls") or []
            rows.append(
                {
                    "bucket": str(b),
                    "signals": int(sigs),
                    "winrate_pct": float(winrate),
                    "avg_expectancy_yen_100_shares": float(exp),
                    "total_pnl_yen_100_shares": float(pnl_sum),
                    "lose_worst10_sum_yen_100_shares": float(_lose_worst10_sum_yen_100_shares_from_pnls(pnls if isinstance(pnls, list) else [])),
                }
            )
        # signals desc で見やすく
        out[feat] = sorted(rows, key=lambda x: int(x.get("signals", 0)), reverse=True)
    return out


def _build_composite_signal_feature_analysis_from_signal_dicts(signal_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    gap / entry_vwap_distance と market_regime / entry_time_bucket の 2次元集計。
    """
    gap_edges = [-3.0, -1.0, 1.0, 3.0]
    gap_labels = ["<=-3", "-3~-1", "-1~1", "1~3", ">=3"]
    vdist_edges = [0.5, 1.0, 1.5, 2.0]
    vdist_labels = ["<=0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", ">=2.0"]

    # analysis_name -> (key1, key2) -> accum
    acc: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    def _add_cross(name: str, k1: str, k2: str, pnl: float, is_win: bool, is_lose: bool) -> None:
        acc.setdefault(name, {})
        key = (str(k1), str(k2))
        a = acc[name].setdefault(key, {"signals": 0, "win": 0, "lose": 0, "pnl_sum": 0.0, "pnls": []})
        a["signals"] = int(a.get("signals", 0)) + 1
        if bool(is_win):
            a["win"] = int(a.get("win", 0)) + 1
        if bool(is_lose):
            a["lose"] = int(a.get("lose", 0)) + 1
        a["pnl_sum"] = float(a.get("pnl_sum", 0.0)) + float(pnl)
        try:
            a["pnls"].append(float(pnl))
        except Exception:
            pass

    for s in signal_dicts:
        if not isinstance(s, dict):
            continue
        if bool(s.get("excluded_from_eval", False)):
            continue
        pnl = float(s.get("pnl_yen_100_shares") or 0.0)
        res = str(s.get("result") or "")
        is_win = res == "WIN"
        is_lose = res == "LOSE"
        gap = s.get("gap_pct")
        vdist = s.get("entry_vwap_distance_pct")
        gap_b = _bucket_label_by_edges(gap, gap_edges, gap_labels)
        vdist_b = _bucket_label_by_edges(vdist, vdist_edges, vdist_labels)
        regime_b = str(s.get("market_regime") or "").strip() or "N/A"
        time_b = str(s.get("entry_time_bucket") or "").strip() or "N/A"

        _add_cross("gap_pct_x_market_regime", gap_b, regime_b, pnl, is_win, is_lose)
        _add_cross("gap_pct_x_time_bucket", gap_b, time_b, pnl, is_win, is_lose)
        _add_cross("entry_vwap_distance_pct_x_market_regime", vdist_b, regime_b, pnl, is_win, is_lose)
        _add_cross("entry_vwap_distance_pct_x_time_bucket", vdist_b, time_b, pnl, is_win, is_lose)

    def _rows_for_name(
        name: str,
        field1: str,
        field2: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (b1, b2), a in (acc.get(name) or {}).items():
            sigs = int(a.get("signals", 0))
            win = int(a.get("win", 0))
            pnl_sum = float(a.get("pnl_sum", 0.0))
            winrate = (float(win) / float(sigs) * 100.0) if sigs > 0 else 0.0
            exp = (float(pnl_sum) / float(sigs)) if sigs > 0 else 0.0
            pnls = a.get("pnls") or []
            rows.append(
                {
                    field1: str(b1),
                    field2: str(b2),
                    "signals": int(sigs),
                    "winrate_pct": float(winrate),
                    "avg_expectancy_yen_100_shares": float(exp),
                    "total_pnl_yen_100_shares": float(pnl_sum),
                    "lose_worst10_sum_yen_100_shares": float(
                        _lose_worst10_sum_yen_100_shares_from_pnls(pnls if isinstance(pnls, list) else [])
                    ),
                }
            )
        return sorted(rows, key=lambda x: int(x.get("signals", 0)), reverse=True)

    return {
        "gap_pct_x_market_regime": _rows_for_name("gap_pct_x_market_regime", "gap_pct_bucket", "market_regime"),
        "gap_pct_x_time_bucket": _rows_for_name("gap_pct_x_time_bucket", "gap_pct_bucket", "entry_time_bucket"),
        "entry_vwap_distance_pct_x_market_regime": _rows_for_name(
            "entry_vwap_distance_pct_x_market_regime", "entry_vwap_distance_pct_bucket", "market_regime"
        ),
        "entry_vwap_distance_pct_x_time_bucket": _rows_for_name(
            "entry_vwap_distance_pct_x_time_bucket", "entry_vwap_distance_pct_bucket", "entry_time_bucket"
        ),
    }


def _bucket_high_update_count_before_entry(n: Any) -> str:
    if n is None:
        return "N/A"
    try:
        v = int(float(n))
    except Exception:
        return "N/A"
    if v <= 0:
        return "0"
    if v == 1:
        return "1"
    if v == 2:
        return "2"
    if 3 <= v <= 5:
        return "3~5"
    return ">=6"


def _build_signal_state_cross_analysis_from_signal_dicts(signal_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    high_update / hold と market_regime / entry_vwap_distance_pct bucket の2次元集計（eval対象のみ）。
    """
    vdist_edges = [0.5, 1.0, 1.5, 2.0]
    vdist_labels = ["<=0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", ">=2.0"]
    hold_edges = [5.0, 15.0, 30.0, 60.0, 120.0]
    hold_labels = ["<=5", "5~15", "15~30", "30~60", "60~120", ">120"]

    acc: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    def _add_cross(name: str, k1: str, k2: str, pnl: float, is_win: bool, is_lose: bool) -> None:
        acc.setdefault(name, {})
        key = (str(k1), str(k2))
        a = acc[name].setdefault(key, {"signals": 0, "win": 0, "lose": 0, "pnl_sum": 0.0, "pnls": []})
        a["signals"] = int(a.get("signals", 0)) + 1
        if bool(is_win):
            a["win"] = int(a.get("win", 0)) + 1
        if bool(is_lose):
            a["lose"] = int(a.get("lose", 0)) + 1
        a["pnl_sum"] = float(a.get("pnl_sum", 0.0)) + float(pnl)
        try:
            a["pnls"].append(float(pnl))
        except Exception:
            pass

    for s in signal_dicts:
        if not isinstance(s, dict):
            continue
        if bool(s.get("excluded_from_eval", False)):
            continue
        pnl = float(s.get("pnl_yen_100_shares") or 0.0)
        res = str(s.get("result") or "")
        is_win = res == "WIN"
        is_lose = res == "LOSE"
        vdist = s.get("entry_vwap_distance_pct")
        hm = s.get("hold_minutes")
        huc = s.get("high_update_count_before_entry")
        vdist_b = _bucket_label_by_edges(vdist, vdist_edges, vdist_labels)
        hold_b = _bucket_label_by_edges(hm, hold_edges, hold_labels)
        huc_b = _bucket_high_update_count_before_entry(huc)
        regime_b = str(s.get("market_regime") or "").strip() or "N/A"

        _add_cross("high_update_count_before_entry_x_market_regime", huc_b, regime_b, pnl, is_win, is_lose)
        _add_cross("high_update_count_before_entry_x_entry_vwap_distance_pct_bucket", huc_b, vdist_b, pnl, is_win, is_lose)
        _add_cross("hold_minutes_x_market_regime", hold_b, regime_b, pnl, is_win, is_lose)
        _add_cross("hold_minutes_x_entry_vwap_distance_pct_bucket", hold_b, vdist_b, pnl, is_win, is_lose)

    def _rows_for_name(name: str, field1: str, field2: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (b1, b2), a in (acc.get(name) or {}).items():
            sigs = int(a.get("signals", 0))
            win = int(a.get("win", 0))
            pnl_sum = float(a.get("pnl_sum", 0.0))
            winrate = (float(win) / float(sigs) * 100.0) if sigs > 0 else 0.0
            exp = (float(pnl_sum) / float(sigs)) if sigs > 0 else 0.0
            pnls = a.get("pnls") or []
            rows.append(
                {
                    field1: str(b1),
                    field2: str(b2),
                    "signals": int(sigs),
                    "winrate_pct": float(winrate),
                    "avg_expectancy_yen_100_shares": float(exp),
                    "total_pnl_yen_100_shares": float(pnl_sum),
                    "lose_worst10_sum_yen_100_shares": float(
                        _lose_worst10_sum_yen_100_shares_from_pnls(pnls if isinstance(pnls, list) else [])
                    ),
                }
            )
        return sorted(rows, key=lambda x: int(x.get("signals", 0)), reverse=True)

    return {
        "high_update_count_before_entry_x_market_regime": _rows_for_name(
            "high_update_count_before_entry_x_market_regime",
            "high_update_count_before_entry_bucket",
            "market_regime",
        ),
        "high_update_count_before_entry_x_entry_vwap_distance_pct_bucket": _rows_for_name(
            "high_update_count_before_entry_x_entry_vwap_distance_pct_bucket",
            "high_update_count_before_entry_bucket",
            "entry_vwap_distance_pct_bucket",
        ),
        "hold_minutes_x_market_regime": _rows_for_name(
            "hold_minutes_x_market_regime", "hold_minutes_bucket", "market_regime"
        ),
        "hold_minutes_x_entry_vwap_distance_pct_bucket": _rows_for_name(
            "hold_minutes_x_entry_vwap_distance_pct_bucket",
            "hold_minutes_bucket",
            "entry_vwap_distance_pct_bucket",
        ),
    }


def _build_strong_loser_analysis_from_signal_dicts(signal_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    market_regime == STRONG かつ pnl_yen_100_shares < 0 のシグナルを対象に、bucket ごとに集計する。
    """
    gap_edges = [-3.0, -1.0, 1.0, 3.0]
    gap_labels = ["<=-3", "-3~-1", "-1~1", "1~3", ">=3"]
    vdist_edges = [0.5, 1.0, 1.5, 2.0]
    vdist_labels = ["<=0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", ">=2.0"]
    atr_edges = [1.0, 2.0, 3.0]
    atr_labels = ["<1", "1~2", "2~3", ">=3"]
    hold_edges = [5.0, 15.0, 30.0, 60.0, 120.0]
    hold_labels = ["<=5", "5~15", "15~30", "30~60", "60~120", ">120"]

    acc: dict[str, dict[str, dict[str, Any]]] = {}

    def _add(feature: str, bucket: str, pnl: float) -> None:
        acc.setdefault(feature, {})
        a = acc[feature].setdefault(bucket, {"signals": 0, "pnl_sum": 0.0, "pnls": []})
        a["signals"] = int(a.get("signals", 0)) + 1
        a["pnl_sum"] = float(a.get("pnl_sum", 0.0)) + float(pnl)
        try:
            a["pnls"].append(float(pnl))
        except Exception:
            pass

    for s in signal_dicts:
        if not isinstance(s, dict):
            continue
        if bool(s.get("excluded_from_eval", False)):
            continue
        rk = str(s.get("market_regime") or "").strip().upper()
        if rk != "STRONG":
            continue
        pnl = float(s.get("pnl_yen_100_shares") or 0.0)
        if pnl >= 0.0:
            continue

        gap = s.get("gap_pct")
        vdist = s.get("entry_vwap_distance_pct")
        atrp = s.get("atr_pct")
        etb = str(s.get("entry_time_bucket") or "").strip() or "N/A"
        hm = s.get("hold_minutes")
        huc = s.get("high_update_count_before_entry")

        _add("gap_pct", _bucket_label_by_edges(gap, gap_edges, gap_labels), pnl)
        _add("entry_vwap_distance_pct", _bucket_label_by_edges(vdist, vdist_edges, vdist_labels), pnl)
        _add("atr_pct", _bucket_label_by_edges(atrp, atr_edges, atr_labels), pnl)
        _add("entry_time_bucket", etb, pnl)
        _add("hold_minutes", _bucket_label_by_edges(hm, hold_edges, hold_labels), pnl)
        _add("high_update_count_before_entry", _bucket_high_update_count_before_entry(huc), pnl)

    feat_order = [
        "gap_pct",
        "entry_vwap_distance_pct",
        "atr_pct",
        "entry_time_bucket",
        "hold_minutes",
        "high_update_count_before_entry",
    ]
    out: dict[str, Any] = {}
    for feat in feat_order:
        buckets = acc.get(feat) or {}
        rows: list[dict[str, Any]] = []
        for b, a in buckets.items():
            sigs = int(a.get("signals", 0))
            pnl_sum = float(a.get("pnl_sum", 0.0))
            exp = (float(pnl_sum) / float(sigs)) if sigs > 0 else 0.0
            pnls = a.get("pnls") or []
            rows.append(
                {
                    "bucket": str(b),
                    "signals": int(sigs),
                    "total_pnl_yen_100_shares": float(pnl_sum),
                    "avg_expectancy_yen_100_shares": float(exp),
                    "lose_worst10_sum_yen_100_shares": float(
                        _lose_worst10_sum_yen_100_shares_from_pnls(pnls if isinstance(pnls, list) else [])
                    ),
                }
            )
        out[feat] = sorted(rows, key=lambda x: int(x.get("signals", 0)), reverse=True)
    return out


def run_daily_loss_stop_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    daily_loss_stop の閾値比較を sweep します。
    - 対象config: replay_morning_vwap2(OFF), replay_morning_vwap2_dd30k/dd50k/dd70k
    - 各configについて SWEEP_REPLAY_RANGES（random_apr）×n_repeat のみ実行
    - 出力: results/daily_loss_stop_sweep_<stamp>/
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _ensure_replay_configs_exist()  # configs が無ければ生成

    cfg_files = [
        "replay_morning_vwap2.json",
        "replay_morning_vwap2_dd30k.json",
        "replay_morning_vwap2_dd50k.json",
        "replay_morning_vwap2_dd70k.json",
    ]
    cfg_paths: list[str] = []
    for fn in cfg_files:
        p = _resolve_replay_config_path(os.path.join("configs", fn))
        if p:
            cfg_paths.append(p)

    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"daily_loss_stop_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    print(f"[{now_str()}] daily_loss_stop sweep: configs={len(cfg_paths)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []
    for cfg_path in cfg_paths:
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_path))
        cfg_slug = os.path.basename(cfg_path).replace(".json", "")

        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{cfg_slug}_{rng}"
            output_subdir = os.path.join(f"daily_loss_stop_sweep_{sweep_stamp}", f"{cfg_slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {cfg_slug}  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    run_tag = f"run{i:02d}"
                    candidates = [
                        fn
                        for fn in os.listdir(results_dir)
                        if fn.endswith(".json")
                        and ("replay_summary_" in fn)
                        and (not fn.endswith("_symbol_scores.json"))
                        and fn.endswith(f"{run_tag}.json")
                    ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries_for_daily_loss_stop(run_summaries)
            rows.append(
                {
                    "config_name": cfg_name,
                    "config_path": str(cfg_path),
                    "config_slug": cfg_slug,
                    "replay_range": str(rng),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== daily_loss_stop sweep ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("configs:")
    for p in cfg_paths:
        out_lines.append(f"  - {p}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for p in fps:
                    out_lines.append(f"  - {p}")
        except Exception:
            continue
    out_lines.append("")

    hdr = (
        "rank\tconfig_name\treplay_range\tavg_expectancy_yen\ttotal_pnl_100_shares\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tlose_worst10_sum_yen\ttotal_signals\t"
        "daily_loss_stop_trigger_count\tdaily_loss_stop_skipped_entries\t"
        "max_intraday_drawdown\tavg_daily_drawdown\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('total_signals') or 0)}\t"
            f"{int(s.get('daily_loss_stop_trigger_count') or 0)}\t"
            f"{int(s.get('daily_loss_stop_skipped_entries') or 0)}\t"
            f"{float(s.get('max_intraday_drawdown_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('avg_daily_drawdown_yen_100_shares') or 0.0):+.2f}\t"
            f"results/{r.get('output_subdir')}/"
        )

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] daily_loss_stop sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0


def _write_regime_filter_sweep_configs(script_dir: str) -> list[str]:
    """
    replay_morning_vwap2 をベースに regime_filters のON/OFF組み合わせ config を configs/regime_filter_sweep/ に保存。
    """
    _ensure_replay_configs_exist()
    sweep_dir = os.path.join(script_dir, "configs", "regime_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)

    base_rel = os.path.join("configs", "replay_morning_vwap2.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        presets = _default_replay_configs_dicts()
        fallback = presets.get("replay_morning_vwap2.json")
        if isinstance(fallback, dict):
            base_cfg = dict(fallback)

    cases: list[tuple[str, dict[str, bool]]] = [
        ("baseline_off", {}),
        ("disable_morning_weak", {"disable_morning_weak": True}),
        ("disable_rising_ratio_lt50", {"disable_rising_ratio_lt50": True}),
        ("disable_topix_weak", {"disable_topix_weak": True}),
        ("disable_morning_weak__disable_rising_ratio_lt50", {"disable_morning_weak": True, "disable_rising_ratio_lt50": True}),
    ]

    out_paths: list[str] = []
    for slug, flags in cases:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        cfg["name"] = f"replay_morning_vwap2_regime_{slug}"
        if flags:
            cfg["regime_filters"] = dict(flags)
        else:
            cfg.pop("regime_filters", None)
        fn = f"replay_morning_vwap2_regime_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        out_paths.append(os.path.abspath(path))
    return out_paths


def _write_topix_weak_threshold_sweep_configs(script_dir: str) -> list[str]:
    """
    replay_morning_vwap2 をベースに TOPIX_WEAK threshold を変えた config を configs/regime_filter_sweep/ に保存。
    """
    _ensure_replay_configs_exist()
    sweep_dir = os.path.join(script_dir, "configs", "regime_filter_sweep")
    os.makedirs(sweep_dir, exist_ok=True)

    base_rel = os.path.join("configs", "replay_morning_vwap2.json")
    base_path = _resolve_replay_config_path(base_rel)
    base_cfg = _load_replay_config(base_path) if base_path else {}
    if not base_cfg:
        presets = _default_replay_configs_dicts()
        fallback = presets.get("replay_morning_vwap2.json")
        if isinstance(fallback, dict):
            base_cfg = dict(fallback)

    thresholds = [-0.2, -0.3, -0.5, -0.7]
    out_paths: list[str] = []
    for thr in thresholds:
        cfg = json.loads(json.dumps(base_cfg))
        cfg.pop("_path", None)
        slug = f"topix_weak_thr{str(abs(thr)).replace('.', '')}".replace("-", "")
        cfg["name"] = f"replay_morning_vwap2_regime_{slug}"
        cfg["regime_filters"] = {"disable_topix_weak": True, "topix_weak_threshold_pct": float(thr)}
        fn = f"replay_morning_vwap2_regime_{slug}.json"
        path = os.path.join(sweep_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        out_paths.append(os.path.abspath(path))
    return out_paths


def run_topix_weak_threshold_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    TOPIX_WEAK threshold を sweep します（disable_topix_weak を有効化）。
    - threshold: -0.2/-0.3/-0.5/-0.7
    - SWEEP_REPLAY_RANGES（random_apr）×n_repeat のみ
    - 出力: results/topix_weak_threshold_sweep_<stamp>/
    - config生成: configs/regime_filter_sweep/
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    cfg_paths = _write_topix_weak_threshold_sweep_configs(script_dir)

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"topix_weak_threshold_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    print(f"[{now_str()}] topix_weak_threshold sweep: configs={len(cfg_paths)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] config_root: {os.path.join(script_dir, 'configs', 'regime_filter_sweep')}")
    for p in cfg_paths:
        print(f"[{now_str()}] 生成 config: {p}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []
    for cfg_path in cfg_paths:
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_path))
        # Windowsのパス長制限を踏まえ、短いslugを使います（ファイル名にも入るため重要）
        thr0 = f.get("regime_filter_topix_weak_threshold_pct")
        thr_s = "na"
        try:
            if isinstance(thr0, (int, float)):
                thr_s = f"thr{str(abs(float(thr0))).replace('.', '')}".replace("-", "")
        except Exception:
            thr_s = "na"
        cfg_slug = f"tw_{thr_s}"

        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{cfg_slug}_{rng}"
            output_subdir = os.path.join(f"topix_weak_threshold_sweep_{sweep_stamp}", f"{cfg_slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {cfg_slug}  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    # repeat=1 の場合は runXX が付かないため、最新のjsonを拾う
                    candidates = [
                        fn
                        for fn in os.listdir(results_dir)
                        if fn.endswith(".json")
                        and ("replay_summary_" in fn)
                        and (not fn.endswith("_symbol_scores.json"))
                    ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries_for_regime_filter(run_summaries)
            rows.append(
                {
                    "config_name": cfg_name,
                    "config_path": str(cfg_path),
                    "config_slug": cfg_slug,
                    "replay_range": str(rng),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== topix_weak_threshold sweep ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("configs:")
    for p in cfg_paths:
        out_lines.append(f"  - {p}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for p in fps:
                    out_lines.append(f"  - {p}")
        except Exception:
            continue
    out_lines.append("")

    hdr = (
        "rank\tconfig_name\treplay_range\tavg_expectancy_yen\ttotal_pnl_100_shares\tlose_worst10_sum_yen\t"
        "max_intraday_drawdown\tskipped_signals_count\ttotal_signals\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_intraday_drawdown_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('skipped_signals_count') or 0)}\t"
            f"{int(s.get('total_signals') or 0)}\t"
            f"results/{r.get('output_subdir')}/"
        )

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] topix_weak_threshold sweep summary_path: {out_path}")
    print("\n".join(out_lines))
    return 0

def run_regime_filter_sweep(
    *,
    fixed_watch: Optional[list[str]],
    interval_sec: float,
    only_changes: bool,
    replay_seed: Optional[int],
    replay_mode: str,
    n_repeat: int,
) -> int:
    """
    regime_filters の組み合わせを sweep します。
    - 対象: baseline(OFF), morning_weak, rising<50, topix_weak, morning_weak+rising<50
    - SWEEP_REPLAY_RANGES（random_apr）×n_repeat のみ
    - 出力: results/regime_filter_sweep_<stamp>/
    - config生成: configs/regime_filter_sweep/
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ranges = list(SWEEP_REPLAY_RANGES)
    sweep_stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    cfg_paths = _write_regime_filter_sweep_configs(script_dir)

    results_root = os.path.join(script_dir, "results")
    os.makedirs(results_root, exist_ok=True)
    sweep_root = os.path.join(results_root, f"regime_filter_sweep_{sweep_stamp}")
    os.makedirs(sweep_root, exist_ok=True)

    print(f"[{now_str()}] regime_filter sweep: configs={len(cfg_paths)} ranges={ranges} repeat={n_repeat}")
    print(f"[{now_str()}] sweep_root: {sweep_root}")
    print(f"[{now_str()}] config_root: {os.path.join(script_dir, 'configs', 'regime_filter_sweep')}")
    for p in cfg_paths:
        print(f"[{now_str()}] 生成 config: {p}")

    rows: list[dict[str, Any]] = []
    collect_debug_rows: list[dict[str, Any]] = []
    for cfg_path in cfg_paths:
        cfg_raw = _load_replay_config(cfg_path)
        f = _apply_replay_config_to_flags(cfg=cfg_raw)
        cfg_name = str(f.get("replay_config_name") or os.path.basename(cfg_path))
        # Windowsのパス長制限を踏まえ、短いslugを使います（ファイル名にも入るため重要）
        mw = bool(f.get("regime_filter_disable_morning_weak", False))
        rlt50 = bool(f.get("regime_filter_disable_rising_ratio_lt50", False))
        tw = bool(f.get("regime_filter_disable_topix_weak", False))
        if (not mw) and (not rlt50) and (not tw):
            cfg_slug = "base"
        else:
            parts = []
            if mw:
                parts.append("mw")
            if rlt50:
                parts.append("rlt50")
            if tw:
                parts.append("tw")
            cfg_slug = "_".join(parts) if parts else "x"

        for rng in ranges:
            replay_random_days = 5
            batch_stamp = f"{sweep_stamp}_{cfg_slug}_{rng}"
            output_subdir = os.path.join(f"regime_filter_sweep_{sweep_stamp}", f"{cfg_slug}_{rng}")

            print("")
            print(f"[{now_str()}] --- sweep cell: {cfg_slug}  {rng}  ({n_repeat} runs) ---")
            print(f"[{now_str()}] output_subdir: results/{output_subdir}/")

            run_summaries: list[dict[str, Any]] = []
            results_dir = os.path.join(script_dir, "results", output_subdir)
            os.makedirs(results_dir, exist_ok=True)

            for i in range(1, int(n_repeat) + 1):
                seed_run = int(replay_seed) + i - 1 if replay_seed is not None else None
                code = run_replay(
                    interval_sec=float(interval_sec),
                    only_changes=bool(only_changes),
                    fixed_watch=fixed_watch,
                    replay_range=str(rng),
                    replay_random_days=int(replay_random_days),
                    replay_random_months=3,
                    replay_seed=seed_run,
                    replay_mode=str(replay_mode or "normal"),
                    replay_fast_discord=False,
                    replay_fast_verbose=False,
                    replay_fast_print_signal_details=False,
                    replay_market_debug=False,
                    replay_repeat_run_no=i,
                    replay_repeat_total=int(n_repeat),
                    replay_output_subdir=output_subdir,
                    replay_batch_stamp=batch_stamp,
                    replay_morning_screen_hhmm="",
                    one_trade_per_symbol_per_day=False,
                    enable_add=False,
                    replay_early_exit_before_stop=bool(f["replay_early_exit_before_stop"]),
                    replay_early_exit_vwap=bool(f["replay_early_exit_vwap"]),
                    replay_early_exit_recent_low=bool(f["replay_early_exit_recent_low"]),
                    replay_disable_afternoon_entry=bool(f["replay_disable_afternoon_entry"]),
                    replay_strict_afternoon_entry=bool(f["replay_strict_afternoon_entry"]),
                    replay_afternoon_topix_weak_block=bool(f["replay_afternoon_topix_weak_block"]),
                    replay_config_name=str(f.get("replay_config_name") or ""),
                    replay_config_path=str(cfg_path),
                    aft_volume_spike_ratio_min=float(f["aft_volume_spike_ratio_min"]),
                    aft_vwap_dist_pct_max=float(f["aft_vwap_dist_pct_max"]),
                    aft_rebreak_mult=float(f["aft_rebreak_mult"]),
                    entry_filter_rsi_enabled=bool(f["entry_filter_rsi_enabled"]),
                    entry_filter_rsi_exclude_above=float(f["entry_filter_rsi_exclude_above"]),
                    entry_filter_vwap_distance_enabled=bool(f["entry_filter_vwap_distance_enabled"]),
                    entry_filter_vwap_distance_exclude_above=float(f["entry_filter_vwap_distance_exclude_above"]),
                    entry_filter_atr_pct_enabled=bool(f["entry_filter_atr_pct_enabled"]),
                    entry_filter_atr_pct_exclude_above=float(f["entry_filter_atr_pct_exclude_above"]),
                    daily_loss_stop_enabled=bool(f.get("daily_loss_stop_enabled", False)),
                    daily_loss_stop_threshold_yen_100_shares=float(
                        f.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
                    ),
                    regime_filter_disable_morning_weak=bool(f.get("regime_filter_disable_morning_weak", False)),
                    regime_filter_disable_rising_ratio_lt50=bool(f.get("regime_filter_disable_rising_ratio_lt50", False)),
                    regime_filter_disable_topix_weak=bool(f.get("regime_filter_disable_topix_weak", False)),
                    regime_filter_topix_weak_threshold_pct=f.get("regime_filter_topix_weak_threshold_pct"),
                    **_replay_regime_control_kwargs_from_flags(f),
                    replay_settings=None,
                )
                if int(code) != 0:
                    print(f"[{now_str()}] sweep 中断: run_replay exit={int(code)} (run={i})")
                    return int(code)

                try:
                    # レポートjson（統計入り）だけを拾う（*_symbol_scores.json を誤って拾うと集計が0になり得る）
                    candidates = [
                        fn
                        for fn in os.listdir(results_dir)
                        if fn.endswith(".json") and ("replay_summary_" in fn) and (not fn.endswith("_symbol_scores.json"))
                    ]
                    candidates_sorted = sorted(
                        candidates,
                        key=lambda x: os.path.getmtime(os.path.join(results_dir, x)),
                        reverse=True,
                    )
                    if candidates_sorted:
                        p = os.path.join(results_dir, candidates_sorted[0])
                        with open(p, "r", encoding="utf-8") as fp:
                            rep = json.load(fp)
                        run_summaries.append({"run_no": i, "json_path": p, "report": rep})
                    collect_debug_rows.append(
                        {
                            "cell_folder": str(output_subdir),
                            "run_no": int(i),
                            "found_json_count": int(len(candidates_sorted)),
                            "found_json_paths": [os.path.join(results_dir, x) for x in candidates_sorted[:10]],
                            "loaded_runs_count": int(len(run_summaries)),
                        }
                    )
                except Exception:
                    pass

            summ = _aggregate_replay_repeat_run_summaries_for_regime_filter(run_summaries)
            rows.append(
                {
                    "config_name": cfg_name,
                    "config_path": str(cfg_path),
                    "config_slug": cfg_slug,
                    "replay_range": str(rng),
                    "output_subdir": str(output_subdir),
                    "batch_stamp": str(batch_stamp),
                    "summary": summ,
                }
            )

    rows_sorted = sorted(
        rows,
        key=lambda r: float(((r.get("summary") or {}).get("avg_expectancy_yen_100_shares")) or 0.0),
        reverse=True,
    )

    out_lines: list[str] = []
    out_lines.append("=== regime_filter sweep ===")
    out_lines.append(f"saved_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")
    out_lines.append(f"sweep_stamp: {sweep_stamp}")
    out_lines.append(f"repeat_per_cell: {int(n_repeat)}")
    out_lines.append(f"replay_mode: {replay_mode}")
    out_lines.append(f"replay_seed: {replay_seed}")
    out_lines.append("")
    out_lines.append("configs:")
    for p in cfg_paths:
        out_lines.append(f"  - {p}")
    out_lines.append("")
    out_lines.append("ソート: avg_expectancy_yen_100_shares（降順）")
    out_lines.append("")
    out_lines.append("[SWEEP_COLLECT_DEBUG]")
    out_lines.append("")
    for it in collect_debug_rows[:200]:
        try:
            out_lines.append(
                f"cell_folder: {it.get('cell_folder')} run_no={int(it.get('run_no') or 0)} "
                f"found_json_count={int(it.get('found_json_count') or 0)} loaded_runs_count={int(it.get('loaded_runs_count') or 0)}"
            )
            fps = it.get("found_json_paths") or []
            if isinstance(fps, list) and fps:
                for p in fps:
                    out_lines.append(f"  - {p}")
        except Exception:
            continue
    out_lines.append("")

    hdr = (
        "rank\tconfig_name\treplay_range\tavg_expectancy_yen\ttotal_pnl_100_shares\tmax_lose_run_yen\t"
        "plus_runs\tminus_runs\tlose_worst10_sum_yen\tpassed_signals_count\tskipped_signals_count\tskip_ratio\t"
        "max_intraday_drawdown\tresults_folder"
    )
    out_lines.append(hdr)
    for idx, r in enumerate(rows_sorted, start=1):
        s = r.get("summary") or {}
        out_lines.append(
            f"{idx}\t{r.get('config_name')}\t{r.get('replay_range')}\t"
            f"{float(s.get('avg_expectancy_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('total_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{float(s.get('max_lose_run_pnl_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('plus_runs') or 0)}\t{int(s.get('minus_runs') or 0)}\t"
            f"{float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+.2f}\t"
            f"{int(s.get('passed_signals_count') or 0)}\t"
            f"{int(s.get('skipped_signals_count') or 0)}\t"
            f"{float(s.get('skip_ratio') or 0.0):.3f}\t"
            f"{float(s.get('max_intraday_drawdown_yen_100_shares') or 0.0):+.2f}\t"
            f"results/{r.get('output_subdir')}/"
        )

    out_path = os.path.join(sweep_root, "sweep_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print("")
    print(f"[{now_str()}] regime_filter sweep summary_path: {out_path}")
    print("\n".join(out_lines))
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

    paper_trade = bool(getattr(args, "paper_trade", False))
    if paper_trade and bool(getattr(args, "replay", False)):
        print(f"[{now_str()}] --paper-trade と --replay は同時指定できません。")
        return 2

    # --replay が指定されたときだけ TEST_REPLAY_MODE を有効化します。
    # （指定しなければ False のまま = いつものリアルタイム監視に戻ります）
    global TEST_REPLAY_MODE
    TEST_REPLAY_MODE = bool(getattr(args, "replay", False)) and (not paper_trade)
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
    # CLI同時指定チェック用（config適用後でも参照できるよう、ここで確定）
    _cli_disable_afternoon_entry: bool = bool(getattr(args, "replay_disable_afternoon_entry", False))
    _cli_strict_afternoon_entry: bool = bool(getattr(args, "replay_strict_afternoon_entry", False))
    replay_afternoon_compare: bool = bool(getattr(args, "replay_afternoon_compare", False))
    replay_config_path: str = str(getattr(args, "replay_config", "") or "").strip()
    # 要件: replay実行時に config パス未指定なら configs/replay_morning_vwap2.json をデフォルトで読む（自動生成も行う）
    # さらに、未生成の比較用config（replay_morning_rsi75 等）を configs/ に揃えてから読み込む
    # paper_trade 時は未指定なら configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json（Replay検証と同一）
    if bool(TEST_REPLAY_MODE) or paper_trade:
        _default_replay_cfg_path = _ensure_replay_configs_exist()
        if not replay_config_path:
            if paper_trade:
                replay_config_path = _resolve_replay_config_path(
                    "configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json"
                )
            else:
                replay_config_path = _default_replay_cfg_path
    if (bool(TEST_REPLAY_MODE) or paper_trade) and replay_config_path:
        # configs/ が無いケースでも落ちないようにする（ユーザーが明示パス指定した場合）
        try:
            parent = os.path.dirname(os.path.abspath(_resolve_replay_config_path(replay_config_path)))
            if parent:
                os.makedirs(parent, exist_ok=True)
        except Exception:
            pass

    # 相対パスはカレント依存で読み損なうことがあるため、必ずスクリプト基準へ解決してから読む
    if replay_config_path:
        replay_config_path = _resolve_replay_config_path(replay_config_path)

    cfg_raw = _load_replay_config(replay_config_path)
    cfg_flags = _apply_replay_config_to_flags(cfg=cfg_raw)

    if (bool(TEST_REPLAY_MODE) or paper_trade) and replay_config_path:
        try:
            _bn_pt = os.path.basename(str(replay_config_path).replace("\\", "/"))
            if _bn_pt in PAPER_TRADE_REPLAY_CONFIG_FILENAMES:
                print(
                    f"[{now_str()}] Paper trade 暫定候補 config（当面フィルタ追加なし・運用検証向け）: {_bn_pt}"
                )
        except Exception:
            pass

    # プリセット別の期待値（読み込み後に実フラグと照合して WARN）
    _preset_expect_morning: dict[str, Any] = {
        "replay_early_exit_before_stop": True,
        "replay_early_exit_vwap": True,
        "replay_early_exit_recent_low": True,
        "replay_strict_afternoon_entry": True,
        "replay_disable_afternoon_entry": True,
        "replay_afternoon_topix_weak_block": True,
    }
    _preset_expect: dict[str, dict[str, Any]] = {
        "replay_morning_only.json": dict(_preset_expect_morning),
        "replay_morning_rsi75.json": dict(_preset_expect_morning),
        "replay_morning_vwap2.json": dict(_preset_expect_morning),
        "replay_morning_vwap15.json": dict(_preset_expect_morning),
        "replay_morning_vwap20.json": dict(_preset_expect_morning),
        "replay_morning_vwap25.json": dict(_preset_expect_morning),
        "replay_morning_vwap30.json": dict(_preset_expect_morning),
        "replay_morning_atr4.json": dict(_preset_expect_morning),
    }

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

    entry_filter_rsi_enabled = bool(cfg_flags.get("entry_filter_rsi_enabled", False))
    entry_filter_rsi_exclude_above = float(cfg_flags.get("entry_filter_rsi_exclude_above", 75.0))
    entry_filter_vwap_distance_enabled = bool(cfg_flags.get("entry_filter_vwap_distance_enabled", False))
    entry_filter_vwap_distance_exclude_above = float(cfg_flags.get("entry_filter_vwap_distance_exclude_above", 2.0))
    entry_filter_atr_pct_enabled = bool(cfg_flags.get("entry_filter_atr_pct_enabled", False))
    entry_filter_atr_pct_exclude_above = float(cfg_flags.get("entry_filter_atr_pct_exclude_above", 4.0))
    daily_loss_stop_enabled = bool(cfg_flags.get("daily_loss_stop_enabled", False))
    daily_loss_stop_threshold_yen_100_shares = float(cfg_flags.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0))
    regime_filter_disable_morning_weak = bool(cfg_flags.get("regime_filter_disable_morning_weak", False))
    regime_filter_disable_rising_ratio_lt50 = bool(cfg_flags.get("regime_filter_disable_rising_ratio_lt50", False))
    regime_filter_disable_topix_weak = bool(cfg_flags.get("regime_filter_disable_topix_weak", False))
    regime_filter_topix_weak_threshold_pct = cfg_flags.get("regime_filter_topix_weak_threshold_pct", None)
    signal_filter_disable_gap_ge_pct = bool(cfg_flags.get("signal_filter_disable_gap_ge_pct", False))
    signal_filter_gap_ge_threshold_pct = float(cfg_flags.get("signal_filter_gap_ge_threshold_pct", 3.0))
    signal_filter_disable_vwap_distance_ge_pct = bool(cfg_flags.get("signal_filter_disable_vwap_distance_ge_pct", False))
    signal_filter_vwap_distance_ge_threshold_pct = float(cfg_flags.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5))
    signal_filter_disable_entry_after_hhmm = bool(cfg_flags.get("signal_filter_disable_entry_after_hhmm", False))
    signal_filter_entry_after_hhmm = str(cfg_flags.get("signal_filter_entry_after_hhmm", "10:30"))
    composite_signal_filter_disable_weak_vwap_ge = bool(cfg_flags.get("composite_signal_filter_disable_weak_vwap_ge", False))
    composite_signal_filter_weak_vwap_ge_threshold_pct = float(
        cfg_flags.get("composite_signal_filter_weak_vwap_ge_threshold_pct", 1.5)
    )
    composite_signal_filter_disable_weak_gap_ge = bool(cfg_flags.get("composite_signal_filter_disable_weak_gap_ge", False))
    composite_signal_filter_weak_gap_ge_threshold_pct = float(
        cfg_flags.get("composite_signal_filter_weak_gap_ge_threshold_pct", 3.0)
    )
    regime_control_enabled = bool(cfg_flags.get("regime_control_enabled", False))

    if replay_config_path:
        # 必須同等キーがプリセット期待と一致しない場合は WARNING（マージ後でも欠落/上書きミスを検知）
        try:
            base_fn = os.path.basename(str(replay_config_path or "").replace("\\", "/"))
            exp = _preset_expect.get(base_fn)
            if isinstance(exp, dict):
                for k, ev in exp.items():
                    av = cfg_flags.get(k)
                    if av != ev:
                        print(
                            f"[{now_str()}][WARNING] Replay config preset mismatch: "
                            f"preset={base_fn} key={k} expected={ev} actual={av} "
                            f"(JSONキー名: early_exit / vwap_break_exit / recent_5m_low_break_exit / strict_afternoon / disable_afternoon_entry / topix_weak_block)"
                        )
        except Exception:
            pass

        # replay_morning_* なのに後場禁止が効いていない場合は明示
        try:
            base_fn2 = os.path.basename(str(replay_config_path or "").replace("\\", "/"))
            morning_presets = (
                "replay_morning_only.json",
                "replay_morning_rsi75.json",
                "replay_morning_vwap2.json",
                "replay_morning_vwap15.json",
                "replay_morning_vwap20.json",
                "replay_morning_vwap25.json",
                "replay_morning_vwap30.json",
                "replay_morning_atr4.json",
            )
            if base_fn2 in morning_presets and not bool(replay_disable_afternoon_entry):
                print(
                    f"[{now_str()}] CONFIG_MISMATCH_WARNING: "
                    f"config_path suggests morning-only preset but disable_afternoon_entry=False "
                    f"(check JSON key disable_afternoon_entry: true, or delete stale config file and regenerate)"
                )
        except Exception:
            pass
    else:
        replay_early_exit_vwap = True
        replay_early_exit_recent_low = True
        replay_afternoon_topix_weak_block = True
        aft_volume_spike_ratio_min = float(AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN)
        aft_vwap_dist_pct_max = float(AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX)
        aft_rebreak_mult = float(AFTERNOON_ENTRY_STRICT_REBREAK_MULT)
        replay_config_name = ""

    # 要件: replay開始時に必ず読み込んだconfigをprint（反映確認）
    if bool(TEST_REPLAY_MODE) or paper_trade:
        print("Loaded replay config:")
        print(f"config_name={str(replay_config_name or '')}")
        print(f"config_path={str(replay_config_path or '')}")
        print(f"early_exit_before_stop={bool(replay_early_exit_before_stop)}")
        print(f"vwap_break_exit={bool(replay_early_exit_vwap)}")
        print(f"recent_5m_low_break_exit={bool(replay_early_exit_recent_low)}")
        print(f"strict_afternoon={bool(replay_strict_afternoon_entry)}")
        print(f"disable_afternoon_entry={bool(replay_disable_afternoon_entry)}")
        print(f"topix_weak_block={bool(replay_afternoon_topix_weak_block)}")
        print(
            f"entry_filter_rsi: enabled={bool(entry_filter_rsi_enabled)} exclude_above={float(entry_filter_rsi_exclude_above):g}"
        )
        print(
            "entry_filter_vwap_distance: "
            f"enabled={bool(entry_filter_vwap_distance_enabled)} "
            f"exclude_above={float(entry_filter_vwap_distance_exclude_above):g}%"
        )
        print(
            "entry_filter_atr_pct: "
            f"enabled={bool(entry_filter_atr_pct_enabled)} exclude_above={float(entry_filter_atr_pct_exclude_above):g}%"
        )
        print(
            "signal_filters: "
            f"gap_ge disable={bool(signal_filter_disable_gap_ge_pct)} thr={float(signal_filter_gap_ge_threshold_pct):g}% | "
            f"vwap_ge disable={bool(signal_filter_disable_vwap_distance_ge_pct)} thr={float(signal_filter_vwap_distance_ge_threshold_pct):g}%"
        )
        print(
            "composite_signal_filters(WEAKのみ): "
            f"weak_gap_ge disable={bool(composite_signal_filter_disable_weak_gap_ge)} thr={float(composite_signal_filter_weak_gap_ge_threshold_pct):g}% | "
            f"weak_vwap_ge disable={bool(composite_signal_filter_disable_weak_vwap_ge)} thr={float(composite_signal_filter_weak_vwap_ge_threshold_pct):g}% | "
            f"weak_risk_filter={str(cfg_flags.get('composite_signal_filter_weak_risk_filter') or '')}"
        )
        print(
            "composite_signal_filters(STRONG): "
            f"strong_risk_filter={str(cfg_flags.get('composite_signal_filter_strong_risk_filter') or '')} "
            f"thr={float(cfg_flags.get('composite_signal_filter_strong_vwap_ge_threshold_pct', 1.5)):g}%"
        )
        _sc_en_t = bool(cfg_flags.get("composite_signal_filter_strong_combo_enabled", False))
        _sc_bl_t = list(cfg_flags.get("composite_signal_filter_strong_combo_block_conditions") or [])
        _fc_t = _sc_bl_t[0] if _sc_bl_t else {}
        print(
            "strong_combo_filter: "
            f"enabled={_sc_en_t} "
            f"market_regime={str(_fc_t.get('market_regime') or '')} "
            f"vwap_ge={_fc_t.get('entry_vwap_distance_pct_ge')} "
            f"hu_le={_fc_t.get('high_update_count_before_entry_le')} "
            f"hu_eq={_fc_t.get('high_update_count_before_entry_eq')}"
        )
        print(f"regime_controls: enabled={bool(regime_control_enabled)}")

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

    def _src_ef(filter_key: str, child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                ef0 = cfg_raw.get("entry_filters")
                if isinstance(ef0, dict):
                    sub = ef0.get(filter_key)
                    if isinstance(sub, dict) and child_key in sub:
                        return "config"
        except Exception:
            pass
        return "default"

    def _src_rc(child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                rc0 = cfg_raw.get("risk_controls")
                if isinstance(rc0, dict):
                    sub = rc0.get("daily_loss_stop")
                    if isinstance(sub, dict) and child_key in sub:
                        return "config"
        except Exception:
            pass
        return "default"

    def _src_rf(child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                rf0 = cfg_raw.get("regime_filters")
                if isinstance(rf0, dict) and child_key in rf0:
                    return "config"
        except Exception:
            pass
        return "default"

    def _src_sf(child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                sf0 = cfg_raw.get("signal_filters")
                if isinstance(sf0, dict) and child_key in sf0:
                    return "config"
        except Exception:
            pass
        return "default"

    def _src_csf(child_key: str) -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                csf0 = cfg_raw.get("composite_signal_filters")
                if isinstance(csf0, dict) and child_key in csf0:
                    return "config"
        except Exception:
            pass
        return "default"

    def _src_rcfg_any() -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict) and isinstance(cfg_raw.get("regime_controls"), dict):
                return "config"
        except Exception:
            pass
        return "default"

    def _src_scf_strong_combo_any() -> str:
        try:
            if replay_config_path and isinstance(cfg_raw, dict):
                csf = cfg_raw.get("composite_signal_filters")
                if isinstance(csf, dict) and isinstance(csf.get("strong_combo_filter"), dict):
                    return "config"
        except Exception:
            pass
        return "default"

    _sc_src_combo = _src_scf_strong_combo_any()
    _sc_block_main = list(cfg_flags.get("composite_signal_filter_strong_combo_block_conditions") or [])
    _first_sc_main = _sc_block_main[0] if _sc_block_main else {}

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
        "entry_filters": {
            "rsi": {
                "enabled": {"value": bool(entry_filter_rsi_enabled), "source": _src_ef("rsi", "enabled")},
                "exclude_above": {"value": float(entry_filter_rsi_exclude_above), "source": _src_ef("rsi", "exclude_above")},
            },
            "vwap_distance_pct": {
                "enabled": {"value": bool(entry_filter_vwap_distance_enabled), "source": _src_ef("vwap_distance_pct", "enabled")},
                "exclude_above": {
                    "value": float(entry_filter_vwap_distance_exclude_above),
                    "source": _src_ef("vwap_distance_pct", "exclude_above"),
                },
            },
            "atr_pct": {
                "enabled": {"value": bool(entry_filter_atr_pct_enabled), "source": _src_ef("atr_pct", "enabled")},
                "exclude_above": {"value": float(entry_filter_atr_pct_exclude_above), "source": _src_ef("atr_pct", "exclude_above")},
            },
        },
        "risk_controls": {
            "daily_loss_stop": {
                "enabled": {"value": bool(daily_loss_stop_enabled), "source": _src_rc("enabled")},
                "stop_yen_100_shares": {"value": float(daily_loss_stop_threshold_yen_100_shares), "source": _src_rc("stop_yen_100_shares")},
            }
        },
        "regime_filters": {
            "disable_morning_weak": {"value": bool(regime_filter_disable_morning_weak), "source": _src_rf("disable_morning_weak")},
            "disable_rising_ratio_lt50": {
                "value": bool(regime_filter_disable_rising_ratio_lt50),
                "source": _src_rf("disable_rising_ratio_lt50"),
            },
            "disable_topix_weak": {"value": bool(regime_filter_disable_topix_weak), "source": _src_rf("disable_topix_weak")},
            "topix_weak_threshold_pct": {
                "value": (
                    float(regime_filter_topix_weak_threshold_pct)
                    if isinstance(regime_filter_topix_weak_threshold_pct, (int, float))
                    else float(WEAK_TOPIX_CHG_PCT_MAX)
                ),
                "source": _src_rf("topix_weak_threshold_pct"),
            },
        },
        "signal_filters": {
            "disable_gap_ge_pct": {"value": bool(signal_filter_disable_gap_ge_pct), "source": _src_sf("disable_gap_ge_pct")},
            "gap_ge_threshold_pct": {"value": float(signal_filter_gap_ge_threshold_pct), "source": _src_sf("gap_ge_threshold_pct")},
            "disable_vwap_distance_ge_pct": {
                "value": bool(signal_filter_disable_vwap_distance_ge_pct),
                "source": _src_sf("disable_vwap_distance_ge_pct"),
            },
            "vwap_distance_ge_threshold_pct": {
                "value": float(signal_filter_vwap_distance_ge_threshold_pct),
                "source": _src_sf("vwap_distance_ge_threshold_pct"),
            },
            "disable_entry_after_hhmm": {"value": bool(signal_filter_disable_entry_after_hhmm), "source": _src_sf("disable_entry_after_hhmm")},
            "entry_after_hhmm": {"value": str(signal_filter_entry_after_hhmm), "source": _src_sf("entry_after_hhmm")},
        },
        "composite_signal_filters": {
            "disable_state_weak_and_vwap_ge_pct": {
                "value": bool(composite_signal_filter_disable_weak_vwap_ge),
                "source": _src_csf("disable_state_weak_and_vwap_ge_pct"),
            },
            "state_weak_vwap_ge_threshold_pct": {
                "value": float(composite_signal_filter_weak_vwap_ge_threshold_pct),
                "source": _src_csf("state_weak_vwap_ge_threshold_pct"),
            },
            "disable_state_weak_and_gap_ge_pct": {
                "value": bool(composite_signal_filter_disable_weak_gap_ge),
                "source": _src_csf("disable_state_weak_and_gap_ge_pct"),
            },
            "state_weak_gap_ge_threshold_pct": {
                "value": float(composite_signal_filter_weak_gap_ge_threshold_pct),
                "source": _src_csf("state_weak_gap_ge_threshold_pct"),
            },
            "weak_risk_filter": {
                "value": str(cfg_flags.get("composite_signal_filter_weak_risk_filter") or ""),
                "source": _src_csf("weak_risk_filter"),
            },
            "strong_risk_filter": {
                "value": str(cfg_flags.get("composite_signal_filter_strong_risk_filter") or ""),
                "source": _src_csf("strong_risk_filter"),
            },
            "strong_vwap_ge_threshold_pct": {
                "value": float(cfg_flags.get("composite_signal_filter_strong_vwap_ge_threshold_pct", 1.5)),
                "source": _src_csf("strong_vwap_ge_threshold_pct"),
            },
        },
        "strong_combo_filter": {
            "enabled": {
                "value": bool(cfg_flags.get("composite_signal_filter_strong_combo_enabled", False)),
                "source": _sc_src_combo,
            },
            "market_regime": {
                "value": str(_first_sc_main.get("market_regime") or ""),
                "source": _sc_src_combo,
            },
            "entry_vwap_distance_pct_ge": {
                "value": (
                    float(_first_sc_main["entry_vwap_distance_pct_ge"])
                    if isinstance(_first_sc_main.get("entry_vwap_distance_pct_ge"), (int, float))
                    else None
                ),
                "source": _sc_src_combo,
            },
            "high_update_count_before_entry_le": {
                "value": (
                    int(_first_sc_main["high_update_count_before_entry_le"])
                    if isinstance(_first_sc_main.get("high_update_count_before_entry_le"), (int, float))
                    else None
                ),
                "source": _sc_src_combo,
            },
            "high_update_count_before_entry_eq": {
                "value": (
                    int(_first_sc_main["high_update_count_before_entry_eq"])
                    if isinstance(_first_sc_main.get("high_update_count_before_entry_eq"), (int, float))
                    else None
                ),
                "source": _sc_src_combo,
            },
        },
        "regime_controls": {
            "enabled": {"value": bool(regime_control_enabled), "source": _src_rcfg_any()},
        },
    }
    _rr_cli = str(replay_range).strip()
    if _replay_fixed_random_pool_dates(_rr_cli):
        _mx = _replay_fixed_random_meta_extra(_rr_cli)
        replay_settings["replay_date_pool_start"] = {"value": str(_mx.get("replay_date_pool_start") or ""), "source": _rr_cli}
        replay_settings["replay_date_pool_end"] = {"value": str(_mx.get("replay_date_pool_end") or ""), "source": _rr_cli}
        replay_settings["replay_candidate_days_count"] = {
            "value": int(_replay_fixed_random_weekday_candidate_count(_rr_cli)),
            "source": _rr_cli,
        }

    # 事故防止: CLIだけ「後場禁止」と「後場厳格化」を同時指定しない（config の replay_morning_only は両方 True が正当）
    if bool(_cli_disable_afternoon_entry) and bool(_cli_strict_afternoon_entry):
        print(f"[{now_str()}] --replay-disable-afternoon-entry と --replay-strict-afternoon-entry は同時指定できません。")
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

    if bool(getattr(args, "intraday_1m_cache_report_only", False)):
        cov = summarize_intraday_1m_cache_coverage()
        print_intraday_1m_cache_coverage_report(cov)
        days_cached = int(cov.get("unique_calendar_days") or 0)
        print(f"=== キャッシュ済みカレンダー日数（全日・全銘柄合算）: {days_cached} 日分 ===")
        print("")
        return 0

    if bool(getattr(args, "save_intraday_1m_eod", False)):
        syms = _resolve_watch_symbols_for_eod(fixed_watch)
        delay_sec = float(getattr(args, "intraday_1m_eod_delay_sec", 0.15) or 0.0)
        return int(
            run_intraday_1m_eod_save_cli(
                syms,
                day_jst=str(getattr(args, "intraday_1m_eod_date", "") or ""),
                force_before_close=bool(getattr(args, "force_intraday_1m_eod_time", False)),
                timeout_sec=25.0,
                delay_sec=max(0.0, delay_sec),
            )
        )

    if bool(getattr(args, "vwap_distance_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed: Optional[int] = int(_rs) if _rs is not None else None
        return int(
            run_vwap_distance_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=10,
            )
        )

    if bool(getattr(args, "daily_loss_stop_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed: Optional[int] = int(_rs) if _rs is not None else None
        return int(
            run_daily_loss_stop_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=10,
            )
        )

    if bool(getattr(args, "regime_filter_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed: Optional[int] = int(_rs) if _rs is not None else None
        _rep = getattr(args, "replay_repeat", None)
        _nrep = int(_rep) if _rep is not None else 10
        return int(
            run_regime_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep),
            )
        )

    if bool(getattr(args, "topix_weak_threshold_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed: Optional[int] = int(_rs) if _rs is not None else None
        return int(
            run_topix_weak_threshold_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=10,
            )
        )

    if bool(getattr(args, "signal_filter_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed: Optional[int] = int(_rs) if _rs is not None else None
        _rep = getattr(args, "replay_repeat", None)
        _nrep = int(_rep) if _rep is not None else 10
        return int(
            run_signal_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep),
            )
        )

    if bool(getattr(args, "composite_filter_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed2: Optional[int] = int(_rs) if _rs is not None else None
        _rep = getattr(args, "replay_repeat", None)
        _nrep = int(_rep) if _rep is not None else 10
        return int(
            run_composite_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed2,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep),
            )
        )

    if bool(getattr(args, "regime_control_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed_rc: Optional[int] = int(_rs) if _rs is not None else None
        _rep = getattr(args, "replay_repeat", None)
        _nrep_rc = int(_rep) if _rep is not None else 10
        return int(
            run_regime_control_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_rc,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_rc),
            )
        )

    if bool(getattr(args, "weak_risk_filter_sweep", False)):
        _rs = getattr(args, "replay_seed", None)
        _seed_wrf: Optional[int] = int(_rs) if _rs is not None else None
        _rep = getattr(args, "replay_repeat", None)
        _nrep_wrf = int(_rep) if _rep is not None else 10
        return int(
            run_weak_risk_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_wrf,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_wrf),
            )
        )

    if bool(getattr(args, "strong_risk_filter_sweep", False)):
        _rs2 = getattr(args, "replay_seed", None)
        _seed_srf: Optional[int] = int(_rs2) if _rs2 is not None else None
        _rep2 = getattr(args, "replay_repeat", None)
        _nrep_srf = int(_rep2) if _rep2 is not None else 10
        return int(
            run_strong_risk_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_srf,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_srf),
            )
        )

    if bool(getattr(args, "strong_combo_filter_sweep", False)):
        _rs3 = getattr(args, "replay_seed", None)
        _seed_scf: Optional[int] = int(_rs3) if _rs3 is not None else None
        _rep3 = getattr(args, "replay_repeat", None)
        _nrep_scf = int(_rep3) if _rep3 is not None else 10
        return int(
            run_strong_combo_filter_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_scf,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_scf),
            )
        )

    if bool(getattr(args, "strong_trend_quality_sweep", False)):
        _rs_stq = getattr(args, "replay_seed", None)
        _seed_stq: Optional[int] = int(_rs_stq) if _rs_stq is not None else None
        _rep_stq = getattr(args, "replay_repeat", None)
        _nrep_stq = int(_rep_stq) if _rep_stq is not None else 10
        return int(
            run_strong_trend_quality_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_stq,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_stq),
            )
        )

    if bool(getattr(args, "strong_trend_quality_validation_sweep", False)):
        _rs_val = getattr(args, "replay_seed", None)
        _seed_val: Optional[int] = int(_rs_val) if _rs_val is not None else None
        _rep_val = getattr(args, "replay_repeat", None)
        _nrep_val = int(_rep_val) if _rep_val is not None else 20
        return int(
            run_strong_trend_quality_validation_sweep(
                fixed_watch=fixed_watch,
                interval_sec=interval_sec,
                only_changes=only_changes,
                replay_seed=_seed_val,
                replay_mode=str(getattr(args, "replay_mode", "normal") or "normal"),
                n_repeat=int(_nrep_val),
            )
        )

    if paper_trade:
        _pti = float(getattr(args, "paper_trade_interval", 60.0) or 60.0)
        if _pti <= 0:
            print(f"[{now_str()}] --paper-trade-interval は 0 より大きい値にしてください。")
            return 2
        _batch_pt = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        _pt_kw: dict[str, Any] = {
            "interval_sec": interval_sec,
            "only_changes": only_changes,
            "fixed_watch": fixed_watch,
            "replay_range": "1d",
            "replay_random_days": 0,
            "replay_random_months": 3,
            "replay_seed": None,
            "replay_mode": "fast",
            "replay_fast_discord": False,
            "replay_fast_verbose": False,
            "replay_fast_print_signal_details": False,
            "replay_market_debug": False,
            "replay_repeat_run_no": 1,
            "replay_repeat_total": 1,
            "replay_output_subdir": "",
            "replay_batch_stamp": _batch_pt,
            "replay_morning_screen_hhmm": "",
            "one_trade_per_symbol_per_day": one_trade_per_symbol_per_day,
            "enable_add": enable_add,
            "replay_early_exit_before_stop": replay_early_exit_before_stop,
            "replay_early_exit_vwap": bool(replay_early_exit_vwap),
            "replay_early_exit_recent_low": bool(replay_early_exit_recent_low),
            "replay_disable_afternoon_entry": bool(replay_disable_afternoon_entry),
            "replay_strict_afternoon_entry": bool(replay_strict_afternoon_entry),
            "replay_afternoon_topix_weak_block": bool(replay_afternoon_topix_weak_block),
            "replay_config_name": str(replay_config_name or ""),
            "replay_config_path": str(replay_config_path or ""),
            "aft_volume_spike_ratio_min": float(aft_volume_spike_ratio_min),
            "aft_vwap_dist_pct_max": float(aft_vwap_dist_pct_max),
            "aft_rebreak_mult": float(aft_rebreak_mult),
            "entry_filter_rsi_enabled": bool(entry_filter_rsi_enabled),
            "entry_filter_rsi_exclude_above": float(entry_filter_rsi_exclude_above),
            "entry_filter_vwap_distance_enabled": bool(entry_filter_vwap_distance_enabled),
            "entry_filter_vwap_distance_exclude_above": float(entry_filter_vwap_distance_exclude_above),
            "entry_filter_atr_pct_enabled": bool(entry_filter_atr_pct_enabled),
            "entry_filter_atr_pct_exclude_above": float(entry_filter_atr_pct_exclude_above),
            "daily_loss_stop_enabled": bool(daily_loss_stop_enabled),
            "daily_loss_stop_threshold_yen_100_shares": float(daily_loss_stop_threshold_yen_100_shares),
            "regime_filter_disable_morning_weak": bool(regime_filter_disable_morning_weak),
            "regime_filter_disable_rising_ratio_lt50": bool(regime_filter_disable_rising_ratio_lt50),
            "regime_filter_disable_topix_weak": bool(regime_filter_disable_topix_weak),
            "regime_filter_topix_weak_threshold_pct": regime_filter_topix_weak_threshold_pct,
            "signal_filter_disable_gap_ge_pct": bool(signal_filter_disable_gap_ge_pct),
            "signal_filter_gap_ge_threshold_pct": float(signal_filter_gap_ge_threshold_pct),
            "signal_filter_disable_vwap_distance_ge_pct": bool(signal_filter_disable_vwap_distance_ge_pct),
            "signal_filter_vwap_distance_ge_threshold_pct": float(signal_filter_vwap_distance_ge_threshold_pct),
            "signal_filter_disable_entry_after_hhmm": bool(signal_filter_disable_entry_after_hhmm),
            "signal_filter_entry_after_hhmm": str(signal_filter_entry_after_hhmm),
        }
        _pt_kw.update(_replay_composite_signal_filter_kwargs_from_flags(cfg_flags))
        _pt_kw.update(_replay_regime_control_kwargs_from_flags(cfg_flags))
        _pt_kw["replay_settings"] = replay_settings
        return int(run_paper_trade(paper_trade_interval_sec=_pti, run_replay_kw=_pt_kw))

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
        if str(replay_range) in FIXED_RANDOM_REPLAY_LABELS and int(replay_random_days or 0) <= 0:
            replay_random_days = 5

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
        if str(replay_range) in FIXED_RANDOM_REPLAY_LABELS:
            repeat_label = str(replay_range)
        elif int(replay_random_days or 0) > 0:
            repeat_label = f"random_{int(replay_random_days)}d"
        else:
            repeat_label = str(replay_range)

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
                    entry_filter_rsi_enabled=bool(entry_filter_rsi_enabled),
                    entry_filter_rsi_exclude_above=float(entry_filter_rsi_exclude_above),
                    entry_filter_vwap_distance_enabled=bool(entry_filter_vwap_distance_enabled),
                    entry_filter_vwap_distance_exclude_above=float(entry_filter_vwap_distance_exclude_above),
                    entry_filter_atr_pct_enabled=bool(entry_filter_atr_pct_enabled),
                    entry_filter_atr_pct_exclude_above=float(entry_filter_atr_pct_exclude_above),
                    daily_loss_stop_enabled=bool(daily_loss_stop_enabled),
                    daily_loss_stop_threshold_yen_100_shares=float(daily_loss_stop_threshold_yen_100_shares),
                    regime_filter_disable_morning_weak=bool(regime_filter_disable_morning_weak),
                    regime_filter_disable_rising_ratio_lt50=bool(regime_filter_disable_rising_ratio_lt50),
                    regime_filter_disable_topix_weak=bool(regime_filter_disable_topix_weak),
                    regime_filter_topix_weak_threshold_pct=regime_filter_topix_weak_threshold_pct,
                    signal_filter_disable_gap_ge_pct=bool(signal_filter_disable_gap_ge_pct),
                    signal_filter_gap_ge_threshold_pct=float(signal_filter_gap_ge_threshold_pct),
                    signal_filter_disable_vwap_distance_ge_pct=bool(signal_filter_disable_vwap_distance_ge_pct),
                    signal_filter_vwap_distance_ge_threshold_pct=float(signal_filter_vwap_distance_ge_threshold_pct),
                    signal_filter_disable_entry_after_hhmm=bool(signal_filter_disable_entry_after_hhmm),
                    signal_filter_entry_after_hhmm=str(signal_filter_entry_after_hhmm),
                    **_replay_composite_signal_filter_kwargs_from_flags(cfg_flags),
                    **_replay_regime_control_kwargs_from_flags(cfg_flags),
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
                rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
                rf = ((rep.get("overall_summary") or {}).get("regime_filters")) or {}
                run_stats.append(
                    {
                        "signals": sigs,
                        "win_rate_pct": wr,
                        "pnl": pnl,
                        "exp": exp,
                        "max_intraday_dd": float(rc.get("max_intraday_drawdown_yen_100_shares") or 0.0)
                        if isinstance(rc, dict)
                        else 0.0,
                        "avg_daily_dd": float(rc.get("avg_daily_drawdown_yen_100_shares") or 0.0)
                        if isinstance(rc, dict)
                        else 0.0,
                        "daily_loss_stop_trigger_count": int(rc.get("daily_loss_stop_trigger_count") or 0)
                        if isinstance(rc, dict)
                        else 0,
                        "daily_loss_stop_skipped_entries": int(rc.get("daily_loss_stop_skipped_entries") or 0)
                        if isinstance(rc, dict)
                        else 0,
                        "regime_filter_skipped_signals": int(rf.get("skipped_signals_count") or 0) if isinstance(rf, dict) else 0,
                    }
                )

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
            max_intraday_dd_worst = max(float(x.get("max_intraday_dd") or 0.0) for x in run_stats) if run_stats else 0.0
            avg_daily_dd_avg = (
                sum(float(x.get("avg_daily_dd") or 0.0) for x in run_stats) / float(total_runs)
                if total_runs > 0
                else 0.0
            )
            daily_loss_stop_trigger_count_total = sum(int(x.get("daily_loss_stop_trigger_count") or 0) for x in run_stats)
            daily_loss_stop_skipped_entries_total = sum(int(x.get("daily_loss_stop_skipped_entries") or 0) for x in run_stats)
            regime_filter_skipped_signals_total = sum(int(x.get("regime_filter_skipped_signals") or 0) for x in run_stats)
            plus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) > 0)
            minus_runs = sum(1 for x in run_stats if float(x.get("pnl") or 0.0) < 0)
            max_win_run = max(run_stats, key=lambda x: float(x.get("pnl") or 0.0))
            max_lose_run = min(run_stats, key=lambda x: float(x.get("pnl") or 0.0))

            sum_lose_worst10_yen = 0.0
            for rr in run_summaries:
                rep = rr.get("report") or {}
                aa = rep.get("accident_analysis") or {}
                lw = aa.get("lose_worst10") or []
                if not isinstance(lw, list):
                    continue
                for it in lw:
                    try:
                        sum_lose_worst10_yen += float(it.get("pnl_yen_100_shares") or 0.0)
                    except Exception:
                        continue

            # 銘柄別期待値ランキング（合算）
            sym_rank = []
            for sym, pnl in by_symbol_pnl.items():
                n_sig = int(by_symbol_signals.get(sym, 0))
                exp2 = (float(pnl) / float(n_sig)) if n_sig > 0 else 0.0
                sym_rank.append({"symbol": sym, "signals": n_sig, "pnl_yen_100_shares": float(pnl), "expectancy_yen_100_shares": float(exp2)})
            sym_rank_sorted = sorted(sym_rank, key=lambda x: float(x.get("expectancy_yen_100_shares") or 0.0), reverse=True)[:30]

            # 銘柄依存（symbol contribution）分析（run合算）
            sym_contrib = _build_symbol_contribution_analysis(
                by_symbol_summary={
                    sym: {"signals": int(by_symbol_signals.get(sym, 0)), "pnl_yen_100_shares": float(pnl)}
                    for sym, pnl in by_symbol_pnl.items()
                },
                total_pnl_yen_100_shares=float(total_pnl),
                total_signals=int(total_signals),
                exclude_top_n_symbols_list=(1, 2, 3),
            )

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
                    "max_intraday_drawdown_yen_100_shares_worst": float(max_intraday_dd_worst),
                    "avg_daily_drawdown_yen_100_shares_avg": float(avg_daily_dd_avg),
                    "daily_loss_stop_trigger_count_total": int(daily_loss_stop_trigger_count_total),
                    "daily_loss_stop_skipped_entries_total": int(daily_loss_stop_skipped_entries_total),
                    "regime_filter_skipped_signals_total": int(regime_filter_skipped_signals_total),
                    "add_on_total_pnl_yen_100_shares": float(add_on_total),
                    "add_off_ref_total_pnl_yen_100_shares": float(add_off_ref_total),
                    "plus_runs": int(plus_runs),
                    "minus_runs": int(minus_runs),
                    "max_win_run_pnl_yen_100_shares": float(max_win_run.get("pnl") or 0.0),
                    "max_lose_run_pnl_yen_100_shares": float(max_lose_run.get("pnl") or 0.0),
                    "sum_lose_worst10_yen_100_shares": float(sum_lose_worst10_yen),
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
                "symbol_contribution_analysis": sym_contrib,
                "signal_feature_analysis": _build_signal_feature_analysis_from_signal_dicts(
                    [s for rr in run_summaries for s in ((rr.get("report") or {}).get("signals") or []) if isinstance(s, dict)]
                ),
                "signal_composite_feature_analysis": _build_composite_signal_feature_analysis_from_signal_dicts(
                    [s for rr in run_summaries for s in ((rr.get("report") or {}).get("signals") or []) if isinstance(s, dict)]
                ),
                "strong_loser_analysis": _build_strong_loser_analysis_from_signal_dicts(
                    [s for rr in run_summaries for s in ((rr.get("report") or {}).get("signals") or []) if isinstance(s, dict)]
                ),
                "signal_state_cross_analysis": _build_signal_state_cross_analysis_from_signal_dicts(
                    [s for rr in run_summaries for s in ((rr.get("report") or {}).get("signals") or []) if isinstance(s, dict)]
                ),
                "combo_filter_analysis": _aggregate_combo_filter_analysis_from_run_summaries(run_summaries),
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
            debug_lines: list[str] = []
            lines.append("=== Replay 合算サマリー ===")
            lines.append(f"debug_file: {name_base}_debug.txt")
            lines.append("")
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
                rs_first = None
                for rr in run_summaries:
                    rep = rr.get("report") or {}
                    rs_first = rep.get("replay_settings") if isinstance(rep.get("replay_settings"), dict) else None
                    if rs_first:
                        break
                if isinstance(rs_first, dict):
                    for pk in ("replay_date_pool_start", "replay_date_pool_end", "replay_candidate_days_count"):
                        pv = rs_first.get(pk)
                        if isinstance(pv, dict):
                            lines.append(f"{pk}: {pv.get('value')} ({pv.get('source')})")
                ef_hdr = rs_first.get("entry_filters") if isinstance(rs_first.get("entry_filters"), dict) else {}
                if isinstance(ef_hdr, dict):
                    for fk, label in [
                        ("rsi", "RSI filter"),
                        ("vwap_distance_pct", "VWAP distance filter"),
                        ("atr_pct", "ATR filter"),
                    ]:
                        sub = ef_hdr.get(fk) if isinstance(ef_hdr.get(fk), dict) else {}
                        en = sub.get("enabled") if isinstance(sub.get("enabled"), dict) else {}
                        thr = sub.get("exclude_above") if isinstance(sub.get("exclude_above"), dict) else {}
                        if isinstance(en, dict) and isinstance(thr, dict):
                            lines.append(
                                f"{label}: enabled={en.get('value')} threshold={thr.get('value')} "
                                f"({en.get('source')}/{thr.get('source')})"
                            )
                lines.append("")
            except Exception:
                pass
            lines.append(f"- saved_at_jst: {agg['meta']['saved_at_jst']}")
            lines.append(f"- repeat_label: {repeat_label}")
            if output_subdir:
                lines.append(f"- output_folder: results/{output_subdir}/")
            lines.append(f"- 実行回数: {total_runs}")
            s = agg["summary"]
            lines.append("【比較指標（run合算・同一replay-range内の条件比較）】")
            lines.append(f"- 平均expectancy(円/信号・run平均): {s['avg_expectancy_yen_100_shares']:+,.0f}円")
            lines.append(f"- 合計100株損益: {s['total_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- 最大負けrun: {s['max_lose_run_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- プラスrun数/マイナスrun数: {s['plus_runs']}/{s['minus_runs']}")
            lines.append(f"- lose_worst10_sum合算(全run): {float(s.get('sum_lose_worst10_yen_100_shares') or 0.0):+,.0f}円")
            lines.append(
                f"- max_intraday_drawdown(最悪run): {float(s.get('max_intraday_drawdown_yen_100_shares_worst') or 0.0):+,.0f}円"
            )
            lines.append(
                f"- avg_daily_drawdown(run平均): {float(s.get('avg_daily_drawdown_yen_100_shares_avg') or 0.0):+,.0f}円"
            )
            lines.append(
                f"- daily_loss_stop_trigger_count(合算): {int(s.get('daily_loss_stop_trigger_count_total') or 0)}"
            )
            lines.append(
                f"- daily_loss_stop_skipped_entries(合算): {int(s.get('daily_loss_stop_skipped_entries_total') or 0)}"
            )
            lines.append(
                f"- regime_filter_skipped_signals_count(合算): {int(s.get('regime_filter_skipped_signals_total') or 0)}"
            )
            lines.append(f"- 合計signal数: {s['total_signals']}")
            lines.append("")
            lines.append("【その他サマリー】")
            lines.append(f"- 平均勝率: {s['avg_win_rate_pct']:.1f}%")
            lines.append(f"- 平均100株損益: {s['avg_pnl_yen_100_shares']:+,.0f}円")
            lines.append(f"- ADD ON時損益(合算,100株): {float(s.get('add_on_total_pnl_yen_100_shares') or 0.0):+,.0f}円")
            lines.append(f"- ADD OFF時損益(参考/BASEのみ合算,100株): {float(s.get('add_off_ref_total_pnl_yen_100_shares') or 0.0):+,.0f}円")
            lines.append(f"- 最大勝ちrun: {s['max_win_run_pnl_yen_100_shares']:+,.0f}円")
            lines.append("")

            # =========================
            # TIME_BUCKET_ANALYSIS（run合算）
            # =========================
            tb_order = [
                "09:00-09:30",
                "09:30-10:00",
                "10:00-10:30",
                "10:30-11:00",
                "11:00-11:30",
                "12:30-13:00",
                "13:00-14:00",
                "14:00-15:00",
            ]
            tb_tot: dict[str, dict[str, float]] = {}
            tb_cnt: dict[str, dict[str, int]] = {}
            for rr in run_summaries:
                rep = rr.get("report") or {}
                tba = rep.get("time_bucket_analysis") or {}
                if not isinstance(tba, dict):
                    continue
                for b, row in tba.items():
                    if b not in tb_order or not isinstance(row, dict):
                        continue
                    tb_cnt.setdefault(b, {})
                    tb_tot.setdefault(b, {})
                    tb_cnt[b]["signals"] = int(tb_cnt[b].get("signals", 0)) + int(row.get("signals") or 0)
                    tb_cnt[b]["wins"] = int(tb_cnt[b].get("wins", 0)) + int(round((float(row.get("winrate_pct") or 0.0) / 100.0) * float(row.get("signals") or 0)))
                    tb_tot[b]["pnl"] = float(tb_tot[b].get("pnl", 0.0)) + float(row.get("total_pnl_yen_100_shares") or 0.0)
                    tb_tot[b]["lose_worst10_sum"] = float(tb_tot[b].get("lose_worst10_sum", 0.0)) + float(
                        row.get("lose_worst10_sum_yen_100_shares") or 0.0
                    )
                    # hold minutes は signals 加重で合算
                    tb_tot[b]["hold_min_sum"] = float(tb_tot[b].get("hold_min_sum", 0.0)) + float(
                        row.get("avg_hold_minutes") or 0.0
                    ) * float(row.get("signals") or 0.0)

            lines.append("[TIME_BUCKET_ANALYSIS]")
            lines.append("")
            for b in tb_order:
                sigs = int((tb_cnt.get(b, {}) or {}).get("signals", 0))
                if sigs <= 0:
                    continue
                pnl = float((tb_tot.get(b, {}) or {}).get("pnl", 0.0))
                exp_y = (pnl / float(sigs)) if sigs > 0 else 0.0
                wins_est = int((tb_cnt.get(b, {}) or {}).get("wins", 0))
                winrate = (float(wins_est) / float(sigs) * 100.0) if sigs > 0 else 0.0
                lw10 = float((tb_tot.get(b, {}) or {}).get("lose_worst10_sum", 0.0))
                hm_avg = float((tb_tot.get(b, {}) or {}).get("hold_min_sum", 0.0)) / float(sigs) if sigs > 0 else 0.0
                lines.append(f"bucket: {b}")
                lines.append(f"signals: {sigs}")
                lines.append(f"winrate: {winrate:.1f}%")
                lines.append(f"expectancy: {exp_y:+.0f}")
                lines.append(f"total_pnl: {pnl:+.0f}")
                lines.append(f"lose_worst10_sum: {lw10:+.0f}")
                lines.append(f"avg_hold_minutes: {hm_avg:.1f}")
                lines.append("")

            # =========================
            # MARKET_REGIME_ANALYSIS（run合算）
            # =========================
            mr_tot: dict[str, dict[str, float]] = {}
            mr_cnt: dict[str, dict[str, int]] = {}
            for rr in run_summaries:
                rep = rr.get("report") or {}
                mra = rep.get("market_regime_analysis") or {}
                if not isinstance(mra, dict):
                    continue
                for k, row in mra.items():
                    if not isinstance(row, dict):
                        continue
                    mr_cnt.setdefault(k, {})
                    mr_tot.setdefault(k, {})
                    mr_cnt[k]["signals"] = int(mr_cnt[k].get("signals", 0)) + int(row.get("signals") or 0)
                    mr_cnt[k]["wins_est"] = int(mr_cnt[k].get("wins_est", 0)) + int(
                        round((float(row.get("winrate_pct") or 0.0) / 100.0) * float(row.get("signals") or 0))
                    )
                    mr_tot[k]["pnl"] = float(mr_tot[k].get("pnl", 0.0)) + float(row.get("total_pnl_yen_100_shares") or 0.0)
                    mr_tot[k]["lw10"] = float(mr_tot[k].get("lw10", 0.0)) + float(row.get("lose_worst10_sum_yen_100_shares") or 0.0)
                    mr_tot[k]["max_dd"] = float(max(float(mr_tot[k].get("max_dd", 0.0)), float(row.get("max_drawdown_yen_100_shares_est") or 0.0)))

            if mr_tot:
                lines.append("[MARKET_REGIME_ANALYSIS]")
                lines.append("")
                for k in sorted(mr_tot.keys()):
                    sigs = int((mr_cnt.get(k, {}) or {}).get("signals", 0))
                    if sigs <= 0:
                        continue
                    wins = int((mr_cnt.get(k, {}) or {}).get("wins_est", 0))
                    winrate = (float(wins) / float(sigs) * 100.0) if sigs > 0 else 0.0
                    pnl = float((mr_tot.get(k, {}) or {}).get("pnl", 0.0))
                    exp_y = (pnl / float(sigs)) if sigs > 0 else 0.0
                    lw10 = float((mr_tot.get(k, {}) or {}).get("lw10", 0.0))
                    mdd = float((mr_tot.get(k, {}) or {}).get("max_dd", 0.0))
                    lines.append(f"regime: {k}")
                    lines.append(f"signals: {sigs}")
                    lines.append(f"winrate: {winrate:.1f}%")
                    lines.append(f"avg_expectancy_yen_100_shares: {exp_y:+.0f}")
                    lines.append(f"total_pnl_100_shares: {pnl:+.0f}")
                    lines.append(f"lose_worst10_sum: {lw10:+.0f}")
                    lines.append(f"max_drawdown: {mdd:+.0f}")
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
                        lines.append(
                            "  RSI filter: "
                            f"enabled={bool(cfg.get('entry_filter_rsi_enabled', False))} "
                            f"threshold={float(cfg.get('entry_filter_rsi_exclude_above', 75.0))}"
                        )
                        lines.append(
                            "  VWAP distance filter: "
                            f"enabled={bool(cfg.get('entry_filter_vwap_distance_enabled', False))} "
                            f"threshold={float(cfg.get('entry_filter_vwap_distance_exclude_above', 2.0))}%"
                        )
                        lines.append(
                            "  ATR filter: "
                            f"enabled={bool(cfg.get('entry_filter_atr_pct_enabled', False))} "
                            f"threshold={float(cfg.get('entry_filter_atr_pct_exclude_above', 4.0))}%"
                        )
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

            lines.append("【銘柄別 期待値ランキング（合算）】")
            for it in agg["by_symbol_expectancy_ranking"][:10]:
                lines.append(
                    f"- {it['symbol']}: signals={int(it['signals'])}  expectancy={float(it['expectancy_yen_100_shares']):+,.0f}円  "
                    f"100株損益={float(it['pnl_yen_100_shares']):+,.0f}円"
                )
            lines.append("")

            # =========================
            # SYMBOL_CONTRIBUTION_ANALYSIS（run合算）
            # =========================
            try:
                sca = agg.get("symbol_contribution_analysis") or {}
                lines.append("【銘柄依存分析（symbol contribution）】")
                lines.append("")
                bys = sca.get("by_symbol") or []
                if isinstance(bys, list) and bys:
                    lines.append("- symbol pnl contribution (pnl desc, top10):")
                    for it in bys[:10]:
                        if not isinstance(it, dict):
                            continue
                        lines.append(
                            f"  - {it.get('symbol')}: pnl={float(it.get('pnl_yen_100_shares') or 0.0):+,.0f}円 "
                            f"ratio={float(it.get('pnl_ratio_of_total') or 0.0):+.2f} "
                            f"signals={int(it.get('signals') or 0)} "
                            f"cum={float(it.get('cumulative_pnl_yen_100_shares') or 0.0):+,.0f}円"
                        )
                sims = sca.get("exclude_top_n_simulation") or []
                if isinstance(sims, list) and sims:
                    lines.append("")
                    lines.append("- exclude top N symbols simulation:")
                    for sim in sims:
                        if not isinstance(sim, dict):
                            continue
                        lines.append(
                            f"  - exclude_top_n={int(sim.get('exclude_top_n_symbols') or 0)} "
                            f"symbols={sim.get('excluded_symbols') or []} "
                            f"total_pnl_after={float(sim.get('total_pnl_after_yen_100_shares') or 0.0):+,.0f}円 "
                            f"expectancy_after={float(sim.get('expectancy_after_yen_100_shares_per_signal') or 0.0):+,.0f}円 "
                            f"(signals_after={int(sim.get('total_signals_after') or 0)})"
                        )
                lines.append("")
            except Exception:
                pass

            # =========================
            # SIGNAL_FEATURE_ANALYSIS（run合算）
            # =========================
            try:
                sfa = agg.get("signal_feature_analysis") or {}
                lines.append("【SIGNAL_FEATURE_ANALYSIS】")
                lines.append("")
                for feat in ["gap_pct", "entry_vwap_distance_pct", "first_30m_volume_ratio", "atr_pct"]:
                    rows = sfa.get(feat) or []
                    if not isinstance(rows, list) or not rows:
                        continue
                    lines.append(f"[{feat}]")
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        lines.append(
                            f"- bucket={r.get('bucket')}: signals={int(r.get('signals') or 0)} "
                            f"winrate={float(r.get('winrate_pct') or 0.0):.1f}% "
                            f"avg_expectancy_yen_100_shares={float(r.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f} "
                            f"total_pnl_yen_100_shares={float(r.get('total_pnl_yen_100_shares') or 0.0):+,.0f} "
                            f"lose_worst10_sum_yen_100_shares={float(r.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f}"
                        )
                    lines.append("")
            except Exception:
                pass

            # =========================
            # SIGNAL_COMPOSITE_FEATURE_ANALYSIS（run合算）
            # =========================
            try:
                scfa = agg.get("signal_composite_feature_analysis") or {}
                lines.append("【SIGNAL_COMPOSITE_FEATURE_ANALYSIS】")
                lines.append("")
                _scfa_sections: list[tuple[str, str, str]] = [
                    ("gap_pct_x_market_regime", "gap_pct_bucket", "market_regime"),
                    ("gap_pct_x_time_bucket", "gap_pct_bucket", "entry_time_bucket"),
                    ("entry_vwap_distance_pct_x_market_regime", "entry_vwap_distance_pct_bucket", "market_regime"),
                    ("entry_vwap_distance_pct_x_time_bucket", "entry_vwap_distance_pct_bucket", "entry_time_bucket"),
                ]
                for sec_key, fk1, fk2 in _scfa_sections:
                    rows2 = scfa.get(sec_key) or []
                    if not isinstance(rows2, list) or not rows2:
                        continue
                    lines.append(f"[{sec_key}]")
                    for r2 in rows2:
                        if not isinstance(r2, dict):
                            continue
                        lines.append(
                            f"- {fk1}={r2.get(fk1)} × {fk2}={r2.get(fk2)}: "
                            f"signals={int(r2.get('signals') or 0)} "
                            f"winrate={float(r2.get('winrate_pct') or 0.0):.1f}% "
                            f"avg_expectancy_yen_100_shares={float(r2.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f} "
                            f"total_pnl_yen_100_shares={float(r2.get('total_pnl_yen_100_shares') or 0.0):+,.0f} "
                            f"lose_worst10_sum_yen_100_shares={float(r2.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f}"
                        )
                    lines.append("")
            except Exception:
                pass

            # =========================
            # STRONG_LOSER_ANALYSIS（run合算）
            # =========================
            try:
                sla = agg.get("strong_loser_analysis") or {}
                lines.append("【STRONG_LOSER_ANALYSIS】")
                lines.append("")
                for feat in [
                    "gap_pct",
                    "entry_vwap_distance_pct",
                    "atr_pct",
                    "entry_time_bucket",
                    "hold_minutes",
                    "high_update_count_before_entry",
                ]:
                    rows_sla = sla.get(feat) or []
                    if not isinstance(rows_sla, list) or not rows_sla:
                        continue
                    lines.append(f"[STRONG_LOSER_ANALYSIS / {feat}]")
                    for r in rows_sla:
                        if not isinstance(r, dict):
                            continue
                        lines.append(
                            f"- bucket={r.get('bucket')}: signals={int(r.get('signals') or 0)} "
                            f"total_pnl_yen_100_shares={float(r.get('total_pnl_yen_100_shares') or 0.0):+,.0f} "
                            f"avg_expectancy_yen_100_shares={float(r.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f} "
                            f"lose_worst10_sum_yen_100_shares={float(r.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f}"
                        )
                    lines.append("")
            except Exception:
                pass

            # =========================
            # SIGNAL_STATE_CROSS_ANALYSIS（run合算）
            # =========================
            try:
                ssca = agg.get("signal_state_cross_analysis") or {}
                lines.append("【SIGNAL_STATE_CROSS_ANALYSIS】")
                lines.append("")
                _ssca_sections: list[tuple[str, str, str]] = [
                    (
                        "high_update_count_before_entry_x_market_regime",
                        "high_update_count_before_entry_bucket",
                        "market_regime",
                    ),
                    (
                        "high_update_count_before_entry_x_entry_vwap_distance_pct_bucket",
                        "high_update_count_before_entry_bucket",
                        "entry_vwap_distance_pct_bucket",
                    ),
                    ("hold_minutes_x_market_regime", "hold_minutes_bucket", "market_regime"),
                    (
                        "hold_minutes_x_entry_vwap_distance_pct_bucket",
                        "hold_minutes_bucket",
                        "entry_vwap_distance_pct_bucket",
                    ),
                ]
                for sec_key, fk1, fk2 in _ssca_sections:
                    rows_ss = ssca.get(sec_key) or []
                    if not isinstance(rows_ss, list) or not rows_ss:
                        continue
                    lines.append(f"[SIGNAL_STATE_CROSS_ANALYSIS / {sec_key}]")
                    for rss in rows_ss:
                        if not isinstance(rss, dict):
                            continue
                        lines.append(
                            f"- {fk1}={rss.get(fk1)} × {fk2}={rss.get(fk2)}: "
                            f"signals={int(rss.get('signals') or 0)} "
                            f"winrate={float(rss.get('winrate_pct') or 0.0):.1f}% "
                            f"avg_expectancy_yen_100_shares={float(rss.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f} "
                            f"total_pnl_yen_100_shares={float(rss.get('total_pnl_yen_100_shares') or 0.0):+,.0f} "
                            f"lose_worst10_sum_yen_100_shares={float(rss.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f}"
                        )
                    lines.append("")
            except Exception:
                pass

            # =========================
            # COMBO_FILTER_ANALYSIS（run合算）
            # =========================
            try:
                cfa_agg = agg.get("combo_filter_analysis") or {}
                sc_agg = cfa_agg.get("strong_combo_filter") if isinstance(cfa_agg.get("strong_combo_filter"), dict) else {}
                lines.append("【COMBO_FILTER_ANALYSIS】")
                lines.append("")
                if isinstance(sc_agg, dict) and sc_agg:
                    lines.append(f"- enabled: {bool(sc_agg.get('enabled', False))}")
                    skr = sc_agg.get("skip_reason_counts") if isinstance(sc_agg.get("skip_reason_counts"), dict) else {}
                    if skr:
                        lines.append("- reason別 skipped_signals:")
                        for rk, cnt in sorted(skr.items(), key=lambda kv: int(kv[1]), reverse=True):
                            lines.append(f"  - {rk}: {int(cnt)}")
                    vpa_c = sc_agg.get("virtual_pnl_analysis") if isinstance(sc_agg.get("virtual_pnl_analysis"), dict) else {}
                    br_c = vpa_c.get("by_reason") if isinstance(vpa_c.get("by_reason"), dict) else {}
                    if br_c:
                        lines.append("- reason別 virtual expectancy / prevented_loss_estimate:")
                        for rk in sorted(br_c.keys()):
                            row_c = br_c.get(rk) if isinstance(br_c.get(rk), dict) else {}
                            lines.append(
                                f"  - [{rk}] skipped={int(row_c.get('skipped_signals_count') or 0)} "
                                f"virtual_exp_if_skipped={float(row_c.get('avg_expectancy_yen_100_shares_if_skipped') or 0.0):+,.0f}円 "
                                f"prevented_loss_est={float(row_c.get('prevented_loss_estimate_yen_100_shares') or 0.0):+,.0f}円"
                            )
                    lines.append("")
            except Exception:
                pass

            signal_filter_agg_lines: list[str] = []
            regime_control_agg_lines: list[str] = []
            # =========================
            # SIGNAL_FILTER_ANALYSIS（run合算）
            # =========================
            try:
                skipped_total = 0
                skip_reason_counts: dict[str, int] = {}
                virt_cnt = 0
                virt_pnl = 0.0
                composite_skipped = 0
                composite_skip_reason: dict[str, int] = {}
                comp_virt_skipped = 0
                comp_virt_pnl = 0.0
                comp_prevented = 0.0
                for rr in run_summaries:
                    rep = rr.get("report") or {}
                    sf = ((rep.get("overall_summary") or {}).get("signal_filters")) or {}
                    if not isinstance(sf, dict):
                        continue
                    skipped_total += int(sf.get("skipped_signals_count") or 0)
                    for k, v in (sf.get("skip_reason_counts") or {}).items():
                        try:
                            kk = str(k)
                            vv = int(v)
                            if kk:
                                skip_reason_counts[kk] = int(skip_reason_counts.get(kk, 0)) + vv
                        except Exception:
                            continue
                    vpa = sf.get("virtual_pnl_analysis") or {}
                    if isinstance(vpa, dict):
                        virt_cnt += int(vpa.get("skipped_signals_count") or 0)
                        virt_pnl += float(vpa.get("total_pnl_yen_100_shares") or 0.0)

                    csf = sf.get("composite_signal_filters") or {}
                    if isinstance(csf, dict):
                        composite_skipped += int(csf.get("skipped_signals_count") or 0)
                        for ck, cv in (csf.get("skip_reason_counts") or {}).items():
                            try:
                                ckk = str(ck)
                                cvv = int(cv)
                                if ckk:
                                    composite_skip_reason[ckk] = int(composite_skip_reason.get(ckk, 0)) + cvv
                            except Exception:
                                continue
                        cvpa = csf.get("virtual_pnl_analysis") or {}
                        if isinstance(cvpa, dict):
                            comp_virt_skipped += int(cvpa.get("skipped_signals_count") or 0)
                            comp_virt_pnl += float(cvpa.get("total_pnl_yen_100_shares") or 0.0)
                            comp_prevented += float(cvpa.get("prevented_loss_estimate_yen_100_shares") or 0.0)

                prevented_all = float(-virt_pnl)
                comp_expectancy = (
                    float(comp_virt_pnl / float(comp_virt_skipped)) if int(comp_virt_skipped) > 0 else 0.0
                )

                signal_filter_agg_lines.append("[SIGNAL_FILTER_ANALYSIS]")
                signal_filter_agg_lines.append("")
                signal_filter_agg_lines.append(f"- skipped_signals_count(合算=SIGNAL_FILTER+COMPOSITE): {int(skipped_total)}")
                if skip_reason_counts:
                    signal_filter_agg_lines.append("- skip_reason_counts:")
                    for it in sorted(skip_reason_counts.items(), key=lambda x: int(x[1]), reverse=True):
                        signal_filter_agg_lines.append(f"  - {it[0]}: {int(it[1])}")
                signal_filter_agg_lines.append(f"- virtual_skipped_signals_count(合算=simple+COMPOSITE): {int(virt_cnt)}")
                signal_filter_agg_lines.append(f"- virtual_total_pnl_yen_100_shares(合算): {float(virt_pnl):+,.0f}")
                signal_filter_agg_lines.append(f"- virtual_prevented_loss_estimate_yen_100_shares(合算): {float(prevented_all):+,.0f}")
                signal_filter_agg_lines.append("")
                signal_filter_agg_lines.append("[COMPOSITE_SIGNAL_FILTERS / market_regime==WEAK のみ]")
                signal_filter_agg_lines.append(f"- composite_skipped_signals_count(合算): {int(composite_skipped)}")
                if composite_skip_reason:
                    signal_filter_agg_lines.append("- composite_skip_reason_counts:")
                    for it in sorted(composite_skip_reason.items(), key=lambda x: int(x[1]), reverse=True):
                        signal_filter_agg_lines.append(f"  - {it[0]}: {int(it[1])}")
                signal_filter_agg_lines.append(f"- composite_virtual_skipped_signals_count: {int(comp_virt_skipped)}")
                signal_filter_agg_lines.append(f"- composite_virtual_total_pnl_yen_100_shares: {float(comp_virt_pnl):+,.0f}")
                signal_filter_agg_lines.append(f"- composite_prevented_loss_estimate_yen_100_shares: {float(comp_prevented):+,.0f}")
                signal_filter_agg_lines.append(
                    f"- composite_expectancy_if_skipped_yen_100_shares: {float(comp_expectancy):+,.0f}"
                )
                signal_filter_agg_lines.append("")
                lines.extend(signal_filter_agg_lines)
            except Exception:
                pass

            # =========================
            # REGIME_CONTROL_ANALYSIS（run合算 / market_regime 別）
            # =========================
            try:
                rc_skip = 0
                rc_reasons: dict[str, int] = {}
                rc_vcnt = 0
                rc_vpnl = 0.0
                mr_acc: dict[str, dict[str, float]] = {
                    rk: {"signals": 0.0, "pnl": 0.0, "lw10": 0.0}
                    for rk in ("STRONG", "NORMAL", "WEAK", "CRASH")
                }
                for rr in run_summaries:
                    rep = rr.get("report") or {}
                    ov_rc = rep.get("overall_summary") or {}
                    rc = ov_rc.get("regime_controls") if isinstance(ov_rc.get("regime_controls"), dict) else {}
                    rc_skip += int(rc.get("skipped_signals_count") or 0)
                    for kk, vv in (rc.get("skip_reason_counts") or {}).items():
                        try:
                            ks = str(kk)
                            if ks:
                                rc_reasons[ks] = int(rc_reasons.get(ks, 0)) + int(vv or 0)
                        except Exception:
                            continue
                    vpa2 = rc.get("virtual_pnl_analysis") if isinstance(rc.get("virtual_pnl_analysis"), dict) else {}
                    if isinstance(vpa2, dict):
                        rc_vcnt += int(vpa2.get("skipped_signals_count") or 0)
                        rc_vpnl += float(vpa2.get("total_pnl_yen_100_shares") or 0.0)
                    evmr2 = rc.get("eval_by_market_regime") if isinstance(rc.get("eval_by_market_regime"), dict) else {}
                    if isinstance(evmr2, dict):
                        for rk2 in mr_acc:
                            row2 = evmr2.get(rk2)
                            if not isinstance(row2, dict):
                                continue
                            mr_acc[rk2]["signals"] += float(row2.get("signals") or 0)
                            mr_acc[rk2]["pnl"] += float(row2.get("total_pnl_yen_100_shares") or 0.0)
                            mr_acc[rk2]["lw10"] += float(row2.get("lose_worst10_sum_yen_100_shares") or 0.0)

                regime_control_agg_lines.append("[REGIME_CONTROL_ANALYSIS]")
                regime_control_agg_lines.append("")
                regime_control_agg_lines.append(f"- skipped_signals_count(run合算): {int(rc_skip)}")
                if rc_reasons:
                    regime_control_agg_lines.append("- skip_reason_counts:")
                    for it in sorted(rc_reasons.items(), key=lambda x: int(x[1]), reverse=True):
                        regime_control_agg_lines.append(f"  - {it[0]}: {int(it[1])}")
                regime_control_agg_lines.append(f"- virtual_pnl_analysis.skipped_signals_count(run合算): {int(rc_vcnt)}")
                regime_control_agg_lines.append(f"- virtual_pnl_analysis.total_pnl_yen_100_shares(run合算): {float(rc_vpnl):+,.0f}")
                if int(rc_vcnt) > 0:
                    regime_control_agg_lines.append(
                        f"- virtual_if_skipped_avg_expectancy(run合算): {float(rc_vpnl/float(rc_vcnt)):+,.0f}円"
                    )
                else:
                    regime_control_agg_lines.append("- virtual_if_skipped_avg_expectancy(run合算): 0円")
                regime_control_agg_lines.append(
                    f"- virtual_prevented_loss_estimate(run合算): {-float(rc_vpnl):+,.0f}"
                )
                regime_control_agg_lines.append("")
                regime_control_agg_lines.append("- eval_by_market_regime（実際に採用されたBASE信号・run値の単純合算）:")
                for rk3 in ("STRONG", "NORMAL", "WEAK", "CRASH"):
                    acc3 = mr_acc.get(rk3) or {}
                    n3 = int(acc3.get("signals") or 0)
                    p3 = float(acc3.get("pnl") or 0.0)
                    lw3 = float(acc3.get("lw10") or 0.0)
                    exp3 = (p3 / float(n3)) if n3 > 0 else 0.0
                    regime_control_agg_lines.append(
                        f"  - {rk3}: signals={n3}  expectancy={exp3:+,.0f}円  total_pnl={p3:+,.0f}円  lose_worst10_sum={lw3:+,.0f}円"
                    )
                regime_control_agg_lines.append("")
                lines.extend(regime_control_agg_lines)
            except Exception:
                pass

            # ----- 以下は all_runs_debug.txt へ分離（マーケット/REJECT/パイプライン等） -----
            debug_lines.append("=== Replay 合算サマリー（デバッグ詳細） ===")
            debug_lines.append(f"summary_file: {name_base}.txt")
            debug_lines.append("")
            if signal_filter_agg_lines:
                debug_lines.extend(signal_filter_agg_lines)
            if regime_control_agg_lines:
                debug_lines.extend(regime_control_agg_lines)

            # =========================
            # SYMBOL_CONTRIBUTION_ANALYSIS（デバッグ）
            # =========================
            try:
                sca = agg.get("symbol_contribution_analysis") or {}
                debug_lines.append("[SYMBOL_CONTRIBUTION_ANALYSIS]")
                debug_lines.append("")
                bys = sca.get("by_symbol") or []
                if isinstance(bys, list) and bys:
                    debug_lines.append("by_symbol (pnl desc, top30):")
                    for it in bys[:30]:
                        if not isinstance(it, dict):
                            continue
                        debug_lines.append(
                            f"- {it.get('symbol')}: pnl={float(it.get('pnl_yen_100_shares') or 0.0):+,.2f} "
                            f"ratio_total={float(it.get('pnl_ratio_of_total') or 0.0):+.4f} "
                            f"ratio_abs={float(it.get('pnl_ratio_of_abs_total') or 0.0):.4f} "
                            f"signals={int(it.get('signals') or 0)} "
                            f"cum_pnl={float(it.get('cumulative_pnl_yen_100_shares') or 0.0):+,.2f}"
                        )
                sims = sca.get("exclude_top_n_simulation") or []
                if isinstance(sims, list) and sims:
                    debug_lines.append("")
                    debug_lines.append("exclude_top_n_simulation:")
                    for sim in sims:
                        if not isinstance(sim, dict):
                            continue
                        debug_lines.append(
                            f"- exclude_top_n={int(sim.get('exclude_top_n_symbols') or 0)} "
                            f"excluded_symbols={sim.get('excluded_symbols') or []} "
                            f"pnl_after={float(sim.get('total_pnl_after_yen_100_shares') or 0.0):+,.2f} "
                            f"exp_after={float(sim.get('expectancy_after_yen_100_shares_per_signal') or 0.0):+,.2f} "
                            f"signals_after={int(sim.get('total_signals_after') or 0)}"
                        )
                debug_lines.append("")
            except Exception:
                pass

            # =========================
            # SIGNAL_COMPOSITE_FEATURE_ANALYSIS（デバッグ・全行）
            # =========================
            try:
                scfa2 = agg.get("signal_composite_feature_analysis") or {}
                debug_lines.append("[SIGNAL_COMPOSITE_FEATURE_ANALYSIS]")
                debug_lines.append("")
                _scfa_dbg: list[tuple[str, str, str]] = [
                    ("gap_pct_x_market_regime", "gap_pct_bucket", "market_regime"),
                    ("gap_pct_x_time_bucket", "gap_pct_bucket", "entry_time_bucket"),
                    ("entry_vwap_distance_pct_x_market_regime", "entry_vwap_distance_pct_bucket", "market_regime"),
                    ("entry_vwap_distance_pct_x_time_bucket", "entry_vwap_distance_pct_bucket", "entry_time_bucket"),
                ]
                for sec_key, fk1, fk2 in _scfa_dbg:
                    rows2 = scfa2.get(sec_key) or []
                    if not isinstance(rows2, list) or not rows2:
                        continue
                    debug_lines.append(f"[{sec_key}]")
                    for r2 in rows2:
                        if not isinstance(r2, dict):
                            continue
                        debug_lines.append(
                            f"- {fk1}={r2.get(fk1)} | {fk2}={r2.get(fk2)} | "
                            f"signals={int(r2.get('signals') or 0)} "
                            f"winrate_pct={float(r2.get('winrate_pct') or 0.0):.2f} "
                            f"avg_expectancy_yen_100_shares={float(r2.get('avg_expectancy_yen_100_shares') or 0.0):+.2f} "
                            f"total_pnl_yen_100_shares={float(r2.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                            f"lose_worst10_sum_yen_100_shares={float(r2.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
                        )
                    debug_lines.append("")
            except Exception:
                pass

            # =========================
            # STRONG_LOSER_ANALYSIS（デバッグ・全行）
            # =========================
            try:
                sla2 = agg.get("strong_loser_analysis") or {}
                debug_lines.append("[STRONG_LOSER_ANALYSIS]")
                debug_lines.append("")
                for feat2 in [
                    "gap_pct",
                    "entry_vwap_distance_pct",
                    "atr_pct",
                    "entry_time_bucket",
                    "hold_minutes",
                    "high_update_count_before_entry",
                ]:
                    rows_dbg = sla2.get(feat2) or []
                    if not isinstance(rows_dbg, list) or not rows_dbg:
                        continue
                    debug_lines.append(f"[STRONG_LOSER_ANALYSIS / {feat2}]")
                    for rd in rows_dbg:
                        if not isinstance(rd, dict):
                            continue
                        debug_lines.append(
                            f"- bucket={rd.get('bucket')} | "
                            f"signals={int(rd.get('signals') or 0)} "
                            f"total_pnl_yen_100_shares={float(rd.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                            f"avg_expectancy_yen_100_shares={float(rd.get('avg_expectancy_yen_100_shares') or 0.0):+.2f} "
                            f"lose_worst10_sum_yen_100_shares={float(rd.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
                        )
                    debug_lines.append("")
            except Exception:
                pass

            # =========================
            # SIGNAL_STATE_CROSS_ANALYSIS（デバッグ・全行）
            # =========================
            try:
                ssca_dbg = agg.get("signal_state_cross_analysis") or {}
                debug_lines.append("[SIGNAL_STATE_CROSS_ANALYSIS]")
                debug_lines.append("")
                _ssca_dbg: list[tuple[str, str, str]] = [
                    (
                        "high_update_count_before_entry_x_market_regime",
                        "high_update_count_before_entry_bucket",
                        "market_regime",
                    ),
                    (
                        "high_update_count_before_entry_x_entry_vwap_distance_pct_bucket",
                        "high_update_count_before_entry_bucket",
                        "entry_vwap_distance_pct_bucket",
                    ),
                    ("hold_minutes_x_market_regime", "hold_minutes_bucket", "market_regime"),
                    (
                        "hold_minutes_x_entry_vwap_distance_pct_bucket",
                        "hold_minutes_bucket",
                        "entry_vwap_distance_pct_bucket",
                    ),
                ]
                for sec_key, fk1, fk2 in _ssca_dbg:
                    rows_ssd = ssca_dbg.get(sec_key) or []
                    if not isinstance(rows_ssd, list) or not rows_ssd:
                        continue
                    debug_lines.append(f"[{sec_key}]")
                    for rsd in rows_ssd:
                        if not isinstance(rsd, dict):
                            continue
                        debug_lines.append(
                            f"- {fk1}={rsd.get(fk1)} | {fk2}={rsd.get(fk2)} | "
                            f"signals={int(rsd.get('signals') or 0)} "
                            f"winrate_pct={float(rsd.get('winrate_pct') or 0.0):.2f} "
                            f"avg_expectancy_yen_100_shares={float(rsd.get('avg_expectancy_yen_100_shares') or 0.0):+.2f} "
                            f"total_pnl_yen_100_shares={float(rsd.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                            f"lose_worst10_sum_yen_100_shares={float(rsd.get('lose_worst10_sum_yen_100_shares') or 0.0):+.2f}"
                        )
                    debug_lines.append("")
            except Exception:
                pass

            # =========================
            # COMBO_FILTER_ANALYSIS（デバッグ）
            # =========================
            try:
                cfa_dbg = agg.get("combo_filter_analysis") or {}
                sc_dbg = cfa_dbg.get("strong_combo_filter") if isinstance(cfa_dbg.get("strong_combo_filter"), dict) else {}
                debug_lines.append("[COMBO_FILTER_ANALYSIS]")
                debug_lines.append("")
                if isinstance(sc_dbg, dict) and sc_dbg:
                    debug_lines.append(f"enabled={bool(sc_dbg.get('enabled', False))}")
                    skd = sc_dbg.get("skip_reason_counts") if isinstance(sc_dbg.get("skip_reason_counts"), dict) else {}
                    if skd:
                        debug_lines.append("skip_reason_counts:")
                        for rk, cnt in sorted(skd.items(), key=lambda kv: int(kv[1]), reverse=True):
                            debug_lines.append(f"  - {rk}: {int(cnt)}")
                    vp_dbg = sc_dbg.get("virtual_pnl_analysis") if isinstance(sc_dbg.get("virtual_pnl_analysis"), dict) else {}
                    br_dbg = vp_dbg.get("by_reason") if isinstance(vp_dbg.get("by_reason"), dict) else {}
                    if br_dbg:
                        debug_lines.append("by_reason (virtual expectancy / prevented_loss):")
                        for rk in sorted(br_dbg.keys()):
                            row_d = br_dbg.get(rk) if isinstance(br_dbg.get(rk), dict) else {}
                            debug_lines.append(
                                f"  - [{rk}] skipped={int(row_d.get('skipped_signals_count') or 0)} "
                                f"virtual_resolved={int(row_d.get('virtual_resolved_count') or 0)} "
                                f"total_pnl={float(row_d.get('total_pnl_yen_100_shares') or 0.0):+.2f} "
                                f"avg_exp_if_skipped={float(row_d.get('avg_expectancy_yen_100_shares_if_skipped') or 0.0):+.2f} "
                                f"prevented_loss_est={float(row_d.get('prevented_loss_estimate_yen_100_shares') or 0.0):+.2f}"
                            )
                    debug_lines.append("")
            except Exception:
                pass

            # =========================
            # risk_controls.daily_loss_stop（デバッグ）
            # =========================
            debug_lines.append("[DAILY_LOSS_STOP_DEBUG]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
                run_no = int(rr.get("run_no") or 0)
                if not isinstance(rc, dict):
                    debug_lines.append(f"run{run_no:02d}: (no risk_controls)")
                    continue
                debug_lines.append(
                    f"run{run_no:02d}: enabled={bool(rc.get('daily_loss_stop_enabled', False))} "
                    f"thr={float(rc.get('daily_loss_stop_threshold_yen_100_shares') or 0.0):g} "
                    f"trigger_count={int(rc.get('daily_loss_stop_trigger_count') or 0)} "
                    f"skipped_entries={int(rc.get('daily_loss_stop_skipped_entries') or 0)} "
                    f"daily_pnl_min={float(rc.get('daily_pnl_min_yen_100_shares') or 0.0):+,.0f} "
                    f"max_intraday_dd={float(rc.get('max_intraday_drawdown_yen_100_shares') or 0.0):+,.0f} "
                    f"avg_daily_dd={float(rc.get('avg_daily_drawdown_yen_100_shares') or 0.0):+,.0f}"
                )
                tdays = rc.get("daily_loss_stop_triggered_days") or []
                if isinstance(tdays, list) and tdays:
                    debug_lines.append("  triggered_days:")
                    for d in tdays:
                        debug_lines.append(f"    - {d}")
            debug_lines.append("")

            debug_lines.append("[DAILY_LOSS_STOP_ANALYSIS]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                rc = ((rep.get("overall_summary") or {}).get("risk_controls")) or {}
                run_no = int(rr.get("run_no") or 0)
                if not isinstance(rc, dict):
                    continue
                an = rc.get("daily_loss_stop_analysis") or []
                if not isinstance(an, list) or not an:
                    continue
                debug_lines.append(f"run{run_no:02d}:")
                for it in an:
                    if not isinstance(it, dict):
                        continue
                    debug_lines.append(f"  trigger_day: {str(it.get('trigger_day_jst') or '')}")
                    debug_lines.append(f"  trigger_datetime_jst: {str(it.get('trigger_datetime_jst') or '')}")
                    debug_lines.append(
                        f"  pnl_before_trigger: {float(it.get('pnl_before_trigger_yen_100_shares') or 0.0):+,.0f}"
                    )
                    debug_lines.append(
                        f"  skipped_entries_count_after_trigger: {int(it.get('skipped_entries_count_after_trigger') or 0)}"
                    )
                    debug_lines.append(
                        f"  skipped_entries_virtual_pnl_sum: {float(it.get('skipped_entries_virtual_pnl_sum_yen_100_shares') or 0.0):+,.0f}"
                    )
                    debug_lines.append(
                        f"  prevented_loss_estimate: {float(it.get('prevented_loss_estimate_yen_100_shares') or 0.0):+,.0f}"
                    )
                    debug_lines.append(
                        f"  skipped_entries_virtual_winrate: {float(it.get('skipped_entries_virtual_winrate_pct') or 0.0):.1f}%"
                    )
                    debug_lines.append("")
            debug_lines.append("")

            # =========================
            # regime_filters（デバッグ）
            # =========================
            debug_lines.append("[REGIME_FILTER_DEBUG]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                rf = ((rep.get("overall_summary") or {}).get("regime_filters")) or {}
                run_no = int(rr.get("run_no") or 0)
                if not isinstance(rf, dict):
                    debug_lines.append(f"run{run_no:02d}: (no regime_filters)")
                    continue
                debug_lines.append(
                    f"run{run_no:02d}: "
                    f"disable_morning_weak={bool(rf.get('disable_morning_weak', False))} "
                    f"disable_rising_ratio_lt50={bool(rf.get('disable_rising_ratio_lt50', False))} "
                    f"disable_topix_weak={bool(rf.get('disable_topix_weak', False))} "
                    f"skipped_signals={int(rf.get('skipped_signals_count') or 0)}"
                )
                src = rf.get("skip_reason_counts") or {}
                if isinstance(src, dict) and src:
                    debug_lines.append("  skip_reason_counts:")
                    for k in sorted(src.keys()):
                        debug_lines.append(f"    - {k}: {int(src.get(k) or 0)}")
                d2 = rf.get("diag") or {}
                if isinstance(d2, dict) and d2:
                    debug_lines.append("  [REGIME_FILTER_DIAG]")
                    debug_lines.append(f"  filter_name: {str(d2.get('filter_name') or '')}")
                    debug_lines.append(f"  checked_count: {int(d2.get('checked_count') or 0)}")
                    debug_lines.append(f"  skipped_count: {int(d2.get('skipped_count') or 0)}")
                    debug_lines.append(f"  passed_count: {int(d2.get('passed_count') or 0)}")
                    debug_lines.append(f"  skip_ratio: {float(d2.get('skip_ratio') or 0.0):.1%}")
                    ss = d2.get("sample_skipped") or []
                    if isinstance(ss, list) and ss:
                        debug_lines.append("  sample_skipped:")
                        for it in ss[:10]:
                            if not isinstance(it, dict):
                                continue
                            debug_lines.append(
                                "    - "
                                f"symbol={it.get('symbol')} time={it.get('time_jst')} "
                                f"market_regime={it.get('market_regime')} "
                                f"rising_ratio={it.get('rising_ratio')} topix_pct={it.get('topix_pct')} "
                                f"reason={it.get('reason')}"
                            )
            debug_lines.append("")

            debug_lines.append("[MARKET_REGIME_DISTRIBUTION]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                md = ((rep.get("meta") or {}).get("market_regime_distribution")) or {}
                if not isinstance(md, dict) or not md:
                    continue
                debug_lines.append(f"run{run_no:02d}: {', '.join([f'{k}={int(md.get(k) or 0)}' for k in sorted(md.keys())])}")
            debug_lines.append("")

            debug_lines.append("[RISING_RATIO_DISTRIBUTION]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                rd = ((rep.get("meta") or {}).get("rising_ratio_distribution")) or {}
                if not isinstance(rd, dict) or not rd:
                    continue
                debug_lines.append(
                    f"run{run_no:02d}: samples={int(rd.get('samples') or 0)} "
                    f"avg={float(rd.get('avg') or 0.0):.3f} "
                    f"min={rd.get('min')} max={rd.get('max')} "
                    f"lt50_ratio={float(rd.get('lt50_ratio') or 0.0):.1%} "
                    f"lt40={int(rd.get('lt40_count') or 0)} lt50={int(rd.get('lt50_count') or 0)} ge60={int(rd.get('ge60_count') or 0)}"
                )
            debug_lines.append("")

            # =========================
            # TIME_BUCKET_ANALYSIS（デバッグ: runごと）
            # =========================
            debug_lines.append("[TIME_BUCKET_ANALYSIS]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                tba = rep.get("time_bucket_analysis") or {}
                if not isinstance(tba, dict) or not tba:
                    continue
                debug_lines.append(f"run{run_no:02d}:")
                for b in [
                    "09:00-09:30",
                    "09:30-10:00",
                    "10:00-10:30",
                    "10:30-11:00",
                    "11:00-11:30",
                    "12:30-13:00",
                    "13:00-14:00",
                    "14:00-15:00",
                ]:
                    row = tba.get(b)
                    if not isinstance(row, dict):
                        continue
                    debug_lines.append(f"  bucket: {b}")
                    debug_lines.append(f"    signals: {int(row.get('signals') or 0)}")
                    debug_lines.append(f"    winrate: {float(row.get('winrate_pct') or 0.0):.1f}%")
                    debug_lines.append(f"    expectancy: {float(row.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f}")
                    debug_lines.append(f"    total_pnl: {float(row.get('total_pnl_yen_100_shares') or 0.0):+,.0f}")
                    debug_lines.append(f"    lose_worst10_sum: {float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f}")
                    debug_lines.append(f"    avg_hold_minutes: {float(row.get('avg_hold_minutes') or 0.0):.1f}")
                debug_lines.append("")
            debug_lines.append("")

            debug_lines.append("[MARKET_REGIME_ANALYSIS]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                mra = rep.get("market_regime_analysis") or {}
                if not isinstance(mra, dict) or not mra:
                    continue
                debug_lines.append(f"run{run_no:02d}:")
                for k in sorted(mra.keys()):
                    row = mra.get(k) or {}
                    if not isinstance(row, dict):
                        continue
                    debug_lines.append(
                        f"  {k}: signals={int(row.get('signals') or 0)} "
                        f"winrate={float(row.get('winrate_pct') or 0.0):.1f}% "
                        f"exp={float(row.get('avg_expectancy_yen_100_shares') or 0.0):+,.0f} "
                        f"pnl={float(row.get('total_pnl_yen_100_shares') or 0.0):+,.0f} "
                        f"lw10={float(row.get('lose_worst10_sum_yen_100_shares') or 0.0):+,.0f} "
                        f"max_dd={float(row.get('max_drawdown_yen_100_shares_est') or 0.0):+,.0f}"
                    )
                debug_lines.append("")
            debug_lines.append("")

            # =========================
            # REJECT理由ランキング（ユーザー要望）
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
            debug_lines.append("[REJECT_REASON_RANKING]")
            debug_lines.append("")
            for it in rej_rank[:30]:
                debug_lines.append(f"{it['reason']}: {int(it['count'])}")
            debug_lines.append("")

            # =========================
            # PIPELINE_DEBUG（ユーザー要望）
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
            debug_lines.append("[PIPELINE_DEBUG]")
            debug_lines.append("")
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
                    debug_lines.append(f"{k}={int(pd_tot.get(k) or 0)}")
            debug_lines.append("")
            debug_lines.append("continue_reason_counts:")
            for it in sorted(cr_tot.items(), key=lambda kv: int(kv[1]), reverse=True)[:30]:
                debug_lines.append(f"{it[0]}: {int(it[1])}")
            debug_lines.append("")

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
            debug_lines.append("[EVAL_FILTER / SIGNAL_STAGE]")
            debug_lines.append("")
            debug_lines.append("generated_signal_count=" + str(int(gen_total)))
            debug_lines.append("replay_signals_count=" + str(int(rep_total)))
            debug_lines.append("eval_signals_count=" + str(int(eval_total)))
            debug_lines.append("")

            # =========================
            # MARKET_DEBUG
            # =========================
            debug_lines.append("[MARKET_DEBUG]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                md = rep.get("market_debug") or {}
                rows = md.get("rows") or []
                rows_total = int(md.get("rows_total") or 0)
                truncated = bool(md.get("truncated", False))
                if not isinstance(rows, list) or not rows:
                    debug_lines.append(f"run{run_no:02d}: (no market_debug rows)")
                    debug_lines.append("")
                    continue
                debug_lines.append(f"run{run_no:02d}: market_debug_rows={rows_total}{' (truncated)' if truncated else ''}")
                debug_lines.append("")
                for r2 in rows:
                    if not isinstance(r2, dict):
                        continue
                    debug_lines.append(str(r2.get("timestamp_jst") or ""))
                    debug_lines.append(f"symbol={str(r2.get('symbol') or '')}")
                    debug_lines.append("")
                    debug_lines.append(f"topix_fetch_ok={bool(r2.get('topix_fetch_ok', False))}")
                    tr = r2.get("topix_raw")
                    if isinstance(tr, (int, float)):
                        debug_lines.append(f"topix_raw={float(tr):.2f}")
                    else:
                        debug_lines.append("topix_raw=N/A")
                    pc2 = r2.get("topix_prev_close")
                    if isinstance(pc2, (int, float)):
                        debug_lines.append(f"topix_prev_close={float(pc2):.2f}")
                    else:
                        debug_lines.append("topix_prev_close=N/A")
                    tp = r2.get("topix_pct")
                    if isinstance(tp, (int, float)):
                        debug_lines.append(f"topix_pct={float(tp):+.2f}")
                    else:
                        debug_lines.append("topix_pct=N/A")
                    debug_lines.append(f"market_state={str(r2.get('market_state') or '')}")
                    debug_lines.append(f"entry_allowed={bool(r2.get('entry_allowed', True))}")
                    br = r2.get("blocked_reason") or []
                    if isinstance(br, list):
                        br_s = ",".join([str(x) for x in br if str(x).strip()])
                    else:
                        br_s = str(br)
                    debug_lines.append(f"blocked_reason=[{br_s}]")
                    debug_lines.append("")
                debug_lines.append("")
                continue

            # =========================
            # CROSSED_DEBUG（ユーザー要望）
            # =========================
            debug_lines.append("[CROSSED_DEBUG]")
            debug_lines.append("")
            for rr in run_summaries:
                rep = rr.get("report") or {}
                run_no = int(rr.get("run_no") or 0)
                cd = rep.get("crossed_debug") or {}
                rows = cd.get("rows") or []
                rows_total = int(cd.get("rows_total") or 0)
                truncated = bool(cd.get("truncated", False))
                if not isinstance(rows, list) or not rows:
                    debug_lines.append(f"run{run_no:02d}: (no crossed_debug rows)")
                    debug_lines.append("")
                    continue
                debug_lines.append(f"run{run_no:02d}: crossed_debug_rows={rows_total}{' (truncated)' if truncated else ''}")
                debug_lines.append("")
                for r2 in rows:
                    if not isinstance(r2, dict):
                        continue
                    debug_lines.append(f"symbol={str(r2.get('symbol') or '')}")
                    debug_lines.append(f"time={str(r2.get('time_jst') or '')}")
                    p = r2.get("price")
                    h5 = r2.get("high_5m")
                    cr = bool(r2.get("crossed", False))
                    df = r2.get("diff_pct")
                    if isinstance(p, (int, float)):
                        debug_lines.append(f"price={float(p):.2f}")
                    else:
                        debug_lines.append("price=N/A")
                    if isinstance(h5, (int, float)):
                        debug_lines.append(f"high_5m={float(h5):.2f}")
                    else:
                        debug_lines.append("high_5m=N/A")
                    debug_lines.append(f"crossed={cr}")
                    if isinstance(df, (int, float)):
                        debug_lines.append(f"diff={float(df):+.2f}%")
                    else:
                        debug_lines.append("diff=N/A")
                    debug_lines.append("")
                debug_lines.append("")

                # (legacy) signals[] ベースの出力（互換用）
                sigs = rep.get("signals") or []
                if not isinstance(sigs, list) or not sigs:
                    debug_lines.append(f"run{run_no:02d}: (no signals)")
                    debug_lines.append("")
                    continue
                for s2 in sigs:
                    if not isinstance(s2, dict):
                        continue
                    debug_lines.append(f"run{run_no:02d} {str(s2.get('symbol') or '')} {str(s2.get('signal_time_jst') or '')}")
                    debug_lines.append(f"topix_fetch_ok={bool(s2.get('topix_fetch_ok', False))}")
                    tr = s2.get("topix_raw")
                    if isinstance(tr, (int, float)):
                        debug_lines.append(f"topix_raw={float(tr):.2f}")
                    else:
                        debug_lines.append("topix_raw=N/A")
                    tp = s2.get("topix_pct")
                    if isinstance(tp, (int, float)):
                        debug_lines.append(f"topix_pct={float(tp):+.2f}")
                    else:
                        debug_lines.append("topix_pct=N/A")
                    debug_lines.append(f"market_state={str(s2.get('market_state') or s2.get('market_regime') or '')}")
                    debug_lines.append(f"entry_allowed={bool(s2.get('entry_allowed', True))}")
                    br2 = str(s2.get('blocked_reason') or "")
                    debug_lines.append(f"blocked_reason=[{br2}]")
                    debug_lines.append("")

            txt_path = os.path.join(results_dir, f"{name_base}.txt")
            debug_txt_path = os.path.join(results_dir, f"{name_base}_debug.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            with open(debug_txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(debug_lines) + "\n")

            print("\n".join(lines))
            print(f"[{now_str()}] 合算サマリーを保存しました: {txt_path}")
            print(f"[{now_str()}] デバッグ詳細を保存しました: {debug_txt_path}")
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


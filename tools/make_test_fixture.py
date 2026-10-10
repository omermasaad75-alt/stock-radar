#!/usr/bin/env python3
"""Synthetic QA data ONLY. Writes JSON to stdout, never to production data.json.

The two assets exercise positive and negative divergences with standard CMF
computed from controlled OHLCV. This is not a historical backtest or market data.
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from radar.analytics import attach_analytics
from scanner import build_payload, clean


def scenario(direction):
    n = 64
    i = np.arange(n)
    sign = 1 if direction == "positive" else -1
    low = 10 - sign * .012 * i + .45 * np.sin(i * np.pi / 5)
    location = .07 + (.86 * i / (n - 1)) if sign == 1 else .93 - .86 * i / (n - 1)
    df = pd.DataFrame({"Open": low + .5, "High": low + 1, "Low": low,
                       "Close": low + location, "Volume": 100000},
                      index=pd.bdate_range(end="2026-10-08", periods=n))
    h4 = df.copy()
    h4.index = pd.date_range("2026-09-24T13:30:00Z", periods=n, freq="4h")
    signal = {"ticker": "DEMO-POS" if sign == 1 else "DEMO-NEG",
              "company": "Synthetic positive flow" if sign == 1 else "Synthetic negative flow",
              "price": float(df['Close'].iloc[-1]), "support": float(df['Low'].tail(10).min()),
              "stage": "قيد المتابعة", "readiness_score": 45, "support_hold_sessions": 6,
              "split": {"date": "2026-08-31", "ratio": "1:10", "days_since": 40},
              "liquidity_sweep": False, "indicators": {}, "checklist": [], "max_drawdown_pct": 45,
              "targets": [{"level": 12, "label": "مقاومة تعليمية", "final": False}]}
    result = attach_analytics(signal, df, h4)
    assert result['liquidity']['divergence']['type'] == direction, result['liquidity']['divergence']
    return result


if __name__ == '__main__':
    output = build_payload([scenario("positive"), scenario("negative")],
                           {"symbols_total": 2, "split_window_pass": 2, "rise_ok_pass": 2,
                            "drop_rsi_pass": 2, "data_coverage_pct": 100},
                           dt.datetime.now(dt.timezone.utc).isoformat())
    output['demo'] = True
    output['analysis']['note'] = 'Synthetic QA data only; no real securities or prices.'
    print(json.dumps(clean(output), ensure_ascii=False, allow_nan=False))

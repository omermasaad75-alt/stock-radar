"""Hand-checked indicator math and explicitly synthetic divergence cases."""
import json
import unittest
from unittest.mock import patch

import scanner
from pathlib import Path

import numpy as np
import pandas as pd

from radar.analytics import (
    adl_series, anchored_vwap, attach_analytics, cmf_series, divergence,
    frame_from_bars, indicator_frame, mfi_series, mfm_series, obv_series,
    pivots, risk_plan, rsi, volume_series,
)
from scanner import build_payload, clean


def frame(n=40, volume=100):
    return pd.DataFrame({"Open": 11., "High": 12., "Low": 10., "Close": 11.5, "Volume": volume},
                        index=pd.date_range("2026-01-01", periods=n))


def pair_bars(direction):
    """Exported-bar fixture, NOT market data. Tests comparison logic in isolation."""
    bars = [{"date": f"2026-01-{i + 1:02}", "low": 10.0, "high": 12.0, "close": 11.0,
             "volume": 100, "cmf": 0.0, "mfi": 40.0, "obv": 0.0} for i in range(30)]
    if direction == "positive":
        bars[20].update(low=9, cmf=-.3, mfi=22, obv=-500)
        bars[26].update(low=8.7, cmf=-.1, mfi=40, obv=-200)
    else:
        bars[20].update(high=13, cmf=.3, mfi=70, obv=500)
        bars[26].update(high=13.4, cmf=.1, mfi=50, obv=200)
    bars[-1]["cmf"] = -.05 if direction == "positive" else .05
    return bars


class IndicatorTests(unittest.TestCase):
    def test_cmf_uses_standard_clv_not_body_or_momentum(self):
        df = frame()
        self.assertTrue((mfm_series(df) == .5).all())
        cmf = cmf_series(df)
        self.assertTrue(cmf.iloc[:19].isna().all())
        self.assertAlmostEqual(cmf.iloc[19], .5)
        altered = df.copy()
        altered["Open"] = 10.2
        pd.testing.assert_series_equal(cmf, cmf_series(altered))

    def test_cmf_actual_volume_weighting(self):
        df = frame(20)
        df.loc[df.index[-1], ["Close", "Volume"]] = [10, 1900]
        # 19 * (+.5 * 100) + (-1 * 1900), divided by 19*100 + 1900.
        self.assertAlmostEqual(cmf_series(df).iloc[-1], -.25)

    def test_flat_bar_multiplier_is_zero(self):
        df = frame(22)
        df[["Open", "High", "Low", "Close"]] = 10
        self.assertTrue((mfm_series(df) == 0).all())
        self.assertEqual(cmf_series(df).iloc[-1], 0)
        self.assertEqual(mfi_series(df).iloc[-1], 50)

    def test_all_zero_volume_not_a_measurable_cmf(self):
        df = frame(volume=0)
        self.assertTrue(cmf_series(df).isna().all())
        self.assertTrue(anchored_vwap(df).isna().all())
        self.assertTrue((volume_series(df) == 0).all())

    def test_no_synthetic_volume_on_missing_or_partial_data(self):
        df = frame().drop(columns="Volume")
        self.assertTrue(volume_series(df).isna().all())
        self.assertTrue(cmf_series(df).isna().all())
        df["Volume"] = 100
        df.loc[df.index[-3], "Volume"] = np.nan
        self.assertTrue(cmf_series(df).iloc[-3:].isna().all())
        self.assertTrue(adl_series(df).iloc[-3:].isna().all())
        self.assertTrue(obv_series(df).iloc[-3:].isna().all())

    def test_zero_volume_is_preserved(self):
        df = frame()
        df.loc[df.index[-1], "Volume"] = 0
        self.assertEqual(volume_series(df).iloc[-1], 0)
        self.assertAlmostEqual(cmf_series(df).iloc[-1], .5)

    def test_mfi_positive_and_negative_flow(self):
        df = frame(20)
        p = np.arange(20) + 10.
        for c in ("Open", "High", "Low", "Close"):
            df[c] = p
        self.assertTrue(mfi_series(df).iloc[:14].isna().all())
        self.assertEqual(mfi_series(df).iloc[14], 100)
        for c in ("Open", "High", "Low", "Close"):
            df[c] = p[::-1]
        self.assertEqual(mfi_series(df).iloc[-1], 0)

    def test_mfi_hand_calculation(self):
        df = frame(3)
        for c in ("Open", "High", "Low", "Close"):
            df[c] = [10, 12, 11]
        # Positive money 1200; negative money 1100.
        self.assertAlmostEqual(mfi_series(df, 2).iloc[-1], 100 * 1200 / 2300)

    def test_obv_and_adl_have_consistent_origins(self):
        df = frame(4)
        df["Close"] = [11, 12, 11, 11.5]
        df["Volume"] = [100, 200, 300, 400]
        self.assertEqual(obv_series(df).tolist(), [0, 200, -100, 300])
        self.assertEqual(adl_series(df).tolist(), [0, 200, 200, 400])

    def test_anchored_vwap_is_not_scaled_to_headline(self):
        df = frame(4)
        df["Close"] = [10, 11, 12, 11.5]
        df["Volume"] = [100, 200, 300, 400]
        vw = anchored_vwap(df, df.index[2])
        self.assertTrue(vw.iloc[:2].isna().all())
        self.assertAlmostEqual(vw.iloc[-1], ((12+10+12)/3*300 + (12+10+11.5)/3*400) / 700)

    def test_indicators_do_not_backfill_from_future(self):
        df = frame(70)
        df["Close"] = 11 + np.sin(np.arange(70) / 3) * .5
        full = indicator_frame(df)
        prefix = indicator_frame(df.iloc[:30])
        pd.testing.assert_frame_equal(full.iloc[:30], prefix)
        self.assertTrue(rsi(df["Close"]).iloc[:14].isna().all())


class DivergenceTests(unittest.TestCase):
    def test_positive_lower_low_higher_money_flow(self):
        bars = pair_bars("positive")
        d = divergence(bars)
        self.assertEqual(d["type"], "positive")
        self.assertEqual((d["p1_idx"], d["p2_idx"]), (20, 26))
        self.assertLess(d["price_change_pct"], 0)
        self.assertGreater(d["cmf_delta"], 0)
        self.assertEqual(d["p1_cmf"], bars[20]["cmf"])
        self.assertEqual(d["p2_price"], bars[26]["low"])
        self.assertIn("CMF(20)", d["indicators_involved"])

    def test_negative_higher_high_lower_money_flow(self):
        bars = pair_bars("negative")
        d = divergence(bars)
        self.assertEqual(d["type"], "negative")
        self.assertEqual(d["p2_price"], bars[26]["high"])
        self.assertGreater(d["price_change_pct"], 0)
        self.assertLess(d["cmf_delta"], 0)

    def test_parallel_falling_price_and_money_is_not_negative_divergence(self):
        bars = pair_bars("negative")
        for i, b in enumerate(bars):
            b.update(low=10-i*.1, high=12-i*.1, close=11-i*.1, cmf=.4-i*.02)
        self.assertEqual(divergence(bars)["type"], "none")

    def test_unknown_volume_is_not_no_divergence(self):
        bars = pair_bars("positive")
        for b in bars:
            b.update(volume=None, cmf=None, mfi=None, obv=None)
        self.assertEqual(divergence(bars)["type"], "unavailable")
        self.assertIsNone(divergence(bars)["p1_idx"])

    def test_last_two_bars_are_not_confirmed_pivots(self):
        a = np.array([10.] * 30)
        a[28] = 7
        self.assertNotIn(28, pivots(a))

    def test_mfi_only_evidence_is_labeled_mfi_only(self):
        bars = pair_bars("positive")
        bars[20].update(cmf=0, obv=0)
        bars[26].update(cmf=0, obv=0)
        d = divergence(bars)
        self.assertEqual(d["direction"], "positive")
        self.assertEqual(d["indicators_involved"], ["MFI(14)"])

    def test_dormant_stealth_accumulation_is_separate(self):
        bars = pair_bars("positive")
        for i, b in enumerate(bars):
            b.update(low=10, high=10.4, close=10.2, cmf=-.1+i*.01, mfi=40+i)
        d = divergence(bars, support=10)
        self.assertEqual(d["type"], "stealth_accumulation")
        self.assertEqual(d["direction"], "positive")
        self.assertEqual((d["p1_idx"], d["p2_idx"]), (22, 29))


class IntegrationTests(unittest.TestCase):
    def signal(self):
        return {"ticker": "TEST", "stage": "شبه جاهز", "price": 11.5, "support": 10.9,
                "readiness_score": 70, "support_hold_sessions": 8,
                "split": {"date": "2026-01-01"}, "liquidity_sweep": False,
                "targets": [{"level": 12.5, "label": "مقاومة", "final": False}],
                "indicators": {"rsi": 48, "vwap": 11.2}}

    def test_snapshot_preserves_original_prices_and_stage(self):
        signal = self.signal()
        out = attach_analytics(signal, frame().drop(columns="Volume"), snapshot=True)
        self.assertEqual(out["stage"], signal["stage"])
        self.assertEqual(out["readiness_score"], 70)
        self.assertEqual(out["indicators"]["rsi"], 48)
        self.assertIsNone(out["liquidity"]["cmf"])
        self.assertIsNone(out["liquidity"]["cmf_4h"])
        self.assertTrue(out["grade_provisional"])
        self.assertEqual(out["confirmation_available_weight"], 25)
        self.assertIsNone(out["confirmations"]["cmf_accum"])
        self.assertIsNone(out["chart"][-1]["volume"])
        self.assertFalse(signal.get("liquidity"))

    def test_four_hour_uses_its_own_observed_candles(self):
        daily, h4 = frame(), frame(volume=400)
        h4["Close"] = 10.5
        out = attach_analytics(self.signal(), daily, h4)
        self.assertAlmostEqual(out["liquidity"]["cmf"], .5)
        self.assertAlmostEqual(out["liquidity"]["cmf_4h"], -.5)
        self.assertEqual(len(out["chart_4h"]), 40)
        self.assertFalse(out["grade_provisional"])

    def test_short_four_hour_history_is_not_renamed_cmf20(self):
        out = attach_analytics(self.signal(), frame(), frame(10))
        self.assertIsNone(out["liquidity"]["cmf_4h"])
        self.assertIsNone(out["confirmations"]["mtf_4h"])

    def test_risk_sizing_never_exceeds_equity(self):
        s = {**self.signal(), "support": 11.734693}
        r = risk_plan(s)
        self.assertTrue(r["valid"])
        self.assertLessEqual(r["position_value"], 10000)
        self.assertLessEqual(r["risk_amount"], 100)
        s["support"] = 12
        r = risk_plan(s)
        self.assertFalse(r["valid"])
        self.assertIsNone(r["shares"])
        self.assertIsNone(r["targets"][0]["rr"])

    def test_empty_payload_and_absent_diagnostics_not_fabricated(self):
        p = build_payload([], {})
        self.assertEqual(p["count"], 0)
        self.assertIsNone(p["stats"]["avg_cmf"])
        self.assertEqual(p["diagnostics"], {})
        self.assertNotIn("items", p)  # no second full copy of each chart

    def test_serialization_has_no_nan_or_infinity(self):
        out = attach_analytics(self.signal(), frame().drop(columns="Volume"), snapshot=True)
        encoded = json.dumps(clean(out), allow_nan=False)
        self.assertNotIn('NaN', encoded)
        again = attach_analytics(out, frame_from_bars(out["chart"]), snapshot=True)
        self.assertEqual(clean(out["liquidity"]), clean(again["liquidity"]))

    def test_live_build_signal_exports_observed_daily_and_four_hour_volume(self):
        df, h4 = frame(), frame(volume=400)
        h4["Close"] = 10.5
        flags = {key: False for key, _, _ in scanner.CK}
        flags.update(drop=True, rsi=True, hold5=True, res=True)
        info = {"f": flags, "S": 10.9, "S2": 10.9, "H": 12, "note": {}, "last": 11.5,
                "top": 14, "low_all": 10, "F": 14, "F_date": "2026-01-02", "c0": 12,
                "chg": -4, "dd": 35, "rsi_min": 25, "rsi_now": 50, "hold": 6,
                "pattern": "دعم رئيسي", "hold_after": None, "run_pct": 5,
                "peak_run": 15, "ext_neck": -4, "brk_age": 0, "ema": (12, 13, 14),
                "vwap": 11.2, "r20": False, "rv": False}
        with patch('scanner.yf.Ticker', return_value=object()), \
             patch('scanner.fundamentals', return_value={"company": "Synthetic test"}), \
             patch('scanner.news_block', return_value=([], [])), \
             patch('scanner.tf4h', return_value=h4):
            signal, score = scanner.build_signal('TEST', df, df.index[0], '1:10', info, df.index[-1].date())
        self.assertEqual(score, 55)
        self.assertEqual(signal['chart'][-1]['volume'], 100)
        self.assertEqual(signal['chart_4h'][-1]['volume'], 400)
        self.assertAlmostEqual(signal['liquidity']['cmf'], .5)
        self.assertAlmostEqual(signal['liquidity']['cmf_4h'], -.5)

    def test_four_hour_resampling_starts_at_new_york_session_open(self):
        hourly = frame(7)
        hourly.index = pd.date_range('2026-01-05 09:30', periods=7, freq='h', tz='America/New_York')
        with patch('scanner.yf.download', return_value=hourly):
            four = scanner.tf4h('TEST')
        self.assertEqual([(t.hour, t.minute) for t in four.index], [(9, 30), (13, 30)])
        self.assertEqual(four['Volume'].tolist(), [400, 300])

    def test_resampling_does_not_turn_missing_hourly_volume_into_zero(self):
        hourly = frame(7)
        hourly.index = pd.date_range('2026-01-05 09:30', periods=7, freq='h', tz='America/New_York')
        hourly.loc[hourly.index[1], 'Volume'] = np.nan
        with patch('scanner.yf.download', return_value=hourly):
            four = scanner.tf4h('TEST')
        self.assertEqual(len(four), 2)
        self.assertTrue(pd.isna(four['Volume'].iloc[0]))
        self.assertEqual(four['Volume'].iloc[-1], 300)

    def test_no_network_does_not_replace_saved_data(self):
        with patch('scanner.universe', return_value=[]), patch('scanner.save_payload') as save:
            with self.assertRaisesRegex(RuntimeError, 'previous data.json preserved'):
                scanner.main()
            save.assert_not_called()

    def test_saved_docs_and_root_data_are_identical_and_honest(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / 'data.json').read_bytes(), (root / 'docs/data.json').read_bytes())
        raw = json.loads((root / 'data.json').read_text())
        for s in raw["signals"]:
            if (s.get('liquidity') or {}).get('data_quality', {}).get('volume_source') == 'missing':
                self.assertIsNone(s['liquidity']['cmf'])
                self.assertIsNone(s['liquidity']['mfi'])
                self.assertEqual(s['liquidity']['divergence']['type'], 'unavailable')


if __name__ == "__main__":
    unittest.main()

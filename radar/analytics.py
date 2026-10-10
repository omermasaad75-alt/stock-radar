"""Price, volume and dormant-base analytics. No network calls or inferred volume.

CMF is the standard Chaikin formula, NOT a blend of CMF periods or candle bodies.
Missing observations stay missing; only test fixtures may use synthetic OHLCV.
All divergence indices refer to the exported chart, and all endpoints are the
actual indicator values at those bars. These rules are screening heuristics,
not evidence of institutional buying or a backtested trading recommendation.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CMF_PERIOD, CMF_FAST, MFI_PERIOD = 20, 10, 14
CHART_BARS, FOUR_HOUR_BARS = 90, 96
CONFIRM_DEFS = [
    ("cmf_accum", "CMF(20) موجب ويتحسن أو CMF(10) يؤكد التدفق", 25),
    ("liq_divergence", "انحراف إيجابي للسيولة أو تجميع خفي قرب الدعم", 25),
    ("mfi_flow", "تعافي MFI(14) مع تفوق حجم الجلسات الصاعدة", 15),
    ("dormant_tight", "قاعدة خاملـة ضيقة مع ثبات الدعم", 15),
    ("liquidity_sweep", "سحب سيولة تحت الدعم وإغلاق فوقه", 10),
    ("mtf_4h", "RSI وCMF يؤكدان التحسن على 4 ساعات", 10),
]
STATE_LABELS = {"READY": "جاهز فنيًا", "SEMI": "شبه جاهز", "WATCH": "قيد المتابعة", "MOVED": "انطلق بالفعل"}
STATE_CODES = {v: k for k, v in STATE_LABELS.items()}


def value(x, digits=4):
    """JSON-safe scalar. In particular, NaN must never become a displayed zero."""
    if x is None or not np.isfinite(float(x)):
        return None
    return round(float(x), digits)


def last(series, digits=4):
    return value(series.iloc[-1], digits) if len(series) else None


def slope(series, look=8):
    s = pd.Series(series, dtype=float).tail(look)
    # A missing point is not a zero, nor may gaps be collapsed in time.
    if len(s) < 3 or s.isna().any():
        return None
    return value(np.polyfit(np.arange(len(s)), s.to_numpy(), 1)[0], 6)


def change(series, look=5):
    return value(series.iloc[-1] - series.iloc[-look - 1], 6) if len(series) > look else None


def frame_from_bars(bars):
    rows = [{k.title(): b.get(k) for k in ("open", "high", "low", "close", "volume")} for b in bars]
    dates = pd.to_datetime([b.get("date") or b.get("time") for b in bars], utc=True)
    df = pd.DataFrame(rows, index=dates)
    return normalize_frame(df)


def normalize_frame(df):
    """Keep observed OHLCV only. Zero volume is valid; negative volume is not."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    for c in ("Open", "High", "Low", "Close", "Volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce") if c in out else np.nan
    out = out.replace([np.inf, -np.inf], np.nan)
    out.loc[out["Volume"] < 0, "Volume"] = np.nan
    out = out[~out.index.duplicated(keep="last")].sort_index()
    good = (out["High"] >= out["Low"]) & (out["Low"] > 0)
    good &= out["High"] >= out[["Open", "Close"]].max(axis=1)
    good &= out["Low"] <= out[["Open", "Close"]].min(axis=1)
    return out.loc[good].dropna(subset=["Open", "High", "Low", "Close"])


def volume_series(df):
    if "Volume" not in df:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df["Volume"], errors="coerce").where(lambda v: (v >= 0) & np.isfinite(v))


def rsi(close, period=14):
    diff = close.astype(float).diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    result = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    result = result.mask((loss == 0) & (gain > 0), 100)
    return result.mask((loss == 0) & (gain == 0), 50)


def ema(close, period):
    return close.astype(float).ewm(span=period, adjust=False).mean()


def mfm_series(df):
    """Standard close location value: (2C-H-L)/(H-L). Flat bars contribute 0."""
    spread = df["High"] - df["Low"]
    return ((2 * df["Close"] - df["High"] - df["Low"]) / spread.replace(0, np.nan)).mask(spread == 0, 0).clip(-1, 1)


def cmf_series(df, period=CMF_PERIOD, vol=None):
    v = volume_series(df) if vol is None else vol.reindex(df.index)
    numerator = (mfm_series(df) * v).rolling(period, min_periods=period).sum()
    denominator = v.rolling(period, min_periods=period).sum().replace(0, np.nan)
    return numerator / denominator


def mfi_series(df, period=MFI_PERIOD, vol=None):
    v = volume_series(df) if vol is None else vol.reindex(df.index)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    delta = tp.diff()
    raw = tp * v
    valid = raw.notna() & delta.notna()
    positive = raw.where(delta > 0, 0).where(valid)
    negative = raw.where(delta < 0, 0).where(valid)
    p = positive.rolling(period, min_periods=period).sum()
    n = negative.rolling(period, min_periods=period).sum()
    result = 100 - 100 / (1 + p / n.replace(0, np.nan))
    result = result.mask((n == 0) & (p > 0), 100)
    return result.mask((n == 0) & (p == 0), 50)


def obv_series(df, vol=None):
    v = volume_series(df) if vol is None else vol.reindex(df.index)
    direction = np.sign(df["Close"].diff()).fillna(0)
    # The origin is 0, but no cumulative line is knowable after missing volume.
    return (direction * v).cumsum(skipna=False)


def adl_series(df, vol=None):
    v = volume_series(df) if vol is None else vol.reindex(df.index)
    return (mfm_series(df) * v).cumsum(skipna=False)


def anchored_vwap(df, anchor=None):
    v = volume_series(df)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    if anchor is not None:
        at = pd.Timestamp(anchor)
        if df.index.tz is not None and at.tz is None:
            at = at.tz_localize(df.index.tz)
        elif df.index.tz is None and at.tz is not None:
            at = at.tz_localize(None)
        mask = df.index >= at
    else:
        mask = np.ones(len(df), dtype=bool)
    result = pd.Series(np.nan, index=df.index, dtype=float)
    weighted = (tp[mask] * v[mask]).cumsum(skipna=False)
    result.loc[mask] = weighted / v[mask].cumsum(skipna=False).replace(0, np.nan)
    return result


def indicator_frame(df, anchor=None):
    v = volume_series(df)
    out = pd.DataFrame(index=df.index)
    out["rsi"] = rsi(df["Close"])
    for period in (20, 30, 50):
        out[f"ema{period}"] = ema(df["Close"], period)
    out["cmf"] = cmf_series(df, 20, v)
    out["cmf10"] = cmf_series(df, 10, v)
    out["mfi"] = mfi_series(df, 14, v)
    out["clv"] = mfm_series(df)
    out["mfv"] = out["clv"] * v
    out["obv"] = obv_series(df, v)
    out["adl"] = adl_series(df, v)
    out["vwap"] = anchored_vwap(df, anchor)
    out["macd"] = ema(df["Close"], 12) - ema(df["Close"], 26)
    out["macd_signal"] = ema(out["macd"], 9)
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    previous = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - previous).abs(), (df["Low"] - previous).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return out


def chart_bars(df, indicators, limit=CHART_BARS, intraday=False):
    bars = []
    for idx, row in df.tail(limit).iterrows():
        stamp = pd.Timestamp(idx).isoformat() if intraday else str(pd.Timestamp(idx).date())
        bar = {"date": stamp, "time": stamp}
        for c in ("Open", "High", "Low", "Close", "Volume"):
            bar[c.lower()] = value(row[c], 4 if c != "Volume" else 0)
        for key in indicators.columns:
            bar[key] = value(indicators.loc[idx, key], 6)
        bar["cmf_10"] = bar["cmf10"]
        bar["mfm"] = bar["clv"]
        bar["macd_line"] = bar["macd"]
        bars.append(bar)
    return bars


def pivots(values, mode="low", radius=2, min_sep=4):
    """Confirmed local extrema only: last `radius` candles cannot be pivots yet."""
    a = np.asarray(values, dtype=float)
    found = []
    for i in range(radius, len(a) - radius):
        window = a[i - radius:i + radius + 1]
        extreme = np.min(window) if mode == "low" else np.max(window)
        if not np.isfinite(a[i]) or a[i] != extreme or np.all(window == a[i]):
            continue
        if found and i - found[-1] < min_sep:
            better = a[i] < a[found[-1]] if mode == "low" else a[i] > a[found[-1]]
            if better:
                found[-1] = i
        else:
            found.append(i)
    return found


def divergence(bars, support=None):
    available = bool(bars and bars[-1].get("cmf") is not None)
    empty = {
        "type": "none" if available else "unavailable", "direction": None,
        "direction_ar": "لا انحراف مؤكد" if available else "بانتظار حجم التداول",
        "badge": "محايد" if available else "غير متاح", "strength": None,
        "p1_idx": None, "p2_idx": None, "p1_date": None, "p2_date": None,
        "p1_price": None, "p2_price": None, "price_change_pct": None,
        "p1_cmf": None, "p2_cmf": None, "cmf_delta": None,
        "p1_mfi": None, "p2_mfi": None, "mfi_delta": None,
        "p1_obv": None, "p2_obv": None, "obv_delta": None,
        "indicators_involved": [], "summary": "لا توجد مقارنة مستوفية للشروط بين قمتين أو قاعين مؤكدين.",
        "detail": "الانحراف إشارة رصد لا يؤكد الانعكاس؛ يُراجع الدعم والسعر قبل أي قرار.",
        "events": [],
    }
    if not available:
        empty["summary"] = "لا يمكن حساب الانحراف دون أحجام فعلية و20 شمعة صالحة لمؤشر CMF."
        empty["detail"] = "غياب الحجم ليس سيولة صفرية، ولا يعني غياب التجميع. انتظر مسح Yahoo Finance الجديد."
        return empty

    def event(kind, a, b, price_key, involved):
        p, q = bars[a], bars[b]
        direction = "negative" if kind == "negative" else "positive"
        title = {"positive": "انحراف إيجابي للسيولة", "negative": "انحراف سلبي للسيولة", "stealth_accumulation": "تجميع خفي في قاعدة خاملة"}[kind]
        d = {
            **empty, "type": kind, "direction": direction, "direction_ar": title,
            "badge": "إيجابي" if direction == "positive" else "سلبي",
            "strength": "قوي" if len(involved) >= 2 else "متوسط",
            "p1_idx": a, "p2_idx": b, "p1_date": p["date"], "p2_date": q["date"],
            "p1_price": p[price_key], "p2_price": q[price_key], "price_key": price_key,
            "price_change_pct": value((q[price_key] / p[price_key] - 1) * 100, 2),
            "indicators_involved": involved, "events": [],
        }
        for key in ("cmf", "mfi", "obv"):
            d[f"p1_{key}"] = p.get(key)
            d[f"p2_{key}"] = q.get(key)
            d[f"{key}_delta"] = value(q[key] - p[key], 6) if q.get(key) is not None and p.get(key) is not None else None
        desc = "قاع أدنى / إعادة اختبار قاع مع تحسن المؤشرات" if direction == "positive" else "قمة أعلى / مزدوجة مع تراجع المؤشرات"
        if kind == "stealth_accumulation":
            desc = "سعر شبه ثابت قرب الدعم مقابل تحسن CMF فوق الصفر"
        d["summary"] = f"{desc} · {', '.join(involved)}."
        delta_text = f"ΔCMF {d['cmf_delta']:+.3f}" if d["cmf_delta"] is not None else "CMF غير متاح عند إحدى النقطتين"
        d["detail"] = f"المقارنة من {p['date']} إلى {q['date']}: السعر {d['price_change_pct']:+.2f}%، {delta_text}. قوة الوصف تعني توافق المؤشرات لا احتمال ربح."
        return d

    candidates = []
    start = max(0, len(bars) - 40)
    for direction, price_key in (("positive", "low"), ("negative", "high")):
        pts = [start + i for i in pivots([b[price_key] for b in bars[start:]], "low" if direction == "positive" else "high")]
        for a, b in zip(pts[-4:], pts[-4:][1:]):
            if a >= b or b - a < 4 or len(bars) - 1 - b > 12:
                continue
            p, q = bars[a], bars[b]
            if p.get("cmf") is None or q.get("cmf") is None:
                continue
            price_change = q[price_key] / p[price_key] - 1
            if (direction == "positive" and price_change > .015) or (direction == "negative" and price_change < -.015):
                continue
            sign = 1 if direction == "positive" else -1
            involved = []
            if sign * (q["cmf"] - p["cmf"]) >= .04:
                involved.append("CMF(20)")
            if p.get("mfi") is not None and q.get("mfi") is not None and sign * (q["mfi"] - p["mfi"]) >= 5:
                involved.append("MFI(14)")
            v = [x["volume"] for x in bars[a:b + 1] if x.get("volume") is not None]
            if v and p.get("obv") is not None and q.get("obv") is not None and sign * (q["obv"] - p["obv"]) >= .5 * np.mean(v):
                involved.append("OBV")
            if involved:
                candidates.append(event(direction, a, b, price_key, involved))
    # A dormant-flow comparison is separate from a classical pivot divergence.
    if len(bars) >= 8 and support and support > 0:
        a, b = len(bars) - 8, len(bars) - 1
        p, q = bars[a], bars[b]
        recent = bars[a:]
        price_range = max(x["high"] for x in recent) / min(x["low"] for x in recent) - 1
        near_support = 0 <= q["close"] / support - 1 <= .12
        flat = abs(q["close"] / p["close"] - 1) <= .03 and price_range <= .12
        if near_support and flat and p.get("cmf") is not None and q["cmf"] > 0 and q["cmf"] - p["cmf"] >= .06:
            involved = ["CMF(20)"]
            if p.get("mfi") is not None and q.get("mfi") is not None and q["mfi"] - p["mfi"] >= 5:
                involved.append("MFI(14)")
            candidates.append(event("stealth_accumulation", a, b, "close", involved))
    if not candidates:
        return empty
    candidates.sort(key=lambda e: (e["p2_idx"], len(e["indicators_involved"])), reverse=True)
    chosen = dict(candidates[0])
    chosen["events"] = candidates[:4]
    return chosen


def dormant_base(df, support=None, hold_sessions=0):
    if df.empty:
        return {"is_dormant": False, "dormancy_score": None, "dormancy_label": "لا توجد شموع", "volume_dryup_ratio": None}
    recent = df.tail(10)
    earlier = df.iloc[max(0, len(df) - 30):max(0, len(df) - 10)]
    ref = support if support and support > 0 else float(recent["Low"].min())
    lo, hi = float(recent["Low"].min()), float(recent["High"].max())
    range_pct = (hi / lo - 1) * 100
    daily_range = float(((recent["High"] - recent["Low"]) / recent["Close"]).mean() * 100)
    prior_range = float(((earlier["High"] - earlier["Low"]) / earlier["Close"]).mean() * 100) if len(earlier) else None
    compression = (1 - daily_range / prior_range) * 100 if prior_range and prior_range > 0 else None
    distance = (float(df["Close"].iloc[-1]) / ref - 1) * 100
    v, pv = volume_series(recent), volume_series(earlier)
    dryup = float(v.mean() / pv.mean()) if len(pv) >= 5 and v.notna().all() and pv.notna().all() and pv.mean() > 0 else None
    touches = int(((recent["Low"] <= ref * 1.025) & (recent["High"] >= ref * .98) & (recent["Close"] >= ref * .98)).sum())
    held = bool((recent["Close"] >= ref * .98).all())
    dormant = bool(hold_sessions >= 5 and held and range_pct <= 15 and -2 <= distance <= 12)
    score = min(100, max(0, min(hold_sessions, 10) * 4 + (25 if range_pct <= 10 else 12 if range_pct <= 15 else 0) + (15 if held else 0) + (10 if touches >= 2 else 0) + (10 if compression is not None and compression >= 25 else 0)))
    pts = pivots(df["Low"].tail(30).to_numpy())
    pattern, neckline = "غير مكتمل", None
    if len(pts) >= 2:
        tail = df.tail(30)
        a, b = pts[-2:]
        p1, p2 = float(tail["Low"].iloc[a]), float(tail["Low"].iloc[b])
        ratio = p2 / p1 - 1
        if abs(ratio) <= .03:
            pattern = "قاع مزدوج محتمل"
        elif .03 < ratio <= .12:
            pattern = "قاع أعلى محتمل"
        if pattern != "غير مكتمل":
            neckline = float(tail["High"].iloc[a:b + 1].max())
    return {
        "is_dormant": dormant, "dormancy_score": score,
        "dormancy_label": "ارتكاز خامل قرب الدعم" if dormant else "قاعدة قيد التشكّل / مراقبة",
        "base_low": value(lo), "base_high": value(hi), "base_range_pct": value(range_pct, 2),
        "base_tightness_pct": value(daily_range, 2), "tightness_pct": value(range_pct, 2),
        "volatility_compression_pct": value(compression, 1), "volume_dryup_ratio": value(dryup, 3),
        "volume_dried_up": dryup <= .65 if dryup is not None else None,
        "base_avg_volume": value(v.mean(), 0) if v.notna().all() else None,
        "support_touches": touches, "support_held": held, "sessions_in_base": hold_sessions,
        "base_bars": len(recent), "base_start": str(recent.index[0].date()),
        "distance_to_support_pct": value(distance, 2), "pattern": pattern,
        "neckline": value(neckline), "neckline_distance_pct": value((neckline / df["Close"].iloc[-1] - 1) * 100, 2) if neckline else None,
    }


def liquidity_summary(df, ind, bars, base, ind4=None):
    v = volume_series(df)
    cmf, fast, mfi = last(ind["cmf"]), last(ind["cmf10"]), last(ind["mfi"], 2)
    cmf_change, mfi_change = change(ind["cmf"]), change(ind["mfi"])
    available = cmf is not None
    all_volume = bool(len(df) and v.notna().all())
    div = divergence(bars, base.get("base_low") if not base.get("reference_support") else base["reference_support"])
    delta = df["Close"].diff().tail(10)
    vv = v.tail(10)
    up = float(vv[delta > 0].sum()) if len(vv) >= 10 and vv.notna().all() else None
    down = float(vv[delta < 0].sum()) if up is not None else None
    ratio = up / down if down is not None and down > 0 else None
    # Net buying is a CLV-volume proxy, not an order-book or executed buy/sell feed.
    mfv_tail = ind["mfv"].tail(10)
    net = float(mfv_tail.sum()) if len(mfv_tail) == 10 and mfv_tail.notna().all() else None
    obv_slope = slope(ind["obv"])
    acc_score = None
    detected = None
    state = "بانتظار بيانات الحجم"
    if available:
        raw = 50 + np.clip(cmf * 100, -30, 30)
        raw += np.clip((cmf_change or 0) * 100, -15, 15)
        raw += np.clip(((mfi if mfi is not None else 50) - 50) * .25, -10, 10)
        raw += 5 if obv_slope is not None and obv_slope > 0 else -5 if obv_slope is not None and obv_slope < 0 else 0
        raw += 10 if div["direction"] == "positive" else -10 if div["direction"] == "negative" else 0
        acc_score = int(round(np.clip(raw, 0, 100)))
        detected = bool(cmf > .05 and acc_score >= 60 and div["direction"] != "negative")
        state = "تجميع قوي" if detected and acc_score >= 75 else "تجميع خفي في القاع" if div["type"] == "stealth_accumulation" else "تصريف / ضغط بيعي" if cmf < -.05 else "حيادي / مراقبة"
    quality_status = "available" if available else "volume_missing" if v.notna().sum() == 0 else "insufficient_history"
    return {
        "available": available, "cmf": cmf, "cmf_10": fast,
        "cmf_4h": last(ind4["cmf"]) if ind4 is not None and len(ind4) else None,
        "cmf_change": cmf_change, "cmf_slope": slope(ind["cmf"]),
        "cmf_label": "تدفق موجب" if cmf is not None and cmf > .05 else "تدفق سالب" if cmf is not None and cmf < -.05 else "محايد" if cmf is not None else "الحجم غير متاح",
        "mfi": mfi, "mfi_4h": last(ind4["mfi"], 2) if ind4 is not None and len(ind4) else None,
        "mfi_min": value(ind["mfi"].tail(30).min(), 2), "mfi_change": mfi_change,
        "clv": last(ind["clv"]), "mfv": last(ind["mfv"], 0),
        "obv": last(ind["obv"], 0), "obv_slope": obv_slope,
        "adl": last(ind["adl"], 0), "adl_slope": slope(ind["adl"]),
        "up_down_vol_ratio": value(ratio, 2), "up_volume_10d": value(up, 0), "down_volume_10d": value(down, 0),
        "net_buying": value(net, 0), "volume_dryup_ratio": base.get("volume_dryup_ratio"),
        "accumulation_score": acc_score, "accumulation_detected": detected, "accumulation_state": state,
        "divergence": div,
        "data_quality": {
            "status": quality_status, "volume_source": "observed_ohlcv" if all_volume else "partial" if v.notna().any() else "missing",
            "bars": len(df), "volume_bars": int(v.notna().sum()), "required_cmf_bars": 20,
            "as_of": str(df.index[-1].date()) if len(df) else None,
            "note": "حجم التداول الفعلي؛ لا حجم مستنتج من السعر أو الفلوت." if all_volume else "CMF محسوب من آخر نافذة حجم كاملة؛ بعض التاريخ الحجمي غير مكتمل." if available else "اللقطة المحفوظة لا تحتوي حجمًا كافيًا. CMF وMFI والانحراف غير متاحة حتى توفر الأحجام المطلوبة.",
        },
    }


def confirmations(liq, base, sweep, ind4=None):
    cmf, fast, mfi = liq["cmf"], liq["cmf_10"], liq["mfi"]
    div = liq["divergence"]
    has4 = ind4 is not None and len(ind4) and last(ind4["cmf"]) is not None and last(ind4["rsi"]) is not None
    checks = {
        "cmf_accum": bool(cmf > .05 and ((liq["cmf_change"] or 0) > 0 or (fast is not None and fast > .05))) if cmf is not None else None,
        "liq_divergence": div["direction"] == "positive" if div["type"] != "unavailable" else None,
        "mfi_flow": bool(mfi >= 40 and (liq["mfi_change"] or 0) > 0 and liq["up_volume_10d"] > 1.1 * liq["down_volume_10d"]) if mfi is not None and liq["up_volume_10d"] is not None and liq["down_volume_10d"] is not None else None,
        "dormant_tight": bool(base.get("is_dormant")), "liquidity_sweep": bool(sweep),
        "mtf_4h": bool(last(ind4["rsi"]) >= 35 and last(ind4["cmf"]) > 0) if has4 else None,
    }
    earned = sum(w for key, _, w in CONFIRM_DEFS if checks[key] is True)
    known = sum(w for key, _, w in CONFIRM_DEFS if checks[key] is not None)
    return checks, earned, known


def risk_plan(signal, atr=None):
    price, sup = signal.get("price"), signal.get("support")
    stop = sup * .98 if sup and sup > 0 else None
    valid = bool(price and stop and 0 < stop < price)
    risk_share = price - stop if valid else None
    targets = []
    for idx, target in enumerate(signal.get("targets") or []):
        level = target.get("level")
        if level is None:
            continue
        targets.append({**target, "short": f"T{idx + 1}", "price": level,
                        "pct": value((level / price - 1) * 100, 2) if price else None,
                        "rr": value((level - price) / risk_share, 2) if valid else None})
    shares = math.floor(min(100 / risk_share, 10000 / price)) if valid else None
    return {
        "valid": valid, "entry": price, "stop": value(stop), "stop_method": "الدعم المرجعي − 2%",
        "risk_pct": value(risk_share / price * 100, 2) if valid else None,
        "targets": targets, "rr_t1": targets[0]["rr"] if targets else None,
        "rr_t2": targets[1]["rr"] if len(targets) > 1 else None,
        "breakout_trigger": signal.get("neckline") or signal.get("resistance"),
        "atr": atr, "shares": shares, "position_value": value(shares * price, 2) if shares is not None else None,
        "risk_amount": value(shares * risk_share, 2) if valid else None,
        "account_equity": 10000, "risk_budget": 100,
        "disclaimer": "سيناريو ورقي فقط. الوقف لا يضمن التنفيذ عند الفجوات؛ الحجم محدود برأس مال 10,000$ ومخاطرة حتى 1%. لا توجد أوامر تداول.",
    }


def attach_analytics(signal, df, h4=None, snapshot=False):
    """Preserve original strategy and market snapshot; add a separate confirmation layer."""
    result = dict(signal)
    df = normalize_frame(df)
    if df.empty:
        return result
    anchor = (signal.get("split") or {}).get("date")
    ind = indicator_frame(df, anchor)
    four = normalize_frame(h4) if h4 is not None and len(h4) else None
    ind4 = indicator_frame(four, anchor) if four is not None and len(four) else None
    bars = chart_bars(df, ind)
    base = dormant_base(df, signal.get("support"), int(signal.get("support_hold_sessions") or 0))
    base["reference_support"] = signal.get("support")
    liq = liquidity_summary(df, ind, bars, base, ind4)
    checks, confirm, known = confirmations(liq, base, signal.get("liquidity_sweep"), ind4)
    core = int(signal.get("readiness_score") or 0)
    composite = round(.7 * core + .3 * confirm)
    grade = "A+" if composite >= 82 else "A" if composite >= 68 else "B" if composite >= 52 else "C" if composite >= 38 else "D"
    result.update({
        "state": STATE_CODES.get(signal.get("stage"), "WATCH"), "state_label": signal.get("stage"),
        "strength_score": core, "confirm_score": confirm, "confirmation_available_weight": known,
        "composite_score": composite, "grade": grade, "grade_provisional": known < 100,
        "confirmations": checks, "liquidity": liq, "liquidity_divergence": liq["divergence"],
        "dormant_base": base, "chart": bars, "chart_4h": chart_bars(four, ind4, FOUR_HOUR_BARS, True) if ind4 is not None else [],
        "risk": risk_plan(signal, last(ind["atr"])),
        "analysis_quality": {"price_history_bars": len(df), "limited_price_history": len(df) < 50,
                             "snapshot_enrichment": snapshot,
                             "note": "المؤشرات السعرية على الشارت محسوبة من التاريخ المتاح وقد تختلف عن قراءات المسح الكامل." if snapshot else "المؤشرات محسوبة قبل اختصار الشموع المعروضة."},
    })
    original = signal.get("indicators") or {}
    result["indicators"] = {**original, **{k: liq.get(k) for k in ("cmf", "cmf_10", "cmf_4h", "cmf_slope", "mfi", "mfi_4h", "mfi_min", "clv", "mfv", "obv", "obv_slope", "adl")}}
    if not snapshot:
        result["indicators"].update({k: last(ind[k]) for k in ("rsi", "ema20", "ema30", "ema50", "vwap", "macd", "macd_signal", "macd_hist", "atr")})
        result["indicators"]["rsi_4h"] = last(ind4["rsi"]) if ind4 is not None else None
    return result

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Radar scanner - fixed candle/data pipeline.

Fixes:
- ignores placeholder symbols such as EXAMPLE
- robust yfinance candle loading with retries and validation
- keeps symbols with data errors visible instead of silently dropping them
- stores the last 250 daily OHLCV candles in docs/data.json
- records candle/data status for dashboard diagnostics
- preserves the existing 7-condition split/spike strategy
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES_PATH = os.path.join(REPO_ROOT, "reverse_split_candidates.json")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "watchlist.json")
OUTPUT_PATH = os.path.join(REPO_ROOT, "docs", "data.json")

SUPPORT_HOLD_SESSIONS = 5
RETEST_HOLD_SESSIONS = 3
BREAKOUT_PCT = 0.20
RSI_MAX = 30
FLOAT_MAX = 5_000_000
SHORT_SHARES_MAX = 20_000
PRICE_MIN, PRICE_MAX = 1.0, 5.0
OPEN_DAY_RISE_MAX_PCT = 20.0
DAILY_VOLUME_DORMANT_MAX = 300_000
SPIKE_MIN_PCT = 100.0
SPIKE_WINDOW_DAYS = 25
RETEST_WINDOW_MIN_DAYS, RETEST_WINDOW_MAX_DAYS = 4, 20
CANDLE_LOOKBACK = 250
MIN_CANDLES = 15
NEGATIVE_KEYWORDS = [
    "offering", "dilution", "going concern", "delisting", "delist",
    "bankruptcy", "chapter 11", "default", "restatement",
    "sec investigation", "class action", "resign", "auditor",
    "non-compliance", "notice of non-compliance",
]
INVALID_SYMBOLS = {
    "EXAMPLE", "TEST", "TICKER", "SYMBOL", "PLACEHOLDER", "XXXX"
}

def clean_number(v):
    try:
        x = float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None

def get_daily_candles(symbol, period="1y", retries=3):
    """Return normalized OHLCV dataframe or None. Never raises."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"    [DATA] {symbol}: تحميل الشموع ({attempt}/{retries})")
            t = yf.Ticker(symbol)
            hist = t.history(
                period=period,
                interval="1d",
                auto_adjust=False,
                actions=False,
                raise_errors=False,
            )
            if hist is None or hist.empty:
                raise ValueError("Yahoo أعاد بيانات فارغة")

            df = hist.reset_index()
            date_col = "Date" if "Date" in df.columns else "Datetime"
            required = [date_col, "Open", "High", "Low", "Close", "Volume"]
            missing = [c for c in required if c not in df.columns]
            if missing:
                raise ValueError(f"أعمدة مفقودة: {', '.join(missing)}")

            df = df.rename(columns={
                date_col: "t", "Open": "o", "High": "h",
                "Low": "l", "Close": "c", "Volume": "v",
            })
            df["t"] = pd.to_datetime(df["t"], errors="coerce", utc=True).dt.tz_localize(None)
            for col in ["o", "h", "l", "c", "v"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["t", "o", "h", "l", "c", "v"]].dropna()
            df = df[(df["o"] > 0) & (df["h"] > 0) & (df["l"] > 0) & (df["c"] > 0)]
            df = df[df["h"] >= df[["o", "c", "l"]].max(axis=1)]
            df = df[df["l"] <= df[["o", "c", "h"]].min(axis=1)]
            df = df.drop_duplicates(subset=["t"]).sort_values("t").reset_index(drop=True)
            if df.empty:
                raise ValueError("لم تبقَ شموع صالحة بعد التنظيف")

            print(f"    [DATA] {symbol}: {len(df)} شمعة صالحة ✓")
            return df.tail(CANDLE_LOOKBACK).copy()
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                time.sleep(1.5 * attempt)
    print(f"    [DATA-ERROR] {symbol}: {last_error}")
    return None

def serialize_candles(df):
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.tail(CANDLE_LOOKBACK).iterrows():
        out.append({
            "time": row["t"].strftime("%Y-%m-%d"),
            "open": round(float(row["o"]), 6),
            "high": round(float(row["h"]), 6),
            "low": round(float(row["l"]), 6),
            "close": round(float(row["c"]), 6),
            "volume": int(max(0, row["v"])),
        })
    return out

def get_latest_reverse_split(symbol):
    try:
        t = yf.Ticker(symbol)
        splits = t.splits
        if splits is None or splits.empty:
            return None, None
        latest_date, latest_ratio = None, None
        for date, ratio in splits.items():
            ratio = clean_number(ratio)
            if ratio is None or ratio >= 1:
                continue
            d = pd.Timestamp(date).tz_localize(None).date()
            if latest_date is None or d > latest_date:
                latest_date, latest_ratio = d, ratio
        if latest_date is None:
            return None, None
        return str(latest_date), f"1:{round(1/latest_ratio)}"
    except Exception:
        return None, None

def get_financials(symbol):
    out = {"float": None, "mcap": None, "short_shares": None, "short_date": None}
    try:
        t = yf.Ticker(symbol)
        info = t.get_info() if hasattr(t, "get_info") else t.info
        out["float"] = info.get("floatShares") or info.get("sharesOutstanding")
        out["mcap"] = info.get("marketCap")
        out["short_shares"] = info.get("sharesShort")
        out["short_date"] = info.get("dateShortInterest")
    except Exception as e:
        print(f"    [FIN] {symbol}: تعذر جلب البيانات المالية: {e}")
    return out

def get_news_flags(symbol, limit=15):
    try:
        t = yf.Ticker(symbol)
        news = t.news or []
        hits = []
        for n in news[:limit]:
            content = n.get("content", n)
            if not isinstance(content, dict):
                continue
            title = (content.get("title") or "").lower()
            summary = str(content.get("summary") or "").lower()
            text = title + " " + summary
            for kw in NEGATIVE_KEYWORDS:
                if kw in text:
                    hits.append({"headline": content.get("title"), "keyword": kw})
                    break
        return hits
    except Exception:
        return []

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line

def classify_macd(macd_line, signal_line, hist):
    if len(hist) < 2 or pd.isna(hist.iloc[-1]):
        return "—"
    h, m = hist.iloc[-1], macd_line.iloc[-1]
    if h > 0 and m > 0 and h > hist.iloc[-2]:
        return "إيجابي"
    if h > 0:
        return "إيجابي خفيف"
    if h < 0:
        return "سلبي"
    return "محايد"

def anchored_vwap(df, window=20):
    recent = df.tail(window)
    typical = (recent["h"] + recent["l"] + recent["c"]) / 3
    return (typical * recent["v"]).sum() / max(recent["v"].sum(), 1)

def find_support(df, lookback=30, buffer_pct=0.02):
    recent = df.tail(lookback).reset_index(drop=True)
    if recent.empty:
        return None, 0
    support = float(recent["l"].min())
    hold = 0
    for close in reversed(recent["c"].tolist()):
        if close >= support * (1 - buffer_pct):
            hold += 1
        else:
            break
    return round(support, 4), hold

def find_dropped_candles(df, lookback=250, current_price=None, max_levels=2):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 5:
        return []
    highs = recent["h"]
    candidates = []
    for i in range(2, len(recent) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            level = float(highs[i])
            if (recent["c"].iloc[i+1:] < level).all():
                candidates.append(level)
    candidates = sorted(set(round(c, 3) for c in candidates))
    if current_price:
        candidates = [c for c in candidates if c > current_price * 0.95]
    return candidates[:max_levels]

def detect_entry_phase(df, support, support_hold):
    if support is None or support_hold < SUPPORT_HOLD_SESSIONS:
        return "awaiting-breakout", None
    breakout_level = support * (1 + BREAKOUT_PCT)
    window = df["c"].tolist()[-40:]
    breakout_idx = breakout_high = None
    for i, c in enumerate(window):
        if c >= breakout_level and (breakout_high is None or c > breakout_high):
            breakout_high, breakout_idx = c, i
    if breakout_idx is None:
        return "awaiting-breakout", None
    after_breakout = window[breakout_idx + 1:]
    if not after_breakout:
        return "testing-resistance", {"breakoutHigh": round(breakout_high, 4)}
    if min(after_breakout) < support * 0.97:
        return "invalidated", None
    near_support = [c for c in after_breakout if c <= support * 1.06]
    if len(near_support) >= RETEST_HOLD_SESSIONS:
        retest_low = min(after_breakout[-RETEST_HOLD_SESSIONS:])
        entry_low = round(support + 0.05, 3)
        entry_high = round(support + (breakout_high - support) * 0.4, 3)
        entry_mid = round((entry_low + entry_high) / 2, 3)
        target = round(breakout_high, 3)
        return "entry-confirmed", {
            "support1": round(support, 3), "breakoutHigh": round(breakout_high, 3),
            "breakoutPct": round(BREAKOUT_PCT * 100), "retestLow": round(retest_low, 3),
            "retestSessions": RETEST_HOLD_SESSIONS, "entryLow": entry_low,
            "entryMid": entry_mid, "entryHigh": entry_high,
            "stopLoss": round(support, 3), "target": target,
        }
    return "retesting-support", {"breakoutHigh": round(breakout_high, 4)}

def detect_spike(df):
    recent = df.tail(SPIKE_WINDOW_DAYS + 5).reset_index(drop=True)
    if len(recent) < 3:
        return None
    recent["prev_close"] = recent["c"].shift(1)
    recent["gain_pct"] = (recent["c"] - recent["prev_close"]) / recent["prev_close"] * 100
    spikes = recent[recent["gain_pct"] >= SPIKE_MIN_PCT]
    if spikes.empty:
        return None
    spike_idx = spikes.index[-1]
    spike_row = spikes.iloc[-1]
    peak_price = float(recent["h"].iloc[spike_idx:].max())
    after = recent.iloc[spike_idx:].reset_index(drop=True)
    pullback_low = float(after["l"].min())
    pullback_days = len(after) - 1
    pre_spike_base = float(recent["c"].iloc[max(0, spike_idx - 3):spike_idx].min()) if spike_idx > 0 else float(spike_row["prev_close"])
    support_match_pct = abs(pullback_low - pre_spike_base) / pre_spike_base * 100 if pre_spike_base else None
    return {
        "openPrice": round(float(spike_row["prev_close"]), 4),
        "peakPrice": round(peak_price, 4),
        "spikePct": round((peak_price - float(spike_row["prev_close"])) / float(spike_row["prev_close"]) * 100),
        "pullbackDays": pullback_days, "pullbackLow": round(pullback_low, 4),
        "preSpikeBase": round(pre_spike_base, 4),
        "supportMatchPct": round(support_match_pct, 1) if support_match_pct is not None else None,
        "inWindow": RETEST_WINDOW_MIN_DAYS <= pullback_days <= RETEST_WINDOW_MAX_DAYS,
    }

def candle_meta(df):
    candles = serialize_candles(df)
    return {
        "candles": candles,
        "candleCount": len(candles),
        "candleStart": candles[0]["time"] if candles else None,
        "candleEnd": candles[-1]["time"] if candles else None,
        "dataStatus": "ready" if len(candles) >= MIN_CANDLES else "insufficient",
        "dataError": None,
    }

def score_split_model(symbol, df, split_date, split_ratio, fin, news_hits):
    price = float(df["c"].iloc[-1])
    prev_close = float(df["c"].iloc[-2]) if len(df) > 1 else price
    chg = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
    support, support_hold = find_support(df)
    dropped = find_dropped_candles(df, current_price=price)
    daily_vol = int(df["v"].iloc[-1])
    r = rsi(df["c"]).iloc[-1]
    macd_line, signal_line, hist = macd(df["c"])
    macd_state = classify_macd(macd_line, signal_line, hist)
    ma20 = df["c"].rolling(20).mean().iloc[-1] if len(df) >= 20 else None
    ma50 = df["c"].rolling(50).mean().iloc[-1] if len(df) >= 50 else None
    ma200 = df["c"].rolling(200).mean().iloc[-1] if len(df) >= 200 else None
    vwap = anchored_vwap(df)
    float_shares, mcap_usd, short_shares = fin.get("float"), fin.get("mcap"), fin.get("short_shares")
    short_unknown = short_shares is None
    open_price = open_high = None
    excluded_reason = None
    if split_date:
        try:
            day_rows = df[df["t"].dt.date == pd.to_datetime(split_date).date()]
            if not day_rows.empty:
                open_price, open_high = float(day_rows["o"].iloc[0]), float(day_rows["h"].iloc[0])
        except Exception:
            pass
    if open_price and open_high and (open_high - open_price) / open_price * 100 > OPEN_DAY_RISE_MAX_PCT:
        excluded_reason = "open-rise"
    # السعر خارج 1$–5$ = مستبعد تلقائياً، وليس "مرصوداً" أو "شبه جاهز".
    # لا يسمح هذا الشرط بدخول السهم في أي تصنيف فرصة.
    if not (PRICE_MIN <= price <= PRICE_MAX):
        excluded_reason = excluded_reason or "price-range"

    cond_support = support_hold >= SUPPORT_HOLD_SESSIONS
    cond_news = len(news_hits) == 0
    cond_macd = macd_state in ("سلبي", "محايد", "إيجابي خفيف")
    cond_rsi = (r is not None) and (not math.isnan(r)) and r < RSI_MAX
    cond_below_ma = all([
        ma20 is None or pd.isna(ma20) or price < ma20,
        ma50 is None or pd.isna(ma50) or price < ma50,
        ma200 is None or pd.isna(ma200) or price < ma200,
        price < vwap if vwap else True,
    ])
    cond_float = (float_shares is not None) and (float_shares < FLOAT_MAX)
    cond_short = short_unknown or (short_shares < SHORT_SHARES_MAX)
    conds = [int(cond_support), int(cond_news), int(cond_macd), int(cond_rsi), int(cond_below_ma), int(cond_float), int(cond_short)]
    met = sum(conds)
    if excluded_reason: status = "excluded"
    elif met == 7: status = "ready"
    elif met >= 5: status = "near"
    elif met >= 3: status = "watch"
    else: status = "flag"
    # إشارة الدخول المؤكدة لا تُمنح إلا للسهم المصنف "جاهز فنياً".
    # السهم شبه الجاهز يبقى مراقبة/انتظار فقط مهما كانت حالة نموذج الدخول.
    entry_phase, entry_model = ("not-applicable", None)
    if status == "ready" and support is not None:
        entry_phase, entry_model = detect_entry_phase(df, support, support_hold)
    return {
        "tk": symbol, "price": round(price, 4), "chg": chg, "status": status, "model": "split",
        "conds": conds, "exclusionReason": excluded_reason,
        "split": split_date, "ratio": split_ratio, "floatShares": float_shares,
        "shortShares": short_shares, "shortUnknown": short_unknown, "mcapUSD": mcap_usd,
        "openPrice": open_price, "openHigh": open_high, "support": support,
        "supportHoldSessions": support_hold, "droppedCandles": dropped, "dailyVolume": daily_vol,
        "entryPhase": entry_phase, "entryModel": entry_model,
        "macd": macd_state, "rsi": None if r is None or math.isnan(r) else round(r, 1),
        "ma20": None if ma20 is None or pd.isna(ma20) else round(ma20, 4),
        "ma50": None if ma50 is None or pd.isna(ma50) else round(ma50, 4),
        "ma200": None if ma200 is None or pd.isna(ma200) else round(ma200, 4),
        "vwap": round(vwap, 4) if vwap else None, "newsFlags": news_hits,
        **candle_meta(df),
    }

def score_spike_model(symbol, df, spike, fin, news_hits):
    price = float(df["c"].iloc[-1])
    prev_close = float(df["c"].iloc[-2]) if len(df) > 1 else price
    chg = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
    r = rsi(df["c"]).iloc[-1]
    float_shares, mcap_usd, short_shares = fin.get("float"), fin.get("mcap"), fin.get("short_shares")
    short_pct = None
    if short_shares is not None and mcap_usd:
        threshold = 0.5 if mcap_usd <= 5_000_000 else (0.4 if mcap_usd <= 15_000_000 else 0.3)
        short_pct = short_shares * price / mcap_usd * 100
        cond_short = short_pct <= threshold
    else:
        cond_short = True
    cond_float = (float_shares is not None) and (float_shares < FLOAT_MAX)
    cond_rsi = (r is not None) and (not math.isnan(r)) and r < RSI_MAX
    cond_news = len(news_hits) == 0
    vol_recent, vol_avg = df["v"].tail(3).mean(), df["v"].tail(10).mean()
    cond_vol_dryup = vol_recent < vol_avg if vol_avg else False
    conds = [1, int(bool(spike.get("inWindow"))), int(cond_float), int(cond_short), int(cond_rsi), int(cond_news), int(cond_vol_dryup)]
    met = sum(conds)
    excluded_reason = "price-range" if not (PRICE_MIN <= price <= PRICE_MAX) else None
    if excluded_reason: status = "excluded"
    elif met == 7: status = "ready"
    elif met >= 5: status = "near"
    elif met >= 3: status = "watch"
    else: status = "flag"
    return {
        "tk": symbol, "price": round(price, 4), "chg": chg, "status": status, "model": "spike",
        "exclusionReason": excluded_reason, "conds": conds,
        "peakPrice": spike["peakPrice"], "spikePct": f'+{spike["spikePct"]}%',
        "openPrice": spike["openPrice"], "pullbackDays": spike["pullbackDays"],
        "pullbackLow": spike["pullbackLow"],
        "supportMatch": f'مطابقة تقريبية (فرق {spike["supportMatchPct"]}٪)' if spike.get("supportMatchPct") is not None else "غير محسوبة",
        "floatShares": float_shares, "mcapUSD": mcap_usd,
        "shortPct": round(short_pct, 3) if short_pct is not None else None,
        "shortShares": short_shares,
        "rsi": None if r is None or math.isnan(r) else round(r, 1), "newsFlags": news_hits,
        **candle_meta(df),
    }

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def build_symbol_list():
    auto, manual = load_json(CANDIDATES_PATH), load_json(WATCHLIST_PATH)
    merged = {}
    for c in auto:
        sym = str(c.get("symbol", "")).upper().strip()
        if sym and sym not in INVALID_SYMBOLS:
            merged[sym] = {"symbol": sym, "model_hint": "split", "split_date": c.get("split_date")}
    for m in manual:
        sym = str(m.get("symbol", "")).upper().strip()
        if not sym or sym in INVALID_SYMBOLS:
            if sym:
                print(f"[SKIP] {sym}: رمز تجريبي/placeholder — تم تجاهله")
            continue
        if sym in merged and m.get("model_hint", "auto") == "auto":
            continue
        merged[sym] = {"symbol": sym, "model_hint": m.get("model_hint", "auto"), "split_date": m.get("manual_split_date")}
    return list(merged.values())

def error_result(symbol, error, status="data-error"):
    return {
        "tk": symbol, "price": None, "chg": 0, "status": "flag",
        "model": "split", "conds": [0,0,0,0,0,0,0],
        "dataStatus": status, "dataError": error, "candles": [],
        "candleCount": 0, "candleStart": None, "candleEnd": None,
        "note": error,
    }

def analyze_symbol(entry):
    symbol = entry["symbol"]
    print(f"[..] {symbol}")
    df = get_daily_candles(symbol)
    if df is None:
        print(f"[!!] {symbol}: فشل تحميل بيانات الشموع")
        return [error_result(symbol, "لم يتمكن Yahoo Finance من توفير بيانات OHLCV لهذا الرمز.", "error")]
    if len(df) < MIN_CANDLES:
        print(f"[!!] {symbol}: لا توجد بيانات شموع كافية ({len(df)} < {MIN_CANDLES})")
        meta = candle_meta(df)
        _price = round(float(df["c"].iloc[-1]), 4)
        _status = "excluded" if not (PRICE_MIN <= _price <= PRICE_MAX) else "flag"
        _reason = "price-range" if _status == "excluded" else None
        return [{
            "tk": symbol, "price": _price,
            "chg": 0, "status": _status, "model": "split",
            "conds": [0,0,0,0,0,0,0], "dataStatus": "insufficient",
            "dataError": f"عدد الشموع {len(df)} أقل من الحد الأدنى {MIN_CANDLES}.",
            "exclusionReason": _reason,
            **meta,
        }]

    fin = get_financials(symbol)
    news_hits = get_news_flags(symbol)
    model_hint = entry.get("model_hint", "auto")
    split_date, split_ratio = entry.get("split_date"), None
    if not split_date or model_hint in ("split", "auto"):
        d, r = get_latest_reverse_split(symbol)
        split_date, split_ratio = split_date or d, r
    spike = detect_spike(df)
    results = []
    if model_hint in ("split", "auto") and split_date:
        results.append(score_split_model(symbol, df, split_date, split_ratio, fin, news_hits))
    if model_hint in ("spike", "auto") and spike:
        results.append(score_spike_model(symbol, df, spike, fin, news_hits))
    if not results:
        meta = candle_meta(df)
        results.append({
            "tk": symbol, "price": round(float(df["c"].iloc[-1]), 4),
            "chg": round((float(df["c"].iloc[-1]) - float(df["c"].iloc[-2])) / float(df["c"].iloc[-2]) * 100, 2) if len(df)>1 and df["c"].iloc[-2] else 0,
            "status": "flag", "model": "split", "conds": [0,0,0,0,0,0,0],
            "note": "لم يُكتشف تقسيم عكسي حديث ولا صعود حاد — أُدرج للمراقبة الأساسية فقط",
            **meta,
        })
    return results

def main():
    symbols = build_symbol_list()
    print(f"\n🔎 عدد الرموز بعد التنظيف: {len(symbols)}")
    all_results = []
    for entry in symbols:
        try:
            res = analyze_symbol(entry)
            if res:
                all_results.extend(res)
        except Exception as e:
            print(f"[XX] {entry.get('symbol')}: خطأ — {e}", file=sys.stderr)
            all_results.append(error_result(entry.get("symbol"), str(e), "error"))
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanner_version": "2.1-candles-fixed",
        "candle_policy": {"interval": "1d", "max_candles": CANDLE_LOOKBACK, "minimum": MIN_CANDLES},
        "stocks": all_results,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    tmp = OUTPUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp, OUTPUT_PATH)
    ready = sum(1 for s in all_results if s.get("dataStatus") == "ready")
    print(f"\n✅ تم كتابة {len(all_results)} نتيجة في {OUTPUT_PATH}")
    print(f"📊 نتائج بها شموع: {ready} | أخطاء/نقص بيانات: {len(all_results)-ready}")

if __name__ == "__main__":
    main()

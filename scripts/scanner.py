#!/usr/bin/env python3
"""رادار التقسيم العكسي — ارتكاز الدعم.
يبحث عن أسهم قُسّمت عكسيًا قبل 20–50 يومًا، ويقيّمها على فريم اليومي و4 ساعات، ويكتب data.json للداشبورد.
البيانات من Yahoo Finance فقط. للتعديل على الشروط غيّر الثوابت بالأسفل."""
import datetime as dt, io, json, math, re, time, urllib.request
import numpy as np, pandas as pd, yfinance as yf

# ───────── الشروط (قابلة للتعديل) ─────────
WIN_MIN, WIN_MAX = 20, 50      # عمر التقسيم بالأيام
MAX_RISE = 20.0                # أعلى صعود مسموح من إغلاق يوم التقسيم (٪) — يُقاس على أعلى إغلاق بعد التقسيم
DROP_MIN = 35.0                # أدنى هبوط يُعد قويًا (٪) من القمة بعد التقسيم
RSI_OS = 30.0                  # التشبع البيعي: RSI(14) أدنى من هذا
HOLD_MIN = 5                   # جلسات ثبات الدعم
RES_MIN = 15.0                 # أدنى ارتداد نحو المقاومة (٪) — الهدف ≈ 20 مع تسامح
RETEST_NEAR = 12.0             # العودة لاختبار الدعم تعني النزول إلى أقل من هذه النسبة فوقه (٪)
HIGHER_LOW = 3.0               # أعلى من الدعم بهذه النسبة = قاع أعلى (دعم مزدوج)
HOLD_AFTER_MIN = 2             # جلسات الثبات بعد الاختبار
NECK_MAX = 30.0                # أقصى بُعد لخط العنق عن الدعم (٪)
NEAR = 0.02                    # "يقترب" من EMA20/VWAP = ضمن 2٪
STAGES = [(80, "🟢 جاهز فنيًا"), (55, "🟠 شبه جاهز"), (30, "🟡 قيد المتابعة")]
BATCH = 200
CK = [("drop", "هبوط قوي بعد التقسيم (قد يتخطى 50%)", 10), ("rsi", "RSI لمس التشبع البيعي (تحت 30)", 10),
      ("hold5", "ثبات الدعم 5 جلسات دون كسر", 15), ("res", "اختبار أقرب مقاومة (ارتداد ≈ 20%)", 10),
      ("retest", "عودة لاختبار الدعم أو قاع أعلى (دعم مزدوج)", 15), ("hold_after", "ثبات 2–5 جلسات بعد الاختبار", 10),
      ("neck", "اختراق خط العنق (قريب من الدعم)", 10), ("ema", "تحت EMA 20/30/50 ثم استعادة EMA20 و VWAP", 10),
      ("news", "لا أخبار سلبية قادمة / أو محفز إيجابي", 10)]
WARN = {"offering": "طرح أسهم", "public offering": "طرح عام", "dilution": "تخفيف", "registered direct": "طرح مباشر",
        "private placement": "طرح خاص", "warrant": "وارنت", "going concern": "شك بالاستمرارية", "delist": "شطب",
        "bankruptcy": "إفلاس", "deficiency": "عدم امتثال", "convertible": "سندات قابلة للتحويل", "ATM": "برنامج ATM",
        "reverse split": "تقسيم عكسي جديد"}
CAT = {"FDA": "FDA", "approval": "موافقة", "phase": "تجارب سريرية", "contract": "عقد", "partnership": "شراكة",
       "acquisition": "استحواذ", "patent": "براءة اختراع", "grant": "منحة", "launch": "إطلاق"}


def universe():
    s = set()
    for url, col in (("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
                     ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol")):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode()
            df = pd.read_csv(io.StringIO(raw), sep="|", dtype=str)
            df = df[df[col].notna() & ~df[col].str.startswith("File Creation", na=False)]
            for f in ("ETF", "Test Issue"):
                if f in df: df = df[df[f] != "Y"]
            for t in df[col]:
                if t.isalpha() and len(t) <= 5 and not (len(t) == 5 and t[-1] in "WRU"): s.add(t)
        except Exception as e:
            print("universe error", url, e)
    try:
        s |= {l.strip().upper() for l in open("extra_tickers.txt") if l.strip() and not l.startswith("#")}
    except FileNotFoundError:
        pass
    return sorted(s)


def rsi(c, n=14):
    d = c.diff(); ru = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); rd = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + ru / rd.replace(0, np.nan)); out[rd == 0] = 100
    return out


def ema(c, n): return c.ewm(span=n, adjust=False).mean()


def evaluate(d, split_date, extra_sweep=False):
    """تقييم يومي بحت. يرجع (reason, info) — reason=None إن تجاوز بوابات الدخول."""
    i0 = d.index.searchsorted(split_date)
    if i0 >= len(d) - 3: return "no_data", {}
    post = d.iloc[i0:]
    cl, hi, lo = post["Close"].values, post["High"].values, post["Low"].values
    c0, last = cl[0], cl[-1]
    rise_peak = (cl.max() / c0 - 1) * 100
    info = {"c0": c0, "last": last, "chg": (last / c0 - 1) * 100, "rise_peak": rise_peak}
    if rise_peak > MAX_RISE: return "rose_over_20", info
    pk = int(np.argmax(cl)); peak = cl[pk]
    dd = (1 - lo[pk:].min() / peak) * 100
    rs = rsi(d["Close"]); rsi_min = float(rs.loc[post.index].min())
    info.update(dd=dd, rsi_min=rsi_min, rsi_now=float(rs.iloc[-1]))
    f = {k: False for k, _, _ in CK}; note = {}
    f["drop"] = dd >= DROP_MIN; f["rsi"] = rsi_min < RSI_OS
    if not f["drop"]: return "no_strong_drop", {**info, "f": f, "note": note}
    if not f["rsi"]: return "rsi_not_oversold", {**info, "f": f, "note": note}
    ci = pk + int(np.argmin(cl[pk:])); S = float(cl[ci]); hold = len(cl) - 1 - ci
    f["hold5"] = hold >= HOLD_MIN; note["hold5"] = f"{hold} جلسات منذ القاع"
    info.update(S=S, hold=hold)
    H = ri = None; bounce = None; S2 = S; pattern = "—"; sweep = False; hold_after = None; r_i = None
    if hold >= 1:
        # أول قمة ارتداد مؤكدة: نتتبع أعلى سعر حتى يتراجع الإغلاق 7% عنه بعد بلوغ حد الارتداد
        run, ri = -1.0, ci + 1
        for j in range(ci + 1, len(cl)):
            if hi[j] > run: run, ri = float(hi[j]), j
            if run >= S * (1 + RES_MIN / 100) and cl[j] <= run * 0.93: break
        H = run; bounce = (H / S - 1) * 100
        f["res"] = bounce >= RES_MIN; note["res"] = f"ارتداد {bounce:.0f}%"
    if f["res"] and ri < len(cl) - 1:
        seg = lo[ri + 1:]; r_i = ri + 1 + int(np.argmin(seg)); low2 = float(seg.min())
        if (low2 / S - 1) * 100 <= RETEST_NEAR:
            if low2 >= S * (1 + HIGHER_LOW / 100): pattern, S2 = "قاع أعلى (دعم مزدوج)", low2
            else: pattern = "دعم رئيسي"
            f["retest"] = True
        hold_after = len(cl) - 1 - r_i
        f["hold_after"] = f["retest"] and hold_after >= HOLD_AFTER_MIN; note["hold_after"] = f"{hold_after} جلسات"
    sw = (lo[ci + 1:] < S * 0.998) & (cl[ci + 1:] >= S)
    sweep = bool(sw.any()) or extra_sweep
    if f["retest"] and H:
        ndist = (H / S2 - 1) * 100
        f["neck"] = last > H and ndist <= NECK_MAX; info["ndist"] = ndist
    e20, e30, e50 = ema(d["Close"], 20), ema(d["Close"], 30), ema(d["Close"], 50)
    gi = i0 + ci
    below = d["Close"].iloc[gi] < min(e20.iloc[gi], e30.iloc[gi], e50.iloc[gi])
    tp = (post["High"] + post["Low"] + post["Close"]) / 3; vol = post["Volume"].replace(0, np.nan)
    vwap = float((tp * vol).sum() / vol.sum()) if vol.notna().any() else float(tp.mean())
    r20 = last >= e20.iloc[-1] * (1 - NEAR); rv = last >= vwap * (1 - NEAR)
    f["ema"] = bool(below and r20 and rv)
    info.update(f=f, note=note, H=H, bounce=bounce, S2=S2, pattern=pattern, sweep=sweep, hold_after=hold_after,
                ema=(float(e20.iloc[-1]), float(e30.iloc[-1]), float(e50.iloc[-1])), vwap=vwap, r20=bool(r20), rv=bool(rv))
    return None, info


def tf4h(t):
    """فريم 4 ساعات: تجميع شموع الساعة. يرجع (rsi4h, أدنى سعر خلال سحب سيولة تحت الدعم لاحقًا يُفحص بـ sweep4h)."""
    try:
        h = yf.download(t, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
        h = h.dropna(subset=["Close"])
        return h.resample("4h", origin="start_day", offset="1h30min").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    except Exception as e:
        print("4h", t, e); return None


def sweep_4h(h4, S, since):
    if h4 is None or S is None: return False
    x = h4[h4.index.tz_localize(None) >= since] if h4.index.tz is not None else h4[h4.index >= since]
    return bool(((x["Low"] < S * 0.998) & (x["Close"] >= S)).any())


def parse_news(tk):
    out = []
    try:
        for n in tk.news or []:
            c = n.get("content", n)
            title = c.get("title") or n.get("title") or ""
            ts = c.get("pubDate") or n.get("providerPublishTime")
            when = pd.to_datetime(ts, unit="s", utc=True) if isinstance(ts, (int, float)) else pd.to_datetime(ts, utc=True, errors="coerce")
            url = ((c.get("canonicalUrl") or {}).get("url")) or n.get("link") or ""
            src = ((c.get("provider") or {}).get("displayName")) or n.get("publisher") or "Yahoo"
            if title and pd.notna(when): out.append((title, when, url, src))
    except Exception as e:
        print("news", e)
    return out


def news_block(tk, today):
    warns, cats = [], []
    for title, when, url, src in parse_news(tk):
        age = (today - when.tz_localize(None).date()).days if hasattr(when, "tz_localize") else 99
        if age > 5: continue
        for kw, ar in WARN.items():
            if re.search(r"\b" + re.escape(kw) + r"\b", title, re.I):
                warns.append({"type": ar, "title": title, "date": str(when.date()), "source": src, "url": url}); break
        else:
            for kw, ar in CAT.items():
                if re.search(r"\b" + re.escape(kw) + r"\b", title, re.I):
                    cats.append({"type": "📰 خبر إيجابي — " + ar, "title": title, "date": str(when.date()), "days_until": None, "source": src, "url": url}); break
    try:
        ed = (tk.calendar or {}).get("Earnings Date") or []
        for e in ed[:1]:
            du = (e - today).days
            if 0 <= du <= 30: cats.append({"type": "📅 نتائج ربع سنوية", "title": "موعد إعلان النتائج", "date": str(e), "days_until": du, "source": "Yahoo"})
    except Exception: pass
    return warns, cats


def fundamentals(tk):
    try: i = tk.info or {}
    except Exception: i = {}
    fl, so = i.get("floatShares"), i.get("sharesOutstanding")
    return {"float": fl or so, "float_exact": bool(fl), "float_source": "yahoo_float" if fl else ("yahoo_outstanding" if so else "unknown"),
            "shares_outstanding": so, "market_cap": i.get("marketCap")}


def clean(o):
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)): return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 4)
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.bool_): return bool(o)
    return o


def build_signal(t, d, sdate, ratio, info, today):
    tk = yf.Ticker(t); fu = fundamentals(tk); warns, cats = news_block(tk, today)
    f = dict(info["f"]); f["news"] = not warns
    since = d.index[d.index.searchsorted(sdate)]
    h4 = tf4h(t); S, S2, H = info.get("S"), info.get("S2"), info.get("H")
    sweep = info["sweep"] or sweep_4h(h4, S, pd.Timestamp(since))
    rsi4 = float(rsi(h4["Close"]).iloc[-1]) if h4 is not None and len(h4) > 20 else None
    score = sum(p for k, _, p in CK if f.get(k)); stage = None
    for th, name in STAGES:
        if score >= th: stage = name; break
    if stage and stage.startswith("🟢") and not (f["neck"] and f["retest"] and f["news"]): stage = "🟠 شبه جاهز"
    if not stage: return None, score
    note = info.get("note", {}); last = info["last"]
    checklist = [{"key": k, "rule": r, "points": p, "status": bool(f.get(k)), "note": note.get(k, "")} for k, r, p in CK]
    low_ref = min(S, S2) if S2 else S; unit = (H - S2) if (H and S2) else 0
    plan = {"entry_price": round(last, 4), "stop_loss": round(low_ref * 0.98, 4), "target_1": round(H + unit * 0.5, 4) if f["neck"] and H else H,
            "target_2": round(H + unit, 4) if f["neck"] and H else None} if stage.startswith("🟢") else {}
    ch = d.tail(30)
    chart = [{"date": str(i.date()), "open": r.Open, "high": r.High, "low": r.Low, "close": r.Close} for i, r in ch.iterrows()]
    return {"ticker": t, "price": last, **fu, "split": {"date": str(sdate.date()), "ratio": ratio, "days_since": (today - sdate.date()).days, "close_on_split_day": info["c0"]},
            "change_since_split_pct": info["chg"], "max_drawdown_pct": info["dd"], "rsi_min": info["rsi_min"], "support": S,
            "support_hold_sessions": info["hold"], "resistance": H, "resistance_bounce_pct": info.get("bounce"),
            "neckline": H if f["retest"] else None, "neckline_distance_pct": info.get("ndist"), "pattern_type": info["pattern"],
            "retest_hold_sessions": info["hold_after"], "liquidity_sweep": sweep, "stage": stage, "readiness_score": score,
            "checklist": checklist, "missing_conditions": [r for k, r, _ in CK if not f.get(k)], "plan": plan,
            "indicators": {"rsi": info["rsi_now"], "rsi_4h": rsi4, "ema20": info["ema"][0], "ema30": info["ema"][1], "ema50": info["ema"][2],
                           "vwap": info["vwap"], "ema20_reclaim": info["r20"], "vwap_reclaim": info["rv"]},
            "news": {"warnings": warns, "catalysts": cats}, "has_warning": bool(warns), "has_upcoming_catalyst": bool(cats), "chart": chart}, score


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    syms = universe(); print("universe", len(syms))
    cand, total, got = [], 0, 0
    diag = {"split_window_pass": 0, "rise_ok_pass": 0, "drop_rsi_pass": 0, "support_hold_pass": 0}; reasons = {}; near = []
    for i in range(0, len(syms), BATCH):
        b = syms[i:i + BATCH]
        try: data = yf.download(b, period="6mo", interval="1d", group_by="ticker", actions=True, auto_adjust=True, threads=True, progress=False)
        except Exception as e: print("batch", i, e); continue
        for t in b:
            total += 1
            try: d = data[t].dropna(subset=["Close"])
            except Exception: continue
            if len(d) < 30 or "Stock Splits" not in d: continue
            got += 1
            d.index = pd.to_datetime(d.index).tz_localize(None)
            sp = d["Stock Splits"]; rs_ = sp[(sp > 0) & (sp < 1)]
            if rs_.empty: continue
            sdate = rs_.index[-1]; days = (today - sdate.date()).days
            if not (WIN_MIN <= days <= WIN_MAX): reasons["split_out_of_window"] = reasons.get("split_out_of_window", 0) + 1; continue
            cand.append((t, d.drop(columns=["Stock Splits", "Dividends"], errors="ignore"), sdate, f"1:{round(1 / rs_.iloc[-1])}"))
        time.sleep(1)
    diag["split_window_pass"] = len(cand); signals = []
    for t, d, sdate, ratio in cand:
        try:
            reason, info = evaluate(d, sdate)
            if reason is None or reason in ("no_strong_drop", "rsi_not_oversold"):
                if reason != "rose_over_20" and reason != "no_data": diag["rise_ok_pass"] += 1
            if reason is None or reason == "rsi_not_oversold": diag["drop_rsi_pass"] += 1 if reason is None else 0
            if reason is None and info["hold"] >= HOLD_MIN: diag["support_hold_pass"] += 1
            sig = None
            if reason is None:
                sig, score = build_signal(t, d, sdate, ratio, info, today)
                if sig is None: reason = "support_broken"
            if sig: signals.append(sig); continue
            reason = reason or "unknown"; reasons[reason] = reasons.get(reason, 0) + 1
            if len(near) < 10 and info:
                near.append({"ticker": t, "price": float(d["Close"].iloc[-1]), "days_since_split": (today - sdate.date()).days,
                             "change_since_split_pct": info.get("chg", 0), "float": fundamentals(yf.Ticker(t))["float"], "reason": reason})
        except Exception as e:
            print("eval", t, e); reasons["error"] = reasons.get("error", 0) + 1
    order = {s[1]: i for i, s in enumerate(STAGES)}
    signals.sort(key=lambda x: -x["readiness_score"])
    out = {"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "count": len(signals),
           "stats": {"catalysts": sum(1 for s in signals if s["has_upcoming_catalyst"])}, "signals": signals,
           "diagnostics": {"symbols_total": total, **diag, "data_coverage_pct": round(100 * got / max(1, total)),
                           "reject_reasons": reasons, "near_misses": near}}
    json.dump(clean(out), open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("signals", len(signals), "candidates", len(cand))


if __name__ == "__main__":
    main()

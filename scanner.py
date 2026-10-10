#!/usr/bin/env python3
"""رادار التقسيم العكسي — ارتكاز الدعم.
يبحث عن أسهم قُسّمت عكسيًا قبل 20–50 يومًا، ويقيّمها على فريم اليومي و4 ساعات، ويكتب data.json للداشبورد.
البيانات من Yahoo Finance فقط. للتعديل على الشروط غيّر الثوابت بالأسفل."""
import argparse
import datetime as dt, io, json, math, re, time, urllib.request
from pathlib import Path

from radar.analytics import (
    CMF_PERIOD, CMF_FAST, MFI_PERIOD, CONFIRM_DEFS, STATE_LABELS,
    anchored_vwap, attach_analytics, frame_from_bars, rsi, ema,
)
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
EXT_NECK_MAX = 8.0             # أبعد من هذا (٪) فوق خط العنق = تجاوز منطقة الدخول
EXCLUDE_RUN = 70.0              # صعد أكثر من هذا (٪) من الدعم بعد الثبات = حقق المطلوب، يُستبعد نهائيًا
BRK_AGE_MAX = 3                # مرّ على الاختراق أكثر من هذه الجلسات = متأخر
NEAR = 0.02                    # "يقترب" من EMA20/VWAP = ضمن 2٪
STAGES = [(80, "جاهز فنيًا"), (55, "شبه جاهز"), (30, "قيد المتابعة")]
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


def evaluate(d, split_date, extra_sweep=False):
    """تقييم يومي بحت. يرجع (reason, info) — reason=None إن تجاوز بوابات الدخول."""
    i0 = d.index.searchsorted(split_date)
    if i0 >= len(d) - 3: return "no_data", {}
    post = d.iloc[i0:]
    cl, hi, lo, op = post["Close"].values, post["High"].values, post["Low"].values, post["Open"].values
    c0, last = cl[0], cl[-1]
    rise_peak = (cl.max() / c0 - 1) * 100
    info = {"c0": c0, "last": last, "chg": (last / c0 - 1) * 100, "rise_peak": rise_peak}
    if rise_peak > MAX_RISE: return "rose_over_20", info
    pk = int(np.argmax(cl)); peak = cl[pk]
    dd = (1 - lo[pk:].min() / peak) * 100
    rs = rsi(d["Close"]); rsi_min = float(rs.loc[post.index].min())
    kh = int(np.argmax(hi))                                  # أعلى قمة (High) بعد التقسيم = الهدف الأخير
    info.update(dd=dd, rsi_min=rsi_min, rsi_now=float(rs.iloc[-1]), top=float(hi[kh]), low_all=float(lo[pk:].min()),
                F=float(hi[kh]), F_date=str(post.index[kh].date()))
    f = {k: False for k, _, _ in CK}; note = {}
    f["drop"] = dd >= DROP_MIN; f["rsi"] = rsi_min < RSI_OS
    if not f["drop"]: return "no_strong_drop", {**info, "f": f, "note": note}
    if not f["rsi"]: return "rsi_not_oversold", {**info, "f": f, "note": note}
    ci = pk + int(np.argmin(cl[pk:])); S = float(cl[ci]); hold = len(cl) - 1 - ci
    f["hold5"] = hold >= HOLD_MIN; note["hold5"] = f"{hold} جلسات منذ القاع"
    peak_run = (float(hi[ci:].max()) / S - 1) * 100
    info.update(S=S, hold=hold, peak_run=peak_run)
    if peak_run >= EXCLUDE_RUN: return "target_achieved", {**info, "f": f, "note": note}
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
    sweep_info = None
    if sw.any():
        jj = ci + 1 + int(np.where(sw)[0][-1])
        sweep_info = {"date": str(post.index[jj].date()), "low": float(lo[jj]), "close": float(cl[jj]), "tf": "يومي"}
    sweep = sweep_info is not None or extra_sweep
    if f["retest"] and H:
        ndist = (H / S2 - 1) * 100
        f["neck"] = last > H and ndist <= NECK_MAX; info["ndist"] = ndist
    brk_age = ext_neck = None
    if H:
        below = np.where(cl <= H)[0]; brk_age = len(cl) - 1 - int(below[-1]) if len(below) else len(cl)
        ext_neck = (last / H - 1) * 100
    run_pct = (last / min(S, S2) - 1) * 100
    e20, e30, e50 = ema(d["Close"], 20), ema(d["Close"], 30), ema(d["Close"], 50)
    gi = i0 + ci
    below = d["Close"].iloc[gi] < min(e20.iloc[gi], e30.iloc[gi], e50.iloc[gi])
    vw = anchored_vwap(post)
    vwap = float(vw.iloc[-1]) if pd.notna(vw.iloc[-1]) else None
    r20 = last >= e20.iloc[-1] * (1 - NEAR); rv = vwap is not None and last >= vwap * (1 - NEAR)
    f["ema"] = bool(below and r20 and rv)
    info.update(f=f, note=note, brk_age=brk_age, ext_neck=ext_neck, run_pct=run_pct, H=H, bounce=bounce, S2=S2, pattern=pattern, sweep=sweep, sweep_info=sweep_info, hold_after=hold_after,
                ema=(float(e20.iloc[-1]), float(e30.iloc[-1]), float(e50.iloc[-1])), vwap=vwap, r20=bool(r20), rv=bool(rv))
    return None, info


def tf4h(t):
    """فريم 4 ساعات من أحجام ساعة فعلية؛ بداية تجميع الجلسة 09:30 نيويورك."""
    try:
        h = yf.download(t, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
        h = h.dropna(subset=["Close"])
        if h.index.tz is not None:
            h.index = h.index.tz_convert("America/New_York")
        return h.resample("4h", origin="start_day", offset="9h30min").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": lambda v: v.sum() if v.notna().all() else np.nan}).dropna(subset=["Open", "High", "Low", "Close"])
    except Exception as e:
        print("4h", t, e); return None


def sweep_4h(h4, S, since):
    """سحب سيولة على 4 ساعات: شمعة كسرت الدعم بذيلها وأغلقت فوقه."""
    if h4 is None or S is None: return None
    idx = h4.index.tz_localize(None) if h4.index.tz is not None else h4.index
    x = h4[idx >= since]; m = (x["Low"] < S * 0.998) & (x["Close"] >= S)
    if not m.any(): return None
    r = x[m].iloc[-1]
    return {"date": str(x[m].index[-1])[:16], "low": float(r["Low"]), "close": float(r["Close"]), "tf": "4 ساعات"}


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
    i = {}
    for k in range(3):
        try:
            i = tk.info or {}
            if i: break
        except Exception as e:
            print("info", e)
        time.sleep(2 * (k + 1))
    fl, so, mc = i.get("floatShares"), i.get("sharesOutstanding"), i.get("marketCap")
    if not so or not mc:
        try:
            fi = tk.fast_info
            so = so or fi["shares"]; mc = mc or fi["marketCap"]
        except Exception as e:
            print("fast_info", e)
    fl = fl or so
    return {"company": i.get("shortName") or i.get("longName") or "", "float": fl, "float_exact": bool(i.get("floatShares")), "float_source": "yahoo_float" if i.get("floatShares") else ("yahoo_outstanding" if so else "unknown"),
            "shares_outstanding": so, "market_cap": mc}


def is_moved(f, info):
    """السهم اخترق خط العنق لكنه انطلق بالفعل: ابتعد عن خط العنق أكثر من EXT_NECK_MAX، أو مرّ على الاختراق أكثر من BRK_AGE_MAX جلسات."""
    if not f.get("neck"): return False
    return bool((info["ext_neck"] is not None and info["ext_neck"] > EXT_NECK_MAX)
                or (info["brk_age"] is not None and info["brk_age"] > BRK_AGE_MAX))


def make_targets(last, H, top, low, F):
    """أهداف موزعة فوق السعر: مقاومة/خط العنق ثم فيبو لموجة الهبوط، والأخير = أعلى قمة بعد التقسيم."""
    cands = [(H, "مقاومة / خط العنق")] if H and H > last * 1.02 else []
    for r, nm in ((0.382, "فيبو 38.2%"), (0.5, "فيبو 50%"), (0.618, "فيبو 61.8%"), (0.786, "فيبو 78.6%")):
        cands.append((low + (top - low) * r, nm))
    out = []
    for lv, nm in sorted(cands):
        if lv <= last * 1.02 or lv >= F * 0.97: continue
        if out and lv < out[-1]["level"] * 1.04: continue
        out.append({"level": lv, "label": nm, "final": False})
    out = out[:3]
    if F > last * 1.02: out.append({"level": F, "label": "الهدف الأخير — أعلى قمة بعد التقسيم", "final": True})
    return out


def clean(o):
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)): return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 6)
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.bool_): return bool(o)
    return o


def build_signal(t, d, sdate, ratio, info, today):
    tk = yf.Ticker(t); fu = fundamentals(tk); warns, cats = news_block(tk, today)
    f = dict(info["f"]); f["news"] = not warns
    since = d.index[d.index.searchsorted(sdate)]
    h4 = tf4h(t); S, S2, H = info.get("S"), info.get("S2"), info.get("H")
    sweep_info = info.get("sweep_info") or sweep_4h(h4, S, pd.Timestamp(since)); sweep = sweep_info is not None
    rsi4 = float(rsi(h4["Close"]).iloc[-1]) if h4 is not None and len(h4) > 20 else None
    score = sum(p for k, _, p in CK if f.get(k)); stage = None
    for th, name in STAGES:
        if score >= th: stage = name; break
    if stage == "جاهز فنيًا" and not (f["neck"] and f["retest"] and f["news"]): stage = "شبه جاهز"
    if stage and is_moved(f, info): stage = "انطلق بالفعل"
    if not stage: return None, score
    note = info.get("note", {}); last = info["last"]
    checklist = [{"key": k, "rule": r, "points": p, "status": bool(f.get(k)), "note": note.get(k, "")} for k, r, p in CK]
    low_ref = min(S, S2) if S2 else S
    targets = make_targets(last, H, info["top"], info["low_all"], info["F"])
    plan = {"entry_price": round(last, 4), "stop_loss": round(low_ref * 0.98, 4)} if stage == "جاهز فنيًا" else {}
    ch = d.tail(30)
    chart = [{"date": str(i.date()), "open": r.Open, "high": r.High, "low": r.Low, "close": r.Close} for i, r in ch.iterrows()]
    signal = {"ticker": t, "price": last, **fu, "split": {"date": str(sdate.date()), "ratio": ratio, "days_since": (today - sdate.date()).days, "close_on_split_day": info["c0"]},
            "change_since_split_pct": info["chg"], "max_drawdown_pct": info["dd"], "rsi_min": info["rsi_min"], "support": S,
            "support_hold_sessions": info["hold"], "resistance": H, "resistance_bounce_pct": info.get("bounce"),
            "neckline": H if f["retest"] else None, "neckline_distance_pct": info.get("ndist"), "pattern_type": info["pattern"],
            "retest_hold_sessions": info["hold_after"], "run_pct": info["run_pct"], "peak_run_pct": info["peak_run"], "ext_neck_pct": info["ext_neck"], "breakout_age": info["brk_age"], "liquidity_sweep": sweep, "sweep": sweep_info, "targets": targets, "final_target": {"level": info["F"], "date": info["F_date"]}, "stage": stage, "readiness_score": score,
            "checklist": checklist, "missing_conditions": [r for k, r, _ in CK if not f.get(k)], "plan": plan,
            "indicators": {"rsi": info["rsi_now"], "rsi_4h": rsi4, "ema20": info["ema"][0], "ema30": info["ema"][1], "ema50": info["ema"][2],
                           "vwap": info["vwap"], "ema20_reclaim": info["r20"], "vwap_reclaim": info["rv"]},
            "news": {"warnings": warns, "catalysts": cats}, "has_warning": bool(warns), "has_upcoming_catalyst": bool(cats), "chart": chart}
    return attach_analytics(signal, d, h4), score


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    syms = universe(); print("universe", len(syms))
    if not syms:
        raise RuntimeError("Universe unavailable; previous data.json preserved")
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
            if reason not in ("rose_over_20", "no_data"): diag["rise_ok_pass"] += 1
            if reason in (None, "target_achieved"): diag["drop_rsi_pass"] += 1
            if reason is None and info["hold"] >= HOLD_MIN: diag["support_hold_pass"] += 1
            sig = None
            if reason is None:
                sig, score = build_signal(t, d, sdate, ratio, info, today)
                if sig is None: reason = "support_broken"
            if sig: signals.append(sig); continue
            reason = reason or "unknown"; reasons[reason] = reasons.get(reason, 0) + 1
            if len(near) < 10 and info and reason != "target_achieved":
                near.append({"ticker": t, "price": float(d["Close"].iloc[-1]), "days_since_split": (today - sdate.date()).days,
                             "change_since_split_pct": info.get("chg", 0), "float": fundamentals(yf.Ticker(t))["float"], "reason": reason})
        except Exception as e:
            print("eval", t, e); reasons["error"] = reasons.get("error", 0) + 1
    if not got:
        raise RuntimeError("No Yahoo frames fetched; previous data.json preserved")
    if cand and reasons.get("error", 0) == len(cand):
        raise RuntimeError("All candidate evaluations failed; previous data.json preserved")
    diag = {"symbols_total": total, **diag, "data_coverage_pct": round(100 * got / max(1, total)),
            "reject_reasons": reasons, "near_misses": near}
    save_payload(build_payload(signals, diag))
    print("signals", len(signals), "candidates", len(cand))


def build_payload(signals, diagnostics, updated_at=None):
    """Do not duplicate signals or fabricate coverage when unavailable."""
    signals = sorted(signals, key=lambda s: (-s.get("readiness_score", 0), -s.get("confirm_score", 0), s["ticker"]))
    def count(test):
        return sum(bool(test(s)) for s in signals)
    states = {key: count(lambda s: s.get("stage") == label) for key, label in STATE_LABELS.items()}
    valid = [s["liquidity"] for s in signals if (s.get("liquidity") or {}).get("available")]
    stats = {
        "ready": states["READY"], "semi": states["SEMI"], "watch": states["WATCH"], "moved": states["MOVED"],
        "catalysts": count(lambda s: s.get("has_upcoming_catalyst")), "warnings": count(lambda s: s.get("has_warning")),
        "dormant_bases": count(lambda s: (s.get("dormant_base") or {}).get("is_dormant")),
        "liquidity_sweeps": count(lambda s: s.get("liquidity_sweep")),
        "liquidity_available": len(valid), "liquidity_unavailable": len(signals) - len(valid),
        "accumulation_detected": count(lambda s: (s.get("liquidity") or {}).get("accumulation_detected")),
        "bullish_divergence": count(lambda s: ((s.get("liquidity") or {}).get("divergence") or {}).get("direction") == "positive"),
        "bearish_divergence": count(lambda s: ((s.get("liquidity") or {}).get("divergence") or {}).get("direction") == "negative"),
        "avg_cmf": sum(l["cmf"] for l in valid) / len(valid) if valid else None,
    }
    rules = {
        "split_window_days": [WIN_MIN, WIN_MAX], "max_rise_from_split_pct": MAX_RISE, "min_drop_pct": DROP_MIN,
        "rsi_oversold": RSI_OS, "support_hold_sessions": HOLD_MIN, "resistance_bounce_pct": RES_MIN,
        "retest_near_pct": RETEST_NEAR, "higher_low_pct": HIGHER_LOW, "hold_after_retest_sessions": HOLD_AFTER_MIN,
        "neckline_max_dist_pct": NECK_MAX, "max_extension_above_neck_pct": EXT_NECK_MAX,
        "exclude_run_from_support_pct": EXCLUDE_RUN, "cmf_period": CMF_PERIOD, "cmf_fast_period": CMF_FAST, "mfi_period": MFI_PERIOD,
        "divergence_cmf_delta": .04, "divergence_mfi_delta": 5, "pivot_radius": 2, "pivot_min_separation": 4,
        "composite_weights": [70, 30], "timeframes": ["1D", "4H"],
    }
    return {
        "schema_version": 3, "updated_at": updated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(signals), "stats": stats, "signals": signals, "diagnostics": diagnostics,
        "states": STATE_LABELS, "rules": rules,
        "conditions": [{"key": k, "label": r, "weight": w, "core": True} for k, r, w in CK]
                      + [{"key": k, "label": r, "weight": w, "core": False} for k, r, w in CONFIRM_DEFS],
        "analysis": {"engine": "cmf-liquidity-v1", "recalculated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "note": "وقت إعادة الحساب منفصل عن وقت مسح السوق. لا أحجام اصطناعية أو قراءات 4H مستنتجة من اليومي."},
    }


def save_payload(payload):
    encoded = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    for file in (Path("data.json"), Path("docs/data.json")):
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(file)


def enrich_from_disk(input_path="data.json", metadata_path=None):
    """Offline upgrade. OHLC-only snapshots deliberately keep money-flow values null."""
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
    names = {}
    if metadata_path:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        rows = metadata if isinstance(metadata, list) else metadata.get("all", metadata.get("tickers", []))
        names = {r.get("ticker") or r.get("symbol"): r["company"] for r in rows if r.get("company")}
    signals = []
    for s in raw.get("signals", []):
        if not s.get("chart"):
            signals.append(s)
            continue
        copy = {**s, "company": s.get("company") or names.get(s["ticker"], "")}
        h4 = frame_from_bars(s["chart_4h"]) if s.get("chart_4h") else None
        signals.append(attach_analytics(copy, frame_from_bars(s["chart"]), h4, snapshot=True))
    payload = build_payload(signals, raw.get("diagnostics") or {}, raw.get("updated_at"))
    save_payload(payload)
    print("enriched", len(signals), "| liquidity available", payload["stats"]["liquidity_available"],
          "| pending actual volume", payload["stats"]["liquidity_unavailable"])
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reverse-split scanner and honest offline CMF enrichment")
    parser.add_argument("--enrich", action="store_true", help="recalculate saved data without accessing Yahoo")
    parser.add_argument("--input", default="data.json", help="snapshot to enrich")
    parser.add_argument("--metadata", help="optional JSON containing observed company names (not volume proxies)")
    args = parser.parse_args()
    if args.enrich:
        enrich_from_disk(args.input, args.metadata)
    else:
        main()

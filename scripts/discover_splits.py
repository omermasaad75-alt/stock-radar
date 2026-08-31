#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اكتشاف تلقائي للأسهم اللي عملت تقسيم عكسي حديثًا (بدون إدخال رموز يدوي) —
عبر BusinessQuant corporate actions API. يكتب قائمة المرشحين في
reverse_split_candidates.json ليقرأها scanner.py بعده.

المصدر ده هو نفسه اللي مستخدم في مستودعك (reverse-split-radar) وشغال معاك
مجانًا حاليًا — نفس الفكرة بالظبط، بس منظّف ومدمج مع استراتيجيتنا.
"""

import os
import json
from datetime import datetime, timedelta

import requests

MIN_DAYS = 20
MAX_DAYS = 50
API_URL = "https://data.businessquant.com/corporate_actions"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(REPO_ROOT, "reverse_split_candidates.json")


def valid_symbol(symbol):
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return False
    if symbol.endswith("F"):        # استبعاد رموز OTC اللي تنتهي بـ F
        return False
    if not symbol.isalnum():
        return False
    return True


def discover_reverse_splits():
    print("=" * 60)
    print("🚨 اكتشاف التقسيمات العكسية — BusinessQuant")
    print("=" * 60)

    api_key = os.getenv("BUSINESSQUANT_API_KEY")
    if not api_key:
        print("❌ لم يتم العثور على BUSINESSQUANT_API_KEY في متغيرات البيئة")
        return []

    today = datetime.now().date()
    start_date = today - timedelta(days=MAX_DAYS)
    end_date = today - timedelta(days=MIN_DAYS)

    print(f"📅 نافذة البحث: من {start_date} إلى {end_date} (٪{MIN_DAYS}-{MAX_DAYS} يوم بعد التقسيم)")

    params = {
        "action": "split",
        "from_date": str(start_date),
        "till_date": str(end_date),
        "limit": 10000,
        "api_key": api_key,
    }

    try:
        response = requests.get(API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", [])
        print(f"📊 عدد عمليات التقسيم التي رجعها المصدر: {len(data)}")

        candidates = []
        for item in data:
            ticker = str(item.get("ticker", "")).strip().upper()
            action = str(item.get("action", "")).strip().lower()
            notes = str(item.get("notes", "")).strip()
            date_text = str(item.get("date", "")).strip()

            if not valid_symbol(ticker):
                continue
            if action != "split":
                continue
            if "reverse split" not in notes.lower():
                continue
            try:
                split_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except Exception:
                continue

            days_passed = (today - split_date).days
            if not (MIN_DAYS <= days_passed <= MAX_DAYS):
                continue

            candidates.append({
                "symbol": ticker,
                "split_date": str(split_date),
                "days": days_passed,
                "reverse_split": notes,
                "company": str(item.get("name", "")),
                "model_hint": "split",
            })

        # إزالة التكرار (آخر ظهور لكل رمز)
        unique = {c["symbol"]: c for c in candidates}
        candidates = sorted(unique.values(), key=lambda x: (x["days"], x["symbol"]))

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)

        print()
        if not candidates:
            print("⚪ لا توجد أسهم حاليًا ضمن نافذة 20-50 يوم")
        else:
            for c in candidates:
                print(f"🟢 {c['symbol']} | التقسيم: {c['split_date']} | العمر: {c['days']} يوم")
        print()
        print(f"📌 عدد المرشحين: {len(candidates)} — محفوظة في {OUTPUT_FILE}")
        return candidates

    except Exception as e:
        print(f"❌ خطأ أثناء الاتصال بـ BusinessQuant: {e}")
        return []


if __name__ == "__main__":
    discover_reverse_splits()

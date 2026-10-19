"""סוכן ריברס: לכל מסגרת שהכלי לא מצא — חיפוש ממוקד בכל ערוץ מקוון.

קורא את קובץ העבודה "ריברס ידני - כל הערוצים.xlsx" (גיליון כיוון 1),
מריץ חיפושי Google דרך Serper (gl=il, hl=iw), מדרג כל תוצאה ומחזיר לכל מסגרת
ולכל ערוץ את המועמד הטוב ביותר עם קישור וציון. התוצאות נכתבות לגיליון חדש
"סוכן – ממצאים" באותו קובץ; עמודות הבודקות לא נוגעות.

הסוכן רושם נוכחות עסקית של המסגרת בלבד (דף, מודעה, רישום באינדקס).

הרצה:
    set -a && source .env && set +a      # SERPER_API_KEY
    python3 -m mvp.agent_reverse --city "קרית אתא" --limit 10     # פיילוט
    python3 -m mvp.agent_reverse --priority                        # כל שורות העדיפות
"""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .config import RAW_DIR
from .normalize import clean, name_key, name_sim

WORKBOOK = Path.home() / (
    "Library/CloudStorage/OneDrive-SocialFinanceIsrael/Data & Measurement site - Documents/"
    "ניהול פעילות הייעוץ/פרויקטי ייעוץ שלטון מקומי/יד הנדיב 24-25- מענים לתושבים/"
    "אסטרטגיית נתונים לגיל הרך/4מדידה והערכה/שיפורים/ממשק מיפוי מסגרות/4 MVP דאטה סט/ריברס וסוכן/"
    "ריברס ידני - כל הערוצים.xlsx"
)
SHEET_IN = "כיוון 1 – מהרשימה לערוץ"
SHEET_OUT = "סוכן – ממצאים"
CACHE = RAW_DIR / "serper_cache.json"

CITY_ALIASES = {"קרית אתא": ["קרית אתא", "קריית אתא"], "חדרה": ["חדרה"], "נתניה": ["נתניה"]}
CHILDCARE = ["משפחתון", "גן", "פעוטון", "מעון", "תינוקות", "פעוטות", "גיל הרך", "מטפלת", "סייעת"]

# ערוץ → מגביל אתרים (ריק = כל הרשת)
CHANNELS = {
    "גוגל – שם + עיר": "",
    "אינסטגרם": "site:instagram.com",
    "פייסבוק": "site:facebook.com",
    "טיקטוק": "site:tiktok.com",
    "דרושים": "(site:alljobs.co.il OR site:drushim.co.il OR site:yad2.co.il OR site:jobmaster.co.il)",
    "אינדקסים": "(site:easy.co.il OR site:b144.co.il OR site:baderechlagan.co.il OR site:d.co.il)",
}


# ── חיפוש ────────────────────────────────────────────────────────────────────
def _load_cache():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _save_cache(c):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


def search(q, cache, key):
    h = hashlib.md5(q.encode()).hexdigest()
    if h in cache:
        return cache[h]["organic"]
    r = requests.post("https://google.serper.dev/search",
                      headers={"X-API-KEY": key, "Content-Type": "application/json"},
                      json={"q": q, "gl": "il", "hl": "iw", "num": 10}, timeout=30)
    r.raise_for_status()
    organic = [{"title": o.get("title", ""), "link": o.get("link", ""), "snippet": o.get("snippet", "")}
               for o in r.json().get("organic", [])]
    cache[h] = {"q": q, "organic": organic}
    time.sleep(0.3)
    return organic


def queries(name, city, street):
    """לכל ערוץ: חיפוש לפי שם, ואם יש רחוב — גם לפי כתובת."""
    out = {}
    for ch, site in CHANNELS.items():
        qs = [f'"{name}" {city} {site}'.strip()]
        if ch == "גוגל – שם + עיר" and street:
            qs.append(f'משפחתון OR גן "{street}" {city}')
        out[ch] = qs
    return out


# ── דירוג ────────────────────────────────────────────────────────────────────
PERSONAL = re.compile(r"facebook\.com/(public/|people/|profile\.php)|People named|Join Facebook to connect")


def score(hit, name, city, street):
    """0–100. השם המלא כצירוף + עיר + מילת גיל רך. פרופיל אישי נפסל."""
    text = clean(f"{hit['title']} {hit['snippet']}")
    if PERSONAL.search(f"{hit['link']} {hit['snippet']}"):
        return 0
    if clean(name) and clean(name) in text:
        s = 50
    else:
        s = 35 * max(name_sim(name, hit["title"]), _token_cover(name, text))
    if any(a in text for a in CITY_ALIASES.get(city, [city])):
        s += 20
    if any(w in text for w in CHILDCARE):
        s += 20
    elif s < 50:
        s -= 20
    if street and clean(street) in text:
        s += 10
    return max(0, min(round(s), 100))


def _token_cover(name, text):
    toks = name_key(name).split()
    if not toks:
        return 0.0
    t = text.lower()
    return sum(1 for w in toks if w in t) / len(toks)


def verdict(s):
    if s >= 85:
        return "סביר שנמצא"
    if s >= 60:
        return "לבדיקה"
    return "לא נמצא"


# ── הרצה ─────────────────────────────────────────────────────────────────────
def read_rows(city=None, priority=False, limit=None):
    wb = load_workbook(WORKBOOK, read_only=True)
    ws = wb[SHEET_IN]
    it = ws.iter_rows(values_only=True)
    head = next(it)
    ix = {h: i for i, h in enumerate(head)}
    rows = []
    for r in it:
        if city and r[ix["רשות"]] != city:
            continue
        if priority and r[ix["בעדיפות"]] != "כן":
            continue
        rows.append({"city": r[ix["רשות"]], "name": r[ix["שם במיפוי הרשות"]],
                     "type": r[ix["סוג במיפוי"]], "address": r[ix["כתובת"]] or ""})
    return rows[:limit] if limit else rows


def street_of(address):
    m = re.match(r"\s*([^\d]+?)\s*\d", address or "")
    return m.group(1).strip() if m else ""


def run(rows, key):
    cache = _load_cache()
    results = []
    try:
        for i, row in enumerate(rows, 1):
            street = street_of(row["address"])
            for ch, qs in queries(row["name"], row["city"], street).items():
                best, best_q = None, qs[0]
                for q in qs:
                    for hit in search(q, cache, key):
                        s = score(hit, row["name"], row["city"], street)
                        if not best or s > best["score"]:
                            best, best_q = {**hit, "score": s}, q
                results.append({**row, "channel": ch, "query": best_q,
                                "score": best["score"] if best else 0,
                                "verdict": verdict(best["score"]) if best else "לא נמצא",
                                "title": best["title"] if best else "",
                                "link": best["link"] if best else "",
                                "snippet": best["snippet"] if best else ""})
            print(f"{i}/{len(rows)} {row['city']} · {row['name']}")
    finally:
        _save_cache(cache)
    return results


def write(results):
    wb = load_workbook(WORKBOOK)
    if SHEET_OUT in wb.sheetnames:
        del wb[SHEET_OUT]
    ws = wb.create_sheet(SHEET_OUT)
    ws.sheet_view.rightToLeft = True
    cols = ["רשות", "שם במיפוי הרשות", "סוג במיפוי", "ערוץ", "הכרעת הסוכן", "ציון",
            "כותרת", "קישור", "תקציר", "חיפוש", "אימות אנושי"]
    keys = ["city", "name", "type", "channel", "verdict", "score", "title", "link", "snippet", "query"]
    for j, h in enumerate(cols, 1):
        c = ws.cell(1, j, h)
        c.font = Font(bold=True, color="FFFFFF", name="Arial")
        c.fill = PatternFill("solid", fgColor="042C53")
    fills = {"סביר שנמצא": "C9EBD6", "לבדיקה": "FEF6DD"}
    for i, r in enumerate(results, 2):
        for j, k in enumerate(keys, 1):
            c = ws.cell(i, j, r[k])
            c.alignment = Alignment(wrap_text=True, vertical="top")
        if r["verdict"] in fills:
            ws.cell(i, 5).fill = PatternFill("solid", fgColor=fills[r["verdict"]])
    for j, w in enumerate([10, 24, 14, 16, 13, 7, 30, 34, 50, 34, 12], 1):
        ws.column_dimensions[chr(64 + j)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:K{len(results) + 1}"
    wb.save(WORKBOOK)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--priority", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise SystemExit("חסר SERPER_API_KEY ב-.env")
    rows = read_rows(a.city, a.priority, a.limit)
    print(f"{len(rows)} מסגרות × {len(CHANNELS)} ערוצים")
    res = run(rows, key)
    write(res)
    found = {(r["city"], r["name"]) for r in res if r["verdict"] == "סביר שנמצא"}
    check = {(r["city"], r["name"]) for r in res if r["verdict"] == "לבדיקה"} - found
    print(f"סביר שנמצאו: {len(found)} · לבדיקה: {len(check)} · מתוך {len(rows)}")


if __name__ == "__main__":
    main()

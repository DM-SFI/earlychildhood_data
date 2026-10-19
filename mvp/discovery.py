"""שכבה 2 — גילוי מסגרות בעיר בלי רשימת ייחוס.

מחפש בגוגל (Serper, gl=il) ניסוחים שהורים ומטפלות כותבים — בקבוצות פייסבוק ציבוריות,
אינסטגרם, טיקטוק ואינדקסים — ושומר את כל התוצאות הרלוונטיות לעיר.
חילוץ שמות המסגרות מהתוצאות והכרעה אם זו מסגרת לידה-3 בעיר נעשים בשלב הבא
(candidates.py / מודל שפה).

הרצה:
    set -a && source .env && set +a
    python3 -m mvp.discovery --city דימונה
פלט: data/output/discovery_<עיר>.json
"""
import argparse
import json
import os
import re

from .agent_reverse import _load_cache, _save_cache, search
from .config import CITIES, OUT_DIR
from .normalize import clean

CHILDCARE = re.compile(r"משפחתון|פעוטון|מעון|גן ביתי|גן פרטי|גן ילדים|גנון|תינוקי|פעוטות|תינוקות|מטפלת|daycare|kindergarten")

# ניסוחים שחוזרים בפוסטים של מטפלות והורים. {c} = שם העיר.
TEMPLATES = {
    "פייסבוק – קבוצות": [
        "site:facebook.com/groups משפחתון {c}",
        'site:facebook.com/groups "הרשמה לשנה הבאה" משפחתון {c}',
        "site:facebook.com/groups ממליצה משפחתון {c}",
        "site:facebook.com/groups המלצות גן פעוטות {c}",
        'site:facebook.com/groups "גן ביתי" {c}',
        'site:facebook.com/groups "מקום פנוי" משפחתון {c}',
        "site:facebook.com/groups פעוטון {c}",
        "site:facebook.com/groups מטפלת תינוקות {c} מקומות",
    ],
    "פייסבוק – דפים": [
        "site:facebook.com משפחתון {c} -groups",
        "site:facebook.com פעוטון {c} -groups",
    ],
    "אינסטגרם": [
        "site:instagram.com משפחתון {c}",
        "site:instagram.com פעוטון {c}",
        'site:instagram.com "גן" תינוקות {c}',
    ],
    "טיקטוק": ["site:tiktok.com משפחתון {c}"],
    "אינדקסים ורשת": [
        "משפחתון {c}",
        "משפחתון פרטי {c}",
        "פעוטון {c} גילאי 0-3",
        "גן ביתי לתינוקות {c}",
    ],
}
PAGES = 2


def discover(city, key):
    cache = _load_cache()
    aliases = CITIES[city]["aliases"]
    hits, seen = [], set()
    try:
        for channel, templates in TEMPLATES.items():
            for t in templates:
                q = t.format(c=city)
                for page in range(1, PAGES + 1):
                    for h in search(q, cache, key, page=page):
                        text = clean(f"{h['title']} {h['snippet']}")
                        if h["link"] in seen or not CHILDCARE.search(text):
                            continue
                        seen.add(h["link"])
                        hits.append({**h, "channel": channel, "query": q,
                                     "city_mentioned": any(a in text for a in aliases)})
    finally:
        _save_cache(cache)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=list(CITIES))
    a = ap.parse_args()
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise SystemExit("חסר SERPER_API_KEY ב-.env")
    hits = discover(a.city, key)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"discovery_{a.city}.json"
    path.write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {}
    for h in hits:
        by[h["channel"]] = by.get(h["channel"], 0) + 1
    print(f"{a.city}: {len(hits)} תוצאות רלוונטיות · עם אזכור העיר: {sum(h['city_mentioned'] for h in hits)}")
    for ch, n in by.items():
        print(f"   {ch}: {n}")
    print(f"→ {path}")


if __name__ == "__main__":
    main()

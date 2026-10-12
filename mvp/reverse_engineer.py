"""Reverse engineering: לכל מסגרת שזוהתה ידנית ולא נמצאה אוטומטית — האם והיכן אפשר היה למצוא אותה?

לכל מסגרת שהוחמצה נבדק, לפי הסדר:
  1. האם הופיעה בתוצאות הגולמיות של Google / בדרך לגן ונפלה בסינון (ולמה)
  2. חיפוש ממוקד ב-Google Places לפי שם המסגרת + עיר
  3. חיפוש ממוקד לפי כתובת: איזה עסקים רשומים בגוגל בכתובת הזו
  4. האם מופיעה במקור מנהלי (חינוך/עבודה) בשם אחר — כלומר בעצם מפוקחת

הרצה:  set -a && source .env && set +a && python3 -m mvp.reverse_engineer
פלט:   data/output/ריברס_אינג'ינירינג.xlsx

הערת פרטיות: נשמר רק "נמצא/לא נמצא + איפה" ברמת מסגרת; לא נאסף מידע אישי נוסף.
"""
import json
import os
import re
import time

import pandas as pd
import requests

from .build import build_city
from .config import CITIES, OUT_DIR, RAW_DIR
from .evaluate import GROUND_TRUTH, match
from .normalize import clean, in_city, name_sim, split_address, street_sim
from .sources import GOOGLE_EXCLUDE, GOOGLE_INCLUDE, GOOGLE_TERMS, TODAY

KEY = os.environ.get("GOOGLE_API_KEY")
CACHE = RAW_DIR / TODAY / "reverse_cache.json"
UNSUPERVISED = re.compile(r"ביתית|משפחתון פרטי|משפחתון \(מאגר")
CHILDCARE = re.compile(r"משפחתון|פעוטון|מעון|גן|תינוק|פעוט|טף|קטנטנים|baby|daycare|kids")
NOT_CHILDCARE = re.compile(r"מכון|התפתחות|קליניק|רופא|חנות|שדרות")


def _cache():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _google(endpoint, params, cache):
    k = endpoint + json.dumps(params, ensure_ascii=False, sort_keys=True)
    if k not in cache:
        r = requests.get(f"https://maps.googleapis.com/maps/api/{endpoint}/json",
                         params={**params, "key": KEY, "language": "he"}, timeout=60)
        cache[k] = r.json()
        time.sleep(0.2)
    return cache[k]


def _is_hit(gt, name, address, aliases):
    st, num = split_address(address, aliases)
    ns = name_sim(gt["gt_name"], name)
    same_addr = street_sim(gt["gt_street"], st) >= 0.85 and gt["gt_num"] and gt["gt_num"] == num
    return ns >= 0.85 or (same_addr and ns >= 0.4) or (ns >= 0.6 and street_sim(gt["gt_street"], st) >= 0.85)


def _filter_reason(name, address, aliases, status=None):
    if not in_city(address, aliases):
        return "כתובת מחוץ לעיר / לא מזוהה"
    if status == "CLOSED_PERMANENTLY":
        return "מסומן בגוגל כסגור"
    if not GOOGLE_INCLUDE.search(name.lower()):
        return "השם לא מכיל מילת מפתח של גיל רך"
    if GOOGLE_EXCLUDE.search(name.lower()):
        return "השם נפסל במילות סינון"
    return "עבר סינון — לא הותאם באיחוד"


def diagnose(city, gt, records, cache):
    aliases = CITIES[city]["aliases"]
    town = CITIES[city]["mol_town"]
    d = {"נמצא בגוגל (גולמי)": "", "נמצא בבדרך לגן (גולמי)": "", "חיפוש ממוקד לפי שם": "",
         "עסקים בגוגל בכתובת": "", "מקור מנהלי בשם אחר": "", "שם בגוגל": "", "קטגוריות גוגל": ""}

    # 1. תוצאות גולמיות מההרצה (לפני סינון)
    gpath = RAW_DIR / TODAY / f"google_{town}.json"
    for p in (json.loads(gpath.read_text(encoding="utf-8")).values() if gpath.exists() else []):
        if _is_hit(gt, p.get("name", ""), p.get("formatted_address", ""), aliases):
            d["נמצא בגוגל (גולמי)"] = _filter_reason(p["name"], p.get("formatted_address", ""), aliases,
                                                     p.get("business_status"))
            d["שם בגוגל"] = p["name"]
            break
    bpath = RAW_DIR / TODAY / f"bdl_{town}.json"
    for c in (json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else []):
        if _is_hit(gt, c["name"], c["address"], aliases):
            why = ("גן עירייה" if "עירייה" in c["kind"] else
                   "גילאים 3+" if c["age_min"] and c["age_min"] >= 3 else
                   "מחוץ לעיר" if not in_city(c["address"], aliases) else "עבר סינון — לא הותאם")
            d["נמצא בבדרך לגן (גולמי)"] = why
            break

    # 4. מקור מנהלי בשם/כתובת דומים
    for r in records:
        if r["source"] in ("משרד החינוך", "זרוע העבודה") and _is_hit(gt, r["name"], r["address"] or "", aliases):
            d["מקור מנהלי בשם אחר"] = f"{r['source']}: {r['name']} ({r['source_type']})"
            break

    if not KEY:
        return d
    # 2. חיפוש ממוקד לפי שם
    res = _google("place/textsearch", {"query": f"{gt['gt_name']} {city}"}, cache).get("results", [])[:5]
    hit = next((p for p in res if _is_hit(gt, p["name"], p.get("formatted_address", ""), aliases)), None)
    if hit:
        d["חיפוש ממוקד לפי שם"] = "נמצא"
        d["שם בגוגל"] = d["שם בגוגל"] or hit["name"]
        d["קטגוריות גוגל"] = ", ".join(t for t in hit.get("types", []) if t not in ("point_of_interest", "establishment"))
    else:
        d["חיפוש ממוקד לפי שם"] = "לא נמצא"

    # 3. מה רשום בגוגל בכתובת עצמה
    if gt["gt_street"] and gt["gt_num"]:
        geo = _google("geocode", {"address": f"{gt['gt_address']}, {city}"}, cache).get("results", [])
        if geo:
            loc = geo[0]["geometry"]["location"]
            near = _google("place/nearbysearch", {"location": f"{loc['lat']},{loc['lng']}", "radius": 40},
                           cache).get("results", [])
            kids = [p["name"] for p in near
                    if CHILDCARE.search(p["name"].lower()) and not NOT_CHILDCARE.search(p["name"])]
            d["עסקים בגוגל בכתובת"] = ("עסק גיל רך: " + " | ".join(kids[:3])) if kids else \
                f"אין עסק גיל רך ({len(near)} עסקים אחרים)"
        else:
            d["עסקים בגוגל בכתובת"] = "כתובת לא אותרה"
    else:
        d["עסקים בגוגל בכתובת"] = "אין כתובת מלאה במיפוי"
    return d


def verdict(d):
    if d["מקור מנהלי בשם אחר"]:
        return "כנראה מפוקחת — מקור מנהלי באותה כתובת בשם אחר", "לבדוק ידנית; אם אותה מסגרת — שיפור התאמה"
    if d["נמצא בגוגל (גולמי)"] or d["נמצא בבדרך לגן (גולמי)"]:
        return "נשלפה אוטומטית ונפלה בסינון", "לתקן סינון: " + (d["נמצא בגוגל (גולמי)"] or d["נמצא בבדרך לגן (גולמי)"])
    if d["חיפוש ממוקד לפי שם"] == "נמצא":
        return "קיימת בגוגל — החיפוש הכללי לא שלף אותה", "להוסיף ניסוחי חיפוש / קטגוריות: " + (d["קטגוריות גוגל"] or "?")
    if d["עסקים בגוגל בכתובת"].startswith("עסק גיל רך"):
        return "בכתובת רשום עסק גיל רך בשם אחר", "חיפוש לפי מיקום (nearby) במקום לפי מילות מפתח"
    return "לא נמצאה בשום מקור אוטומטי", "רק מקורות רשותיים / ידניים / רישום יזום"


def run(cities=("קרית אתא", "חדרה")):
    cache = _cache()
    rows = []
    for city in cities:
        print(f"\n🏙️  {city}")
        records, facilities = build_city(city, verbose=False)
        for gt in GROUND_TRUTH[city]():
            f, why = match(gt, facilities)
            unsup = bool(UNSUPERVISED.search(str(gt["gt_type"])))
            if f and not unsup:
                continue
            row = {"רשות": city, "שם במיפוי": gt["gt_name"], "סוג במיפוי": gt["gt_type"],
                   "כתובת במיפוי": gt["gt_address"], "לא מפוקחת?": "כן" if unsup else "לא"}
            if f:
                row.update({"אבחנה": "נמצאה אוטומטית", "מה לשנות": "—", "מקורות": f["מקורות"]})
            else:
                d = diagnose(city, gt, records, cache)
                v, fix = verdict(d)
                row.update({"אבחנה": v, "מה לשנות": fix, **d})
            rows.append(row)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    df = pd.DataFrame(rows)
    summ = df.groupby(["לא מפוקחת?", "אבחנה"]).size().rename("כמות").reset_index() \
        .sort_values(["לא מפוקחת?", "כמות"], ascending=[False, False])
    path = OUT_DIR / "ריברס_אינג'ינירינג.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summ.to_excel(xw, sheet_name="סיכום", index=False)
        df.to_excel(xw, sheet_name="פירוט לפי מסגרת", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 26
    print(summ.to_string(index=False))
    print(f"→ {path}")
    return df


if __name__ == "__main__":
    run()

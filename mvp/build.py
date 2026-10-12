"""איחוד הרשומות מכל המקורות לטבלת מסגרות אחת לכל עיר (לפי מילון הנתונים).

הרצה (מתיקיית הריפו):
    source .env && python3 -m mvp.build            # כל ערי הפיילוט
    python3 -m mvp.build --city נתניה
"""
import argparse
import hashlib
from datetime import date

import pandas as pd

from .config import CITIES, OUT_DIR
from .normalize import name_key, name_sim, phone_key, street_sim
from .sources import ALL_SOURCES

ADMIN = {"משרד החינוך", "זרוע העבודה", "משרד הבריאות", "משרד הרווחה"}
SOURCE_RANK = {"משרד החינוך": 0, "זרוע העבודה": 1, "משרד הבריאות": 2, "משרד הרווחה": 3, "בדרך לגן": 4,
               "Google Places": 5}

# סוג מסגרת -> (גורם מפקח, גורם רושם). לוגיקה מתוך "עזר אפיון טכני" — שאלה פתוחה #4 במילון.
TYPE_LOGIC = {
    "מעון סמל": ("משרד החינוך", "זרוע העבודה"),
    "מעון פרטי": ("משרד החינוך", "המסגרת"),
    "משפחתון סמל": ("זרוע העבודה", "זרוע העבודה"),
    "מעון יום שיקומי": ("משרד הבריאות", "משרד הבריאות"),
    "מסגרת רווחה": ("משרד הרווחה", "משרד הרווחה"),
    "חשד למסגרת לא מפוקחת": ("לא מפוקח", "לא נדרש"),
}


# ── איחוד ישויות ────────────────────────────────────────────────────────────
class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _conflict(members_a, members_b):
    """שתי קבוצות שמכילות סמלים שונים מאותו משרד הן מסגרות שונות (גם אם באותה כתובת)."""
    for field in ("moe_symbol", "mol_symbol", "welfare_id"):
        sa = {m[field] for m in members_a if m[field]}
        sb = {m[field] for m in members_b if m[field]}
        if sa and sb and not (sa & sb):
            return True
    return False


def same_place(a, b):
    """האם שתי רשומות מתארות את אותה מסגרת. מחזיר את סיבת ההתאמה או None."""
    if a["moe_symbol"] and a["moe_symbol"] == b["moe_symbol"]:
        return "סמל חינוך"
    if a["mol_symbol"] and a["mol_symbol"] == b["mol_symbol"]:
        return "סמל עבודה"
    ns = name_sim(a["name"], b["name"])
    ss = street_sim(a["street"], b["street"])
    same_num = a["house_number"] and a["house_number"] == b["house_number"]
    if ss >= 0.85 and same_num and ns >= 0.5:
        return "כתובת+שם"
    pa, pb = phone_key(a["phone"]), phone_key(b["phone"])
    if pa and pa == pb and ns >= 0.5:
        return "טלפון+שם"
    if ns >= 0.85 and ss >= 0.7:
        return "שם+רחוב"
    if ns >= 0.95 and (not a["street"] or not b["street"]):
        return "שם"
    if ns >= 0.6 and ss >= 0.85 and (a["source"] not in ADMIN or b["source"] not in ADMIN):
        # שם שיווקי דומה באותו רחוב (מספר בית חסר או שונה במקור המסחרי)
        return "שם דומה+רחוב"
    if ss >= 0.85 and same_num and ("משרד הרווחה" in (a["source"], b["source"])
                                    or a["source"] not in ADMIN or b["source"] not in ADMIN):
        # מקור מסחרי/רווחה באותה כתובת בדיוק כמו מסגרת מוכרת — כמעט תמיד אותה מסגרת בשם שיווקי
        return "כתובת"
    return None


def resolve(records):
    dsu = DSU(len(records))
    reasons = {}
    order = sorted(range(len(records)), key=lambda i: SOURCE_RANK[records[i]["source"]])
    for x in range(len(order)):
        for y in range(x + 1, len(order)):
            i, j = order[x], order[y]
            ri, rj = dsu.find(i), dsu.find(j)
            if ri == rj:
                continue
            why = same_place(records[i], records[j])
            if not why:
                continue
            ga = [records[k] for k in range(len(records)) if dsu.find(k) == ri]
            gb = [records[k] for k in range(len(records)) if dsu.find(k) == rj]
            if _conflict(ga, gb):
                continue
            dsu.union(i, j)
            reasons[(i, j)] = why
    groups = {}
    for i in range(len(records)):
        groups.setdefault(dsu.find(i), []).append(records[i])
    return list(groups.values())


# ── בניית רשומת מסגרת לפי מילון הנתונים ──────────────────────────────────────
def _first(members, field):
    for m in sorted(members, key=lambda m: SOURCE_RANK[m["source"]]):
        if m.get(field) not in (None, "", "None"):
            return m[field]
    return None


def facility_type(members):
    by = {m["source"]: m for m in members}
    if "משרד החינוך" in by:
        return by["משרד החינוך"]["source_type"]
    if "זרוע העבודה" in by:
        return by["זרוע העבודה"]["source_type"]
    if "משרד הבריאות" in by:
        return "מעון יום שיקומי"
    if "משרד הרווחה" in by:
        return "מסגרת רווחה"
    return "חשד למסגרת לא מפוקחת"


def facility_id(members, city, ftype):
    moe = _first(members, "moe_symbol")
    if moe:
        return f"MOE-{moe}"
    mol = _first(members, "mol_symbol")
    if mol:
        return f"MOL-{mol}"
    wel = _first(members, "welfare_id")
    if wel:
        return f"WEL-{wel}"
    # סמל פיקטיבי יציב: אותה מסגרת תקבל אותו סמל בכל הרצה כל עוד השם והכתובת לא השתנו
    base = f"{name_key(_first(members, 'name'))}|{_first(members, 'street')}|{_first(members, 'house_number')}"
    return f"SFI-{CITIES[city]['mol_town']}-{hashlib.sha1(base.encode()).hexdigest()[:6].upper()}"


def to_facility(members, city):
    ftype = facility_type(members)
    sources = sorted({m["source"] for m in members}, key=SOURCE_RANK.get)
    is_admin = any(s in ADMIN for s in sources)
    commercial = [s for s in sources if s not in ADMIN]
    supervisor, registrar = TYPE_LOGIC[ftype]
    if is_admin:
        status, reliability = "מאומת – מקור מנהלי", "גבוהה"
    elif len(commercial) >= 2:
        status, reliability = "ליד – לאימות", "בינונית"
    else:
        status, reliability = "ליד – לאימות", "נמוכה"
    links = [m["extra"].get("url") for m in members if m.get("extra") and m["extra"].get("url")]
    return {
        "מזהה מסגרת": facility_id(members, city, ftype),
        "שם מסגרת": _first(members, "name"),
        "סוג מסגרת": ftype,
        "סמל משרד החינוך": _first(members, "moe_symbol"),
        "סמל זרוע העבודה": _first(members, "mol_symbol"),
        "מזהה משרד הרווחה": _first(members, "welfare_id"),
        "רשות": city,
        "רחוב": _first([m for m in members if m["source"] in ADMIN] or members, "address"),
        "מספר בית": _first(members, "house_number"),
        "בעלות / ארגון": _first(members, "owner"),
        "סטטוס רישוי": _first(members, "license_status"),
        "גורם מפקח": supervisor,
        "גורם רושם": registrar,
        "מקומות מאושרים – תינוקות": _first(members, "places_babies"),
        "מקומות מאושרים – פעוטות": _first(members, "places_toddlers"),
        "מקומות מאושרים – בוגרים": _first(members, "places_adults"),
        "שם מנהלת": _first(members, "manager_name"),
        "טלפון": _first(members, "phone"),
        "קו רוחב": _first(members, "lat"),
        "קו אורך": _first(members, "lng"),
        "מקורות": "; ".join(sources),
        "מספר מקורות": len(sources),
        "סטטוס אימות": status,
        "מהימנות": reliability,
        "קישורים": " ".join(links),
        "תאריך שליפה": date.today().isoformat(),
        "_members": members,
    }


def build_city(city, verbose=True):
    records = []
    for fn in ALL_SOURCES:
        try:
            got = fn(city)
        except Exception as e:  # מקור שנשבר לא מפיל את כל ההרצה
            print(f"   ❌ {fn.__name__}: {e}")
            got = []
        if verbose:
            print(f"   {fn.__name__:15s} {len(got):4d} רשומות")
        records += got
    groups = resolve(records)
    # השמות רווחה הן מידע משלים על מעונות קיימים; רשומת רווחה שלא שויכה לא הופכת למסגרת חדשה
    groups = [g for g in groups if {m["source"] for m in g} != {"משרד הרווחה"}]
    facilities = [to_facility(g, city) for g in groups]
    facilities.sort(key=lambda f: (f["סטטוס אימות"] != "מאומת – מקור מנהלי", f["סוג מסגרת"], f["שם מסגרת"] or ""))
    return records, facilities


def write_outputs(city, records, facilities):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{k: v for k, v in f.items() if k != "_members"} for f in facilities])
    raw = pd.DataFrame([{k: v for k, v in r.items() if k != "extra"} for r in records])
    path = OUT_DIR / f"מסגרות_{city}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="מסגרות", index=False)
        raw.to_excel(xw, sheet_name="רשומות מקור", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "B2"
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", choices=list(CITIES))
    a = ap.parse_args()
    for city in [a.city] if a.city else CITIES:
        print(f"\n🏙️  {city}")
        records, facilities = build_city(city)
        path = write_outputs(city, records, facilities)
        print(f"   → {len(facilities)} מסגרות אחרי איחוד | {path}")


if __name__ == "__main__":
    main()

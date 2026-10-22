"""איחוד שלוש השכבות לרשימה אחת לעיר, בלי רשימת ייחוס.

שכבה 1 – מקורות ממשלתיים ומסחריים (mvp.build)
שכבה 2 – גילוי בקבוצות הורים, אינסטגרם ואינדקסים (mvp.discovery + הכרעה)
הסיווג לפי סוג פיקוח: ממשלתי (סמל) / פרטי בפיקוח / פרטי לא מפוקח.

הרצה:
    python3 -m mvp.layers --city דימונה --judgment data/manual/discovery_judgment_2026-10-19.json
"""
import argparse
import json
import html
import re
from pathlib import Path

import pandas as pd

from .agent_reverse import WORKBOOK
from .config import CITIES, OUT_DIR
from .normalize import clean, name_sim

GOV_TYPES = {"מעון סמל", "משפחתון סמל", "מעון יום שיקומי", "מסגרת רווחה"}
EXPORT_DIR = WORKBOOK.parent.parent / "בדיקה בערים חדשות"


def supervision(ftype):
    if ftype in GOV_TYPES:
        return "ממשלתי (סמל)"
    if ftype == "מעון פרטי":
        return "פרטי בפיקוח"
    return "פרטי לא מפוקח"


def _street(text):
    m = re.search(r"([א-ת'\" ]{2,20}?)\s+(\d{1,4})\b", clean(text or ""))
    return (m.group(1).strip(), m.group(2)) if m else (None, None)


GENERIC = {"משפחתון", "פעוטון", "מעון", "גן", "גנון", "של", "הגן", "המשפחתון", "הפעוטון", "המעון",
           "בוטיק", "ילדים", "פרטי", "חב", "ד"}


def _core(name):
    """השם בלי המילים הגנריות — 'המשפחתון של ענבל' → 'ענבל'."""
    return " ".join(w for w in clean(name).split() if w not in GENERIC)


def _name_match(a, b):
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return name_sim(a, b) if clean(a) == clean(b) or not (ca or cb) else 0.0
    return name_sim(ca, cb)


def match_layer1(cand, facilities):
    """שם דומה (בלי מילים גנריות), או אותו רחוב ומספר בית."""
    c_street, c_num = _street(cand.get("note", ""))
    best, best_s = None, 0.0
    for f in facilities:
        s = _name_match(cand["name"], f["שם מסגרת"] or "")
        f_street, f_num = _street(f"{f['רחוב'] or ''} {f['מספר בית'] or ''}")
        if c_street and f_street and c_num == f_num and name_sim(c_street, f_street) > 0.8:
            s = max(s, 0.9)
        if s > best_s:
            best, best_s = f, s
    return (best, best_s) if best_s >= 0.75 else (None, best_s)


# ── סיווג מחמיר ל"פרטי לא מפוקח" ────────────────────────────────────────────
# מסגרת נספרת כלא מפוקחת רק אם יש ראיה שהיא קטנה ולגילאי 0–3. כל השאר עובר לאחת
# משתי הקטגוריות "כנראה מפוקח – כפילות" או "גיל/פיקוח לא ברורים".
UNSUP, LIKELY_SUP, UNCLEAR = "פרטי לא מפוקח", "חייבת ברישיון – לא ברשימה הרשמית", "גן/מעון פרטי – גיל או פיקוח לא ברורים"
OFFICIAL_DUP = "כפילות של מסגרת מפוקחת"
NAME_ONLY = "שם של מעון או רשת – לא הותאם לרשימה"
NETWORK = re.compile(r"מעון|ויצו|ויצ ו|נעמ|חניכי|צעדים|רשת")
SMALL = re.compile(r"משפחתון|משפחתוגן|פעוטון|תינוקי|גן ביתי|מטפלת")


def _bdl_cards(city):
    import glob
    from .config import CITIES, RAW_DIR
    files = sorted(glob.glob(str(RAW_DIR / "*" / f"bdl_{CITIES[city]['mol_town']}.json")))
    if not files:
        return {}
    return {clean(c["name"]): c for c in json.loads(Path(files[-1]).read_text(encoding="utf-8"))}


def official_match(name, address, official, city_aliases=()):
    """התאמה לרשימה הרשמית. מחמיר בכוונה:
    1. שם ייחודי זהה (בלי מילים גנריות ובלי שם העיר), מול שם המסגרת או שם הבעלים.
    2. משפחתון סמל רשום בשם המטפלת — שם פרטי זהה לאחת המילים בשם המטפלת.
    3. אותו רחוב ומספר בית ±2 — רק למסגרת שהשם שלה לא מעיד על משפחתון קטן."""
    cities = {clean(a) for a in city_aliases} | {"ויצו", "ויצ", "ו", "נעמת", "נעמ", "ת", "צעדים", "קטנטנים", "פעוטות", "תינוקות"}
    core = " ".join(w for w in _core(html.unescape(str(name))).split() if w not in cities)
    st, num = _street(html.unescape(str(address)))
    small = bool(SMALL.search(str(name)))
    for o in official:
        for field in (o["name"], o.get("owner", "")):
            oc = " ".join(w for w in _core(field).split() if w not in cities)
            if core and oc and len(core) >= 3 and core == oc:
                return o["name"]
        if small and o.get("type") == "משפחתון סמל" and core and len(core) >= 3 and " " not in core \
                and core in _core(o["name"]).split():
            return o["name"]
        ost, onum = _street(f"{o['street'] or ''} {o['house_number'] or ''}")
        if not small and st and ost and num and onum and name_sim(st, ost) > 0.85 \
                and abs(int(num) - int(onum)) <= 2:
            return o["name"]
    return None

def strict(df, city, cands):
    cards = _bdl_cards(city)
    raw = pd.read_excel(OUT_DIR / f"מסגרות_{city}.xlsx", sheet_name="רשומות מקור")
    official = [{"name": str(r["name"]), "owner": str(r["owner"]) if str(r["owner"]) != "nan" else "",
                 "type": r["source_type"], "street": r["street"] if str(r["street"]) != "nan" else "",
                 "house_number": str(r["house_number"]).split(".")[0] if str(r["house_number"]) != "nan" else ""}
                for _, r in raw[raw["source"].isin(["משרד החינוך", "זרוע העבודה"])].iterrows()]
    kinds = {c["name"]: c for c in cands}
    sup_addr = {(_street(a)) for a in df.loc[df["שכבת פיקוח"] != UNSUP, "כתובת"] if _street(a)[1]}
    out = []
    for _, r in df.iterrows():
        lvl, name, why = r["שכבת פיקוח"], str(r["שם מסגרת"]), ""
        if lvl == UNSUP:
            if r["נמצא בשכבה 1"] != "כן":  # רק בגילוי
                c = kinds.get(name, {})
                if c.get("conf") == "נמוכה":
                    lvl, why = UNCLEAR, "ודאות נמוכה בגילוי"
                elif not SMALL.search(f"{name} {c.get('kind', '')}"):
                    lvl, why = UNCLEAR, "גן או מעון – לא ברור שקטן ולגילאי 0–3"
            else:
                card = cards.get(clean(name))
                om = official_match(name, r["כתובת"], official, CITIES[city]["aliases"])
                if om:
                    lvl, why = OFFICIAL_DUP, f"ברשימה הרשמית בשם '{om}'"
                elif NETWORK.search(name) or re.match(r"^\S+ \d+$", name):
                    lvl, why = NAME_ONLY, "שם של מעון או רשת, בלי התאמה לרשימה הרשמית"
                elif _street(r["כתובת"])[1] and _street(r["כתובת"]) in sup_addr:
                    lvl, why = OFFICIAL_DUP, "אותה כתובת כמו מסגרת מפוקחת"
                elif card and (card.get("max_children") or 0) >= 7 and card.get("age_max") and card["age_max"] <= 3.5:
                    lvl, why = LIKELY_SUP, f"עד {card['max_children']} ילדים בגילאי {card.get('age_min') or 0:g}–{card['age_max']:g} – חייב ברישיון"
                elif card and card.get("age_max") and card["age_max"] > 3.5:
                    lvl, why = UNCLEAR, f"גן מעורב עד גיל {card['age_max']:g}"
                elif card and card.get("max_children") and card["max_children"] <= 6:
                    pass  # קטן ולגילאי 0–3 – נשאר
                elif SMALL.search(name):
                    pass
                elif r["נמצא בשכבה 2"] == "כן" and SMALL.search(
                        kinds.get(str(r["הערה"]).replace("בגילוי: ", ""), {}).get("kind", "")):
                    pass
                else:
                    lvl, why = UNCLEAR, "אין מידע על גיל או גודל"
        out.append((lvl, why))
    df = df.copy()
    df["שכבת פיקוח"] = [o[0] for o in out]
    df["סיבת סיווג"] = [o[1] for o in out]
    # כפילות: אותו שם בלי מילים גנריות ("משפחתון אביבית הכי קרוב לאמא" / "משפחתון הכי קרוב לאמא")
    seen, keep = {}, []
    for i, r in df.iterrows():
        k = (r["שכבת פיקוח"], _core(str(r["שם מסגרת"])).replace("אביבית ", ""))
        if r["שכבת פיקוח"] == UNSUP and k[1] and k in seen:
            keep.append(False)
            continue
        seen[k] = i
        keep.append(True)
    return df[keep]


def run(city, judgment_path):
    fac = pd.read_excel(OUT_DIR / f"מסגרות_{city}.xlsx").to_dict("records")
    hits = json.loads((OUT_DIR / f"discovery_{city}.json").read_text(encoding="utf-8"))
    judg = json.loads(Path(judgment_path).read_text(encoding="utf-8"))
    cands, rejected = judg[city], judg.get(f"{city}_נפסלו", [])

    noise = {n["name"]: n["why"] for n in judg.get(f"{city}_רעש_שכבה1", [])}
    rejected = rejected + [{"name": k, "why": f"שכבה 1 (מקור מסחרי): {v}", "ev": []} for k, v in noise.items()]
    fac = [f for f in fac if f["שם מסגרת"] not in noise]
    rows = []
    for f in fac:
        rows.append({"שכבת פיקוח": supervision(f["סוג מסגרת"]), "שם מסגרת": f["שם מסגרת"],
                     "סוג": f["סוג מסגרת"], "כתובת": f["רחוב"], "נמצא בשכבה 1": "כן",
                     "נמצא בשכבה 2": "", "מקורות": f["מקורות"], "ודאות": f["מהימנות"],
                     "ראיה מהגילוי": "", "הערה": ""})
    new = 0
    for c in cands:
        links = " ".join(hits[i]["link"] for i in c["ev"])
        src = sorted({hits[i]["channel"] for i in c["ev"]})
        f, s = match_layer1(c, fac)
        if c.get("nomatch"):  # התאמה שגויה לפי שם דומה — נבדק ידנית
            f = None
        if c.get("match"):
            f = next((x for x in fac if x["שם מסגרת"] == c["match"]), f)
        if f:
            r = next(r for r in rows if r["שם מסגרת"] == f["שם מסגרת"] and r["נמצא בשכבה 1"] == "כן")
            r["נמצא בשכבה 2"] = "כן"
            r["ראיה מהגילוי"] = links
            r["הערה"] = f"בגילוי: {c['name']}"
            continue
        new += 1
        rows.append({"שכבת פיקוח": "פרטי לא מפוקח", "שם מסגרת": c["name"], "סוג": c["kind"],
                     "כתובת": "", "נמצא בשכבה 1": "", "נמצא בשכבה 2": "כן",
                     "מקורות": "; ".join(src), "ודאות": c["conf"], "ראיה מהגילוי": links,
                     "הערה": c["note"]})
    df = strict(pd.DataFrame(rows), city, cands)
    order = {"ממשלתי (סמל)": 0, "פרטי בפיקוח": 1, OFFICIAL_DUP: 2, NAME_ONLY: 3, LIKELY_SUP: 4, UNSUP: 5, UNCLEAR: 6}
    df = df.sort_values(by=["שכבת פיקוח", "נמצא בשכבה 1"], key=lambda s: s.map(order) if s.name == "שכבת פיקוח" else s,
                        ascending=[True, False])

    rej = pd.DataFrame([{"שם": r["name"], "למה נפסל": r["why"],
                         "ראיה": " ".join(hits[i]["link"] for i in r["ev"]) or "ראו קובץ שכבה 1"} for r in rejected])
    l1, l2 = df["נמצא בשכבה 1"] == "כן", df["נמצא בשכבה 2"] == "כן"
    summ = pd.DataFrame([{
        "שכבת פיקוח": lvl,
        "מסגרות": int((df["שכבת פיקוח"] == lvl).sum()),
        "רק בשכבה 1": int(((df["שכבת פיקוח"] == lvl) & l1 & ~l2).sum()),
        "בשתי השכבות": int(((df["שכבת פיקוח"] == lvl) & l1 & l2).sum()),
        "רק בגילוי (שכבה 2)": int(((df["שכבת פיקוח"] == lvl) & ~l1 & l2).sum()),
    } for lvl in order])

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"מסגרות_{city}_3 שכבות.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summ.to_excel(xw, sheet_name="סיכום", index=False)
        df.to_excel(xw, sheet_name="כל המסגרות", index=False)
        rej.to_excel(xw, sheet_name="נפסלו בגילוי", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "B2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 22
    print(city)
    print(summ.to_string(index=False))
    print(f"גילוי: {len(cands)} מועמדים, {len(cands) - new} כבר בשכבה 1, {new} חדשים · נפסלו {len(rejected)}")
    print(f"→ {path}")
    return df, summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--judgment", required=True)
    a = ap.parse_args()
    run(a.city, a.judgment)


if __name__ == "__main__":
    main()

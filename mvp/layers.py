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

POTENTIAL = "מסגרת פוטנציאלית"
SUP_DUP = "מפוקחת – רשומה בשם או בכתובת אחרים"
DISCOVERY_CH = {"פייסבוק – קבוצות", "פייסבוק – דפים", "אינסטגרם", "טיקטוק", "אינדקסים ורשת"}


def assess(df, city, cands):
    """מפוקחת = ברשימה הרשמית (גם אם רשומה אחרת). כל השאר = מסגרת פוטנציאלית,
    עם רמת ודאות, הערות וסימון לתיקוף אנושי. לא מסווגים לפי מספר ילדים."""
    cards = _bdl_cards(city)
    raw = pd.read_excel(OUT_DIR / f"מסגרות_{city}.xlsx", sheet_name="רשומות מקור")
    official = [{"name": str(r["name"]), "owner": str(r["owner"]) if str(r["owner"]) != "nan" else "",
                 "type": r["source_type"], "street": r["street"] if str(r["street"]) != "nan" else "",
                 "house_number": str(r["house_number"]).split(".")[0] if str(r["house_number"]) != "nan" else ""}
                for _, r in raw[raw["source"].isin(["משרד החינוך", "זרוע העבודה"])].iterrows()]
    kinds = {c["name"]: c for c in cands}
    sup_addr = {_street(a) for a in df.loc[df["שכבת פיקוח"] != UNSUP, "כתובת"] if _street(a)[1]}
    status, conf, notes, check = [], [], [], []
    for _, r in df.iterrows():
        name = str(r["שם מסגרת"])
        if r["שכבת פיקוח"] != UNSUP:
            status.append(r["שכבת פיקוח"]); conf.append("גבוהה"); notes.append(""); check.append("")
            continue
        n = []
        prev = str(r["הערה"]) if str(r["הערה"]) != "nan" else ""
        if prev:
            n.append(prev)
        if r["נמצא בשכבה 1"] == "כן":
            om = official_match(name, r["כתובת"], official, CITIES[city]["aliases"])
            if om or (_street(r["כתובת"])[1] and _street(r["כתובת"]) in sup_addr):
                status.append(SUP_DUP); conf.append("בינונית")
                notes.append(f"ברשימה הרשמית בשם '{om}'" if om else "באותה כתובת כמו מסגרת מפוקחת")
                check.append("מומלץ")
                continue
        cand = kinds.get(name) or kinds.get(prev.replace("בגילוי: ", ""), {})
        card = cards.get(clean(name)) if r["נמצא בשכבה 1"] == "כן" else None
        srcs = [s for s in str(r["מקורות"]).split("; ") if s and s != "nan"]
        n_src = len([s for s in srcs if s not in DISCOVERY_CH]) + (1 if r["נמצא בשכבה 2"] == "כן" else 0)
        young = bool(SMALL.search(f"{name} {cand.get('kind', '')}"))
        if card and card.get("age_min") is not None:
            n.append(f"גילאי {card['age_min']:g}–{card['age_max']:g} לפי בדרך לגן")
            young = young or (card["age_min"] < 3)
            if card.get("age_max") and card["age_max"] > 3.5:
                n.append("מקבל גם ילדים מעל גיל 3")
        elif not young:
            n.append("אין מידע על גילאים")
        if NETWORK.search(name) or re.match(r"^\S+ \d+$", name):
            n.append("שם של מעון או רשת – ייתכן שמפוקחת, לא נמצאה ברשימה הרשמית")
        if r["נמצא בשכבה 1"] != "כן":
            c = cand.get("conf", "בינונית")
        elif n_src >= 2 and young:
            c = "גבוהה"
        elif young:
            c = "בינונית"
        else:
            c = "נמוכה"
        status.append(POTENTIAL); conf.append(c); notes.append(" · ".join(dict.fromkeys(n)))
        check.append("מומלץ" if c == "גבוהה" else "נדרש")
    df = df.copy()
    df.insert(0, "סטטוס", status)
    df["ודאות"], df["הערות"], df["תיקוף אנושי"] = conf, notes, check
    df = df.drop(columns=["שכבת פיקוח", "הערה"])
    # כפילות בין מקורות: אותו שם בלי מילים גנריות
    seen, keep = set(), []
    for _, r in df.iterrows():
        k = (r["סטטוס"], _core(str(r["שם מסגרת"])).replace("אביבית ", ""))
        keep.append(not (r["סטטוס"] == POTENTIAL and k[1] and k in seen))
        seen.add(k)
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
    df = assess(pd.DataFrame(rows), city, cands)
    st_order = {"ממשלתי (סמל)": 0, "פרטי בפיקוח": 1, SUP_DUP: 2, POTENTIAL: 3}
    cf_order = {"גבוהה": 0, "בינונית": 1, "נמוכה": 2}
    df = df.assign(_s=df["סטטוס"].map(st_order), _c=df["ודאות"].map(cf_order)) \
           .sort_values(["_s", "_c", "שם מסגרת"]).drop(columns=["_s", "_c"])

    rej = pd.DataFrame([{"שם": r["name"], "למה נפסל": r["why"],
                         "ראיה": " ".join(hits[i]["link"] for i in r["ev"]) or "ראו קובץ שכבה 1"} for r in rejected])
    pot = df[df["סטטוס"] == POTENTIAL]
    only2 = pot["נמצא בשכבה 1"] != "כן"
    summ = pd.DataFrame(
        [{"סטטוס": s, "ודאות": "", "מסגרות": int((df["סטטוס"] == s).sum()), "רק בגילוי ברשת": 0}
         for s in ("ממשלתי (סמל)", "פרטי בפיקוח", SUP_DUP)] +
        [{"סטטוס": POTENTIAL, "ודאות": c, "מסגרות": int((pot["ודאות"] == c).sum()),
          "רק בגילוי ברשת": int(((pot["ודאות"] == c) & only2).sum())} for c in cf_order])

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

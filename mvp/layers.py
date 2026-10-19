"""איחוד שלוש השכבות לרשימה אחת לעיר, בלי רשימת ייחוס.

שכבה 1 – מקורות ממשלתיים ומסחריים (mvp.build)
שכבה 2 – גילוי בקבוצות הורים, אינסטגרם ואינדקסים (mvp.discovery + הכרעה)
הסיווג לפי סוג פיקוח: ממשלתי (סמל) / פרטי בפיקוח / פרטי לא מפוקח.

הרצה:
    python3 -m mvp.layers --city דימונה --judgment data/manual/discovery_judgment_2026-10-19.json
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd

from .agent_reverse import WORKBOOK
from .config import OUT_DIR
from .normalize import clean, name_sim

GOV_TYPES = {"מעון סמל", "משפחתון סמל", "מעון יום שיקומי", "מסגרת רווחה"}
EXPORT_DIR = WORKBOOK.parent.parent / "בדיקה בעיר חדשה – דימונה ורמת השרון"


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
    df = pd.DataFrame(rows)
    order = {"ממשלתי (סמל)": 0, "פרטי בפיקוח": 1, "פרטי לא מפוקח": 2}
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

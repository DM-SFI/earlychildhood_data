"""השוואת הדאטה סט שנבנה אוטומטית מול מיפוי ידני של הרשות (קרקע אמת).

הרצה:
    source .env && python3 -m mvp.evaluate
פלט: data/output/השוואה_<עיר>.xlsx + סיכום במסוף.
"""
import re
from pathlib import Path

import pandas as pd

from .build import build_city, write_outputs
from .config import CITIES, OUT_DIR
from .normalize import clean, name_sim, phone_key, split_address, street_sim

ONEDRIVE = Path.home() / ("Library/CloudStorage/OneDrive-SocialFinanceIsrael/Data & Measurement site - Documents/"
                          "ניהול פעילות הייעוץ/פרויקטי ייעוץ שלטון מקומי/יד הנדיב 24-25- מענים לתושבים/"
                          "אסטרטגיית נתונים לגיל הרך")


def _addr(v):
    return "" if pd.isna(v) else str(v)


def _sym(v):
    s = re.sub(r"\D", "", str(v or "").split(".")[0])
    return s or None


def gt_kiryat_ata():
    df = pd.read_excel(ONEDRIVE / "2025קרית אתא/תוצרי ביניים/מיפוי מסגרות חינוך- מעודכן 31.12.25.xlsx")
    df = df[df["שם מסגרת"].notna()]
    rows = []
    for _, r in df.iterrows():
        rows.append({"gt_name": clean(r["שם מסגרת"]), "gt_type": r["סוג מסגרת"], "gt_symbol": _sym(r["סמל"]),
                     "gt_street": split_address(f"{_addr(r['רחוב'])} {_addr(r['מספר'])}")[0],
                     "gt_num": _sym(r["מספר"]) or "", "gt_phone": phone_key(r.get("טלפון מנהלת")),
                     "gt_address": f"{r['רחוב']} {r['מספר']}"})
    return rows


def gt_netanya():
    base = ONEDRIVE / "2026נתניה/חומרים מהלקוח/מיפוי מסגרות"
    rows = []
    m = pd.read_excel(base / "מיפוי מעונות נתניה.xlsx", sheet_name="מעונות")
    m = m[m["שם המעון "].notna()]
    for _, r in m.iterrows():
        st, num = split_address(_addr(r["כתובת"]), ["נתניה"])
        kind = r.get("סוג מעון")
        rows.append({"gt_name": clean(r["שם המעון "]),
                     "gt_type": f"מעון {kind}" if isinstance(kind, str) else "מעון (לא צוין)",
                     "gt_symbol": _sym(r["סמל חינוך"]), "gt_street": st, "gt_num": num,
                     "gt_phone": phone_key(r.get("טלפון קווי/נייד")), "gt_address": r["כתובת"]})
    f = pd.read_excel(base / "מאגר משפחתונים אוגוסט 2026.xlsx", sheet_name="מאגר מלא")
    f = f[f["שם המשפחתון"].notna() & (f["סטטוס פעילות"] != "סגור")]
    for _, r in f.iterrows():
        st, num = split_address(_addr(r["כתובת"]), ["נתניה"])
        rows.append({"gt_name": clean(r["שם המשפחתון"]), "gt_type": "משפחתון (מאגר רשותי)",
                     "gt_symbol": None, "gt_street": st, "gt_num": num,
                     "gt_phone": phone_key(r.get("טלפון")), "gt_address": r["כתובת"]})
    return rows


GROUND_TRUTH = {"קרית אתא": gt_kiryat_ata, "נתניה": gt_netanya}


def match(gt, facilities):
    """מחזיר (מסגרת, סיבה) או (None, None)."""
    best, best_score, why = None, 0, None
    for f in facilities:
        members = f["_members"]
        syms = {m[k] for m in members for k in ("moe_symbol", "mol_symbol") if m[k]}
        if gt["gt_symbol"] and gt["gt_symbol"] in syms:
            return f, "סמל"
        for m in members:
            ns = name_sim(gt["gt_name"], m["name"])
            ss = street_sim(gt["gt_street"], m["street"])
            same_num = gt["gt_num"] and gt["gt_num"] == m["house_number"]
            same_phone = gt["gt_phone"] and gt["gt_phone"] == phone_key(m["phone"])
            score, reason = 0, None
            if same_phone:
                score, reason = 3, "טלפון"
            elif ss >= 0.85 and same_num:
                score, reason = 2 + ns, "כתובת"
            elif ns >= 0.85 and ss >= 0.7:
                score, reason = 1.5 + ns, "שם+רחוב"
            elif ns >= 0.95 and not (gt["gt_street"] and m["street"]):
                score, reason = 1 + ns, "שם (ללא כתובת)"
            if score > best_score:
                best, best_score, why = f, score, reason
    return best, why


def evaluate_city(city):
    print(f"\n🏙️  {city}")
    records, facilities = build_city(city)
    write_outputs(city, records, facilities)
    gt = GROUND_TRUTH[city]()

    matched_ids, rows = set(), []
    for g in gt:
        f, why = match(g, facilities)
        if f:
            matched_ids.add(f["מזהה מסגרת"])
        rows.append({**{k: g[k] for k in ("gt_name", "gt_type", "gt_symbol", "gt_address")},
                     "נמצא?": "כן" if f else "לא", "אופן התאמה": why,
                     "מזהה מסגרת": f["מזהה מסגרת"] if f else None,
                     "שם במקור": f["שם מסגרת"] if f else None,
                     "מקורות": f["מקורות"] if f else None})
    det = pd.DataFrame(rows).rename(columns={"gt_name": "שם במיפוי הרשות", "gt_type": "סוג במיפוי הרשות",
                                             "gt_symbol": "סמל במיפוי הרשות", "gt_address": "כתובת במיפוי הרשות"})
    by_type = det.groupby("סוג במיפוי הרשות").agg(במיפוי=("נמצא?", "size"),
                                                  נמצאו=("נמצא?", lambda s: (s == "כן").sum()))
    by_type["כיסוי"] = (by_type["נמצאו"] / by_type["במיפוי"]).map("{:.0%}".format)

    # מה המקורות מצאו שלא מופיע במיפוי הרשות
    extra = [f for f in facilities if f["מזהה מסגרת"] not in matched_ids]
    ext = pd.DataFrame([{k: v for k, v in f.items() if k != "_members"} for f in extra])
    ext_summary = ext.groupby(["סוג מסגרת", "מקורות"]).size().rename("כמות").reset_index() if len(ext) else ext

    # תרומת כל מקור: כמה ממסגרות קרקע-האמת נמצאו בזכותו
    src_rows = []
    fac_by_id = {f["מזהה מסגרת"]: f for f in facilities}
    for typ, grp in det[det["נמצא?"] == "כן"].groupby("סוג במיפוי הרשות"):
        for src in ["משרד החינוך", "זרוע העבודה", "משרד הבריאות", "משרד הרווחה", "בדרך לגן", "Google Places"]:
            n = sum(src in fac_by_id[i]["מקורות"] for i in grp["מזהה מסגרת"])
            src_rows.append({"סוג במיפוי הרשות": typ, "מקור": src, "נמצאו דרכו": n})
    src = pd.DataFrame(src_rows).pivot(index="סוג במיפוי הרשות", columns="מקור", values="נמצאו דרכו") \
        if src_rows else pd.DataFrame()

    path = OUT_DIR / f"השוואה_{city}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        by_type.reset_index().to_excel(xw, sheet_name="כיסוי לפי סוג", index=False)
        src.reset_index().to_excel(xw, sheet_name="תרומת מקורות", index=False)
        det.to_excel(xw, sheet_name="מיפוי הרשות מול הכלי", index=False)
        ext.to_excel(xw, sheet_name="נמצא בכלי ולא במיפוי", index=False)
        ext_summary.to_excel(xw, sheet_name="סיכום תוספות", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "A2"

    print(by_type.to_string())
    print("\nתרומת מקורות:\n", src.to_string())
    print(f"\nנמצאו בכלי ולא במיפוי הרשות: {len(extra)}")
    if len(ext):
        print(ext_summary.to_string(index=False))
    print(f"→ {path}")
    return by_type, src, ext_summary


def main():
    for city in CITIES:
        evaluate_city(city)


if __name__ == "__main__":
    main()

"""קובץ עבודה לתיקוף ידני (לנועה) מתוך קובץ ההשוואה.

הרצה: python3 -m mvp.validation_task --city נתניה
"""
import argparse

import pandas as pd

from .config import CITIES, OUT_DIR

CHECK_COLS = ["פעילה? (כן/לא/לא ידוע)", "מקבלת גילאי 0-3? (כן/לא)",
              "סוג בפועל (משפחתון ביתי / מעון פרטי / מעון סמל / לא רלוונטי)",
              "קיימת במיפוי הרשות? (כן/לא)", "איך אומת (טלפון/אתר/פייסבוק/רשות)", "הערות"]


def build(city):
    cmp_path = OUT_DIR / f"השוואה_{city}.xlsx"
    det = pd.read_excel(cmp_path, sheet_name="מיפוי הרשות מול הכלי")
    ext = pd.read_excel(cmp_path, sheet_name="נמצא בכלי ולא במיפוי")

    leads = ext[ext["סוג מסגרת"] == "חשד למסגרת לא מפוקחת"][
        ["מזהה מסגרת", "שם מסגרת", "רחוב", "מקורות", "מהימנות", "קישורים"]].copy()
    for c in CHECK_COLS:
        leads[c] = ""

    admin_extra = ext[ext["סוג מסגרת"] != "חשד למסגרת לא מפוקחת"][
        ["מזהה מסגרת", "שם מסגרת", "סוג מסגרת", "רחוב", "סטטוס רישוי", "מקורות"]].copy()
    admin_extra["למה לא במיפוי הרשות? (חדשה / שם אחר / חסרה / לא רלוונטית)"] = ""

    missed = det[det["נמצא?"] == "לא"][["שם במיפוי הרשות", "סוג במיפוי הרשות", "סמל במיפוי הרשות",
                                         "כתובת במיפוי הרשות"]].copy()
    missed["עדיין פעילה? / שם אחר במקור?"] = ""

    path = OUT_DIR / f"תיקוף_{city}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([
            ("1. לידים לאימות", f"{len(leads)} מסגרות שנמצאו רק במקורות מסחריים. לכל אחת: פעילה? גילאים? סוג?"),
            ("2. מוכרות שחסרות ברשות", f"{len(admin_extra)} מסגרות ממקור ממשלתי שלא נמצאו במיפוי הרשות"),
            ("3. ברשות ולא בכלי", f"{len(missed)} מסגרות מהמיפוי הרשותי שהכלי לא מצא — עוזר לנו להבין מה חסר"),
            ("מטרה", "1) מיפוי מהימן  2) ללמוד אילו מקורות/ניסוחים מוצאים מסגרות ביתיות (reverse engineering)"),
        ], columns=["גיליון", "מה עושים"]).to_excel(xw, sheet_name="הסבר", index=False)
        leads.to_excel(xw, sheet_name="1. לידים לאימות", index=False)
        admin_extra.to_excel(xw, sheet_name="2. מוכרות שחסרות ברשות", index=False)
        missed.to_excel(xw, sheet_name="3. ברשות ולא בכלי", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 24
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", choices=list(CITIES), required=True)
    print(build(ap.parse_args().city))

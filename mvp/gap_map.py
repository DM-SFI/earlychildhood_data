"""מפת פערים לפי שכונה: איפה צפויות מסגרות ביתיות שאנחנו לא מכירים.

שלושה קבצים של הרשות + הדאטה סט שלנו:
  ביקוש   — ילדים בגילאי 0-2 לפי שכונה (קובץ הרשות)
  איתות   — רישום לטרום-טרום: כמה ילדים הגיעו מ"משפחתון" / "בית" לפי שכונה (קובץ ללא מזהים)
  היצע    — מסגרות ידועות (ממשלתי + מאגר המשפחתונים של הרשות + לידים), משויכות לשכונה לפי טבלת רחובות

מדד החשד: כל משפחתון "מוציא" בערך K ילדים לגן בשנה. מספר הילדים שהגיעו ממשפחתון בשכונה, חלקי K,
נותן אומדן למספר המשפחתונים הפעילים. אומדן פחות משפחתונים ידועים = משפחתונים חשודים שלא מוכרים לנו.
K הוא הנחה (ברירת מחדל 2) — לא נתון. המטרה היא דירוג שכונות לחיפוש, לא ספירה מדויקת.

הרצה:  set -a && source .env && set +a && python3 -m mvp.gap_map
פלט:   data/output/מפת פערים_נתניה.xlsx + .html
פרטיות: רק נתונים מצרפיים ברמת שכונה יוצאים מהסקריפט.
"""
import json
import os
from datetime import date

import pandas as pd
import requests

from .build import build_city
from .config import OUT_DIR, RAW_DIR
from .evaluate import ONEDRIVE
from .normalize import clean, split_address, street_key, street_sim

CITY = "נתניה"
BASE = ONEDRIVE / "2026נתניה/חומרים מהלקוח"
K_GRADUATES_PER_FAMILY_DAYCARE = 2
MIN_KIDS = 30  # שכונות קטנות מזה (אזורי תעשייה) לא מוצגות


def norm_hood(s):
    s = clean(s).replace("קריית", "קרית")
    return {"משכונות זבולון": "משכנות זבולון"}.get(s, s)


# ── טבלת רחוב → שכונה ──────────────────────────────────────────────────────
def street_table():
    for enc in ("utf-8-sig", "cp1255"):
        try:
            t = pd.read_csv(BASE / "דוח שיוך רחובות לאזורים.csv", encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    t["key"] = t["שם רחוב"].map(street_key)
    t["hood"] = t["אזור"].map(norm_hood)
    return t


def to_hood(address, table):
    street, num = split_address(address, ["נתניה"])
    if not street:
        return None
    cand = table[table["key"] == street]
    if cand.empty:
        scores = table["key"].map(lambda k: street_sim(street, k))
        if scores.max() < 0.88:
            return None
        cand = table[scores == scores.max()]
    if num and len(cand) > 1:
        n = int(num)
        rng = cand[(cand["ממספר"] <= n) & (cand["עד מספר"] >= n)]
        if len(rng):
            cand = rng
    return cand["hood"].mode().iloc[0]


# ── שלושת הרכיבים ───────────────────────────────────────────────────────────
def demand():
    d = pd.read_excel(BASE / "גילאי לידה עד 3 לפי שכונות.xlsx", sheet_name="חלוקה לפי גיל")
    d = d[d["שכונה"].notna() & ~d["שכונה"].astype(str).str.contains("סכום|כולל")]
    d["hood"] = d["שכונה"].map(norm_hood)
    return d.groupby("hood")["סכום כולל"].sum().rename("ילדים 0-2")


def registration_signal():
    r = pd.read_excel(BASE / "רישום לגנים/רישום טרום טרום ללא מזהים.xlsx",
                      usecols=["שכונה", "סוג מסגרת קודמת_תאור"])
    r = r[r["שכונה"].notna()]
    r["hood"] = r["שכונה"].map(norm_hood)
    ct = pd.crosstab(r["hood"], r["סוג מסגרת קודמת_תאור"])
    out = pd.DataFrame({
        "נרשמו לטרום-טרום": ct.sum(axis=1),
        "הגיעו ממשפחתון": ct.get("משפחתון", 0),
        "הגיעו מהבית": ct.get("בית", 0),
        "הגיעו ממעון": ct.get("מעון", 0),
    })
    return out


def supply(table):
    _, facilities = build_city(CITY, verbose=False)
    rows = []
    for f in facilities:
        addr = f"{f['רחוב'] or ''} {f['מספר בית'] or ''}"
        kind = ("משפחתון מוכר" if f["סוג מסגרת"] == "משפחתון סמל" else
                "ליד" if f["סוג מסגרת"] == "חשד למסגרת לא מפוקחת" else "מעון מפוקח")
        places = sum(v for v in (f["מקומות מאושרים – תינוקות"], f["מקומות מאושרים – פעוטות"],
                                 f["מקומות מאושרים – בוגרים"]) if isinstance(v, (int, float)) and v == v)
        rows.append({"hood": to_hood(addr, table), "kind": kind, "places": places})
    pool = pd.read_excel(ONEDRIVE / "2026נתניה/חומרים מהלקוח/מיפוי מסגרות/מאגר משפחתונים אוגוסט 2026.xlsx",
                         sheet_name="מאגר מלא")
    pool = pool[pool["שם המשפחתון"].notna() & (pool["סטטוס פעילות"] != "סגור")]
    for a in pool["כתובת"]:
        rows.append({"hood": to_hood("" if pd.isna(a) else str(a), table), "kind": "משפחתון במאגר הרשות",
                     "places": 0})
    s = pd.DataFrame(rows)
    unplaced = s[s["hood"].isna()].groupby("kind").size().to_dict()
    piv = s.dropna(subset=["hood"]).pivot_table(index="hood", columns="kind", values="places",
                                                aggfunc="size", fill_value=0)
    piv["מקומות מאושרים (סמל)"] = s.dropna(subset=["hood"]).groupby("hood")["places"].sum()
    return piv, unplaced


def geocode_hoods(hoods, table):
    """מרכז שכונה = ממוצע של 3 רחובות שלה בגיאוקודינג (גיאוקודינג של שם שכונה לא אמין)."""
    key = os.environ.get("GOOGLE_API_KEY")
    cache_path = RAW_DIR / "street_geocode_netanya.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    cents = {}
    for h in hoods:
        streets = table[(table["hood"] == h) & (table["סוג מספר בית"] == "כל המספרים")]["שם רחוב"].tolist()
        streets = (streets or table[table["hood"] == h]["שם רחוב"].tolist())[:5]
        pts = []
        for st in streets:
            if st not in cache and key:
                r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                                 params={"address": f"{st}, נתניה, ישראל", "key": key, "language": "he"},
                                 timeout=30).json()
                res = [x for x in r.get("results") or [] if "route" in x.get("types", [])]
                cache[st] = res[0]["geometry"]["location"] if res else None
            pt = cache.get(st)
            if pt and 32.26 <= pt["lat"] <= 32.37 and 34.82 <= pt["lng"] <= 34.90:  # בתוך תחום נתניה
                pts.append(pt)
        cents[h] = ({"lat": sum(p["lat"] for p in pts) / len(pts), "lng": sum(p["lng"] for p in pts) / len(pts)}
                    if pts else None)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cents


# ── הרכבה ──────────────────────────────────────────────────────────────────
def build():
    table = street_table()
    sup, unplaced = supply(table)
    df = pd.concat([demand(), registration_signal(), sup], axis=1).fillna(0)
    for c in ["מעון מפוקח", "משפחתון מוכר", "משפחתון במאגר הרשות", "ליד"]:
        if c not in df:
            df[c] = 0
    df = df[df["ילדים 0-2"] >= MIN_KIDS].copy()
    df["% ממשפחתון"] = (df["הגיעו ממשפחתון"] / df["נרשמו לטרום-טרום"].replace(0, pd.NA) * 100).round(0)
    df["% מהבית"] = (df["הגיעו מהבית"] / df["נרשמו לטרום-טרום"].replace(0, pd.NA) * 100).round(0)
    df["מעונות ל-100 ילדים"] = (df["מעון מפוקח"] / df["ילדים 0-2"] * 100).round(1)
    df["משפחתונים ידועים"] = df["משפחתון מוכר"] + df["משפחתון במאגר הרשות"]
    df["אומדן משפחתונים פעילים"] = (df["הגיעו ממשפחתון"] / K_GRADUATES_PER_FAMILY_DAYCARE).round(0)
    df["משפחתונים חשודים (לא ידועים)"] = (df["אומדן משפחתונים פעילים"] - df["משפחתונים ידועים"]).clip(lower=0)
    df = df.sort_values("משפחתונים חשודים (לא ידועים)", ascending=False)
    df.index.name = "שכונה"
    cols = ["ילדים 0-2", "נרשמו לטרום-טרום", "הגיעו ממשפחתון", "% ממשפחתון", "הגיעו מהבית", "% מהבית",
            "מעון מפוקח", "מעונות ל-100 ילדים", "מקומות מאושרים (סמל)", "משפחתונים ידועים", "ליד",
            "אומדן משפחתונים פעילים", "משפחתונים חשודים (לא ידועים)"]
    return df[cols], unplaced


def write(df, unplaced):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = OUT_DIR / f"מפת פערים_{CITY}.xlsx"
    notes = pd.DataFrame([
        ("שיטה", f"אומדן משפחתונים = ילדים שהגיעו ממשפחתון לרישום טרום-טרום ÷ {K_GRADUATES_PER_FAMILY_DAYCARE} "
                 "(הנחה: כמה ילדים כל משפחתון מוציא לגן בשנה)"),
        ("חשודים", "אומדן פחות משפחתונים ידועים (זרוע העבודה + מאגר הרשות). לדירוג שכונות — לא לספירה"),
        ("לא שויכו לשכונה", json.dumps(unplaced, ensure_ascii=False) + " — בעיקר משפחתונים במאגר בלי כתובת"),
        ("מקורות", "גילאי לידה עד 3 לפי שכונות; רישום טרום טרום ללא מזהים; דוח שיוך רחובות לאזורים; "
                   "מאגר משפחתונים 8.2026; הדאטה סט של ה-MVP"),
        ("תאריך", date.today().isoformat()),
    ], columns=["", ""])
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        df.reset_index().to_excel(xw, sheet_name="לפי שכונה", index=False)
        notes.to_excel(xw, sheet_name="הסבר", index=False)
        for ws in xw.book.worksheets:
            ws.sheet_view.rightToLeft = True
            ws.freeze_panes = "B2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 16
    return xlsx


def main():
    df, unplaced = build()
    path = write(df, unplaced)
    cents = geocode_hoods(df.index, street_table())
    html = write_html(df, unplaced, cents)
    (OUT_DIR / f"מפת פערים_{CITY}.json").write_text(json.dumps({
        "rows": df.reset_index().to_dict(orient="records"), "centroids": cents, "unplaced": unplaced,
        "k": K_GRADUATES_PER_FAMILY_DAYCARE}, ensure_ascii=False, default=float), encoding="utf-8")
    print(df.head(15).to_string())
    print("לא שויכו:", unplaced)
    print("→", path)
    print("→", html)



HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>מפת פערים · נתניה</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root{--blue:#0E7FE8;--navy:#042C53;--pink:#E91E63;--yellow:#FBBF24;--ink:#12120F;--ink2:#4A4A44;--ink3:#6B6B63;--line:#ECEBE6;--bg:#fff;--soft:#F6F8FB;
--s1:#D6E9FB;--s2:#9CCBF5;--s3:#5AA8EE;--s4:#0E7FE8;--s5:#042C53;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Heebo,sans-serif;color:var(--ink);background:var(--bg);padding:24px 16px 40px;max-width:1240px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}
header img{height:34px}
.eyebrow{color:var(--pink);font-weight:800;font-size:14px}
h1{font-size:30px;font-weight:900;line-height:1.15}h1 b{color:var(--blue)}
.lead{font-size:15px;color:var(--ink2);max-width:880px;line-height:1.5;margin:6px 0 14px}
.controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;background:var(--soft);border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:14px}
.controls input{accent-color:var(--blue);width:160px}.controls b{color:var(--blue);font-size:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.kpi{border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .n{font-size:28px;font-weight:900;color:var(--navy);line-height:1.1}.kpi .t{font-size:13px;color:var(--ink3);margin-top:2px}
.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:16px}
.card{border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card h2{font-size:16px;font-weight:800;margin-bottom:8px}
#map{height:520px;border-radius:8px}
.legend{display:flex;gap:12px;align-items:center;font-size:12.5px;color:var(--ink2);margin-top:8px;flex-wrap:wrap}
.ramp{display:flex}.ramp span{width:22px;height:10px}
.bars{display:flex;flex-direction:column;gap:5px}
.bar{display:grid;grid-template-columns:130px 1fr 44px;align-items:center;gap:8px;font-size:13px;cursor:default}
.bar .track{height:16px;position:relative}
.bar .fill{position:absolute;inset-inline-start:0;top:0;height:100%;background:var(--blue);border-radius:0 4px 4px 0}
[dir=rtl] .bar .fill{border-radius:4px 0 0 4px}
.bar .v{font-weight:800;color:var(--ink);text-align:start}
.bar:hover .fill{background:var(--navy)}
.note{background:#FEF6DD;border-radius:10px;padding:10px 14px;font-size:13.5px;line-height:1.5;margin-top:16px}
.note b{color:var(--navy)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
th,td{padding:6px 6px;border-bottom:1px solid var(--line);text-align:start;white-space:nowrap}
th{color:var(--ink3);font-weight:700;cursor:pointer;position:sticky;top:0;background:#fff}
td.num,th.num{text-align:end;font-variant-numeric:tabular-nums}
.tablewrap{max-height:420px;overflow:auto;margin-top:16px}
.tip{font-family:Heebo,sans-serif;font-size:13px;line-height:1.5;direction:rtl}.tip b{color:var(--navy)}
footer{font-size:12px;color:var(--ink3);margin-top:16px}
@media (max-width:860px){.grid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<header><div><div class="eyebrow">מיפוי מסגרות לידה-3 · נתניה</div>
<h1>איפה צפויים <b>משפחתונים שאנחנו לא מכירים?</b></h1>
<p class="lead">לכל שכונה: כמה ילדים שנרשמו לטרום-טרום הגיעו ממשפחתון, לעומת כמה משפחתונים אנחנו מכירים שם. הפער מצביע איפה כדאי לחפש — זה אומדן לדירוג שכונות, לא ספירה.</p></div>
<img src="https://sfi.org.il/wp-content/uploads/2023/09/%D7%9C%D7%95%D7%92%D7%95-%D7%9E%D7%A2%D7%95%D7%93%D7%9B%D7%9F-2026.png" alt="SFI"></header>

<div class="controls">הנחה: כל משפחתון שולח לגן בכל שנה <input type="range" id="k" min="1" max="4" step="0.5" value="__K__"> <b id="kv">__K__</b> ילדים בממוצע
<span style="color:var(--ink3)">· הזיזו כדי לראות איך האומדן משתנה</span></div>

<div class="kpis">
<div class="kpi"><div class="n" id="k1"></div><div class="t">ילדים שנרשמו לטרום-טרום והגיעו ממשפחתון</div></div>
<div class="kpi"><div class="n" id="k2"></div><div class="t">אומדן משפחתונים פעילים בעיר</div></div>
<div class="kpi"><div class="n" id="k3"></div><div class="t">משפחתונים ידועים (זרוע העבודה + מאגר הרשות)</div></div>
<div class="kpi"><div class="n" id="k4" style="color:var(--pink)"></div><div class="t">פער — משפחתונים שכנראה לא מוכרים</div></div>
</div>

<div class="grid">
<div class="card"><h2>מפה · גודל העיגול = משפחתונים חשודים · צבע = % שהגיעו ממשפחתון</h2><div id="map"></div>
<div class="legend"><span>% מהנרשמים שהגיעו ממשפחתון:</span><div class="ramp"><span style="background:var(--s1)"></span><span style="background:var(--s2)"></span><span style="background:var(--s3)"></span><span style="background:var(--s4)"></span><span style="background:var(--s5)"></span></div><span>10% → 40%+</span></div></div>
<div class="card"><h2>15 השכונות עם הפער הגדול ביותר</h2><div class="bars" id="bars"></div></div>
</div>

<div class="note"><b>לקרוא בזהירות:</b> ל-__UNPLACED__ משפחתונים במאגר הרשות אין כתובת, ולכן הם לא שויכו לשכונה — חלק מהפער הוא המשפחתונים האלה.
השלמת כתובות למאגר (משימה של נועה) תחדד את המפה. בקרית צאנז שיעור ההגעה ממשפחתון גבוה במיוחד (55%) — דפוס מוכר בשכונות חרדיות, שם המסגרות הביתיות הן חלק מרכזי מההיצע.</div>

<div class="card tablewrap"><h2>כל השכונות (לחיצה על כותרת ממיינת)</h2><table id="tbl"></table></div>
<footer>מקורות: גילאי לידה עד 3 לפי שכונות · רישום טרום-טרום ללא מזהים · דוח שיוך רחובות לאזורים · מאגר משפחתונים 8.2026 (עיריית נתניה) · דאטה סט MVP (משרד החינוך, זרוע העבודה, משרד הבריאות, מקורות מסחריים). נתונים מצרפיים בלבד. __DATE__</footer>

<script>
const D=__DATA__;
const fmt=n=>Math.round(n).toLocaleString('he-IL');
const ramp=p=>p>=40?'var(--s5)':p>=30?'var(--s4)':p>=20?'var(--s3)':p>=15?'var(--s2)':'var(--s1)';
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v.slice(4,-1)).trim();
const map=L.map('map',{scrollWheelZoom:false}).setView([32.315,34.862],13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap',maxZoom:17}).addTo(map);
let layer=L.layerGroup().addTo(map);
function calc(k){return D.rows.map(r=>{const est=Math.round(r['הגיעו ממשפחתון']/k);return {...r,est,gap:Math.max(0,est-r['משפחתונים ידועים'])};});}
function render(){
 const k=+document.getElementById('k').value;document.getElementById('kv').textContent=k;
 const rows=calc(k).sort((a,b)=>b.gap-a.gap);
 const tot=s=>rows.reduce((a,r)=>a+r[s],0);
 document.getElementById('k1').textContent=fmt(tot('הגיעו ממשפחתון'));
 document.getElementById('k2').textContent=fmt(tot('est'));
 const known=tot('משפחתונים ידועים')+D.unplacedPool;
 document.getElementById('k3').textContent=fmt(known);
 document.getElementById('k4').textContent=fmt(Math.max(0,tot('est')-known));
 layer.clearLayers();
 rows.forEach(r=>{const c=D.centroids[r['שכונה']];if(!c)return;
  const tip=`<div class="tip"><b>${r['שכונה']}</b><br>ילדים 0-2: ${fmt(r['ילדים 0-2'])}<br>נרשמו לטרום-טרום: ${fmt(r['נרשמו לטרום-טרום'])}<br>הגיעו ממשפחתון: ${fmt(r['הגיעו ממשפחתון'])} (${r['% ממשפחתון']}%)<br>משפחתונים ידועים: ${r['משפחתונים ידועים']} · מעונות מפוקחים: ${r['מעון מפוקח']}<br>אומדן משפחתונים: ${r.est} · <b>חשודים: ${r.gap}</b></div>`;
  L.circleMarker([c.lat,c.lng],{radius:Math.max(5,Math.sqrt(r.gap)*4.2),color:'#fff',weight:2,fillColor:css(ramp(r['% ממשפחתון'])),fillOpacity:.9}).bindTooltip(tip,{direction:'top'}).addTo(layer);});
 const top=rows.slice(0,15),mx=Math.max(1,...top.map(r=>r.gap));
 document.getElementById('bars').innerHTML=top.map(r=>`<div class="bar" title="${r['שכונה']}: הגיעו ממשפחתון ${r['הגיעו ממשפחתון']}, ידועים ${r['משפחתונים ידועים']}, אומדן ${r.est}"><span>${r['שכונה']}</span><div class="track"><div class="fill" style="width:${r.gap/mx*100}%"></div></div><span class="v">${r.gap}</span></div>`).join('');
 drawTable(rows);
}
const cols=[['שכונה','שכונה'],['ילדים 0-2','ילדים 0-2'],['נרשמו לטרום-טרום','נרשמו'],['הגיעו ממשפחתון','ממשפחתון'],['% ממשפחתון','% ממשפחתון'],['% מהבית','% מהבית'],['מעון מפוקח','מעונות מפוקחים'],['מעונות ל-100 ילדים','מעונות ל-100'],['משפחתונים ידועים','משפחתונים ידועים'],['ליד','לידים'],['est','אומדן משפחתונים'],['gap','חשודים']];
let sortKey='gap',asc=false;
function drawTable(rows){rows=[...rows].sort((a,b)=>{const x=a[sortKey],y=b[sortKey];return (typeof x==='string'?x.localeCompare(y,'he'):x-y)*(asc?1:-1);});
 document.getElementById('tbl').innerHTML='<tr>'+cols.map(([k,l])=>`<th class="${k==='שכונה'?'':'num'}" data-k="${k}">${l}${k===sortKey?(asc?' ▲':' ▼'):''}</th>`).join('')+'</tr>'+
 rows.map(r=>'<tr>'+cols.map(([k])=>`<td class="${k==='שכונה'?'':'num'}">${typeof r[k]==='number'?(k.includes('100')?r[k].toFixed(1):fmt(r[k])):r[k]}</td>`).join('')+'</tr>').join('');
 document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;asc=sortKey===k?!asc:k==='שכונה';sortKey=k;render();});}
document.getElementById('k').oninput=render;render();
</script></body></html>"""


def write_html(df, unplaced, cents):
    data = {"rows": df.reset_index().to_dict(orient="records"), "centroids": cents,
            "unplacedPool": int(unplaced.get("משפחתון במאגר הרשות", 0))}
    html = (HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False, default=float))
            .replace("__K__", str(K_GRADUATES_PER_FAMILY_DAYCARE))
            .replace("__UNPLACED__", str(data["unplacedPool"]))
            .replace("__DATE__", date.today().strftime("%d.%m.%Y")))
    path = OUT_DIR / f"מפת פערים_{CITY}.html"
    path.write_text(html, encoding="utf-8")
    return path


if __name__ == "__main__":
    main()

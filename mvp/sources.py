"""שליפה מהמקורות. כל פונקציה מחזירה רשימת רשומות במבנה אחיד (dict) לעיר אחת.

מבנה רשומה אחידה (לפני איחוד):
    source, source_id, name, address, street, house_number, city,
    moe_symbol, mol_symbol, welfare_id, source_type, owner, license_status,
    manager_name, phone, places_babies, places_toddlers, places_adults,
    lat, lng, age_min, age_max, extra
"""
import json
import os
import re
import time
from datetime import date

import requests

from .config import CITIES, MOE_YEAR, MOL_YEAR, RAW_DIR, UA
from .normalize import clean, in_city, split_address

TODAY = date.today().isoformat()


def _cache(name):
    d = RAW_DIR / TODAY
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _get_json(url, cache_name, method="GET", body=None, headers=None):
    path = _cache(cache_name)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    if method == "POST":
        r = requests.post(url, json=body, headers=h, timeout=90)
    else:
        r = requests.get(url, headers=h, timeout=90)
    r.raise_for_status()
    data = r.json()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _rec(**kw):
    base = dict.fromkeys([
        "source", "source_id", "name", "address", "street", "house_number", "city",
        "moe_symbol", "mol_symbol", "welfare_id", "source_type", "owner", "license_status",
        "manager_name", "phone", "places_babies", "places_toddlers", "places_adults",
        "lat", "lng", "age_min", "age_max", "extra",
    ])
    base.update(kw)
    return base


# ── 1. משרד החינוך — פורטל הורים (מעונות סמל + פרטיים בפיקוח) ─────────────────
def moe(city):
    cfg = CITIES[city]
    url = ("https://parents.education.gov.il/api/data/meonot/GetMeonot?pageNumber=1&pageSize=10000"
           f"&shnatLimudim={MOE_YEAR}&codeYishuvMisgeret=&codeRechovMisgeret=&semelMisgeretIrgun="
           "&semelMaonKlqalaMehasava=&codeMigzar=&isAscending=true&yishuv=&rechov=&misparBait=&radius=0")
    data = _get_json(url, "moe_daycare.json")
    out = []
    for r in data["list"]:
        if not in_city(r["yishuV_MISGERET"], cfg["aliases"]):
            continue
        street = (r.get("rechoV_MISGERET") or "").strip()
        num = str(r.get("mispaR_BAIT") or "")
        phone = f"{r.get('kidomeT_TELEPHONE') or ''}-{r.get('telephonE_MAON') or ''}".strip("-")
        mol = r.get("semeL_MAON_KLKALA_MEHASAVA")
        out.append(_rec(
            source="משרד החינוך", source_id=str(r["semeL_MISGERET_IRGUNIT"]),
            name=clean(r["sheM_MISGERET_IRGUNIT"]), address=f"{street} {num}".strip(),
            street=split_address(f"{street} {num}")[0], house_number=num, city=city,
            moe_symbol=str(r["semeL_MISGERET_IRGUNIT"]), mol_symbol=str(mol) if mol else None,
            source_type="מעון סמל" if mol else "מעון פרטי",
            owner=(r.get("sheM_BAALUT") or "").strip(), license_status=r.get("teuR_STATUS_RISHUY"),
            manager_name=f"{r.get('sheM_PRATI_MENAHEL') or ''} {r.get('sheM_MISHPACHA_MENAHEL') or ''}".strip(),
            phone=phone, extra={"sector": r.get("teuR_MIGZAR")},
        ))
    return out


# ── 2. זרוע העבודה — איתור מסגרת מוכרת (מעונות + משפחתונים סמל) ───────────────
def mol(city):
    cfg = CITIES[city]
    out = []
    for frame_type, label in [(1, "מעון סמל"), (2, "משפחתון סמל")]:
        data = _get_json(
            "https://daycareclasssearch.labor.gov.il/api/FramesSearch/GetFramesList",
            f"mol_{cfg['mol_town']}_{frame_type}.json", method="POST",
            body={"Year": MOL_YEAR, "FrameType": frame_type, "TownId": cfg["mol_town"], "FrameId": ""},
            headers={"Origin": "https://daycareclasssearch.labor.gov.il",
                     "Referer": "https://daycareclasssearch.labor.gov.il/"},
        )
        for r in data.get("Frames") or []:
            street, num = split_address(r.get("Address"), cfg["aliases"])
            out.append(_rec(
                source="זרוע העבודה", source_id=str(r["FrameId"]), name=clean(r["FrameName"]),
                address=r.get("Address"), street=street, house_number=num, city=city,
                mol_symbol=str(r["FrameId"]), source_type=label, owner=r.get("OrganizationName"),
                manager_name=r.get("ManagerName"), phone=r.get("Phone"),
                places_babies=r.get("NumOfApprovedBabiesPlaces"),
                places_toddlers=r.get("NumOfApprovedToddlersPlaces"),
                places_adults=r.get("NumOfApprovedAdultsPlaces"),
                extra={"daycare_type": r.get("DaycareTypeDesc")},
            ))
        time.sleep(1)
    return out


# ── 3. משרד הרווחה — מסגרות בפיקוח (data.gov.il) ─────────────────────────────
WELFARE_RESOURCE = "de069ddf-bcbc-4754-bda0-84873a353f7b"


def welfare(city):
    cfg = CITIES[city]
    data = _get_json(
        f"https://data.gov.il/api/3/action/datastore_search?resource_id={WELFARE_RESOURCE}&limit=50000",
        "welfare_frames.json")
    out = []
    for r in data["result"]["records"]:
        if r.get("Type_Descr") not in ("מעון יום", "מעון רב תכליתי", "שילוב במעון יום"):
            continue
        if not in_city(r.get("City_Name") or r.get("Authoritys") or "", cfg["aliases"]):
            continue
        # מעונות יום שיקומיים נלקחים מרשימת משרד הבריאות (health_rehab); כאן רק השמות רווחה בגיל הרך
        if r.get("Head_Department") != "ילד ונוער":
            continue
        if r.get("Status_des") and r["Status_des"] != "פעילה":
            continue
        name = clean(r.get("Name"))
        # מעונות יום למבוגרים (21+ / בוגרים) — לא רלוונטיים
        if re.search(r"21|בוגרים|מבוגרים|תעסוקה", name) or (r.get("From_Age") or 0) > 6:
            continue
        dept = r.get("Head_Department")
        street, num = split_address(r.get("Adrees"), cfg["aliases"])
        out.append(_rec(
            source="משרד הרווחה", source_id=str(r["Misgeret_Id"]), name=name, address=r.get("Adrees"),
            street=street, house_number=num, city=city, welfare_id=str(r["Misgeret_Id"]),
            source_type="מעון יום (השמת רווחה)",
            owner=r.get("Organization"), manager_name=r.get("Maneger_Name"), phone=r.get("Telephone"),
            age_min=r.get("From_Age"), age_max=r.get("To_Age"),
            extra={"department": dept, "owner_type": r.get("Owner_Code_Descr"),
                   "capacity": r.get("Actual_Capacity")},
        ))
    return out


# ── 4. משרד הבריאות — רשימת מעונות יום שיקומיים (PDF) ──────────────────────
HEALTH_PDF = "https://www.gov.il/BlobFolder/reports/rehab-day-care-list/he/units_child-development_rehab_day_care_list.pdf"


def _unvisual(s):
    """ה-PDF שמור בסדר ויזואלי (הפוך). הופכים את המחרוזת ומחזירים מספרים לכיוונם."""
    s = (s or "").replace("\n", " ")[::-1]
    s = re.sub(r"\d+", lambda m: m.group(0)[::-1], s)
    return s.replace("(", "\0").replace(")", "(").replace("\0", ")").strip()


def health_rehab(city):
    import pdfplumber
    cfg = CITIES[city]
    pdf_path = RAW_DIR.parent / "manual" / "rehab_daycare_health.pdf"
    if not pdf_path.exists():
        # gov.il חוסם בקשות ללא Referer
        r = requests.get(HEALTH_PDF, headers={"User-Agent": UA,
                         "Referer": "https://www.gov.il/he/pages/rehab-day-care-list"}, timeout=90)
        r.raise_for_status()
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or not row[0] or not re.search(r"\d{5,}", row[0]):
                        continue
                    ids = re.findall(r"\d{5,}", row[0])
                    address = _unvisual(row[3])
                    if not in_city(address, cfg["aliases"]):
                        continue
                    phone = re.search(r"\d{2,3}-\d{6,7}", row[1] or "")
                    street, num = split_address(re.sub(r"^רח'?\s*", "", address), cfg["aliases"])
                    out.append(_rec(
                        source="משרד הבריאות", source_id=ids[0], name=clean(_unvisual(row[4])),
                        address=address, street=street, house_number=num, city=city,
                        welfare_id=ids[0], source_type="מעון יום שיקומי",
                        manager_name=_unvisual(row[2]), phone=phone.group(0) if phone else None,
                        extra={"all_ids": ids},
                    ))
    return out


# ── 5. בדרך לגן — עמודי עיר (מסחרי) ──────────────────────────────────────────
_CARD = re.compile(r'class="col-md-12 col-sm-12 col-xs-12 gan-card(.*?)(?=class="col-md-12 col-sm-12 col-xs-12 gan-card|<div id="gans-map")', re.S)


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def baderechlagan(city):
    cfg = CITIES[city]
    path = _cache(f"bdl_{cfg['mol_town']}.json")
    if path.exists():
        cards = json.loads(path.read_text(encoding="utf-8"))
    else:
        cards, seen = [], set()
        for page in range(1, 30):
            url = f"https://www.baderechlagan.co.il/ganei-yeladim/{cfg['bdl_slug']}?page={page}"
            html = requests.get(url, headers={"User-Agent": UA}, timeout=60).text
            new = 0
            for m in _CARD.finditer(html):
                block = m.group(1)
                gid = re.search(r'href="/gans/([0-9a-f]+)"', block)
                if not gid or gid.group(1) in seen:
                    continue
                seen.add(gid.group(1))
                new += 1
                title = re.search(r"<h4 class=\"pr10\">(.*?)</h4>", block, re.S)
                title_txt = _strip(title.group(1)) if title else ""
                name, _, kind = title_txt.partition("|")
                addr = re.search(r'fa-map-marker"></i>\s*([^<]+)', block)
                ages = re.search(r"גילאים:\s*</span>\s*([\d.]+)\s*עד\s*([\d.]+)", block)
                kids = re.search(r'num-of-children[^>]*>(\d+)', block)
                cards.append({"id": gid.group(1), "name": name.strip(), "kind": kind.strip(),
                              "address": addr.group(1).strip() if addr else "",
                              "age_min": float(ages.group(1)) if ages else None,
                              "age_max": float(ages.group(2)) if ages else None,
                              "max_children": int(kids.group(1)) if kids else None})
            if new == 0:
                break
            time.sleep(1.5)
        path.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")

    out = []
    for c in cards:
        if "עירייה" in c["kind"] or not in_city(c["address"], cfg["aliases"]):
            continue
        if c["age_min"] is not None and c["age_min"] >= 3:
            continue
        street, num = split_address(c["address"], cfg["aliases"])
        out.append(_rec(
            source="בדרך לגן", source_id=c["id"], name=clean(c["name"]), address=c["address"],
            street=street, house_number=num, city=city, source_type=c["kind"] or "לא ידוע",
            age_min=c["age_min"], age_max=c["age_max"],
            extra={"url": f"https://www.baderechlagan.co.il/gans/{c['id']}",
                   "max_children": c["max_children"]},
        ))
    return out


# ── 6. b144 (מדריך עסקים, מסחרי) ────────────────────────────────────────────
# עמודי b144 מציגים 15 תוצאות ראשונות בשרת; השאר נטענות עם טוקן סשן. לא עוקפים את זה —
# הרשימה נאספת דרך דפדפן (לחיצה על "תוצאות נוספות") ונשמרת ב-data/manual/b144_<תאריך>.json.
B144_SKIP = re.compile(r"יעוץ|רישוי גני|איגוד מורים|הנהלה|בניה|אגודות מוסדות")


def b144(city):
    files = sorted((RAW_DIR.parent / "manual").glob("b144_*.json"))
    if not files:
        return []
    items = json.loads(files[-1].read_text(encoding="utf-8")).get(city, [])
    out = []
    for name, street_addr, loc, bid in items:
        if not in_city(loc, CITIES[city]["aliases"]) or B144_SKIP.search(name):
            continue
        street, num = split_address(street_addr, CITIES[city]["aliases"])
        out.append(_rec(
            source="b144", source_id=bid, name=clean(name), address=street_addr, street=street,
            house_number=num, city=city, source_type="לא ידוע",
            extra={"url": f"https://www.b144.co.il/b144_sip/{bid}/", "file": files[-1].name},
        ))
    return out


# ── 7. Google Places (מסחרי, אמינות נמוכה) ───────────────────────────────────
GOOGLE_TERMS = ["משפחתון", "פעוטון", "מעון יום", "גן תינוקות", "גן פעוטות", "מטפלת לתינוקות"]
GOOGLE_INCLUDE = re.compile(r"משפחתון|פעוטון|מעון|גן|תינוק|פעוט|טף|קטנטנים|baby|daycare|kids|מטפלת")
GOOGLE_EXCLUDE = re.compile(r"כלב|חתול|dog|cat|חיות|ספר |בית ספר|יסודי|תיכון|חוג|ספורט|קאנטרי|חנות|צעצוע|"
                            r"ציוד|אופנה|בגדי|מועדון|ירקות|אירועים|רופא|קופת|מרפאה|ספרייה|גן ציבורי|פארק|גן שעשועים|"
                            r"חובה|טרום|מטפלת רגשית|טיפול רגשי|קלינאית|פסיכולוג")


def google_places(city):
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("   ⚠️  אין GOOGLE_API_KEY — מדלג על Google Places")
        return []
    cfg = CITIES[city]
    path = _cache(f"google_{cfg['mol_town']}.json")
    if path.exists():
        places = json.loads(path.read_text(encoding="utf-8"))
    else:
        places = {}
        lat, lng = cfg["center"]
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        for term in GOOGLE_TERMS:
            params = {"query": f"{term} {city}", "location": f"{lat},{lng}", "radius": cfg["radius_m"],
                      "language": "he", "key": key}
            while True:
                d = requests.get(url, params=params, timeout=60).json()
                for p in d.get("results", []):
                    places.setdefault(p["place_id"], p)
                tok = d.get("next_page_token")
                if not tok:
                    break
                time.sleep(2)
                params = {"pagetoken": tok, "key": key}
        path.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")

    out = []
    for pid, p in places.items():
        name, addr = p.get("name", ""), p.get("formatted_address", "")
        if not in_city(addr, cfg["aliases"]):
            continue
        if not GOOGLE_INCLUDE.search(name.lower()) or GOOGLE_EXCLUDE.search(name.lower()):
            continue
        if p.get("business_status") == "CLOSED_PERMANENTLY":
            continue
        street, num = split_address(addr, cfg["aliases"])
        loc = p.get("geometry", {}).get("location", {})
        out.append(_rec(
            source="Google Places", source_id=pid, name=clean(name), address=addr, street=street,
            house_number=num, city=city, source_type="לא ידוע", lat=loc.get("lat"), lng=loc.get("lng"),
            extra={"url": f"https://www.google.com/maps/place/?q=place_id:{pid}",
                   "rating": p.get("rating"), "reviews": p.get("user_ratings_total"),
                   "types": p.get("types")},
        ))
    return out


ALL_SOURCES = [moe, mol, health_rehab, welfare, baderechlagan, b144, google_places]

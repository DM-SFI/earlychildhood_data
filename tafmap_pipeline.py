"""
Pipeline: נתוני מסגרות גיל הרך (0–3)
======================================
מקורות (לפי סדר עדיפות):
  1. מפת הטף  — opendata.hasadna.org.il  (מאוחד, נקי, מעודכן יומי)
  2. data.gov.il  — fallback ישיר למשרד החינוך + משרד העבודה

הרצה:
  python3 tafmap_pipeline.py               # ברירת מחדל: מפת הטף
  python3 tafmap_pipeline.py --source gov  # ישירות מהממשלה
  python3 tafmap_pipeline.py --fallback    # מפת הטף → fallback אוטומטי

פלט: facilities.csv  +  facilities.json
"""

import requests
import csv
import json
import time
import argparse
import sys
from datetime import datetime
from typing import Optional

# ─── הגדרות מקורות ──────────────────────────────────────────────────────────

TAFMAP = {
    "name": "מפת הטף",
    "base_url": "https://opendata.hasadna.org.il/api/3/action/datastore_search",
    "resource_id": "9b29b8e0-bd40-4b8d-9e4b-f76c40e37f9e",
    "page_size": 1000,
}

# data.gov.il — resource IDs של המשרדים הרלוונטיים
# לאימות: https://data.gov.il/dataset?q=מעון+יום&sort=score+desc
GOV_IL_SOURCES = [
    {
        "name": "משרד החינוך — מעונות יום מפוקחים",
        "base_url": "https://data.gov.il/api/3/action/datastore_search",
        "resource_id": "2834d03e-bf1d-4f1f-b9a2-f2e5de58e43f",  # ← לאמת ב-data.gov.il
        "ministry": "moe",
    },
    {
        "name": "משרד העבודה — משפחתונים ופעוטונים",
        "base_url": "https://data.gov.il/api/3/action/datastore_search",
        "resource_id": "bb68386a-21a4-4a1b-82e6-cc1d9c92831a",  # ← לאמת ב-data.gov.il
        "ministry": "mol",
    },
]

OUTPUT_CSV = "facilities.csv"
OUTPUT_JSON = "facilities.json"

# ─── שכבת שליפה ─────────────────────────────────────────────────────────────

def fetch_ckan_resource(base_url: str, resource_id: str, page_size: int = 1000) -> list[dict]:
    """שולף את כל הרשומות מ-CKAN resource עם pagination."""
    records = []
    offset = 0

    while True:
        params = {
            "resource_id": resource_id,
            "limit": page_size,
            "offset": offset,
        }
        try:
            r = requests.get(base_url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise ConnectionError(f"שגיאת חיבור: {e}")

        if not data.get("success"):
            raise ValueError(f"CKAN החזיר שגיאה: {data.get('error')}")

        batch = data["result"]["records"]
        records.extend(batch)

        total = data["result"]["total"]
        offset += len(batch)
        print(f"  ← {offset}/{total} רשומות", end="\r")

        if offset >= total or not batch:
            break
        time.sleep(0.3)  # נימוס ל-API

    print()
    return records


# ─── שכבת נרמול ─────────────────────────────────────────────────────────────

def normalize_tafmap(record: dict) -> dict:
    """ממיר רשומת מפת הטף לסכמה אחידה."""
    official_raw = record.get("official", "")
    sources = _parse_sources(official_raw)

    return {
        "id": str(record.get("_id", "")),
        "name": _extract_name(sources) or "",
        "city": record.get("city", ""),
        "address": record.get("formatted_address", ""),
        "lat": record.get("lat", ""),
        "lng": record.get("lng", ""),
        "facility_kind": record.get("facility_kind", ""),
        "license_status": _extract_license(sources),
        "symbol": _extract_symbol(sources),
        "manager": _extract_manager(sources),
        "phone": _extract_phone(sources),
        "available_places": _extract_places(sources),
        "ministry_sources": ",".join(_extract_ministries(sources)),
        "source": "tafmap",
        "fetched_at": datetime.now().isoformat(),
    }


def normalize_gov_il(record: dict, ministry: str) -> dict:
    """ממיר רשומת data.gov.il לסכמה אחידה."""
    # שמות עמודות משתנים בין משרדים — מיפוי לסכמה אחידה
    name_keys = ["שם מסגרת", "שם המסגרת", "name", "facility_name", "שם"]
    city_keys = ["עיר", "ישוב", "city", "yishuv"]
    addr_keys = ["כתובת", "address", "כתובת מלאה"]
    symbol_keys = ["סמל מסגרת", "סמל", "symbol", "kod"]
    phone_keys = ["טלפון", "phone", "tel"]

    return {
        "id": f"{ministry}_{record.get('_id', '')}",
        "name": _find_value(record, name_keys),
        "city": _find_value(record, city_keys),
        "address": _find_value(record, addr_keys),
        "lat": record.get("lat", record.get("latitude", "")),
        "lng": record.get("lng", record.get("longitude", "")),
        "facility_kind": "",
        "license_status": "מפוקח",  # אם הוא ברשימת המשרד — מפוקח
        "symbol": _find_value(record, symbol_keys),
        "manager": "",
        "phone": _find_value(record, phone_keys),
        "available_places": "",
        "ministry_sources": ministry,
        "source": f"gov_il_{ministry}",
        "fetched_at": datetime.now().isoformat(),
    }


# ─── עזרים לפרסור ────────────────────────────────────────────────────────────

import re

def _parse_sources(official_raw: str) -> list[dict]:
    """ממיר את שדה official (Python dict syntax) לרשימת dict."""
    if not official_raw:
        return []
    try:
        s = official_raw.replace("None", "null").replace("True", "true").replace("False", "false")
        s = re.sub(r"'([^']*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', s)
        parsed = json.loads(s)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return []

def _find_value(record: dict, keys: list[str]) -> str:
    for k in keys:
        if k in record and record[k]:
            return str(record[k])
    return ""

def _extract_name(sources: list[dict]) -> str:
    for s in sources:
        if s.get("name"):
            return s["name"]
    return ""

def _extract_symbol(sources: list[dict]) -> str:
    for s in sources:
        for k in ["symbol", "סמל", "kod"]:
            if s.get(k):
                return str(s[k])
    return ""

def _extract_license(sources: list[dict]) -> str:
    for s in sources:
        for k in ["license_status", "רישיון", "status"]:
            if s.get(k):
                return str(s[k])
    return "לא ידוע"

def _extract_manager(sources: list[dict]) -> str:
    for s in sources:
        for k in ["manager", "מנהלת", "מנהל"]:
            if s.get(k):
                return str(s[k])
    return ""

def _extract_phone(sources: list[dict]) -> str:
    for s in sources:
        for k in ["phone", "טלפון", "tel"]:
            if s.get(k):
                return str(s[k])
    return ""

def _extract_places(sources: list[dict]) -> str:
    for s in sources:
        for k in ["approved_places", "מקומות", "places"]:
            if s.get(k):
                return str(s[k])
    return ""

def _extract_ministries(sources: list[dict]) -> list[str]:
    ministries = set()
    for s in sources:
        src = s.get("source", s.get("src", ""))
        if src:
            ministries.add(src)
    return list(ministries)


# ─── פייפליין ראשי ───────────────────────────────────────────────────────────

def fetch_from_tafmap() -> list[dict]:
    print(f"\n📡 שולף ממפת הטף...")
    raw = fetch_ckan_resource(
        TAFMAP["base_url"],
        TAFMAP["resource_id"],
        TAFMAP["page_size"],
    )
    print(f"  ✓ {len(raw)} רשומות גולמיות")
    normalized = [normalize_tafmap(r) for r in raw]
    return normalized


def fetch_from_gov_il() -> list[dict]:
    all_records = []
    for src in GOV_IL_SOURCES:
        print(f"\n📡 שולף מ{src['name']}...")
        try:
            raw = fetch_ckan_resource(src["base_url"], src["resource_id"])
            print(f"  ✓ {len(raw)} רשומות")
            normalized = [normalize_gov_il(r, src["ministry"]) for r in raw]
            all_records.extend(normalized)
        except Exception as e:
            print(f"  ⚠️  נכשל: {e}")

    # הסר כפולות לפי שם + עיר
    seen = set()
    unique = []
    for r in all_records:
        key = (r["name"].strip().lower(), r["city"].strip().lower())
        if key not in seen and r["name"]:
            seen.add(key)
            unique.append(r)

    print(f"\n  ✓ {len(unique)} רשומות ייחודיות אחרי deduplication")
    return unique


def run(source: str = "tafmap", fallback: bool = True) -> list[dict]:
    print("=" * 60)
    print("  Pipeline: מסגרות גיל הרך 0–3")
    print(f"  מקור: {source} | fallback: {fallback}")
    print("=" * 60)

    records = []

    if source in ("tafmap", "auto"):
        try:
            records = fetch_from_tafmap()
        except Exception as e:
            print(f"\n❌  מפת הטף לא זמינה: {e}")
            if fallback:
                print("🔄  עובר ל-fallback: data.gov.il...")
                records = fetch_from_gov_il()
            else:
                print("   הפעילי --fallback כדי לנסות data.gov.il")
                sys.exit(1)

    elif source == "gov":
        records = fetch_from_gov_il()

    else:
        print(f"מקור לא מוכר: {source}")
        sys.exit(1)

    # ─── שמירה ───
    save(records)
    return records


def save(records: list[dict]):
    if not records:
        print("⚠️  אין נתונים לשמירה")
        return

    # JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # CSV
    if records:
        with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)

    print(f"\n{'='*60}")
    print(f"  ✅  {len(records)} רשומות נשמרו")
    print(f"  📄  {OUTPUT_CSV}")
    print(f"  📄  {OUTPUT_JSON}")
    print(f"  🕐  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline: מסגרות גיל הרך")
    parser.add_argument(
        "--source",
        choices=["tafmap", "gov", "auto"],
        default="tafmap",
        help="מקור הנתונים (ברירת מחדל: tafmap)",
    )
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="עבור ל-data.gov.il אם מפת הטף לא זמינה",
    )
    args = parser.parse_args()
    run(source=args.source, fallback=args.fallback)

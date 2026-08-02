"""
כרייה: מסגרות גיל הרך ללא סמל רשמי
Google Places API — פיילוט חדרה וקריית אתא
הרצה: python3 google_places_pilot.py
"""

import requests, csv, json, time, io, os

# המפתח נקרא ממשתנה סביבה — לא נכתב בקוד, כדי שלא יישמר ל-GitHub.
# הרצה:  GOOGLE_API_KEY="המפתח" python3 google_places_pilot.py
# או:    יוצרים קובץ .env (מוחרג מ-git) ומריצים  source .env
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    raise SystemExit(
        "חסר GOOGLE_API_KEY.\n"
        'הרצה:  GOOGLE_API_KEY="המפתח שלך" python3 google_places_pilot.py'
    )

CITIES = {
    "חדרה": (32.4343, 34.9194),
    "קריית אתא": (32.8000, 35.1000),
}

SEARCH_TERMS = ["מעון יום לילדים", "משפחתון", "פעוטון", "גן ילדים פרטי 0-3"]

MAX_DISTANCE_KM = 8  # סינון תוצאות שרחוקות מדי מהעיר


def distance_km(lat1, lng1, lat2, lng2):
    """מרחק קירוב בק״מ בין שתי נקודות"""
    import math
    dlat = abs(lat1 - lat2) * 111
    dlng = abs(lng1 - lng2) * 111 * math.cos(math.radians(lat1))
    return math.sqrt(dlat**2 + dlng**2)

TAFMAP_CSV = "mapathataf.csv"  # הקובץ שהורדת


def search_places(query, lat, lng, radius=5000):
    """Google Places Text Search"""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "location": f"{lat},{lng}",
        "radius": radius,
        "language": "he",
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params)
    data = r.json()
    results = data.get("results", [])

    # Handle pagination
    while "next_page_token" in data:
        time.sleep(2)
        params2 = {"pagetoken": data["next_page_token"], "key": GOOGLE_API_KEY}
        r2 = requests.get(url, params=params2)
        data = r2.json()
        results += data.get("results", [])

    return results


CITY_ALIASES = {
    "חדרה": ["חדרה"],
    "קריית אתא": ["קריית אתא", "קרית אתא"],
}

def load_tafmap(city_filter):
    """טען את נתוני מפת הטף לעיר מסוימת"""
    known = set()
    known_names = []
    aliases = CITY_ALIASES.get(city_filter, [city_filter])
    try:
        with open(TAFMAP_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = row.get("city", "")
                if any(alias in city for alias in aliases):
                    addr = (row.get("formatted_address") or "").strip().lower()
                    if addr:
                        known.add(addr[:30])
                    # Extract name from official field
                    official = row.get("official", "")
                    import re
                    names = re.findall(r"'name':\s*'([^']+)'", official)
                    known_names.extend(names)
    except FileNotFoundError:
        print(f"⚠️  לא נמצא {TAFMAP_CSV} — ממשיך בלי השוואה")
    return known, set(n.strip() for n in known_names)


def run():
    print("=" * 60)
    print("  כרייה: מסגרות ללא סמל רשמי — Google Places")
    print("=" * 60)

    all_leads = []

    for city, (lat, lng) in CITIES.items():
        print(f"\n🏙️  {city} ({lat}, {lng})")
        known_addrs, known_names = load_tafmap(city)
        print(f"   מפת הטף: {len(known_names)} מסגרות ידועות")

        google_results = {}
        for term in SEARCH_TERMS:
            results = search_places(term, lat, lng)
            for r in results:
                place_id = r["place_id"]
                if place_id not in google_results:
                    google_results[place_id] = r

        print(f"   Google: {len(google_results)} תוצאות ייחודיות")

        # השווה
        new_leads = []
        for place_id, r in google_results.items():
            name = r.get("name", "")
            address = r.get("formatted_address", "").lower()
            rating = r.get("rating", "—")
            n_ratings = r.get("user_ratings_total", 0)
            res_lat = r["geometry"]["location"]["lat"]
            res_lng = r["geometry"]["location"]["lng"]

            # סנן תוצאות רחוקות מדי
            if distance_km(lat, lng, res_lat, res_lng) > MAX_DISTANCE_KM:
                continue

            # סנן מעונות כלבים
            DOG_KEYWORDS = ["כלב", "dog", "חתול", "cat", "חיות מחמד", "פנסיון"]
            if any(kw in name.lower() for kw in DOG_KEYWORDS):
                continue

            # בדוק אם הוא כבר ידוע
            name_known = any(
                known.strip() in name or name in known.strip()
                for known in known_names
                if len(known) > 3
            )
            addr_known = any(address[:30] in k or k in address[:30] for k in known_addrs if k)

            if not name_known and not addr_known:
                new_leads.append({
                    "city": city,
                    "name": name,
                    "address": r.get("formatted_address", ""),
                    "rating": rating,
                    "reviews": n_ratings,
                    "lat": r["geometry"]["location"]["lat"],
                    "lng": r["geometry"]["location"]["lng"],
                    "google_url": f"https://maps.google.com/?cid={place_id}",
                    "status": "ליד — לאימות",
                })

        print(f"   ✨ לידים חדשים (לא במפת הטף): {len(new_leads)}")
        for lead in new_leads[:5]:
            print(f"      • {lead['name']} | {lead['address'][:50]}")

        all_leads.extend(new_leads)

    # שמור תוצאות
    output_file = "google_leads.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  סה\"כ לידים חדשים: {len(all_leads)}")
    print(f"  נשמר: {output_file}")
    print(f"{'='*60}")


if __name__ == "__main__":
    if GOOGLE_API_KEY == "YOUR_API_KEY_HERE":
        print("❌  שכחת להכניס API key!")
        print("   פתחי google_places_pilot.py והחליפי YOUR_API_KEY_HERE")
    else:
        run()

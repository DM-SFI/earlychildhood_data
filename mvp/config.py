"""הגדרות משותפות ל-MVP של דאטה סט מסגרות לידה-3."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "output"

# שנת הלימודים לשליפה: משרד החינוך מסמן לפי שנת הסיום (2027 = תשפ"ז),
# זרוע העבודה לפי שנת הפתיחה (2026 = תשפ"ז).
MOE_YEAR = 2027
MOL_YEAR = 2026

# רשויות הפיילוט.
# mol_town = קוד ישוב למ"ס (משמש את זרוע העבודה)
# bdl_slug = הנתיב בעמוד העיר של "בדרך לגן"
CITIES = {
    "קרית אתא": {
        "aliases": ["קרית אתא", "קריית אתא"],
        "mol_town": 6800,
        "center": (32.8065, 35.1065),
        "radius_m": 4000,
        "bdl_slug": "קרית-אתא",
    },
    "חדרה": {
        "aliases": ["חדרה"],
        "mol_town": 6500,
        "center": (32.4365, 34.9196),
        "radius_m": 6000,
        "bdl_slug": "חדרה",
    },
    "נתניה": {
        "aliases": ["נתניה"],
        "mol_town": 7400,
        "center": (32.3215, 34.8532),
        "radius_m": 7000,
        "bdl_slug": "נתניה",
    },
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

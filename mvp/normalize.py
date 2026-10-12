"""ניקוי שמות וכתובות לצורך השוואה בין מקורות."""
import html
import re
from difflib import SequenceMatcher

_PUNCT = re.compile(r"[\"'`׳״\-–_.,()/\\|:;!?*#]+")
_SPACES = re.compile(r"\s+")

# מילים שמופיעות בשמות מסגרות ולא עוזרות להבחין ביניהן
NAME_STOPWORDS = {
    "מעון", "מעונות", "יום", "משפחתון", "משפחתונים", "פעוטון", "גן", "גנון", "גני", "ילדים",
    "תינוקות", "בעמ", "בע\"מ", "עמ", "ער", "חלצ", "של", "ה", "ו", "פרטי", "סמל",
    "daycare", "the", "גנים", "מרכז", "לגיל", "הרך",
}

STREET_PREFIXES = ("רחוב ", "רח ", "שדרות ", "שד ", "שדי ", "דרך ", "סמטת ", "סמ ")


def clean(s) -> str:
    if s is None:
        return ""
    s = str(s).replace("‏", "").replace("‎", "")
    s = _PUNCT.sub(" ", s)
    s = s.replace("קריית", "קרית")
    return _SPACES.sub(" ", s).strip()


def name_key(name) -> str:
    """שם מנורמל ללא מילים גנריות — לצורך השוואה."""
    toks = [t for t in clean(name).lower().split() if t not in NAME_STOPWORDS]
    return " ".join(toks)


def name_sim(a, b) -> float:
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    short, long_ = sorted((ka, kb), key=len)
    # הכלה נחשבת רק כשהחלק המוכל משמעותי ("קט" בתוך "קטי" לא נחשב)
    if short in long_ and len(short) >= 4 and len(short) >= 0.6 * len(long_):
        return 0.9
    return SequenceMatcher(None, ka, kb).ratio()


def split_address(addr, city_aliases=()):
    """'משה הס 19, נתניה' -> ('משה הס', '19'). מחזיר רחוב מנורמל ומספר בית."""
    a = clean(addr)
    for c in city_aliases:
        a = a.replace(clean(c), " ")
    a = re.sub(r"\bישראל\b", " ", a)
    a = _SPACES.sub(" ", a).strip()
    m = re.search(r"^(.*?)\s*(\d+)\s*[א-ת]?\s*$", a) or re.search(r"^(.*?)\s(\d+)\b", a)
    if m:
        street, num = m.group(1), m.group(2)
    else:
        street, num = a, ""
    return street_key(street), num


def street_key(street) -> str:
    """מפתח רחוב: בלי קידומות ("רח'", "שד'", "ד"ר") ובסדר מילים קבוע — "רייק חביבה" == "חביבה רייק"."""
    s = clean(street) + " "
    for p in STREET_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
    toks = [t for t in s.split() if t not in {"דר", "הרב", "רב", "פרופ", "סמטת", "שדרות", "רחוב"}]
    return "".join(sorted(toks))


def street_sim(a, b) -> float:
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def in_city(text, aliases) -> bool:
    t = clean(text)
    return any(clean(a) in t for a in aliases)


def phone_key(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    if d.startswith("972"):
        d = "0" + d[3:]
    if d and not d.startswith("0"):
        d = "0" + d
    return d if len(d) >= 9 else ""

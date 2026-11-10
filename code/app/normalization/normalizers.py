"""Deterministic normalizers (spec Stage 2).

Raw values are always preserved alongside normalized values; these helpers
never mutate input data.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional, Tuple

US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND",
    "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
VALID_STATE_CODES = set(US_STATES.values())

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "corp", "corporation",
    "co", "company", "plc", "pc", "dba",
}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%d-%b-%Y")


def normalize_date(value: Any) -> Optional[str]:
    """Return ISO YYYY-MM-DD or None if unparseable. Accepts datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # ISO datetime
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_number(value: Any) -> Optional[float]:
    """Parse numbers from strings like '$1,234.56', '(500)', '12.5%'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[$€£,\s]", "", text)
    text = text.rstrip("%")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def normalize_money(value: Any, default_currency: str = "USD") -> Tuple[Optional[float], Optional[str]]:
    number = parse_number(value)
    if number is None:
        return None, None
    currency = default_currency
    if isinstance(value, str) and "€" in value:
        currency = "EUR"
    elif isinstance(value, str) and "£" in value:
        currency = "GBP"
    return number, currency


def normalize_state(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    upper = text.upper()
    if upper in VALID_STATE_CODES:
        return upper
    return US_STATES.get(upper)


def normalize_entity_name(value: Any) -> Optional[str]:
    """Canonical comparison form; the original string is always preserved
    separately by the caller. Strips legal suffixes and punctuation."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    text = re.sub(r"[^\w\s&]", " ", text)
    tokens = [t for t in text.split() if t not in _LEGAL_SUFFIXES]
    return " ".join(tokens) if tokens else None


def normalize_address(value: Any) -> Optional[str]:
    """Normalization for comparison only — NOT proof of identity."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    replacements = {
        "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
        "road": "rd", "lane": "ln", "suite": "ste", "highway": "hwy",
        "north": "n", "south": "s", "east": "e", "west": "w",
    }
    text = re.sub(r"[^\w\s#]", " ", text)
    tokens = [replacements.get(t, t) for t in text.split()]
    return " ".join(tokens) if tokens else None


def normalize_vin(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return text or None


def normalize_class_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text.zfill(4)
    return text.upper()


def classify_value_presence(value: Any) -> str:
    """Distinguish null / missing / zero / empty / N-A / negative-declaration."""
    if value is None:
        return "missing"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "empty"
        if text.upper() in {"N/A", "NA", "NOT APPLICABLE"}:
            return "not_applicable"
        if text.upper() in {"NONE", "NO", "NONE KNOWN"}:
            return "explicit_negative"
        return "present"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "zero" if value == 0 else "present"
    return "present"

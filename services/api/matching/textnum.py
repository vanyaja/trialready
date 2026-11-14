"""Small text->number helpers shared by answer extraction and the MockJev rule engine."""
import re
from typing import Optional

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
         "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TENS_RX = re.compile(r"\b(" + "|".join(_TENS) + r")(?:[\s-]+(one|two|three|four|five|six|seven|eight|nine))?\b", re.I)
_ONES_RX = re.compile(r"\b(" + "|".join(_ONES[2:]) + r")\b", re.I)  # 'one' is too common a word to rewrite
NUM = r"(\d+(?:\.\d+)?)"


def words_to_digits(text: str) -> str:
    t = text or ""
    t = re.sub(r"\b(a|one) year and a half\b", "18 months", t, flags=re.I)
    t = re.sub(r"\bhalf a year\b", "6 months", t, flags=re.I)
    t = re.sub(r"\ba couple of\b", "2", t, flags=re.I)
    t = re.sub(r"\ba few\b", "3", t, flags=re.I)
    t = re.sub(r"\b(a|an|one)\s+(year|month|week|day)\b", r"1 \2", t, flags=re.I)
    t = _TENS_RX.sub(lambda m: str(_TENS[m.group(1).lower()] + (_ONES.index(m.group(2).lower()) if m.group(2) else 0)), t)
    t = _ONES_RX.sub(lambda m: str(_ONES.index(m.group(1).lower())), t)
    return t


def norm(text: str) -> str:
    t = (text or "").replace("\\", "").replace("≥", ">=").replace("≤", "<=").replace("–", "-").replace("—", "-").replace("²", "2")
    return re.sub(r"\s+", " ", t).strip().lower()


_UNIT = r"(%|percent|mmol/mol|mmol|kg/m2|kg|years?|yrs?|months?|weeks?|days?|ml/min\S*|ml)"
_GE = r">=|=>|at least|minimum(?: of)?|no less than|not less than|(?:greater|more) than or equal to|equal to or (?:greater|more) than"
_LE = r"<=|=<|at most|maximum(?: of)?|no more than|not more than|up to|less than or equal to|equal to or less than|not exceeding"
_GT = r">|more than|greater than|above|over|exceeding|higher than|older than"
_LT = r"<|less than|below|under|lower than|younger than|fewer than"
_CMP_RX = re.compile(rf"(?P<ge>{_GE})|(?P<le>{_LE})|(?P<gt>{_GT})|(?P<lt>{_LT})")
_CON_RX = re.compile(rf"(?P<cmp>{_GE}|{_LE}|{_GT}|{_LT})\s*(?:or equal to\s*)?(?:of\s+)?{NUM}\s*{_UNIT}?")
_RANGE_RX = re.compile(rf"{NUM}\s*{_UNIT}?\s*(?:-|to)\s*{NUM}\s*{_UNIT}?")
_BETWEEN_RX = re.compile(rf"between\s+(?:the\s+)?(?:ages?\s+of\s+)?{NUM}\s*{_UNIT}?\s*(?:and|&)\s*{NUM}\s*{_UNIT}?")
_SUFFIX_RX = re.compile(rf"{NUM}\s*{_UNIT}?\s*(?:old|of age)?\s*(?:\([^)]*\)\s*)?(?:or|and)\s+(?P<dir>more|greater|older|above|higher|over|less|lower|younger|below|under)")


def _op(cmp_text: str) -> str:
    m = _CMP_RX.fullmatch(cmp_text)
    return {"ge": ">=", "le": "<=", "gt": ">", "lt": "<"}[m.lastgroup] if m else ">="


def constraints(text: str) -> list[tuple]:
    """All numeric constraints in a criterion: [(op, value, unit|None)] with op in >=,>,<=,<."""
    t = norm(text)
    out, used = [], []

    def free(a, b):
        return all(b <= x or a >= y for x, y in used)

    for m in _BETWEEN_RX.finditer(t):
        unit = m.group(4) or m.group(2)
        out += [(">=", float(m.group(1)), unit), ("<=", float(m.group(3)), unit)]
        used.append(m.span())
    for m in _CON_RX.finditer(t):
        if free(*m.span()):
            out.append((_op(m.group("cmp")), float(m.group(2)), m.group(3)))
            used.append(m.span())
    for m in _RANGE_RX.finditer(t):
        if free(*m.span()):
            unit = m.group(4) or m.group(2)
            out += [(">=", float(m.group(1)), unit), ("<=", float(m.group(3)), unit)]
            used.append(m.span())
    for m in _SUFFIX_RX.finditer(t):
        if free(*m.span()):
            op = ">=" if m.group("dir") in ("more", "greater", "older", "above", "higher", "over") else "<="
            out.append((op, float(m.group(1)), m.group(2)))
            used.append(m.span())
    return out


def to_interval(cons: list[tuple]) -> tuple[Optional[float], Optional[float]]:
    """Collapse constraints to [lo, hi] (strict bounds nudged by a small epsilon)."""
    lo = hi = None
    for op, v, _ in cons:
        if op in (">=", ">"):
            v2 = v + (1e-6 if op == ">" else 0)
            lo = v2 if lo is None else max(lo, v2)
        else:
            v2 = v - (1e-6 if op == "<" else 0)
            hi = v2 if hi is None else min(hi, v2)
    return lo, hi


def value_in(v: float, lo: Optional[float], hi: Optional[float]) -> bool:
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def interval_in(b: tuple, lo: Optional[float], hi: Optional[float]) -> Optional[bool]:
    """Band [b0,b1] vs [lo,hi]: True if fully inside, False if fully outside, None if it straddles."""
    b0, b1 = b
    if value_in(b0, lo, hi) and value_in(b1, lo, hi):
        return True
    if (lo is not None and b1 < lo) or (hi is not None and b0 > hi):
        return False
    return None


def to_years(v: float, unit: Optional[str]) -> Optional[float]:
    if not unit:
        return None
    u = unit.lower()
    if u.startswith(("year", "yr")):
        return v
    if u.startswith("month"):
        return v / 12
    if u.startswith("week"):
        return v / 52
    if u.startswith("day"):
        return v / 365
    return None


def pct_to_mmol(p: float) -> float:
    return round((p - 2.15) * 10.929, 1)


def mmol_to_pct(m: float) -> float:
    return round(m / 10.929 + 2.15, 1)

"""Equity view (spec §12): registry composition vs ONS Census 2021 (TS021 ethnic group, TS007A age) for Camden and London.
CSVs in data/census/ were downloaded from the Nomis API (see data/census/SOURCE.md)."""
import csv
from functools import lru_cache

from sqlmodel import select

from ..config import DATA
from ..db import db_session
from ..models import Profile

GROUPS = {
    "Asian": "Asian, Asian British or Asian Welsh",
    "Black": "Black, Black British, Black Welsh, Caribbean or African",
    "Mixed": "Mixed or Multiple ethnic groups",
    "White": "White",
    "Other": "Other ethnic group",
}
AGE_BANDS = {"20-39": (20, 39), "40-59": (40, 59), "60-74": (60, 74), "75+": (75, 200)}
AREAS = {"camden": "Camden", "london": "London"}


@lru_cache
def _rows(name: str) -> list[dict]:
    path = DATA / "census" / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def census_ethnicity(area: str) -> dict:
    rows = [r for r in _rows("ts021_ethnic_group_camden_london.csv") if r["GEOGRAPHY_NAME"] == AREAS[area]]
    by = {r["C2021_ETH_20_NAME"]: int(r["OBS_VALUE"]) for r in rows}
    total = by.get("Total: All usual residents") or 1
    return {g: {"n": by.get(label, 0), "pct": round(100 * by.get(label, 0) / total, 1)} for g, label in GROUPS.items()}


def census_age(area: str) -> dict:
    """Share of residents aged 20+ in each band (5-year census bands aggregated)."""
    rows = [r for r in _rows("ts007a_age_camden_london.csv") if r["GEOGRAPHY_NAME"] == AREAS[area]]
    out = {b: 0 for b in AGE_BANDS}
    for r in rows:
        nums = [int(x) for x in r["C2021_AGE_19_NAME"].replace("Aged", "").replace("years", "").replace("and over", "to 200").replace("and under", "").split() if x.isdigit()]
        if len(nums) < 2 or nums[0] < 20:
            continue
        for b, (lo, hi) in AGE_BANDS.items():
            if lo <= nums[0] <= hi:
                out[b] += int(r["OBS_VALUE"])
    total = sum(out.values()) or 1
    return {b: {"n": n, "pct": round(100 * n / total, 1)} for b, n in out.items()}


def equity(area: str = "camden") -> dict:
    area = area if area in AREAS else "camden"
    with db_session() as db:
        members = db.exec(select(Profile).where(Profile.registry_member == True)).all()  # noqa: E712
    n = len(members)
    eth_known = [m for m in members if m.ethnicity_self in GROUPS]
    ce, ca = census_ethnicity(area), census_age(area)
    eth = []
    for g in GROUPS:
        k = sum(1 for m in eth_known if m.ethnicity_self == g)
        reg = round(100 * k / len(eth_known), 1) if eth_known else 0.0
        eth.append({"group": g, "registry_n": k, "registry_pct": reg, "census_pct": ce[g]["pct"], "gap_pp": round(reg - ce[g]["pct"], 1),
                    "under_represented": bool(eth_known) and reg < 0.8 * ce[g]["pct"]})
    aged = [m for m in members if m.age is not None and m.age >= 20]
    age = []
    for b, (lo, hi) in AGE_BANDS.items():
        k = sum(1 for m in aged if lo <= m.age <= hi)
        reg = round(100 * k / len(aged), 1) if aged else 0.0
        age.append({"band": b, "registry_n": k, "registry_pct": reg, "census_pct": ca[b]["pct"], "gap_pp": round(reg - ca[b]["pct"], 1)})
    langs: dict[str, int] = {}
    for m in members:
        langs[m.language or "en"] = langs.get(m.language or "en", 0) + 1
    return {"area": AREAS[area], "areas": list(AREAS.values()), "registry_n": n, "ethnicity_answered": len(eth_known), "ethnicity": eth, "age": age,
            "languages": [{"language": k, "n": v} for k, v in sorted(langs.items(), key=lambda x: -x[1])],
            "missing": [e["group"] for e in eth if e["under_represented"]],
            "note": "Demo sample (synthetic personas + consenting attendees), not representative. Ethnicity is optional and self-reported. "
                    "Census age shares are of residents aged 20+; type 2 diabetes prevalence differs by age and ethnic group, so the Census is a reference point, not a target.",
            "source": "ONS Census 2021, tables TS021 and TS007A, via Nomis (Open Government Licence v3.0)."}

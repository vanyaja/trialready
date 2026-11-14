"""Deterministic filters applied before any Jev call (spec §8.1): age, sex, status, distance."""
import json
import math
from functools import lru_cache
from typing import Optional

import httpx

from ..config import OUTCODES, get_settings

OPEN_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING"}


@lru_cache
def _table() -> dict:
    if OUTCODES.exists():
        return json.loads(OUTCODES.read_text(encoding="utf-8")).get("table", {})
    return {}


_live_cache: dict[str, Optional[tuple]] = {}


def outcode_latlon(outcode: Optional[str]) -> Optional[tuple]:
    if not outcode:
        return None
    oc = outcode.upper().replace(" ", "")
    hit = _table().get(oc)
    if hit:
        return tuple(hit)
    if oc in _live_cache:
        return _live_cache[oc]
    res = None
    if get_settings().allow_live_geocode:
        try:
            r = httpx.get(f"https://api.postcodes.io/outcodes/{oc}", timeout=1.5)
            if r.status_code == 200:
                j = r.json()["result"]
                res = (j["latitude"], j["longitude"])
        except Exception:
            res = None
    _live_cache[oc] = res
    return res


def is_known_outcode(oc: str) -> bool:
    return oc.upper() in _table()


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def nearest_site(trial, latlon: Optional[tuple]) -> tuple[Optional[dict], Optional[float]]:
    sites = trial.sites or []
    if not sites:
        return None, None
    if not latlon:
        return sites[0], None
    best = min(sites, key=lambda s: haversine_km(latlon, (s["lat"], s["lon"])))
    return best, round(haversine_km(latlon, (best["lat"], best["lon"])), 1)


def check(trial, age: Optional[int], sex: Optional[str], outcode: Optional[str]) -> dict:
    """Returns {ok, reason, distance_km, site}. Unknown values never exclude (they are asked for first anyway)."""
    latlon = outcode_latlon(outcode)
    site, dist = nearest_site(trial, latlon)
    res = {"ok": True, "reason": None, "distance_km": dist, "site": site}
    if trial.status not in OPEN_STATUSES:
        return dict(res, ok=False, reason="not recruiting")
    if age is not None:
        if trial.min_age is not None and age < trial.min_age:
            return dict(res, ok=False, reason=f"minimum age {trial.min_age}")
        if trial.max_age is not None and age > trial.max_age:
            return dict(res, ok=False, reason=f"maximum age {trial.max_age}")
    if sex and trial.sex in ("MALE", "FEMALE") and sex.upper() != trial.sex:
        return dict(res, ok=False, reason=f"{trial.sex.lower()} participants only")
    if dist is not None and dist > get_settings().max_distance_km:
        return dict(res, ok=False, reason=f"nearest site {dist:.0f} km away")
    return res


def prior(trial, dist: Optional[float]) -> float:
    """Cheap ranking prior for the K_CANDIDATES cut: condition match + distance."""
    cond = " ".join(trial.conditions or []).lower()
    c = 1.0 if ("type 2" in cond or "t2d" in cond or "type ii" in cond) else (0.6 if "diabet" in cond else 0.3)
    d = 0.5 if dist is None else max(0.0, 1.0 - dist / max(get_settings().max_distance_km, 1.0))
    return c + d

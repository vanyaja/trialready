"""ClinicalTrials.gov API v2 ingest + normalise (spec §7.1).

CLI:
  python -m services.api.trials.ingest_ctgov              # fetch live, write raw + processed JSON
  python -m services.api.trials.ingest_ctgov --from-raw   # rebuild processed JSON from data/raw (offline)
  python -m services.api.trials.ingest_ctgov --llm        # use the offline LLM for atomising + lay summaries
"""
import argparse
import json
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import PROCESSED_TRIALS, RAW_TRIALS
from .atomize import atomize
from .lay_summary import lay_for

API = "https://clinicaltrials.gov/api/v2/studies"
MIN_TRIALS = 40


def _fetch_all(params: dict) -> list[dict]:
    out, token = [], None
    with httpx.Client(timeout=40, headers={"User-Agent": "TrialReady-hackathon/0.1"}) as c:
        while True:
            p = dict(params, pageSize=100)
            if token:
                p["pageToken"] = token
            r = c.get(API, params=p)
            r.raise_for_status()
            j = r.json()
            out += j.get("studies", [])
            token = j.get("nextPageToken")
            if not token:
                return out


def _trim(study: dict) -> Optional[dict]:
    """Keep interventional studies with >=1 UK site; drop non-UK sites and site contact details."""
    p = study.get("protocolSection", {})
    if p.get("designModule", {}).get("studyType") != "INTERVENTIONAL":
        return None
    locs = [{k: l.get(k) for k in ("facility", "status", "city", "state", "zip", "country", "geoPoint")}
            for l in p.get("contactsLocationsModule", {}).get("locations", []) if l.get("country") == "United Kingdom"]
    if not locs:
        return None
    ident, status = p.get("identificationModule", {}), p.get("statusModule", {})
    return {"protocolSection": {
        "identificationModule": {k: ident.get(k) for k in ("nctId", "briefTitle", "officialTitle")},
        "statusModule": {"overallStatus": status.get("overallStatus"), "lastUpdatePostDateStruct": status.get("lastUpdatePostDateStruct")},
        "descriptionModule": {"briefSummary": p.get("descriptionModule", {}).get("briefSummary")},
        "conditionsModule": {"conditions": p.get("conditionsModule", {}).get("conditions")},
        "designModule": {"studyType": "INTERVENTIONAL", "phases": p.get("designModule", {}).get("phases")},
        "armsInterventionsModule": {"interventions": [{"type": i.get("type"), "name": i.get("name")}
                                                      for i in p.get("armsInterventionsModule", {}).get("interventions", [])]},
        "eligibilityModule": p.get("eligibilityModule", {}),
        "contactsLocationsModule": {"locations": locs},
    }}


def fetch_raw(cond: str = "type 2 diabetes") -> dict:
    """RECRUITING first; add NOT_YET_RECRUITING, then widen the condition, if fewer than ~40 trials (spec §7.1)."""
    base = {"query.cond": cond, "query.locn": "United Kingdom"}
    seen, studies, statuses, conds = set(), [], ["RECRUITING"], [cond]

    def add(batch):
        for s in batch:
            t = _trim(s)
            if t and t["protocolSection"]["identificationModule"]["nctId"] not in seen:
                seen.add(t["protocolSection"]["identificationModule"]["nctId"])
                studies.append(t)

    add(_fetch_all(dict(base, **{"filter.overallStatus": "RECRUITING"})))
    if len(studies) < MIN_TRIALS:
        statuses.append("NOT_YET_RECRUITING")
        add(_fetch_all(dict(base, **{"filter.overallStatus": "NOT_YET_RECRUITING"})))
    if len(studies) < MIN_TRIALS:
        wide = "diabetes OR obesity OR chronic kidney disease"
        conds.append(wide)
        add(_fetch_all({"query.cond": wide, "query.locn": "United Kingdom", "filter.overallStatus": "RECRUITING"}))
    return {"fetched_at": datetime.now(timezone.utc).isoformat(), "source": "ClinicalTrials.gov API v2",
            "query": {"cond": conds, "locn": "United Kingdom", "statuses": statuses, "studyType": "INTERVENTIONAL",
                      "note": "locations trimmed to United Kingdom; site contact details removed"},
            "studies": studies}


def _age(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"(\d+)\s*(\w+)", s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n if unit.startswith("year") else (n // 12 if unit.startswith("month") else 0)


def normalize(study: dict, use_llm: bool = False) -> dict:
    p = study["protocolSection"]
    tid = p["identificationModule"]["nctId"]
    elig = p.get("eligibilityModule", {}) or {}
    sites, seen = [], set()
    for l in p.get("contactsLocationsModule", {}).get("locations", []):
        gp = l.get("geoPoint") or {}
        key = (l.get("facility"), l.get("city"))
        if key in seen or gp.get("lat") is None:
            continue
        seen.add(key)
        sites.append({"name": l.get("facility") or "Research site", "city": l.get("city") or "", "lat": gp["lat"], "lon": gp["lon"]})
    title = p["identificationModule"].get("briefTitle") or ""
    summary = (p.get("descriptionModule", {}) or {}).get("briefSummary") or ""
    interventions = [i["name"] for i in (p.get("armsInterventionsModule", {}) or {}).get("interventions", []) if i.get("name")]
    lay = lay_for(tid, title, summary, interventions, sorted({s["city"] for s in sites}), use_llm)
    crits, source = atomize(elig.get("eligibilityCriteria") or "", use_llm)
    n_inc = n_exc = 0
    for c in crits:
        if c["kind"] == "inclusion":
            n_inc += 1
            c["id"] = f"{tid}__inc_{n_inc}"
        else:
            n_exc += 1
            c["id"] = f"{tid}__exc_{n_exc}"
    return {
        "id": tid, "title": title, "lay_title": lay["lay_title"], "lay_summary": lay["lay_summary"], "lay_source": lay["lay_source"],
        "phase": "/".join(p.get("designModule", {}).get("phases") or []) or "NA",
        "status": p.get("statusModule", {}).get("overallStatus") or "RECRUITING",
        "conditions": p.get("conditionsModule", {}).get("conditions") or [],
        "min_age": _age(elig.get("minimumAge")), "max_age": _age(elig.get("maximumAge")), "sex": elig.get("sex") or "ALL",
        "healthy_volunteers": bool(elig.get("healthyVolunteers")),
        "sites": sites, "last_update": (p.get("statusModule", {}).get("lastUpdatePostDateStruct") or {}).get("date") or "",
        "source": "ctgov", "atomize_source": source, "interventions": interventions, "criteria": crits,
    }


def build_processed(raw: dict, use_llm: bool = False) -> dict:
    trials = [normalize(s, use_llm) for s in raw["studies"]]
    return {"built_at": datetime.now(timezone.utc).isoformat(), "fetched_at": raw.get("fetched_at"), "source": raw.get("source"),
            "query": raw.get("query"), "n_trials": len(trials), "n_criteria": sum(len(t["criteria"]) for t in trials), "trials": trials}


def run(from_raw: bool = False, use_llm: bool = False) -> dict:
    if from_raw:
        raw = json.loads(RAW_TRIALS.read_text(encoding="utf-8"))
    else:
        raw = fetch_raw()
        RAW_TRIALS.parent.mkdir(parents=True, exist_ok=True)
        RAW_TRIALS.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    processed = build_processed(raw, use_llm)
    PROCESSED_TRIALS.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_TRIALS.write_text(json.dumps(processed, ensure_ascii=False, indent=1), encoding="utf-8")
    return processed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-raw", action="store_true")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    out = run(a.from_raw, a.llm)
    print(f"{out['n_trials']} trials, {out['n_criteria']} atomic criteria -> {PROCESSED_TRIALS}")

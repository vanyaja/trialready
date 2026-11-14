"""Caller answer -> structured facts per topic.

Numbers (age, HbA1c value, BMI, months-ago, postcode district) are parsed deterministically.
Bands/classes come from Jev `choice`/`noul` extract questions (jev/questions.py); with MockJev those questions are
answered by the same rules below, so the pipeline is identical with and without a key.
"""
import re
from datetime import datetime
from typing import Optional

from ..trials.hard_filters import is_known_outcode
from .textnum import mmol_to_pct, pct_to_mmol, words_to_digits

MED_CLASSES = {
    "metformin": r"metformin|glucophage",
    "sulfonylurea": r"gliclazide|glimepiride|glipizide|sulph?onylurea|diamicron",
    "dpp4": r"sitagliptin|linagliptin|alogliptin|saxagliptin|vildagliptin|gliptin|januvia|dpp",
    "sglt2": r"dapagliflozin|empagliflozin|canagliflozin|ertugliflozin|gliflozin|forxiga|jardiance|sglt",
    "glp1": r"semaglutide|ozempic|rybelsus|wegovy|liraglutide|victoza|dulaglutide|trulicity|tirzepatide|mounjaro|exenatide|glp|weekly injection",
    "insulin": r"insulin|lantus|novorapid|humalog|toujeo|levemir|tresiba|humulin|novomix",
    "tzd": r"pioglitazone|glitazone",
}
ORAL = {"metformin", "sulfonylurea", "dpp4", "sglt2", "tzd"}
_NEG = re.compile(r"^\W*(no|nope|nah|none|never|not|nothing|negative)\b|\b(don't|do not|haven't|have not|never) (have|had|take|taken|been)\b", re.I)
_DK = re.compile(r"don'?t know|do not know|not sure|no idea|can'?t remember|cannot remember|unsure|couldn'?t (say|tell)", re.I)
HBA1C_BANDS = {"lt_7": (20, 52.9), "7_8": (53, 63.9), "8_10": (64, 85.9), "gt_10": (86, 160)}
BMI_BANDS = {"lt_25": (15, 24.9), "25_30": (25, 29.9), "30_35": (30, 34.9), "35_40": (35, 39.9), "gt_40": (40, 70)}
KIDNEY_EGFR = {"none": (60, 130), "mild": (60, 89), "ckd3": (30, 59), "severe": (0, 29)}


def _neg(t: str) -> bool:
    return bool(_NEG.search(t))


def _band(v: float, bands: dict) -> str:
    for k, (a, b) in bands.items():
        if a <= v <= b + 0.09:
            return k
    return "unknown"


def _ago_months(t: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year|yr)s?", t)
    if m:
        v, u = float(m.group(1)), m.group(2)
        return round({"day": v / 30, "week": v / 4.345, "month": v, "year": v * 12, "yr": v * 12}[u], 1)
    m = re.search(r"\b(?:in|since|around|about)?\s*((?:19|20)\d\d)\b", t)
    if m:
        return max(0.0, (datetime.now().year - int(m.group(1))) * 12.0)
    if re.search(r"last year", t):
        return 12.0
    if re.search(r"last month", t):
        return 1.0
    if re.search(r"last week|this week|yesterday|recently|just now", t):
        return 0.5
    if re.search(r"this year", t):
        return 4.0
    if re.search(r"years ago|long time|ages ago|decade", t):
        return 60.0
    return None


def find_outcode(text: str) -> Optional[str]:
    t = (text or "").upper()
    t = re.sub(r"\b([A-Z])\s+(?=[A-Z0-9]\b)", r"\1", t)  # "N W 3" -> "NW3"
    t = re.sub(r"\b([A-Z]{1,2})\s+(\d{1,2}[A-Z]?)\b", r"\1\2", t)  # "SE 15" -> "SE15"
    cands = re.findall(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)(?:\s*\d[A-Z]{2})?\b", t)
    for c in cands:
        if is_known_outcode(c):
            return c
    return cands[0] if cands else None


def rule_extract(topic: str, answer: str) -> dict:
    """Deterministic extraction. Returned dict always has the topic's full key set."""
    raw = answer or ""
    t = words_to_digits(raw).lower()
    dk = bool(_DK.search(t))
    if topic == "age_sex":
        age = None
        for m in re.finditer(r"\b(\d{2,3})\b", t):
            if 16 <= int(m.group(1)) <= 110:
                age = int(m.group(1))
                break
        sex = "female" if re.search(r"\b(female|woman|lady|girl|mrs|ms|she)\b", t) else ("male" if re.search(r"\b(male|man|gentleman|boy|mr|bloke|he)\b", t) else None)
        return {"age": age, "sex": sex}
    if topic == "location_travel":
        return {"outcode": find_outcode(raw)}
    if topic == "diagnosis_duration":
        if re.search(r"type\s*(1|one|i)\b", t) and not re.search(r"type\s*(2|two|ii)\b", t):
            dx = "type1"
        elif re.search(r"pre-?diabet|borderline", t) or (_neg(t) and not re.search(r"type\s*(2|two|ii)", t)):
            dx = "none"
        elif re.search(r"type\s*(2|two|ii)|diabet|diagnos|\byes\b|\byeah\b|\byep\b|\d", t):
            dx = "type2"
        else:
            dx = "unknown"
        months = _ago_months(t)
        if months is None and re.search(r"recent|new|just", t):
            months = 3.0
        return {"dx": dx, "years": None if months is None else round(months / 12, 2)}
    if topic == "diabetes_meds":
        meds = [c for c, rx in MED_CLASSES.items() if re.search(rx, t)]
        if "insulin" in meds and re.search(r"(no|not|never|don't|without)\s+(\w+\s){0,3}insulin", t):
            meds.remove("insulin")
        none = not meds and bool(re.search(r"\bno(ne|thing)?\b|diet|not (on|taking)|don't take", t))
        return {"known": bool(meds) or none, "meds": meds, "none": none}
    if topic == "hba1c":
        mmol = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent|per cent)", t)
        if m:
            mmol = pct_to_mmol(float(m.group(1)))
        else:
            m = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
            if m:
                v = float(m.group(1))
                mmol = pct_to_mmol(v) if 4 <= v <= 18 else (v if 20 <= v <= 160 else None)
        return {"mmol": mmol, "pct": None if mmol is None else mmol_to_pct(mmol), "band": "unknown" if mmol is None else _band(mmol, HBA1C_BANDS)}
    if topic == "weight_bmi":
        bmi = None
        m = re.search(r"bmi\D{0,12}(\d+(?:\.\d+)?)", t)
        if m:
            bmi = float(m.group(1))
        else:
            kg = h = None
            m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo)", t)
            if m:
                kg = float(m.group(1))
            m = re.search(r"(\d+)\s*(?:st|stone)\s*(\d+)?", t)
            if m and kg is None:
                kg = int(m.group(1)) * 6.35 + (int(m.group(2)) * 0.4536 if m.group(2) else 0)
            m = re.search(r"(\d+(?:\.\d+)?)\s*(lb|pound)", t)
            if m and kg is None:
                kg = float(m.group(1)) * 0.4536
            m = re.search(r"(\d)\s*(?:ft|foot|feet|')\s*(\d{1,2})?", t)
            if m:
                h = int(m.group(1)) * 0.3048 + (int(m.group(2)) * 0.0254 if m.group(2) else 0)
            m = re.search(r"\b(1\.\d{1,2})\s*(m|metre|meter)?\b", t)
            if m and h is None:
                h = float(m.group(1))
            m = re.search(r"\b(1\d{2})\s*(cm|centi)", t)
            if m and h is None:
                h = int(m.group(1)) / 100
            if kg and h:
                bmi = round(kg / (h * h), 1)
            elif not dk:
                m = re.fullmatch(r"\D*(\d{2}(?:\.\d)?)\D*", t)
                if m and 15 <= float(m.group(1)) <= 70:
                    bmi = float(m.group(1))
        return {"bmi": bmi, "band": "unknown" if bmi is None else _band(bmi, BMI_BANDS)}
    if topic == "kidney":
        if re.search(r"dialysis|transplant|stage\s*(4|5|four|five)|severe|kidney failure|end[- ]stage", t):
            band = "severe"
        elif re.search(r"stage\s*(3|three)|moderate|\bckd\b|chronic kidney", t):
            band = "ckd3"
        elif re.search(r"mild|early|slight|stage\s*(1|2)|a bit|protein in", t):
            band = "mild"
        elif _neg(t) or re.search(r"fine|normal|healthy|no problem", t):
            band = "none"
        else:
            band = "unknown"
        return {"band": band}
    if topic == "cardio_recent":
        types = [k for k, rx in {"mi": r"heart attack|myocardial|\bmi\b|stent|bypass", "stroke": r"stroke|\btia\b", "hf": r"heart failure"}.items() if re.search(rx, t)]
        if types and not re.search(r"\b(no|never|not had|haven't had)\b[^.]{0,25}(heart attack|stroke|heart failure)", t) or (re.search(r"\byes\b|\byeah\b", t) and not _neg(t)):
            return {"band": "event", "months_ago": _ago_months(t), "types": types}
        if _neg(t) or re.search(r"\bno\b|never|none", t):
            return {"band": "none", "months_ago": None, "types": []}
        return {"band": "unknown", "months_ago": None, "types": []}
    if topic == "cancer_recent":
        if _neg(t) or re.search(r"\bno\b|never|none", t):
            return {"band": "none", "years_ago": None}
        if re.search(r"cancer|tumou?r|chemo|radiotherap|\byes\b|\byeah\b|lymphoma|leuk|melanoma", t):
            mo = _ago_months(t)
            return {"band": "event", "years_ago": None if mo is None else round(mo / 12, 1)}
        return {"band": "unknown", "years_ago": None}
    if topic == "pregnancy":
        if re.search(r"not applicable|menopaus|hysterectom|i'?m a man|male", t):
            band = "not_applicable"
        elif _neg(t) or re.search(r"\bno\b|neither|none", t):
            band = "no"
        elif re.search(r"breast\s*-?feed|nursing", t):
            band = "breastfeeding"
        elif re.search(r"planning|trying|hope to", t):
            band = "planning"
        elif re.search(r"pregnan|expecting|\byes\b|weeks", t):
            band = "pregnant"
        else:
            band = "unknown"
        return {"band": band}
    if topic == "other_trial_recent":
        if _neg(t) or re.search(r"\bno\b|never|none", t):
            band = "no"
        elif re.search(r"current|at the moment|right now|taking part|i am in|i'm in|\byes\b|\byeah\b", t):
            band = "current"
        elif re.search(r"finished|last|ago|recent|was in", t):
            band = "recent"
        else:
            band = "unknown"
        return {"band": band}
    return {}


def merge_jev(topic: str, facts: dict, answers: dict, min_conf: float = 0.55) -> dict:
    """Overlay Jev extract answers (keys `x__<field>`) on the rule facts. Numbers parsed by rules always win."""
    f = dict(facts)

    def choice(field):
        a = answers.get(f"x__{field}")
        if a and a.get("type") == "choice" and (a.get("confidence") or 0) >= min_conf and a.get("choice") not in (None, "unknown"):
            return a["choice"]
        return None

    if topic == "age_sex":
        f["sex"] = choice("sex") or f.get("sex")
    elif topic == "diagnosis_duration":
        f["dx"] = choice("dx") or f.get("dx")
    elif topic == "diabetes_meds":
        meds = set(f.get("meds", []))
        seen = False
        for c in MED_CLASSES:
            a = answers.get(f"x__med_{c}")
            if a and a.get("type") == "noul":
                seen = True
                (meds.add if a["noul"] >= 0.6 else (meds.discard if a["noul"] <= 0.25 else (lambda _: None)))(c)
        a = answers.get("x__med_none")
        if a and a.get("type") == "noul" and a["noul"] >= 0.6 and not meds:
            f["none"] = True
        f["meds"] = sorted(meds)
        f["known"] = bool(meds) or bool(f.get("none")) or (seen and f.get("known", False))
    elif topic in ("hba1c", "weight_bmi"):
        num_key = "mmol" if topic == "hba1c" else "bmi"
        if f.get(num_key) is None:
            f["band"] = choice("band") or f.get("band", "unknown")
    elif topic in ("kidney", "pregnancy", "other_trial_recent", "cardio_recent", "cancer_recent"):
        c = choice("band")
        if c and not (topic in ("cardio_recent", "cancer_recent") and f.get("band") == "event" and c == "event"):
            f["band"] = c
    return f


def summarize(profile_facts: dict, age=None, sex=None) -> str:
    """One-paragraph patient summary sent to Jev as part of `state`."""
    f, parts = profile_facts or {}, []
    if age or sex:
        parts.append(f"{age or 'Adult'}{'-year-old' if age else ''} {sex or 'person'}.")
    d = f.get("diagnosis_duration")
    if d:
        parts.append({"type2": "Has type 2 diabetes", "type1": "Has type 1 diabetes", "none": "Does not have diabetes"}.get(d.get("dx"), "Diabetes status unclear")
                     + (f", diagnosed about {d['years']:g} years ago." if d.get("years") is not None else "."))
    m = f.get("diabetes_meds")
    if m and m.get("known"):
        parts.append("Takes no diabetes medicines." if not m.get("meds") else "Current diabetes medicines (classes): " + ", ".join(m["meds"]) + "." + ("" if "insulin" in m["meds"] else " Not on insulin."))
    h = f.get("hba1c")
    if h and h.get("mmol") is not None:
        parts.append(f"Latest HbA1c {h['mmol']:g} mmol/mol ({h['pct']:g}%).")
    w = f.get("weight_bmi")
    if w and w.get("bmi") is not None:
        parts.append(f"BMI about {w['bmi']:g} kg/m2.")
    k = f.get("kidney")
    if k and k.get("band") not in (None, "unknown"):
        parts.append({"none": "No known kidney disease.", "mild": "Mild kidney problems.", "ckd3": "Moderate chronic kidney disease (about stage 3).", "severe": "Severe kidney disease (stage 4-5 or dialysis)."}[k["band"]])
    c = f.get("cardio_recent")
    if c and c.get("band") == "none":
        parts.append("No heart attack, stroke or heart-failure admission ever.")
    elif c and c.get("band") == "event":
        when = f" about {c['months_ago']:g} months ago" if c.get("months_ago") is not None else ""
        parts.append(f"Past cardiovascular event ({', '.join(c.get('types') or ['unspecified'])}){when}.")
    ca = f.get("cancer_recent")
    if ca and ca.get("band") == "none":
        parts.append("No cancer in the last five years.")
    elif ca and ca.get("band") == "event":
        parts.append("Cancer or cancer treatment" + (f" about {ca['years_ago']:g} years ago." if ca.get("years_ago") is not None else " reported."))
    p = f.get("pregnancy")
    if p and p.get("band") not in (None, "unknown"):
        parts.append({"no": "Not pregnant or breastfeeding.", "not_applicable": "Pregnancy not applicable.", "pregnant": "Currently pregnant.", "breastfeeding": "Currently breastfeeding.", "planning": "Planning a pregnancy."}[p["band"]])
    o = f.get("other_trial_recent")
    if o and o.get("band") not in (None, "unknown"):
        parts.append({"no": "Not in another research study now or in the last 3 months.", "current": "Currently taking part in another research study.", "recent": "Took part in another research study recently."}[o["band"]])
    return " ".join(parts)

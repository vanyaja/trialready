"""Rule engine behind MockJev (spec §8.2: deterministic pseudo-probabilities from keyword rules).

`judge(text, facts)` answers "is this criterion statement true for the patient?" -> True / False / None(unknown).
It is a stand-in so the UI, voice loop and tests run without a key. It is NOT a clinical model and its
benchmark numbers mean nothing.
"""
import hashlib
import re
from typing import Optional

from ..matching.extract import BMI_BANDS, HBA1C_BANDS, KIDNEY_EGFR, MED_CLASSES, ORAL
from ..matching.textnum import constraints, interval_in, norm, pct_to_mmol, to_interval, to_years, value_in
from ..trials.topics import tag_topic

_NEG_PREFIX = re.compile(r"^(no|not|without|absence of|free (of|from)|never|non[- ])\b")
_UNABLE = re.compile(r"\b(unable|not able|inability|cannot|unwilling|not willing|incapable)\b")
_TIME_UNITS = ("day", "week", "month", "year", "yr")


def _window_years(t: str) -> Optional[float]:
    for _, v, u in constraints(t):
        if u and u.startswith(_TIME_UNITS):
            return to_years(v, u)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year|yr)s?", t)
    return to_years(float(m.group(1)), m.group(2)) if m else None


def _age_sex(t, f):
    a = f.get("age_sex") or {}
    age, sex = a.get("age"), a.get("sex")
    res = []
    cons = [c for c in constraints(t) if (c[2] is None or c[2].startswith(("year", "yr"))) and 5 <= c[1] <= 110]
    if cons:
        if age is None:
            return None
        lo, hi = to_interval(cons)
        res.append(value_in(age, lo, hi))
    both = re.search(r"male or female|males? (and|&|or) females?|men (and|or) women|either sex|all gender|any gender", t)
    only_f = re.search(r"\b(females?|women)\b", t) and not re.search(r"\b(males?|men)\b", t.replace("female", "").replace("women", ""))
    only_m = re.search(r"\b(males?|men)\b", t.replace("female", "").replace("women", "")) and not re.search(r"\b(females?|women)\b", t)
    if both:
        res.append(True)
    elif only_f or only_m:
        if re.search(r"post-?menopaus", t):
            return None
        if sex is None:
            return None
        res.append(sex == ("female" if only_f else "male"))
    return all(res) if res else None


def _diagnosis(t, f):
    d = f.get("diagnosis_duration") or {}
    dx, years = d.get("dx"), d.get("years")
    if dx in (None, "unknown"):
        return None
    has_t2, has_t1 = bool(re.search(r"type (2|ii|two)|t2d", t)), bool(re.search(r"type (1|i|one)\b|t1d", t))
    if re.search(r"relative|family history|autoantibod|other than (t2d|type 2)|life expectancy", t):
        return None
    if re.search(r"(except|other than|not clearly|apart from)( for)? (t2|type 2|type ii)", t):
        has_t2 = False
    if re.search(r"ketoacidosis|hyperosmolar|\bmody\b|gestational|secondary (forms of )?diabetes|pancreat", t):
        # "T1DM or T2DM, or history of ketoacidosis": true if the patient has a listed type, otherwise unknown
        return True if ((has_t1 and dx == "type1") or (has_t2 and dx == "type2")) else None
    if re.search(r"without (type 2 )?diabet|no (history of |diagnosis of )?(type 2 )?diabet|non-?diabet|do(es)? not have (type 2 )?diabet", t):
        return dx == "none"
    if has_t1 and has_t2:  # "type 1, type 2 or any other diabetes"
        truth = dx in ("type1", "type2")
    elif has_t1:
        truth = dx == "type1"
    elif has_t2:
        truth = dx == "type2"
    elif re.search(r"(diagnos\w+ (of|with)|have|has|with|history of|living with) (insulin[- ]treated )?diabet", t):
        truth = dx in ("type1", "type2")
    else:
        return None
    if not truth:
        return False
    cons = [(op, to_years(v, u), u) for op, v, u in constraints(t) if u and u.startswith(_TIME_UNITS)]
    cons = [c for c in cons if c[1] is not None]
    m = re.search(r"within (the )?(last|past|previous|preceding)?\s*(\d+)\s*(day|week|month|year)s?", t)
    if m:
        cons.append(("<=", to_years(float(m.group(3)), m.group(4)), m.group(4)))
    if cons:
        if years is None:
            return None
        lo, hi = to_interval(cons)
        return value_in(years, lo, hi)
    return True


def _meds(t, f):
    m = f.get("diabetes_meds") or {}
    if not m.get("known"):
        return None
    have = set(m.get("meds") or [])
    parts = re.split(r"\b(?:but not|not on|other than|except|excluding|not (?:currently )?(?:taking|treated with|receiving|using)|na[iï]ve to|without|no (?:prior|previous|current) (?:use|treatment)(?: of| with)?)\b", t, maxsplit=1)
    pos, neg = parts[0], parts[1] if len(parts) > 1 else ""

    def classes(seg):
        cs = {c for c, rx in MED_CLASSES.items() if re.search(rx, seg)}
        if re.search(r"oral (glucose|glyc|anti|hypoglyc|agent)|oral .{0,20}lowering|tablet", seg):
            cs |= ORAL
        if re.search(r"(glucose|glyc\w+)[- ]lowering (medic|therap|drug|agent|treat)|anti-?diabetic|anti-?hyperglyc|diabetes medic", seg) and not cs:
            cs |= set(MED_CLASSES)
        return cs

    p, n = classes(pos), classes(neg)
    if "other than" in t or "except" in t:  # "any agent other than metformin" -> true if they take something outside the list
        if not n or re.search(r"other than (those |that |as )?(stated|specified|listed|permitted|allowed|described)", t):
            return None
        return bool(have - n)
    if not p and not n:
        return None
    ok_pos = True
    if p:
        ok_pos = bool(have & p) or (not have and bool(re.search(r"diet|lifestyle|drug[- ]na[iï]ve|treatment[- ]na[iï]ve", pos)))
    return ok_pos and not (have & n)


def _hba1c(t, f):
    h = f.get("hba1c") or {}
    cons = []
    for op, v, u in constraints(t):
        if u in ("%", "percent") or (u is None and 4 <= v <= 18):
            cons.append((op, pct_to_mmol(v), "mmol"))
        elif (u and u.startswith("mmol")) or (u is None and 20 <= v <= 160):
            cons.append((op, v, "mmol"))
    if not cons:
        return None
    lo, hi = to_interval(cons)
    if h.get("mmol") is not None:
        return value_in(h["mmol"], lo, hi)
    if h.get("band") in HBA1C_BANDS:
        return interval_in(HBA1C_BANDS[h["band"]], lo, hi)
    return None


def _bmi(t, f):
    w = f.get("weight_bmi") or {}
    cons = [c for c in constraints(t) if (c[2] in ("kg/m2", None)) and 15 <= c[1] <= 70]
    if not re.search(r"bmi|body mass index", t):
        if re.match(r"^(have |has |with |diagnos\w+ (of|with) |people with |adults with )?(obesity|obese)\b", t) and len(t) < 70 and not re.search(r"caus|secondary|due to|induced", t):
            cons = [(">=", 30.0, None)]
        else:
            return None
    if not cons:
        return None
    lo, hi = to_interval(cons)
    lows = [v for op, v, _ in cons if op in (">=", ">")]
    if len(lows) > 1 and re.search(r"\bor\b", t):  # "BMI >= 30 OR BMI >= 27 with a comorbidity"
        lo = min(lows)
    if w.get("bmi") is not None:
        return value_in(w["bmi"], lo, hi)
    if w.get("band") in BMI_BANDS:
        return interval_in(BMI_BANDS[w["band"]], lo, hi)
    return None


def _kidney(t, f):
    band = (f.get("kidney") or {}).get("band")
    if band not in KIDNEY_EGFR:
        return None
    cons = [c for c in constraints(t) if (c[2] is None or c[2].startswith("ml")) and 5 <= c[1] <= 130]
    if cons and re.search(r"egfr|glomerular|filtration|creatinine clearance", t):
        lo, hi = to_interval(cons)
        return interval_in(KIDNEY_EGFR[band], lo, hi)
    if re.search(r"dialysis|end[- ]stage|transplant|severe|stage (4|5|iv|v)\b", t):
        return band == "severe"
    if re.search(r"renal|kidney|nephropath|ckd", t):
        return band in ("ckd3", "severe") if band != "mild" else None
    return None


def _cardio(t, f):
    c = f.get("cardio_recent") or {}
    band = c.get("band")
    if band in (None, "unknown"):
        return None
    if re.search(r"\brisk\b|planned|blood pressure|hypertension|arrhythm|fibrillation", t):
        return None
    if re.search(r"nyha|ejection fraction|lvef", t):
        return False if band == "none" else None
    if band == "none":
        return False
    types = set(c.get("types") or [])
    wanted = {k for k, rx in {"mi": r"myocardial|heart attack|coronary|angina|revascular|cardiovascular|cardiac", "stroke": r"stroke|transient|tia|cerebrovascular|cardiovascular", "hf": r"heart failure|cardiovascular|cardiac"}.items() if re.search(rx, t)}
    if types and wanted and not (types & wanted):
        return False
    win = _window_years(t)
    if win is None:
        return True
    if c.get("months_ago") is None:
        return None
    return c["months_ago"] / 12 <= win


def _cancer(t, f):
    c = f.get("cancer_recent") or {}
    band = c.get("band")
    if band in (None, "unknown"):
        return None
    if band == "none":
        return False
    win = _window_years(t)
    if win is None:
        return True
    if c.get("years_ago") is None:
        return None
    return c["years_ago"] <= win


def _pregnancy(t, f):
    band = (f.get("pregnancy") or {}).get("band")
    sex = (f.get("age_sex") or {}).get("sex")
    if re.search(r"contracepti|child[- ]?bearing", t) and not re.search(r"pregnan|breast|lactat", t):
        return False if (sex == "male" or band == "not_applicable") and re.search(r"unable|unwilling|not using|refus", t) else None
    if sex == "male" or band in ("no", "not_applicable"):
        return False
    if band in ("pregnant", "breastfeeding"):
        return True
    if band == "planning":
        return True if re.search(r"plan|intend|consider|wish", t) else None
    return None


def _other_trial(t, f):
    band = (f.get("other_trial_recent") or {}).get("band")
    if band in (None, "unknown"):
        return None
    if band == "no":
        return False
    if band == "current":
        return True
    return True if _window_years(t) else None


def judge(text: str, facts: dict, consent: bool = True, language: str = "en") -> Optional[bool]:
    t = norm(text)
    topic = tag_topic(text)
    inv = False
    if re.search(r"(one|any|either|more) of the following", t):
        return None  # alternatives list: needs a model (or a nurse), not keyword rules
    if topic == "other":
        if re.search(r"informed consent|capacity to consent|willing(ness)? (and able )?to (give|provide) .{0,20}consent", t):
            return (not consent) if _UNABLE.search(t) else consent
        if "english" in t and language == "en" and len(t) < 90:
            return False if _UNABLE.search(t) else True
        return None
    if topic == "location_travel":
        return None
    fn = {"age_sex": _age_sex, "diagnosis_duration": _diagnosis, "diabetes_meds": _meds, "hba1c": _hba1c, "weight_bmi": _bmi,
          "kidney": _kidney, "cardio_recent": _cardio, "cancer_recent": _cancer, "pregnancy": _pregnancy, "other_trial_recent": _other_trial}[topic]
    if topic in ("kidney", "cardio_recent", "cancer_recent", "pregnancy", "other_trial_recent") and _NEG_PREFIX.search(t):
        inv = True
    r = fn(t, facts or {})
    return (not r) if (inv and r is not None) else r


def _jitter(key: str, lo: float, hi: float) -> float:
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return lo + (hi - lo) * h


def probs_for(verdict: Optional[bool], key: str) -> dict:
    if verdict is None:
        u = _jitter(key, 0.955, 0.985)
        return {"met": (1 - u) / 2, "not_met": (1 - u) / 2, "unknown": u}
    p = _jitter(key, 0.955, 0.985)
    rest = (1 - p) / 2
    return {"met": p, "not_met": rest, "unknown": rest} if verdict else {"met": rest, "not_met": p, "unknown": rest}


# ---------------------------------------------------------------- guards (spec §8.6) and misc nouls

_URGENT = re.compile(r"chest pain|crushing|can'?t breathe|cannot breathe|struggling to breathe|face (is )?droop|slurr|suicid|end my life|kill myself|overdose|passing out|about to faint|collaps|unconscious|severe (hypo|bleed)", re.I)
_PAST = re.compile(r"\bago\b|last (year|month)|years|months|\bhad\b|history|used to|in (19|20)\d\d", re.I)
_STOP = re.compile(r"\b(stop|hang up|end (the |this )?call|that'?s enough|i (have|'ve got) to go|goodbye|bye|not interested|no more questions)\b", re.I)
_WITHDRAW = re.compile(r"remove me|delete (my|all|everything)|withdraw|forget me|take me off|don'?t (store|keep)|erase", re.I)
_ADVICE = re.compile(r"should i (take|stop|change|start|increase|reduce)|is it safe (to|for)|what (dose|should i)|can you (recommend|advise)|which (medicine|medication) should|do you think i should", re.I)
_NONANSWER = re.compile(r"^\W*(what|pardon|sorry|huh|eh|hmm+|um+|uh+|er+|hello|come again|say that again|what do you mean|i don'?t understand)\W*\??$", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|fine|go ahead|of course|please|alright|all right|i do|happy to|go on|absolutely|certainly|that'?s fine|sounds good|i agree|correct|right)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|not|don'?t|do not|never|rather not|stop)\b", re.I)


def guard(name: str, answer: str) -> float:
    a = answer or ""
    if name == "urgent_symptom":
        return 0.93 if (_URGENT.search(a) and not _PAST.search(a)) else 0.02
    if name == "wants_to_stop":
        return 0.9 if _STOP.search(a) else 0.03
    if name == "withdraw_consent":
        return 0.92 if _WITHDRAW.search(a) else 0.02
    if name == "asks_medical_advice":
        return 0.88 if _ADVICE.search(a) else 0.03
    if name == "answered_the_question":
        return 0.12 if (not a.strip() or _NONANSWER.match(a.strip())) else 0.93
    if name == "agrees":
        return 0.95 if (_YES.search(a) and not _NO.search(a)) else (0.05 if _NO.search(a) else 0.5)
    if name == "no_change":
        return 0.92 if re.search(r"^\W*(no|nope|nothing|same|no change|not changed|unchanged|still the same|none)\b", a, re.I) and len(a) < 60 else 0.1
    return 0.5

"""Score every candidate trial for a profile (spec §8).

For trial t with inclusion set I and exclusion set E and Jev probabilities per criterion:
  p_ok(c)  = P(met) for c in I,  P(not_met) for c in E
  p_bad(c) = the opposite clear answer;  p_unk(c) = P(unknown)
  P_opt(t) = prod_c (p_ok + p_unk)                    "still possible"
  P_pes(t) = prod_c  p_ok                             "confirmed"            (PES_MODE=strict, literal spec)
  knock-out: any p_bad(c) >= KNOCKOUT

PES_MODE=screenable (default) changes one thing: a criterion that a phone screen cannot confirm contributes
(p_ok + p_unk) to P_pes instead of p_ok. That covers (a) topic `other` (lab values, investigator judgement),
(b) cohort-specific criteria, and (c) criteria whose topic HAS been asked and are still unknown (the caller could
not settle them). So P_pes means "confirmed as far as a phone screen can", `uncertainty` = P_opt - P_pes is exactly
what further questions could still resolve, and it reaches ~0 once the relevant topics are asked.
Under the literal formula, one unanswerable lab criterion pins P_pes at ~0: the stop rule never fires and nobody is
ever referred. The unresolved criteria are listed as "for the nurse" on every referral. P_pes <= P_opt in both modes.
"""
import math
import time
from typing import Optional

from sqlalchemy import delete
from sqlmodel import select

from ..config import get_settings
from ..db import STORE, db_session
from ..jev.client import JevRouter
from ..jev.mock_rules import judge, probs_for
from ..jev.questions import criterion_question
from ..models import Criterion, CriterionScore, Profile, TrialScore
from ..trials import hard_filters
from ..trials.topics import ASKABLE
from . import thresholds as T
from .extract import summarize

UNKNOWN_PRIOR = {"met": 0.015, "not_met": 0.015, "unknown": 0.97}
_scores: dict[str, dict[str, dict]] = {}  # profile_id -> criterion_id -> probs


def screenable(c: Criterion) -> bool:
    return c.topic in ASKABLE and not c.cohort_specific


def can_knock_out(c: Criterion) -> bool:
    return not c.cohort_specific


def p_parts(c: Criterion, pr: dict) -> tuple[float, float, float]:
    """(p_ok, p_bad, p_unk) for one criterion."""
    if c.kind == "inclusion":
        return pr["met"], pr["not_met"], pr["unknown"]
    return pr["not_met"], pr["met"], pr["unknown"]


def trial_math(criteria: list[Criterion], probs: dict[str, dict], pes_mode: Optional[str] = None, asked=frozenset()) -> dict:
    """Pure function: probabilities -> P_opt / P_pes / uncertainty / knock-out."""
    mode = pes_mode or get_settings().pes_mode
    log_opt = log_pes = 0.0
    ko: Optional[str] = None
    ko_p = 0.0
    n_scr = n_ok = n_unk = n_nurse = n_inc = 0
    for c in criteria:
        ok, bad, unk = p_parts(c, probs.get(c.id, UNKNOWN_PRIOR))
        pending = screenable(c) and c.topic not in asked  # a question could still settle this one
        if screenable(c):
            n_scr += 1
            n_ok += ok >= 0.5
            n_unk += unk >= 0.5 and pending
        n_nurse += unk >= 0.5 and not pending
        n_inc += c.kind == "inclusion" and not c.hard_filter and not c.cohort_specific and ok >= 0.5
        opt = min(1.0, ok + unk)
        log_opt += math.log(max(opt, 1e-9))
        pes = ok if (mode == "strict" or pending) else opt
        log_pes += math.log(max(pes, 1e-9))
        if can_knock_out(c) and bad >= T.KNOCKOUT and bad > ko_p:
            ko, ko_p = c.id, bad
    p_opt, p_pes = math.exp(log_opt), math.exp(log_pes)
    return {"p_opt": p_opt, "p_pes": min(p_pes, p_opt), "uncertainty": max(0.0, p_opt - p_pes), "knocked_out": ko is not None, "knock_criterion_id": ko,
            "n_criteria": len(criteria), "n_screenable": n_scr, "n_confirmed": int(n_ok), "n_unknown": int(n_unk), "n_nurse": int(n_nurse), "n_inc_confirmed": int(n_inc)}


def build_state(profile: Profile, consent: bool = True) -> dict:
    facts = {k: {kk: vv for kk, vv in v.items() if kk not in ("answer_text", "ts", "question")} for k, v in (profile.facts or {}).items()}
    answers = [{"topic": k, "question": v.get("question", ""), "answer": v.get("answer_text", "")} for k, v in (profile.facts or {}).items() if v.get("answer_text")]
    return {"patient": facts, "summary": summarize(profile.facts, profile.age, profile.sex), "answers": answers,
            "consent": consent, "language": profile.language or "en"}


def cached_scores(profile_id: str) -> dict[str, dict]:
    if profile_id not in _scores:
        with db_session() as db:
            rows = db.exec(select(CriterionScore).where(CriterionScore.profile_id == profile_id)).all()
        _scores[profile_id] = {r.criterion_id: {"met": r.p_met, "not_met": r.p_not_met, "unknown": r.p_unknown} for r in rows}
    return _scores[profile_id]


def forget(profile_id: str):
    _scores.pop(profile_id, None)


def candidates(profile: Profile, trial_ids: Optional[list[str]] = None) -> tuple[list[dict], list[dict]]:
    """Deterministic hard filters + K_CANDIDATES cut. Returns (kept, filtered_out)."""
    kept, out = [], []
    for t in STORE.all():
        if trial_ids is not None and t.id not in trial_ids:
            continue
        hf = hard_filters.check(t, profile.age, profile.sex, profile.postcode_district)
        row = {"trial": t, "distance_km": hf["distance_km"], "site": hf["site"], "reason": hf["reason"]}
        (kept if hf["ok"] else out).append(row)
    kept.sort(key=lambda r: -hard_filters.prior(r["trial"], r["distance_km"]))
    k = get_settings().k_candidates
    for r in kept[k:]:
        r["reason"] = "outside top candidates"
    return kept[:k], out + kept[k:]


async def score_profile(profile: Profile, router: JevRouter, trial_ids: Optional[list[str]] = None,
                        timeout: Optional[float] = None, persist: bool = True) -> dict:
    """Hard filters -> Jev on every criterion of every candidate -> trial scores. Returns ranked results."""
    t0 = time.perf_counter()
    kept, filtered = candidates(profile, trial_ids)
    state = build_state(profile)
    prev = cached_scores(profile.id)
    questions, local = {}, {}
    for r in kept:
        for c in STORE.crit(r["trial"].id):
            if c.hard_filter:  # age/sex-only criteria: deterministic, never sent to Jev
                local[c.id] = probs_for(judge(c.text, state["patient"]), c.id)
            else:
                questions[c.id] = criterion_question(c.kind, c.text)
    answers = await router.decide(state, questions, timeout=timeout) if questions else {}
    probs = dict(local)
    missing = 0
    for cid in questions:
        a = answers.get(cid)
        if a:
            probs[cid] = a["probabilities"]
        else:  # timeout / backend failure: keep the previous turn's decision (spec §17.2)
            missing += 1
            probs[cid] = prev.get(cid, UNKNOWN_PRIOR)
    results = []
    for r in kept:
        t = r["trial"]
        m = trial_math(STORE.crit(t.id), probs, asked=frozenset(profile.facts or {}))
        results.append({"trial_id": t.id, "lay_title": t.lay_title or t.title, "title": t.title, "source": t.source, "status": t.status,
                        "distance_km": r["distance_km"], "site": r["site"], **m, "band": T.band(m["p_pes"], m["p_opt"], m["knocked_out"], m["n_inc_confirmed"])})
    results.sort(key=lambda x: (x["knocked_out"], -x["p_opt"], -x["p_pes"]))
    prev.update(probs)
    if persist:
        _persist(profile.id, probs, results, router.model_label)
    return {"results": results, "filtered": [{"trial_id": f["trial"].id, "lay_title": f["trial"].lay_title, "reason": f["reason"]} for f in filtered],
            "n_total": len(kept) + len(filtered), "n_candidates": len(kept),
            "n_possible": sum(1 for x in results if not x["knocked_out"]),
            "decisions": len(answers), "missing": missing, "latency_ms": round((time.perf_counter() - t0) * 1000)}


def _persist(profile_id: str, probs: dict, results: list[dict], model: str):
    with db_session() as db:
        if probs:
            db.exec(delete(CriterionScore).where(CriterionScore.profile_id == profile_id, CriterionScore.criterion_id.in_(list(probs))))
            db.add_all([CriterionScore(profile_id=profile_id, criterion_id=cid, p_met=p["met"], p_not_met=p["not_met"], p_unknown=p["unknown"], model=model)
                        for cid, p in probs.items()])
        ids = [r["trial_id"] for r in results]
        if ids:
            db.exec(delete(TrialScore).where(TrialScore.profile_id == profile_id, TrialScore.trial_id.in_(ids)))
            db.add_all([TrialScore(profile_id=profile_id, trial_id=r["trial_id"], p_opt=r["p_opt"], p_pes=r["p_pes"], uncertainty=r["uncertainty"],
                                   knocked_out=r["knocked_out"], knock_criterion_id=r["knock_criterion_id"], distance_km=r["distance_km"]) for r in results])
        db.commit()


def criteria_table(profile: Profile, trial_id: str) -> list[dict]:
    """Per-criterion rows for the UI / referral evidence: status, confidence, evidence quote."""
    probs = cached_scores(profile.id)
    facts = profile.facts or {}
    rows = []
    for c in STORE.crit(trial_id):
        pr = probs.get(c.id, UNKNOWN_PRIOR)
        status = max(pr, key=pr.get)
        ok, bad, unk = p_parts(c, pr)
        quote = (facts.get(c.topic) or {}).get("answer_text") if status != "unknown" or c.topic in facts else None
        rows.append({"criterion_id": c.id, "kind": c.kind, "topic": c.topic, "text": c.text, "lay_text": c.lay_text or c.text,
                     "status": status, "confidence": round(pr[status], 3), "ok": True if ok >= 0.5 else (False if bad >= 0.5 else None),
                     "p_ok": round(ok, 3), "p_bad": round(bad, 3), "p_unk": round(unk, 3), "evidence": quote,
                     "screenable": screenable(c), "pending": screenable(c) and c.topic not in facts and not c.hard_filter,
                     "hard_filter": c.hard_filter, "cohort_specific": c.cohort_specific})
    return rows

"""Intake loop: ElevenLabs server tools -> FastAPI (spec §9.1, §9.3, §15). The same functions drive the built-in
browser call and the persona replay tests, so every channel exercises one code path."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlmodel import select

from ..config import get_settings
from ..db import STORE, db_session
from ..events import bus
from ..jev.client import get_router
from ..jev.questions import ethnicity_question, extract_questions, guard_questions
from ..matching import adaptive, engine
from ..matching import thresholds as T
from ..matching.extract import merge_jev, rule_extract
from ..models import Answer, CallLog, ContactDetail, CriterionScore, Outcome, Profile, Referral, ScreenSession, TrialScore, now
from ..trials.topics import TOPICS

router = APIRouter(prefix="/api/tools", tags=["voice-tools"])

FIRST_MESSAGE = ("Hi, I'm the TrialReady assistant. I can check, in about five minutes, whether there are diabetes research studies near you "
                 "that you might be able to join. I'm an AI, this is not medical advice, and a research nurse always makes the final decision. "
                 "Is it OK if I ask you a few questions and store your answers for this check?")
SAFETY_MESSAGE = "This sounds urgent. Please hang up and call 999, or NHS 111 if it's not an emergency."
ADVICE_MESSAGE = "I can't give medical advice; your GP or the research team can help."
FUTURE_CONTACT_Q = "Can we call you if a new study you might fit opens? You can say 'remove me' at any time."


def tool_auth(x_tool_secret: Optional[str] = Header(default=None)):
    secret = get_settings().tool_shared_secret
    if secret and x_tool_secret != secret:
        raise HTTPException(401, "bad tool secret")


def age_band(age: Optional[int]) -> Optional[str]:
    if age is None:
        return None
    for lo, hi in ((18, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 79)):
        if lo <= age <= hi:
            return f"{lo}-{hi}"
    return "80+" if age >= 80 else "under 18"


def _load(db, session_id: str) -> tuple[ScreenSession, Profile]:
    sess = db.get(ScreenSession, session_id)
    if not sess:
        raise HTTPException(404, "unknown session_id")
    prof = db.exec(select(Profile).where(Profile.session_id == session_id)).first()
    if not prof:
        prof = Profile(session_id=session_id, language=sess.language)
        db.add(prof)
        db.commit()
    return sess, prof


def create_session(channel: str = "web", language: str = "en", conversation_id: Optional[str] = None) -> dict:
    with db_session() as db:
        sess = ScreenSession(channel=channel, language=(language or "en")[:8], eleven_conversation_id=conversation_id)
        db.add(sess)
        db.add(Profile(session_id=sess.id, language=sess.language))
        db.commit()
    bus.publish("session_started", {"session_id": sess.id, "channel": channel, "language": sess.language, "trials_total": len(STORE.all())}, sess.id)
    return {"session_id": sess.id, "first_message": FIRST_MESSAGE}


def delete_profile_data(db, profile_id: str, session_id: Optional[str] = None):
    """'Remove me': erase the profile and everything derived from it."""
    ref_ids = [r.id for r in db.exec(select(Referral).where(Referral.profile_id == profile_id)).all()]
    if ref_ids:
        db.exec(delete(Outcome).where(Outcome.referral_id.in_(ref_ids)))
    for model in (CriterionScore, TrialScore, Referral, CallLog):
        db.exec(delete(model).where(model.profile_id == profile_id))
    db.exec(delete(ContactDetail).where(ContactDetail.profile_id == profile_id))
    if session_id:
        db.exec(delete(Answer).where(Answer.session_id == session_id))
    db.exec(delete(Profile).where(Profile.id == profile_id))
    engine.forget(profile_id)


def _public_top(results: list[dict], n: int = 3) -> list[dict]:
    return [{"lay_title": r["lay_title"], "p_opt": round(r["p_opt"], 2)} for r in results if not r["knocked_out"]][:n]


def _event_top(results: list[dict], n: int = 10) -> list[dict]:
    return [{"trial_id": r["trial_id"], "lay_title": r["lay_title"], "p_opt": round(r["p_opt"], 3), "p_pes": round(r["p_pes"], 3),
             "uncertainty": round(r["uncertainty"], 3), "knocked_out": r["knocked_out"], "band": r["band"], "source": r["source"],
             "distance_km": r["distance_km"], "site": (r["site"] or {}).get("name"), "knock_criterion_id": r["knock_criterion_id"],
             "knock_text": (STORE.by_id[r["knock_criterion_id"]].lay_text if r["knock_criterion_id"] in STORE.by_id else None),
             "n_screenable": r["n_screenable"], "n_confirmed": r["n_confirmed"], "n_unknown": r["n_unknown"], "n_nurse": r["n_nurse"]} for r in results[:n]]


async def record_consent(session_id: str, screening: bool, future_contact: Optional[bool] = None, language: Optional[str] = None,
                         first_name: Optional[str] = None, phone: Optional[str] = None) -> dict:
    with db_session() as db:
        sess, prof = _load(db, session_id)
        if language:
            sess.language = prof.language = language[:8]
        if not screening and not sess.consent_screening:
            sess.status = "declined"
            db.add(sess)
            db.commit()
            bus.publish("consent", {"screening": False}, session_id)
            return {"ok": True, "done": True, "say": "No problem at all. Nothing has been stored. Thank you for your time, goodbye."}
        sess.consent_screening = True
        sess.consent_ts = sess.consent_ts or now()
        if future_contact is not None:
            sess.consent_contact = bool(future_contact)
            prof.registry_member = bool(future_contact)
        if first_name:
            prof.first_name = first_name.strip()[:40]
        if phone and future_contact:
            e164 = re.sub(r"[^\d+]", "", phone)
            if e164.startswith("0"):
                e164 = "+44" + e164[1:]
            if re.fullmatch(r"\+\d{8,15}", e164):
                db.merge(ContactDetail(profile_id=prof.id, phone_e164=e164))
        out = {"ok": True, "done": False, "registry_member": prof.registry_member}
        if sess.status == "started":
            sess.status = "screening"
            sess.current_topic = "age_sex"
            out.update(topic="age_sex", next_question=TOPICS["age_sex"]["question_en"])
        db.add(sess)
        db.add(prof)
        db.commit()
    bus.publish("consent", {"screening": True, "future_contact": future_contact, "registry_member": out["registry_member"]}, session_id)
    if out.get("next_question"):
        bus.publish("next_question", {"topic": out["topic"], "question": out["next_question"]}, session_id)
    return out


async def record_answer(session_id: str, answer_text: str, topic: Optional[str] = None) -> dict:
    s = get_settings()
    jev = get_router()
    with db_session() as db:
        sess, prof = _load(db, session_id)
        if not sess.consent_screening:
            return {"done": True, "say": "I need your permission before I can ask any questions.", "error": "no_consent"}
        topic = topic if topic in TOPICS else (sess.current_topic or "age_sex")
        question = TOPICS[topic]["question_en"]

        # 1) one Jev call: per-utterance guards + band extraction for this topic (spec §8.6)
        a = await jev.decide({"topic": topic, "question": question, "answer": answer_text},
                             {**guard_questions(), **extract_questions(topic)}, timeout=s.jev_turn_timeout_s)
        g = lambda k, default: (a.get(f"g__{k}") or {}).get("noul", default)  # noqa: E731
        if g("urgent_symptom", 0.0) >= T.URGENT:
            sess.status = "safety"
            db.add(sess)
            db.commit()
            bus.publish("safety", {"message": SAFETY_MESSAGE, "answer": answer_text}, session_id)
            return {"done": True, "safety_message": SAFETY_MESSAGE, "top_trials": []}
        if g("withdraw_consent", 0.0) >= T.WITHDRAW:
            delete_profile_data(db, prof.id, session_id)
            sess.status, sess.consent_contact, sess.consent_screening = "withdrawn", False, False
            db.add(sess)
            db.commit()
            bus.publish("withdrawn", {}, session_id)
            return {"done": True, "withdrawn": True, "say": "Done. I have deleted your answers and removed you from our list. Take care, goodbye.", "top_trials": []}
        say = ADVICE_MESSAGE if g("asks_medical_advice", 0.0) >= T.MEDICAL_ADVICE else None
        wants_stop = g("wants_to_stop", 0.0) >= T.WANTS_STOP
        if g("answered_the_question", 1.0) < T.ANSWERED_MIN and not wants_stop and topic not in (sess.reasked or []):
            sess.reasked = list(sess.reasked or []) + [topic]
            db.add(sess)
            db.commit()
            q = TOPICS[topic]["question_alt"]
            bus.publish("next_question", {"topic": topic, "question": q, "reask": True}, session_id)
            return {"done": False, "topic": topic, "next_question": q, "say": say, "reask": True, "top_trials": []}

        # 2) facts
        if not wants_stop or len(answer_text.split()) > 4:
            facts = merge_jev(topic, rule_extract(topic, answer_text), a)
            facts.update(answer_text=answer_text, question=question, ts=now().isoformat())
            pf = dict(prof.facts or {})
            pf[topic] = facts
            if topic == "age_sex":
                prof.age, prof.sex, prof.age_band = facts.get("age"), facts.get("sex"), age_band(facts.get("age"))
                if prof.sex == "male" or (prof.age or 0) >= 56:
                    pf.setdefault("pregnancy", {"band": "not_applicable", "derived": True})
            if topic == "location_travel":
                prof.postcode_district = facts.get("outcode")
            prof.facts = pf
            db.add(Answer(session_id=session_id, topic=topic, question_text=question, answer_text=answer_text,
                          extracted={k: v for k, v in facts.items() if k not in ("answer_text", "question", "ts")}))
            prof.transcript_snippets = list(prof.transcript_snippets or []) + [{"topic": topic, "q": question, "a": answer_text}]
            if topic not in (sess.asked_topics or []):
                sess.asked_topics = list(sess.asked_topics or []) + [topic]
        db.add(prof)
        db.add(sess)
        db.commit()

        # 3) rescore every criterion of every candidate trial (spec §8.4)
        scored = await engine.score_profile(prof, jev, timeout=s.jev_turn_timeout_s)
        results = scored["results"]
        nxt, value, values = adaptive.next_topic(prof, results, sess.asked_topics or [])
        stop = "caller_stop" if wants_stop else adaptive.should_stop(results, sess.asked_topics or [], value, nxt)
        done = stop is not None
        sess.current_topic = None if done else nxt
        sess.funnel = list(sess.funnel or []) + [scored["n_possible"]]
        db.add(sess)
        db.commit()
        extracted = {k: v for k, v in (prof.facts or {}).get(topic, {}).items() if k not in ("answer_text", "question", "ts")}

    next_q = None if done else TOPICS[nxt]["question_en"]
    bus.publish("turn_scored", {
        "turn": {"topic": topic, "label": TOPICS[topic]["label"], "question": question, "answer": answer_text, "extracted": extracted},
        "trials_total": scored["n_total"], "trials_candidates": scored["n_candidates"], "trials_possible": scored["n_possible"],
        "funnel": sess.funnel, "top": _event_top(results, 40), "filtered": scored["filtered"][:40], "asked": sess.asked_topics,
        "topic_values": {k: round(v, 3) for k, v in values.items()}, "done": done, "stop_reason": stop,
        "jev": {"decisions": scored["decisions"] + len(a), "latency_ms": scored["latency_ms"], "missing": scored["missing"], **{k: jev.snapshot()[k] for k in ("active", "mock", "fallback_mode")}},
    }, session_id)
    if next_q:
        bus.publish("next_question", {"topic": nxt, "question": next_q}, session_id)
    return {"done": done, "topic": None if done else nxt, "next_question": next_q, "say": say,
            "trials_possible": scored["n_possible"], "top_trials": _public_top(results)}


def build_evidence(prof: Profile, trial_id: str) -> list[dict]:
    return engine.criteria_table(prof, trial_id)


def create_referral(db, prof: Profile, r: dict, source: str) -> Referral:
    existing = db.exec(select(Referral).where(Referral.profile_id == prof.id, Referral.trial_id == r["trial_id"])).first()
    ref = existing or Referral(profile_id=prof.id, trial_id=r["trial_id"], source=source)
    ref.band, ref.p_pes, ref.p_opt = r["band"], r["p_pes"], r["p_opt"]
    ref.evidence = build_evidence(prof, r["trial_id"])
    db.add(ref)
    db.commit()
    db.refresh(ref)
    bus.publish("referral_created", {"referral_id": ref.id, "trial_id": ref.trial_id, "lay_title": r["lay_title"], "band": ref.band,
                                     "source": source, "p_pes": round(ref.p_pes, 3), "p_opt": round(ref.p_opt, 3), "profile": prof.first_name or f"P-{prof.id[:6]}"}, prof.session_id)
    return ref


async def finish_screening(session_id: str) -> dict:
    jev = get_router()
    with db_session() as db:
        sess, prof = _load(db, session_id)
        if not sess.consent_screening:
            return {"spoken_summary": "No screening took place.", "outcome": "no_consent", "trials": []}
        scored = await engine.score_profile(prof, jev, timeout=get_settings().jev_turn_timeout_s)
        live = [r for r in scored["results"] if not r["knocked_out"]]
        refer = [r for r in live if r["band"] == "refer"]
        review = [r for r in live if r["band"] == "human_review"]
        picks = (refer + review)[:3]
        refs = [create_referral(db, prof, r, "intake") for r in (refer[:2] or review[:1])]
        sess.status = "done"
        db.add(sess)
        db.commit()
        outcome = "refer" if refer else ("human_review" if review else "no_match")
        trials = []
        for r in picks:
            t = STORE.get(r["trial_id"])
            unknown = [c["lay_text"] for c in engine.criteria_table(prof, t.id) if c["status"] == "unknown" and c["screenable"]][:2]
            trials.append({"trial_id": t.id, "lay_title": t.lay_title, "lay_summary": t.lay_summary, "band": r["band"], "site": (r["site"] or {}).get("name"),
                           "city": (r["site"] or {}).get("city"), "distance_km": r["distance_km"], "to_check": unknown, "source": t.source})
    if not picks:
        spoken = ("Thank you. Based on what you've told me, I can't see a study that fits you right now. "
                  "New studies open all the time, and we can let you know if one looks right for you.")
    else:
        n = len(picks)
        parts = [f"Thank you. Based on what you've told me, you may be a good fit for {n} {'study' if n == 1 else 'studies'} near you."]
        for i, t in enumerate(trials[:2]):
            where = f" in {t['city']}" if t["city"] else ""
            parts.append(f"{'The first' if i == 0 else 'Another'} is: {t['lay_title']}{where}.")
        parts.append("A research nurse will confirm whether you are eligible." if refer else
                     "A research nurse may call you to check a few details before confirming.")
    spoken = spoken if not picks else " ".join(parts)
    bus.publish("screening_done", {"outcome": outcome, "trials": trials, "spoken_summary": spoken, "referral_ids": [r.id for r in refs]}, session_id)
    return {"spoken_summary": spoken[:560], "outcome": outcome, "n_trials": len(picks), "trials": [{"lay_title": t["lay_title"], "band": t["band"]} for t in trials]}


async def record_ethnicity(session_id: str, answer_text: str) -> dict:
    a = await get_router().decide({"topic": "ethnicity", "answer": answer_text}, ethnicity_question(), timeout=get_settings().jev_turn_timeout_s)
    choice = (a.get("x__eth") or {}).get("choice", "unknown")
    with db_session() as db:
        _, prof = _load(db, session_id)
        prof.ethnicity_self = None if choice == "unknown" else choice
        db.add(prof)
        db.commit()
    return {"ok": True, "group": prof.ethnicity_self}


# ---------------------------------------------------------------- HTTP (ElevenLabs server tools)

class StartSessionIn(BaseModel):
    caller_language: Optional[str] = "en"
    conversation_id: Optional[str] = None
    caller_id: Optional[str] = None


class ConsentIn(BaseModel):
    session_id: str
    screening: bool = True
    future_contact: Optional[bool] = None
    language: Optional[str] = None
    first_name: Optional[str] = None
    phone: Optional[str] = None


class AnswerIn(BaseModel):
    session_id: str
    answer_text: str
    topic: Optional[str] = None


class SessionIn(BaseModel):
    session_id: str


class EthnicityIn(BaseModel):
    session_id: str
    answer_text: str


@router.post("/start_session", dependencies=[Depends(tool_auth)])
async def http_start_session(b: StartSessionIn):
    out = create_session("phone_in", b.caller_language or "en", b.conversation_id)
    if b.caller_id:  # kept apart from the profile; only used if the caller later consents to future contact
        _pending_caller[out["session_id"]] = b.caller_id
    return {"session_id": out["session_id"]}


_pending_caller: dict[str, str] = {}


@router.post("/record_consent", dependencies=[Depends(tool_auth)])
async def http_record_consent(b: ConsentIn):
    phone = b.phone or (_pending_caller.get(b.session_id) if b.future_contact else None)
    return await record_consent(b.session_id, b.screening, b.future_contact, b.language, b.first_name, phone)


@router.post("/record_answer", dependencies=[Depends(tool_auth)])
async def http_record_answer(b: AnswerIn):
    return await record_answer(b.session_id, b.answer_text, b.topic)


@router.post("/finish_screening", dependencies=[Depends(tool_auth)])
async def http_finish(b: SessionIn):
    return await finish_screening(b.session_id)


@router.post("/record_ethnicity", dependencies=[Depends(tool_auth)])
async def http_ethnicity(b: EthnicityIn):
    return await record_ethnicity(b.session_id, b.answer_text)

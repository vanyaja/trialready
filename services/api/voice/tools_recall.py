"""Recall loop tools: re-check 1-2 facts, offer a slot, create the booking (spec §9.2)."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..config import get_settings
from ..db import STORE, db_session
from ..events import bus
from ..jev.client import get_router
from ..jev.questions import extract_questions, guard_questions, noul
from ..matching import engine
from ..matching import thresholds as T
from ..matching.extract import merge_jev, rule_extract
from ..models import CallLog, Profile, Referral, Slot, now
from ..site.slots import ensure_slots, spoken
from ..trials import hard_filters
from ..trials.topics import CHANGEABLE, RECHECK_QUESTIONS, TOPIC_ORDER, TOPICS
from .tools_intake import SAFETY_MESSAGE, delete_profile_data, tool_auth

router = APIRouter(prefix="/api/tools", tags=["voice-tools"])


def unasked_unknown_topics(prof: Profile, trial_id: str) -> list[str]:
    """Topics behind this trial's still-unknown criteria that the person was never asked about."""
    topics = []
    for row in engine.criteria_table(prof, trial_id):
        if row["pending"] and row["status"] == "unknown" and row["topic"] not in topics:
            topics.append(row["topic"])
    return sorted(topics, key=TOPIC_ORDER.index)


def recheck_plan(prof: Profile, trial_id: str, max_q: int = 2) -> list[dict]:
    """The 1-2 questions for the recall call: first anything never asked that this trial needs, then the
    changeable topics its criteria lean on most."""
    new = unasked_unknown_topics(prof, trial_id)
    plan = [{"topic": t, "question": TOPICS[t]["question_en"], "kind": "new"} for t in new[:max_q]]
    counts = {t: 0 for t in CHANGEABLE}
    for c in STORE.crit(trial_id):
        if c.topic in counts:
            counts[c.topic] += 1
    for t in sorted(counts, key=lambda t: (-counts[t], CHANGEABLE.index(t))):
        if len(plan) >= max_q:
            break
        if counts[t] > 0 and t in (prof.facts or {}) and not (prof.facts[t] or {}).get("derived") and all(p["topic"] != t for p in plan):
            plan.append({"topic": t, "question": RECHECK_QUESTIONS[t], "kind": "recheck"})
    return plan or [{"topic": "diabetes_meds", "question": RECHECK_QUESTIONS["diabetes_meds"], "kind": "recheck"}]


def call_worthy(prof: Profile, r: dict, max_q: int = 2) -> bool:
    """Phone when the trial is a confirmed fit, or when at most `max_q` never-asked questions stand between
    'possible' and 'confirmed' (the recall call asks them)."""
    if r["knocked_out"]:
        return False
    if r["band"] == "refer":
        return True
    if r["band"] != "human_review":
        return False
    return 0 < len(unasked_unknown_topics(prof, r["trial_id"])) <= max_q


def site_for(prof: Profile, trial_id: str) -> dict:
    t = STORE.get(trial_id)
    site, dist = hard_filters.nearest_site(t, hard_filters.outcode_latlon(prof.postcode_district)) if t else (None, None)
    return {"name": (site or {}).get("name") or "the research site", "city": (site or {}).get("city") or "", "distance_km": dist}


def latest_call(db, referral_id: int) -> Optional[CallLog]:
    return db.exec(select(CallLog).where(CallLog.referral_id == referral_id).order_by(CallLog.id.desc())).first()


def set_call_status(db, call: Optional[CallLog], status: str, summary: Optional[str] = None, line: Optional[dict] = None):
    if not call:
        return
    call.status = status
    if summary:
        call.summary = summary
    if line:
        call.transcript = list(call.transcript or []) + [line]
    if status in ("booked", "not_eligible", "declined", "no_answer", "completed", "failed", "safety", "withdrawn"):
        call.ended_at = now()
    db.add(call)
    db.commit()
    bus.publish("call_status", {"call_id": call.id, "referral_id": call.referral_id, "status": status, "summary": call.summary, "mode": call.mode})


def _merge_changed(topic: str, old: dict, new: dict, text: str) -> dict:
    if topic == "diabetes_meds":
        meds = set(old.get("meds") or [])
        stopped = bool(re.search(r"stopp|no longer|came off|come off|not taking|instead of|switched from", text, re.I))
        meds = (meds - set(new.get("meds") or [])) if stopped and not re.search(r"start|now on|switched to", text, re.I) else (meds | set(new.get("meds") or []))
        return {**old, "meds": sorted(meds), "known": True, "none": not meds}
    if new.get("band", "unknown") in (None, "unknown") and new.get("mmol") is None:
        return old
    return {**old, **{k: v for k, v in new.items() if v is not None}}


async def recall_record_answer(referral_id: int, topic: str, answer_text: str) -> dict:
    s, jev = get_settings(), get_router()
    with db_session() as db:
        ref = db.get(Referral, referral_id)
        if not ref:
            raise HTTPException(404, "unknown referral_id")
        prof = db.get(Profile, ref.profile_id)
        call = latest_call(db, referral_id)
        plan = (ref.recheck or {}).get("plan") or recheck_plan(prof, ref.trial_id)
        item = next((p for p in plan if p["topic"] == topic), None) or next((p for p in plan if p["topic"] not in (ref.recheck or {}).get("answers", {})), plan[0])
        topic, question, is_new = item["topic"], item["question"], item.get("kind") == "new"
        a = await jev.decide({"topic": topic, "question": question, "answer": answer_text},
                             {**guard_questions(), **extract_questions(topic), "y__no_change": noul("Does the caller say nothing has changed since the last conversation?")},
                             timeout=s.jev_turn_timeout_s)
        g = lambda k, d: (a.get(k) or {}).get("noul", d)  # noqa: E731
        line = {"topic": topic, "q": question, "a": answer_text}
        if g("g__urgent_symptom", 0) >= T.URGENT:
            set_call_status(db, call, "safety", "Emergency language: caller told to ring 999 / 111.", line)
            return {"still_eligible": False, "next": "end", "safety_message": SAFETY_MESSAGE}
        if g("g__withdraw_consent", 0) >= T.WITHDRAW:
            set_call_status(db, call, "withdrawn", "Caller asked to be removed; data deleted.")
            delete_profile_data(db, prof.id, prof.session_id)
            db.commit()
            return {"still_eligible": False, "next": "end", "say": "Done. I have removed you from our list and deleted your answers. Goodbye."}
        if is_new or g("y__no_change", 0) < 0.6:
            new = merge_jev(topic, rule_extract(topic, answer_text), a)
            pf = dict(prof.facts or {})
            merged = new if is_new else _merge_changed(topic, pf.get(topic) or {}, new, answer_text)
            pf[topic] = {**merged, "answer_text": answer_text, "question": question, "ts": now().isoformat()}
            prof.transcript_snippets = list(prof.transcript_snippets or []) + [{"topic": topic, "q": question, "a": answer_text, "call": "recall"}]
            prof.facts = pf
            db.add(prof)
            db.commit()
        scored = await engine.score_profile(prof, jev, trial_ids=[ref.trial_id], timeout=s.jev_turn_timeout_s)
        r = scored["results"][0] if scored["results"] else None
        eligible = bool(r and not r["knocked_out"] and r["band"] in ("refer", "human_review"))
        rc = dict(ref.recheck or {})
        rc.setdefault("plan", plan)
        rc["answers"] = {**rc.get("answers", {}), topic: answer_text}
        rc["still_eligible"] = eligible
        ref.recheck = rc
        if r:
            ref.p_pes, ref.p_opt, ref.band = r["p_pes"], r["p_opt"], r["band"] if not r["knocked_out"] else ref.band
            ref.evidence = engine.criteria_table(prof, ref.trial_id)
        remaining = [p for p in plan if p["topic"] not in rc["answers"]]
        if not eligible:
            ref.status = "ineligible"
            db.add(ref)
            db.commit()
            set_call_status(db, call, "not_eligible", f"No longer fits after re-check ({topic}).", line)
            bus.publish("referral_updated", {"referral_id": ref.id, "status": ref.status})
            return {"still_eligible": False, "next": "end",
                    "say": "Thank you for telling me. That means this particular study is not the right fit at the moment. We will keep you on the list for future studies. Goodbye."}
        db.add(ref)
        db.commit()
        set_call_status(db, call, "in_progress", None, line)
        bus.publish("referral_updated", {"referral_id": ref.id, "status": ref.status})
        if remaining:
            return {"still_eligible": True, "next": "ask", "next_topic": remaining[0]["topic"], "next_question": remaining[0]["question"]}
        return {"still_eligible": True, "next": "offer_slots"}


def get_slots(referral_id: int) -> dict:
    with db_session() as db:
        ref = db.get(Referral, referral_id)
        if not ref:
            raise HTTPException(404, "unknown referral_id")
        prof = db.get(Profile, ref.profile_id)
        site = site_for(prof, ref.trial_id)
        slots = ensure_slots(db, site["name"])[:2]
        return {"site_name": site["name"], "site_area": site["city"], "slots": [{"slot_id": sl.id, "when": spoken(sl)} for sl in slots]}


def book_slot(referral_id: int, slot_id: int) -> dict:
    with db_session() as db:
        ref, slot = db.get(Referral, referral_id), db.get(Slot, slot_id)
        if not ref or not slot:
            raise HTTPException(404, "unknown referral_id or slot_id")
        if slot.booked >= slot.capacity:
            return {"ok": False, "say": "Sorry, that time has just been taken. Shall I read the other option?"}
        slot.booked += 1
        ref.slot_id, ref.status = slot.id, "booked"
        db.add(slot)
        db.add(ref)
        db.commit()
        when = spoken(slot)
        set_call_status(db, latest_call(db, referral_id), "booked", f"Booked {when} at {slot.site_name}.")
        t = STORE.get(ref.trial_id)
        bus.publish("referral_updated", {"referral_id": ref.id, "status": "booked", "slot": when, "site": slot.site_name, "lay_title": t.lay_title if t else ""})
        return {"ok": True, "confirmation": f"You are booked for {when} at {slot.site_name}. A confirmation text would follow (not sent in this demo). A research nurse will confirm your eligibility at the visit."}


class RecallAnswerIn(BaseModel):
    referral_id: int
    topic: Optional[str] = ""
    answer_text: str


class ReferralIn(BaseModel):
    referral_id: int


class BookIn(BaseModel):
    referral_id: int
    slot_id: int


@router.post("/recall_record_answer", dependencies=[Depends(tool_auth)])
async def http_recall_answer(b: RecallAnswerIn):
    return await recall_record_answer(b.referral_id, b.topic or "", b.answer_text)


@router.post("/get_slots", dependencies=[Depends(tool_auth)])
async def http_get_slots(b: ReferralIn):
    return get_slots(b.referral_id)


@router.post("/book_slot", dependencies=[Depends(tool_auth)])
async def http_book_slot(b: BookIn):
    return book_slot(b.referral_id, b.slot_id)

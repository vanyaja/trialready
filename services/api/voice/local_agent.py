"""Built-in scripted agent: runs the same intake / recall scripts as the ElevenLabs agents, without ElevenLabs.

Used by (1) the browser fallback call on /call (typed, or browser speech), (2) "answer in browser" for simulated
outbound calls, (3) persona replay tests. It calls exactly the same tool functions the ElevenLabs agents call.
"""
import re
from typing import Optional

from sqlmodel import select

from ..config import get_settings
from ..db import STORE, db_session
from ..jev.client import get_router
from ..jev.questions import guard_questions, noul
from ..matching import thresholds as T
from ..models import CallLog, Profile, Referral, ScreenSession
from . import outbound
from .tools_intake import FUTURE_CONTACT_Q, SAFETY_MESSAGE, delete_profile_data, finish_screening, record_answer, record_consent, record_ethnicity
from .tools_recall import book_slot, get_slots, recall_record_answer, set_call_status

ETHNICITY_Q = ("Thank you, you're on the list. One last optional question, to help us check we are reaching everyone fairly: "
               "how would you describe your ethnic group? You can say skip.")
GOODBYE = "Thank you. That's everything. Take care, goodbye."


async def _agrees(text: str) -> tuple:
    a = await get_router().decide({"answer": text}, {"y__agrees": noul("Does the caller agree / say yes?"), **guard_questions()},
                                  timeout=get_settings().jev_turn_timeout_s)
    return (a.get("y__agrees") or {}).get("noul", 0.5), a


def _set_stage(session_id: str, stage: str):
    with db_session() as db:
        s = db.get(ScreenSession, session_id)
        s.stage = stage
        db.add(s)
        db.commit()


async def intake_turn(session_id: str, text: str) -> dict:
    with db_session() as db:
        sess = db.get(ScreenSession, session_id)
        if not sess:
            return {"say": "Session not found.", "done": True, "stage": "end"}
        stage = sess.stage
    if stage == "consent":
        p, _ = await _agrees(text)
        if p >= T.AGREES:
            r = await record_consent(session_id, True)
            _set_stage(session_id, "questions")
            return {"say": "Thank you. " + r["next_question"], "done": False, "stage": "questions", "topic": r["topic"]}
        if p <= 0.3:
            r = await record_consent(session_id, False)
            _set_stage(session_id, "end")
            return {"say": r["say"], "done": True, "stage": "end"}
        return {"say": "Sorry, I didn't catch that. Is it OK if I ask you a few questions and store your answers for this check? Yes or no.", "done": False, "stage": "consent"}
    if stage == "questions":
        r = await record_answer(session_id, text)
        if r.get("safety_message"):
            _set_stage(session_id, "end")
            return {"say": r["safety_message"], "done": True, "stage": "end", "safety": True}
        if r.get("withdrawn") or r.get("error"):
            _set_stage(session_id, "end")
            return {"say": r["say"], "done": True, "stage": "end"}
        prefix = (r["say"] + " ") if r.get("say") else ""
        if not r["done"]:
            return {"say": prefix + r["next_question"], "done": False, "stage": "questions", "topic": r["topic"]}
        fin = await finish_screening(session_id)
        _set_stage(session_id, "future_contact")
        return {"say": prefix + fin["spoken_summary"] + " " + FUTURE_CONTACT_Q, "done": False, "stage": "future_contact", "outcome": fin["outcome"]}
    if stage == "future_contact":
        p, a = await _agrees(text)
        if (a.get("g__withdraw_consent") or {}).get("noul", 0) >= T.WITHDRAW:
            with db_session() as db:
                sess = db.get(ScreenSession, session_id)
                prof = db.exec(select(Profile).where(Profile.session_id == session_id)).first()
                if prof:
                    delete_profile_data(db, prof.id, session_id)
                sess.status, sess.stage, sess.consent_contact = "withdrawn", "end", False
                db.add(sess)
                db.commit()
            return {"say": "Done. I have deleted your answers and removed you from our list. Goodbye.", "done": True, "stage": "end"}
        if p >= T.AGREES:
            await record_consent(session_id, True, future_contact=True)
            _set_stage(session_id, "ethnicity")
            return {"say": ETHNICITY_Q, "done": False, "stage": "ethnicity"}
        await record_consent(session_id, True, future_contact=False)
        _set_stage(session_id, "end")
        return {"say": "No problem, we won't contact you again. " + GOODBYE, "done": True, "stage": "end"}
    if stage == "ethnicity":
        await record_ethnicity(session_id, text)
        _set_stage(session_id, "end")
        return {"say": GOODBYE, "done": True, "stage": "end"}
    return {"say": "This call has ended.", "done": True, "stage": "end"}


# ---------------------------------------------------------------- simulated outbound call answered in the browser

_recall_state: dict[int, dict] = {}


def recall_answer(call_id: int) -> Optional[dict]:
    """Pick up a ringing simulated call. Returns the agent's opening line, or None if it is no longer ringing."""
    if not outbound.claim(call_id):
        return None
    with db_session() as db:
        call = db.get(CallLog, call_id)
        ref = db.get(Referral, call.referral_id)
        prof = db.get(Profile, ref.profile_id)
        t = STORE.get(ref.trial_id)
        plan = (ref.recheck or {}).get("plan") or []
        set_call_status(db, call, "in_progress", "Answered in the browser.")
        name = prof.first_name or "there"
    _recall_state[call_id] = {"stage": "good_time", "referral_id": ref.id, "plan": plan, "i": 0, "slots": []}
    return {"say": f"Hello {name}, this is the TrialReady assistant, an AI, calling about research studies you agreed to hear about. "
                   f"A new study has opened that may suit you: {t.lay_title if t else 'a new study'}. Is now a good time for two quick questions?",
            "done": False}


async def recall_turn(call_id: int, text: str) -> dict:
    st = _recall_state.get(call_id)
    if not st:
        return {"say": "This call has ended.", "done": True}
    rid = st["referral_id"]
    if st["stage"] == "good_time":
        p, a = await _agrees(text)
        if (a.get("g__urgent_symptom") or {}).get("noul", 0) >= T.URGENT:
            with db_session() as db:
                set_call_status(db, db.get(CallLog, call_id), "safety", "Emergency language: told to ring 999 / 111.")
            _recall_state.pop(call_id, None)
            return {"say": SAFETY_MESSAGE, "done": True}
        if p < T.AGREES:
            with db_session() as db:
                set_call_status(db, db.get(CallLog, call_id), "declined", "Not a good time; will retry later.")
            _recall_state.pop(call_id, None)
            return {"say": "No problem. I'll try again another day. Goodbye.", "done": True}
        st["stage"] = "recheck"
        return {"say": st["plan"][0]["question"], "done": False}
    if st["stage"] == "recheck":
        topic = st["plan"][st["i"]]["topic"]
        r = await recall_record_answer(rid, topic, text)
        if r.get("safety_message") or r["next"] == "end":
            _recall_state.pop(call_id, None)
            return {"say": r.get("safety_message") or r.get("say") or "Thank you, goodbye.", "done": True}
        if r["next"] == "ask":
            st["i"] += 1
            return {"say": r["next_question"], "done": False}
        sl = get_slots(rid)
        st.update(stage="slots", slots=sl["slots"])
        opts = " or ".join(s["when"] for s in sl["slots"])
        return {"say": f"Thank you, it still looks like a possible fit. The research team at {sl['site_name']} can see you on {opts}. Which would you prefer?", "done": False}
    if st["stage"] == "slots":
        low = text.lower()
        slots = st["slots"]
        pick = None
        if re.search(r"\b(second|latter|later|two|2nd|afternoon)\b", low) and len(slots) > 1:
            pick = slots[1]
        elif re.search(r"\b(first|former|earlier|one|1st|morning|yes|either|fine|ok|okay)\b", low):
            pick = slots[0]
        else:
            pick = next((s for s in slots if any(tok in low for tok in s["when"].lower().split()[:1])), None)
        if not pick:
            if re.search(r"\b(no|neither|can't|cannot)\b", low):
                with db_session() as db:
                    set_call_status(db, db.get(CallLog, call_id), "declined", "Neither slot suited; nurse to follow up.")
                _recall_state.pop(call_id, None)
                return {"say": "No problem. A research nurse will get in touch to find a time that suits you. Goodbye.", "done": True}
            return {"say": f"Sorry, was that the first option, {slots[0]['when']}, or the second?", "done": False}
        b = book_slot(rid, pick["slot_id"])
        _recall_state.pop(call_id, None)
        return {"say": (b.get("confirmation") or b.get("say") or "Booked.") + " Thank you, goodbye.", "done": True}
    return {"say": "This call has ended.", "done": True}

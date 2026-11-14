"""Outbound recall calls. Real: ElevenLabs Agents + Twilio (`POST /v1/convai/twilio/outbound-call`).
Fallback (no keys / no phone / API error): a simulated call that can be answered in the browser (spec §17.3)."""
import asyncio
import logging
from typing import Optional

import httpx

from ..config import get_settings
from ..db import STORE, db_session
from ..events import bus
from ..models import CallLog, ContactDetail, Profile, Referral
from .tools_recall import book_slot, get_slots, recall_record_answer, recheck_plan, set_call_status, site_for

log = logging.getLogger("outbound")
_claimed: dict[int, bool] = {}      # sim calls answered in the browser
_sems: dict = {}


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _sems:
        _sems.clear()
        _sems[loop] = asyncio.Semaphore(get_settings().outbound_concurrency)
    return _sems[loop]


def claim(call_id: int) -> bool:
    if _claimed.get(call_id) is False:
        _claimed[call_id] = True
        return True
    return False


def dynamic_variables(ref: Referral, prof: Profile) -> dict:
    t = STORE.get(ref.trial_id)
    plan = (ref.recheck or {}).get("plan") or recheck_plan(prof, ref.trial_id)
    site = site_for(prof, ref.trial_id)
    dv = {"first_name": prof.first_name or "there", "referral_id": ref.id, "trial_lay_title": t.lay_title if t else "a research study",
          "site_name": site["name"], "site_area": site["city"],
          "recheck_topic_1": plan[0]["topic"], "recheck_question_1": plan[0]["question"],
          "recheck_topic_2": plan[1]["topic"] if len(plan) > 1 else "", "recheck_question_2": plan[1]["question"] if len(plan) > 1 else ""}
    return dv


async def place_call(referral_id: int, force_sim: bool = False) -> dict:
    """Create the CallLog and start the call (real or simulated). Returns immediately."""
    s = get_settings()
    with db_session() as db:
        ref = db.get(Referral, referral_id)
        prof = db.get(Profile, ref.profile_id)
        contact = db.get(ContactDetail, prof.id)
        plan = recheck_plan(prof, ref.trial_id)
        ref.recheck = {"plan": plan, "answers": {}}
        db.add(ref)
        allowed = bool(contact and contact.phone_e164 and contact.phone_e164.replace(" ", "") in s.outbound_allowed_numbers)
        if s.elevenlabs_outbound_ready and contact and not force_sim and not allowed:
            log.info("referral %s: number not in OUTBOUND_ALLOWLIST - simulated call instead of a real one", referral_id)
        real = bool(s.elevenlabs_outbound_ready and allowed and not force_sim)
        call = CallLog(direction="outbound", mode="elevenlabs" if real else "sim", profile_id=prof.id, trial_id=ref.trial_id, referral_id=ref.id, status="dialing")
        db.add(call)
        db.commit()
        db.refresh(call)
        dv = dynamic_variables(ref, prof)
        to_number = contact.phone_e164 if contact else None
        view = {"call_id": call.id, "referral_id": ref.id, "status": "dialing", "mode": call.mode, "person": prof.first_name or f"P-{prof.id[:6]}",
                "lay_title": dv["trial_lay_title"], "site": dv["site_name"]}
    bus.publish("call_status", view)
    if real:
        asyncio.ensure_future(_elevenlabs_call(call.id, referral_id, to_number, dv))
    else:
        _claimed[call.id] = False
        asyncio.ensure_future(_simulate(call.id, referral_id, plan))
    return view


async def _elevenlabs_call(call_id: int, referral_id: int, to_number: str, dv: dict):
    s = get_settings()
    async with _semaphore():
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{s.elevenlabs_base_url}/v1/convai/twilio/outbound-call", headers={"xi-api-key": s.elevenlabs_api_key},
                                 json={"agent_id": s.elevenlabs_recall_agent_id, "agent_phone_number_id": s.elevenlabs_phone_number_id, "to_number": to_number,
                                       "conversation_initiation_client_data": {"dynamic_variables": dv}})
            r.raise_for_status()
            data = r.json()
            if data.get("success") is False:
                raise RuntimeError(data.get("message") or "outbound call rejected")
            with db_session() as db:
                call = db.get(CallLog, call_id)
                call.eleven_conversation_id = data.get("conversation_id")
                set_call_status(db, call, "ringing", "Phone ringing (ElevenLabs + Twilio).")
        except Exception as e:  # noqa: BLE001
            log.warning("ElevenLabs outbound failed for referral %s: %r - falling back to simulated call", referral_id, e)
            with db_session() as db:
                call = db.get(CallLog, call_id)
                call.mode = "sim"
                set_call_status(db, call, "dialing", f"Phone call failed ({str(e)[:80]}); simulated call instead.")
            _claimed[call_id] = False
            await _simulate(call_id, referral_id, recheck_plan_for(referral_id))


def _persona_script(referral_id: int) -> dict:
    """Scripted answers for synthetic personas, so auto-played simulated calls answer never-asked questions in character."""
    import json

    from ..config import DATA
    with db_session() as db:
        ref = db.get(Referral, referral_id)
        prof = db.get(Profile, ref.profile_id) if ref else None
    f = DATA / "personas" / f"{prof.persona_id}.json" if prof and prof.persona_id else None
    return json.loads(f.read_text(encoding="utf-8")).get("script", {}) if f and f.exists() else {}


def recheck_plan_for(referral_id: int) -> list[dict]:
    with db_session() as db:
        ref = db.get(Referral, referral_id)
        return (ref.recheck or {}).get("plan") or recheck_plan(db.get(Profile, ref.profile_id), ref.trial_id)


async def _simulate(call_id: int, referral_id: int, plan: list[dict]):
    """Ring; if nobody answers in the browser within SIM_RING_SECONDS, play the call out automatically."""
    s = get_settings()
    async with _semaphore():
        await asyncio.sleep(0.8)
        with db_session() as db:
            set_call_status(db, db.get(CallLog, call_id), "ringing", "Simulated call ringing - answer it in the browser.")
        waited = 0.0
        while waited < s.sim_ring_seconds:
            await asyncio.sleep(0.25)
            waited += 0.25
            if _claimed.get(call_id):
                return  # the browser took over (voice/local_agent.py)
        _claimed[call_id] = True
        try:
            with db_session() as db:
                set_call_status(db, db.get(CallLog, call_id), "in_progress", "Auto-answered (simulated persona).")
            res = {"still_eligible": True, "next": "offer_slots"}
            script = _persona_script(referral_id)
            for p in plan:
                await asyncio.sleep(0.9)
                ans = script.get(p["topic"], "No.") if p.get("kind") == "new" else "No, nothing has changed."
                res = await recall_record_answer(referral_id, p["topic"], ans)
                if not res.get("still_eligible"):
                    return
            await asyncio.sleep(0.9)
            slots = get_slots(referral_id)["slots"]
            if slots:
                book_slot(referral_id, slots[0]["slot_id"])
        except Exception as e:  # noqa: BLE001
            log.exception("simulated call failed")
            with db_session() as db:
                set_call_status(db, db.get(CallLog, call_id), "failed", str(e)[:120])

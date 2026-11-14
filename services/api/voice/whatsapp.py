"""WhatsApp channel through Twilio (the Twilio WhatsApp sandbox, or your own approved WhatsApp sender). Text only.

Inbound:  Twilio POSTs every incoming WhatsApp message (form-encoded) to /api/whatsapp/inbound. We check
          X-Twilio-Signature with TWILIO_AUTH_TOKEN, hand the text to the built-in scripted agent (voice/local_agent.py:
          the same intake / recall scripts and the same backend tools the ElevenLabs voice agents use, so every turn is
          scored by Jev exactly like a call) and answer with TwiML.
Outbound: a registry member who joined over WhatsApp is contacted about a new trial by a WhatsApp message instead of a
          phone call - only if their number is in OUTBOUND_ALLOWLIST. Their replies continue the recall script.
Twilio only lets a business start a WhatsApp conversation with free text inside 24 hours of the person's last message;
outside that window set TWILIO_WHATSAPP_CONTENT_SID to an approved template ({{1}} = first name, {{2}} = study title).
"""
import base64
import hashlib
import hmac
import json
import logging
from typing import Optional
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select

from ..config import get_settings
from ..db import db_session
from ..models import CallLog, Profile, ScreenSession, WhatsAppContact, WhatsAppThread, now
from .tools_intake import create_session
from .tools_recall import set_call_status

log = logging.getLogger("whatsapp")
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

INTRO = ("Hi, I'm the TrialReady assistant. I can check, in about five minutes, whether there are diabetes research studies near you "
         "that you might be able to join. I'm an AI, this is not medical advice, and a research nurse always makes the final decision. "
         "Is it OK if I ask you a few questions and store your answers for this check? Please reply YES or NO.")
RESTART = {"restart", "start over", "start again", "reset"}
MAX_LEN = 1500  # Twilio WhatsApp body limit is 1600


def e164(addr: str) -> str:
    return (addr or "").replace("whatsapp:", "").strip().replace(" ", "")


def twilio_signature(url: str, params: dict, token: str) -> str:
    """Twilio request signature: base64(HMAC-SHA1(auth_token, url + concatenated sorted key/value pairs))."""
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode("utf-8"), hashlib.sha1).digest()).decode()


def _signed(request: Request, params: dict) -> bool:
    s = get_settings()
    sig = request.headers.get("x-twilio-signature")
    if not (s.twilio_auth_token and sig):
        return False
    # Twilio signs the URL it called: the public tunnel URL, not the local one uvicorn sees.
    urls = {str(request.url)}
    if s.public_api_base_url:
        urls.add(s.public_api_base_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else ""))
    return any(hmac.compare_digest(twilio_signature(u, params, s.twilio_auth_token), sig) for u in urls)


def twiml(text: str) -> Response:
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>' + (f"<Message>{escape(text[:MAX_LEN])}</Message>" if text else "") + "</Response>"
    return Response(body, media_type="application/xml")


def _save_thread(phone: str, kind: str, session_id: Optional[str] = None, call_id: Optional[int] = None):
    with db_session() as db:
        th = db.get(WhatsAppThread, phone) or WhatsAppThread(phone_e164=phone)
        th.kind, th.session_id, th.call_id, th.updated_at = kind, session_id, call_id, now()
        db.add(th)
        db.commit()


def _end_thread(phone: str):
    with db_session() as db:
        th = db.get(WhatsAppThread, phone)
        if th:
            db.delete(th)
            db.commit()


def _sync_contact(phone: str, session_id: str, profile_name: str = ""):
    """After an intake turn: remember the WhatsApp number only while the person has agreed to future contact."""
    with db_session() as db:
        sess = db.get(ScreenSession, session_id)
        prof = db.exec(select(Profile).where(Profile.session_id == session_id)).first()
        if not (sess and prof):
            return
        wc = db.get(WhatsAppContact, prof.id)
        if sess.consent_contact and not wc:
            db.add(WhatsAppContact(profile_id=prof.id, phone_e164=phone))
            if profile_name and not prof.first_name:
                prof.first_name = profile_name.split()[0][:40]
                db.add(prof)
        elif not sess.consent_contact and wc:
            db.delete(wc)
        db.commit()


def _log_line(call_id: int, role: str, text: str):
    with db_session() as db:
        call = db.get(CallLog, call_id)
        if call:
            call.transcript = list(call.transcript or []) + [{"role": role, "message": text}]
            db.add(call)
            db.commit()


async def handle_message(phone: str, text: str, profile_name: str = "") -> str:
    """One incoming WhatsApp message -> the reply text."""
    from . import local_agent

    with db_session() as db:
        th = db.get(WhatsAppThread, phone)
        if th and text.strip().lower() in RESTART:
            db.delete(th)
            db.commit()
            th = None
        kind, sid, call_id = (th.kind, th.session_id, th.call_id) if th else (None, None, None)

    if kind == "recall" and call_id in local_agent._recall_state:
        _log_line(call_id, "user", text)
        with db_session() as db:
            call = db.get(CallLog, call_id)
            if call and call.status == "ringing":
                set_call_status(db, call, "in_progress", "Replying on WhatsApp.")
        r = await local_agent.recall_turn(call_id, text)
        _log_line(call_id, "agent", r["say"])
        if r["done"]:
            _end_thread(phone)
        return r["say"]

    if kind == "intake" and sid:
        r = await local_agent.intake_turn(sid, text)
        _sync_contact(phone, sid, profile_name)
        if r["done"]:
            _end_thread(phone)
        return r["say"]  # safety messages are returned exactly as written

    # no open conversation: start a new intake. Nothing is stored about the person until they reply YES.
    sid = create_session("whatsapp", "en")["session_id"]
    _save_thread(phone, "intake", session_id=sid)
    return INTRO


@router.post("/inbound")
async def inbound(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    params = dict(parse_qsl(raw, keep_blank_values=True))
    if not _signed(request, params):
        raise HTTPException(403, "invalid or missing X-Twilio-Signature (is TWILIO_AUTH_TOKEN set?)")
    phone = e164(params.get("From", ""))
    if not phone:
        return twiml("")
    try:
        reply = await handle_message(phone, (params.get("Body") or "").strip(), params.get("ProfileName", ""))
    except Exception:  # noqa: BLE001 - never leave a WhatsApp user without an answer
        log.exception("whatsapp turn failed")
        reply = "Sorry, something went wrong on our side. Please try again in a moment, or reply RESTART."
    return twiml(reply)


# ---------------------------------------------------------------- outbound

def contact_for(db, profile_id: str) -> Optional[str]:
    wc = db.get(WhatsAppContact, profile_id)
    return wc.phone_e164 if wc else None


async def send(to_phone: str, body: str, content_vars: Optional[dict] = None) -> str:
    """Send one WhatsApp message through the Twilio Messages API. Returns the message SID."""
    s = get_settings()
    frm = s.twilio_whatsapp_from if s.twilio_whatsapp_from.startswith("whatsapp:") else f"whatsapp:{s.twilio_whatsapp_from}"
    data = {"From": frm, "To": f"whatsapp:{to_phone}"}
    if s.twilio_whatsapp_content_sid and content_vars is not None:
        data.update(ContentSid=s.twilio_whatsapp_content_sid, ContentVariables=json.dumps(content_vars))
    else:
        data["Body"] = body[:MAX_LEN]
    auth = ((s.twilio_api_key_sid, s.twilio_api_key_secret) if s.twilio_api_key_sid and s.twilio_api_key_secret
            else (s.twilio_account_sid, s.twilio_auth_token))
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json", data=data, auth=auth)
    if r.status_code >= 400:
        raise RuntimeError(f"Twilio HTTP {r.status_code}: {r.text[:200]}")
    return r.json().get("sid", "")


async def start_recall(call_id: int, referral_id: int, to_phone: str, dv: dict):
    """Open a recall conversation on WhatsApp: send the opening line, then route replies to the recall script."""
    from . import local_agent, outbound

    outbound._claimed[call_id] = False
    opening = local_agent.recall_answer(call_id)
    if opening is None:
        return
    text = opening["say"] + " Please reply YES or NO."
    try:
        await send(to_phone, text, {"1": str(dv.get("first_name") or "there"), "2": str(dv.get("trial_lay_title") or "a research study")})
        _save_thread(to_phone, "recall", call_id=call_id)
        with db_session() as db:
            set_call_status(db, db.get(CallLog, call_id), "ringing", "WhatsApp message sent - waiting for a reply.", {"role": "agent", "message": text})
    except Exception as e:  # noqa: BLE001
        log.warning("WhatsApp recall failed for referral %s: %r - falling back to a simulated call", referral_id, e)
        local_agent._recall_state.pop(call_id, None)
        with db_session() as db:
            call = db.get(CallLog, call_id)
            call.mode = "sim"
            set_call_status(db, call, "dialing", f"WhatsApp failed ({str(e)[:80]}); simulated call instead.")
        outbound._claimed[call_id] = False
        await outbound._simulate(call_id, referral_id, outbound.recheck_plan_for(referral_id))

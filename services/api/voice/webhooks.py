"""ElevenLabs post-call webhook (spec §9.3): store transcript + summary in CallLog.

Signature: header `ElevenLabs-Signature: t=<unix>,v0=<hex>` where v0 = HMAC-SHA256(secret, f"{t}.{raw_body}").
"""
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from ..config import get_settings
from ..db import db_session
from ..events import bus
from ..models import CallLog, ScreenSession, now

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def verify_signature(raw: bytes, header: Optional[str], secret: str, tolerance_s: int = 1800) -> bool:
    if not header:
        return False
    try:
        parts = dict(p.strip().split("=", 1) for p in header.split(","))
        ts, sig = parts["t"], parts["v0"]
        if abs(time.time() - int(ts)) > tolerance_s:
            return False
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


@router.post("/elevenlabs/post-call")
async def post_call(request: Request):
    raw = await request.body()
    secret = get_settings().elevenlabs_webhook_secret
    if secret and not verify_signature(raw, request.headers.get("elevenlabs-signature"), secret):
        raise HTTPException(401, "invalid signature")
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON")
    if ev.get("type") != "post_call_transcription":
        return {"status": "ignored", "type": ev.get("type")}
    data = ev.get("data") or {}
    conv_id = data.get("conversation_id")
    transcript = [{"role": t.get("role"), "message": t.get("message")} for t in (data.get("transcript") or []) if t.get("message")]
    summary = ((data.get("analysis") or {}).get("transcript_summary") or "")[:1000]
    dv = ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {})
    with db_session() as db:
        call = db.exec(select(CallLog).where(CallLog.eleven_conversation_id == conv_id)).first() if conv_id else None
        if not call and dv.get("referral_id"):
            call = db.exec(select(CallLog).where(CallLog.referral_id == int(dv["referral_id"])).order_by(CallLog.id.desc())).first()
        if not call:
            # inbound / web intake conversation: keep the transcript in a CallLog row of its own
            sess = db.get(ScreenSession, str(dv.get("session_id"))) if dv.get("session_id") else None
            call = CallLog(direction="inbound", mode="elevenlabs", eleven_conversation_id=conv_id, status="completed")
            if sess:
                sess.eleven_conversation_id = conv_id
                db.add(sess)
        call.transcript = transcript
        call.summary = summary or call.summary
        call.eleven_conversation_id = conv_id
        if call.status in ("dialing", "ringing", "in_progress", "queued"):
            call.status = "completed" if transcript else "no_answer"
        call.ended_at = call.ended_at or now()
        db.add(call)
        db.commit()
        bus.publish("call_status", {"call_id": call.id, "referral_id": call.referral_id, "status": call.status, "summary": call.summary, "mode": call.mode})
    return {"status": "received"}

"""WhatsApp channel through Twilio: signature check, full intake by text, recall by WhatsApp message -> booking."""
import asyncio
import re
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from services.api.db import db_session
from services.api.models import CallLog, Profile, Referral, ScreenSession, WhatsAppContact, WhatsAppThread
from services.api.seed import load_personas
from services.api.voice import whatsapp

BASE = "https://trialready-test.example"
TOKEN = "test-auth-token"
PHONE = "+447700900123"  # Ofcom drama range: never a real person


@pytest.fixture()
def wa(seeded, monkeypatch):
    from services.api.config import get_settings
    s = get_settings()
    for k, v in {"twilio_auth_token": TOKEN, "twilio_account_sid": "AC_test", "twilio_whatsapp_from": "whatsapp:+14155238886",
                 "public_api_base_url": BASE, "outbound_allowlist": PHONE}.items():
        monkeypatch.setattr(s, k, v)
    return s


def _client():
    from services.api.main import app
    return TestClient(app)


def _post(c, body: str, phone: str = PHONE, sign: bool = True, name: str = "Agnieszka"):
    params = {"From": f"whatsapp:{phone}", "To": "whatsapp:+14155238886", "Body": body, "ProfileName": name}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Twilio-Signature"] = whatsapp.twilio_signature(f"{BASE}/api/whatsapp/inbound", params, TOKEN)
    r = c.post("/api/whatsapp/inbound", content=urlencode(params), headers=headers)
    return r, (re.search(r"<Message>(.*)</Message>", r.text, re.S).group(1) if r.status_code == 200 and "<Message>" in r.text else "")


def test_whatsapp_rejects_unsigned_and_forged(wa):
    with _client() as c:
        assert _post(c, "hi", sign=False)[0].status_code == 403
        params = {"From": f"whatsapp:{PHONE}", "Body": "hi"}
        bad = whatsapp.twilio_signature(f"{BASE}/api/whatsapp/inbound", params, "wrong-token")
        assert c.post("/api/whatsapp/inbound", content=urlencode(params), headers={"X-Twilio-Signature": bad,
                      "Content-Type": "application/x-www-form-urlencoded"}).status_code == 403


def test_whatsapp_full_intake_joins_registry(wa):
    persona = next(p for p in load_personas() if p["id"] == "p03_agnieszka")
    with _client() as c:
        r, say = _post(c, "restart")  # any first message (or RESTART) opens a fresh conversation with the intro
        assert r.status_code == 200 and "Please reply YES or NO" in say
        with db_session() as db:
            sid = db.get(WhatsAppThread, PHONE).session_id
            assert db.get(ScreenSession, sid).channel == "whatsapp"
        r, say = _post(c, "Yes, that's fine")
        for _ in range(20):
            with db_session() as db:
                sess = db.get(ScreenSession, sid)
                stage, topic = sess.stage, sess.current_topic
            if stage == "end":
                break
            ans = {"questions": persona["script"].get(topic, "I don't know."), "future_contact": "Yes please",
                   "ethnicity": "White Polish"}.get(stage, "yes")
            r, say = _post(c, ans)
            assert r.status_code == 200 and say
        assert stage == "end"
    with db_session() as db:
        prof = db.exec(select(Profile).where(Profile.session_id == sid)).first()
        assert prof.registry_member and db.get(WhatsAppContact, prof.id).phone_e164 == PHONE
        assert db.get(WhatsAppThread, PHONE) is None  # conversation closed


def test_whatsapp_recall_message_and_booking(wa, monkeypatch):
    sent = []

    async def fake_send(to_phone, body, content_vars=None):
        sent.append((to_phone, body))
        return "SM_test"
    monkeypatch.setattr(whatsapp, "send", fake_send)
    from services.api.registry.rematch import insert_demo_trial
    from services.api.voice import outbound

    with db_session() as db:  # the WhatsApp registry member from the intake test, or attach the number to a persona
        wc = db.exec(select(WhatsAppContact).where(WhatsAppContact.phone_e164 == PHONE)).first()
        if not wc:
            prof = db.exec(select(Profile).where(Profile.persona_id == "p01_imran")).first()
            db.add(WhatsAppContact(profile_id=prof.id, phone_e164=PHONE))
            db.commit()
            pid = prof.id
        else:
            pid = wc.profile_id
    tid = insert_demo_trial()
    with db_session() as db:
        ref = db.exec(select(Referral).where(Referral.profile_id == pid, Referral.trial_id == tid)).first()
        if not ref:
            ref = Referral(profile_id=pid, trial_id=tid, source="recall", status="new", evidence={})
            db.add(ref)
            db.commit()
            db.refresh(ref)
        rid = ref.id

    async def place():
        v = await outbound.place_call(rid)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sent:
                break
        return v
    view = asyncio.run(place())
    assert view["mode"] == "whatsapp" and sent and sent[0][0] == PHONE and "Please reply YES or NO" in sent[0][1]
    with _client() as c:
        r, say = _post(c, "Yes, now is fine")
        assert r.status_code == 200 and say
        for _ in range(8):
            with db_session() as db:
                call = db.exec(select(CallLog).where(CallLog.referral_id == rid).order_by(CallLog.id.desc())).first()
            if call.status in ("booked", "not_eligible", "declined", "safety"):
                break
            ans = "the first one" if "prefer" in say else "No, nothing has changed."
            r, say = _post(c, ans)
    assert call.mode == "whatsapp" and call.status in ("booked", "not_eligible"), (call.status, call.summary)
    assert any(t["role"] == "user" for t in call.transcript)
    with db_session() as db:
        assert db.get(WhatsAppThread, PHONE) is None


def test_whatsapp_number_not_allowlisted_gets_simulated_call(wa, monkeypatch):
    monkeypatch.setattr(wa, "outbound_allowlist", "")
    from services.api.voice import outbound
    with db_session() as db:
        wc = db.exec(select(WhatsAppContact)).first()
        ref = db.exec(select(Referral).where(Referral.profile_id == wc.profile_id)).first()
    v = asyncio.run(outbound.place_call(ref.id, force_sim=False))
    assert v["mode"] == "sim"

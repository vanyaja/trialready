"""Spec §23.2, .4, .5, .6: persona replay, safety/withdraw, new-trial -> outbound -> booking, benchmark output."""
import asyncio
import json

from fastapi.testclient import TestClient
from sqlmodel import select

from services.api.db import db_session
from services.api.models import CallLog, CriterionScore, Profile, Referral, ScreenSession, TrialScore
from services.api.seed import load_personas, replay_persona


def _persona(pid):
    return next(p for p in load_personas() if p["id"] == pid)


def test_persona_replay_three_personas(seeded, run):
    assert seeded["trials"] >= 40 and seeded["registry_members"] == 10
    for pid in ("p01_imran", "p04_derek", "p05_priya"):
        tr = run(replay_persona(_persona(pid)))
        assert tr["status"] == "done" and tr["n_questions"] <= 8, tr
        assert tr["topics"][:2] == ["age_sex", "location_travel"]


def test_mi_persona_knocked_out_of_cardio_exclusion_trials(seeded):
    with db_session() as db:
        p = db.exec(select(Profile).where(Profile.persona_id == "p04_derek")).first()
        ko = db.exec(select(TrialScore).where(TrialScore.profile_id == p.id, TrialScore.knocked_out == True)).all()  # noqa: E712
        cardio_ko = [t for t in ko if t.knock_criterion_id and "exc" in t.knock_criterion_id and
                     db.get(CriterionScore, (p.id, t.knock_criterion_id)).p_met >= 0.8]
        from services.api.db import STORE
        assert any(STORE.by_id[t.knock_criterion_id].topic == "cardio_recent" for t in cardio_ko), "heart attack 4 months ago must knock out a cardio-exclusion trial"


def _client():
    from services.api.main import app
    return TestClient(app)


def test_emergency_phrase_and_remove_me(seeded):
    with _client() as c:
        sid = c.post("/api/sessions", json={}).json()["session_id"]
        assert c.post("/api/tools/record_answer", json={"session_id": sid, "answer_text": "58 male"}).json()["error"] == "no_consent"
        r = c.post("/api/tools/record_consent", json={"session_id": sid, "screening": True}).json()
        assert r["topic"] == "age_sex" and r["next_question"]
        r = c.post("/api/tools/record_answer", json={"session_id": sid, "topic": "age_sex", "answer_text": "Actually I've got chest pain right now and I can't breathe"}).json()
        assert r["done"] and "999" in r["safety_message"]

        sid = c.post("/api/sessions", json={}).json()["session_id"]
        c.post("/api/tools/record_consent", json={"session_id": sid, "screening": True, "future_contact": True})
        r = c.post("/api/tools/record_answer", json={"session_id": sid, "topic": "age_sex", "answer_text": "I'm 60, male"}).json()
        assert not r["done"] and r["topic"] == "location_travel" and len(json.dumps(r)) < 700
        r = c.post("/api/tools/record_answer", json={"session_id": sid, "answer_text": "pardon?"}).json()
        assert r.get("reask") and r["topic"] == "location_travel"
        r = c.post("/api/tools/record_answer", json={"session_id": sid, "answer_text": "Actually, remove me please"}).json()
        assert r["done"] and r["withdrawn"]
        with db_session() as db:
            assert db.exec(select(Profile).where(Profile.session_id == sid)).first() is None
            assert db.get(ScreenSession, sid).status == "withdrawn"
        assert c.post("/api/tools/record_consent", json={"session_id": "nope", "screening": True}).status_code == 404


def test_tool_secret_enforced(seeded, monkeypatch):
    from services.api.config import get_settings
    monkeypatch.setattr(get_settings(), "tool_shared_secret", "s3cret")
    with _client() as c:
        assert c.post("/api/tools/start_session", json={}).status_code == 401
        assert c.post("/api/tools/start_session", json={}, headers={"X-Tool-Secret": "s3cret"}).status_code == 200


def test_new_trial_rematch_outbound_booking(seeded):
    from services.api.registry.rematch import insert_demo_trial, rematch

    async def flow():
        tid = insert_demo_trial()
        res = await rematch([tid], place_calls=True, force_sim=True)
        for _ in range(80):
            await asyncio.sleep(0.25)
            with db_session() as db:
                if not [c for c in db.exec(select(CallLog)).all() if c.status in ("dialing", "ringing", "in_progress")]:
                    break
        return res

    res = asyncio.run(flow())
    assert len(res["calls"]) >= 2, res
    with db_session() as db:
        calls = db.exec(select(CallLog)).all()
        assert sum(1 for c in calls if c.status == "booked") >= 1
        booked = db.exec(select(Referral).where(Referral.trial_id == "DEMO-001", Referral.status == "booked")).all()
        assert booked and all(r.slot_id and r.source == "recall" for r in booked)
        names = {db.get(Profile, r.profile_id).persona_id for r in booked}
        assert "p01_imran" in names and "p02_marcia" not in names and "p04_derek" not in names
    with _client() as c:
        refs = c.get("/api/site/referrals").json()["referrals"]
        assert any(r["trial"]["id"] == "DEMO-001" and r["status"] == "booked" and r["slot"] for r in refs)
        rid = next(r["id"] for r in refs if r["status"] == "booked")
        d = c.post(f"/api/site/referrals/{rid}/decision", json={"decision": "accepted", "reason": "confirmed at visit"}).json()
        assert d["status"] == "accepted" and d["outcomes"][0]["decision"] == "accepted" and d["evidence"]
        assert c.get("/api/equity").json()["registry_n"] >= 9
        st = c.get("/api/stats").json()
        assert st["jev"]["mock"] and st["trials"]["demo"] == 1


def test_browser_answered_sim_call(seeded, monkeypatch):
    from services.api.config import get_settings
    from services.api.registry.rematch import insert_demo_trial, rematch
    from services.api.voice import local_agent
    monkeypatch.setattr(get_settings(), "sim_ring_seconds", 30.0)

    async def flow():
        tid = insert_demo_trial()
        res = await rematch([tid], place_calls=True, force_sim=True)
        await asyncio.sleep(1.3)  # ringing
        cid = res["calls"][0]
        said = [local_agent.recall_answer(cid)]
        assert said[0] and not said[0]["done"]
        for text in ("Yes, go ahead", "No", "No, nothing has changed", "The second one please"):
            said.append(await local_agent.recall_turn(cid, text))
            if said[-1]["done"]:
                break
        return cid, said

    cid, said = asyncio.run(flow())
    assert said[-1]["done"] and "booked" in said[-1]["say"].lower(), said
    with db_session() as db:
        assert db.get(CallLog, cid).status == "booked"


def test_benchmark_outputs(tmp_path, monkeypatch):
    from services.api.benchmark import run_jev_benchmark as rb
    monkeypatch.setattr(rb, "BENCH_RESULTS", tmp_path / "results.json")
    out = asyncio.run(rb.main(120, True, False, 0))
    m = out["mappings"]["drop_not_applicable"]
    assert out["mock"] and out["warning"] and m["n"] > 80
    for k in ("accuracy", "macro_f1", "ece", "confusion", "reliability"):
        assert m[k] is not None
    assert out["latency"]["request_p50_ms"] is not None and out["latency"]["request_p95_ms"] is not None
    assert out["cost"]["usd_per_1000_criteria"] > 0 and (tmp_path / "results.json").exists()
    assert any(r.get("reported_not_rerun") for r in out["comparison"])


def test_real_calls_only_to_allowlisted_numbers(seeded, monkeypatch):
    """With ElevenLabs + Twilio configured, a registry number NOT in OUTBOUND_ALLOWLIST still gets a simulated call."""
    from services.api.config import get_settings
    from services.api.models import ContactDetail
    from services.api.voice import outbound

    s = get_settings()
    monkeypatch.setattr(s, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(s, "elevenlabs_recall_agent_id", "agent")
    monkeypatch.setattr(s, "elevenlabs_phone_number_id", "phone")
    monkeypatch.setattr(s, "outbound_allowlist", "+447700900001")
    placed = []

    async def fake_real(call_id, referral_id, to_number, dv):
        placed.append(to_number)
    monkeypatch.setattr(outbound, "_elevenlabs_call", fake_real)

    async def flow():
        with db_session() as db:
            ref = db.exec(select(Referral)).first()
            if not db.get(ContactDetail, ref.profile_id):
                db.add(ContactDetail(profile_id=ref.profile_id, phone_e164="+447700900999"))  # not allowlisted
            db.commit()
            rid = ref.id
        v = await outbound.place_call(rid)
        await asyncio.sleep(0.05)
        return v
    view = asyncio.run(flow())
    assert view["mode"] == "sim" and placed == []
    assert s.outbound_allowed_numbers == {"+447700900001"}

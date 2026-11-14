"""TrialReady API. Run from the repo root:  uvicorn services.api.main:app --port 8000"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from .config import BENCH_RESULTS, PROCESSED_TRIALS, get_settings
from .db import STORE, db_session, init_db, load_processed_trials
from .equity.census import equity
from .events import bus
from .jev.client import get_router
from .matching import engine, thresholds
from .models import Answer, CallLog, ContactDetail, Profile, Referral, ScreenSession, now
from .registry import rematch as rematch_mod
from .seed import seed
from .site.referrals import pseudonym
from .site.referrals import router as site_router
from .trials.topics import TOPICS
from .voice import local_agent
from .voice.outbound import place_call
from .voice.tools_intake import FIRST_MESSAGE, create_session
from .voice.tools_intake import router as intake_router
from .voice.tools_recall import router as recall_router
from .voice.webhooks import router as webhook_router
from .voice.whatsapp import router as whatsapp_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("trialready")
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_db()
    STORE.load(force=True)
    if not STORE.all():
        n = load_processed_trials(PROCESSED_TRIALS)
        log.info("loaded %d trials from %s", n, PROCESSED_TRIALS.name)
    s = get_settings()
    if s.demo_mode:
        with db_session() as db:
            empty = db.exec(select(Profile)).first() is None
        if empty:
            log.info("demo mode: seeding personas -> %s", await seed(reset=False))
    if s.enable_scheduler:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            _scheduler = AsyncIOScheduler(timezone="Europe/London")
            _scheduler.add_job(rematch_mod.nightly, "cron", hour=2, minute=0, id="nightly_ingest_rematch")
            _scheduler.start()
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler not started: %r", e)
    r = get_router()
    log.info("Jev provider chain: %s", " -> ".join(b.name for b in r.backends))
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="TrialReady", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for r in (intake_router, recall_router, webhook_router, whatsapp_router, site_router):
    app.include_router(r)


# ---------------------------------------------------------------- sessions / realtime

class SessionIn(BaseModel):
    channel: str = "web"
    language: str = "en"


@app.post("/api/sessions")
def new_session(b: Optional[SessionIn] = None):
    b = b or SessionIn()
    return create_session(b.channel, b.language)


def _session_state(db, sess: ScreenSession) -> dict:
    prof = db.exec(select(Profile).where(Profile.session_id == sess.id)).first()
    answers = db.exec(select(Answer).where(Answer.session_id == sess.id).order_by(Answer.id)).all()
    return {"session_id": sess.id, "channel": sess.channel, "language": sess.language, "status": sess.status, "stage": sess.stage,
            "consent_screening": sess.consent_screening, "consent_contact": sess.consent_contact, "asked": sess.asked_topics or [],
            "current_topic": sess.current_topic, "funnel": sess.funnel or [], "trials_total": len(STORE.all()),
            "turns": [{"topic": a.topic, "label": TOPICS.get(a.topic, {}).get("label", a.topic), "question": a.question_text, "answer": a.answer_text, "extracted": a.extracted} for a in answers],
            "profile_id": prof.id if prof else None}


@app.get("/api/sessions/latest")
def latest_session(channel: Optional[str] = None):
    with db_session() as db:
        q = select(ScreenSession).where(ScreenSession.channel != "sim").order_by(ScreenSession.created_at.desc())
        if channel:
            q = q.where(ScreenSession.channel == channel)
        sess = db.exec(q).first()
        if not sess:
            raise HTTPException(404, "no sessions yet")
        return _session_state(db, sess)


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    with db_session() as db:
        sess = db.get(ScreenSession, sid)
        if not sess:
            raise HTTPException(404)
        return _session_state(db, sess)


@app.get("/api/sessions/{sid}/trial/{trial_id}")
def session_trial(sid: str, trial_id: str):
    with db_session() as db:
        prof = db.exec(select(Profile).where(Profile.session_id == sid)).first()
        t = STORE.get(trial_id)
        if not prof or not t:
            raise HTTPException(404)
        rows = engine.criteria_table(prof, trial_id)
        m = engine.trial_math(STORE.crit(trial_id), engine.cached_scores(prof.id), asked=frozenset(prof.facts or {}))
    return {"trial": {"id": t.id, "title": t.title, "lay_title": t.lay_title, "lay_summary": t.lay_summary, "phase": t.phase, "status": t.status, "source": t.source},
            "criteria": rows, **{k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}}


@app.get("/api/events")
async def events(session_id: Optional[str] = None):
    return StreamingResponse(bus.stream(session_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ---------------------------------------------------------------- built-in (no-ElevenLabs) agent

class TurnIn(BaseModel):
    session_id: str
    text: str


class CallTurnIn(BaseModel):
    text: str


@app.post("/api/sim/turn")
async def sim_turn(b: TurnIn):
    return await local_agent.intake_turn(b.session_id, b.text)


@app.post("/api/sim/calls/{call_id}/answer")
def sim_call_answer(call_id: int):
    r = local_agent.recall_answer(call_id)
    if r is None:
        raise HTTPException(409, "call is not ringing")
    return r


@app.post("/api/sim/calls/{call_id}/turn")
async def sim_call_turn(call_id: int, b: CallTurnIn):
    return await local_agent.recall_turn(call_id, b.text)


# ---------------------------------------------------------------- trials

def _trial_view(t) -> dict:
    cr = STORE.crit(t.id)
    return {"id": t.id, "title": t.title, "lay_title": t.lay_title, "lay_summary": t.lay_summary, "phase": t.phase, "status": t.status,
            "conditions": t.conditions, "min_age": t.min_age, "max_age": t.max_age, "sex": t.sex, "sites": t.sites, "last_update": t.last_update,
            "source": t.source, "n_criteria": len(cr), "n_inclusion": sum(1 for c in cr if c.kind == "inclusion")}


@app.get("/api/trials")
def trials():
    ts = sorted(STORE.all(), key=lambda t: (t.source != "demo", t.id))
    return {"count": len(ts), "trials": [_trial_view(t) for t in ts]}


@app.get("/api/trials/{trial_id}")
def trial(trial_id: str):
    t = STORE.get(trial_id)
    if not t:
        raise HTTPException(404)
    return {**_trial_view(t), "criteria": [c.model_dump() for c in STORE.crit(trial_id)]}


class IngestIn(BaseModel):
    live: bool = True
    llm: bool = False


@app.post("/api/trials/ingest")
async def ingest(b: Optional[IngestIn] = None):
    b = b or IngestIn()
    from .trials import ingest_ctgov
    mode = "live"
    try:
        out = await asyncio.to_thread(ingest_ctgov.run, not b.live, b.llm)
    except Exception as e:  # noqa: BLE001 - CT.gov down: fall back to the committed JSON (spec §17.4)
        log.warning("live ingest failed: %r", e)
        mode, out = f"committed JSON (live ingest failed: {str(e)[:120]})", None
    n = load_processed_trials(PROCESSED_TRIALS)
    return {"mode": mode if b.live else "rebuilt from data/raw", "trials": n, "criteria": out["n_criteria"] if out else None}


# ---------------------------------------------------------------- registry / rematch / outbound

class RematchIn(BaseModel):
    trial_ids: Optional[list[str]] = None
    place_calls: bool = True
    force_sim: bool = False


@app.post("/api/registry/rematch")
async def registry_rematch(b: Optional[RematchIn] = None):
    b = b or RematchIn()
    ids = b.trial_ids or [t.id for t in STORE.all()]
    return await rematch_mod.rematch(ids, b.place_calls, b.force_sim)


class DemoIn(BaseModel):
    force_sim: bool = False


@app.post("/api/demo/new-trial")
async def demo_new_trial(b: Optional[DemoIn] = None):
    tid = rematch_mod.insert_demo_trial(reset=True)
    res = await rematch_mod.rematch([tid], place_calls=True, force_sim=bool(b and b.force_sim))
    return {"trial_id": tid, **res}


@app.post("/api/demo/reset")
async def demo_reset():
    return await seed(reset=True)


@app.get("/api/registry")
def registry():
    with db_session() as db:
        sessions = db.exec(select(ScreenSession).where(ScreenSession.channel != "sim")).all()
        all_sessions = db.exec(select(ScreenSession)).all()
        members = db.exec(select(Profile).where(Profile.registry_member == True).order_by(Profile.created_at.desc())).all()  # noqa: E712
        phones = {c.profile_id for c in db.exec(select(ContactDetail)).all()}
        by_sess = {s.id: s for s in all_sessions}

        def count(items, key):
            out: dict = {}
            for i in items:
                out[key(i)] = out.get(key(i), 0) + 1
            return [{"key": k, "n": v} for k, v in sorted(out.items(), key=lambda x: -x[1])]

        return {
            "members": len(members), "with_phone": sum(1 for m in members if m.id in phones), "synthetic": sum(1 for m in members if m.persona_id),
            "consent": {"sessions": len(all_sessions), "live_sessions": len(sessions), "consent_screening": sum(1 for s in all_sessions if s.consent_screening),
                        "consent_contact": sum(1 for s in all_sessions if s.consent_contact), "withdrawn": sum(1 for s in all_sessions if s.status == "withdrawn"),
                        "safety_stops": sum(1 for s in all_sessions if s.status == "safety")},
            "languages": count(members, lambda m: m.language or "en"),
            "channels": count(members, lambda m: (by_sess.get(m.session_id).channel if by_sess.get(m.session_id) else "unknown")),
            "list": [{"profile_id": m.id, "pseudonym": pseudonym(m), "age_band": m.age_band, "sex": m.sex, "district": m.postcode_district, "language": m.language,
                      "ethnicity": m.ethnicity_self, "synthetic": bool(m.persona_id), "has_phone": m.id in phones, "topics": sorted((m.facts or {}).keys())} for m in members],
        }


class ContactIn(BaseModel):
    first_name: Optional[str] = None
    phone_e164: str
    consent_confirmed: bool = False


@app.post("/api/registry/{profile_id}/contact")
def set_contact(profile_id: str, b: ContactIn):
    """Attach a consenting attendee's phone number to a registry profile (stored apart from the profile; purged after the event)."""
    import re
    if not b.consent_confirmed:
        raise HTTPException(400, "consent_confirmed must be true: only numbers of people who explicitly agreed to be called")
    if not re.fullmatch(r"\+\d{8,15}", b.phone_e164):
        raise HTTPException(400, "phone must be E.164, e.g. +447700900123")
    with db_session() as db:
        p = db.get(Profile, profile_id)
        if not p or not p.registry_member:
            raise HTTPException(404, "not a registry member")
        if b.first_name:
            p.first_name = b.first_name.strip()[:40]
        db.merge(ContactDetail(profile_id=profile_id, phone_e164=b.phone_e164))
        db.add(p)
        db.commit()
    return {"ok": True}


class OutboundIn(BaseModel):
    referral_id: int
    force_sim: bool = False


@app.post("/api/outbound/call")
async def outbound_call(b: OutboundIn):
    with db_session() as db:
        if not db.get(Referral, b.referral_id):
            raise HTTPException(404)
    return await place_call(b.referral_id, b.force_sim)


@app.get("/api/calls")
def calls(limit: int = 30):
    with db_session() as db:
        rows = db.exec(select(CallLog).order_by(CallLog.id.desc()).limit(limit)).all()
        out = []
        for c in rows:
            p = db.get(Profile, c.profile_id) if c.profile_id else None
            t = STORE.get(c.trial_id) if c.trial_id else None
            out.append({"call_id": c.id, "referral_id": c.referral_id, "status": c.status, "mode": c.mode, "direction": c.direction, "summary": c.summary,
                        "person": pseudonym(p) if p else "-", "lay_title": t.lay_title if t else "", "started_at": c.started_at.isoformat(),
                        "ended_at": c.ended_at.isoformat() if c.ended_at else None, "transcript": c.transcript or []})
        return {"calls": out}


# ---------------------------------------------------------------- dashboards

@app.get("/api/equity")
def api_equity(area: str = "camden"):
    return equity(area)


@app.get("/api/benchmark")
def api_benchmark():
    if not BENCH_RESULTS.exists():
        return {"available": False, "hint": "Run: python -m services.api.benchmark.run_jev_benchmark --n 500"}
    return {"available": True, **json.loads(BENCH_RESULTS.read_text(encoding="utf-8")),
            "thresholds_in_use": {"KNOCKOUT": thresholds.KNOCKOUT, "REFER_P_PES": thresholds.REFER_P_PES, "REVIEW_P_OPT": thresholds.REVIEW_P_OPT}}


@app.get("/api/stats")
def stats():
    s = get_settings()
    with db_session() as db:
        refs = db.exec(select(Referral)).all()
        cl = db.exec(select(CallLog)).all()
        members = len(db.exec(select(Profile).where(Profile.registry_member == True)).all())  # noqa: E712
    ts = STORE.all()

    def by(items, attr):
        out: dict = {}
        for i in items:
            out[getattr(i, attr)] = out.get(getattr(i, attr), 0) + 1
        return out

    return {"time": now().isoformat(),
            "trials": {"count": len(ts), "criteria": sum(len(STORE.crit(t.id)) for t in ts), "last_update": max((t.last_update for t in ts), default=""),
                       "recruiting": sum(1 for t in ts if t.status == "RECRUITING"), "demo": sum(1 for t in ts if t.source == "demo")},
            "registry_members": members, "referrals": by(refs, "status"), "referral_bands": by(refs, "band"), "calls": by(cl, "status"),
            "jev": get_router().snapshot(),
            "voice": {"elevenlabs_intake_agent": bool(s.elevenlabs_intake_agent_id), "outbound_ready": s.elevenlabs_outbound_ready,
                      "webhook_signed": bool(s.elevenlabs_webhook_secret), "tool_secret_set": bool(s.tool_shared_secret),
                      "outbound_allowlist_count": len(s.outbound_allowed_numbers), "whatsapp_ready": s.whatsapp_ready},
            "pes_mode": s.pes_mode}


@app.get("/api/config")
def public_config():
    s = get_settings()
    return {"elevenlabs_intake_agent_id": s.elevenlabs_intake_agent_id or None, "outbound_ready": s.elevenlabs_outbound_ready,
            "first_message": FIRST_MESSAGE, "demo_mode": s.demo_mode, "jev": {k: get_router().snapshot()[k] for k in ("active", "mock", "fallback_mode", "model")}}


@app.get("/api/health")
def health():
    return {"ok": True}

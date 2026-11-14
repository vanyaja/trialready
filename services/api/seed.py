"""Demo state: trials + synthetic personas in the registry. Used by scripts/seed_demo.py and POST /api/demo/reset."""
import json
import time
from typing import Optional

from sqlalchemy import delete
from sqlmodel import SQLModel, select

from .config import DATA, PROCESSED_TRIALS
from .db import STORE, db_session, get_engine, init_db, load_processed_trials
from .events import bus
from .jev.client import JevRouter, get_router, set_router
from .matching import engine
from .models import Profile, Referral, ScreenSession
from .voice import local_agent
from .voice.tools_intake import create_session

PERSONA_DIR = DATA / "personas"


def load_personas() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PERSONA_DIR.glob("*.json"))]


async def replay_persona(p: dict, channel: str = "sim", max_turns: int = 20) -> dict:
    """Run one persona through the full intake script using its scripted answers. Returns a trace for tests."""
    sid = create_session(channel, p.get("language", "en"))["session_id"]
    trace = {"session_id": sid, "turns": [], "topics": []}
    r = await local_agent.intake_turn(sid, p["script"].get("consent", "Yes, that's fine."))
    for _ in range(max_turns):
        trace["turns"].append(r)
        if r["done"]:
            break
        if r["stage"] == "questions":
            topic = r.get("topic")
            trace["topics"].append(topic)
            ans = p["script"].get(topic, "I don't know.")
        elif r["stage"] == "future_contact":
            ans = p["script"].get("future_contact", "Yes please.")
        elif r["stage"] == "ethnicity":
            ans = p["script"].get("ethnicity", "skip")
        else:
            ans = "yes"
        r = await local_agent.intake_turn(sid, ans)
    with db_session() as db:
        prof = db.exec(select(Profile).where(Profile.session_id == sid)).first()
        if prof:
            prof.persona_id, prof.first_name = p["id"], p.get("first_name")
            db.add(prof)
            db.commit()
            trace["profile_id"] = prof.id
        sess = db.get(ScreenSession, sid)
        trace["status"], trace["n_questions"] = sess.status, len(sess.asked_topics or [])
    return trace


async def seed(reset: bool = True, personas: bool = True, quiet: bool = True) -> dict:
    t0 = time.perf_counter()
    if reset:
        SQLModel.metadata.drop_all(get_engine())
        engine._scores.clear()
    init_db()
    n_trials = load_processed_trials(PROCESSED_TRIALS)
    STORE.load(force=True)
    traces = []
    if personas:
        prev: Optional[JevRouter] = get_router()
        muted, bus.publish = bus.publish, (lambda *a, **k: None) if quiet else bus.publish
        set_router(JevRouter(force_mock=True))  # seeding is deterministic and free: always the rule engine
        try:
            for p in load_personas():
                traces.append(await replay_persona(p))
        finally:
            set_router(prev)
            bus.publish = muted
    with db_session() as db:
        # personas are registry members, not live referrals: start the nurse queue empty
        ids = [p.id for p in db.exec(select(Profile).where(Profile.persona_id != None)).all()]  # noqa: E711
        if ids:
            db.exec(delete(Referral).where(Referral.profile_id.in_(ids)))
            db.commit()
        members = len(db.exec(select(Profile).where(Profile.registry_member == True)).all())  # noqa: E712
    return {"trials": n_trials, "personas": len(traces), "registry_members": members, "seconds": round(time.perf_counter() - t0, 2)}

"""Rematch + recall (spec §10): re-score every registry member against new/updated trials, refer and phone the matches."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete
from sqlmodel import select

from ..config import DATA, get_settings
from ..db import STORE, db_session, upsert_trial
from ..events import bus
from ..jev.client import get_router
from ..matching import engine
from ..models import CallLog, CriterionScore, Outcome, Profile, Referral, Trial, TrialScore
from ..voice.outbound import place_call
from ..voice.tools_intake import create_referral
from ..voice.tools_recall import call_worthy

log = logging.getLogger("rematch")
DEMO_TRIAL = DATA / "processed" / "demo_trial.json"


async def rematch(trial_ids: list[str], place_calls: bool = True, force_sim: bool = False) -> dict:
    jev = get_router()
    with db_session() as db:
        members = db.exec(select(Profile).where(Profile.registry_member == True)).all()  # noqa: E712
    total, matched, calls, decisions = len(members), [], [], 0
    bus.publish("rematch_progress", {"done": 0, "total": total, "matched": 0, "trial_ids": trial_ids, "state": "started"})
    for i, prof in enumerate(members, 1):
        scored = await engine.score_profile(prof, jev, trial_ids=trial_ids, timeout=max(get_settings().jev_turn_timeout_s, 5.0))
        decisions += scored["decisions"]
        for r in scored["results"]:
            if not call_worthy(prof, r):
                continue
            with db_session() as db:
                if db.exec(select(Referral).where(Referral.profile_id == prof.id, Referral.trial_id == r["trial_id"])).first():
                    continue
                ref = create_referral(db, prof, r, "recall")
            matched.append(ref.id)
            if place_calls:
                calls.append(await place_call(ref.id, force_sim=force_sim))
        bus.publish("rematch_progress", {"done": i, "total": total, "matched": len(matched), "trial_ids": trial_ids, "state": "running"})
        await asyncio.sleep(0)
    out = {"profiles": total, "trials": trial_ids, "referrals": matched, "calls": [c["call_id"] for c in calls], "decisions": decisions}
    bus.publish("rematch_progress", {"done": total, "total": total, "matched": len(matched), "trial_ids": trial_ids, "state": "finished", "decisions": decisions})
    return out


def insert_demo_trial(reset: bool = True) -> str:
    """Insert the clearly-labelled fictional trial used for the 'new trial opens -> phones ring' moment (spec §7.4)."""
    t = json.loads(DEMO_TRIAL.read_text(encoding="utf-8"))
    t["last_update"] = datetime.now(timezone.utc).date().isoformat()
    with db_session() as db:
        if reset:  # make the stage button repeatable
            ref_ids = [r.id for r in db.exec(select(Referral).where(Referral.trial_id == t["id"])).all()]
            if ref_ids:
                db.exec(delete(Outcome).where(Outcome.referral_id.in_(ref_ids)))
                db.exec(delete(CallLog).where(CallLog.referral_id.in_(ref_ids)))
                db.exec(delete(Referral).where(Referral.id.in_(ref_ids)))
            db.exec(delete(TrialScore).where(TrialScore.trial_id == t["id"]))
            db.exec(delete(CriterionScore).where(CriterionScore.criterion_id.like(f"{t['id']}__%")))
        upsert_trial(db, t)
        db.commit()
    engine._scores.clear()
    STORE.load(force=True)
    bus.publish("trial_added", {"trial_id": t["id"], "lay_title": t["lay_title"], "source": "demo"})
    return t["id"]


async def nightly(use_llm: bool = False) -> dict:
    """02:00 job: pull CT.gov, upsert trials that are new or updated since the last run, rematch them."""
    from ..trials.ingest_ctgov import build_processed, fetch_raw
    try:
        raw = await asyncio.to_thread(fetch_raw)
    except Exception as e:  # noqa: BLE001 - CT.gov down: keep the committed data (spec §17.4)
        log.warning("nightly ingest failed: %r", e)
        return {"error": str(e)[:200]}
    processed = await asyncio.to_thread(build_processed, raw, use_llm)
    changed = []
    with db_session() as db:
        for t in processed["trials"]:
            old: Optional[Trial] = db.get(Trial, t["id"])
            if old is None or (t["last_update"] or "") > (old.last_update or ""):
                upsert_trial(db, t)
                changed.append(t["id"])
        db.commit()
    if changed:
        STORE.load(force=True)
        res = await rematch(changed)
        return {"changed": changed, **res}
    return {"changed": []}

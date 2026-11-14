"""Research-nurse dashboard API (spec §11)."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..db import STORE, db_session
from ..events import bus
from ..models import CallLog, ContactDetail, Outcome, Profile, Referral, Slot
from .slots import spoken

router = APIRouter(prefix="/api/site", tags=["site"])


def pseudonym(p: Optional[Profile]) -> str:
    if not p:
        return "removed"
    return p.first_name or f"P-{p.id[:6]}"


def referral_view(db, r: Referral, detail: bool = True) -> dict:
    t = STORE.get(r.trial_id)
    p = db.get(Profile, r.profile_id)
    slot = db.get(Slot, r.slot_id) if r.slot_id else None
    out = {"id": r.id, "status": r.status, "band": r.band, "source": r.source, "created_at": r.created_at.isoformat(),
           "p_pes": round(r.p_pes, 3), "p_opt": round(r.p_opt, 3),
           "trial": {"id": r.trial_id, "title": t.title if t else r.trial_id, "lay_title": t.lay_title if t else "", "source": t.source if t else "", "phase": t.phase if t else ""},
           "person": {"pseudonym": pseudonym(p), "age_band": p.age_band if p else None, "sex": p.sex if p else None, "district": p.postcode_district if p else None,
                      "language": p.language if p else None, "synthetic": bool(p and p.persona_id)},
           "slot": {"id": slot.id, "site_name": slot.site_name, "start_ts": slot.start_ts.isoformat(), "spoken": spoken(slot)} if slot else None}
    ev = r.evidence or []
    out["counts"] = {"ok": sum(1 for e in ev if e.get("ok") is True), "problem": sum(1 for e in ev if e.get("ok") is False), "unknown": sum(1 for e in ev if e.get("ok") is None)}
    if detail:
        out["evidence"] = ev
        out["recheck"] = r.recheck or {}
        out["transcript"] = (p.transcript_snippets if p else []) or []
        calls = db.exec(select(CallLog).where(CallLog.referral_id == r.id).order_by(CallLog.id.desc())).all()
        out["calls"] = [{"id": c.id, "status": c.status, "mode": c.mode, "summary": c.summary, "transcript": c.transcript or [], "started_at": c.started_at.isoformat()} for c in calls]
        outs = db.exec(select(Outcome).where(Outcome.referral_id == r.id).order_by(Outcome.id.desc())).all()
        out["outcomes"] = [{"decision": o.nurse_decision, "reason": o.reason, "ts": o.ts.isoformat()} for o in outs]
        out["has_phone"] = bool(p and db.get(ContactDetail, p.id))
    return out


@router.get("/referrals")
def list_referrals(detail: bool = False):
    with db_session() as db:
        refs = db.exec(select(Referral).order_by(Referral.id.desc())).all()
        n_out = len(db.exec(select(Outcome)).all())
        return {"referrals": [referral_view(db, r, detail) for r in refs], "outcomes_logged": n_out,
                "outcome_loop_note": "Nurse outcomes are stored so thresholds can be recalibrated per site (isotonic). For the hackathon, calibration is shown from the benchmark."}


@router.get("/referrals/{rid}")
def get_referral(rid: int):
    with db_session() as db:
        r = db.get(Referral, rid)
        if not r:
            raise HTTPException(404)
        return referral_view(db, r)


class DecisionIn(BaseModel):
    decision: str  # accepted | needs_info | ineligible
    reason: str = ""


@router.post("/referrals/{rid}/decision")
def decide(rid: int, b: DecisionIn):
    if b.decision not in ("accepted", "needs_info", "ineligible"):
        raise HTTPException(400, "decision must be accepted | needs_info | ineligible")
    with db_session() as db:
        r = db.get(Referral, rid)
        if not r:
            raise HTTPException(404)
        r.status = b.decision
        db.add(r)
        db.add(Outcome(referral_id=rid, nurse_decision=b.decision, reason=b.reason))
        db.commit()
        bus.publish("referral_updated", {"referral_id": rid, "status": r.status})
        return referral_view(db, r)

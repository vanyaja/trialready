"""SQLModel tables (spec §6). No `from __future__ import annotations` here: SQLModel needs real types."""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


def short_id() -> str:
    return uuid.uuid4().hex[:12]


class Trial(SQLModel, table=True):
    id: str = Field(primary_key=True)  # NCT id or DEMO-xxx
    title: str = ""
    lay_title: str = ""
    lay_summary: str = ""
    phase: str = ""
    status: str = "RECRUITING"
    conditions: list = Field(default_factory=list, sa_column=Column(JSON))
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    sex: str = "ALL"
    sites: list = Field(default_factory=list, sa_column=Column(JSON))  # [{name, city, lat, lon}]
    last_update: str = ""
    source: str = "ctgov"  # ctgov | demo


class Criterion(SQLModel, table=True):
    id: str = Field(primary_key=True)  # f"{trial_id}__inc_3"
    trial_id: str = Field(index=True)
    kind: str = "inclusion"  # inclusion | exclusion
    text: str = ""
    lay_text: str = ""
    topic: str = "other"
    hard_filter: bool = False
    cohort_specific: bool = False  # applies to one cohort/sub-study only: never knocks a trial out


class ScreenSession(SQLModel, table=True):
    __tablename__ = "session"
    id: str = Field(default_factory=short_id, primary_key=True)
    channel: str = "web"  # web | phone_in | phone_out | sim
    language: str = "en"
    consent_screening: bool = False
    consent_contact: bool = False
    consent_ts: Optional[datetime] = None
    status: str = "started"  # started | screening | done | safety | withdrawn | declined
    created_at: datetime = Field(default_factory=now)
    eleven_conversation_id: Optional[str] = None
    # conversation state
    current_topic: Optional[str] = None
    asked_topics: list = Field(default_factory=list, sa_column=Column(JSON))
    reasked: list = Field(default_factory=list, sa_column=Column(JSON))
    stage: str = "consent"  # local-agent stage
    funnel: list = Field(default_factory=list, sa_column=Column(JSON))  # trials still possible after each turn


class Profile(SQLModel, table=True):
    id: str = Field(default_factory=short_id, primary_key=True)
    session_id: Optional[str] = Field(default=None, index=True)
    registry_member: bool = False
    first_name: Optional[str] = None
    postcode_district: Optional[str] = None
    age: Optional[int] = None
    age_band: Optional[str] = None
    sex: Optional[str] = None
    facts: dict = Field(default_factory=dict, sa_column=Column(JSON))  # {topic: {..., answer_text, ts}}
    transcript_snippets: list = Field(default_factory=list, sa_column=Column(JSON))
    ethnicity_self: Optional[str] = None
    language: str = "en"
    persona_id: Optional[str] = None  # set for synthetic personas
    created_at: datetime = Field(default_factory=now)


class ContactDetail(SQLModel, table=True):
    """Phone numbers are stored apart from the pseudonymised profile (spec §6/§18)."""
    profile_id: str = Field(primary_key=True)
    phone_e164: str


class Answer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    topic: str
    question_text: str = ""
    answer_text: str = ""
    extracted: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ts: datetime = Field(default_factory=now)


class CriterionScore(SQLModel, table=True):
    profile_id: str = Field(primary_key=True)
    criterion_id: str = Field(primary_key=True)
    p_met: float = 0.0
    p_not_met: float = 0.0
    p_unknown: float = 1.0
    model: str = ""
    ts: datetime = Field(default_factory=now)


class TrialScore(SQLModel, table=True):
    profile_id: str = Field(primary_key=True)
    trial_id: str = Field(primary_key=True)
    p_opt: float = 0.0
    p_pes: float = 0.0
    uncertainty: float = 0.0
    knocked_out: bool = False
    knock_criterion_id: Optional[str] = None
    distance_km: Optional[float] = None
    ts: datetime = Field(default_factory=now)


class Referral(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: str = Field(index=True)
    trial_id: str = Field(index=True)
    source: str = "intake"  # intake | recall
    band: str = "refer"  # refer | human_review
    status: str = "new"  # new | booked | accepted | needs_info | ineligible
    slot_id: Optional[int] = None
    p_pes: float = 0.0
    p_opt: float = 0.0
    evidence: Any = Field(default_factory=list, sa_column=Column(JSON))
    recheck: Any = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


class Slot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    site_name: str = Field(index=True)
    start_ts: datetime
    capacity: int = 1
    booked: int = 0


class CallLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    direction: str = "outbound"
    mode: str = "sim"  # elevenlabs | sim
    profile_id: Optional[str] = Field(default=None, index=True)
    trial_id: Optional[str] = None
    referral_id: Optional[int] = None
    eleven_conversation_id: Optional[str] = Field(default=None, index=True)
    status: str = "queued"  # queued | dialing | ringing | in_progress | booked | not_eligible | declined | no_answer | completed | failed
    started_at: datetime = Field(default_factory=now)
    ended_at: Optional[datetime] = None
    summary: str = ""
    transcript: Any = Field(default_factory=list, sa_column=Column(JSON))


class Outcome(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    referral_id: int = Field(index=True)
    nurse_decision: str
    reason: str = ""
    ts: datetime = Field(default_factory=now)


class BenchmarkRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    model: str
    n_pairs: int = 0
    metrics: Any = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


class WhatsAppContact(SQLModel, table=True):
    """Registry members who joined over WhatsApp (Twilio). Kept apart from the pseudonymised profile, like ContactDetail."""
    profile_id: str = Field(primary_key=True)
    phone_e164: str


class WhatsAppThread(SQLModel, table=True):
    """Routes an incoming WhatsApp message to the conversation it belongs to (an intake session or a recall call)."""
    phone_e164: str = Field(primary_key=True)
    kind: str = "intake"  # intake | recall
    session_id: Optional[str] = None
    call_id: Optional[int] = None
    updated_at: datetime = Field(default_factory=now)

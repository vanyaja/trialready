"""Engine, sessions and the in-memory trial store."""
import json
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import delete
from sqlmodel import Session, SQLModel, create_engine, select

from . import models  # noqa: F401  (register tables)
from .config import PROCESSED_TRIALS, get_settings
from .models import Criterion, Trial

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().db_url
        _engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    STORE.loaded = False


def init_db():
    SQLModel.metadata.create_all(get_engine())


@contextmanager
def db_session():
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


class TrialStore:
    """Trials + criteria cached in memory: read on every scoring turn, written rarely."""

    def __init__(self):
        self.trials: dict[str, Trial] = {}
        self.criteria: dict[str, list[Criterion]] = {}
        self.by_id: dict[str, Criterion] = {}
        self.loaded = False

    def load(self, force: bool = False):
        if self.loaded and not force:
            return
        with db_session() as db:
            self.trials = {t.id: t for t in db.exec(select(Trial)).all()}
            self.criteria = {tid: [] for tid in self.trials}
            self.by_id = {}
            for c in db.exec(select(Criterion)).all():
                self.criteria.setdefault(c.trial_id, []).append(c)
                self.by_id[c.id] = c
        for lst in self.criteria.values():
            lst.sort(key=lambda c: (c.kind != "inclusion", int(c.id.rsplit("_", 1)[-1])))
        self.loaded = True

    def get(self, trial_id: str) -> Optional[Trial]:
        self.load()
        return self.trials.get(trial_id)

    def all(self) -> list[Trial]:
        self.load()
        return list(self.trials.values())

    def crit(self, trial_id: str) -> list[Criterion]:
        self.load()
        return self.criteria.get(trial_id, [])


STORE = TrialStore()


def upsert_trial(db: Session, t: dict):
    """Insert/replace one processed trial dict ({..., criteria:[...]})."""
    db.exec(delete(Criterion).where(Criterion.trial_id == t["id"]))
    existing = db.get(Trial, t["id"])
    if existing:
        db.delete(existing)
        db.flush()
    db.add(Trial(**{k: v for k, v in t.items() if k in Trial.model_fields}))
    for c in t.get("criteria", []):
        db.add(Criterion(**{k: v for k, v in c.items() if k in Criterion.model_fields}, trial_id=t["id"]))


def load_processed_trials(path=PROCESSED_TRIALS) -> int:
    """Load the committed processed JSON into the DB (spec §17.4 fallback when CT.gov is down)."""
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    with db_session() as db:
        for t in data["trials"]:
            upsert_trial(db, t)
        db.commit()
    STORE.load(force=True)
    return len(data["trials"])

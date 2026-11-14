"""Site appointment slots (demo data; a real deployment would read the site's calendar)."""
from datetime import datetime, time, timedelta, timezone

from sqlmodel import select

from ..models import Slot


def ensure_slots(db, site_name: str, n_days: int = 3) -> list[Slot]:
    """Make sure the site has upcoming slots: next weekdays at 10:00 and 14:30."""
    nowt = datetime.now(timezone.utc)
    have = db.exec(select(Slot).where(Slot.site_name == site_name, Slot.start_ts > nowt, Slot.booked < Slot.capacity).order_by(Slot.start_ts)).all()
    if len(have) >= 2:
        return have
    day, made = nowt.date() + timedelta(days=2), 0
    while made < n_days:
        if day.weekday() < 5:
            for hh, mm in ((10, 0), (14, 30)):
                db.add(Slot(site_name=site_name, start_ts=datetime.combine(day, time(hh, mm), tzinfo=timezone.utc), capacity=3))
            made += 1
        day += timedelta(days=1)
    db.commit()
    return db.exec(select(Slot).where(Slot.site_name == site_name, Slot.start_ts > nowt, Slot.booked < Slot.capacity).order_by(Slot.start_ts)).all()


def spoken(slot: Slot) -> str:
    d = slot.start_ts
    hour = d.strftime("%I").lstrip("0")
    mins = "" if d.minute == 0 else f":{d.minute:02d}"
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')} at {hour}{mins} {'am' if d.hour < 12 else 'pm'}"

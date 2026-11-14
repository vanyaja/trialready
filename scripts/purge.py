"""Post-event purge (spec §6/§18): delete phone numbers and all non-synthetic profiles and their data.   python scripts/purge.py --yes"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete  # noqa: E402
from sqlmodel import select  # noqa: E402

from services.api.db import db_session, init_db  # noqa: E402
from services.api.models import ContactDetail, Profile  # noqa: E402
from services.api.voice.tools_intake import delete_profile_data  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    init_db()
    with db_session() as db:
        phones = len(db.exec(select(ContactDetail)).all())
        real = db.exec(select(Profile).where(Profile.persona_id == None)).all()  # noqa: E711
        print(f"{phones} phone numbers, {len(real)} non-synthetic profiles")
        if not a.yes:
            print("dry run; pass --yes to delete")
            sys.exit(0)
        db.exec(delete(ContactDetail))
        for p in real:
            delete_profile_data(db, p.id, p.session_id)
        db.commit()
        print("purged")

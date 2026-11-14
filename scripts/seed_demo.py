"""Recreate a clean demo state: trials + 10 synthetic personas in the registry.   python scripts/seed_demo.py --reset"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.api.seed import seed  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop all tables first")
    ap.add_argument("--no-personas", action="store_true")
    a = ap.parse_args()
    print(asyncio.run(seed(reset=a.reset, personas=not a.no_personas)))

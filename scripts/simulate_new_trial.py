"""'A new trial opens': insert the fictional DEMO-001 trial and rematch the registry (spec §7.4, §10).

If the API server is running this goes through it (so the dashboards update live and calls are placed by the server);
otherwise it runs in-process with simulated calls."""
import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--sim", action="store_true", help="force simulated calls even if ElevenLabs/Twilio are configured")
    a = ap.parse_args()
    try:
        r = httpx.post(f"{a.api}/api/demo/new-trial", json={"force_sim": a.sim}, timeout=120)
        r.raise_for_status()
        print("via API:", r.json())
    except httpx.HTTPError as e:
        print(f"API not reachable ({e!r}); running in-process.")
        from services.api.db import init_db
        from services.api.registry.rematch import insert_demo_trial, rematch

        async def main():
            init_db()
            tid = insert_demo_trial()
            res = await rematch([tid], place_calls=True, force_sim=True)
            await asyncio.sleep(12)  # let the simulated calls play out
            return res

        print(asyncio.run(main()))

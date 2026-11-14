"""One real Jev request per configured provider - no MockJev fallback (spec §24 "verify first").

    python scripts/jev_smoke.py              # every provider that has a key in .env
    python scripts/jev_smoke.py typesafe     # just one

Sends a synthetic profile (no real patient data) with one criterion `choice` question and one guard `noul`,
then checks the reply parses into the shape the app uses (services/api/jev/client.py normalize_answers).
Never prints keys or request headers. Exit code 0 only if every provider tried returned parsable answers.
"""
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from services.api.config import get_settings  # noqa: E402
from services.api.jev.client import HttpJevBackend  # noqa: E402
from services.api.jev.questions import criterion_question, guard_questions  # noqa: E402

STATE = {"profile": {"age": 58, "sex": "male", "diabetes": "type 2, diagnosed 6 years ago", "medication": ["metformin", "gliclazide"],
                     "hba1c_mmol_mol": 64, "bmi": 31},
         "answer": "I'm on metformin and gliclazide, my last HbA1c was 64."}


def questions() -> dict:
    g = guard_questions()
    return {"c_hba1c": criterion_question("inclusion", "HbA1c between 7.0% and 10.5% (53-91 mmol/mol)"),
            "c_insulin": criterion_question("exclusion", "Current treatment with insulin"),
            "g__urgent_symptom": g["g__urgent_symptom"]}


async def check(name: str, b: HttpJevBackend) -> bool:
    print(f"\n[{name}] POST https://{urlparse(b.url).netloc}{urlparse(b.url).path}  model={b.model}")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            ans, usage = await b.decide(c, STATE, questions())
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED after {(time.perf_counter() - t0) * 1000:.0f} ms: {str(e)[:300]}")
        return False
    ms = (time.perf_counter() - t0) * 1000
    print(f"  OK {ms:.0f} ms  resolved model={b.resolved_model!r}  usage={usage}")
    ok = True
    for k, a in ans.items():
        if a["type"] == "choice":
            p = {o: round(v, 3) for o, v in a["probabilities"].items()}
            print(f"  {k}: choice={a['choice']} confidence={a['confidence']:.3f} probabilities={p}")
            ok &= set(p) == {"met", "not_met", "unknown"} and abs(sum(p.values()) - 1) < 0.01
        else:
            print(f"  {k}: noul={a['noul']:.3f}")
            ok &= 0.0 <= a["noul"] <= 1.0
    missing = set(questions()) - set(ans)
    if missing:
        print(f"  missing answers: {sorted(missing)}")
    ok &= not missing
    print("  schema: " + ("parses into the app's expected shape" if ok else "DOES NOT match the expected shape"))
    return ok


async def main(only: list[str]) -> int:
    s = get_settings()
    providers = {
        "typesafe": ("TYPESAFE_API_KEY", HttpJevBackend("typesafe", s.typesafe_base_url, s.typesafe_api_key, s.jev_model)),
        "openrouter": ("OPENROUTER_API_KEY", HttpJevBackend("openrouter", s.openrouter_jev_url, s.openrouter_api_key, s.openrouter_jev_model)),
        "aimlapi": ("AIMLAPI_API_KEY", HttpJevBackend("aimlapi", s.aimlapi_jev_url, s.aimlapi_api_key, s.aimlapi_jev_model)),
    }
    tried, results = 0, []
    for name, (var, b) in providers.items():
        if only and name not in only:
            continue
        if not b.key:
            print(f"[{name}] skipped: {var} is empty in .env")
            continue
        tried += 1
        results.append(await check(name, b))
    if not tried:
        print("\nNo Jev provider key set - nothing was called. The app will run on MockJev (labelled 'mock').")
        return 2
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

# TrialReady

**A voice-first network that pre-screens people for UK clinical trials in a five-minute conversation, re-checks every registered person against every new trial, and phones them the moment they look eligible — handing a research nurse an evidence-backed referral.**

AI Cup 2026 · Track D (Science & Engineering). Full build spec: [`AGENTS.md`](AGENTS.md).

> Pre-screening only. Not medical advice. No diagnosis, no treatment advice. A research nurse / PI always confirms eligibility.
> Data: public (ClinicalTrials.gov, ONS Census, TrialGPT annotations), synthetic personas, and explicitly consenting attendees only.

## What runs today

| Part | State |
|---|---|
| Intake loop: consent → adaptive questions → score every criterion of every candidate trial after each answer → top 3 in plain language → registry | Working end to end |
| Rematch + recall: new trial → re-score registry → outbound call → re-check 1–2 things → book a slot → referral | Working end to end |
| Nurse dashboard, registry, equity view (real Census 2021 figures), benchmark page | Working |
| Trial data | 41 real UK interventional type 2 diabetes trials from ClinicalTrials.gov (35 recruiting + 6 not yet recruiting), fetched 14 Nov 2026, 669 atomic criteria |
| **Jev** | Adapter for TypeSafe native → OpenRouter → AIML API is written and unit-tested against the documented schema, **but has never been called with a real key**. With no key the app runs on `MockJev` (keyword rules) and says so in the header badge |
| **ElevenLabs / Twilio** | Tool endpoints, outbound-call client, signed post-call webhook and the web SDK component are written. **Not exercised against a live account.** Without credentials: a built-in scripted agent (typed or browser speech) and simulated calls you can answer in the browser |
| Benchmark | Pipeline runs on the 1,015 TrialGPT expert annotations. The committed `results.json` is a **mock run** and is labelled as such on the page. It is not a result |

## Quick start (no keys needed)

Requirements: Python 3.10+ and Node 18+.

**Windows (PowerShell)**
```powershell
cd D:\trialready
py -3 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn services.api.main:app --port 8000
```
Second terminal:
```powershell
cd D:\trialready\apps\web
npm install
npm run dev
```

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env && uvicorn services.api.main:app --port 8000
# second terminal
cd apps/web && npm install && npm run dev
```

Open <http://localhost:3000>. On first start the API loads the committed trials and seeds 10 synthetic personas into the registry (about 5 s).

## Demo runbook (maps to the 2-minute script, spec §21)

1. **`/call`** → *Start call*. Answer as a patient (try: "58, male" · "N W 3" · "BMI 31" · "type 2, six years ago" · "metformin and gliclazide" · "no, never" · "64"). The meter narrows after each answer (`41 → 29 → 19 → …`), the criteria table fills with met / does not apply / for the nurse, with confidence and the caller's own words as evidence. Ends with up to three studies in plain language and the future-contact question.
   - Say "I have chest pain right now" → safety stop with the 999 / 111 message. Say "remove me" → profile deleted.
2. **`/admin`** → *Simulate new trial opening*. The fictional `DEMO-001` is inserted, every registry member is re-scored, matches are phoned. Simulated calls ring for 6 s — click *Answer in browser* to take one yourself, or let them play out. One persona (Tom) is called and found ineligible on the re-check; that is deliberate.
3. **`/site`** → the booked referrals: criterion table, transcript, slot, Accept / Needs info / Ineligible.
4. **`/benchmark`**, **`/equity`**, **`/registry`**.

Reset between rehearsals: `python scripts/seed_demo.py --reset` (or *Reset demo state* on `/admin`).

## Switching on the real services

1. **Jev** — put a key in `.env` (`TYPESAFE_API_KEY`, or `OPENROUTER_API_KEY` / `AIMLAPI_API_KEY` as fallbacks) and restart. The header badge turns green; if the preferred provider fails the router falls back down the chain and the badge says so. Then:
   ```bash
   python -m services.api.benchmark.run_jev_benchmark --n 500            # writes data/benchmark/results.json
   python -m services.api.benchmark.run_jev_benchmark --n 200 --baseline # + generative-LLM row on the same pairs
   ```
   First thing to check with a real key: one request in the logs returns answers (the adapter accepts `answers` or `decisions` at the top level; anything else raises and falls back to mock — you will see the badge).
2. **ElevenLabs + Twilio** — follow [`docs/ELEVENLABS_SETUP.md`](docs/ELEVENLABS_SETUP.md).
3. **Better criteria text** — with `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`: `python -m services.api.trials.ingest_ctgov --from-raw --llm` re-atomises the criteria and rewrites the plain-English text with the offline LLM, then restart (or `/admin` → Re-ingest).
4. **Fresh trials** — `python -m services.api.trials.ingest_ctgov` (live fetch), or the nightly 02:00 job.

## Tests

```bash
python -m pytest services/api/tests -q      # 14 tests, ~15 s, no network, no keys
```
Covers spec §23: atomiser schema, scoring maths (`P_pes ≤ P_opt`, knock-out), adaptive topic choice, guard routing, persona replay (≤ 8 questions; heart-attack persona knocked out of cardio-exclusion trials), emergency phrase, "remove me", tool-secret enforcement, new trial → ≥ 2 outbound calls → booking visible to the nurse, browser-answered call, webhook signature, benchmark output.

Not covered by tests (needs accounts): live ElevenLabs web call latency, real outbound calls, real Jev responses.

## Deviations from the spec — read before the Q&A

1. **`P_pes` is not the literal product.** Spec §8.4 defines `P_pes = Π p_ok(c)` over all criteria. Real trials carry criteria no phone call can settle (lab values, investigator judgement). One such criterion pins the literal `P_pes` at ~0, so `uncertainty` never drops below the stop threshold and **nobody is ever referred**. Default here (`PES_MODE=screenable`): a criterion that a phone screen cannot confirm — topic `other`, cohort-specific, or still unknown after its topic was asked — contributes `p_ok + p_unk` instead of `p_ok`. `P_pes` then means "confirmed as far as a phone screen can"; those criteria are listed "for the nurse" on the referral. `PES_MODE=strict` gives the literal formula. `P_pes ≤ P_opt` holds in both.
2. **Threshold tuning conflates two quantities.** Spec §13 says to pick the `P_pes` cut-off giving ≥ 0.9 precision on "met". Benchmark precision is per criterion; `P_pes` is a product over a trial. The benchmark reports the per-criterion `P(met)` cut-off; the trial-level thresholds stay in `matching/thresholds.py` and need trial-level outcome data (the nurse outcomes this app logs) to tune properly.
3. **The recall call also asks what was never asked.** A registry member is phoned when the new trial is a confirmed fit, or when at most two never-asked questions stand between "possible" and "confirmed"; the call asks those first, then re-checks a changeable fact.
4. **"At least one of the following" lists stay one criterion**, and **cohort-specific criteria never knock a trial out** (master protocols, control groups). The rule-based atomiser detects both; the LLM atomiser is prompted to.
5. **`K_CANDIDATES=30`** means the first drop on the meter (41 → ~29) is partly the candidate cap, not only age/sex.
6. **shadcn/ui** is not installed; the few primitives are hand-written Tailwind classes in `globals.css`.
7. Ten intake **topics are asked in value order**, so the first clinical question is often BMI (the most common criterion), not diagnosis.

## Verify-first status (spec §24)

| Item | Status |
|---|---|
| Jev request/response field names | `state` + `questions{type, instructions, criteria}` → `choice / confidence / probabilities`, `noul` confirmed from the AIML API docs. TypeSafe-native and OpenRouter payloads **not verified with a key**. Max questions per request and rate limits: unknown |
| Jev model IDs | AIML API lists `typesafe/jev` (currently Jev 1.14, released 14 Oct 2026). The spec's OpenRouter ID `typesafe/jev-1.13` may be stale — check before relying on that fallback |
| Count of UK T2D trials | 35 recruiting interventional with a UK site (< 40), so `NOT_YET_RECRUITING` was added → 41. Several are obesity, type 1 or unrelated trials that merely mention type 2 diabetes; they are knocked out by their own criteria |
| TrialGPT annotations | 1,015 rows; columns and label set verified; trimmed copy committed |
| ElevenLabs | Outbound endpoint + body, webhook signature scheme, `@elevenlabs/react` 1.22 (`ConversationProvider` + `useConversation`) verified from docs/types. System dynamic variables, server-tool UI and second-language support: **verify in the dashboard** |
| Twilio | Not done (needs your account). Trial accounts call verified numbers only |
| Tunnel | Not done |

## How it is built

```
apps/web            Next.js 14 (App Router) + Tailwind + Recharts + @elevenlabs/react; SSE for live updates
services/api        FastAPI, SQLModel/SQLite, APScheduler
  jev/              router with batching (50), concurrency (8), retries, fallback chain, cache, latency/cost log; MockJev rule engine
  trials/           CT.gov v2 ingest, rule-based + LLM atomiser, lay summaries, hard filters (age/sex/status/distance)
  matching/         scoring (engine.py), next-question (adaptive.py), thresholds, answer extraction
  voice/            ElevenLabs server-tool endpoints (intake + recall), outbound client, webhook, built-in scripted agent, prompts
  registry/ site/ equity/ benchmark/
data/               raw + processed trials, lay summaries, personas, Census CSVs, outcode centroids, benchmark annotations/results
scripts/            seed_demo.py, simulate_new_trial.py, purge.py
```
Every Jev decision is a typed question: a `choice` (met / not_met / unknown) per criterion, `noul` guards on every utterance (emergency, stop, withdraw consent, answered-the-question, asks-for-medical-advice), and `choice`/`noul` questions that turn a spoken answer into bands. A turn asks roughly 300–600 questions.

## Reused components and data (disclosure)

- **NIH TrialGPT** (Jin et al., *Nature Communications* 2024, doi:10.1038/s41467-024-53081-z; github.com/ncbi-nlp/TrialGPT): the criterion-level matching idea and label set, and the public expert annotations `ncbi/TrialGPT-Criterion-Annotations` (Hugging Face) for the benchmark. No TrialGPT code is included.
- **ClinicalTrials.gov API v2** — trial records (public domain). Site contact names/emails were stripped at ingest.
- **ONS Census 2021** TS021 and TS007A for Camden and London via the Nomis API (Open Government Licence v3.0) — `data/census/SOURCE.md`.
- **postcodes.io** outward-code centroids (ONS Postcode Directory, OGL) — `data/geo/outcodes.json`.
- **Libraries**: FastAPI, Uvicorn, httpx, pydantic, SQLModel, APScheduler, tenacity, pytest; Next.js, React, Tailwind CSS, Recharts, @elevenlabs/react.
- **AI assistance**: the code was written with Claude. The plain-English trial titles and summaries in `data/processed/lay_summaries.json` were drafted by Claude from the public trial records and have **not** been reviewed by a clinician or sponsor.
- **Services**: ElevenLabs Agents, Twilio, TypeSafe Jev (optional OpenRouter / AIML API gateways), optional Anthropic/OpenAI for offline preprocessing.

## Problem and evidence (as supplied in the spec — not re-verified during the build)

- Willing but never asked: 47% likely to take part; trial participation discussed in 5% of consultations in the past 12 months; 10% have ever taken part (NIHR-part-funded UHS / Alterline survey).
- Be Part of Research: ~708,000 sign-ups (Apr 2026) against a 1.5M target; matching by chosen conditions/demographics, contact by email.
- UK industry-trial participants fell 25% (2022/23 → 2024/25); < 30% of industry trials recruited on time. **Source is the ABPI, the pharma trade body.**
- Independent check: 56% of NIHR HTA RCTs hit their final recruitment target (PMC5372123).
- Representation: clopidogrel and British South Asians (PMC11956285).
- TrialGPT: 87.3% criterion-level accuracy; 42.6% less screening time in a small user study; evaluated on synthetic patients. Recomputed here from the dataset's stored GPT-4 predictions under our 3-way mapping on a 469-pair sample: 86.8%.
- Jev speed and price figures are **vendor-reported**; the benchmark in this repo is the independent check, once it has been run with a key.

Sources are listed in `AGENTS.md` §2.

## Limitations

- MockJev is a keyword rule engine. It makes mistakes a model would not (and vice versa). Do not present mock output as model output.
- The rule-based atomiser is faithful (it never invents text) but coarse; run the LLM atomiser before judging.
- English only in the built-in agent; multilingual depends on the ElevenLabs agent.
- Site geo-points from ClinicalTrials.gov are city-level; distances are approximate.
- Appointment slots are demo data. The "confirmation text" is not sent.
- Regulatory status (MHRA software as a medical device) has not been assessed. Health data is UK GDPR special-category data: explicit consent, minimisation, deletion on request are implemented for the demo; a DPIA is not.

## Privacy

Profiles are pseudonymised; phone numbers live in a separate table and only for people who agreed to future contact. "Remove me" deletes the profile, answers, scores, referrals and number. After the event: `python scripts/purge.py --yes`.

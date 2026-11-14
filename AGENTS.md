# TrialReady — Build Spec (AI Cup 2026, Track D: Science & Engineering)

> Put this file in the repo root as `AGENTS.md` (Codex reads it automatically) or `SPEC.md`.
> Build order, acceptance tests and "verify first" items are at the end. Read §24 before writing code.

---

## 0. One-liner

**TrialReady is a voice-first, multilingual network that pre-screens people for UK clinical trials in a 5-minute phone/web conversation, re-checks every registered person against every new trial each night, and phones them the moment they look eligible, handing a research nurse a ready-made, evidence-backed referral.**

- Voice: **ElevenLabs Agents** (inbound intake agent + outbound recall agent over Twilio).
- Decisions: **TypeSafe Jev** (typed, calibrated, sub-second decisions on every criterion, every turn).
- Base: **NIH TrialGPT** (`github.com/ncbi-nlp/TrialGPT`): retrieval/matching method, prompts, public benchmark data.
- Data: **ClinicalTrials.gov API v2** (UK recruiting trials), ONS Census 2021 (equity view), TrialGPT public cohorts + expert criterion annotations (benchmark).
- Demo focus: **Type 2 diabetes**, UK recruiting interventional trials.

---

## 1. Hackathon constraints (design to these)

- Build only after track release (Fri 13 Nov 19:00). Submit Sat 14 Nov **15:30** on Commfy. Shortlist judging 16:00 (2-min pitch + demo, 1-min Q&A). Final 17:15 (5 min + 5 min Q&A).
- Data rule: **only public, synthetic or consented data.** No NHS/patient records. Demo users = synthetic personas + consenting attendees.
- Disclose all reused libraries/templates/datasets in the submission and README.
- Judging (shortlist): Pain & Impact 40 · Build & Live Demo 30 · AI Value & Integration 30.
  Judging (final): Pain 30 · Build 25 · AI Value 20 · Adoption & Feasibility 15 · Pitch 10.
- Side prize: **Best with ElevenLabs** (no Top 8 needed). Voice must be the product, not a decoration.

### How the build maps to scores
| Criterion | What earns it in this build |
|---|---|
| Pain & Impact | Evidence slide (§2), equity view vs Census, named user (research nurse + patient) |
| Build & Live Demo | Live web call → narrowing eligibility meter → "new trial opens" → attendee phones ring → referral appears on nurse dashboard |
| AI Value | Jev makes ~hundreds of calibrated criterion decisions per turn; adaptive next-question; calibrated hand-off thresholds; benchmark vs TrialGPT |
| Adoption | Positioned as voice/proactive layer on NIHR "Be Part of Research"; per-referral pricing; cost-per-screen from benchmark |
| Pitch | 3-minute script in §21 |

---

## 2. Problem & evidence (for README, slides, submission)

Use these exactly; caveats included on purpose (academic judges will probe).

- Willing but never asked: NIHR-part-funded survey — 47% likely to take part; trial participation discussed in only 5% of consultations in the past 12 months; only 10% have ever taken part.
  Source: UHS / Alterline "Engaging for increased research participation" — https://www.uhs.nhs.uk/Media/Southampton-Clinical-Research/Marketresearch/Engaging-for-increased-research-participation-key-findings-v2.pdf
- National registry under target and email-only: Be Part of Research had ~708,000 sign-ups (Apr 2026) vs a 1.5M target by Apr 2026; matching by chosen conditions/demographics, contact by email.
  Sources: https://www.bristolbrc.nihr.ac.uk/news/the-benefits-of-using-nihrs-be-part-of-research-service/ · https://research.hscni.net/node/1841 · https://www.uclh.nhs.uk/news/uclh-supports-national-campaign-calling-more-research-participation
- NIHR wants under-represented groups in line with ONS Census: https://rdforum.nhs.uk/news/entry/185320
- Recruitment failure: UK industry-trial participants fell 25% (2022/23→2024/25); <30% of industry trials recruited on time. **Caveat: ABPI = pharma trade body.**
  https://www.abpi.org.uk/publications/uk-industry-clinical-trials-translating-actions-intoimpact · https://www.clinicaltrialsarena.com/newsletters/uk-patients-being-denied-access-to-cutting-edge-clinical-research
- Independent check: only 56% of NIHR HTA RCTs hit their final recruitment target. https://pmc.ncbi.nlm.nih.gov/articles/PMC5372123
- Why representation matters: clopidogrel may be ineffective for British South Asians, who were not represented in the trials. https://pmc.ncbi.nlm.nih.gov/articles/PMC11956285
- Method base: TrialGPT — criterion-level accuracy 87.3% (close to experts), −42.6% screening time in a user study; evaluated on synthetic patients. https://doi.org/10.1038/s41467-024-53081-z
- Weakness we fix: LLM trial matchers depend on proprietary API models (cost, reproducibility, privacy). https://arxiv.org/pdf/2505.08508
- Jev: typed answers with calibrated probabilities, 70–500 ms, $0.042 / 1M input tokens. **Caveat: vendor-reported, not independently replicated — our benchmark is the independent check.** https://innfactory.ai/en/ai-models/typesafe-jev/

---

## 3. Product: three loops + two proof modules

1. **Intake loop (inbound, web or phone).** Consent → adaptive questions (max ~8) → Jev scores every criterion of every candidate trial after each answer → agent asks the most informative next question → ends with top 3 trials in plain language + "a research nurse confirms eligibility". Person is saved to the **registry** (if they consented to future contact).
2. **Rematch + Recall loop (proactive).** When a trial is added/updated (nightly job or "Simulate new trial" button), Jev re-scores all registry profiles → people above threshold get an **outbound ElevenLabs call** → agent re-confirms 1–2 things that may have changed → offers a slot at the site → creates a referral.
3. **Site loop.** Research-nurse dashboard: referral queue with criterion table (met / not met / unknown, evidence quote, confidence), transcript, booked slot; nurse records outcome (accepted / needs info / ineligible). Outcomes are stored for future recalibration.
4. **Benchmark module.** Jev vs TrialGPT on public expert criterion annotations: accuracy, macro-F1, calibration (ECE), latency, cost.
5. **Equity module.** Registry composition (age band, self-reported ethnicity, language) vs ONS Census 2021 for a chosen London borough → "who is missing".

---

## 4. Architecture

```
            ┌──────────────────────── Web (Next.js) ─────────────────────────┐
            │ /call  /registry  /site  /admin  /benchmark  /equity  (SSE)    │
            └───────────────┬───────────────────────────────▲───────────────┘
                            │ REST                           │ SSE events
 Patient ── web call ──► ElevenLabs Intake Agent ──server tools──► FastAPI ──► SQLite
 Patient ◄── phone ───── ElevenLabs Recall Agent ◄─ outbound API ── FastAPI
                                    (Twilio number imported in ElevenLabs)
                                                                     │
                     ┌───────────────────────────────────────────────┼─────────────┐
                     ▼                                               ▼             ▼
            TypeSafe Jev (decisions)                    ClinicalTrials.gov v2   Offline LLM
            fallback: OpenRouter / AIML API              (ingest, cached JSON)  (atomize criteria,
                                                                                lay summaries; cached)
```

### Stack
| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, httpx (async), pydantic v2, SQLModel + SQLite, APScheduler, tenacity | One process. SQLite file is fine for the demo. |
| Frontend | Next.js (App Router) + TypeScript, Tailwind, shadcn/ui, Recharts, EventSource (SSE) | "Stage mode": large fonts for projector. |
| Voice | ElevenLabs Agents: Intake agent + Recall agent; web SDK (`@elevenlabs/react`, verify current package name) | Server (webhook) tools call FastAPI. Post-call webhook for transcripts. |
| Telephony | Twilio number imported into ElevenLabs (native integration); outbound via `POST /v1/convai/twilio/outbound-call` | Trial Twilio accounts can only call verified numbers → verify team/volunteer phones in the morning. |
| Decision model | TypeSafe Jev (`jev-latest`) | Adapter with 3 backends: TypeSafe native → OpenRouter → AIML API; plus `MockJev` for offline dev. |
| Offline generative LLM | Any (Anthropic/OpenAI) via env var | Only for preprocessing; outputs committed to `data/processed/` so the live demo never depends on it. |
| Base repo | `ncbi-nlp/TrialGPT` | Reuse prompts, label set, datasets; not its architecture. |
| Public tunnel | `cloudflared tunnel --url http://localhost:8000` (backup: Railway/Render deploy) | ElevenLabs tools/webhooks need public HTTPS. |
| Frontend hosting | Local `next dev` on presenter laptop (backup: Vercel) | |

---

## 5. Repo structure

```
trialready/
  AGENTS.md                      # this spec
  README.md                      # setup, reused components, limitations, sources
  .env.example
  apps/web/                      # Next.js
    app/(stage)/call/page.tsx
    app/registry/page.tsx
    app/site/page.tsx
    app/admin/page.tsx
    app/benchmark/page.tsx
    app/equity/page.tsx
    components/{EligibilityMeter,TrialCard,CriteriaTable,Transcript,ReferralCard,CallMonitor,EquityChart,CalibrationChart}.tsx
    lib/{api.ts,sse.ts}
  services/api/
    main.py                      # FastAPI app, routers
    config.py                    # env + thresholds
    db.py, models.py             # SQLModel tables (§6)
    jev/
      client.py                  # JevClient interface + TypeSafe/OpenRouter/AIML/Mock backends
      questions.py               # builders for criterion/topic/safety questions
    trials/
      ingest_ctgov.py            # CT.gov v2 fetch + normalize
      atomize.py                 # offline LLM: criteria → atomic criteria (+topic tags, lay text)
      lay_summary.py             # offline LLM: plain-English trial summaries
      hard_filters.py            # age/sex/status/distance
    matching/
      engine.py                  # score trials for a profile (§8)
      adaptive.py                # next-question selection (§8.4)
      thresholds.py
    voice/
      tools_intake.py            # ElevenLabs server-tool endpoints (intake)
      tools_recall.py            # ElevenLabs server-tool endpoints (recall)
      outbound.py                # ElevenLabs Twilio outbound-call client
      webhooks.py                # post-call webhook (verify signature)
      prompts/{intake_system.md,recall_system.md}
    registry/
      rematch.py                 # nightly + on-demand rematch
    site/
      referrals.py, slots.py
    equity/
      census.py                  # ONS Census 2021 tables (cached CSV)
    benchmark/
      load_trialgpt_annotations.py
      run_jev_benchmark.py
      report.py                  # metrics JSON + charts data
    events.py                    # in-process pub/sub → SSE
    tests/
  data/
    raw/                         # downloads (gitignored if large)
    processed/trials_t2d_uk.json # committed: trials + atomic criteria + topics + lay summaries
    personas/*.json              # 10 synthetic personas
    census/                      # ONS extracts
    benchmark/results.json
  scripts/
    seed_demo.py                 # sites, slots, personas, registry
    simulate_new_trial.py
```

---

## 6. Data model (SQLModel)

- `Trial(id NCT, title, lay_title, lay_summary, phase, status, conditions[], min_age, max_age, sex, sites[] (name, city, lat, lon), last_update, source='ctgov'|'demo')`
- `Criterion(id, trial_id, kind='inclusion'|'exclusion', text, lay_text, topic, hard_filter bool)`
- `Session(id, channel='web'|'phone_in'|'phone_out', language, consent_screening bool, consent_contact bool, consent_ts, status, created_at, eleven_conversation_id)`
- `Profile(id, session_id, registry_member bool, first_name?, phone_e164? (store separately, demo only), postcode_district, age_band, sex, facts JSON {topic: {band, answer_text, ts}}, transcript_snippets JSON, ethnicity_self? , language)`
- `Answer(id, session_id, topic, question_text, answer_text, extracted JSON, ts)`
- `CriterionScore(profile_id, criterion_id, p_met, p_not_met, p_unknown, model, ts)` — latest per pair
- `TrialScore(profile_id, trial_id, p_opt, p_pes, uncertainty, knocked_out bool, ts)`
- `Referral(id, profile_id, trial_id, source='intake'|'recall', status='new'|'booked'|'accepted'|'needs_info'|'ineligible', slot_id?, evidence JSON, created_at)`
- `Slot(id, site_name, start_ts, capacity, booked)`
- `CallLog(id, direction, profile_id, trial_id?, eleven_conversation_id, status, started_at, ended_at, summary)`
- `Outcome(referral_id, nurse_decision, reason, ts)`
- `BenchmarkRun(id, model, n_pairs, metrics JSON, created_at)`

Privacy: phone numbers only for consenting attendees, deleted after the event (`scripts/purge.py`). No real medical records ever.

---

## 7. Trial data pipeline

### 7.1 Ingest (ClinicalTrials.gov API v2)
- `GET https://clinicaltrials.gov/api/v2/studies` with `query.cond="type 2 diabetes"`, `query.locn="United Kingdom"`, `filter.overallStatus=RECRUITING` (add `NOT_YET_RECRUITING` if < 40 trials), `pageSize=100`, follow `nextPageToken`.
- Keep interventional studies; extract `protocolSection.identificationModule`, `statusModule`, `eligibilityModule` (`eligibilityCriteria`, `minimumAge`, `maximumAge`, `sex`, `healthyVolunteers`), `contactsLocationsModule.locations` (keep UK sites with geoPoint), `conditionsModule`, `designModule.phases`.
- If fewer than ~40 UK T2D trials: widen to `diabetes OR obesity OR chronic kidney disease` and tag condition. Log the count.

### 7.2 Atomize criteria (offline LLM, cached)
For each trial: split `eligibilityCriteria` into inclusion/exclusion atomic criteria (one testable condition each). For each atomic criterion output: `text` (faithful), `lay_text` (plain English, ≤ 20 words), `topic` (one of §8.3 topics or `other`), `hard_filter` (true if age/sex only).
Rules: never invent criteria; keep numbers/units; criteria that require labs the caller cannot know (e.g., exact eGFR) still get a topic and will often end as `unknown` → that is fine and honest.
Validate JSON with pydantic; retry on schema failure; commit result to `data/processed/trials_t2d_uk.json`.

### 7.3 Lay summaries (offline LLM, cached)
Per trial: `lay_title` (≤ 10 words) and `lay_summary` (≤ 60 words, reading age ~11, what the study tests, what taking part involves, where). No efficacy claims.

### 7.4 Demo trials
`scripts/simulate_new_trial.py` inserts a realistic **demo** trial (clearly labelled `source='demo'`, fictional ID `DEMO-001`) whose criteria match 2–3 seeded/attendee profiles. Used for the "new trial opens → phones ring" moment. The UI shows the label "Demo trial (fictional)".

---

## 8. Matching engine

### 8.1 Hard filters (deterministic, before Jev)
Age within [min,max] (if age known), sex compatible, status recruiting, nearest UK site within `MAX_DISTANCE_KM` of postcode district centroid (use a small cached outward-code → lat/lon table; if unavailable, skip distance). Keep top `K_CANDIDATES=30` by a cheap prior (condition match + distance).

### 8.2 Jev adapter (`jev/client.py`)
Request shape (confirmed for the AIML API gateway; **verify identical field names on TypeSafe native docs**):
```json
{
  "model": "jev-latest",
  "state": { "profile": {...}, "recent_answers": ["..."] },
  "questions": {
    "NCT01234567__inc_3": {
      "type": "choice",
      "instructions": "Does the patient satisfy this clinical-trial INCLUSION criterion: 'HbA1c between 7.0% and 10.5%'?",
      "criteria": {
        "met": "The information clearly shows the patient satisfies the criterion.",
        "not_met": "The information clearly shows the patient does NOT satisfy the criterion.",
        "unknown": "The information does not say enough to decide."
      }
    }
  }
}
```
Response: `answers[key] = {type:"choice", choice, confidence, probabilities:{met, not_met, unknown}}`; `noul → {type:"noul", noul:0..1}`; plus `usage`.
- Endpoints: TypeSafe native `POST https://api.typesafe.ai/v1/systemone` (Bearer `TYPESAFE_API_KEY`); OpenRouter `POST https://openrouter.ai/api/alpha/decisions` (model `typesafe/jev-1.13`); AIML API `POST https://api.aimlapi.com/v1/decisions` (model `typesafe/jev`).
- Batch questions: `JEV_BATCH_SIZE=50` keys per request (configurable; no documented max on AIML schema), `JEV_CONCURRENCY=8`, async with tenacity retries (2 retries, 300 ms backoff), per-turn timeout 2.5 s.
- Cache by `(hash(state), question_key)`.
- `MockJev` returns deterministic pseudo-probabilities from keyword rules — used when `MOCK_JEV=true` so UI/voice can be built without keys.
- Log latency and usage for every call (feeds benchmark & cost slide).

### 8.3 Intake topics (T2D)
`age_sex`, `diagnosis_duration`, `diabetes_meds` (metformin, sulfonylurea, DPP-4, SGLT2, GLP-1, insulin), `hba1c` (bands), `weight_bmi` (bands), `kidney`, `cardio_recent` (heart attack/stroke/heart-failure admission in last 6–12 months), `cancer_recent`, `pregnancy`, `other_trial_recent`, `location_travel`, `contact_preference`.
Each topic has: `question_en` (plain, one thing at a time), optional `extract` Jev choice question producing a band (e.g. HbA1c: `lt_7`, `7_8`, `8_10`, `gt_10`, `unknown` with mmol/mol equivalents in descriptions).

### 8.4 Scoring and next question
For trial `t`, inclusion set `I`, exclusion set `E`, per-criterion probs from Jev:
- `p_ok(c) = P(met)` for `c ∈ I`; `p_ok(c) = P(not_met)` for `c ∈ E`; `p_bad(c)` = the opposite clear answer; `p_unk(c) = P(unknown)`.
- **Pessimistic** (confirmed) `P_pes(t) = Π p_ok(c)`; **Optimistic** (still possible) `P_opt(t) = Π (p_ok(c) + p_unk(c))`; `uncertainty = P_opt − P_pes`.
- **Knock-out:** any `p_bad(c) ≥ KNOCKOUT=0.8` → trial excluded (show which criterion).
- Rank by `P_opt`, then `P_pes`.
- **Next topic** = argmax over unasked topics of `Σ_{t ∈ top-10 by P_opt} P_opt(t) · Σ_{c ∈ t, topic(c)=T} p_unk(c)`. Tie-break by fixed topic order (§8.3).
- **Stop** when any: 8 topics asked · top-3 trials have `uncertainty < 0.15` · best topic value < 0.05 · caller wants to stop (Jev noul) · safety flag.

### 8.5 Hand-off thresholds (tune on benchmark, keep in `thresholds.py`)
- `P_pes ≥ 0.6` and no knock-out → **Refer** (create referral, offer slot).
- `P_opt ≥ 0.6` and `P_pes < 0.6` → **Human review** ("a research nurse may call to check X").
- else → **No match now** (stay in registry for future trials).

### 8.6 Per-utterance guard questions (Jev noul, every turn, one call)
`urgent_symptom` ("describes chest pain, stroke signs, severe hypo/hyper symptoms, suicidal thoughts or another emergency now"), `wants_to_stop`, `withdraw_consent`, `answered_the_question`, `asks_medical_advice`.
- `urgent_symptom ≥ 0.5` → tool returns `safety_message` ("This sounds urgent. Please hang up and call 999, or NHS 111 if it's not an emergency.") and ends screening.
- `withdraw_consent ≥ 0.6` → delete profile, confirm verbally.
- `answered_the_question < 0.4` → re-ask once, rephrased.
- `asks_medical_advice ≥ 0.6` → "I can't give medical advice; your GP or the research team can help."

---

## 9. ElevenLabs agents

### 9.1 Intake agent (inbound web + phone)
- First message: "Hi, I'm the TrialReady assistant. I can check, in about five minutes, whether there are diabetes research studies near you that you might be able to join. I'm an AI, this is not medical advice, and a research nurse always makes the final decision. Is it OK if I ask you a few questions and store your answers for this check?"
- Consent captured via tool `record_consent(session_id, screening: bool, future_contact: bool, language)`. If no → polite end.
- Loop: ask the question returned by the backend; after each caller answer call `record_answer(session_id, topic, answer_text)`; speak `next_question`; if `safety_message` present, say it and end; if `done`, call `finish_screening(session_id)` and read `spoken_summary`.
- End: ask for future-contact consent ("Can we call you if a new study you might fit opens? You can say 'remove me' any time.") → `record_consent`.
- Languages: English + one more for the demo (pick a language confirmed in the ElevenLabs language list, e.g. Hindi, Polish or Russian; **verify Urdu support before promising it**). Agent replies in caller's language; tool payloads in English.
- System prompt rules: one question at a time; never diagnose or advise; never claim eligibility — say "you may be a good fit, a research nurse will confirm"; keep turns short; spell out numbers the caller gives back to confirm (e.g. "HbA1c fifty-eight, is that right?").
- Session id: web → pass `session_id` as a dynamic variable at conversation start (backend creates it). Phone inbound → first tool call `start_session(caller_language)` returns `session_id` (verify system dynamic variables for caller ID/conversation ID in docs).

### 9.2 Recall agent (outbound)
- Started by backend via `POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call` with `agent_id`, `agent_phone_number_id`, `to_number`, `conversation_initiation_client_data.dynamic_variables = {first_name, referral_id, trial_lay_title, site_name, site_area, recheck_topic_1, recheck_question_1, recheck_topic_2?, recheck_question_2?}`.
- Script: identify as AI calling on behalf of TrialReady about the study they agreed to hear about → confirm it's a good time → ask the 1–2 recheck questions (tool `recall_record_answer(referral_id, topic, answer_text)` → returns `still_eligible`, `next`) → if eligible, `get_slots(referral_id)` → read 2 options → `book_slot(referral_id, slot_id)` → confirm by voice → mention a confirmation text/email would follow (mock).
- If voicemail/no answer → log, retry later (not in demo).

### 9.3 Tool contract (all tools = ElevenLabs server/webhook tools → FastAPI)
- Public base URL from tunnel; shared secret header `X-Tool-Secret` checked by FastAPI.
- Every tool response ≤ ~600 chars of JSON, target latency < 1.5 s (p95 < 2.5 s). Return a fallback question if Jev times out.
- Post-call webhook `POST /api/webhooks/elevenlabs/post-call`: store transcript + summary into `CallLog`; verify the ElevenLabs signature header (check docs for header name/HMAC scheme).

---

## 10. Registry, rematch, outbound

- Registry = profiles with `consent_contact=true`.
- `rematch(trial_ids)`: for each registry profile × each new/updated trial → hard filters → Jev scoring (batched; this is the "millions of decisions/night" story, only economical with Jev's pricing) → if `Refer` threshold → create `Referral(source='recall', status='new')` and enqueue outbound call (max `OUTBOUND_CONCURRENCY=5`).
- Triggers: APScheduler nightly 02:00 (ingest CT.gov with `last_update` > last run) + `POST /api/registry/rematch` + admin button "Simulate new trial opening" (inserts `DEMO-001` then rematches).
- Call monitor UI shows each outbound call: dialing → in progress → booked / not eligible / no answer.

---

## 11. Site (research nurse) dashboard

- Queue of referrals (new / booked / human-review) with: trial, person (first name or pseudonym), P_pes / P_opt bars, criterion table (criterion lay text · met/not met/unknown · confidence · evidence quote from answers), booked slot, transcript link.
- Actions: Accept · Needs info · Ineligible (+ reason) → `Outcome`.
- "Outcome loop" panel: count of outcomes; text: "outcomes recalibrate thresholds (isotonic) per site" — for the hackathon show calibration from the benchmark (§13) and log outcomes.

---

## 12. Equity view

- ONS Census 2021 ethnicity (TS021) and age for one London borough (e.g. Camden) and London overall — download CSV from Nomis once, cache in `data/census/`.
- Registry composition from optional self-reported answers (asked last, skippable) + language used.
- Chart: registry % vs Census % per group; highlight under-represented groups; note "demo sample, not representative".

---

## 13. Benchmark (the judge-winning slide)

- Data: Hugging Face `ncbi/TrialGPT-Criterion-Annotations` (inspect columns first: expect patient note, criterion text, criterion type, expert label). Patient notes are synthetic/public.
- Map expert labels to our 3-way scheme (met / not_met / unknown); keep the original TrialGPT label set alongside if it has more classes (e.g. "not applicable") and report both mappings.
- Sample N = 500 stratified by label (run full set if time/credits allow).
- Run Jev (choice question per pair; state = patient note). Record prediction, probabilities, latency, tokens.
- Metrics: accuracy (overall / inclusion / exclusion), macro-F1, confusion matrix, **ECE (10 bins) + reliability diagram**, latency p50/p95, cost per 1,000 criteria (tokens × published price; label "vendor price").
- Comparison row: "TrialGPT-Matching (GPT-4), **reported** 87.3% on 1,015 pairs (Nature Comms 2024) — not re-run". If an LLM key is available, also run a generative-LLM baseline on the **same** 200 pairs for an apples-to-apples row.
- Use the reliability results to set thresholds (§8.5): choose `P_pes` cut-off giving ≥ 0.9 precision on "met".
- Output `data/benchmark/results.json`; `/benchmark` page renders table + reliability diagram + latency histogram. **Report honestly even if Jev underperforms** — then the story is "fast, calibrated triage; humans confirm".

---

## 14. Frontend pages

- `/call` (**main stage screen**): Start-call button (ElevenLabs web conversation), live transcript (left), **Eligibility Meter** (centre: "Trials still possible: 37 → 12 → 4", top-10 bars with P_opt/P_pes), selected trial **Criteria Table** (right), current topic chip, safety banner, end card with top-3 lay summaries and consent status.
- `/admin`: ingest status (trial count, last update), "Simulate new trial opening" button, outbound **Call Monitor**, Jev live stats (calls, p50 latency, decisions today, cost).
- `/site`: referral queue + detail drawer + outcome buttons.
- `/registry`: members count, consent stats, languages, channels.
- `/equity`: Census comparison chart.
- `/benchmark`: results.
- Global: "Pre-screening only · not medical advice · synthetic & consented data only" footer. Stage mode toggle (bigger fonts, dark).
- Realtime: SSE `GET /api/events?session_id=` emitting `turn_scored`, `next_question`, `safety`, `referral_created`, `call_status`, `rematch_progress`.

---

## 15. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions` | create web session → `{session_id}` |
| POST | `/api/tools/start_session` | phone inbound session |
| POST | `/api/tools/record_consent` | consent flags |
| POST | `/api/tools/record_answer` | store answer → extract band → guard nouls → rescore → `{next_question, topic, done, safety_message?, top_trials:[{lay_title,p_opt}]}` |
| POST | `/api/tools/finish_screening` | `{spoken_summary, outcome}`; creates intake referral if Refer |
| POST | `/api/tools/recall_record_answer` | recall recheck |
| POST | `/api/tools/get_slots` / `/api/tools/book_slot` | site slots |
| POST | `/api/webhooks/elevenlabs/post-call` | transcripts |
| GET | `/api/events` | SSE |
| GET/POST | `/api/trials`, `/api/trials/ingest` | trials |
| POST | `/api/registry/rematch`, `/api/demo/new-trial` | rematch / demo |
| POST | `/api/outbound/call` | manual outbound |
| GET | `/api/site/referrals`; POST `/api/site/referrals/{id}/decision` | nurse |
| GET | `/api/equity`, `/api/benchmark`, `/api/stats` | dashboards |

---

## 16. Config (`.env.example`)

```
TYPESAFE_API_KEY=
TYPESAFE_BASE_URL=https://api.typesafe.ai/v1/systemone
JEV_MODEL=jev-latest
JEV_PROVIDER=typesafe            # typesafe | openrouter | aimlapi | mock
OPENROUTER_API_KEY=
AIMLAPI_API_KEY=
MOCK_JEV=false
JEV_BATCH_SIZE=50
JEV_CONCURRENCY=8
ELEVENLABS_API_KEY=
ELEVENLABS_INTAKE_AGENT_ID=
ELEVENLABS_RECALL_AGENT_ID=
ELEVENLABS_PHONE_NUMBER_ID=      # agent_phone_number_id of imported Twilio number
ELEVENLABS_WEBHOOK_SECRET=
TOOL_SHARED_SECRET=
OFFLINE_LLM_PROVIDER=anthropic   # or openai; preprocessing only
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
PUBLIC_API_BASE_URL=             # cloudflared URL
DATABASE_URL=sqlite:///./trialready.db
MAX_DISTANCE_KM=60
K_CANDIDATES=30
DEMO_MODE=true
```

---

## 17. Resilience / fallbacks (demo must not die)

1. Jev native fails → OpenRouter → AIML API → `MockJev` (UI shows "fallback mode" badge).
2. Tool timeout → return next topic by fixed order; rescore asynchronously.
3. Phone/Twilio fails → web call for intake; recall demo uses **pre-verified team phones**; last resort: web "incoming call" simulation.
4. CT.gov down → committed `data/processed/trials_t2d_uk.json`.
5. Wi-Fi fails → hotspot; and a **recorded 90-second backup video** of the full flow (record by 14:30).
6. Seed script recreates a clean demo state in < 10 s (`python scripts/seed_demo.py --reset`).

---

## 18. Privacy, safety, ethics (say it before judges ask)

- Hackathon: synthetic personas + explicitly consenting attendees only; data purged after the event.
- Positioning: **pre-screening only**; no diagnosis, no treatment advice; research nurse/PI confirms eligibility. Regulatory status (UK MHRA software-as-medical-device) to be assessed before any real deployment — design keeps the tool to information + referral.
- Health data = UK GDPR special category → explicit consent, data minimisation, pseudonymised profiles, phone numbers stored separately, deletion on request ("remove me").
- AI disclosure at the start of every call; call recording only with consent.
- Calibrated thresholds + human review band to limit false referrals; emergency language triggers 999/111 message.

---

## 19. Synthetic personas (`data/personas/*.json`)

10 personas, diverse: e.g. 58 y/o British Pakistani man, T2D 6 yrs, metformin + gliclazide, HbA1c 64 mmol/mol, BMI 31, prefers a non-English language; 67 y/o Black Caribbean woman on insulin, CKD stage 3; 45 y/o Polish woman newly diagnosed, metformin only; 72 y/o white British man with MI 4 months ago (should be knocked out of some trials); pregnant 34 y/o (pregnancy exclusions); etc. Each has `facts` + a short "how they talk" note for role-play testing and a script of answers for automated tests.

---

## 20. Work plan (3 people + Codex)

| When | Codex / Person A (backend & Jev) | Person B (voice & telephony) | Person C (frontend, pitch, evidence) |
|---|---|---|---|
| Fri 23:30–03:00 (Codex overnight) | Repo scaffold, models, CT.gov ingest, atomize (cached), Jev adapter + MockJev, matching engine, benchmark script | — | — |
| Sat 08:00–10:00 | Verify Jev keys/schema, run benchmark (N=500), tune thresholds | Create Intake agent + tools, tunnel, web call working end-to-end | `/call` stage screen with SSE meter |
| 10:00–12:30 | Rematch + referrals + slots; safety guards | Twilio import, verify phones, Recall agent, outbound flow | `/site`, `/admin` call monitor |
| 12:30–14:00 | Equity data, stats endpoint, bug-fix | Second language, edge cases (no consent, emergency, withdraw) | `/benchmark`, `/equity`, pitch slides (≤ 6) |
| 14:00–15:00 | Freeze. Seed/reset script. | Full rehearsal ×3 | Record backup video; fill Commfy form |
| 15:30 | **Submit** | | |

Rule: from 14:00 no new features — only fixes.

---

## 21. Demo & pitch scripts

**Shortlist (2 min demo + pitch):**
1. (20 s) "Half of people would join a trial; only one in ten ever has; doctors raise it in 5% of appointments. The national registry emails people. We phone them."
2. (60 s) Judge talks to the intake agent on the stage laptop: 4–5 answers. Meter narrows live; criteria table fills with met/unknown + confidence. Ends: "You may be a good fit for 2 studies near you; a research nurse will confirm."
3. (25 s) Click "Simulate new trial opening" → 2–3 team/attendee phones ring → one call answered on speaker → slot booked → referral pops on nurse dashboard.
4. (15 s) Benchmark slide: accuracy / calibration / latency / cost vs TrialGPT reported.

**Final (5 min):** add equity view (who's missing vs Census), business model (per-referral fee paid by sponsors/CROs; NHS sites free; partner channel: patient charities + layer on Be Part of Research), cost per screen from benchmark, path to pilot (one NIHR Research Delivery Network region + one diabetes charity), risks & safeguards (§18).

**Q&A prep:** Why not just Be Part of Research? (we're its voice + proactive layer) · What if it's wrong? (calibrated thresholds, human review, nurse confirms; show reliability diagram) · Is this a medical device? (pre-screening info + referral; formal MHRA assessment before deployment) · Why Jev not GPT? (per-criterion decisions × trials × registry × nightly — latency and cost; calibrated probabilities drive hand-off) · Will people trust an AI call? (calls only after explicit consent, on behalf of a named charity/site, AI disclosed, "remove me" anytime).

---

## 22. Commfy submission draft

- **Project name + one-liner:** TrialReady — a voice-first network that finds people the UK clinical trials they may qualify for, and phones them the moment a new one opens.
- **Problem + target users:** Trials under-recruit while willing people are never asked; registry matching is coarse and email-only, leaving older, offline and minority-language communities out. Users: patients/public (starting with type 2 diabetes) and NHS research nurses/trial sites.
- **How it works + AI's role:** ElevenLabs voice agents run a 5-minute multilingual pre-screen and later call people back; TypeSafe Jev makes hundreds of calibrated eligibility decisions per conversational turn, chooses the next most informative question, and decides who is referred vs reviewed; nightly re-matching of every registry member against new trials; nurse dashboard with evidence per criterion.
- **Tools/tech:** ElevenLabs Agents + Twilio, TypeSafe Jev, FastAPI, Next.js, ClinicalTrials.gov API, NIH TrialGPT (method, prompts, benchmark data), ONS Census 2021. Reused components disclosed in README.
- **Value + difference:** Proactive (it calls you), voice and multilingual (reaches people email misses), calibrated hand-off (nurses get fewer, better referrals), measured: benchmarked against TrialGPT on public expert annotations.
- **Demo link:** (repo + video + live URL)

---

## 23. Acceptance tests (Definition of Done)

1. `pytest`: atomizer output schema; scoring math (P_pes ≤ P_opt; knock-out); adaptive picks highest-value topic on a fixture; guard nouls route correctly (mocked).
2. Persona replay: 3 personas through `/api/tools/record_answer` end-to-end with MockJev and with real Jev; each finishes in ≤ 8 questions; MI-4-months persona is knocked out of cardio-exclusion trials.
3. Live web call: full intake < 5 min; meter updates within 2 s of each answer.
4. Emergency phrase triggers safety message and ends screening; "remove me" deletes profile.
5. "Simulate new trial" → ≥ 2 outbound calls placed within 30 s; one completes booking; referral visible on `/site`.
6. Benchmark run produces `results.json` with accuracy, macro-F1, ECE, p50/p95 latency, cost; `/benchmark` renders.
7. Reset script restores demo state; backup video recorded.

---

## 24. Verify first (before/while coding)

- [ ] TypeSafe key from the event code works; exact native field names (`instructions`, `criteria`, response keys) match §8.2; max questions per request; rate limits.
- [ ] OpenRouter/AIML fallback key works.
- [ ] Count of RECRUITING interventional T2D trials with UK sites (decide widening per §7.1).
- [ ] TrialGPT HF annotation columns + label set; download works.
- [ ] ElevenLabs: server-tool config, dynamic variables, system variables for conversation/caller ID, post-call webhook signature scheme, current web SDK package name, supported languages for the second demo language.
- [ ] Twilio: buy/import number into ElevenLabs; verify all demo phones; check trial-account limitations (consider small top-up).
- [ ] cloudflared tunnel stable on venue Wi-Fi; hotspot as backup.

## Sources
TrialGPT paper https://doi.org/10.1038/s41467-024-53081-z · repo https://github.com/ncbi-nlp/TrialGPT · Jev schema (AIML gateway) https://docs.aimlapi.com/api-references/decision-models/typesafe/jev.md · Jev overview https://innfactory.ai/en/ai-models/typesafe-jev/ · Jev on OpenRouter https://openrouter.ai/blog/insights/what-is-jev/ · ElevenLabs Twilio outbound https://elevenlabs.io/docs/eleven-agents/api-reference/twilio/outbound-call · ElevenLabs Twilio native integration https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration · plus §2 sources.

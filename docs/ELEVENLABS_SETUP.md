# ElevenLabs + Twilio setup (about 30 minutes)

Everything below is configuration in the ElevenLabs dashboard; the code is already written against it.
Dashboard labels change — where a label differs, look for the nearest equivalent. Items marked **verify** were not testable without an account.

## 0. Public URL
ElevenLabs must reach your FastAPI server over HTTPS:

    cloudflared tunnel --url http://localhost:8000

Put the URL in `.env` as `PUBLIC_API_BASE_URL` (reference only) and use it as `<BASE>` below. Set `TOOL_SHARED_SECRET` to a random string.

## 1. Intake agent
- Create agent **TrialReady Intake**. First message: the text in spec §9.1 (also `FIRST_MESSAGE` in `services/api/voice/tools_intake.py`).
- System prompt: `services/api/voice/prompts/intake_system.md`.
- Languages: English + one more that is in the ElevenLabs language list (**verify** your second language before promising it on stage).
- Dynamic variables: add `session_id` with default/placeholder value `none` (the web page overrides it; inbound phone calls use `none` and call `start_session`).
- Make the agent public (no auth) so the web page can start it with just the agent ID, or change `ElevenLabsCall.tsx` to use a signed URL.
- Server (webhook) tools — all `POST`, JSON body, header `X-Tool-Secret: <TOOL_SHARED_SECRET>`:

| Tool name | URL | Body parameters |
|---|---|---|
| `start_session` | `<BASE>/api/tools/start_session` | `caller_language` (string, LLM), `conversation_id` (dynamic variable `system__conversation_id`), `caller_id` (dynamic variable `system__caller_id`) |
| `record_consent` | `<BASE>/api/tools/record_consent` | `session_id` (string), `screening` (boolean), `future_contact` (boolean, optional), `language` (string, optional), `first_name` (string, optional), `phone` (string, optional) |
| `record_answer` | `<BASE>/api/tools/record_answer` | `session_id` (string), `topic` (string), `answer_text` (string) |
| `finish_screening` | `<BASE>/api/tools/finish_screening` | `session_id` (string) |
| `record_ethnicity` | `<BASE>/api/tools/record_ethnicity` | `session_id` (string), `answer_text` (string) |

Tool descriptions (paste into each tool):
- `start_session`: "Phone calls only. Creates the screening session. Call once at the start when session_id is 'none'."
- `record_consent`: "Records whether the caller agrees to screening and, later, to future contact. Returns the first question."
- `record_answer`: "Call after every caller answer. Returns the next question, or done=true, or a safety_message that must be read out."
- `finish_screening`: "Call when record_answer returns done=true. Returns spoken_summary to read out."
- `record_ethnicity`: "Optional final question for the equity view."

Every response is under ~600 characters. Give each tool a response timeout of at least 5 s.

Put the agent ID in `.env` as `ELEVENLABS_INTAKE_AGENT_ID` and restart the API; `/call` then offers "ElevenLabs voice agent".

## 2. Recall agent (outbound)
- Create agent **TrialReady Recall**. System prompt and first message: `services/api/voice/prompts/recall_system.md`.
- Dynamic variables (sent by the backend with every call): `first_name`, `referral_id`, `trial_lay_title`, `site_name`, `site_area`, `recheck_topic_1`, `recheck_question_1`, `recheck_topic_2`, `recheck_question_2`. Give each a placeholder so the dashboard test call works.
- Tools (same header):

| Tool name | URL | Body parameters |
|---|---|---|
| `recall_record_answer` | `<BASE>/api/tools/recall_record_answer` | `referral_id` (number), `topic` (string), `answer_text` (string) |
| `get_slots` | `<BASE>/api/tools/get_slots` | `referral_id` (number) |
| `book_slot` | `<BASE>/api/tools/book_slot` | `referral_id` (number), `slot_id` (number) |

Put the agent ID in `.env` as `ELEVENLABS_RECALL_AGENT_ID`.

## 3. Twilio number
- Buy a number in Twilio; in ElevenLabs → Phone numbers → import from Twilio (account SID + auth token). Assign the **Intake** agent for inbound calls.
- Copy the phone number's ID into `.env` as `ELEVENLABS_PHONE_NUMBER_ID` (this is `agent_phone_number_id` in the outbound API).
- **Twilio trial accounts only call verified numbers**: verify every demo phone in Twilio, or top the account up.
- Outbound calls are placed by the backend: `POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call` with `agent_id`, `agent_phone_number_id`, `to_number`, `conversation_initiation_client_data.dynamic_variables` (`services/api/voice/outbound.py`).

## 4. Post-call webhook
- ElevenLabs → Agents settings → Post-call webhook: `<BASE>/api/webhooks/elevenlabs/post-call`, type transcription, auth HMAC.
- Copy the generated secret into `.env` as `ELEVENLABS_WEBHOOK_SECRET`. The server verifies the `ElevenLabs-Signature` header (`t=<unix>,v0=<hex HMAC-SHA256 of "t.body">`, 30-minute tolerance).

## 5. Phones for the "phones ring" moment
1. A teammate does a real intake (web or phone) and says yes to future contact. On the web call the agent asks for the number; on a phone call the caller ID is used.
   Or: `/registry` → "+ add" attaches a consenting attendee's number to a registry profile.
2. `/admin` → "Simulate new trial opening" (leave "simulated calls only" unticked). Members with a number get a real call; everyone else gets a simulated call you can answer in the browser.
3. If ElevenLabs/Twilio returns an error, that call silently becomes a simulated one — the demo keeps going.

## Checklist before going on stage
- [ ] `curl <BASE>/api/health` returns `{"ok":true}` from outside your laptop
- [ ] One full web call with the ElevenLabs agent: meter moves within 2 s of each answer
- [ ] One inbound phone call shows up on `/call` in "Watch incoming phone calls" mode
- [ ] One outbound call rings a verified phone and books a slot; referral appears on `/site`
- [ ] `python scripts/seed_demo.py --reset` then repeat the stage button once
- [ ] After the event: `python scripts/purge.py --yes`

# WhatsApp through Twilio (about 10 minutes, sandbox)

People can do the whole pre-screen by WhatsApp text, and registry members who joined on WhatsApp are contacted about a new
trial by WhatsApp message instead of a phone call. It is the same script and the same backend as the voice agents: every
message is scored by Jev exactly like a spoken answer, and the safety, "remove me" and medical-advice guards apply.
Text only: Twilio WhatsApp voice calling is not used. Code: `services/api/voice/whatsapp.py`.

## 1. Twilio sandbox (no Meta approval needed)
1. Twilio Console → Messaging → Try it out → **Send a WhatsApp message**. Note the sandbox number (`+1 415 523 8886`) and the join phrase (`join <two-words>`).
2. Each demo phone sends that join phrase **from WhatsApp** to the sandbox number. Only joined phones can talk to the sandbox; re-join if it stops answering (sandbox membership expires after a few days).
3. Sandbox settings → **When a message comes in**: `<PUBLIC_API_BASE_URL>/api/whatsapp/inbound`, method **POST**. Save.

## 2. `.env`
```
TWILIO_ACCOUNT_SID=AC...                 # Console dashboard → Account Info
TWILIO_AUTH_TOKEN=...                    # needed to verify Twilio's signature on every incoming message
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
PUBLIC_API_BASE_URL=https://....trycloudflare.com   # must be exactly the URL Twilio calls (the signature covers it)
OUTBOUND_ALLOWLIST=+447...               # only these numbers get outbound WhatsApp messages
```
Restart the API. `/admin` shows "✓ WhatsApp (Twilio)".

## 3. Try it
- **Intake:** send any message (e.g. "hi") to the sandbox number → the consent question → answer the questions → "yes" to future contact.
  The number is stored only after that yes (separately from the pseudonymised profile) and is deleted by "remove me" and by `scripts/purge.py`.
  Send `RESTART` at any time to start a fresh check.
- **Recall:** `/admin` → "Simulate new trial opening" (leave "simulated calls only" unticked). A registry member who joined on WhatsApp and is on
  `OUTBOUND_ALLOWLIST` gets a WhatsApp message (💬 in the call monitor); their replies re-check, offer two slots and book one; the referral appears on `/site`.
  Anyone else gets a simulated call as before. If Twilio rejects the message, that person silently gets a simulated call instead.

## Limits
- **24-hour window:** WhatsApp only allows free-text business messages within 24 hours of the person's last message. For the demo, have each
  phone message the sandbox (or do an intake) on the day. Outside the window, create an approved template in Twilio Content
  (`{{1}}` = first name, `{{2}}` = study title) and set `TWILIO_WHATSAPP_CONTENT_SID`.
- **Your own number instead of the sandbox:** register a WhatsApp sender in Twilio (Meta business verification and display-name approval - not
  quick), then set `TWILIO_WHATSAPP_FROM=whatsapp:+<your number>` and the same inbound URL on that sender.
- **Cost:** Twilio and Meta charge per WhatsApp message/conversation; see Twilio's pricing page.
- **Privacy:** messages pass through Meta and Twilio. Fine with explicit consent for the demo; a real deployment needs a DPIA.

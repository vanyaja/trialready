"""Create or update the ElevenLabs side of TrialReady from .env (replaces the manual steps in docs/ELEVENLABS_SETUP.md).

    python scripts/setup_elevenlabs.py --check            # what is missing; calls nothing
    python scripts/setup_elevenlabs.py --dry-run          # print the payloads (secrets redacted); calls nothing
    python scripts/setup_elevenlabs.py                    # tools + Intake/Recall agents + post-call webhook
    python scripts/setup_elevenlabs.py --import-twilio    # also import an EXISTING Twilio number (never buys one)

Idempotent: tools/agents/secret are found by name and updated in place. Re-run after the tunnel URL changes
(the webhook URL cannot be edited via the API, so a new webhook + new signing secret is created then).
Writes IDs/secrets into .env only (ELEVENLABS_INTAKE_AGENT_ID, ELEVENLABS_RECALL_AGENT_ID, ELEVENLABS_WEBHOOK_ID,
ELEVENLABS_WEBHOOK_SECRET, ELEVENLABS_PHONE_NUMBER_ID). Never prints keys or secrets.
Places no calls and makes no purchases.

Request shapes were taken from https://api.elevenlabs.io/openapi.json (14 Nov 2026).
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from services.api.config import ROOT, get_settings  # noqa: E402

ENV = ROOT / ".env"
PROMPTS = ROOT / "services" / "api" / "voice" / "prompts"
TAG = "trialready"
SECRET_NAME = "trialready_tool_secret"
WEBHOOK_NAME = "TrialReady post-call"
TTS_MODEL = "eleven_flash_v2_5"  # multilingual conversational model: needed for a non-English language preset

INTAKE_FIRST = ("Hi, I'm the TrialReady assistant. I can check, in about five minutes, whether there are diabetes research studies near you "
                "that you might be able to join. I'm an AI, this is not medical advice, and a research nurse always makes the final decision. "
                "Is it OK if I ask you a few questions and store your answers for this check?")
# Second-language first messages. Machine-drafted translations: have a native speaker check before the demo.
SECOND_LANGUAGE_FIRST = {
    "pl": ("Dzień dobry, jestem asystentem TrialReady. W około pięć minut mogę sprawdzić, czy w pobliżu prowadzone są badania naukowe "
           "dotyczące cukrzycy, w których mógłby Pan lub mogłaby Pani wziąć udział. Jestem sztuczną inteligencją, to nie jest porada medyczna, "
           "a ostateczną decyzję zawsze podejmuje pielęgniarka badawcza. Czy mogę zadać kilka pytań i zapisać odpowiedzi na potrzeby tej rozmowy?"),
    "hi": ("नमस्ते, मैं TrialReady सहायक हूँ। मैं लगभग पाँच मिनट में देख सकता हूँ कि आपके पास डायबिटीज़ से जुड़े ऐसे शोध अध्ययन हैं या नहीं "
           "जिनमें आप शामिल हो सकते हैं। मैं एक AI हूँ, यह चिकित्सा सलाह नहीं है, और अंतिम फ़ैसला हमेशा एक रिसर्च नर्स करती है। "
           "क्या मैं आपसे कुछ सवाल पूछ सकता हूँ और इस जाँच के लिए आपके जवाब सहेज सकता हूँ?"),
}

S_ = lambda d: {"type": "string", "description": d}  # noqa: E731
B_ = lambda d: {"type": "boolean", "description": d}  # noqa: E731
I_ = lambda d: {"type": "integer", "description": d}  # noqa: E731
DV = lambda name: {"type": "string", "dynamic_variable": name}  # noqa: E731

TOOLS = {
    # name: (description, body properties, required, timeout_s)
    "start_session": ("Phone calls only. Creates the screening session. Call once at the start when session_id is 'none'.",
                      {"caller_language": S_("Language the caller is speaking, as an ISO code such as en or pl."),
                       "conversation_id": DV("system__conversation_id"), "caller_id": DV("system__caller_id")}, ["caller_language"], 10),
    "record_consent": ("Records whether the caller agrees to screening and, later, to future contact. Returns the first question.",
                       {"session_id": S_("The session_id for this call."), "screening": B_("True if the caller agreed to the screening questions."),
                        "future_contact": B_("True if the caller agreed to be phoned about future studies. Only at the end of the call."),
                        "language": S_("Language the caller is speaking, ISO code."), "first_name": S_("Caller's first name, only if offered."),
                        "phone": S_("Best phone number for future contact, only if they agreed to future contact.")}, ["session_id", "screening"], 10),
    "record_answer": ("Call after every caller answer. Returns the next question, or done=true, or a safety_message that must be read out.",
                      {"session_id": S_("The session_id for this call."), "topic": S_("The topic of the question you just asked (from the previous tool result)."),
                       "answer_text": S_("The caller's answer, translated faithfully into English, not summarised.")}, ["session_id", "answer_text"], 15),
    "finish_screening": ("Call when record_answer returns done=true. Returns spoken_summary to read out.",
                         {"session_id": S_("The session_id for this call.")}, ["session_id"], 15),
    "record_ethnicity": ("Optional final question for the equity view.",
                         {"session_id": S_("The session_id for this call."), "answer_text": S_("The caller's words, in English. 'skip' if they skipped.")},
                         ["session_id", "answer_text"], 10),
    "recall_record_answer": ("Call after each re-check answer on a recall call. Returns still_eligible and what to do next.",
                             {"referral_id": I_("The referral id for this call."), "topic": S_("The topic of the question you just asked."),
                              "answer_text": S_("The person's answer, translated faithfully into English.")}, ["referral_id", "answer_text"], 15),
    "get_slots": ("Call when the result says next=offer_slots. Returns two appointment options.",
                  {"referral_id": I_("The referral id for this call.")}, ["referral_id"], 10),
    "book_slot": ("Books the slot the person chose. Returns a confirmation to read back.",
                  {"referral_id": I_("The referral id for this call."), "slot_id": I_("The slot_id the person chose.")}, ["referral_id", "slot_id"], 10),
}
INTAKE_TOOLS = ["start_session", "record_consent", "record_answer", "finish_screening", "record_ethnicity"]
RECALL_TOOLS = ["recall_record_answer", "get_slots", "book_slot"]
RECALL_VARS = {"first_name": "there", "referral_id": "0", "trial_lay_title": "a research study", "site_name": "the research clinic",
               "site_area": "London", "recheck_topic_1": "cardio_recent", "recheck_question_1": "Have you had a heart attack or stroke in the last six months?",
               "recheck_topic_2": "", "recheck_question_2": ""}


def set_env(var: str, value: str):
    """Write VAR=value into .env (replace or append). Value is never printed."""
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    line = f"{var}={value}"
    if re.search(rf"^{var}=.*$", text, re.M):
        text = re.sub(rf"^{var}=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV.write_text(text, encoding="utf-8")


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in ("value", "token", "account_auth_token", "X-Tool-Secret") and isinstance(v, str) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


class Api:
    def __init__(self, key: str, base: str, dry: bool):
        self.dry = dry
        self.c = httpx.Client(base_url=base, headers={"xi-api-key": key}, timeout=30)

    def __call__(self, method: str, path: str, body=None, params=None):
        if self.dry:
            print(f"\n--- {method} {path}\n{json.dumps(redact(body), indent=1, ensure_ascii=False)[:2500] if body else ''}")
            return {}
        r = self.c.request(method, path, json=body, params=params)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> HTTP {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}


def prompt_parts(file: str) -> tuple[str, str]:
    text = (PROMPTS / file).read_text(encoding="utf-8")
    m = re.search(r"## First message\s*\n\s*\"(.+?)\"\s*\n", text, re.S)
    return text, (m.group(1).strip() if m else "")


def tool_config(name: str, base_url: str, secret_ref) -> dict:
    desc, props, req, timeout = TOOLS[name]
    return {"type": "webhook", "name": name, "description": desc, "response_timeout_secs": timeout,
            "api_schema": {"url": f"{base_url}/api/tools/{name}", "method": "POST", "content_type": "application/json",
                           "request_headers": {"X-Tool-Secret": secret_ref},
                           "request_body_schema": {"type": "object", "description": f"Arguments for {name}", "properties": props, "required": req}}}


def agent_body(name: str, prompt: str, first: str, tool_ids: list[str], placeholders: dict, second_lang: str | None) -> dict:
    agent = {"first_message": first, "language": "en", "dynamic_variables": {"dynamic_variable_placeholders": placeholders},
             "prompt": {"prompt": prompt, "tool_ids": tool_ids,
                        "built_in_tools": {
                            "end_call": {"type": "system", "name": "end_call", "description": "", "params": {"system_tool_type": "end_call"}},
                            **({"language_detection": {"type": "system", "name": "language_detection", "description": "",
                                                       "params": {"system_tool_type": "language_detection"}}} if second_lang else {})}}}
    cc = {"agent": agent, "tts": {"model_id": TTS_MODEL}}
    if second_lang:
        ov = {"agent": {"language": second_lang}}
        if name.endswith("Intake") and second_lang in SECOND_LANGUAGE_FIRST:
            ov["agent"]["first_message"] = SECOND_LANGUAGE_FIRST[second_lang]
        cc["language_presets"] = {second_lang: {"overrides": ov}}
    return {"name": name, "tags": [TAG], "conversation_config": cc,
            # public agent: the /call page starts it with just the agent ID (switch to signed URLs for anything beyond the demo)
            "platform_settings": {"auth": {"enable_auth": False}}}


def check(s) -> list[str]:
    missing = []
    if not s.elevenlabs_api_key:
        missing.append("ELEVENLABS_API_KEY")
    if not s.public_api_base_url:
        missing.append("PUBLIC_API_BASE_URL (the https cloudflared URL)")
    elif urlparse(s.public_api_base_url).scheme != "https":
        missing.append("PUBLIC_API_BASE_URL must be https")
    if not s.tool_shared_secret:
        missing.append("TOOL_SHARED_SECRET")
    return missing


def twilio_missing(s) -> list[str]:
    m = [] if s.twilio_phone_number else ["TWILIO_PHONE_NUMBER (E.164, a number you already own in Twilio)"]
    if not (s.twilio_account_sid and s.twilio_auth_token) and not (s.twilio_api_key_sid and s.twilio_api_key_secret):
        m.append("TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN, or TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET")
    if s.twilio_api_key_sid and not s.twilio_auth_token:
        m.append("TWILIO_AUTH_TOKEN (needed with an API key so ElevenLabs can validate inbound call webhooks)")
    if s.twilio_account_sid and not s.twilio_account_sid.startswith("AC"):
        m.append("TWILIO_ACCOUNT_SID should start with AC (an SK... value is an API key SID - put it in TWILIO_API_KEY_SID)")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--import-twilio", action="store_true")
    ap.add_argument("--second-language", default="pl", help="ISO code for the second demo language (default pl; 'none' to skip)")
    a = ap.parse_args()
    s = get_settings()
    lang2 = None if a.second_language.lower() == "none" else a.second_language.lower()
    missing = check(s)
    if a.check or (missing and not a.dry_run):
        print("Missing in .env:" if missing else "ElevenLabs settings present.", *missing, sep="\n  - ")
        tm = twilio_missing(s)
        print("Twilio import:", "ready" if not tm else "missing", *tm, sep="\n  - ")
        print("OUTBOUND_ALLOWLIST:", f"{len(s.outbound_allowed_numbers)} number(s)" if s.outbound_allowed_numbers else "empty - real outbound calls are disabled")
        raise SystemExit(1 if missing else 0)

    base = (s.public_api_base_url or "https://<PUBLIC_API_BASE_URL>").rstrip("/")
    api = Api(s.elevenlabs_api_key or "dry-run", s.elevenlabs_base_url, a.dry_run)

    # 1. tool secret as a workspace secret, so the header value is not stored in plain text on the tools
    secret_id = "dry-run-secret"
    if not a.dry_run:
        existing = next((x for x in api("GET", "/v1/convai/secrets").get("secrets", []) if x.get("name") == SECRET_NAME), None)
        if existing:
            api("PATCH", f"/v1/convai/secrets/{existing['secret_id']}", {"type": "update", "name": SECRET_NAME, "value": s.tool_shared_secret})
            secret_id = existing["secret_id"]
        else:
            secret_id = api("POST", "/v1/convai/secrets", {"type": "new", "name": SECRET_NAME, "value": s.tool_shared_secret})["secret_id"]
    print(f"tool secret: workspace secret '{SECRET_NAME}' ready")
    secret_ref = {"secret_id": secret_id}

    # 2. webhook tools
    have = {} if a.dry_run else {t["tool_config"]["name"]: t["id"] for t in api("GET", "/v1/convai/tools").get("tools", [])
                                 if isinstance(t.get("tool_config"), dict) and t["tool_config"].get("name") in TOOLS}
    ids = {}
    for name in TOOLS:
        body = {"tool_config": tool_config(name, base, secret_ref)}
        if name in have:
            api("PATCH", f"/v1/convai/tools/{have[name]}", body)
            ids[name] = have[name]
        else:
            ids[name] = api("POST", "/v1/convai/tools", body).get("id", f"dry-{name}")
    print(f"tools: {len(ids)} webhook tools -> {urlparse(base).netloc}/api/tools/*")

    # 3. agents
    intake_prompt, _ = prompt_parts("intake_system.md")
    recall_prompt, recall_first = prompt_parts("recall_system.md")
    agents_now = {} if a.dry_run else {x["name"]: x["agent_id"] for x in api("GET", "/v1/convai/agents", params={"search": "TrialReady", "page_size": 100}).get("agents", [])}
    out = {}
    for var, name, prompt, first, tools, ph in [
        ("ELEVENLABS_INTAKE_AGENT_ID", "TrialReady Intake", intake_prompt, INTAKE_FIRST, INTAKE_TOOLS, {"session_id": "none"}),
        ("ELEVENLABS_RECALL_AGENT_ID", "TrialReady Recall", recall_prompt, recall_first, RECALL_TOOLS, RECALL_VARS),
    ]:
        body = agent_body(name, prompt, first, [ids[t] for t in tools], ph, lang2)
        agent_id = getattr(s, var.lower()) or agents_now.get(name)
        if agent_id:
            api("PATCH", f"/v1/convai/agents/{agent_id}", body)
        else:
            agent_id = api("POST", "/v1/convai/agents/create", body).get("agent_id", f"dry-{var}")
        out[var] = agent_id
        print(f"agent: {name} ready ({len(tools)} tools, languages: en{'+' + lang2 if lang2 else ''})")

    # 4. post-call webhook (workspace level), HMAC-signed
    hook_url = f"{base}/api/webhooks/elevenlabs/post-call"
    hook_id, hook_secret = s.elevenlabs_webhook_id, None
    if not a.dry_run:
        hooks = api("GET", "/v1/workspace/webhooks").get("webhooks", [])
        same = next((h for h in hooks if h.get("webhook_id") == hook_id and h.get("webhook_url") == hook_url), None)
        if not (same and s.elevenlabs_webhook_secret):
            for h in hooks:  # disable our stale webhook (old tunnel URL)
                if h.get("name") == WEBHOOK_NAME and not h.get("is_disabled"):
                    api("PATCH", f"/v1/workspace/webhooks/{h['webhook_id']}", {"is_disabled": True, "name": WEBHOOK_NAME})
            res = api("POST", "/v1/workspace/webhooks", {"settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": hook_url}})
            hook_id, hook_secret = res["webhook_id"], res.get("webhook_secret")
    else:
        api("POST", "/v1/workspace/webhooks", {"settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": hook_url}})
    api("PATCH", "/v1/convai/settings", {"webhooks": {"post_call_webhook_id": hook_id or "dry-hook", "events": ["transcript"]}})
    print(f"post-call webhook: {urlparse(hook_url).netloc}{urlparse(hook_url).path} (HMAC)" + (" - new signing secret written to .env" if hook_secret else ""))

    # 5. optional: import an existing Twilio number (no purchase)
    if a.import_twilio:
        tm = twilio_missing(s)
        if tm:
            print("twilio import skipped; missing:", *tm, sep="\n  - ")
        else:
            api_key = bool(s.twilio_api_key_sid and s.twilio_api_key_secret)
            body = {"provider": "twilio", "phone_number": s.twilio_phone_number, "label": "TrialReady demo",
                    "sid": s.twilio_api_key_sid if api_key else s.twilio_account_sid,
                    "token": s.twilio_api_key_secret if api_key else s.twilio_auth_token,
                    "agent_id": out["ELEVENLABS_INTAKE_AGENT_ID"], "enable_sms": False}
            if api_key and s.twilio_auth_token:
                body["account_auth_token"] = s.twilio_auth_token
            phone_id = s.elevenlabs_phone_number_id
            if not phone_id and not a.dry_run:
                nums = api("GET", "/v1/convai/phone-numbers")
                phone_id = next((n.get("phone_number_id") for n in nums if n.get("phone_number") == s.twilio_phone_number), "")
            if phone_id:
                api("PATCH", f"/v1/convai/phone-numbers/{phone_id}", {"agent_id": out["ELEVENLABS_INTAKE_AGENT_ID"], "label": "TrialReady demo"})
            else:
                phone_id = api("POST", "/v1/convai/phone-numbers", body).get("phone_number_id", "dry-phone")
            out["ELEVENLABS_PHONE_NUMBER_ID"] = phone_id
            print("twilio: number imported/assigned to the Intake agent (inbound). No call placed.")

    if a.dry_run:
        print("\n(dry run: nothing was sent and .env was not changed)")
        return
    for var, val in out.items():
        set_env(var, val)
    if hook_id:
        set_env("ELEVENLABS_WEBHOOK_ID", hook_id)
    if hook_secret:
        set_env("ELEVENLABS_WEBHOOK_SECRET", hook_secret)
    print("\n.env updated: " + ", ".join(list(out) + (["ELEVENLABS_WEBHOOK_ID"] if hook_id else []) + (["ELEVENLABS_WEBHOOK_SECRET"] if hook_secret else []))
          + ". Restart the API so it picks them up.")


if __name__ == "__main__":
    main()

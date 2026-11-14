"""Environment + tunables. Thresholds live in matching/thresholds.py."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROCESSED_TRIALS = DATA / "processed" / "trials_t2d_uk.json"
RAW_TRIALS = DATA / "raw" / "ctgov_t2d_uk_raw.json"
LAY_SUMMARIES = DATA / "processed" / "lay_summaries.json"
OUTCODES = DATA / "geo" / "outcodes.json"
BENCH_RESULTS = DATA / "benchmark" / "results.json"
BENCH_ANNOTATIONS = DATA / "benchmark" / "trialgpt_criterion_annotations.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # --- Jev (decision model) ---
    typesafe_api_key: str = ""
    typesafe_base_url: str = "https://api.typesafe.ai/v1/systemone"
    jev_model: str = "jev-latest"
    jev_provider: str = "typesafe"  # typesafe | openrouter | aimlapi | mock
    openrouter_api_key: str = ""
    openrouter_jev_url: str = "https://openrouter.ai/api/alpha/decisions"
    openrouter_jev_model: str = "typesafe/jev-1.13"
    aimlapi_api_key: str = ""
    aimlapi_jev_url: str = "https://api.aimlapi.com/v1/decisions"
    aimlapi_jev_model: str = "typesafe/jev"
    mock_jev: bool = False
    jev_batch_size: int = 50
    jev_concurrency: int = 8
    jev_turn_timeout_s: float = 2.5
    jev_request_timeout_s: float = 6.0
    jev_price_per_m_input: float = 0.042  # USD, vendor-published price

    # --- ElevenLabs / Twilio ---
    elevenlabs_api_key: str = ""
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_intake_agent_id: str = ""
    elevenlabs_recall_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
    elevenlabs_webhook_secret: str = ""
    elevenlabs_webhook_id: str = ""
    tool_shared_secret: str = ""
    # Real outbound calls go ONLY to these E.164 numbers (comma-separated). Empty = no real calls; everyone gets a simulated call.
    outbound_allowlist: str = ""
    # Twilio: number import into ElevenLabs (scripts/setup_elevenlabs.py) and the WhatsApp channel (voice/whatsapp.py)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_phone_number: str = ""
    # WhatsApp via Twilio: sandbox ("whatsapp:+14155238886") or your approved WhatsApp sender. Text only.
    twilio_whatsapp_from: str = ""
    twilio_whatsapp_content_sid: str = ""  # optional approved template (needed outside the 24-hour reply window)

    # --- Offline generative LLM (preprocessing only) ---
    offline_llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- App ---
    public_api_base_url: str = ""
    database_url: str = "sqlite:///./trialready.db"
    max_distance_km: float = 60.0
    k_candidates: int = 30
    demo_mode: bool = True
    outbound_concurrency: int = 5
    sim_ring_seconds: float = 6.0
    enable_scheduler: bool = True
    pes_mode: str = "screenable"  # screenable | strict  (see matching/engine.py)
    allow_live_geocode: bool = True

    @property
    def db_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///./"):
            return "sqlite:///" + str(ROOT / url[len("sqlite:///./"):])
        return url

    @property
    def outbound_allowed_numbers(self) -> set[str]:
        return {n.strip().replace(" ", "") for n in self.outbound_allowlist.split(",") if n.strip()}

    @property
    def whatsapp_ready(self) -> bool:
        """Can receive (signature check needs the auth token) and send WhatsApp messages through Twilio."""
        return bool(self.twilio_whatsapp_from and self.twilio_account_sid and self.twilio_auth_token)

    @property
    def elevenlabs_outbound_ready(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_recall_agent_id and self.elevenlabs_phone_number_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

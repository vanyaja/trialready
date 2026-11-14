"""Plain-English trial summaries (spec §7.3). Curated/cached file first, then offline LLM, then a mechanical fallback."""
import json
import re

from ..config import LAY_SUMMARIES
from .atomize import _parse_json_array, llm_available, llm_complete

PROMPT = """Write a plain-English description of this clinical trial for a member of the public (reading age about 11).
Return ONLY a JSON array with one object: {{"lay_title": "<= 10 words", "lay_summary": "<= 60 words: what the study tests, what taking part involves, where"}}.
No efficacy claims. Do not promise benefit.

Title: {title}
Summary: {summary}
Interventions: {interventions}
UK sites: {cities}"""


def load_curated() -> dict:
    if LAY_SUMMARIES.exists():
        return json.loads(LAY_SUMMARIES.read_text(encoding="utf-8")).get("summaries", {})
    return {}


def _first_words(s: str, n: int) -> str:
    w = re.sub(r"\s+", " ", s or "").strip().split(" ")
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def lay_fallback(title: str, summary: str, cities: list[str]) -> dict:
    where = f" Sites in {', '.join(cities[:3])}." if cities else ""
    return {"lay_title": _first_words(title, 10), "lay_summary": _first_words(summary or title, 48) + where, "lay_source": "rule"}


def lay_for(trial_id: str, title: str, summary: str, interventions: list[str], cities: list[str], use_llm: bool = False) -> dict:
    cur = load_curated().get(trial_id)
    if cur:
        return {"lay_title": cur["lay_title"], "lay_summary": cur["lay_summary"], "lay_source": cur.get("lay_source", "curated")}
    if use_llm and llm_available():
        try:
            obj = _parse_json_array(llm_complete(PROMPT.format(title=title, summary=(summary or "")[:2500],
                                                               interventions=", ".join(interventions), cities=", ".join(cities[:5])), 400))[0]
            if len(obj["lay_title"].split()) <= 14 and len(obj["lay_summary"].split()) <= 75:
                return {"lay_title": obj["lay_title"], "lay_summary": obj["lay_summary"], "lay_source": "llm"}
        except Exception:
            pass
    return lay_fallback(title, summary, cities)

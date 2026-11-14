"""Criteria -> atomic criteria (+topic tag, lay text).  Spec §7.2.

Two paths:
  * rule-based (default, no key needed): splits the CT.gov bullet structure, tags topics by keyword,
    writes a mechanical plain-English version.  Faithful by construction (never invents text).
  * offline LLM (`--llm`): better splitting + genuinely plain lay text.  Output is validated with pydantic and
    cached in data/processed/, so the live demo never depends on it.
"""
import json
import re
from typing import Optional

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from ..config import get_settings
from .topics import ASKABLE, is_hard_filter, tag_topic

_HEADER = re.compile(r"^\W*((key|main|major|general|principal|core|additional|other|cohort[- ]specific)\s+)?(inclusion|exclusion)\s+criteria\b.{0,40}$", re.I)
_OR_PARENT = re.compile(r"(one|any|either|more) of the following[^:]{0,40}:$", re.I)
_COHORT = re.compile(r"^(study|cohort|group|arm|part|sub-?study|phase)\s+[\w.]{1,6}\s*[-:]|\((control|comparator) group\)|^(relative|carer|parent|patients?|healthy volunteers?|controls?)\b[^:]{0,30}:", re.I)
_BULLET = re.compile(r"^(\s*)(\*|-|•|·|\d{1,2}[.)]|[a-z][.)]|\([a-z0-9]{1,2}\))\s+(.*)$")


def _clean(s: str) -> str:
    s = s.replace("\\>", ">").replace("\\<", "<").replace("\\&", "&").replace("\\*", "*").replace("\\_", "_").replace("\\-", "-")
    return re.sub(r"\s+", " ", s).strip(" \t;")


def atomize_rule(text: str) -> list[dict]:
    """Split a CT.gov eligibilityCriteria blob into atomic criteria. Sub-bullets are folded into their parent."""
    kind = "inclusion"
    items: list[dict] = []
    label = ""
    cur: Optional[dict] = None
    or_group: Optional[dict] = None
    or_n = 0
    prev_blank = True
    for raw in (text or "").replace("\r", "").split("\n"):
        line = re.sub(r"^(\s*)-(?=[^\s\w-])", r"\1- ", raw.rstrip())
        if not line.strip():
            prev_blank = True
            continue
        stripped = _clean(line)
        m = _BULLET.match(line)
        if not m and _HEADER.match(stripped):
            kind = "exclusion" if re.search(r"exclusion", stripped, re.I) else "inclusion"
            label, cur, or_group = "", None, None
            # cohort-specific headers keep their wording as a label
            if not re.match(r"^\W*(key |main )?(inclusion|exclusion) criteria\W*$", stripped, re.I):
                label = stripped.rstrip(":")
            prev_blank = True
            continue
        if m:
            indent, body = len(m.group(1)), _clean(m.group(3))
            if not body:
                continue
            if indent >= 2 and cur is not None:
                sep = " " if cur["text"].endswith(":") else "; "
                cur["text"] += sep + body
            elif or_group is not None and kind == "inclusion" and or_n < 14:
                # flattened "at least one of the following:" list -> keep as ONE criterion (alternatives, not requirements)
                or_group["text"] += (" " if or_group["text"].endswith(":") else "; or ") + body
                or_n += 1
            else:
                cur = {"kind": kind, "text": (f"{label}: " if label else "") + body, "labelled": bool(label)}
                items.append(cur)
                or_group, or_n = (cur, 0) if (kind == "inclusion" and _OR_PARENT.search(body)) else (None, 0)
        else:
            if stripped.endswith(":") and len(stripped) < 90:
                label, cur = stripped.rstrip(":"), None  # group label, e.g. "For healthy volunteers:"
            elif cur is not None and not prev_blank:
                cur["text"] += " " + stripped  # wrapped continuation line
            else:
                cur = {"kind": kind, "text": (f"{label}: " if label else "") + stripped}
                items.append(cur)
        prev_blank = False
    out = []
    for it in items:
        t = it["text"].strip()
        if len(t) < 4:
            continue
        topic = tag_topic(t)
        cohort = bool(it.get("labelled") or _COHORT.search(t))
        out.append({"kind": it["kind"], "text": t, "lay_text": lay_rule(t), "topic": topic,
                    "hard_filter": is_hard_filter(t, topic) and not cohort, "cohort_specific": cohort})
    return out


_LAY = [
    (r"\bT2DM?\b", "type 2 diabetes"), (r"\bT1DM?\b", "type 1 diabetes"), (r"myocardial infarction", "heart attack"),
    (r"\beGFR\b", "kidney function (eGFR)"), (r"\brenal\b", "kidney"), (r"\bhepatic\b", "liver"), (r"malignanc(y|ies)", "cancer"),
    (r"hypersensitivity", "allergy"), (r"\bBMI\b", "body mass index (BMI)"), (r"≥|>=", "at least "), (r"≤|<=", "at most "),
    (r">", "more than "), (r"<", "less than "), (r"prior to", "before"), (r"subjects?|participants?|patients?", "people"),
    (r"\bscreening\b", "the first study visit"), (r"randomi[sz]ation", "the study start"),
]


def lay_rule(text: str, max_words: int = 20) -> str:
    s = text
    for pat, rep in _LAY:
        s = re.sub(pat, rep, s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split(" ")
    if len(words) > max_words:
        s = " ".join(words[:max_words]).rstrip(",;:(") + "…"
    return s[0].upper() + s[1:] if s else s


# ---------------------------------------------------------------- offline LLM path

class AtomicCriterion(BaseModel):
    kind: str
    text: str
    lay_text: str
    topic: str
    hard_filter: bool = False
    cohort_specific: bool = False

    @field_validator("kind")
    @classmethod
    def _k(cls, v):
        if v not in ("inclusion", "exclusion"):
            raise ValueError("kind")
        return v

    @field_validator("topic")
    @classmethod
    def _t(cls, v):
        return v if v in ASKABLE else "other"

    @field_validator("lay_text")
    @classmethod
    def _l(cls, v):
        if len(v.split()) > 26:
            raise ValueError("lay_text too long")
        return v


ATOMIZE_PROMPT = """You split clinical-trial eligibility criteria into atomic criteria.
Rules:
- One testable condition per item. Never invent criteria. Keep every number and unit exactly.
- kind: "inclusion" or "exclusion".
- text: faithful to the source wording.
- lay_text: plain English, at most 20 words, reading age about 11.
- topic: one of {topics} or "other". Criteria needing lab values the caller may not know still get their topic.
- hard_filter: true only if the criterion is purely about age or sex.
- cohort_specific: true if the criterion applies only to one cohort / sub-study / arm / participant group of the trial.
- "At least one of the following" lists stay ONE criterion (the alternatives joined with "; or ").
Return ONLY a JSON array of objects with keys kind, text, lay_text, topic, hard_filter, cohort_specific.

Eligibility criteria:
---
{criteria}
---"""


def llm_complete(prompt: str, max_tokens: int = 4000) -> str:
    """Minimal provider-agnostic text completion for offline preprocessing."""
    s = get_settings()
    if s.offline_llm_provider == "anthropic" and s.anthropic_api_key:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120,
                       headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                       json={"model": s.anthropic_model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])
    if s.openai_api_key:
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=120,
                       headers={"Authorization": f"Bearer {s.openai_api_key}"},
                       json={"model": s.openai_model, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("No offline LLM key configured (ANTHROPIC_API_KEY or OPENAI_API_KEY)")


def llm_available() -> bool:
    s = get_settings()
    return bool((s.offline_llm_provider == "anthropic" and s.anthropic_api_key) or s.openai_api_key)


def _parse_json_array(txt: str):
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        raise ValueError("no JSON array in LLM output")
    return json.loads(m.group(0))


def atomize_llm(criteria_text: str, retries: int = 2) -> list[dict]:
    prompt = ATOMIZE_PROMPT.format(topics=", ".join(sorted(ASKABLE)), criteria=criteria_text)
    err: Exception = RuntimeError("unreachable")
    for _ in range(retries + 1):
        try:
            items = [AtomicCriterion(**x).model_dump() for x in _parse_json_array(llm_complete(prompt))]
            if items:
                return items
        except (ValidationError, ValueError, json.JSONDecodeError, KeyError) as e:
            err = e
    raise err


def atomize(criteria_text: str, use_llm: bool = False) -> tuple[list[dict], str]:
    if use_llm and llm_available():
        try:
            return atomize_llm(criteria_text), "llm"
        except Exception:
            pass
    return atomize_rule(criteria_text), "rule"

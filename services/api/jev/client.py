"""Jev adapter (spec §8.2): one interface, three HTTP backends (TypeSafe native -> OpenRouter -> AIML API) + MockJev.

Request:  {"model", "state", "questions": {key: {"type": "choice"|"noul", "instructions", "criteria"}}}
Response: {"answers": {key: {"type":"choice","choice","confidence","probabilities"} | {"type":"noul","noul"}}, "usage": {...}}
The AIML API gateway schema is confirmed from its docs; TypeSafe-native and OpenRouter are parsed tolerantly
(answers may sit under "answers" or "decisions") - verify with a real key (spec §24).
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from typing import Any, Optional

import httpx
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_fixed

from ..config import get_settings
from . import mock_rules

log = logging.getLogger("jev")


def _est_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


def normalize_answers(questions: dict, answers: dict) -> dict:
    out = {}
    for key, q in questions.items():
        a = (answers or {}).get(key)
        if not isinstance(a, dict):
            continue
        if q["type"] == "noul":
            v = a.get("noul", a.get("probability"))
            if v is None:
                continue
            out[key] = {"type": "noul", "noul": float(v)}
        else:
            opts = list((q.get("criteria") or {}).keys())
            probs = a.get("probabilities") or {}
            choice = a.get("choice")
            if not probs and choice is not None:
                conf = float(a.get("confidence") or 0.8)
                rest = (1 - conf) / max(len(opts) - 1, 1)
                probs = {o: (conf if o == choice else rest) for o in opts}
            if not probs:
                continue
            probs = {o: float(probs.get(o, 0.0)) for o in opts}
            tot = sum(probs.values()) or 1.0
            probs = {o: p / tot for o, p in probs.items()}
            choice = choice if choice in probs else max(probs, key=probs.get)
            out[key] = {"type": "choice", "choice": choice, "confidence": float(a.get("confidence") or probs[choice]), "probabilities": probs}
    return out


class HttpJevBackend:
    def __init__(self, name: str, url: str, key: str, model: str):
        self.name, self.url, self.key, self.model = name, url, key, model
        self.down_until = 0.0

    async def decide(self, client: httpx.AsyncClient, state: Any, questions: dict) -> tuple[dict, dict]:
        r = await client.post(self.url, headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                              json={"model": self.model, "state": state, "questions": questions})
        r.raise_for_status()
        data = r.json()
        answers = data.get("answers") or data.get("decisions") or (data.get("data") or {}).get("answers") or {}
        norm = normalize_answers(questions, answers)
        if not norm:
            raise ValueError(f"{self.name}: no parsable answers in response keys {list(data)[:6]}")
        return norm, data.get("usage") or {}


_ETH = [("Mixed", r"mixed|dual heritage|multiple|white and (black|asian)"), ("Asian", r"asian|indian|pakistani|bangladeshi|chinese|sri lank|bengali|punjabi|gujarati"),
        ("Black", r"black|african|caribbean|nigerian|ghanaian|somali|jamaican"), ("White", r"white|british|english|irish|polish|european|scottish|welsh|romanian|italian"),
        ("Other", r"arab|other|turkish|kurdish|latin")]


class MockJevBackend:
    """Deterministic keyword rules. Lets UI, voice and tests run with no key. Not a model; not a benchmark result."""
    name, model, down_until = "mock", "mock-rules", 0.0

    async def decide(self, client, state: Any, questions: dict) -> tuple[dict, dict]:
        out = {}
        st = state if isinstance(state, dict) else {}
        answer, topic = st.get("answer", ""), st.get("topic")
        facts = None
        for key, q in questions.items():
            if key.startswith("g__"):
                out[key] = {"type": "noul", "noul": mock_rules.guard(key[3:], answer)}
            elif key.startswith("y__"):
                out[key] = {"type": "noul", "noul": mock_rules.guard(key[3:], answer)}
            elif key.startswith("x__"):
                from ..matching.extract import rule_extract
                if facts is None:
                    facts = rule_extract(topic, answer) if topic else {}
                field = key[3:]
                if field.startswith("med_"):
                    c = field[4:]
                    yes = facts.get("none", False) if c == "none" else c in facts.get("meds", [])
                    out[key] = {"type": "noul", "noul": 0.95 if yes else 0.03}
                    continue
                if field == "eth":
                    val = "unknown"
                    if not re.search(r"skip|rather not|prefer not|^\W*no\W*$|pass", answer, re.I):
                        val = next((g for g, rx in _ETH if re.search(rx, answer, re.I)), "unknown")
                else:
                    val = facts.get({"band": "band", "sex": "sex", "dx": "dx"}.get(field, field)) or "unknown"
                opts = list(q["criteria"].keys())
                val = val if val in opts else "unknown"
                rest = 0.1 / max(len(opts) - 1, 1)
                out[key] = {"type": "choice", "choice": val, "confidence": 0.9, "probabilities": {o: (0.9 if o == val else rest) for o in opts}}
            elif q["type"] == "choice" and set(q.get("criteria") or {}) == {"met", "not_met", "unknown"}:
                m = re.search(r"«(.*)»", q["instructions"], re.S)
                text = m.group(1) if m else q["instructions"]
                if isinstance(state, dict):
                    verdict = mock_rules.judge(text, st.get("patient") or {}, st.get("consent", True), st.get("language", "en"))
                    probs = mock_rules.probs_for(verdict, key)
                else:  # free-text note (benchmark): crude lexical prior only
                    probs = self._free_text(str(state), text, "EXCLUSION" in q["instructions"], key)
                choice = max(probs, key=probs.get)
                out[key] = {"type": "choice", "choice": choice, "confidence": probs[choice], "probabilities": probs}
            elif q["type"] == "noul":
                out[key] = {"type": "noul", "noul": 0.5}
            else:
                opts = list(q["criteria"].keys())
                out[key] = {"type": "choice", "choice": opts[-1], "confidence": 0.5, "probabilities": {o: 1 / len(opts) for o in opts}}
        return out, {"input_tokens": _est_tokens({"state": state, "questions": questions}), "estimated": True}

    @staticmethod
    def _free_text(note: str, crit: str, exclusion: bool, key: str) -> dict:
        words = {w for w in re.findall(r"[a-z]{5,}", crit.lower())} - {"patients", "patient", "within", "history", "study", "other", "which", "would", "treatment"}
        hits = sum(1 for w in words if w in note.lower())
        j = mock_rules._jitter(key, 0.0, 0.25)
        if exclusion:
            p = {"met": 0.08 + (0.3 if hits >= 3 else 0), "not_met": 0.62 - (0.25 if hits >= 3 else 0) + j / 2, "unknown": 0.30 - j / 2}
        else:
            p = {"met": 0.25 + (0.4 if hits >= 2 else 0) + j / 2, "not_met": 0.10, "unknown": 0.65 - (0.4 if hits >= 2 else 0) - j / 2}
        tot = sum(p.values())
        return {k: v / tot for k, v in p.items()}


class JevRouter:
    def __init__(self, force_mock: bool = False):
        s = get_settings()
        self.s = s
        http = {
            "typesafe": HttpJevBackend("typesafe", s.typesafe_base_url, s.typesafe_api_key, s.jev_model),
            "openrouter": HttpJevBackend("openrouter", s.openrouter_jev_url, s.openrouter_api_key, s.openrouter_jev_model),
            "aimlapi": HttpJevBackend("aimlapi", s.aimlapi_jev_url, s.aimlapi_api_key, s.aimlapi_jev_model),
        }
        order = ["typesafe", "openrouter", "aimlapi"]
        if s.jev_provider in order:
            order.remove(s.jev_provider)
            order.insert(0, s.jev_provider)
        self.mock = MockJevBackend()
        self.backends: list = [] if (force_mock or s.mock_jev or s.jev_provider == "mock") else [http[n] for n in order if http[n].key]
        self.backends.append(self.mock)
        self.preferred = self.backends[0].name
        self.last_backend = self.preferred
        self.cache: dict[str, dict] = {}
        self.latencies: deque = deque(maxlen=5000)
        self.stats = {"calls": 0, "decisions": 0, "tokens_in": 0, "errors": 0, "cache_hits": 0, "by_backend": {}}
        self._client: Optional[httpx.AsyncClient] = None

    # -- helpers
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.s.jev_request_timeout_s, limits=httpx.Limits(max_connections=32))
        return self._client

    @property
    def is_mock(self) -> bool:
        return self.last_backend == "mock"

    @property
    def model_label(self) -> str:
        b = next((b for b in self.backends if b.name == self.last_backend), self.mock)
        return f"{b.name}:{b.model}"

    async def _one_batch(self, state: Any, batch: dict, sem: asyncio.Semaphore) -> dict:
        async with sem:
            for b in self.backends:
                if b.down_until > time.time():
                    continue
                t0 = time.perf_counter()
                try:
                    if b is self.mock:
                        ans, usage = await b.decide(None, state, batch)
                    else:
                        async for attempt in AsyncRetrying(stop=stop_after_attempt(3), wait=wait_fixed(0.3), reraise=False):
                            with attempt:
                                ans, usage = await b.decide(self._http(), state, batch)
                except (RetryError, Exception) as e:  # noqa: BLE001 - any backend failure means "try the next one"
                    self.stats["errors"] += 1
                    b.down_until = time.time() + 20
                    log.warning("jev backend %s failed (%s); falling back", b.name, repr(e)[:160])
                    continue
                ms = (time.perf_counter() - t0) * 1000
                tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or _est_tokens({"state": state, "questions": batch}))
                self.latencies.append(ms)
                self.stats["calls"] += 1
                self.stats["decisions"] += len(ans)
                self.stats["tokens_in"] += tokens
                bb = self.stats["by_backend"].setdefault(b.name, {"calls": 0, "decisions": 0})
                bb["calls"] += 1
                bb["decisions"] += len(ans)
                self.last_backend = b.name
                log.info("jev %s n=%d %.0fms tokens=%d", b.name, len(ans), ms, tokens)
                return ans
            return {}

    async def decide(self, state: Any, questions: dict, timeout: Optional[float] = None, use_cache: bool = True) -> dict:
        """Ask many typed questions about one state. Returns {key: answer}; keys may be missing on timeout/failure."""
        if not questions:
            return {}
        sh = hashlib.sha1(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
        out, todo = {}, {}
        for k, q in questions.items():
            ck = f"{sh}:{k}:{hashlib.sha1(q['instructions'].encode()).hexdigest()[:10]}"
            if use_cache and ck in self.cache:
                out[k] = self.cache[ck]
                self.stats["cache_hits"] += 1
            else:
                todo[k] = (q, ck)
        if not todo:
            return out
        keys = list(todo)
        bs = max(1, self.s.jev_batch_size)
        sem = asyncio.Semaphore(max(1, self.s.jev_concurrency))
        tasks = [asyncio.ensure_future(self._one_batch(state, {k: todo[k][0] for k in keys[i:i + bs]}, sem)) for i in range(0, len(keys), bs)]
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for p in pending:
            p.cancel()
        for d in done:
            if d.exception() is None:
                for k, a in d.result().items():
                    out[k] = a
                    if len(self.cache) > 200_000:
                        self.cache.clear()
                    self.cache[todo[k][1]] = a
        return out

    def snapshot(self) -> dict:
        lat = sorted(self.latencies)
        pct = lambda p: round(lat[min(len(lat) - 1, int(len(lat) * p))], 1) if lat else None  # noqa: E731
        cost = self.stats["tokens_in"] / 1e6 * self.s.jev_price_per_m_input
        return {**self.stats, "preferred": self.preferred, "active": self.last_backend, "model": self.model_label,
                "fallback_mode": self.last_backend != self.preferred, "mock": self.is_mock,
                "p50_ms": pct(0.5), "p95_ms": pct(0.95), "cost_usd": round(cost, 6),
                "cost_note": "tokens x vendor-published price" + (" (token count estimated in mock mode)" if self.is_mock else "")}


_router: Optional[JevRouter] = None


def get_router() -> JevRouter:
    global _router
    if _router is None:
        _router = JevRouter()
    return _router


def set_router(r: Optional[JevRouter]):
    global _router
    _router = r

"""Spec §23.1: atomizer schema, scoring math, adaptive choice, guard routing."""
import asyncio
import hashlib
import hmac
import time

from services.api.jev import mock_rules
from services.api.jev.client import JevRouter, normalize_answers
from services.api.jev.questions import criterion_question, guard_questions
from services.api.matching import adaptive, engine
from services.api.matching import thresholds as T
from services.api.matching.extract import rule_extract
from services.api.matching.textnum import constraints, to_interval
from services.api.models import Criterion, Profile
from services.api.trials.atomize import AtomicCriterion, atomize_rule
from services.api.voice.webhooks import verify_signature

SAMPLE = """Inclusion Criteria:

* Males \\& females age ≥18 years
* BMI ≥27 kg/m2
* Diagnosed with T2DM ≥90 days prior to Screening
* HbA1c value at Screening of ≥6.0% (42 mmol/mol) and ≤10% (86 mmol/mol)
* Increased risk as evidenced by at least one of the following:
* Prior myocardial infarction
* Prior stroke

Exclusion Criteria:

* Significant hepatobiliary disease and/or any of the following results at Screening:

  * ALT ≥ 3.0 X ULN
  * AST ≥ 3.0 X ULN
* Use of insulin therapy for T2DM within 3 months prior to screening
* Pregnancy or breastfeeding.
"""


def test_atomizer_schema_and_structure():
    items = atomize_rule(SAMPLE)
    for it in items:
        AtomicCriterion(**it)  # pydantic schema
        assert it["text"] and len(it["lay_text"].split()) <= 21
    inc = [i for i in items if i["kind"] == "inclusion"]
    exc = [i for i in items if i["kind"] == "exclusion"]
    assert len(inc) == 5 and len(exc) == 3
    assert "ALT" in exc[0]["text"] and "AST" in exc[0]["text"], "sub-bullets fold into the parent"
    assert "myocardial" in inc[4]["text"] and "stroke" in inc[4]["text"], "'one of the following' stays one criterion"
    assert [i["topic"] for i in inc[:4]] == ["age_sex", "weight_bmi", "diagnosis_duration", "hba1c"]
    assert inc[0]["hard_filter"] is True and exc[1]["topic"] == "diabetes_meds" and exc[2]["topic"] == "pregnancy"
    assert "\\" not in inc[0]["text"]


def test_numeric_constraints():
    assert to_interval(constraints("HbA1c ≥6.0% (42 mmol/mol) and ≤10% (86 mmol/mol)")) == (6.0, 10.0)
    assert to_interval(constraints("Aged 18 -70 years")) == (18.0, 70.0)
    lo, hi = to_interval(constraints("between the ages of 20 and 75"))
    assert (lo, hi) == (20.0, 75.0)
    assert rule_extract("age_sex", "I'm fifty-eight, male") == {"age": 58, "sex": "male"}
    assert rule_extract("hba1c", "about 7.5 percent")["band"] == "7_8"
    assert rule_extract("cardio_recent", "I had a heart attack four months ago")["months_ago"] == 4
    assert rule_extract("location_travel", "N W 3")["outcode"] == "NW3"


def _crit(i, kind, topic="hba1c", cohort=False):
    return Criterion(id=f"T__{kind[:3]}_{i}", trial_id="T", kind=kind, text="x", topic=topic, cohort_specific=cohort)


def test_scoring_math():
    cs = [_crit(1, "inclusion"), _crit(2, "inclusion", "other"), _crit(1, "exclusion", "kidney")]
    probs = {cs[0].id: {"met": 0.9, "not_met": 0.05, "unknown": 0.05},
             cs[1].id: {"met": 0.1, "not_met": 0.1, "unknown": 0.8},
             cs[2].id: {"met": 0.05, "not_met": 0.9, "unknown": 0.05}}
    for mode in ("screenable", "strict"):
        m = engine.trial_math(cs, probs, mode)
        assert 0 <= m["p_pes"] <= m["p_opt"] <= 1 and not m["knocked_out"]
        assert abs(m["p_opt"] - (0.95 * 0.9 * 0.95)) < 1e-9
    assert abs(engine.trial_math(cs, probs, "strict")["p_pes"] - 0.9 * 0.1 * 0.9) < 1e-9
    assert abs(engine.trial_math(cs, probs, "screenable")["p_pes"] - 0.9 * 0.9 * 0.9) < 1e-9
    # knock-out: clear failure of an inclusion, or clear hit on an exclusion
    probs[cs[2].id] = {"met": 0.85, "not_met": 0.1, "unknown": 0.05}
    m = engine.trial_math(cs, probs)
    assert m["knocked_out"] and m["knock_criterion_id"] == cs[2].id
    probs[cs[2].id] = {"met": 0.79, "not_met": 0.16, "unknown": 0.05}
    assert not engine.trial_math(cs, probs)["knocked_out"]
    # cohort-specific criteria never knock a trial out
    c = _crit(9, "exclusion", "diagnosis_duration", cohort=True)
    assert not engine.trial_math([c], {c.id: {"met": 0.99, "not_met": 0.005, "unknown": 0.005}})["knocked_out"]
    assert T.band(0.7, 0.9, False) == "refer" and T.band(0.2, 0.7, False) == "human_review" and T.band(0.9, 0.9, True) == "no_match"


def test_adaptive_picks_highest_value_topic(monkeypatch):
    crits = {"A": [_crit(1, "inclusion", "hba1c"), _crit(2, "inclusion", "kidney"), _crit(3, "exclusion", "kidney")],
             "B": [_crit(4, "inclusion", "kidney"), _crit(5, "exclusion", "cancer_recent")]}
    monkeypatch.setattr(adaptive.STORE, "crit", lambda tid: crits[tid])
    monkeypatch.setattr(adaptive, "cached_scores", lambda pid: {})  # everything unknown
    prof = Profile(id="p", age=60, sex="male")
    results = [{"trial_id": "A", "p_opt": 0.9, "knocked_out": False, "uncertainty": 0.9}, {"trial_id": "B", "p_opt": 0.5, "knocked_out": False, "uncertainty": 0.5}]
    assert adaptive.next_topic(prof, results, [])[0] == "age_sex"  # fixed first
    topic, value, vals = adaptive.next_topic(prof, results, ["age_sex", "location_travel"])
    assert topic == "kidney" and vals["kidney"] > vals["hba1c"] > vals["cancer_recent"] > 0
    assert "pregnancy" not in vals  # never asked of a 60-year-old man
    assert adaptive.next_topic(prof, results, ["age_sex", "location_travel", "kidney"])[0] == "hba1c"
    assert adaptive.should_stop(results, ["a"] * 8, 1.0, "kidney") == "max_topics"
    done = [dict(r, uncertainty=0.1) for r in results]
    assert adaptive.should_stop(done, ["age_sex", "location_travel"], 1.0, "kidney") == "top3_certain"
    assert adaptive.should_stop(results, ["age_sex", "location_travel"], 0.01, "kidney") == "low_value"


def test_guards_and_mock_router():
    r = JevRouter(force_mock=True)
    ans = asyncio.run(r.decide({"topic": "hba1c", "question": "q", "answer": "I've got crushing chest pain right now"}, guard_questions()))
    assert ans["g__urgent_symptom"]["noul"] >= T.URGENT
    assert mock_rules.guard("urgent_symptom", "I had a stroke two years ago") < T.URGENT
    assert mock_rules.guard("withdraw_consent", "please remove me") >= T.WITHDRAW
    assert mock_rules.guard("answered_the_question", "pardon?") < T.ANSWERED_MIN
    assert mock_rules.guard("asks_medical_advice", "should I stop taking metformin?") >= T.MEDICAL_ADVICE
    assert mock_rules.guard("wants_to_stop", "I have to go, goodbye") >= T.WANTS_STOP
    facts = {"hba1c": {"mmol": 64, "band": "8_10"}}
    a = asyncio.run(r.decide({"patient": facts}, {"k": criterion_question("inclusion", "HbA1c between 7.0% and 10.5%")}))
    assert a["k"]["choice"] == "met" and abs(sum(a["k"]["probabilities"].values()) - 1) < 1e-9
    assert r.snapshot()["mock"] and r.snapshot()["decisions"] >= 6


def test_normalize_real_response_shapes():
    q = {"a": criterion_question("inclusion", "x"), "b": {"type": "noul", "instructions": "y"}}
    out = normalize_answers(q, {"a": {"type": "choice", "choice": "met", "confidence": 0.9}, "b": {"type": "noul", "noul": 0.7}})
    assert out["a"]["probabilities"]["met"] == 0.9 and abs(sum(out["a"]["probabilities"].values()) - 1) < 1e-9 and out["b"]["noul"] == 0.7


def test_webhook_signature():
    body, secret, ts = b'{"type":"post_call_transcription"}', "whsec", str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_signature(body, f"t={ts},v0={sig}", secret)
    assert not verify_signature(body, f"t={ts},v0={'0' * 64}", secret)
    assert not verify_signature(body, f"t={int(ts) - 99999},v0={sig}", secret)
    assert not verify_signature(body, None, secret)


def test_live_only_router_never_falls_back_to_mock():
    """Benchmark/smoke mode: a failing live backend yields no answer, never a mock answer labelled as Jev."""
    class Boom:
        name, model, key, down_until, resolved_model = "typesafe", "jev-latest", "x", 0.0, None

        async def decide(self, client, state, questions):
            raise RuntimeError("provider down")

    r = JevRouter(live_only=True)
    r.backends, r.preferred, r.last_backend = [Boom()], "typesafe", "typesafe"
    out = asyncio.run(r.decide({"answer": "hi"}, {"c": criterion_question("inclusion", "Age 18 or over")}))
    assert out == {} and "mock" not in r.stats["by_backend"]
    assert JevRouter(live_only=False).backends[-1].name == "mock"  # app keeps its offline fallback

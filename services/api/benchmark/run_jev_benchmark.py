"""Jev vs TrialGPT expert criterion annotations (spec §13).

  python -m services.api.benchmark.run_jev_benchmark --n 500            # real Jev (needs a key in .env)
  python -m services.api.benchmark.run_jev_benchmark --n 500 --mock     # pipeline smoke test; results are flagged MOCK
  python -m services.api.benchmark.run_jev_benchmark --n 200 --baseline # + generative-LLM baseline on the same pairs

One request per patient note: state = the note, questions = that patient's sampled criteria (batched by JEV_BATCH_SIZE).
"""
import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

from ..config import BENCH_RESULTS, get_settings
from ..jev.client import JevRouter
from ..jev.questions import criterion_question
from . import report
from .load_trialgpt_annotations import load, map_label, stratified_sample

TRIALGPT_REPORTED = {"label": "TrialGPT-Matching (GPT-4), reported", "accuracy": 0.873, "n": 1015,
                     "note": "Reported in Jin et al., Nature Communications 2024 (doi:10.1038/s41467-024-53081-z) on its own label set - not re-run here.",
                     "reported_not_rerun": True}


async def run_jev(sample: list[dict], notes: dict, router: JevRouter) -> tuple[list[dict], list[float]]:
    by_patient = defaultdict(list)
    for r in sample:
        by_patient[r["patient_id"]].append(r)
    records, per_decision_ms = [], []
    bs = get_settings().jev_batch_size
    for pid, rows in by_patient.items():
        for i in range(0, len(rows), bs):
            chunk = rows[i:i + bs]
            qs = {f"a{r['annotation_id']}": criterion_question(r["criterion_type"], r["criterion_text"]) for r in chunk}
            t0 = time.perf_counter()
            ans = await router.decide(notes[pid], qs, timeout=60, use_cache=False)
            ms = (time.perf_counter() - t0) * 1000
            for r in chunk:
                a = ans.get(f"a{r['annotation_id']}")
                if not a:
                    continue
                per_decision_ms.append(ms / len(chunk))
                records.append({"annotation_id": r["annotation_id"], "criterion_type": r["criterion_type"], "expert": r["expert_eligibility"],
                                "trialgpt": r["gpt4_eligibility"], "pred": a["choice"], "confidence": a["probabilities"][a["choice"]], "probs": a["probabilities"]})
    return records, per_decision_ms


def score(records: list[dict], na_as, pred_key: str = "pred") -> tuple:
    rs = []
    for r in records:
        gold = map_label(r["expert"], na_as)
        if gold is None:
            continue
        rs.append({**r, "gold": gold, "pred": r[pred_key]})
    return report.summarise(rs), rs


BASELINE_PROMPT = """You are helping screen a patient for a clinical trial. Decide whether the patient satisfies ONE {kind} criterion,
using only the patient note. Answer with exactly one word: met, not_met, or unknown.
("met" for an exclusion criterion means the exclusion applies to the patient.)

Patient note:
{note}

{kind_upper} criterion: {criterion}

Answer:"""


def run_baseline(sample: list[dict], notes: dict) -> list[dict]:
    """Generative-LLM baseline on the same pairs (apples-to-apples row). Needs ANTHROPIC_API_KEY or OPENAI_API_KEY."""
    from ..trials.atomize import llm_complete
    out = []
    for r in sample:
        t0 = time.perf_counter()
        try:
            txt = llm_complete(BASELINE_PROMPT.format(kind=r["criterion_type"], kind_upper=r["criterion_type"].upper(), note=notes[r["patient_id"]],
                                                      criterion=r["criterion_text"]), 8).strip().lower()
        except Exception:
            continue
        m = re.search(r"not_met|not met|unknown|met", txt)
        pred = (m.group(0).replace(" ", "_") if m else "unknown")
        out.append({"annotation_id": r["annotation_id"], "criterion_type": r["criterion_type"], "expert": r["expert_eligibility"], "pred": pred,
                    "confidence": 1.0, "probs": {l: float(l == pred) for l in report.LABELS}, "ms": (time.perf_counter() - t0) * 1000})
    return out


async def main(n: int, mock: bool, baseline: bool, baseline_n: int) -> dict:
    s = get_settings()
    data = load()
    sample = stratified_sample(data["rows"], n)
    router = JevRouter(force_mock=mock)
    if router.preferred == "mock" and not mock:
        raise SystemExit("No Jev key configured. Set TYPESAFE_API_KEY / OPENROUTER_API_KEY / AIMLAPI_API_KEY in .env, or pass --mock for a pipeline smoke test.")
    records, per_dec = await run_jev(sample, data["notes"], router)
    snap = router.snapshot()
    drop, drop_rs = score(records, None)
    fold, _ = score(records, "unknown")
    tg_pairs = [(map_label(r["expert"], None), map_label(r["trialgpt"], "unknown")) for r in records if map_label(r["expert"], None)]
    cost_per_1000 = (snap["tokens_in"] / max(len(records), 1)) * 1000 / 1e6 * s.jev_price_per_m_input
    out = {
        "created_at": datetime.now(timezone.utc).isoformat(), "model": snap["model"], "mock": snap["mock"], "fallback_mode": snap["fallback_mode"],
        "warning": "MOCK RUN: keyword rules, not Jev. These numbers are a pipeline smoke test and say nothing about any model." if snap["mock"] else None,
        "dataset": {"name": "ncbi/TrialGPT-Criterion-Annotations", "total_pairs": len(data["rows"]), "sampled": len(sample), "answered": len(records),
                    "sampling": "stratified by criterion type x expert label, seed 7"},
        "mappings": {
            "drop_not_applicable": {"description": "Expert 'not applicable' pairs dropped; 3-way met/not_met/unknown.", **drop},
            "not_applicable_as_unknown": {"description": "Expert 'not applicable' mapped to unknown.", **fold},
        },
        "latency": {"request_p50_ms": snap["p50_ms"], "request_p95_ms": snap["p95_ms"], "per_decision_p50_ms": report.percentile(per_dec, 0.5),
                    "per_decision_p95_ms": report.percentile(per_dec, 0.95), "histogram": report.histogram(list(router.latencies)), "requests": snap["calls"]},
        "cost": {"input_tokens": snap["tokens_in"], "usd_total": round(snap["tokens_in"] / 1e6 * s.jev_price_per_m_input, 6),
                 "usd_per_1000_criteria": round(cost_per_1000, 5), "price_per_m_input_usd": s.jev_price_per_m_input,
                 "label": "vendor price" + (" x estimated tokens (mock)" if snap["mock"] else " x reported input tokens")},
        "threshold": {"met_cutoff": report.met_cutoff(drop_rs, 0.9), "target_precision": 0.9,
                      "note": "Criterion-level P(met) cut-off reaching 0.9 precision on 'met'. Per-criterion, so not directly a trial-level P_pes threshold."},
        "comparison": [
            {"label": f"Jev ({snap['model']})" + (" - MOCK" if snap["mock"] else ""), "accuracy": drop["accuracy"], "macro_f1": drop["macro_f1"], "ece": drop["ece"], "n": drop["n"], "rerun": True},
            {"label": "TrialGPT (GPT-4) labels in the dataset, same pairs, our 3-way mapping", "accuracy": report.accuracy(tg_pairs), "macro_f1": report.macro_f1(tg_pairs),
             "ece": None, "n": len(tg_pairs), "rerun": False, "note": "Recomputed from the dataset's stored GPT-4 predictions; model not re-run."},
            {**TRIALGPT_REPORTED, "macro_f1": None, "ece": None, "rerun": False},
        ],
    }
    if baseline:
        base = run_baseline(sample[:baseline_n], data["notes"])
        if base:
            bsum, _ = score(base, None)
            out["comparison"].append({"label": f"Generative LLM baseline ({s.offline_llm_provider}), same {len(base)} pairs", "accuracy": bsum["accuracy"],
                                      "macro_f1": bsum["macro_f1"], "ece": None, "n": bsum["n"], "rerun": True,
                                      "latency_p50_ms": report.percentile([b["ms"] for b in base], 0.5)})
    BENCH_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    BENCH_RESULTS.write_text(json.dumps(out, indent=1), encoding="utf-8")
    try:
        from ..db import db_session, init_db
        from ..models import BenchmarkRun
        init_db()
        with db_session() as db:
            db.add(BenchmarkRun(model=out["model"], n_pairs=len(records), metrics={k: out[k] for k in ("mappings", "latency", "cost", "mock")}))
            db.commit()
    except Exception:
        pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--baseline-n", type=int, default=200)
    a = ap.parse_args()
    res = asyncio.run(main(a.n, a.mock, a.baseline, a.baseline_n))
    m = res["mappings"]["drop_not_applicable"]
    print(f"{res['model']}{' [MOCK]' if res['mock'] else ''}: n={m['n']} acc={m['accuracy']} macroF1={m['macro_f1']} ECE={m['ece']} "
          f"p50={res['latency']['request_p50_ms']}ms cost/1000={res['cost']['usd_per_1000_criteria']} -> {BENCH_RESULTS}")

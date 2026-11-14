"""TrialGPT expert criterion annotations (Hugging Face `ncbi/TrialGPT-Criterion-Annotations`, 1,015 patient-criterion pairs).

Columns verified 2026-11-14: annotation_id, patient_id, note, trial_id, trial_title, criterion_type, criterion_text,
gpt4_explanation, explanation_correctness, gpt4_sentences, expert_sentences, gpt4_eligibility, expert_eligibility, training.
Expert label set: inclusion -> included | not included | not enough information | not applicable
                  exclusion -> excluded | not excluded | not enough information | not applicable
A trimmed copy (labels + criterion text; notes de-duplicated) is committed in data/benchmark/.
"""
import json
import random
from collections import defaultdict
from typing import Optional

import httpx

from ..config import BENCH_ANNOTATIONS

ROWS_API = "https://datasets-server.huggingface.co/rows"
DATASET = "ncbi/TrialGPT-Criterion-Annotations"

# Our 3-way scheme: met / not_met / unknown ("met" for an exclusion criterion = the exclusion applies).
MAP3 = {"included": "met", "not included": "not_met", "excluded": "met", "not excluded": "not_met", "not enough information": "unknown"}


def map_label(label: str, na_as: Optional[str]) -> Optional[str]:
    """`not applicable` has no counterpart in a 3-way scheme: mapping A drops it (na_as=None), mapping B folds it into `unknown`."""
    if label == "not applicable":
        return na_as
    return MAP3.get(label)


def download() -> dict:
    rows, off, total = [], 0, None
    with httpx.Client(timeout=40) as c:
        while total is None or off < total:
            r = c.get(ROWS_API, params={"dataset": DATASET, "config": "default", "split": "train", "offset": off, "length": 100})
            r.raise_for_status()
            j = r.json()
            total = j["num_rows_total"]
            rows += [x["row"] for x in j["rows"]]
            off += 100
    notes = {}
    out = []
    for r in rows:
        notes.setdefault(r["patient_id"], r["note"])
        out.append({k: r[k] for k in ("annotation_id", "patient_id", "trial_id", "criterion_type", "criterion_text", "gpt4_eligibility", "expert_eligibility")})
    data = {"source": f"https://huggingface.co/datasets/{DATASET}", "notes": notes, "rows": out}
    BENCH_ANNOTATIONS.parent.mkdir(parents=True, exist_ok=True)
    BENCH_ANNOTATIONS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def load() -> dict:
    if BENCH_ANNOTATIONS.exists():
        return json.loads(BENCH_ANNOTATIONS.read_text(encoding="utf-8"))
    return download()


def stratified_sample(rows: list[dict], n: int, seed: int = 7) -> list[dict]:
    """Stratify by (criterion_type, expert label), proportional with at least a few of each rare class."""
    if n >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["criterion_type"], r["expert_eligibility"])].append(r)
    out = []
    for key, g in sorted(groups.items()):
        k = max(min(len(g), 8), round(n * len(g) / len(rows)))
        out += rng.sample(g, min(k, len(g)))
    rng.shuffle(out)
    return out[:n] if len(out) > n else out

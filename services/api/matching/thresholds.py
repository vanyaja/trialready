"""Decision thresholds (spec §8.4-§8.6). Tune from the benchmark reliability results, then edit here."""
import json

from ..config import BENCH_RESULTS

KNOCKOUT = 0.8           # p_bad(c) >= KNOCKOUT  -> trial excluded
REFER_P_PES = 0.6        # P_pes >= this and no knock-out -> Refer
REVIEW_P_OPT = 0.6       # P_opt >= this (and P_pes below REFER) -> Human review
MAX_TOPICS = 8
STOP_UNCERTAINTY = 0.15  # stop when top-3 trials are all below this
STOP_TOPIC_VALUE = 0.05  # stop when the best remaining topic is worth less than this
TOP_FOR_ADAPTIVE = 10

# per-utterance guards (Jev noul)
URGENT = 0.5
WITHDRAW = 0.6
ANSWERED_MIN = 0.4
MEDICAL_ADVICE = 0.6
WANTS_STOP = 0.6
AGREES = 0.6


def band(p_pes: float, p_opt: float, knocked_out: bool, n_inc_confirmed: int = 1) -> str:
    """refer | human_review | unconfirmed | no_match.

    `unconfirmed`: nothing rules the trial out, but no inclusion criterion (beyond age/sex) has been confirmed either -
    e.g. an unrelated trial whose criteria the caller was never asked about. Never referred, never read out, never phoned.
    """
    if knocked_out:
        return "no_match"
    if n_inc_confirmed <= 0:
        return "unconfirmed" if p_opt >= REVIEW_P_OPT else "no_match"
    if p_pes >= REFER_P_PES:
        return "refer"
    if p_opt >= REVIEW_P_OPT:
        return "human_review"
    return "no_match"


def benchmark_suggestion() -> dict:
    """Criterion-level confidence cut-off giving >=0.9 precision on 'met' (spec §13), if a real benchmark run exists.

    NOTE: this is a per-criterion cut-off. REFER_P_PES is a trial-level product over several criteria, so the two
    are related but not interchangeable; the suggestion is shown on /benchmark and applied by hand.
    """
    try:
        r = json.loads(BENCH_RESULTS.read_text(encoding="utf-8"))
        return {"model": r.get("model"), "mock": r.get("mock"), "met_cutoff_precision_0_9": r.get("threshold", {}).get("met_cutoff")}
    except Exception:
        return {}

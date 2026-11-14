"""Next-question selection and stop rule (spec §8.4)."""
from typing import Optional

from ..db import STORE
from ..models import Profile
from ..trials.topics import FIXED_FIRST, TOPIC_ORDER
from . import thresholds as T
from .engine import UNKNOWN_PRIOR, cached_scores, p_parts, screenable


def skip_topics(profile: Profile) -> set[str]:
    """Topics that make no sense to ask (pregnancy for men / women over 55)."""
    if profile.sex == "male" or (profile.age is not None and profile.age >= 56):
        return {"pregnancy"}
    return set()


def topic_values(profile: Profile, results: list[dict], asked: list[str]) -> dict[str, float]:
    """value(T) = sum over top-10 trials by P_opt of P_opt(t) * sum of p_unk(c) for that trial's criteria on topic T."""
    probs = cached_scores(profile.id)
    live = [r for r in results if not r["knocked_out"]][: T.TOP_FOR_ADAPTIVE]
    skip = set(asked) | skip_topics(profile)
    vals = {t: 0.0 for t in TOPIC_ORDER if t not in skip}
    for r in live:
        for c in STORE.crit(r["trial_id"]):
            if c.topic in vals and screenable(c) and not c.hard_filter:
                vals[c.topic] += r["p_opt"] * p_parts(c, probs.get(c.id, UNKNOWN_PRIOR))[2]
    return vals


def next_topic(profile: Profile, results: list[dict], asked: list[str]) -> tuple[Optional[str], float, dict]:
    for t in FIXED_FIRST:  # age/sex and location feed the deterministic hard filters, so they always come first
        if t not in asked:
            return t, 1.0, {}
    vals = topic_values(profile, results, asked)
    if not vals:
        return None, 0.0, vals
    best = max(vals, key=lambda t: (round(vals[t], 6), -TOPIC_ORDER.index(t)))  # tie-break: fixed topic order
    return best, vals[best], vals


def should_stop(results: list[dict], asked: list[str], best_value: float, nxt: Optional[str]) -> Optional[str]:
    """Returns the stop reason, or None to keep asking."""
    if len(asked) >= T.MAX_TOPICS:
        return "max_topics"
    if nxt is None:
        return "no_topics_left"
    if any(t not in asked for t in FIXED_FIRST):
        return None
    live = [r for r in results if not r["knocked_out"]]
    if not live:
        return "no_trials_left"
    if all(r["uncertainty"] < T.STOP_UNCERTAINTY for r in live[:3]):
        return "top3_certain"
    if best_value < T.STOP_TOPIC_VALUE:
        return "low_value"
    return None

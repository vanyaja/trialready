"""Benchmark metrics (spec §13): accuracy, macro-F1, confusion matrix, ECE + reliability diagram, latency, cost."""
from typing import Optional

LABELS = ["met", "not_met", "unknown"]


def accuracy(pairs: list[tuple]) -> Optional[float]:
    return round(sum(1 for g, p in pairs if g == p) / len(pairs), 4) if pairs else None


def macro_f1(pairs: list[tuple], labels=LABELS) -> Optional[float]:
    if not pairs:
        return None
    f1s = []
    for l in labels:
        tp = sum(1 for g, p in pairs if g == l and p == l)
        fp = sum(1 for g, p in pairs if g != l and p == l)
        fn = sum(1 for g, p in pairs if g == l and p != l)
        if tp + fp + fn == 0:
            continue
        prec, rec = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return round(sum(f1s) / len(f1s), 4) if f1s else None


def confusion(pairs: list[tuple], labels=LABELS) -> dict:
    return {g: {p: sum(1 for a, b in pairs if a == g and b == p) for p in labels} for g in labels}


def reliability(conf_correct: list[tuple], bins: int = 10) -> dict:
    """Top-label calibration: bin by confidence; ECE = sum_b (n_b / N) * |acc_b - conf_b|."""
    out, n, ece = [], len(conf_correct), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        items = [(c, k) for c, k in conf_correct if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if not items:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "mid": round((lo + hi) / 2, 2), "n": 0, "confidence": None, "accuracy": None})
            continue
        conf = sum(c for c, _ in items) / len(items)
        acc = sum(1 for _, k in items if k) / len(items)
        ece += len(items) / n * abs(acc - conf)
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "mid": round((lo + hi) / 2, 2), "n": len(items), "confidence": round(conf, 4), "accuracy": round(acc, 4)})
    return {"ece": round(ece, 4) if n else None, "bins": out}


def percentile(xs: list[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def histogram(xs: list[float], edges=(0, 100, 200, 300, 500, 750, 1000, 1500, 2500, 5000, 1e9)) -> list[dict]:
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        out.append({"bucket": f"{int(a)}-{int(b)} ms" if b < 1e9 else f">{int(a)} ms", "n": sum(1 for x in xs if a <= x < b)})
    return out


def met_cutoff(records: list[dict], target_precision: float = 0.9) -> Optional[float]:
    """Smallest P(met) cut-off whose predictions 'met' reach the target precision (needs >= 10 predictions)."""
    cands = sorted({round(r["probs"]["met"], 2) for r in records})
    for c in cands:
        sel = [r for r in records if r["probs"]["met"] >= c]
        if len(sel) >= 10 and sum(1 for r in sel if r["gold"] == "met") / len(sel) >= target_precision:
            return c
    return None


def summarise(records: list[dict]) -> dict:
    pairs = [(r["gold"], r["pred"]) for r in records]
    inc = [(r["gold"], r["pred"]) for r in records if r["criterion_type"] == "inclusion"]
    exc = [(r["gold"], r["pred"]) for r in records if r["criterion_type"] == "exclusion"]
    rel = reliability([(r["confidence"], r["gold"] == r["pred"]) for r in records])
    return {"n": len(records), "accuracy": accuracy(pairs), "accuracy_inclusion": accuracy(inc), "accuracy_exclusion": accuracy(exc),
            "n_inclusion": len(inc), "n_exclusion": len(exc), "macro_f1": macro_f1(pairs), "confusion": confusion(pairs),
            "ece": rel["ece"], "reliability": rel["bins"]}

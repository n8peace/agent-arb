"""Scoring against the hidden answer key, and the strength-gap math.

This is the only module that reads answer.json. It is called after a haggle ends.

Definitions (all whole cents; "owed" = answer.json owedCents):
  Deal:     error = deal - owed. Positive means the claimant got more than owed.
  No deal:  court decides. Effective settlement = owed, so error = 0, but each side
            also pays the fixed friction cost.
  claimantGain   = what the claimant nets minus owed
                 = error (deal)            or  -friction (no deal)
  respondentGain = owed minus what the respondent pays
                 = -error (deal)           or  -friction (no deal)
  Positive gain means that side did better than the fair amount.

Strength gap (per side, opponent held fixed, averaged over cases and both opponent strengths):
  claimant gap   = mean of [claimantGain(claimant strong) - claimantGain(claimant weak)]
  respondent gap = mean of [respondentGain(respondent strong) - respondentGain(respondent weak)]
"""
import json
import os
from typing import Dict, Iterable, List, Optional

STRENGTHS = ("weak", "strong")


def load_answer(case_dir: str) -> dict:
    with open(os.path.join(case_dir, "answer.json"), encoding="utf-8") as f:
        return json.load(f)


def score_run(owed_cents: int, deal_cents: Optional[int], friction_cents: int) -> dict:
    if deal_cents is None:
        return {
            "dealCents": None,
            "errorCents": 0,
            "absErrorCents": 0,
            "gainer": "none",              # no deal: court, both sides pay friction
            "claimantGainCents": -friction_cents,
            "respondentGainCents": -friction_cents,
        }
    err = deal_cents - owed_cents
    return {
        "dealCents": deal_cents,
        "errorCents": err,
        "absErrorCents": abs(err),
        "gainer": "claimant" if err > 0 else "respondent" if err < 0 else "even",
        "claimantGainCents": err,
        "respondentGainCents": -err,
    }


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def strength_gaps(runs: Iterable[dict]) -> dict:
    """runs: dicts with caseId, claimantStrength, respondentStrength, claimantGainCents,
    respondentGainCents. Returns average gaps plus the breakdown by fixed opponent."""
    by_case: Dict[str, Dict[tuple, dict]] = {}
    for r in runs:
        by_case.setdefault(r["caseId"], {})[(r["claimantStrength"], r["respondentStrength"])] = r

    claimant_swings = {"weak": [], "strong": []}    # keyed by the fixed respondent strength
    respondent_swings = {"weak": [], "strong": []}  # keyed by the fixed claimant strength
    for pairings in by_case.values():
        for fixed in STRENGTHS:
            cw, cs = pairings.get(("weak", fixed)), pairings.get(("strong", fixed))
            if cw and cs:
                claimant_swings[fixed].append(cs["claimantGainCents"] - cw["claimantGainCents"])
            rw, rs = pairings.get((fixed, "weak")), pairings.get((fixed, "strong"))
            if rw and rs:
                respondent_swings[fixed].append(rs["respondentGainCents"] - rw["respondentGainCents"])

    def pack(swings):
        allv = swings["weak"] + swings["strong"]
        return {
            "meanCents": _mean(allv),
            "vsWeakOpponentCents": _mean(swings["weak"]),
            "vsStrongOpponentCents": _mean(swings["strong"]),
            "n": len(allv),
        }

    return {"claimant": pack(claimant_swings), "respondent": pack(respondent_swings)}


def summarize(runs: List[dict]) -> dict:
    """Per-pairing averages plus the strength gaps."""
    table = {}
    for c in STRENGTHS:
        for r in STRENGTHS:
            sel = [x for x in runs if x["claimantStrength"] == c and x["respondentStrength"] == r]
            table["%s/%s" % (c, r)] = {
                "runs": len(sel),
                "dealRate": _mean([1.0 if x["outcome"] == "deal" else 0.0 for x in sel]),
                "meanErrorCents": _mean([x["errorCents"] for x in sel]),
                "meanAbsErrorCents": _mean([x["absErrorCents"] for x in sel]),
                "meanClaimantGainCents": _mean([x["claimantGainCents"] for x in sel]),
                "meanRespondentGainCents": _mean([x["respondentGainCents"] for x in sel]),
            }
    return {"pairings": table, "strengthGap": strength_gaps(runs)}
